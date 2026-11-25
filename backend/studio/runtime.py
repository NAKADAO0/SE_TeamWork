"""Bounded, resumable collaboration with durable node attempts and human gates."""
import copy
import difflib
import json
import os
import re
import time
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError
from ..app.planner.providers import HttpProvider
from ..app.planner.models import ProviderError
from ..app.contracts.spec_intake import normalize_spec_dict, gap_check_spec
from . import storage as db
from .models import OUTPUTS
from .convergence import budget, remaining_requests, auto_records, topic_key
from .prompts import STAGES, ROLES, VERSION, messages

class Paused(Exception):
    pass


def checkpoint(store, rid):
    state = db.read(store, rid)
    if state.get("pause_requested"):
        db.change(store, rid, "run.paused", mutate=lambda s: s.update(status="paused", pause_requested=False))
        raise Paused()
    return state


def operation(store, rid, key, node, invoke, role=None):
    state = checkpoint(store, rid)
    cached = next((item for item in reversed(state["executions"]) if item["key"] == key and item["status"] == "succeeded"), None)
    if cached:
        return cached["output"]
    execution_id = "NX-" + uuid4().hex[:12]
    record = dict(id=execution_id, key=key, node_id=node, stage_id=state["stage"], role=role,
                  attempt=1 + sum(item["key"] == key for item in state["executions"]), status="running", started_at=db.now(), prompt_version=VERSION)
    def started(s):
        # Record a return only when an actual execution enters an earlier node.
        order = ["collaborate", "generate", "review", "confirm"]
        epoch = s["stages"].get(s["stage"], {}).get("epoch", 1)
        prefix = f"{s['stage']}:{epoch}:"
        previous = next((item for item in reversed(s["executions"]) if item["key"].startswith(prefix) and item["node_id"] in order), None)
        if node in order and previous and order.index(node) < order.index(previous["node_id"]):
            source = previous["node_id"]
            review = s["stages"].get(s["stage"], {}).get("review", {})
            reason = review.get("summary", "审查后修订方案") if source == "review" else "汇总后进入角色相互评议或追加讨论"
            s["feedback"].append(dict(scope="internal", stage_id=s["stage"], source_node=source, target_node=node,
                trigger="automatic_revision" if source == "review" else "peer_discussion", execution_id=execution_id,
                epoch=epoch, resolved=False, timestamp=db.now(), reason=reason))
        s["executions"].append(record)
    db.change(store, rid, "node.started", {"node_id": node, "node_execution_id": execution_id, "role": role}, started)
    started = time.monotonic()
    try:
        output = invoke()
    except Exception as exc:
        def fail(s):
            item = next(x for x in s["executions"] if x["id"] == execution_id)
            item.update(status="failed", error=str(exc), ended_at=db.now())
        db.change(store, rid, "node.failed", {"node_id": node, "node_execution_id": execution_id, "reason": str(exc)}, fail)
        raise
    def finish(s):
        item = next(x for x in s["executions"] if x["id"] == execution_id)
        item.update(status="succeeded", output=output, ended_at=db.now(), latency_ms=round((time.monotonic()-started)*1000))
        if s.get("step_remaining") is not None:
            s["step_remaining"] -= 1
            if s["step_remaining"] <= 0:
                s["pause_requested"] = True
    db.change(store, rid, "node.completed", {"node_id": node, "node_execution_id": execution_id}, finish)
    checkpoint(store, rid)
    return output


def call_model(kind, context, role=None):
    # Provider and credentials are the same as the existing PM; no secrets enter traces.
    provider = HttpProvider.from_env()
    selector = (role["id"] if role else kind).upper().replace("-", "_")
    provider.model = os.environ.get("STUDIO_MODEL_" + selector, "").strip() or provider.model
    conversation = messages(kind, context, role)
    for attempt in range(2):
        raw = provider.complete(conversation)
        fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", raw.strip(), re.S)
        try:
            return OUTPUTS[kind].model_validate_json(fenced[1] if fenced else raw).model_dump()
        except (ValidationError, ValueError) as exc:
            details = "; ".join(".".join(map(str,item["loc"]))+": "+item["msg"] for item in exc.errors()[:4]) if isinstance(exc, ValidationError) else str(exc)
            if attempt:
                raise ProviderError(f"{kind} 返回格式无效（已自动修复一次）：{details}。可重试失败节点，已完成结果保留。") from exc
            conversation += [{"role":"assistant","content":raw},{"role":"user","content":"只修复输出格式，保留内容和实际引用；返回完整 JSON。错误字段："+details}]



