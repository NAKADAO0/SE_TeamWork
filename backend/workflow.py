"""Local, single-process planning jobs. All external services are injectable."""
from contextlib import closing, contextmanager
import json
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from .services import demo_service


class SpecRef(BaseModel):
    spec_id: str
    version: int = Field(ge=1)


class SpecConfirmation(BaseModel):
    expected_version: int = Field(ge=1)


class Config(BaseModel):
    max_concurrency: int = Field(default=2, ge=1, strict=True)


class JobRequest(BaseModel):
    spec_ref: SpecRef
    config: Config = Field(default_factory=Config)
    mode: str = "offline_rule"
    template_ref: str = "default@1"


def initialize(store):
    with closing(store.connect()) as db, db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS specs(id TEXT PRIMARY KEY, body TEXT NOT NULL, confirmed INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, state TEXT NOT NULL, snapshot TEXT NOT NULL, result TEXT);
            CREATE TABLE IF NOT EXISTS artifacts(plan_id TEXT, revision INTEGER, kind TEXT, body TEXT NOT NULL,
                PRIMARY KEY(plan_id,revision,kind), FOREIGN KEY(plan_id,revision) REFERENCES revisions(plan_id,revision));
        """)
        db.execute("""UPDATE jobs SET state='failed', result=? WHERE state IN ('running','pending')""",
                   (json.dumps({"error":"服务重启，请重新提交作业"}),))


@contextmanager
def transaction(store):
    with closing(store.connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        yield db


def workflow_router(store, planner=None):
    router = APIRouter()

    @router.get("/templates")
    def templates():
        return [{"template_ref":"default@1", "config":{"max_concurrency":2}}]

    @router.post("/specs", status_code=201)
    def submit(spec: dict):
        sid = uuid4().hex
        with transaction(store) as db:
            db.execute("INSERT INTO specs(id,body) VALUES (?,?)", (sid,json.dumps(spec)))
        return {"spec_id":sid,"version":1,"spec":spec}

    @router.post("/specs/{sid}/confirm")
    def confirm_spec(sid: str, request: SpecConfirmation):
        with transaction(store) as db:
            row = db.execute("SELECT body FROM specs WHERE id=?", (sid,)).fetchone()
            if not row:
                raise HTTPException(404,"规格不存在")
            if request.expected_version != 1:
                raise HTTPException(409,"规格版本不匹配")
            try:
                demo_service("plan",json.loads(row[0]))
            except ValueError as error:
                raise HTTPException(422,str(error))
            db.execute("UPDATE specs SET confirmed=1 WHERE id=?", (sid,))
        return {"spec_id":sid,"version":1,"confirmed":True}

    def run_job(jid, snapshot):
        try:
            with transaction(store) as db:
                db.execute("UPDATE jobs SET state='running' WHERE id=?", (jid,))
            spec = snapshot["spec"]
            tasks = (planner(spec) if snapshot["mode"]=="live_llm" else demo_service("plan",spec))
            if not isinstance(tasks,list) or not tasks:
                raise ValueError("规划服务应返回非空任务数组")
            pid = uuid4().hex
            plan = {**snapshot,"tasks":tasks,"capacity":snapshot["config"]["max_concurrency"],"status":"draft"}
            with transaction(store) as db:
                db.execute("INSERT INTO plans VALUES (?,1)",(pid,))
                db.execute("INSERT INTO revisions VALUES (?,1,?)",(pid,json.dumps(plan)))
                db.execute("UPDATE jobs SET state='succeeded',result=? WHERE id=?",
                           (json.dumps({"plan_ref":{"plan_id":pid,"revision":1}}),jid))
        except Exception as error:
            with transaction(store) as db:
                db.execute("UPDATE jobs SET state='failed',result=? WHERE id=?",
                           (json.dumps({"error":str(error) if isinstance(error,ValueError) else "规划服务不可用"}),jid))

    @router.post("/planning-jobs",status_code=202)
    def submit_job(request: JobRequest, background: BackgroundTasks):
        if request.template_ref!="default@1" or request.mode not in ("offline_rule","live_llm"):
            raise HTTPException(422,"未知模板或生成模式")
        if request.mode=="live_llm" and planner is None:
            raise HTTPException(503,"实时 PM 尚未配置")
        with transaction(store) as db:
            row=db.execute("SELECT body,confirmed FROM specs WHERE id=?", (request.spec_ref.spec_id,)).fetchone()
            if not row:
                raise HTTPException(404,"规格不存在")
            if not row[1] or request.spec_ref.version!=1:
                raise HTTPException(409,"需要已确认的规格版本")
            snapshot={**request.model_dump(),"spec":json.loads(row[0])}
            jid=uuid4().hex
            db.execute("INSERT INTO jobs VALUES (?,'pending',?,NULL)",(jid,json.dumps(snapshot)))
        background.add_task(run_job,jid,snapshot)
        return {"job_id":jid,"status":"pending"}

    @router.get("/planning-jobs/{jid}")
    def job(jid: str):
        with closing(store.connect()) as db:
            row=db.execute("SELECT state,result FROM jobs WHERE id=?",(jid,)).fetchone()
        if not row:
            raise HTTPException(404,"作业不存在")
        return {"job_id":jid,"status":row[0],**json.loads(row[1] or "{}")}

    return router
