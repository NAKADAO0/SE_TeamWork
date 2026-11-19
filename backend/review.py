"""Review checkpoints for a confirmed plan; adapters supply evidence, never decisions."""
from contextlib import closing
from datetime import datetime, timezone
import json
import hashlib
from threading import Lock
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, StringConstraints, ValidationError

from .workflow import transaction
from .ai_code_review import generate_code_review
from .app.planner.models import ProviderError

Check = Literal["quality", "logic", "dependencies"]
CHECKS = ("quality", "logic", "dependencies")
REWORK_LIMIT = 3
MAX_ATTEMPTS = 3  # First attempt plus two retries.
POLICY = "lab2-review@1"
Sha = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class Snapshot(BaseModel):
    revision: int = Field(ge=1)
    commit_sha: Sha


class Submission(Snapshot):
    previous_sha: Sha | None = None
    author: Name
    change_ref: Name
    mode: Literal['demo', 'live'] = 'demo'
    diff: str | None = Field(default=None,max_length=28000)


class Finding(BaseModel):
    severity: Literal["low", "medium", "high", "critical"]
    message: Note
    evidence: Note | None = None


class Attempt(Snapshot):
    check: Check
    attempt: int = Field(ge=1, le=MAX_ATTEMPTS)


class Report(Attempt):
    outcome: Literal["completed", "transient_error", "failed"]
    note: Note | None = None
    findings: list[Finding] = Field(default_factory=list, max_length=50)


class ModelSection(BaseModel):
    note: Note
    findings: list[Finding] = Field(max_length=50)


class ModelReview(BaseModel):
    quality: ModelSection
    logic: ModelSection
    dependencies: ModelSection


class Decision(Snapshot):
    reviewer: Name
    decision: Literal["approve", "request_changes", "reject"]
    reason: Note


class Stop(Snapshot):
    operator: Name
    reason: Note


def now():
    return datetime.now(timezone.utc).isoformat()


def current_revision(db, pid):
    row = db.execute("SELECT revision FROM plans WHERE id=?", (pid,)).fetchone()
    if not row:
        raise HTTPException(404, "计划不存在")
    return row[0]


def artifact(db, pid, revision, kind):
    row = db.execute(
        "SELECT body FROM artifacts WHERE plan_id=? AND revision=? AND kind=?",
        (pid, revision, kind),
    ).fetchone()
    return json.loads(row[0]) if row else None


def read_state(db, pid, revision):
    return artifact(db, pid, revision, "review_gates") or {
        "plan_id": pid, "revision": revision, "mode": "demo", "policy": POLICY,
        "rework_count": 0, "current": None, "history": [],
    }


def public(state, latest):
    def sanitized(round_):
        return {key: value for key,value in round_.items() if key != "diff"} if round_ else None
    current = state["current"]
    return {
        **state, "current": sanitized(current), "history":[sanitized(item) for item in state["history"]],
        "stale": not latest,
        "downstream_ready": latest and (current or {}).get("status") == "approved",
    }


def confirmed_snapshot(db, pid, revision):
    confirmation = artifact(db, pid, revision, "confirmation")
    execution = artifact(db, pid, revision, "execution")
    if (confirmation and execution and execution.get("compiled") is not False and
            confirmation["execution_plan_id"] == execution.get("execution_plan_id")):
        return confirmation
    return None

def load_writable(db, pid, request):
    if current_revision(db, pid) != request.revision:
        raise HTTPException(409, "计划已改版，请确认最新修订")
    confirmation = confirmed_snapshot(db, pid, request.revision)
    if not confirmation:
        raise HTTPException(409, "请先确认有效的执行计划")
    return read_state(db, pid, request.revision), confirmation


def save(db, pid, state):
    db.execute("INSERT OR REPLACE INTO artifacts VALUES (?,?,?,?)", (
        pid, state["revision"], "review_gates", json.dumps(state, ensure_ascii=False),
    ))
    return public(state, latest=True)


def load_round(db, pid, request):
    state, confirmation = load_writable(db, pid, request)
    round_ = state["current"]
    if not round_ or round_["commit_sha"] != request.commit_sha:
        raise HTTPException(409, "审查提交已变化，请刷新当前轮次")
    if round_["execution_plan_id"] != confirmation["execution_plan_id"]:
        raise HTTPException(409, "审查绑定的执行计划已失效")
    return state, round_


