import json
from uuid import uuid4
from typing import Literal
from contextlib import closing

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .workflow import transaction
from .services import demo_service


class Revision(BaseModel):
    revision: int = Field(ge=1)


class Confirmation(Revision):
    execution_plan_id: str


def validate_demo(plan):
    errors = []
    tasks = plan.get("tasks", [])
    if not tasks:
        errors.append("任务列表为空")
    for i, task in enumerate(tasks):
        if not isinstance(task,dict) or not all(isinstance(task.get(k),str) and task[k].strip()
                                               for k in ("id","title","role","acceptance")):
            errors.append(f"tasks[{i}] 缺少编号、目标、角色或验收条件")
        elif not isinstance(task.get("depends_on"),list):
            errors.append(f"tasks[{i}].depends_on 应为数组")
    if not errors:
        try:
            demo_service("compile",plan)
        except ValueError as exc:
            errors.append(str(exc))
    return errors


def delivery_router(store, validator=None, compiler=None):
    router = APIRouter()
    check = validator or validate_demo
    schedule = compiler or (lambda plan: demo_service("compile",plan))

    def current(db,pid,revision):
        row=db.execute("SELECT revision FROM plans WHERE id=?",(pid,)).fetchone()
        if not row:
            raise HTTPException(404,"计划不存在")
        if row[0]!=revision:
            raise HTTPException(409,detail={"current_revision":row[0]})

    def document(pid,revision):
        try:
            return store.read(pid,revision)["plan"]
        except KeyError:
            raise HTTPException(404,"计划或修订不存在")

    def lookup(db,pid,revision,kind):
        row=db.execute("SELECT body FROM artifacts WHERE plan_id=? AND revision=? AND kind=?",
                       (pid,revision,kind)).fetchone()
        return json.loads(row[0]) if row else None

    def record(db,pid,revision,kind,value):
        db.execute("INSERT INTO artifacts VALUES (?,?,?,?)",(pid,revision,kind,json.dumps(value)))

    @router.post("/plans/{pid}/validate")
    def validate(pid: str, request: Revision):
        plan=document(pid,request.revision)
        errors=check(plan)
        with transaction(store) as db:
            current(db,pid,request.revision)
            report=lookup(db,pid,request.revision,"validation")
            if report is None:
                report={"validation_ref":uuid4().hex,"plan_id":pid,"revision":request.revision,"errors":errors}
                record(db,pid,request.revision,"validation",report)
        return report

    @router.post("/plans/{pid}/compile")
    def compile_plan(pid: str, request: Revision):
        report=validate(pid,request)
        if report["errors"]:
            raise HTTPException(422,detail=report)
        plan=document(pid,request.revision)
        batches=schedule(plan)
        with transaction(store) as db:
            current(db,pid,request.revision)
            execution=lookup(db,pid,request.revision,"execution")
            if execution is None:
                execution={"execution_plan_id":uuid4().hex,"validation_ref":report["validation_ref"],
                           "plan_id":pid,"revision":request.revision,"batches":batches,
                           "nodes":[t["id"] for t in plan["tasks"]],
                           "edges":[{"from":dep,"to":t["id"]} for t in plan["tasks"] for dep in t["depends_on"]]}
                record(db,pid,request.revision,"execution",execution)
        return execution

    @router.post("/plans/{pid}/confirm")
    def confirm(pid: str, request: Confirmation):
        with transaction(store) as db:
            current(db,pid,request.revision)
            execution=lookup(db,pid,request.revision,"execution")
            if not execution or execution["execution_plan_id"]!=request.execution_plan_id:
                raise HTTPException(409,"需要本修订的有效编排结果")
            if not lookup(db,pid,request.revision,"confirmation"):
                record(db,pid,request.revision,"confirmation",request.model_dump())
        return {"plan_id":pid,"revision":request.revision,"status":"confirmed"}

    @router.get("/plans/{pid}/export")
    def export(pid: str,revision: int=Query(ge=1),format: Literal["json","markdown"]="json"):
        plan=document(pid,revision)
        with closing(store.connect()) as db:
            db.execute("BEGIN")
            artifacts={kind:json.loads(body) for kind,body in db.execute(
                "SELECT kind,body FROM artifacts WHERE plan_id=? AND revision=?",(pid,revision))}
        if "confirmation" not in artifacts:
            raise HTTPException(409,"请先确认当前修订")
        bundle={"plan_id":pid,"revision":revision,"status":"confirmed","plan":plan,**artifacts}
        raw=json.dumps(bundle,ensure_ascii=False,indent=2)
        if format=="markdown":
            tasks="\n".join(f"- {t['id']} {t['title']}（{t['role']}）：{t['acceptance']}" for t in plan["tasks"])
            batches="\n".join(f"{i}. {', '.join(batch)}" for i,batch in enumerate(artifacts["execution"]["batches"],1))
            raw=f"# {plan['spec'].get('product','开发计划')}\n\n修订 {revision}\n\n## 任务与验收\n\n{tasks}\n\n## 执行批次\n\n{batches}\n\n## 完整快照\n\n{raw}\n"
        return Response(raw,media_type="application/json" if format=="json" else "text/markdown",
                        headers={"Content-Disposition":f'attachment; filename="plan-r{revision}.{"json" if format=="json" else "md"}"'})
    return router
