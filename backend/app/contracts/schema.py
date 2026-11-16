"""从契约模型导出 JSON Schema，并提供导出一致性检查。

同一份模型（models.py）既生成 ``schema/*.schema.json``，也在运行时提供
结构校验所需的 Schema，保证“模型 ↔ Schema ↔ 校验”三者不会漂移。
"""

from __future__ import annotations

import json
import types
import typing
from dataclasses import MISSING, fields, is_dataclass
from pathlib import Path
from typing import Any, get_args, get_origin, get_type_hints

from .models import (
    DesignSpec,
    ExecutionPlan,
    ID_PATTERN,
    TaskPlan,
    ValidationReport,
    WorkflowTemplate,
)

CONTRACT_MODELS: dict[str, type] = {
    "design_spec": DesignSpec,
    "workflow_template": WorkflowTemplate,
    "task_plan": TaskPlan,
    "execution_plan": ExecutionPlan,
    "validation_report": ValidationReport,
}

SCHEMA_DIR = Path(__file__).resolve().parent / "schema"
DIALECT = "https://json-schema.org/draft/2020-12/schema"


def _is_optional(annotation: Any) -> tuple[bool, Any]:
    origin = get_origin(annotation)
    if origin is typing.Union or origin is types.UnionType:
        args = [a for a in get_args(annotation) if a is not type(None)]
        return True, args[0] if len(args) == 1 else annotation
    return False, annotation


def _with_description(schema: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    if meta.get("description"):
        schema["description"] = meta["description"]
    return schema


def _value_schema(annotation: Any, meta: dict[str, Any], defs: dict[str, dict]) -> dict[str, Any]:
    optional, inner = _is_optional(annotation)
    if optional:
        return _value_schema(inner, meta, defs)

    if annotation is str:
        schema: dict[str, Any] = {"type": "string"}
        if meta.get("pattern"):
            schema["pattern"] = meta["pattern"]
        if meta.get("choices"):
            schema["enum"] = list(meta["choices"])
        if meta.get("min_length") is not None:
            schema["minLength"] = meta["min_length"]
        elif not meta.get("allow_empty"):
            schema["minLength"] = 1
        return _with_description(schema, meta)

    if annotation is bool:
        return _with_description({"type": "boolean"}, meta)

    if annotation is int:
        schema = {"type": "integer"}
        if meta.get("minimum") is not None:
            schema["minimum"] = meta["minimum"]
        if meta.get("maximum") is not None:
            schema["maximum"] = meta["maximum"]
        return _with_description(schema, meta)

    if annotation is float:
        schema = {"type": "number"}
        if meta.get("exclusive_minimum") is not None:
            schema["exclusiveMinimum"] = meta["exclusive_minimum"]
        if meta.get("minimum") is not None:
            schema["minimum"] = meta["minimum"]
        if meta.get("maximum") is not None:
            schema["maximum"] = meta["maximum"]
        return _with_description(schema, meta)

    origin = get_origin(annotation)
    if origin is list:
        (item_type,) = get_args(annotation)
        item_meta: dict[str, Any] = {}
        if meta.get("items_pattern"):
            item_meta["pattern"] = meta["items_pattern"]
        if meta.get("items_choices"):
            item_meta["choices"] = meta["items_choices"]
        schema = {"type": "array", "items": _value_schema(item_type, item_meta, defs)}
        if meta.get("min_items") is not None:
            schema["minItems"] = meta["min_items"]
        return _with_description(schema, meta)

    if origin is dict:
        _, value_type = get_args(annotation)
        value_meta = {"minimum": 1} if value_type is int else {}
        schema = {
            "type": "object",
            "propertyNames": {"pattern": ID_PATTERN},
            "additionalProperties": _value_schema(value_type, value_meta, defs),
            "minProperties": meta.get("min_properties", 1),
        }
        return _with_description(schema, meta)

    if is_dataclass(annotation):
        name = annotation.__name__
        if name not in defs:
            defs[name] = _object_schema(annotation, defs)
        return {"$ref": f"#/$defs/{name}"}

    raise TypeError(f"无法为注解 {annotation!r} 生成 JSON Schema")


def _object_schema(cls: type, defs: dict[str, dict]) -> dict[str, Any]:
    hints = get_type_hints(cls)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for f in fields(cls):
        properties[f.name] = _value_schema(hints[f.name], dict(f.metadata), defs)
        if f.default is MISSING and f.default_factory is MISSING:
            required.append(f.name)
    return {
        "type": "object",
        "title": cls.__name__,
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def model_schema(name: str, model: type) -> dict[str, Any]:
    """生成单份带 ``$defs`` 的 JSON Schema 文档。"""
    defs: dict[str, dict] = {}
    root = _object_schema(model, defs)
    defs[model.__name__] = root
    return {
        "$schema": DIALECT,
        "$id": f"{name}.schema.json",
        "title": model.__name__,
        "$ref": f"#/$defs/{model.__name__}",
        "$defs": defs,
    }


def contract_schemas() -> dict[str, dict[str, Any]]:
    """返回 {契约键: Schema 文档}，键与 CONTRACT_MODELS 一致。"""
    return {name: model_schema(name, model) for name, model in CONTRACT_MODELS.items()}


def schema_documents() -> dict[str, dict[str, Any]]:
    """返回 {文件名: Schema 文档}，用于写盘。"""
    return {
        f"{name}.schema.json": model_schema(name, model)
        for name, model in CONTRACT_MODELS.items()
    }


def write_schemas(directory: Path | None = None) -> list[Path]:
    """把全部 Schema 写入目录（默认 backend/app/contracts/schema）。"""
    target_dir = Path(directory) if directory is not None else SCHEMA_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for filename, document in schema_documents().items():
        path = target_dir / filename
        text = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        path.write_text(text, encoding="utf-8", newline="\n")
        written.append(path)
    return written


def check_schemas(directory: Path | None = None) -> list[str]:
    """检查磁盘上的 Schema 是否与模型导出结果一致，返回问题列表。"""
    target_dir = Path(directory) if directory is not None else SCHEMA_DIR
    problems: list[str] = []
    for filename, document in schema_documents().items():
        path = target_dir / filename
        if not path.exists():
            problems.append(f"缺少 Schema 文件：{filename}")
            continue
        try:
            on_disk = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            problems.append(f"{filename} 不是合法 JSON：{exc}")
            continue
        if on_disk != document:
            problems.append(f"{filename} 与模型导出的 Schema 不一致，请运行 export_schema --write")
    expected = set(schema_documents())
    if target_dir.exists():
        for path in sorted(target_dir.glob("*.schema.json")):
            if path.name not in expected:
                problems.append(f"多余 Schema 文件：{path.name}")
    return problems


__all__ = [
    "CONTRACT_MODELS",
    "SCHEMA_DIR",
    "DIALECT",
    "model_schema",
    "contract_schemas",
    "schema_documents",
    "write_schemas",
    "check_schemas",
]
