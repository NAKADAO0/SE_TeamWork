"""Local, single-process planning jobs with DesignSpec intake, PM Agent & rule planner."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
import os
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from .app.contracts.spec_intake import gap_check_spec, normalize_spec_dict, parse_markdown_spec, parse_spec_input
from .app.planner.providers import ProviderError
from .app.planner.smart_rules import plan_with_provider
from .services import demo_service
from .store import ROOT


TEMPLATES: dict[str, dict[str, Any]] = {
    "default": {
        "template_ref": "default@1",
        "template_id": "default",
        "version": 1,
        "name": "标准交付模板",
        "description": "按设计、实现、验证、集成四个阶段组织任务；每条范围内需求配置实现与验证任务。",
        "stages": ["design", "implement", "verify", "integrate"],
        "allowed_roles": ["backend", "frontend", "qa"],
        "require_verification": True,
        "max_concurrency": 2,
        "role_capacity": {"backend": 1, "frontend": 1, "qa": 1},
        "ordering": ["priority", "task_id"],
        "config": {"max_concurrency": 2, "role_capacity": {"backend": 1, "frontend": 1, "qa": 1}},
    },
    "fast": {
        "template_ref": "fast@1",
        "template_id": "fast",
        "version": 1,
        "name": "高并行模板",
        "description": "提升 QA 容量与全局并发，验证任务可并行开展以缩短批次数。",
        "stages": ["implement", "verify", "integrate"],
        "allowed_roles": ["backend", "frontend", "qa"],
        "require_verification": True,
        "max_concurrency": 3,
        "role_capacity": {"backend": 1, "frontend": 1, "qa": 2},
        "ordering": ["priority", "task_id"],
        "config": {"max_concurrency": 3, "role_capacity": {"backend": 1, "frontend": 1, "qa": 2}},
    },
}

_TEMPLATE_ALIASES = {
    "default": "default",
    "default@1": "default",
    "tpl-default": "default",
    "tpl-default@1": "default",
    "fast": "fast",
    "fast@1": "fast",
}


def resolve_template(ref: Any) -> dict[str, Any] | None:
    if isinstance(ref, dict):
        tid = str(ref.get("template_id") or "").strip().lower()
        key = _TEMPLATE_ALIASES.get(tid)
        return TEMPLATES.get(key) if key else None
    key = _TEMPLATE_ALIASES.get(str(ref or "").strip().lower())
    return TEMPLATES.get(key) if key else None


def live_llm_env_ready() -> bool:
    return all(bool(os.environ.get(k, "").strip()) for k in ("PM_BASE_URL", "PM_API_KEY", "PM_MODEL"))


class SpecRef(BaseModel):
    spec_id: str
    version: int = Field(ge=1)


class SpecConfirmation(BaseModel):
    expected_version: int = Field(ge=1)


class Config(BaseModel):
    max_concurrency: int = Field(default=2, ge=1, strict=True)
    role_capacity: dict[str, int] | None = None
    ordering: list[str] | None = None


class JobRequest(BaseModel):
    spec_ref: SpecRef
    config: Config = Field(default_factory=Config)
    mode: str = "offline_rule"
    template_ref: str | dict[str, Any] = "default@1"
    template_id: str | None = None


def _is_minimal_legacy_spec(raw: dict[str, Any]) -> bool:
    return (
        isinstance(raw, dict)
        and not isinstance(raw.get("product"), dict)
        and "scope" not in raw
        and "modules" not in raw
        and "design_items" not in raw
        and "design" not in raw
        and isinstance(raw.get("requirements"), list)
        and len(raw["requirements"]) > 0
    )


def initialize(store):
    with closing(store.connect()) as db, db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS specs(id TEXT PRIMARY KEY, body TEXT NOT NULL, confirmed INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS spec_meta(spec_id TEXT PRIMARY KEY, normalized TEXT NOT NULL, source_format TEXT NOT NULL, issues TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, state TEXT NOT NULL, snapshot TEXT NOT NULL, result TEXT);
            CREATE TABLE IF NOT EXISTS artifacts(plan_id TEXT, revision INTEGER, kind TEXT, body TEXT NOT NULL,
                PRIMARY KEY(plan_id,revision,kind), FOREIGN KEY(plan_id,revision) REFERENCES revisions(plan_id,revision));
        """)
        db.execute("""UPDATE jobs SET state='failed', result=? WHERE state IN ('running','pending')""",
                   (json.dumps({"error": "服务重启，请重新提交作业"}),))


