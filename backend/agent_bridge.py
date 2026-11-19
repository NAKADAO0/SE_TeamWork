"""Run confirmed Lab2 plans on the local Lab1 discussion engine."""
from contextlib import closing
import json
import os
import re
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field


class StartRun(BaseModel):
    revision: int = Field(ge=1)
    mode: Literal["demo", "live"]


class Comment(BaseModel):
    text: str = Field(min_length=1, max_length=3000)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def lab1_request(path, payload=None):
    port = os.getenv("LAB1_PORT", "8051")
    if not re.fullmatch(r"[0-9]{1,5}", port) or not 1 <= int(port) <= 65535:
        raise HTTPException(503, "Lab1 协作服务端口配置无效")
    request = Request(f"http://127.0.0.1:{port}{path}",
                      data=None if payload is None else json.dumps(payload).encode(),
                      headers={"Content-Type": "application/json"})
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=5) as response:
            return json.load(response)
    except HTTPError as exc:
        if exc.code in {404, 409, 422}:
            try:
                detail = json.load(exc).get("detail")
            except (ValueError, AttributeError):
                detail = None
            raise HTTPException(exc.code, detail if isinstance(detail, str) else "Lab1 无法执行此操作") from exc
        raise HTTPException(502, "Lab1 协作服务返回异常") from exc
    except (URLError, TimeoutError, ValueError) as exc:
        raise HTTPException(503, "无法连接 Lab1 协作服务，请确认 Lab1 已启动") from exc


def agent_router(store):
    router = APIRouter(prefix="/plans/{pid}/agent-run", tags=["Agent execution"])

    def snapshot(pid, revision, *, latest=False):
        with closing(store.connect()) as db:
            db.execute("BEGIN")
            row = db.execute("""SELECT p.revision,r.document FROM plans p
                JOIN revisions r ON r.plan_id=p.id WHERE p.id=? AND r.revision=?""",
                (pid, revision)).fetchone()
            if not row:
                raise HTTPException(404, "计划修订不存在")
            if latest and row[0] != revision:
                raise HTTPException(409, "请确认最新修订后再派发")
            artifacts = {k: json.loads(body) for k, body in db.execute("""
                SELECT kind,body FROM artifacts WHERE plan_id=? AND revision=?
                AND kind IN ('confirmation','execution','validation')""", (pid, revision))}
        value = artifacts.get("execution") or {}
        execution_id = value.get("execution_plan_id")
        if not execution_id or value.get("compiled") is False or execution_id != (
            artifacts.get("confirmation") or {}
        ).get("execution_plan_id"):
            raise HTTPException(409, "请先确认有效的执行计划")
        plan = dict(json.loads(row[1]), plan_id=pid, revision=revision, status="confirmed")
        return dict(plan_id=pid, revision=revision, status="confirmed", plan=plan, **artifacts)

    def find(pid, revision, run):
        if not re.fullmatch(r"[0-9a-f]{14}", run):
            raise HTTPException(404, "未找到这条运行记录")
        bundle = snapshot(pid, revision)
        route = f"/api/runs/{run}"
        state = lab1_request(route)
        execution = state.get("execution") or {}
        if (state.get("id"), execution.get("plan_id"), execution.get("revision"), execution.get("id")) != (
            run, pid, revision, bundle["execution"]["execution_plan_id"]
        ):
            raise HTTPException(404, "未找到这条运行记录")
        return route, state

    @router.post("", status_code=201)
    def start(pid: str, body: StartRun):
        bundle = snapshot(pid, body.revision, latest=True)
        return lab1_request("/api/executions", {"bundle": bundle, "mode": body.mode})

    @router.get("/{run}")
    def read(pid: str, run: str, revision: int = Query(ge=1)):
        return find(pid, revision, run)[1]

    @router.post("/{run}/interrupt")
    def interrupt(pid: str, run: str, body: Comment, revision: int = Query(ge=1)):
        note = body.text.strip()
        if not note:
            raise HTTPException(422, "请输入讨论意见")
        route, _ = find(pid, revision, run)
        return lab1_request(route + "/interrupt", {"text": note})

    @router.post("/{run}/resume")
    def resume(pid: str, run: str, revision: int = Query(ge=1)):
        route, _ = find(pid, revision, run)
        return lab1_request(route + "/resume", {})

    return router