def normalize_review_issues(review):
    for issue in review["issues"]:
        if issue.get("category") == "ordinary":
            issue.setdefault("reported_severity", issue["severity"])
            issue.update(severity="warning", requires_user=False)
        elif issue["severity"] == "blocking" and issue.get("category") in ("scope", "acceptance", "constraint"):
            # A model cannot bypass a declared critical conflict by mislabelling its owner.
            issue["requires_user"] = True


def baseline_issues(state, candidate):
    issues = []
    baseline = state["spec"]
    stage = state["stage"]
    protected = ["product", "scope", "requirements"] if stage != "requirements" else []
    if stage == "architecture":
        protected.append("modules")
    # Imported explicit requirements and scope remain authoritative even in stage one.
    if state["input_mode"] == "spec":
        protected += [key for key in ("product", "scope", "requirements") if baseline.get(key)]
    if state["stages"].get(stage, {}).get("human_edit"):
        protected += {"requirements": ["product", "scope", "requirements", "constraints"],
                      "product": ["modules", "design_items", "constraints"],
                      "architecture": ["design_items", "constraints"]}[stage]
    for key in set(protected):
        if baseline.get(key) != candidate.get(key):
            issues.append(dict(id="BASELINE-"+key, severity="blocking", message=f"已确认或导入的 {key} 被修改；请恢复或请求用户变更基线", requires_user=True))
    if not candidate.get("product") or not candidate.get("requirements"):
        issues.append(dict(id="SPEC-MISSING", severity="blocking", message="产品或需求缺失，需要补充", requires_user=True))
    for req in candidate.get("requirements", []):
        if not isinstance(req, dict) or not req.get("id") or not req.get("acceptance"):
            issues.append(dict(id="ACCEPTANCE-MISSING", severity="blocking", message="需求编号或验收条件缺失，需要用户确认", requires_user=True))
            break
    return issues


def save_artifact(store, rid, synthesis, round_no):
    def mutate(s):
        previous = next((a["spec"] for a in reversed(s["artifacts"]) if a["stage_id"] == s["stage"] and not a.get("superseded")), s["spec"])
        version = 1 + sum(a["stage_id"] == s["stage"] for a in s["artifacts"])
        diff = list(difflib.unified_diff(json.dumps(previous, ensure_ascii=False, indent=2).splitlines(),
                                      json.dumps(synthesis["spec"], ensure_ascii=False, indent=2).splitlines(),
                                      fromfile=f"v{version-1}", tofile=f"v{version}", lineterm=""))
        s["artifacts"].append(dict(id="ART-"+uuid4().hex[:10], stage_id=s["stage"], version=version, round=round_no,
                                   spec=synthesis["spec"], summary=synthesis["summary"], assumptions=synthesis["assumptions"],
                                   decisions=synthesis["decisions"], diff=diff, created_at=db.now(), superseded=False))
    db.change(store, rid, "artifact.created", {"round": round_no}, mutate)


