from contextlib import asynccontextmanager
import os

from fastapi import FastAPI, HTTPException, Query

from .store import PlanStore, ROOT


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

    return app


app = create_app()
