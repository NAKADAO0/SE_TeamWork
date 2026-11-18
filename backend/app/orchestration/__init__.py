"""成员 4 交付：确定性编排领域服务。

公共入口：

- :func:`compile_plan` —— 消费已校验的 TaskPlan，产出 ExecutionPlan（nodes、edges、
  topological_levels、batches、bindings、validation_ref）与独立的等待原因说明；
- :func:`compile_batches` / :func:`as_compiler` —— 兼容成员 5 现有的
  ``compiler(plan) -> 批次`` 注入点，便于增量接入；
- :class:`OrchestrationError` —— 带错误码与建议 HTTP 状态的领域异常。

目录约定见 ``docs/编排规范.md``；交接说明见 ``docs/handoff/成员4-交接.md``。
"""

from .adapter import (
    as_compiler,
    build_graph_preview,
    compile_batches,
    explain_batches,
)
from .batches import (
    ORDERING_KEYS,
    WAIT_DEPENDENCY,
    WAIT_GLOBAL_CAPACITY,
    WAIT_ROLE_CAPACITY,
    BatchResult,
    RunConfig,
    ScheduleResult,
    schedule,
)
from .errors import (
    ALL_CODES,
    BLOCKED,
    CONFIG_INVALID,
    DEPENDENCY_CYCLE,
    DEPENDENCY_SELF,
    DEPENDENCY_UNKNOWN,
    PLAN_INVALID,
    ROLE_CAPACITY_MISSING,
    SELF_CHECK_FAILED,
    STATUS_BY_CODE,
    TASK_ID_DUPLICATE,
    VALIDATION_BLOCKED,
    VALIDATION_MISSING,
    VALIDATION_STALE,
    OrchestrationError,
)
from .graph import TaskGraph, build_graph, format_cycle
from .service import OrchestrationResult, compile_plan, explain_blocker, summary

__all__ = [
    "ALL_CODES",
    "BLOCKED",
    "CONFIG_INVALID",
    "DEPENDENCY_CYCLE",
    "DEPENDENCY_SELF",
    "DEPENDENCY_UNKNOWN",
    "ORDERING_KEYS",
    "PLAN_INVALID",
    "ROLE_CAPACITY_MISSING",
    "SELF_CHECK_FAILED",
    "STATUS_BY_CODE",
    "TASK_ID_DUPLICATE",
    "VALIDATION_BLOCKED",
    "VALIDATION_MISSING",
    "VALIDATION_STALE",
    "WAIT_DEPENDENCY",
    "WAIT_GLOBAL_CAPACITY",
    "WAIT_ROLE_CAPACITY",
    "BatchResult",
    "OrchestrationError",
    "OrchestrationResult",
    "RunConfig",
    "ScheduleResult",
    "TaskGraph",
    "as_compiler",
    "build_graph",
    "build_graph_preview",
    "compile_batches",
    "compile_plan",
    "explain_batches",
    "explain_blocker",
    "format_cycle",
    "schedule",
    "summary",
]