def stage_work(store, rid):
    state = checkpoint(store, rid)
    stage = state["stage"]
    stage_state = state["stages"].get(stage, {})
    epoch = stage_state.get("epoch", 1)
    prefix = f"{stage}:{epoch}"
    stage_budget = budget(state, stage)
    context = dict(stage=stage, source=state["source"], input_mode=state["input_mode"], baseline=state["spec"], answers=state["answers"],
                   convergence=dict(**stage_budget, autonomous=stage_budget["used"] >= stage_budget["limit"], decisions=[item for item in state.get("decision_ledger", []) if item.get("stage_id")==stage]))
    seed = stage_state.get("seed_discussion") or []
    if seed:
        roles = list({proposal["role"]["id"]: proposal["role"] for proposal in seed}.values())
    else:
        specialists = operation(store, rid, prefix+":roles", "collaborate", lambda: call_model("specialists", context))
        roles = [dict(id=i, name=n, focus=f) for i,n,f in ROLES[stage]]
        for role in specialists["roles"]:
            if role["id"] not in {r["id"] for r in roles}:
                roles.append(role)
    db.change(store, rid, "collaboration.started", {"roles": roles},
              lambda s: s["stages"].setdefault(stage, {}).update(roles=roles))
    discussion = copy.deepcopy(stage_state.get("seed_discussion") or [])
    seeded = bool(discussion)
    context["human_draft"] = stage_state.get("human_draft")
    draft = None
    for revision in range(3):
        if revision:
            draft = operation(store, rid, f"{prefix}:v{revision}:repair", "generate",
                              lambda: checked_synthesis(dict(context, proposals=discussion, previous_draft=draft), discussion))
        if seeded and revision == 0:
            draft = operation(store, rid, prefix+":human_integration", "generate",
                              lambda: checked_synthesis(dict(context, proposals=discussion), discussion))
            validate_decisions(draft, discussion)
        discussion_rounds = range(1, 3) if seeded and revision == 0 and draft["disagreements"] else (() if seeded or revision else range(3))
        for discussion_round in discussion_rounds:
            checkpoint(store, rid)
            round_key = f"{prefix}:v{revision}:d{discussion_round}"
            # First proposals get identical baseline context, never each other's answers.
            shared = dict(context, revision=revision, discussion_round=discussion_round,
                          previous_discussion=discussion if revision or discussion_round else [], draft=draft)
            proposals = []
            participants = roles
            if discussion_round and draft and (seeded or discussion_round > 1):
                unresolved_ids = {d["proposal_id"] for d in draft["decisions"] if d["disposition"] == "needs_evidence"}
                unresolved_roles = {p["role"]["id"] for p in discussion if p["id"] in unresolved_ids}
                if unresolved_roles:
                    participants = [role for role in roles if role["id"] in unresolved_roles]
            for role in participants:
                targets = [p for p in discussion if p["role"]["id"] != role["id"]] if discussion_round else []
                role_context = dict(shared, reply_targets=targets)
                value = operation(store, rid, round_key+":"+role["id"], "collaborate",
                                  lambda role=role, role_context=role_context: checked_proposal(role_context, role), role)
                allowed = {p["id"] for p in targets}
                if discussion_round and not value.get("replies"):
                    raise ValueError("相互评议必须明确回应其他角色的实际提案")
                if any(reply["proposal_id"] not in allowed for reply in value.get("replies", [])):
                    raise ValueError("回应引用了不存在或非其他角色的提案")
                proposals.append(dict(id=round_key+":"+role["id"], role=role, **value))
            discussion.extend(proposals)
            draft = operation(store, rid, round_key+":synthesis", "generate",
                              lambda: checked_synthesis(dict(context, proposals=discussion, previous_draft=draft), discussion))
            validate_decisions(draft, discussion)
            if discussion_round == 0:
                continue  # One peer-review round is mandatory after independent designs.
            if draft["questions"] or not draft["disagreements"]:
                break
        # Artifact creation is idempotent across pause/resume.
        artifact_key = f"{prefix}:v{revision}:artifact"
        operation(store, rid, artifact_key, "artifact", lambda: _artifact(store, rid, draft, revision+1))
        review = operation(store, rid, f"{prefix}:v{revision}:review", "review",
                           lambda: call_model("review", dict(context, draft=draft, proposals=discussion)))
        review = copy.deepcopy(review)
        review["issues"].extend(baseline_issues(state, draft["spec"]))
        if stage == "architecture":
            normalized = normalize_spec_dict(draft["spec"])
            gaps = gap_check_spec(draft["spec"], normalized)
            review["issues"].extend(dict(id="CONTRACT-"+str(i), severity="blocking", message=item["message"], requires_user=False)
                                    for i,item in enumerate(gaps["errors"]))
        normalize_review_issues(review)
        # Human budgets belong to a stage, not to a retry/replanning epoch.
        requests = remaining_requests(state, stage, draft)
        all_requests = draft.get("decision_requests") or [dict(question=q,category="scope") for q in draft["questions"]]
        ordinary = [request for request in all_requests if request.get("category")=="ordinary"]
        repeated = bool(all_requests) and not requests and not ordinary
        user_issues = [issue for issue in review["issues"] if issue["requires_user"]]
        autonomous = stage_budget["used"] >= stage_budget["limit"] or repeated
        records = []
        if (autonomous or ordinary) and (all_requests or draft["disagreements"] or user_issues):
            autonomous_context = dict(context, convergence=dict(context["convergence"],autonomous=autonomous,resolve_ordinary_only=bool(ordinary) and not autonomous),
                                      proposals=discussion, previous_draft=draft, review_feedback=review)
            records = auto_records(draft, all_requests if autonomous else ordinary, stage, "provisional" if autonomous else "automatic")
            draft = operation(store, rid, f"{prefix}:v{revision}:autonomous", "generate", lambda: checked_synthesis(autonomous_context, discussion))
            validate_decisions(draft, discussion)
            operation(store, rid, f"{prefix}:v{revision}:autonomous_artifact", "artifact", lambda: _artifact(store,rid,draft,revision+1))
            review = operation(store, rid, f"{prefix}:v{revision}:autonomous_review", "review",
                               lambda: call_model("review", dict(autonomous_context,draft=draft)))
            review = copy.deepcopy(review)
            review["issues"].extend(baseline_issues(state,draft["spec"]))
            if stage == "architecture":
                gaps=gap_check_spec(draft["spec"],normalize_spec_dict(draft["spec"]))
                review["issues"].extend(dict(id="CONTRACT-"+str(i),severity="blocking",message=item["message"],requires_user=False) for i,item in enumerate(gaps["errors"]))
            records += auto_records(draft,draft.get("decision_requests",[]) if autonomous else [],stage,"provisional" if autonomous else "automatic")
            requests = remaining_requests(state,stage,draft)
        records += auto_records(draft,[],stage,"provisional" if autonomous else "automatic")
        if autonomous:
            risk_requests=[dict(question=issue["message"],recommendation="采用当前主持稿，明确保留此风险，交由最终确认；不声称已经验证。",impact=issue["message"]) for issue in review["issues"] if issue["severity"]=="blocking" or issue["requires_user"]]
            records += auto_records(draft,risk_requests,stage,"provisional")
        records = list({record["key"]: record for record in records}.values())
        normalize_review_issues(review)
        blocked = [issue for issue in review["issues"] if issue["severity"]=="blocking"]
        user_issues = [issue for issue in review["issues"] if issue["requires_user"]]
        passed = review["passed"] and not blocked and not user_issues and not requests
        review["reported_passed"] = review["passed"]
        review["passed"] = passed
        hard = [issue for issue in blocked if issue["id"].startswith(("BASELINE-","SPEC-MISSING","ACCEPTANCE-MISSING","CONTRACT-"))]
        ordinary_only = bool(review["issues"]) and all(issue.get("category")=="ordinary" for issue in review["issues"])
        final_provisional = (autonomous or ordinary_only) and not hard and (not passed or bool(records))
        questions = [] if autonomous else [request["question"] for request in requests]
        def reviewed(s):
            s.setdefault("stage_budgets",{})[stage]=stage_budget
            s["stages"].setdefault(stage,{}).update(round=revision+1,review=review,draft=draft,questions=questions,
                discussion=discussion,discussion_notes=list(draft["disagreements"]) if passed else [],epoch=epoch,provisional=final_provisional,unresolved_requests=requests if autonomous else [],
                ai_decisions=records,budget=stage_budget,hard_conflicts=hard)
            finished = passed or final_provisional or bool(autonomous and hard) or bool(questions or user_issues) or revision==2
            if finished:
                for feedback in s["feedback"]:
                    if feedback.get("scope")=="internal" and (feedback.get("stage_id") or feedback.get("target_stage"))==stage and feedback.get("epoch",epoch)==epoch:
                        feedback.update(resolved=True,resolved_at=db.now())
            ledger=s.setdefault("decision_ledger",[])
            for record in records:
                if not any(item.get("key")==record.get("key") and item.get("stage_id")==stage and item.get("status")==record.get("status") for item in ledger):ledger.append(record)
        db.change(store,rid,"review.completed",{"passed":passed,"round":revision+1,"provisional":final_provisional},reviewed)
        if passed or final_provisional:
            db.change(store,rid,"interaction.requested",{"reason":"AI 已形成暂定方案，请核对影响并最终确认" if final_provisional else "审查通过，等待人工确认"},lambda s:s.update(status="awaiting_confirmation"))
            return
        if autonomous and hard:
            db.change(store,rid,"interaction.requested",{"reason":"明确约束或程序校验仍冲突，请编辑方案；不会自动追问"},lambda s:s.update(status="needs_resolution"))
            return
        if revision == 2 and not questions and not user_issues and not blocked:
            db.change(store,rid,"interaction.requested",{"reason":"审查未通过但没有具体阻断项，自动修订已达上限，请核对审查结果或编辑方案"},lambda s:s.update(status="needs_resolution"))
            return
        if questions or user_issues or revision == 2:
            db.change(store,rid,"interaction.requested",{"reason":"需要回答问题或处理未解决的分歧"},lambda s:s.update(status="needs_input"))
            return
        context["review_feedback"] = review