def request_changes(state, round_, source):
    round_["blocked_by"] = source
    if state["rework_count"] >= REWORK_LIMIT:
        round_["status"] = "needs_attention"
    else:
        state["rework_count"] += 1
        round_["status"] = "changes_requested"


def evaluate(state, round_):
    reports = [attempts[-1] for attempts in round_["checks"].values()]
    if any(r["outcome"] == "failed" or
           (r["outcome"] == "transient_error" and r["attempt"] >= MAX_ATTEMPTS) for r in reports):
        round_["status"] = "needs_attention"
    elif any(r["outcome"] == "transient_error" for r in reports):
        round_["status"] = "retryable"
    elif any(r["outcome"] != "completed" for r in reports):
        round_["status"] = "checking"
    elif any(f["severity"] in ("high", "critical") for r in reports for f in r["findings"]):
        round_["automatic_decision"] = "blocked"
        request_changes(state, round_, "automated")
    else:
        round_["automatic_decision"] = "passed"
        round_["status"] = "awaiting_human"


def review_router(store):
    router = APIRouter(prefix="/plans/{pid}/review", tags=["Review gates"])
    ai_lock, in_flight = Lock(), set()

    @router.get("")
    def get_review(pid: str, revision: int = Query(ge=1)):
        with closing(store.connect()) as db:
            db.execute("BEGIN")
            latest = current_revision(db, pid)
            if not db.execute("SELECT 1 FROM revisions WHERE plan_id=? AND revision=?", (pid, revision)).fetchone():
                raise HTTPException(404, "修订不存在")
            state = read_state(db, pid, revision)
            round_ = state["current"]
            confirmation = confirmed_snapshot(db, pid, revision) if round_ else None
            valid = round_ is None or (
                confirmation is not None and round_["execution_plan_id"] == confirmation["execution_plan_id"]
            )
            return public(state, latest == revision and valid)

    @router.post("", status_code=201)
    def submit(pid: str, request: Submission):
        if request.mode == "live" and not (request.diff or "").strip():
            raise HTTPException(422, "实时 AI_Review 需要当前提交的代码差异")
        if request.mode == "demo" and request.diff is not None:
            raise HTTPException(422, "演示轮不接收代码差异")
        digest = hashlib.sha256(request.diff.encode()).hexdigest() if request.diff else None
        with transaction(store) as db:
            state, confirmation = load_writable(db, pid, request)
            previous = state["current"]
            if previous and previous["commit_sha"] == request.commit_sha:
                if (previous["author"] != request.author or previous["change_ref"] != request.change_ref or
                        previous.get("mode","demo") != request.mode or previous.get("diff_digest") != digest):
                    raise HTTPException(409, "当前提交已绑定其他审查信息或差异内容")
                return public(state, True)
            if request.previous_sha != (previous or {}).get("commit_sha"):
                raise HTTPException(409, "提交已变化，请刷新后重新提交")
            if previous:
                if previous["status"] in ("needs_attention", "rejected", "stopped"):
                    raise HTTPException(409, "当前轮已阻塞或结束，不能直接开启下一轮")
                previous_mode = previous.get("mode","demo")
                if previous_mode == "live" and request.mode != "live":
                    raise HTTPException(409, "实时审查不能降级为演示报告")
                if previous_mode == request.mode and (previous["author"] != request.author or previous["change_ref"] != request.change_ref):
                    raise HTTPException(409, "同一种审查须沿用已绑定的变更和作者")
                if previous_mode != request.mode:
                    state["rework_count"] = 0
                if any(r["commit_sha"] == request.commit_sha for r in state["history"]):
                    raise HTTPException(409, "不能重复使用本修订中的历史提交")
                state["history"].append({key:value for key,value in previous.items() if key != "diff"})
            state["mode"] = request.mode
            state["current"] = {
                "round": len(state["history"]) + 1, "commit_sha": request.commit_sha,
                "execution_plan_id": confirmation["execution_plan_id"],
                "change_ref": request.change_ref, "author": request.author, "submitted_at": now(),
                "status": "checking", "automatic_decision": None, "decision": None,
                "mode":request.mode, "diff":request.diff, "diff_digest":digest,
                "checks": {name: [{"attempt": 1, "outcome": "pending"}] for name in CHECKS},
            }
            return save(db, pid, state)

    @router.post("/ai")
    def ai_review(pid: str, request: Snapshot):
        key = (pid, request.revision, request.commit_sha)
        with ai_lock:
            if key in in_flight:
                raise HTTPException(409,"当前提交的 AI_Review 正在运行")
            in_flight.add(key)
        try:
            with transaction(store) as db:
                _, round_ = load_round(db,pid,request)
                if round_.get("mode") != "live" or round_["status"] != "checking":
                    raise HTTPException(409,"仅待检查的实时提交可调用 DeepSeek")
                diff, digest = round_["diff"], round_["diff_digest"]
                number = round_["round"]
                document = db.execute("SELECT document FROM revisions WHERE plan_id=? AND revision=?",
                                      (pid,request.revision)).fetchone()
                tasks = json.loads(document[0]).get("tasks",[])
                context = [{k: task.get(k) for k in ("id","title","requirement_ids","acceptance")}
                           for task in tasks[:60]]
            try:
                reviewed = ModelReview.model_validate(generate_code_review(diff,context)).model_dump()
            except (ProviderError, ValidationError, ValueError) as exc:
                raise HTTPException(502,"DeepSeek 未返回完整的三类审查报告；此轮仍未通过，可重试") from exc
            with transaction(store) as db:
                state, round_ = load_round(db,pid,request)
                if (round_.get("mode") != "live" or round_["status"] != "checking" or
                        round_["round"] != number or round_["diff_digest"] != digest):
                    raise HTTPException(409,"提交已经变化；旧 AI_Review 不会保存")
                for name, data in reviewed.items():
                    round_["checks"][name][-1].update(data, outcome="completed", received_at=now(), source="deepseek")
                evaluate(state,round_)
                return save(db,pid,state)
        finally:
            with ai_lock:
                in_flight.discard(key)

    @router.post("/reports")
    def report(pid: str, request: Report):
        if request.outcome != "completed" and (request.findings or not request.note):
            raise HTTPException(422, "失败报告须提供原因，不能包含检查发现")
        with transaction(store) as db:
            state, round_ = load_round(db, pid, request)
            if round_.get("mode") == "live":
                raise HTTPException(409, "实时轮次只能写入服务端获取的 AI_Review 报告")
            attempt = round_["checks"][request.check][-1]
            result = request.model_dump(include={"attempt", "outcome", "note", "findings"})
            if all(attempt.get(key) == value for key, value in result.items()):
                return public(state, True)
            if (round_["status"] not in ("checking", "retryable") or
                    attempt["attempt"] != request.attempt or attempt["outcome"] != "pending"):
                raise HTTPException(409, "检查尝试已结束或已被替换")
            attempt.update(result, received_at=now())
            evaluate(state, round_)
            return save(db, pid, state)

    @router.post("/retry")
    def retry(pid: str, request: Attempt):
        with transaction(store) as db:
            state, round_ = load_round(db, pid, request)
            attempts = round_["checks"][request.check]
            last = attempts[-1]
            if (round_["status"] != "retryable" or last["outcome"] != "transient_error" or
                    last["attempt"] != request.attempt or request.attempt >= MAX_ATTEMPTS):
                raise HTTPException(409, "只允许重试当前可安全重跑的临时失败")
            attempts.append({"attempt": request.attempt + 1, "outcome": "pending"})
            evaluate(state, round_)
            return save(db, pid, state)

    @router.post("/decision")
    def decide(pid: str, request: Decision):
        with transaction(store) as db:
            state, round_ = load_round(db, pid, request)
            if round_["status"] != "awaiting_human":
                raise HTTPException(409, "当前提交没有待处理的人工审核")
            if request.reviewer.casefold() == round_["author"].casefold():
                raise HTTPException(403, "提交作者不能审核自己的变更")
            round_["decision"] = request.model_dump(include={"decision", "reason", "reviewer"}) | {
                "decided_at": now(),
            }
            if request.decision == "request_changes":
                request_changes(state, round_, "human")
            else:
                round_["status"] = "approved" if request.decision == "approve" else "rejected"
            return save(db, pid, state)

    @router.post("/stop")
    def stop(pid: str, request: Stop):
        with transaction(store) as db:
            state, round_ = load_round(db, pid, request)
            if round_["status"] != "needs_attention":
                raise HTTPException(409, "仅异常接管中的审查可以终止")
            round_["status"] = "stopped"
            round_["intervention"] = request.model_dump(include={"operator", "reason"}) | {"stopped_at": now()}
            return save(db, pid, state)

    return router
