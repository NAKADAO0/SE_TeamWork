"""Run queries and explicit commands. Browsing never changes execution."""
import json
from typing import Any, Literal
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel, Field
from ..app.contracts.spec_intake import parse_spec_input
from ..workflow import live_llm_env_ready
from . import storage as db
from .adapter import PlanningAdapter
from .prompts import STAGES, LABELS
from .runtime import work

class NewRun(BaseModel):
    source: str = Field(min_length=1, max_length=60000)
    input_mode: Literal["requirements", "spec"] = "requirements"

class Command(BaseModel):
    action: Literal["start", "resume", "step", "pause", "confirm", "answer", "replan", "accept_risk"]
    expected_sequence: int = Field(ge=1)
    note: str = Field(default="", max_length=12000)
    target_stage: Literal["requirements", "product", "architecture"] | None = None
    spec: dict[str, Any] | None = None
    answers: list[dict[str, str]] = Field(default_factory=list, max_length=5)


def definitions():
    stages = [("input", "用户输入"), *LABELS.items(), ("planning", "任务规划"), ("confirmation", "计划确认"), ("execution", "编排执行"), ("delivery", "验证交付")]
    nodes = [dict(id="collaborate", label="独立提案与讨论", renderer="discussion"),
             dict(id="generate", label="整理 / 修订方案", renderer="artifact"),
             dict(id="review", label="独立审查", renderer="review"),
             dict(id="confirm", label="人工确认", renderer="interaction")]
    return dict(stages=[dict(id=k, label=v) for k,v in stages], subworkflows={
        **{stage: dict(nodes=nodes, edges=[dict(source="collaborate", target="generate", type="forward"),
                dict(source="generate", target="review", type="forward"), dict(source="review", target="confirm", type="conditional"),
                dict(source="review", target="generate", type="feedback")]) for stage in STAGES},
        "planning": dict(nodes=[dict(id="spec_adapter", label="复用规格契约", renderer="artifact"), dict(id="plan", label="原 PM 任务规划", renderer="tasks")],
                         edges=[dict(source="spec_adapter", target="plan", type="forward")]),
        "confirmation": dict(nodes=[dict(id="validate", label="契约与依赖校验", renderer="review"), dict(id="compile", label="资源编排", renderer="dag"), dict(id="confirm", label="人工确认计划", renderer="interaction")],
                             edges=[dict(source="validate", target="compile", type="forward"), dict(source="compile", target="confirm", type="forward")])})