def reassess_stage(state):
    """Reapply the gate to an unchanged, previously reviewed artifact; no model call."""
    stage = state["stage"]
    if state["status"] != "needs_input" or stage not in STAGES:
        raise HTTPException(409, "仅能重新检查停在人工答疑的阶段")
    info = state["stages"][stage]
    epoch = info.get("epoch", 1)
    prefix = f"{stage}:{epoch}:"
    executions = [item for item in state["executions"] if item["stage_id"] == stage and item["key"].startswith(prefix)]
    reviewed = next((i for i in range(len(executions)-1,-1,-1) if executions[i]["node_id"] == "review" and executions[i]["status"] == "succeeded"), None)
    if reviewed is None:
        raise HTTPException(409, "没有可复用的成功审查记录，请提交修订并复查")
    generation = next((item for item in reversed(executions[:reviewed]) if item["node_id"] == "generate" and item["status"] == "succeeded"), None)
    if not generation or generation.get("output") != info.get("draft") or any(item["node_id"] == "generate" for item in executions[reviewed+1:]):
        raise HTTPException(409, "当前方案与已审查版本不一致，请提交修订并复查")
    review = copy.deepcopy(executions[reviewed]["output"])
    review["issues"].extend(baseline_issues(state, info["draft"]["spec"]))
    if stage == "architecture":
        gaps = gap_check_spec(info["draft"]["spec"], normalize_spec_dict(info["draft"]["spec"]))
        review["issues"].extend(dict(id="CONTRACT-"+str(i),severity="blocking",message=item["message"],requires_user=False) for i,item in enumerate(gaps["errors"]))
    normalize_review_issues(review)
    if not review["passed"] or any(item["severity"] == "blocking" or item["requires_user"] for item in review["issues"]) or remaining_requests(state,stage,info["draft"]):
        raise HTTPException(409, "仍有实际问题或审查未通过，请先处理，不能仅重新检查状态")
    review["reported_passed"] = review["passed"]
    info.update(review=review,questions=[],discussion_notes=list(info["draft"].get("disagreements",[])),provisional=False,hard_conflicts=[])
    state.update(status="awaiting_confirmation",error=None)


