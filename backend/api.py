from contextlib import asynccontextmanager
import os
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent_bridge import agent_router
from .studio import storage as studio_storage
from .studio.api import studio_router
from .delivery import delivery_router
from .graph_api import graph_router
from .review import review_router
from .store import PlanStore, ROOT, RevisionConflict
from .workflow import initialize, live_llm_env_ready, workflow_router


class DraftUpdate(BaseModel):
    expected_revision: int = Field(ge=1, strict=True)
    plan: dict[str, Any] | None = None
    tasks: list[dict[str, Any]] | None = None
    config: dict[str, Any] | None = None


def create_app(path=None, planner=None, validator=None, compiler=None):
    store = PlanStore(path or os.getenv("PLAN_DB") or ROOT / ".local/plans.sqlite3")

    @asynccontextmanager
    async def lifespan(app):
        store.initialize()
        initialize(store)
        studio_storage.initialize(store)
        yield

    app = FastAPI(title="Lab2 计划服务", lifespan=lifespan)

    @app.middleware("http")
    async def revalidate_frontend(request: Request, call_next):
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.endswith(("/", ".html", ".js", ".css")):
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "live_llm_configured": bool(planner is not None or live_llm_env_ready()),
            "modes": ["offline_rule", "live_llm", "fixture_replay"],
        }

    @app.get("/plans/{plan_id}")
    def get_plan(plan_id: str, revision: int | None = Query(None, ge=1)):
        try:
            return store.read(plan_id, revision)
        except KeyError:
            raise HTTPException(404, detail="计划或修订不存在")

    @app.put("/plans/{plan_id}")
    def update_plan(plan_id: str, update: DraftUpdate):
        if update.plan is not None:
            draft = dict(update.plan)
        elif isinstance(update.tasks, list):
            try:
                current_doc = store.read(plan_id)["plan"]
            except KeyError:
                raise HTTPException(404, detail="计划不存在")
            draft = {**current_doc, "tasks": update.tasks}
            if update.config is not None:
                draft["config"] = update.config
                if "max_concurrency" in update.config:
                    draft["capacity"] = update.config["max_concurrency"]
        else:
            raise HTTPException(422, detail="草稿需要 plan 对象或 tasks 列表")

        has_spec = isinstance(draft.get("spec"), dict) or isinstance(draft.get("normalized_spec"), dict) or isinstance(draft.get("spec_ref"), dict)
        if not isinstance(draft.get("tasks"), list) or not has_spec:
            raise HTTPException(422, detail="草稿需要 spec 对象和 tasks 列表")

        for field in ("batches", "batch_details", "bindings", "topological_levels", "execution_plan_id", "validation_ref"):
            draft.pop(field, None)
        draft["status"] = "draft"
        draft["revision"] = update.expected_revision + 1
        try:
            return store.append(plan_id, update.expected_revision, draft)
        except KeyError:
            raise HTTPException(404, detail="计划不存在")
        except RevisionConflict as conflict:
            raise HTTPException(409, detail={"current_revision": conflict.args[0]})

    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type"],
    )
    app.include_router(graph_router(store))
    workflow = workflow_router(store, planner)
    delivery = delivery_router(store, validator, compiler)
    app.include_router(workflow)
    app.include_router(delivery)
    app.include_router(studio_router(store, workflow, delivery))
    app.include_router(review_router(store))
    app.include_router(agent_router(store))

    @app.get("/workspace.html", include_in_schema=False)
    def legacy_workspace(request: Request):
        query = f"?{request.url.query}" if request.url.query else ""
        return RedirectResponse(f"/frontend/{query}")

    # 同源静态资源托管：一条 uvicorn 命令即可完整运行前端工作台、图谱与后端接口
    for page in ("index.html", "graph.html", "examples.html", "guide.html"):
        page_path = ROOT / page
        if page_path.exists():
            route = "/" if page == "index.html" else f"/{page}"

            def _make_handler(target=page_path):
                return lambda: FileResponse(target)

            app.add_api_route(route, _make_handler(), methods=["GET"], include_in_schema=False)
            if page == "index.html":
                app.add_api_route("/index.html", _make_handler(), methods=["GET"], include_in_schema=False)

    for folder, html_mode in (("frontend", True), ("demo", False), ("examples", False), ("templates", False)):
        folder_path = ROOT / folder
        if folder_path.exists():
            app.mount(f"/{folder}", StaticFiles(directory=folder_path, html=html_mode), name=f"static-{folder}")

    return app


app = create_app()