def studio_router(store, workflow, delivery):
    router = APIRouter(prefix="/studio", tags=["Observable workflow"])
    adapter = PlanningAdapter(workflow, delivery)

    @router.get("/definition")
    def definition():
        return definitions()

    @router.get("/runs")
    def runs():
        return [{key: s[key] for key in ("id", "status", "stage", "updated_at", "sequence")} | {"product": s["spec"].get("product", "未命名项目")} for s in db.list_runs(store)]

    @router.post("/runs", status_code=201)
    def create(body: NewRun):
        if not body.source.strip():
            raise HTTPException(422, "请输入需求或设计规格")
        spec = {}
        if body.input_mode == "spec":
            try:
                value = json.loads(body.source) if body.source.lstrip().startswith("{") else {"text": body.source}
                _, spec, _ = parse_spec_input(value)
            except (ValueError, TypeError) as exc:
                raise HTTPException(422, "规格格式无效：" + str(exc)) from exc
        return db.create(store, body.source, body.input_mode, spec)

    @router.get("/runs/{rid}")
    def read(rid: str, sequence: int | None = Query(default=None, ge=1)):
        return db.read(store, rid, sequence)

    @router.get("/runs/{rid}/events")
    def events(rid: str, after: int = Query(default=0, ge=0)):
        return db.events(store, rid, after)

    @router.post("/runs/{rid}/commands")
    def command(rid: str, body: Command, background: BackgroundTasks):
        state = db.read(store, rid)
        if state["sequence"] != body.expected_sequence:
            raise HTTPException(409, "运行已更新，请刷新后操作")
        action = body.action
        status = state["status"]
        stage = state["stage"]
        launch = False
        if action in ("start", "resume", "step", "answer", "replan") and not live_llm_env_ready():
            raise HTTPException(503, "请先配置 PM_BASE_URL、PM_API_KEY、PM_MODEL，再启动真实 Agent")
        if action == "confirm" and status == "awaiting_confirmation" and stage == "confirmation":
            db.change(store, rid, "command.confirm", {"note": body.note},
                      lambda s: s.update(status="confirming"), body.expected_sequence)
            try:
                adapter.confirm(state["plan_ref"], state["execution"]["execution_plan_id"])
            except Exception:
                db.change(store, rid, "confirmation.failed", mutate=lambda s: s.update(status="awaiting_confirmation"))
                raise
            return db.change(store, rid, "plan.confirmed", mutate=lambda s: s.update(status="complete"))
        def mutate(s):
            nonlocal launch
            if action == "pause":
                if status != "running":
                    raise HTTPException(409, "只有运行中才能请求暂停")
                s.update(pause_requested=True, status="pausing")
            elif action in ("start", "resume", "step"):
                if status not in ("ready", "paused", "failed"):
                    raise HTTPException(409, "当前状态不能启动或继续")
                s.update(status="running", pause_requested=False, error=None, step_remaining=1 if action == "step" else None)
                launch = True
            elif action == "confirm":
                if status != "awaiting_confirmation":
                    raise HTTPException(409, "当前没有可确认的产物")
                if stage in STAGES:
                    current = s["stages"][stage]
                    if not current["review"]["passed"] and not current.get("risk_accepted"):
                        raise HTTPException(409, "审查未通过，不能确认")
                    s["spec"] = current["draft"]["spec"]
                    current["confirmed"] = True
                    current["confirmed_at"] = db.now()
                    current["confirmation_note"] = body.note
                    index = STAGES.index(stage)
                    s.update(stage=STAGES[index+1] if index < 2 else "planning", status="ready")
                elif stage == "confirmation":
                    s.update(status="complete")
                else:
                    raise HTTPException(409, "此阶段不能确认")
            elif action == "accept_risk":
                if status != "needs_input" or stage not in STAGES:
                    raise HTTPException(409, "当前没有可接受风险的方案")
                current = s["stages"][stage]
                if not body.note.strip():
                    raise HTTPException(422, "请填写明确接受哪些风险及原因")
                if current.get("questions") or any(i.get("requires_user") for i in current["review"]["issues"]):
                    raise HTTPException(409, "需求或基线问题必须先回答并重新审查，不能仅接受风险")
                current["risk_accepted"] = dict(note=body.note, timestamp=db.now(), issues=current["review"]["issues"])
                s["answers"].append(dict(stage_id=stage, text="用户明确接受风险："+body.note, timestamp=db.now()))
                s.update(status="awaiting_confirmation")
            elif action in ("answer", "replan"):
                if status in ("running", "pausing", "confirming"):
                    raise HTTPException(409, "请先暂停，再修改运行")
                if not body.note.strip():
                    raise HTTPException(422, "请填写回答或变更原因")
                target = body.target_stage or stage
                if target not in STAGES:
                    raise HTTPException(422, "请选择需要修订的前置阶段")
                current_index = STAGES.index(stage) if stage in STAGES else 3
                if STAGES.index(target) > current_index:
                    raise HTTPException(409, "不能跳过尚未确认的上游阶段")
                previous = s["stages"].get(target, {})
                requests = previous.get("draft", {}).get("decision_requests") or [dict(id="Q"+str(index+1), question=question) for index,question in enumerate(previous.get("questions", []))]
                by_id = {request["id"]: request for request in requests}
                if any(answer.get("question_id") not in by_id for answer in body.answers):
                    raise HTTPException(422, "回答引用了当前阶段不存在的问题，请刷新后重试")
                question_context = [by_id[answer["question_id"]] for answer in body.answers]
                s["answers"].append(dict(stage_id=target, text=body.note, choices=body.answers, questions=question_context, timestamp=db.now()))
                if body.spec is not None:
                    s["spec"] = body.spec
                else:
                    # Roll back to the last confirmed upstream baseline, retaining history.
                    idx = STAGES.index(target)
                    if idx:
                        s["spec"] = s["stages"][STAGES[idx-1]]["draft"]["spec"]
                    elif s["input_mode"] == "requirements":
                        s["spec"] = {}
                affected = list(STAGES[STAGES.index(target):])
                for name in affected:
                    old = s["stages"].get(name, {})
                    s["stages"][name] = dict(epoch=old.get("epoch", 1)+1, confirmed=False)
                    if name == target and target == stage and previous.get("discussion"):
                        s["stages"][name].update(seed_discussion=previous["discussion"], human_draft=body.spec or previous.get("draft", {}).get("spec"), human_edit=body.spec is not None)
                for artifact in s["artifacts"]:
                    if artifact["stage_id"] in affected:
                        artifact["superseded"] = True
                feedback = dict(scope="internal" if target == stage else "cross_stage", source_stage=stage,
                                target_stage=target, reason=body.note, affected_stages=affected+["planning", "confirmation"])
                s["feedback"].append(feedback)
                s.update(stage=target, status="running", error=None, pause_requested=False, plan_ref=None, step_remaining=None)
                s.pop("execution", None)
                s.pop("validation", None)
                s["planning_epoch"] = s.get("planning_epoch", 1)+1
                launch = True
        result = db.change(store, rid, "workflow.feedback" if action in ("replan", "answer") else "command."+action,
                           {"action": action, "note": body.note, "target_stage": body.target_stage}, mutate, body.expected_sequence)
        if launch:
            background.add_task(work, store, rid, adapter)
        return result

    return router
