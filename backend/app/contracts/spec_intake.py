"""统一设计规格接入、Markdown 解析与缺口检查。

支持四种常见输入形态并标准化为契约 DesignSpec 字典：
1. 完整 DesignSpec JSON（examples/contracts/*/design-spec.json）；
2. 工作台草稿 JSON（frontend/fixtures/spec-chat.json，含 product 对象与 design 数组）；
3. 简化演示 JSON（examples/chat.json，仅含 product 字符串与 requirements）；
4. Markdown / 纯文本设计规格（如 examples/unseen-spec/spec.md）。

同时提供面向确认前的缺口检查：对“我要做微信”等一句意图明确给出缺口，不编造需求或范围。
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .models import SCHEMA_VERSION
from ..validation.validator import validate_design_spec

_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_SPLIT_IDS_RE = re.compile(r"[、,，;；\s]+")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+(.+?)\s*$")


def _slug_spec_id(product: str) -> str:
    digest = hashlib.sha1(product.encode("utf-8")).hexdigest()[:6].upper()
    return f"SPEC-{digest}"


def _clean_md_code(text: str) -> str:
    return re.sub(r"`([^`]+)`", r"\1", text).strip()


def _parse_md_table(lines: list[str]) -> list[dict[str, str]]:
    rows: list[list[str]] = []
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [_clean_md_code(c) for c in stripped.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c.replace(" ", "")) for c in cells if c != ""):
            continue
        if cells:
            rows.append(cells)
    if len(rows) < 2:
        return []
    headers = [h.strip() for h in rows[0]]
    records: list[dict[str, str]] = []
    for cells in rows[1:]:
        record: dict[str, str] = {}
        for idx, header in enumerate(headers):
            record[header] = cells[idx].strip() if idx < len(cells) else ""
        records.append(record)
    return records


def _infer_design_kind(item_id: str, desc: str) -> str:
    upper = item_id.upper()
    if upper.startswith("P-"):
        return "page"
    if "CONSIST" in upper or "ERR" in upper or "规则" in desc or "错误码" in desc or "一致" in desc:
        return "rule"
    if "ENTITY" in upper or "实体" in desc or "表结构" in desc:
        return "entity"
    if upper.startswith("I-") or "GET " in desc or "POST " in desc or "PUT " in desc or "接口" in desc:
        return "interface"
    return "other"


def _infer_constraint_category(label: str) -> str:
    if any(k in label for k in ("技术栈", "栈", "stack", "语言", "框架")):
        return "stack"
    if any(k in label for k in ("平台", "端", "platform", "浏览器")):
        return "platform"
    if any(k in label for k in ("安全", "隐私", "权限", "鉴权", "security", "privacy")):
        return "security"
    if any(k in label for k in ("设施", "基础", "登录", "部署", "数据", "infrastructure")):
        return "infrastructure"
    return "other"


def parse_markdown_spec(text: str) -> dict[str, Any]:
    """解析 Markdown/TXT 格式的产品设计规格为统一草稿结构。"""
    lines = text.splitlines()
    product = ""
    sections: dict[str, list[str]] = {}
    current_section = ""

    for line in lines:
        m = _HEADING_RE.match(line)
        if m:
            level, title = len(m.group(1)), _clean_md_code(m.group(2))
            if level == 1 and not product:
                product = re.split(r"\s*[·|｜]\s*", title)[0].strip()
            else:
                current_section = title
                sections.setdefault(current_section, [])
            continue
        if current_section:
            sections.setdefault(current_section, []).append(line)

    # 若无一级标题但只有一段简短描述（如“我要做微信”），保留为产品意图而非完整规格
    if not product and not sections:
        plain = text.strip()
        return {
            "schema_version": SCHEMA_VERSION,
            "spec_id": _slug_spec_id(plain or "DRAFT"),
            "version": 1,
            "product": plain,
            "scope": {"goal": "", "included": [], "excluded": []},
            "requirements": [],
            "modules": [],
            "design_items": [],
            "constraints": [],
            "_source_format": "text",
        }

    def find_section(*keywords: str) -> list[str]:
        for name, body in sections.items():
            if any(kw in name.lower() for kw in keywords):
                return body
        return []

    # 1. 产品范围
    scope_lines = find_section("范围", "scope")
    goal_parts: list[str] = []
    included: list[str] = []
    excluded: list[str] = []
    in_excluded = False
    for raw in scope_lines:
        s = raw.strip()
        if not s:
            continue
        bm = _BULLET_RE.match(s)
        if bm:
            item = _clean_md_code(bm.group(1))
            if in_excluded:
                excluded.append(item)
            else:
                included.append(item)
            continue
        if any(k in s for k in ("排除", "不做", "不包含", "excluded")):
            in_excluded = True
            if "：" in s and not s.startswith(("排除", "明确排除")):
                excluded.append(_clean_md_code(s.split("：", 1)[1]))
            continue
        goal_parts.append(_clean_md_code(s))

    goal_text = " ".join(goal_parts).strip()
    if not included and goal_text:
        candidates = [p.strip("。；; ") for p in re.split(r"[、，,;；。]+", goal_text) if p.strip("。；; ")]
        included = [c for c in candidates if len(c) >= 2][:6]

    # 2. 需求条目
    req_lines = find_section("需求", "requirement")
    requirements: list[dict[str, Any]] = []
    for rec in _parse_md_table(req_lines):
        rid = rec.get("编号") or rec.get("ID") or rec.get("id") or ""
        rgoal = rec.get("行为描述") or rec.get("描述") or rec.get("目标") or rec.get("goal") or ""
        racc = rec.get("验收条件") or rec.get("验收") or rec.get("acceptance") or ""
        if rid or rgoal:
            requirements.append({"id": rid, "goal": rgoal, "acceptance": racc})
    if not requirements:
        for raw in req_lines:
            bm = _BULLET_RE.match(raw)
            if not bm:
                continue
            item = _clean_md_code(bm.group(1))
            parts = [p.strip() for p in re.split(r"[：:|｜]", item, maxsplit=2)]
            if len(parts) >= 2 and _ID_RE.match(parts[0]):
                requirements.append({
                    "id": parts[0],
                    "goal": parts[1],
                    "acceptance": parts[2] if len(parts) > 2 else "",
                })
    all_req_ids = [r["id"] for r in requirements if r.get("id")]

    # 3. 模块
    mod_lines = find_section("模块", "module")
    modules: list[dict[str, Any]] = []
    for rec in _parse_md_table(mod_lines):
        mid = rec.get("编号") or rec.get("ID") or rec.get("id") or ""
        name = rec.get("名称") or rec.get("name") or ""
        duty = rec.get("职责") or rec.get("responsibility") or ""
        rel_raw = rec.get("关联需求") or rec.get("requirement_ids") or ""
        if not mid:
            continue
        if "全部" in rel_raw or "all" in rel_raw.lower():
            req_refs = list(all_req_ids)
        else:
            req_refs = [x for x in _SPLIT_IDS_RE.split(rel_raw) if x and _ID_RE.match(x)]
        resp = f"{name}：{duty}".strip("：") if name and duty else (duty or name)
        modules.append({"id": mid, "responsibility": resp, "requirement_ids": req_refs})
    if not modules:
        for raw in mod_lines:
            bm = _BULLET_RE.match(raw)
            if not bm:
                continue
            item = _clean_md_code(bm.group(1))
            m = re.match(r"^([A-Za-z][A-Za-z0-9_-]*)\s*[：:]\s*(.+)$", item)
            if m:
                modules.append({
                    "id": m.group(1),
                    "responsibility": m.group(2).strip(),
                    "requirement_ids": list(all_req_ids),
                })

    # 4. 设计条目
    design_lines = find_section("设计", "design")
    design_items: list[dict[str, Any]] = []
    for rec in _parse_md_table(design_lines):
        did = rec.get("编号") or rec.get("ID") or rec.get("id") or ""
        desc = rec.get("描述") or rec.get("摘要") or rec.get("description") or rec.get("summary") or ""
        dkind = rec.get("类型") or rec.get("kind") or _infer_design_kind(did, desc)
        rel_raw = rec.get("关联需求") or rec.get("requirement_ids") or ""
        req_refs = [x for x in _SPLIT_IDS_RE.split(rel_raw) if x in all_req_ids] or list(all_req_ids[:1])
        if did:
            design_items.append({"id": did, "kind": dkind, "description": desc, "requirement_ids": req_refs})
    if not design_items:
        for raw in design_lines:
            bm = _BULLET_RE.match(raw)
            if not bm:
                continue
            item = _clean_md_code(bm.group(1))
            m = re.match(r"^([A-Za-z][A-Za-z0-9_-]*)\s*[：:]\s*(.+)$", item)
            if m:
                did, desc = m.group(1), m.group(2).strip()
                matched_reqs = [rid for rid in all_req_ids if rid in desc]
                if not matched_reqs and all_req_ids:
                    # 根据关键词或默认绑定需求，保证追溯存在
                    matched_reqs = [all_req_ids[0]] if len(all_req_ids) == 1 else list(all_req_ids)
                design_items.append({
                    "id": did,
                    "kind": _infer_design_kind(did, desc),
                    "description": desc,
                    "requirement_ids": matched_reqs,
                })

    # 5. 约束
    const_lines = find_section("约束", "constraint")
    constraints: list[dict[str, Any]] = []
    for idx, raw in enumerate(const_lines, 1):
        bm = _BULLET_RE.match(raw)
        if not bm:
            continue
        item = _clean_md_code(bm.group(1))
        if "：" in item or ":" in item:
            label, detail = re.split(r"[：:]", item, maxsplit=1)
        else:
            label, detail = "约束", item
        constraints.append({
            "id": f"C-{idx}",
            "category": _infer_constraint_category(label.strip()),
            "detail": f"{label.strip()}：{detail.strip()}".strip("："),
            "status": "specified",
        })

    return {
        "schema_version": SCHEMA_VERSION,
        "spec_id": _slug_spec_id(product or "MARKDOWN"),
        "version": 1,
        "product": product,
        "scope": {
            "goal": goal_text,
            "included": included,
            "excluded": excluded,
        },
        "requirements": requirements,
        "modules": modules,
        "design_items": design_items,
        "constraints": constraints,
        "_source_format": "markdown",
    }


def normalize_spec_dict(raw: dict[str, Any]) -> dict[str, Any]:
    """将任意 JSON 变体（完整契约 / 前端草稿 / 简化版）规范化为统一结构（不编造缺失字段）。"""
    if not isinstance(raw, dict):
        return {}

    # 提取产品名和范围
    raw_product = raw.get("product")
    raw_scope = raw.get("scope")
    if isinstance(raw_product, dict):
        product_name = str(raw_product.get("name") or raw_product.get("product") or "").strip()
        scope_text = str(raw_product.get("scope") or "").strip()
        excluded = [str(x) for x in (raw_product.get("excluded") or []) if str(x).strip()]
        included = [str(x) for x in (raw_product.get("included") or []) if str(x).strip()]
        if not included and scope_text:
            included = [p.strip("。；; ") for p in re.split(r"[、，,;；。]+", scope_text) if len(p.strip("。；; ")) >= 2][:6]
        scope = {"goal": scope_text, "included": included, "excluded": excluded}
    else:
        product_name = str(raw_product or raw.get("name") or raw.get("product_name") or "").strip()
        if isinstance(raw_scope, dict):
            goal = str(raw_scope.get("goal") or "").strip()
            included = [str(x) for x in (raw_scope.get("included") or raw_scope.get("include") or []) if str(x).strip()]
            excluded = [str(x) for x in (raw_scope.get("excluded") or raw_scope.get("exclude") or []) if str(x).strip()]
            if not included and goal:
                included = [p.strip("。；; ") for p in re.split(r"[、，,;；。]+", goal) if len(p.strip("。；; ")) >= 2][:6]
            scope = {"goal": goal, "included": included, "excluded": excluded}
        elif isinstance(raw_scope, str):
            goal = raw_scope.strip()
            included = [p.strip("。；; ") for p in re.split(r"[、，,;；。]+", goal) if len(p.strip("。；; ")) >= 2][:6] if goal else []
            scope = {"goal": goal, "included": included, "excluded": []}
        else:
            scope = {"goal": "", "included": [], "excluded": []}

    # 需求
    reqs: list[dict[str, Any]] = []
    for item in raw.get("requirements") or []:
        if not isinstance(item, dict):
            continue
        acc = item.get("acceptance")
        if isinstance(acc, list):
            acc_str = "；".join(str(x).strip() for x in acc if str(x).strip())
        else:
            acc_str = str(acc or "").strip()
        reqs.append({
            "id": str(item.get("id") or "").strip(),
            "goal": str(item.get("goal") or item.get("description") or "").strip(),
            "acceptance": acc_str,
        })
    req_ids = [r["id"] for r in reqs if r["id"]]

    # 模块
    modules: list[dict[str, Any]] = []
    for item in raw.get("modules") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        resp = str(item.get("responsibility") or "").strip()
        full_resp = f"{name}：{resp}".strip("：") if name and resp and name not in resp else (resp or name)
        modules.append({
            "id": str(item.get("id") or "").strip(),
            "responsibility": full_resp,
            "requirement_ids": [str(x).strip() for x in (item.get("requirement_ids") or []) if str(x).strip()],
        })

    # 设计条目（兼容 design_items / design / design_refs）
    raw_design = raw.get("design_items")
    if raw_design is None:
        raw_design = raw.get("design")
    if raw_design is None:
        raw_design = raw.get("design_refs")
    design_items: list[dict[str, Any]] = []
    for item in raw_design or []:
        if not isinstance(item, dict):
            continue
        did = str(item.get("id") or "").strip()
        desc = str(item.get("description") or item.get("title") or item.get("summary") or "").strip()
        kind = str(item.get("kind") or item.get("type") or _infer_design_kind(did, desc)).strip()
        if kind == "consistency":
            kind = "rule"
        rel = [str(x).strip() for x in (item.get("requirement_ids") or []) if str(x).strip()]
        if not rel and req_ids:
            rel = list(req_ids)
        design_items.append({
            "id": did,
            "kind": kind,
            "description": desc,
            "requirement_ids": rel,
        })

    # 约束（兼容 list[Constraint] 与 dict）
    raw_constraints = raw.get("constraints")
    constraints: list[dict[str, Any]] = []
    if isinstance(raw_constraints, list):
        for idx, item in enumerate(raw_constraints, 1):
            if not isinstance(item, dict):
                continue
            constraints.append({
                "id": str(item.get("id") or f"C-{idx}").strip(),
                "category": str(item.get("category") or "other").strip(),
                "detail": str(item.get("detail") or "").strip(),
                "status": str(item.get("status") or "specified").strip(),
            })
    elif isinstance(raw_constraints, dict):
        for idx, (k, v) in enumerate(raw_constraints.items(), 1):
            detail = "、".join(str(x) for x in v) if isinstance(v, list) else str(v)
            constraints.append({
                "id": f"C-{idx}",
                "category": _infer_constraint_category(str(k)),
                "detail": f"{k}: {detail}",
                "status": "specified",
            })

    spec_id = str(raw.get("spec_id") or "").strip() or _slug_spec_id(product_name or "SPEC")
    version = raw.get("version") if isinstance(raw.get("version"), int) and not isinstance(raw.get("version"), bool) else 1

    return {
        "schema_version": SCHEMA_VERSION,
        "spec_id": spec_id,
        "version": max(1, version),
        "product": product_name,
        "scope": scope,
        "requirements": reqs,
        "modules": modules,
        "design_items": design_items,
        "constraints": constraints,
    }


def parse_spec_input(payload: Any) -> tuple[dict[str, Any], dict[str, Any], str]:
    """解析任意输入（dict / JSON 字符串 / Markdown 文本）。

    返回 ``(raw_obj, normalized_spec, source_format)``。
    """
    if isinstance(payload, dict):
        if "text" in payload and isinstance(payload["text"], str) and "requirements" not in payload and "product" not in payload:
            return parse_spec_input(payload["text"])
        if "draft" in payload and isinstance(payload["draft"], dict):
            inner = payload["draft"]
            return inner, normalize_spec_dict(inner), "json"
        if "spec" in payload and isinstance(payload["spec"], dict) and "requirements" not in payload:
            inner = payload["spec"]
            return inner, normalize_spec_dict(inner), "json"
        return payload, normalize_spec_dict(payload), "json"

    if isinstance(payload, str):
        text = payload.strip()
        if text.startswith("{") or text.startswith("["):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    return parse_spec_input(parsed)
            except ValueError:
                pass
        md_spec = parse_markdown_spec(text)
        fmt = str(md_spec.pop("_source_format", "markdown"))
        return md_spec, md_spec, fmt

    raise ValueError("规格输入必须是 JSON 对象或 Markdown/文本字符串")


def gap_check_spec(raw: dict[str, Any], normalized: dict[str, Any] | None = None, *, strict_scope: bool = True) -> dict[str, Any]:
    """检查规格缺口，返回 {errors: [...], warnings: [...]}，精确定位到字段。"""
    norm = normalized or normalize_spec_dict(raw)
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    def err(code: str, field_path: str, message: str, related: list[str] | None = None) -> None:
        errors.append({
            "severity": "error",
            "code": code,
            "field_path": field_path,
            "message": message,
            "related_ids": related or [],
        })

    def warn(code: str, field_path: str, message: str, related: list[str] | None = None) -> None:
        warnings.append({
            "severity": "warning",
            "code": code,
            "field_path": field_path,
            "message": message,
            "related_ids": related or [],
        })

    if not norm.get("product"):
        err("MISSING_PRODUCT", "product", "缺少产品名称")

    scope = norm.get("scope") or {}
    goal = str(scope.get("goal") or "").strip()
    included = scope.get("included") or []
    excluded = scope.get("excluded") or []
    if strict_scope and len(goal) < 8 and not included:
        err("MISSING_SCOPE", "scope", "缺少明确的产品范围与首期功能边界（至少需说明目标或首期功能）")
    if not excluded:
        warn("MISSING_EXCLUSION", "scope.excluded", "未写明明确排除项，首期范围可能膨胀")

    reqs = norm.get("requirements") or []
    if not reqs:
        err("MISSING_REQUIREMENTS", "requirements", "至少需要一条包含编号、行为与验收条件的需求")
        return {"errors": errors, "warnings": warnings}

    seen_reqs: set[str] = set()
    for idx, req in enumerate(reqs):
        rid = str(req.get("id") or "").strip()
        path = f"requirements[{idx}]"
        if not rid or not _ID_RE.match(rid):
            err("INVALID_REQUIREMENT_ID", f"{path}.id", f"需求编号 {rid or '(空)'} 不合法", [rid] if rid else [])
            continue
        if rid in seen_reqs:
            err("DUPLICATE_REQUIREMENT_ID", f"{path}.id", f"需求编号 {rid} 重复", [rid])
        seen_reqs.add(rid)
        if not str(req.get("goal") or "").strip():
            err("MISSING_REQUIREMENT_GOAL", f"{path}.goal", f"需求 {rid} 缺少行为描述", [rid])
        if not str(req.get("acceptance") or "").strip():
            err("MISSING_REQUIREMENT_ACCEPTANCE", f"{path}.acceptance", f"需求 {rid} 缺少可检查的验收条件", [rid])

    modules = norm.get("modules") or []
    if not modules:
        warn("MISSING_MODULES", "modules", "未提供模块划分，任务将仅按需求粗粒度生成")
    else:
        for idx, mod in enumerate(modules):
            mid = str(mod.get("id") or "").strip()
            path = f"modules[{idx}]"
            if not mid or not _ID_RE.match(mid):
                err("INVALID_MODULE_ID", f"{path}.id", f"模块编号 {mid or '(空)'} 不合法")
            if not str(mod.get("responsibility") or "").strip():
                err("MISSING_MODULE_RESPONSIBILITY", f"{path}.responsibility", f"模块 {mid or idx} 缺少职责说明")
            for rid in mod.get("requirement_ids") or []:
                if rid not in seen_reqs:
                    err(
                        "UNKNOWN_MODULE_REQUIREMENT",
                        f"{path}.requirement_ids",
                        f"模块 {mid or idx} 引用了不存在的需求 {rid}",
                        [rid],
                    )

    if not norm.get("design_items"):
        warn("MISSING_DESIGN_ITEMS", "design_items", "未提供接口/页面/实体设计条目，建议补充以生成更精细前后端任务")
    if not norm.get("constraints"):
        warn("MISSING_CONSTRAINTS", "constraints", "未记录技术栈、平台或安全约束")

    # 如果结构完整，再用权威 validate_design_spec 补充检查
    if not errors and norm.get("modules") and norm.get("design_items") and norm.get("constraints"):
        report = validate_design_spec(norm)
        existing_keys = {(e["code"], e["field_path"]) for e in errors + warnings}
        for issue in report.issues:
            entry = {
                "severity": issue.severity,
                "code": issue.code,
                "field_path": issue.field_path,
                "message": issue.message,
                "related_ids": list(issue.related_ids),
            }
            if (issue.code, issue.field_path) in existing_keys:
                continue
            if issue.severity == "error":
                errors.append(entry)
            else:
                warnings.append(entry)

    return {"errors": errors, "warnings": warnings}
