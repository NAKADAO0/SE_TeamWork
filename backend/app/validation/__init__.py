"""领域校验包：结构检查 + 规格/计划/执行计划校验。"""

from .validator import (
    validate_design_spec,
    validate_execution_plan,
    validate_task_plan,
)

__all__ = [
    "validate_design_spec",
    "validate_task_plan",
    "validate_execution_plan",
]
