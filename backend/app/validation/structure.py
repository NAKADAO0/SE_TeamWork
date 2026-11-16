"""结构校验：用模型导出的 JSON Schema 检查文档形状。

只覆盖本契约用到的 JSON Schema 子集：
``type / required / properties / additionalProperties / items / enum /
pattern / minLength / minItems / minProperties / minimum / maximum /
exclusiveMinimum / propertyNames``。

输出统一问题对象（code=FIELD_*），字段路径与语义校验保持一致：
``tasks[0].depends_on[1].task_id``；根文档类型错误的路径记为 ``$``。
"""

from __future__ import annotations

import re
from typing import Any

from ..contracts.models import ValidationIssue
from . import codes as C

_TYPE_NAMES = {
    "object": "对象",
    "array": "数组",
    "string": "字符串",
    "integer": "整数",
    "number": "数字",
    "boolean": "布尔值",
}


def display_path(path: str) -> str:
    return path or "$"


def join_path(path: str, name: str) -> str:
    return name if not path else f"{path}.{name}"


def index_path(path: str, index: int) -> str:
    return f"{path}[{index}]"


def _issue(
    code: str,
    path: str,
    message: str,
    related_ids: list[str] | None = None,
    suggested_action: str = "按共享契约修正该字段后重新校验",
) -> ValidationIssue:
    return ValidationIssue(
        code=code,
        severity="error",
        object_id="",
        field_path=display_path(path),
        message=message,
        related_ids=list(related_ids or []),
        suggested_action=suggested_action,
    )


def _actual_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "布尔值"
    if isinstance(value, int):
        return "整数"
    if isinstance(value, float):
        return "浮点数"
    if isinstance(value, str):
        return "字符串"
    if isinstance(value, list):
        return "数组"
    if isinstance(value, dict):
        return "对象"
    return type(value).__name__


