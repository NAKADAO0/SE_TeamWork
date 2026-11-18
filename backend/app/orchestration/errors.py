"""编排领域的错误码与异常。

与校验器（成员 3）的错误码分开维护：校验回答“输入是否合法”，
编排回答“这一次能否排批”。成员 5 可以把 :attr:`OrchestrationError.status`
直接映射成 HTTP 状态码，把 ``as_dict()`` 放进统一错误对象。

命名约定：``ORCH_大类_问题``，全部大写，与 ``backend/app/validation/codes.py``
风格一致；文档 docs/编排规范.md 第 5 节维护同一张表。
"""

from __future__ import annotations

from typing import Any

# --- 准入：编排前必须满足的条件 -------------------------------------------
VALIDATION_MISSING = "ORCH_VALIDATION_MISSING"
VALIDATION_BLOCKED = "ORCH_VALIDATION_BLOCKED"
VALIDATION_STALE = "ORCH_VALIDATION_STALE"

# --- 输入与配置 -----------------------------------------------------------
PLAN_INVALID = "ORCH_PLAN_INVALID"
CONFIG_INVALID = "ORCH_CONFIG_INVALID"
ROLE_CAPACITY_MISSING = "ORCH_ROLE_CAPACITY_MISSING"

# --- 图结构（防御式；正常路径已由成员 3 的校验器拦截） ---------------------
TASK_ID_DUPLICATE = "ORCH_TASK_ID_DUPLICATE"
DEPENDENCY_UNKNOWN = "ORCH_DEPENDENCY_UNKNOWN"
DEPENDENCY_SELF = "ORCH_DEPENDENCY_SELF"
DEPENDENCY_CYCLE = "ORCH_DEPENDENCY_CYCLE"

# --- 排批 -----------------------------------------------------------------
BLOCKED = "ORCH_BLOCKED"
SELF_CHECK_FAILED = "ORCH_SELF_CHECK_FAILED"

ALL_CODES: tuple[str, ...] = (
    VALIDATION_MISSING,
    VALIDATION_BLOCKED,
    VALIDATION_STALE,
    PLAN_INVALID,
    CONFIG_INVALID,
    ROLE_CAPACITY_MISSING,
    TASK_ID_DUPLICATE,
    DEPENDENCY_UNKNOWN,
    DEPENDENCY_SELF,
    DEPENDENCY_CYCLE,
    BLOCKED,
    SELF_CHECK_FAILED,
)

#: 建议的 HTTP 映射：陈旧报告/修订属于版本冲突（409），其余输入问题为 422。
STATUS_BY_CODE: dict[str, int] = {
    VALIDATION_MISSING: 422,
    VALIDATION_BLOCKED: 422,
    VALIDATION_STALE: 409,
    PLAN_INVALID: 422,
    CONFIG_INVALID: 422,
    ROLE_CAPACITY_MISSING: 422,
    TASK_ID_DUPLICATE: 422,
    DEPENDENCY_UNKNOWN: 422,
    DEPENDENCY_SELF: 422,
    DEPENDENCY_CYCLE: 422,
    BLOCKED: 422,
    SELF_CHECK_FAILED: 500,
}


class OrchestrationError(ValueError):
    """编排无法进行时抛出；携带错误码、字段路径与可选细节。

    继承 :class:`ValueError`，因此既有的“领域错误按 422 处理”的调用方
    （例如 ``backend/workflow.py`` 捕获 ``ValueError``）无需改动也能接住。
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        field_path: str = "",
        details: dict[str, Any] | None = None,
        status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.field_path = field_path
        self.details = details or {}
        self.status = status if status is not None else STATUS_BY_CODE.get(code, 422)

    def as_dict(self, request_id: str | None = None) -> dict[str, Any]:
        """统一错误对象：code / message / details / request_id（设计文档 §7.1）。"""
        error: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "details": {"field_path": self.field_path, **self.details}
            if self.field_path
            else dict(self.details),
        }
        if request_id:
            error["request_id"] = request_id
        return error

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return f"OrchestrationError({self.code!r}, {self.message!r})"


__all__ = [
    "ALL_CODES",
    "BLOCKED",
    "CONFIG_INVALID",
    "DEPENDENCY_CYCLE",
    "DEPENDENCY_SELF",
    "DEPENDENCY_UNKNOWN",
    "OrchestrationError",
    "PLAN_INVALID",
    "ROLE_CAPACITY_MISSING",
    "SELF_CHECK_FAILED",
    "STATUS_BY_CODE",
    "TASK_ID_DUPLICATE",
    "VALIDATION_BLOCKED",
    "VALIDATION_MISSING",
    "VALIDATION_STALE",
]