@contextmanager
def transaction(store):
    with closing(store.connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        yield db


def _pick_replay_fixture(normalized_spec: dict[str, Any], template: dict[str, Any], config: dict[str, Any]) -> tuple[dict[str, Any], str]:
    product = str(normalized_spec.get("product") or "")
    spec_id = str(normalized_spec.get("spec_id") or "")
    candidate_rel = None
    if "社区" in product or "COMMUNITY" in spec_id.upper():
        candidate_rel = "examples/contracts/community/task-plan.json"
    elif "聊天" in product or "CHAT" in spec_id.upper():
        candidate_rel = "examples/contracts/chat/task-plan.json"

    if candidate_rel:
        path = ROOT / candidate_rel
        if path.exists():
            plan = json.loads(path.read_text(encoding="utf-8"))
            plan["generation_mode"] = "fixture_replay"
            plan["config"] = config
            plan["replay_source"] = candidate_rel
            return plan, candidate_rel

    res = plan_with_provider(normalized_spec, template, config, mode="offline_rule")
    plan = res["plan"]
    plan["generation_mode"] = "fixture_replay"
    plan["replay_source"] = "structured_rule_replay"
    return plan, "structured_rule_replay"


def workflow_router(store, planner=None):
    router = APIRouter()

    @router.get("/templates")
    def templates():
        return list(TEMPLATES.values())

    @router.get("/specs/sample/{name}")
    def sample_spec(name: str):
        samples = {
            "chat": "contracts/chat/design-spec.md",
            "community": "contracts/community/design-spec.md",
            "unseen": "unseen-spec/spec.md",
            "custom": "design-spec-template.md",
        }
        if name not in samples:
            raise HTTPException(404, "样例规格不存在")
        text = (ROOT / "examples" / samples[name]).read_text(encoding="utf-8")
        draft = parse_markdown_spec(text)
        draft.pop("_source_format", None)
        return {"name": name, "format": "markdown", "draft": draft, "text": text}

    @router.post("/specs", status_code=201)
    def submit(payload: dict[str, Any]):
        try:
            raw_obj, normalized, source_format = parse_spec_input(payload)
        except ValueError as err:
            raise HTTPException(400, detail={"code": "INVALID_SPEC_INPUT", "message": str(err)})

        is_legacy_min = source_format == "json" and _is_minimal_legacy_spec(raw_obj)
        issues = gap_check_spec(raw_obj, normalized, strict_scope=not is_legacy_min)
        sid = f"SPEC-{uuid4().hex[:10].upper()}"
        normalized["spec_id"] = sid
        normalized["version"] = 1
        with transaction(store) as db:
            db.execute("INSERT INTO specs(id,body) VALUES (?,?)", (sid, json.dumps(raw_obj, ensure_ascii=False)))
            db.execute(
                "INSERT INTO spec_meta(spec_id,normalized,source_format,issues) VALUES (?,?,?,?)",
                (sid, json.dumps(normalized, ensure_ascii=False), source_format, json.dumps(issues, ensure_ascii=False)),
            )
        return {
            "spec_id": sid,
            "version": 1,
            "spec": raw_obj,
            "draft": raw_obj,
            "normalized": normalized,
            "source_format": source_format,
            "issues": issues,
        }

    @router.post("/specs/{sid}/confirm")
    def confirm_spec(sid: str, request: SpecConfirmation):
        with transaction(store) as db:
            row = db.execute("SELECT body FROM specs WHERE id=?", (sid,)).fetchone()
            if not row:
                raise HTTPException(404, "规格不存在")
            if request.expected_version != 1:
                raise HTTPException(409, "规格版本不匹配")
            raw_obj = json.loads(row[0])
            meta_row = db.execute("SELECT normalized, source_format FROM spec_meta WHERE spec_id=?", (sid,)).fetchone()
            normalized = json.loads(meta_row[0]) if meta_row else normalize_spec_dict(raw_obj)
            source_format = meta_row[1] if meta_row else "json"
            is_legacy_min = source_format == "json" and _is_minimal_legacy_spec(raw_obj)
            issues = gap_check_spec(raw_obj, normalized, strict_scope=not is_legacy_min)
            if issues["errors"]:
                msg = issues["errors"][0]["message"]
                raise HTTPException(422, detail={"code": "SPEC_GAPS", "message": msg, "issues": issues})
            if is_legacy_min:
                try:
                    demo_service("plan", raw_obj)
                except ValueError as error:
                    raise HTTPException(422, str(error))
            db.execute("UPDATE specs SET confirmed=1 WHERE id=?", (sid,))
        return {
            "spec_id": sid,
            "version": 1,
            "confirmed": True,
            "spec_ref": {"spec_id": sid, "version": 1},
            "confirmed_at": datetime.now(timezone.utc).isoformat(),
        }

    def run_job(jid: str, snapshot: dict[str, Any]):
        try:
            with transaction(store) as db:
                db.execute("UPDATE jobs SET state='running' WHERE id=?", (jid,))

            raw_spec = snapshot["spec"]
            normalized_spec = snapshot["normalized_spec"]
            template = snapshot["template"]
            config = snapshot["config"]
            mode = snapshot["mode"]
            is_legacy_min = snapshot.get("is_legacy_min", False)
            pid = f"PLAN-{uuid4().hex[:10].upper()}"

            if mode == "live_llm":
                outcome = plan_with_provider(
                    {**normalized_spec, "spec_id": snapshot["spec_ref"]["spec_id"], "version": snapshot["spec_ref"]["version"]}
                    if planner is None else raw_spec,
                    template,
                    config,
                    mode="live_llm",
                    custom_planner=planner,
                )
                if outcome["status"] != "succeeded":
                    with transaction(store) as db:
                        db.execute(
                            "UPDATE jobs SET state=?, result=? WHERE id=?",
                            (outcome["status"], json.dumps(outcome, ensure_ascii=False), jid),
                        )
                    return
                plan = {
                    **outcome["plan"],
                    "plan_id": pid,
                    "revision": 1,
                    "spec": raw_spec,
                    "normalized_spec": normalized_spec,
                    "template": template,
                    "capacity": config["max_concurrency"],
                    "status": "draft",
                    "trace": outcome.get("trace", {}),
                }
            elif mode == "fixture_replay":
                replay_plan, replay_source = _pick_replay_fixture(normalized_spec, template, config)
                plan = {
                    **replay_plan,
                    "plan_id": pid,
                    "revision": 1,
                    "spec_ref": snapshot["spec_ref"],
                    "spec": raw_spec,
                    "normalized_spec": normalized_spec,
                    "template": template,
                    "capacity": config["max_concurrency"],
                    "status": "draft",
                    "generation_mode": "fixture_replay",
                    "trace": {"mode": "fixture_replay", "replay_source": replay_source},
                }
            elif is_legacy_min:
                tasks = demo_service("plan", raw_spec)
                if not isinstance(tasks, list) or not tasks:
                    raise ValueError("规划服务应返回非空任务数组")
                plan = {
                    **snapshot,
                    "plan_id": pid,
                    "revision": 1,
                    "tasks": tasks,
                    "capacity": config["max_concurrency"],
                    "generation_mode": "offline_rule",
                    "status": "draft",
                }
            else:
                outcome = plan_with_provider(
                    {**normalized_spec, "spec_id": snapshot["spec_ref"]["spec_id"], "version": snapshot["spec_ref"]["version"]},
                    template,
                    config,
                    mode="offline_rule",
                )
                plan = {
                    **outcome["plan"],
                    "plan_id": pid,
                    "revision": 1,
                    "spec": raw_spec,
                    "normalized_spec": normalized_spec,
                    "template": template,
                    "capacity": config["max_concurrency"],
                    "status": "draft",
                    "trace": outcome.get("trace", {}),
                }

            with transaction(store) as db:
                db.execute("INSERT INTO plans VALUES (?,1)", (pid,))
                db.execute("INSERT INTO revisions VALUES (?,1,?)", (pid, json.dumps(plan, ensure_ascii=False)))
                db.execute(
                    "UPDATE jobs SET state='succeeded',result=? WHERE id=?",
                    (
                        json.dumps(
                            {
                                "plan_ref": {"plan_id": pid, "revision": 1},
                                "generation_mode": plan.get("generation_mode", mode),
                                "trace": plan.get("trace", {}),
                            },
                            ensure_ascii=False,
                        ),
                        jid,
                    ),
                )
        except Exception as error:
            msg = str(error) if isinstance(error, (ValueError, ProviderError)) else "规划服务不可用"
            with transaction(store) as db:
                db.execute(
                    "UPDATE jobs SET state='failed',result=? WHERE id=?",
                    (json.dumps({"error": {"code": "PLANNING_FAILED", "message": msg}, "message": msg}, ensure_ascii=False), jid),
                )

    @router.post("/planning-jobs", status_code=202)
    def submit_job(request: JobRequest, background: BackgroundTasks):
        template = resolve_template(request.template_id or request.template_ref)
        if template is None or request.mode not in ("offline_rule", "live_llm", "fixture_replay"):
            raise HTTPException(422, "未知模板或生成模式")
        if request.mode == "live_llm" and planner is None and not live_llm_env_ready():
            raise HTTPException(503, "实时 PM 尚未配置（请设置环境变量 PM_BASE_URL、PM_API_KEY、PM_MODEL）")

        with transaction(store) as db:
            row = db.execute("SELECT body,confirmed FROM specs WHERE id=?", (request.spec_ref.spec_id,)).fetchone()
            if not row:
                raise HTTPException(404, "规格不存在")
            if not row[1] or request.spec_ref.version != 1:
                raise HTTPException(409, "需要已确认的规格版本")
            raw_spec = json.loads(row[0])
            meta_row = db.execute("SELECT normalized, source_format FROM spec_meta WHERE spec_id=?", (request.spec_ref.spec_id,)).fetchone()
            normalized_spec = json.loads(meta_row[0]) if meta_row else normalize_spec_dict(raw_spec)
            source_format = meta_row[1] if meta_row else "json"
            is_legacy_min = source_format == "json" and _is_minimal_legacy_spec(raw_spec)

            if is_legacy_min and request.config.role_capacity is None:
                effective_config = {"max_concurrency": request.config.max_concurrency}
            else:
                effective_config = {
                    "max_concurrency": request.config.max_concurrency,
                    "role_capacity": request.config.role_capacity or dict(template["role_capacity"]),
                    "ordering": request.config.ordering or list(template["ordering"]),
                }
            snapshot = {
                "spec_ref": request.spec_ref.model_dump(),
                "template_ref": f"{template['template_id']}@{template['version']}",
                "template": template,
                "config": effective_config,
                "mode": request.mode,
                "spec": raw_spec,
                "normalized_spec": normalized_spec,
                "is_legacy_min": is_legacy_min,
            }
            jid = f"JOB-{uuid4().hex[:10].upper()}"
            db.execute("INSERT INTO jobs VALUES (?,'pending',?,NULL)", (jid, json.dumps(snapshot, ensure_ascii=False)))
        background.add_task(run_job, jid, snapshot)
        return {"job_id": jid, "status": "pending"}

    @router.get("/planning-jobs/{jid}")
    def job(jid: str):
        with closing(store.connect()) as db:
            row = db.execute("SELECT state,result FROM jobs WHERE id=?", (jid,)).fetchone()
        if not row:
            raise HTTPException(404, "作业不存在")
        payload = json.loads(row[1] or "{}")
        if isinstance(payload.get("error"), str):
            payload["error"] = {"code": "PLANNING_FAILED", "message": payload["error"]}
        return {"job_id": jid, "status": row[0], **payload}

    return router
