"""Reuse existing route services without changing their prompts, contracts or business rules.

The existing services are closure-based route handlers. This adapter binds their
public inputs once; a future service extraction only needs to replace this file.
"""
from fastapi import BackgroundTasks
from ..workflow import JobRequest, SpecConfirmation
from ..delivery import Revision, CompileRequest, Confirmation

class PlanningAdapter:
    def __init__(self, workflow, delivery):
        self.handlers = {route.path: route.endpoint for route in [*workflow.routes, *delivery.routes]}

    def create_spec(self, spec):
        value = self.handlers["/specs"](spec)
        self.handlers["/specs/{sid}/confirm"](value["spec_id"], SpecConfirmation(expected_version=1))
        return {"spec_id": value["spec_id"], "version": 1}

    def plan(self, spec_ref):
        background = BackgroundTasks()
        submitted = self.handlers["/planning-jobs"](JobRequest(spec_ref=spec_ref, mode="live_llm"), background)
        for task in background.tasks:
            task.func(*task.args, **task.kwargs)
        result = self.handlers["/planning-jobs/{jid}"](submitted["job_id"])
        if result["status"] != "succeeded":
            raise ValueError("PM 规划未通过：" + str(result))
        return result["plan_ref"]

    def validate(self, ref):
        return self.handlers["/plans/{pid}/validate"](ref["plan_id"], Revision(revision=ref["revision"]))

    def compile(self, ref):
        return self.handlers["/plans/{pid}/compile"](ref["plan_id"], CompileRequest(revision=ref["revision"]))

    def confirm(self, ref, execution_id):
        return self.handlers["/plans/{pid}/confirm"](ref["plan_id"], Confirmation(revision=ref["revision"], execution_plan_id=execution_id))
