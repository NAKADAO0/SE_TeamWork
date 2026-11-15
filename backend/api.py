from contextlib import asynccontextmanager
import os

from fastapi import FastAPI, HTTPException, Query

from pydantic import BaseModel, Field

from .store import PlanStore, ROOT, RevisionConflict


class DraftUpdate(BaseModel):
    expected_revision: int = Field(ge=1, strict=True)
    plan: dict


def create_app(path=None):
    store = PlanStore(path or os.getenv("PLAN_DB") or ROOT / ".local/plans.sqlite3")

    @asynccontextmanager
    async def lifespan(app):
        store.initialize()
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

    return app


app = create_app()