def checked_synthesis(context, discussion):
    result = call_model("synthesis", context)
    validate_decisions(result, discussion)
    return result


def checked_proposal(context, role):
    value = call_model("proposal", context, role)
    allowed = {proposal["id"] for proposal in context.get("reply_targets", [])}
    if context["discussion_round"] and not value.get("replies"):
        raise ValueError("相互评议必须明确回应其他角色的实际提案")
    if any(reply["proposal_id"] not in allowed for reply in value.get("replies", [])):
        raise ValueError("回应引用了不存在或非其他角色的提案")
    return value


def validate_decisions(draft, discussion):
    referenced = {decision["proposal_id"] for decision in draft["decisions"]}
    if referenced != {proposal["id"] for proposal in discussion}:
        raise ValueError("主持决策必须关联每条实际提案，不能引用不存在的提案")


def _artifact(store, rid, draft, round_no):
    save_artifact(store, rid, draft, round_no)
    return {"saved": True}


def planning_work(store, rid, adapter):
    state = checkpoint(store, rid)
    prefix = f"planning:{state.get('planning_epoch', 1)}"
    spec_ref = operation(store, rid, prefix+":spec", "spec_adapter", lambda: adapter.create_spec(state["spec"]))
    ref = operation(store, rid, prefix+":plan", "plan", lambda: adapter.plan(spec_ref))
    db.change(store, rid, "plan.created", {"plan_ref": ref}, lambda s: s.update(plan_ref=ref, stage="confirmation"))
    validation = operation(store, rid, prefix+":validate", "validate", lambda: adapter.validate(ref))
    if validation.get("errors"):
        raise ValueError("计划校验失败：" + str(validation["errors"]))
    execution = operation(store, rid, prefix+":compile", "compile", lambda: adapter.compile(ref))
    if execution.get("compiled") is False:
        raise ValueError("计划无法编排：" + str(execution))
    db.change(store, rid, "interaction.requested", {"reason": "执行计划已编排，等待确认"},
              lambda s: s.update(status="awaiting_confirmation", execution=execution, validation=validation))


def work(store, rid, adapter):
    try:
        state = checkpoint(store, rid)
        if state["stage"] in STAGES:
            stage_work(store, rid)
        elif state["stage"] in ("planning", "confirmation"):
            planning_work(store, rid, adapter)
    except Paused:
        pass
    except Exception as exc:
        reason = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
        db.change(store, rid, "run.failed", {"reason": reason}, lambda s: s.update(status="failed", error=reason, pause_requested=False))
