"""字段名唯一来源。

等成员 3 定稿 TaskPlan / DesignSpec 字段后，只改本文件即可，
其余代码一律通过 pick() / out() 读写，不出现硬编码字段名。

约定：
- 每个字段的值是一个列表，第 1 个是我们写出去时用的规范名；
- 后面的都是读取时兼容的别名（成员 3 若改成别的名字，加进列表即可）。
"""

# ---------- 任务单（我们产出） ----------
TASK = {
    "id":             ["id", "task_id"],
    "title":          ["title", "name"],
    "kind":           ["kind", "type"],
    "role":           ["role", "owner_role"],
    "requirement_ids": ["requirement_ids", "requirement_id", "req_ids"],
    "module_ids":     ["module_ids", "module_id"],
    "design_refs":    ["design_refs", "design_ref"],
    "acceptance":     ["acceptance", "acceptance_criteria"],
    "depends_on":     ["depends_on", "deps", "depends"],
    "priority":       ["priority"],
    "estimated_effort": ["estimated_effort", "estimate", "effort"],
    "stage":          ["stage", "phase"],
    "inputs":         ["inputs"],
    "outputs":        ["outputs"],
    "description":    ["description", "desc"],
}

DEPEND = {
    "task_id": ["task_id", "task", "id"],
    "reason":  ["reason", "why"],
}

PLAN = {
    "tasks":           ["tasks", "task_list"],
    "assumptions":     ["assumptions"],
    "clarifications":  ["clarifications", "open_questions", "questions"],
    # 信封字段：成员 3 的 TaskPlan schema 要求必填
    "schema_version":  ["schema_version"],
    "plan_id":         ["plan_id"],
    "revision":        ["revision"],
    "spec_ref":        ["spec_ref"],
    "template_ref":    ["template_ref"],
    "config":          ["config"],
    "generation_mode": ["generation_mode"],
}

# 澄清项：{id, question, related_ids}
CLARIFY = {
    "id":          ["id"],
    "question":    ["question"],
    "related_ids": ["related_ids"],
}

# 输入引用：{source, ref, type}
INPUT_REF = {
    "source": ["source"],
    "ref":    ["ref"],
    "type":   ["type"],
}

# 产物引用：{id, type, description}
OUTPUT_REF = {
    "id":          ["id"],
    "type":        ["type"],
    "description": ["description"],
}

ARTIFACT_TYPES = ("design_contract", "implementation", "verification_report",
                  "integration_report", "support_artifact")

# 估算：{value(正整数), unit, basis}
EFFORT = {
    "value": ["value", "days"],
    "unit":  ["unit"],
    "basis": ["basis", "reason"],
}

SCHEMA_VERSION = "1.0"

# ---------- 设计规格（我们接收） ----------
SPEC = {
    "product":      ["product", "product_name", "name"],
    "scope":        ["scope", "range"],
    "requirements": ["requirements", "reqs"],
    "modules":      ["modules"],
    "design_refs":  ["design_refs", "designs"],
    "constraints":  ["constraints"],
}

REQ = {
    "id":         ["id", "req_id"],
    "goal":       ["goal", "description", "title"],
    "acceptance": ["acceptance", "acceptance_criteria"],
}

MODULE = {
    "id":             ["id", "module_id"],
    "responsibility": ["responsibility", "duty", "description"],
    "requirement_ids": ["requirement_ids", "requirement_id", "req_ids"],
}

DESIGN_REF = {
    "id":      ["id", "ref_id"],
    "type":    ["type", "kind"],
    "summary": ["summary", "title", "description"],
}

# ---------- 任务类型 ----------
KINDS = ("implementation", "verification", "integration", "support")


def pick(obj, table, key, default=None):
    """按别名表从 dict 里取值，取不到返回 default。"""
    if not isinstance(obj, dict):
        return default
    for name in table[key]:
        if name in obj and obj[name] is not None:
            return obj[name]
    return default


def out(table, key):
    """我们写出去时用的规范名。"""
    return table[key][0]
