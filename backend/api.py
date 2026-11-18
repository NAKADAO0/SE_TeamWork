from contextlib import asynccontextmanager
import os

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from pydantic import BaseModel, Field

from .store import PlanStore, ROOT, RevisionConflict


class DraftUpdate(BaseModel):
    expected_revision: int = Field(ge=1, strict=True)
    plan: dict


from .graph_api import graph_router
from .delivery import delivery_router
from .workflow import initialize, workflow_router


def create_app(path=None, planner=None, validator=None, compiler=None):
    store = PlanStore(path or os.getenv("PLAN_DB") or ROOT / ".local/plans.sqlite3")

    @asynccontextmanager
    async def lifespan(app):
        store.initialize()
        initialize(store)
        yield

    app = FastAPI(title="Lab2 计划服务", lifespan=lifespan)

    @app.get("/plans/{plan_id}")
    def get_plan(plan_id: str, revision: int | None = Query(None, ge=1)):
        try:
            return store.read(plan_id, revision)
        except KeyError:
            raise HTTPException(404, detail="计划或修订不存在")

    @app.put("/plans/{plan_id}")
    def update_plan(plan_id: str, update: DraftUpdate):
        draft = dict(update.plan)
        if not isinstance(draft.get("tasks"), list) or not isinstance(draft.get("spec"), dict):
            raise HTTPException(422, detail="草稿需要 spec 对象和 tasks 列表")
        for field in ("batches", "bindings", "topological_levels", "execution_plan_id", "validation_ref"):
            draft.pop(field, None)
        draft["status"] = "draft"
        try:
            return store.append(plan_id, update.expected_revision, draft)
        except KeyError:
            raise HTTPException(404, detail="计划不存在")
        except RevisionConflict as conflict:
            raise HTTPException(409, detail={"current_revision": conflict.args[0]})

    app.add_middleware(CORSMiddleware,
        allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_methods=["GET"], allow_headers=["Content-Type"])
    app.include_router(graph_router(store))
    app.include_router(workflow_router(store, planner))
    app.include_router(delivery_router(store, validator, compiler))
    return app


app = create_app()
