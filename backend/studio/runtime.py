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
    db.change(store, rid, "node.started", {"node_id": node, "node_execution_id": execution_id, "role": role},
              lambda s: s["executions"].append(record))
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
    raw = provider.complete(messages(kind, context, role))
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", raw.strip(), re.S)
    try:
        return OUTPUTS[kind].model_validate_json(fenced[1] if fenced else raw).model_dump()
    except (ValidationError, ValueError) as exc:
        raise ProviderError(f"{kind} 返回格式无效，请重试；未将结果作为产物") from exc


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
    context = dict(stage=stage, source=state["source"], input_mode=state["input_mode"], baseline=state["spec"], answers=state["answers"])
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
                              lambda: call_model("synthesis", dict(context, proposals=discussion, previous_draft=draft)))
        if seeded and revision == 0:
            draft = operation(store, rid, prefix+":human_integration", "generate",
                              lambda: call_model("synthesis", dict(context, proposals=discussion)))
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
                              lambda: call_model("synthesis", dict(context, proposals=discussion, previous_draft=draft)))
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
        # The moderator owns the consolidated user questions. Individual proposals
        # retain their questions as evidence, without duplicating them in the gate.
        questions = list(dict.fromkeys([request["question"] for request in draft.get("decision_requests", [])] or draft["questions"]))
        blocked = [i for i in review["issues"] if i["severity"] == "blocking"]
        user_issues = [i for i in review["issues"] if i["requires_user"]]
        passed = review["passed"] and not blocked and not user_issues and not questions and not draft["disagreements"]
        review["passed"] = passed
        def reviewed(s):
            s["stages"].setdefault(stage, {}).update(round=revision+1, review=review, draft=draft,
                                                      questions=questions, discussion=discussion, epoch=epoch)
        db.change(store, rid, "review.completed", {"passed": passed, "round": revision+1}, reviewed)
        if passed:
            db.change(store, rid, "interaction.requested", {"reason": "审查通过，等待人工确认"},
                      lambda s: s.update(status="awaiting_confirmation"))
            return
        if questions or draft["disagreements"] or user_issues or revision == 2:
            db.change(store, rid, "interaction.requested", {"reason": "需要回答问题或处理未解决的分歧"},
                      lambda s: s.update(status="needs_input"))
            return
        feedback = dict(scope="internal", source_node="review", target_node="generate", round_before=revision+1,
                        round_after=revision+2, reason=review["summary"], issues=review["issues"], stage_id=stage)
        db.change(store, rid, "workflow.feedback", feedback, lambda s: s["feedback"].append(feedback))
        context["review_feedback"] = review


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