def matches_type(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def _resolve(ref: str, root: dict[str, Any]) -> dict[str, Any]:
    prefix = "#/$defs/"
    if not ref.startswith(prefix):
        raise ValueError(f"不支持的 $ref：{ref}")
    name = ref[len(prefix) :]
    try:
        return root["$defs"][name]
    except KeyError as exc:  # pragma: no cover - 只在生成器出错时触发
        raise ValueError(f"$defs 中不存在 {name}") from exc


def check_structure(
    document: Any, schema: dict[str, Any], *, object_id: str = ""
) -> list[ValidationIssue]:
    """按 Schema 检查文档结构，返回统一问题对象列表（全部为 error）。"""
    issues: list[ValidationIssue] = []
    _walk(document, schema, "", issues, schema)
    for issue in issues:
        if not issue.object_id:
            issue.object_id = object_id or "document"
    return issues


def _walk(
    value: Any,
    schema: dict[str, Any],
    path: str,
    issues: list[ValidationIssue],
    root: dict[str, Any],
) -> None:
    if not isinstance(schema, dict):  # pragma: no cover - 生成器不会产出非对象 Schema
        return
    if "$ref" in schema:
        _walk(value, _resolve(schema["$ref"], root), path, issues, root)
        return

    expected = schema.get("type")
    if expected is not None and not matches_type(value, expected):
        issues.append(
            _issue(
                C.FIELD_TYPE,
                path,
                f"{display_path(path)} 期望为{_TYPE_NAMES.get(expected, expected)}，"
                f"实际为{_actual_type(value)}",
                suggested_action="按共享契约修正字段类型",
            )
        )
        return

    if expected == "object" or ("properties" in schema and isinstance(value, dict)):
        _walk_object(value, schema, path, issues, root)
    elif expected == "array" and isinstance(value, list):
        _walk_array(value, schema, path, issues, root)
    elif isinstance(value, str):
        _walk_string(value, schema, path, issues)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        _walk_number(value, schema, path, issues)


def _walk_object(
    value: dict,
    schema: dict[str, Any],
    path: str,
    issues: list[ValidationIssue],
    root: dict[str, Any],
) -> None:
    properties = schema.get("properties", {})
    for name in schema.get("required", []):
        if name not in value:
            child = join_path(path, name)
            issues.append(
                _issue(
                    C.FIELD_REQUIRED,
                    child,
                    f"缺少必填字段 {display_path(child)}",
                    suggested_action="补充该字段后重新校验",
                )
            )
    for name, prop_schema in properties.items():
        if name in value:
            _walk(value[name], prop_schema, join_path(path, name), issues, root)

    additional = schema.get("additionalProperties")
    if additional is False:
        for name in value:
            if name not in properties:
                child = join_path(path, name)
                issues.append(
                    _issue(
                        C.FIELD_UNKNOWN,
                        child,
                        f"字段 {display_path(child)} 不属于当前契约，已拒绝未知字段",
                        suggested_action="移除未知字段，或先更新共享契约",
                    )
                )
    elif isinstance(additional, dict):
        key_schema = schema.get("propertyNames", {})
        key_pattern = key_schema.get("pattern")
        for name, item in value.items():
            child = join_path(path, name)
            if key_pattern and not re.search(key_pattern, str(name)):
                issues.append(
                    _issue(
                        C.FIELD_PATTERN,
                        child,
                        f"键名 {display_path(child)} 不符合格式 {key_pattern}",
                        suggested_action="改用契约允许的键名格式（字母开头的 ID）",
                    )
                )
            _walk(item, additional, child, issues, root)


def _walk_array(
    value: list,
    schema: dict[str, Any],
    path: str,
    issues: list[ValidationIssue],
    root: dict[str, Any],
) -> None:
    min_items = schema.get("minItems")
    if min_items is not None and len(value) < min_items:
        issues.append(
            _issue(
                C.FIELD_EMPTY,
                path,
                f"{display_path(path)} 至少需要 {min_items} 项，当前为 {len(value)} 项",
                suggested_action="补充至少一项后重新校验",
            )
        )
    item_schema = schema.get("items")
    if isinstance(item_schema, dict):
        for index, item in enumerate(value):
            _walk(item, item_schema, index_path(path, index), issues, root)


def _walk_string(value: str, schema: dict[str, Any], path: str, issues: list[ValidationIssue]) -> None:
    choices = schema.get("enum")
    if choices is not None and value not in choices:
        issues.append(
            _issue(
                C.FIELD_CHOICE,
                path,
                f"{display_path(path)} 的取值 {value!r} 不在允许范围 {list(choices)} 内",
                suggested_action="从契约枚举中选择合法取值",
            )
        )
    min_length = schema.get("minLength")
    if min_length is not None and len(value) < min_length:
        issues.append(
            _issue(
                C.FIELD_EMPTY,
                path,
                f"{display_path(path)} 不能为空字符串",
                suggested_action="填写非空内容后重新校验",
            )
        )
    pattern = schema.get("pattern")
    if pattern and not re.search(pattern, value):
        issues.append(
            _issue(
                C.FIELD_PATTERN,
                path,
                f"{display_path(path)} 的取值 {value!r} 不符合格式 {pattern}",
                suggested_action="按契约格式修正，如 ID 须以字母开头且只含字母、数字、下划线、连字符",
            )
        )


def _walk_number(value: float, schema: dict[str, Any], path: str, issues: list[ValidationIssue]) -> None:
    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    exclusive_minimum = schema.get("exclusiveMinimum")
    if minimum is not None and value < minimum:
        issues.append(
            _issue(
                C.FIELD_RANGE,
                path,
                f"{display_path(path)} 不能小于 {minimum}，当前为 {value}",
                suggested_action="调整为允许范围内的数值",
            )
        )
    if maximum is not None and value > maximum:
        issues.append(
            _issue(
                C.FIELD_RANGE,
                path,
                f"{display_path(path)} 不能大于 {maximum}，当前为 {value}",
                suggested_action="调整为允许范围内的数值",
            )
        )
    if exclusive_minimum is not None and value <= exclusive_minimum:
        issues.append(
            _issue(
                C.FIELD_RANGE,
                path,
                f"{display_path(path)} 必须大于 {exclusive_minimum}，当前为 {value}",
                suggested_action="调整为大于零的数值",
            )
        )


__all__ = ["check_structure", "display_path", "join_path", "index_path", "matches_type"]
