"""规则：生成前的规格预检（缺口）+ 生成后的计划校验（问题）。

分工原则：
- 结构问题由这里判，能定位到字段，可自动触发一次修复；
- 语义问题（任务贴不贴合业务、验收可不可操作）规则判不了，进人工检查清单，
  不放进修复循环，否则模型会自我合理化。
"""
from . import fields as F
from .models import Gap, PlanIssue

# 语义质量只能人工看，列出来供检查表直接使用
SEMANTIC_CHECKLIST = [
    "任务是否贴合该规格的业务，而不是通用模板",
    "异常路径（失败、越权、空结果）是否被覆盖",
    "依赖理由是否成立，是否存在为凑顺序而建的依赖",
    "验收条件是否可操作，能否直接转成测试用例",
    "角色分配是否与实际能力相符",
]


def _list(v):
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v]


def _num(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- 预检
def precheck(spec: dict) -> list[Gap]:
    """规格本身是否够用。返回非空即 needs_clarification，不调用模型。"""
    gaps: list[Gap] = []

    if not str(F.pick(spec, F.SPEC, "product", "") or "").strip():
        gaps.append(Gap("G001", "缺少产品范围或产品名称", "product"))

    reqs = _list(F.pick(spec, F.SPEC, "requirements"))
    if not reqs:
        gaps.append(Gap("G002", "没有任何需求条目", "requirements"))

    seen = set()
    for i, req in enumerate(reqs):
        rid = str(F.pick(req, F.REQ, "id", "") or "").strip()
        path = f"requirements[{i}]"
        if not rid:
            gaps.append(Gap("G003", "需求缺少编号", path))
        elif rid in seen:
            gaps.append(Gap("G003", f"需求编号重复：{rid}", path))
        else:
            seen.add(rid)
        if not str(F.pick(req, F.REQ, "goal", "") or "").strip():
            gaps.append(Gap("G004", f"需求 {rid or path} 缺少行为描述", path))
        if not _list(F.pick(req, F.REQ, "acceptance")):
            gaps.append(Gap("G005", f"需求 {rid or path} 缺少验收条件", path))

    modules = _list(F.pick(spec, F.SPEC, "modules"))
    if not modules:
        gaps.append(Gap("G006", "缺少模块划分，无法判断任务归属", "modules"))
    for i, mod in enumerate(modules):
        mid = str(F.pick(mod, F.MODULE, "id", "") or "").strip()
        path = f"modules[{i}]"
        if not mid:
            gaps.append(Gap("G007", "模块缺少编号", path))
        if not str(F.pick(mod, F.MODULE, "responsibility", "") or "").strip():
            gaps.append(Gap("G007", f"模块 {mid or path} 缺少职责说明", path))
        for rid in _list(F.pick(mod, F.MODULE, "requirement_ids")):
            if seen and rid not in seen:
                gaps.append(Gap("G008", f"模块 {mid or path} 引用了不存在的需求 {rid}", path))

    return gaps


# ---------------------------------------------------------------- 校验
def validate(plan, spec: dict, template: dict | None = None) -> list[PlanIssue]:
    """检查模型产出。error 会触发一次修复；warning 只记录。"""
    template = template or {}
    issues: list[PlanIssue] = []

    if not isinstance(plan, dict):
        return [PlanIssue("E001", "error", "输出不是 JSON 对象")]

    tasks = F.pick(plan, F.PLAN, "tasks")
    if not isinstance(tasks, list) or not tasks:
        return [PlanIssue("E002", "error", "缺少 tasks 数组或为空", "tasks")]

    req_ids = {
        str(F.pick(r, F.REQ, "id")) for r in _list(F.pick(spec, F.SPEC, "requirements"))
        if F.pick(r, F.REQ, "id")
    }
    mod_ids = {
        str(F.pick(m, F.MODULE, "id")) for m in _list(F.pick(spec, F.SPEC, "modules"))
        if F.pick(m, F.MODULE, "id")
    }
    ref_ids = {
        str(F.pick(d, F.DESIGN_REF, "id")) for d in _list(F.pick(spec, F.SPEC, "design_refs"))
        if F.pick(d, F.DESIGN_REF, "id")
    }
    allowed_roles = _list(template.get("allowed_roles"))

    ids: list[str] = []
    deps: dict[str, list[str]] = {}

    for i, t in enumerate(tasks):
        if not isinstance(t, dict):
            issues.append(PlanIssue("E003", "error", "任务不是对象", f"tasks[{i}]"))
            continue
        tid = str(F.pick(t, F.TASK, "id", "") or "").strip()
        path = f"tasks[{i}]"
        if not tid:
            issues.append(PlanIssue("E010", "error", "任务缺少编号", path))
            continue
        if tid in ids:
            issues.append(PlanIssue("E011", "error", f"任务编号重复：{tid}", tid))
        ids.append(tid)

        if not str(F.pick(t, F.TASK, "title", "") or "").strip():
            issues.append(PlanIssue("E012", "error", "任务缺少标题", tid))

        kind = str(F.pick(t, F.TASK, "kind", "") or "")
        if kind not in F.KINDS:
            issues.append(PlanIssue("E013", "error", f"任务类型不合法：{kind or '空'}", tid))

        role = str(F.pick(t, F.TASK, "role", "") or "")
        if not role:
            issues.append(PlanIssue("E014", "error", "任务缺少承担角色", tid))
        elif allowed_roles and role not in allowed_roles:
            issues.append(PlanIssue("W001", "warning", f"角色 {role} 不在模板允许列表中", tid))

        acc = _list(F.pick(t, F.TASK, "acceptance"))
        if not acc:
            issues.append(PlanIssue("E015", "error", "任务缺少验收条件", tid))
        elif any(len(str(a).strip()) < 4 for a in acc):
            issues.append(PlanIssue("E015", "error", "存在过短的验收条件（少于 4 字）", tid))

        reqs = _list(F.pick(t, F.TASK, "requirement_ids"))
        if not reqs:
            issues.append(PlanIssue("E016", "error", "任务未关联需求", tid))
        for rid in reqs:
            if str(rid) not in req_ids:
                issues.append(PlanIssue("E017", "error", f"引用了不存在的需求 {rid}", tid))

        for mid in _list(F.pick(t, F.TASK, "module_ids")):
            if str(mid) not in mod_ids:
                issues.append(PlanIssue("E018", "error", f"引用了不存在的模块 {mid}", tid))
        for ref in _list(F.pick(t, F.TASK, "design_refs")):
            if str(ref) not in ref_ids:
                issues.append(PlanIssue("E019", "error", f"引用了不存在的设计条目 {ref}", tid))

        dep_ids = []
        for d in _list(F.pick(t, F.TASK, "depends_on")):
            dep_id = str(F.pick(d, F.DEPEND, "task_id", "") or "")
            if not dep_id:
                issues.append(PlanIssue("E020", "error", "依赖项缺少前置任务编号", tid))
                continue
            if not str(F.pick(d, F.DEPEND, "reason", "") or "").strip():
                issues.append(PlanIssue("E020", "error", f"依赖 {dep_id} 缺少原因", tid))
            if dep_id == tid:
                issues.append(PlanIssue("E022", "error", "任务依赖了自己", tid))
            dep_ids.append(dep_id)
        deps[tid] = dep_ids

        if _num(F.pick(t, F.TASK, "priority"), 0) < 0:
            issues.append(PlanIssue("W002", "warning", "优先级为负数", tid))

        issues.extend(_check_effort(t, tid))

    for tid, dep_ids in deps.items():
        for dep_id in dep_ids:
            if dep_id and dep_id not in ids:
                issues.append(PlanIssue("E021", "error", f"依赖了不存在的任务 {dep_id}", tid))

    cycle = _find_cycle(deps)
    if cycle:
        issues.append(PlanIssue("E030", "error", "任务依赖成环：" + " → ".join(cycle), "tasks"))

    if bool(template.get("require_verification", True)):
        impl = {r for t in tasks if isinstance(t, dict)
                and F.pick(t, F.TASK, "kind") == "implementation"
                for r in _list(F.pick(t, F.TASK, "requirement_ids"))}
        ver = {r for t in tasks if isinstance(t, dict)
               and F.pick(t, F.TASK, "kind") == "verification"
               for r in _list(F.pick(t, F.TASK, "requirement_ids"))}
        for rid in sorted(req_ids):
            if rid not in impl:
                issues.append(PlanIssue("E031", "error", f"需求 {rid} 没有实现类任务", rid))
            if rid not in ver:
                issues.append(PlanIssue("E032", "error", f"需求 {rid} 没有验证类任务", rid))

    if not isinstance(F.pick(plan, F.PLAN, "clarifications", []), list):
        issues.append(PlanIssue("W003", "warning", "clarifications 应为数组"))

    return issues


def _find_cycle(deps: dict[str, list[str]]) -> list[str] | None:
    """返回一条真实闭合路径，用于报错定位；不擅自删边。"""
    WHITE, GREY, BLACK = 0, 1, 2
    color = {k: WHITE for k in deps}
    stack: list[str] = []

    def dfs(u: str):
        color[u] = GREY
        stack.append(u)
        for v in deps.get(u, []):
            if v not in color:
                continue
            if color[v] == GREY:
                return stack[stack.index(v):] + [v]
            if color[v] == WHITE:
                found = dfs(v)
                if found:
                    return found
        stack.pop()
        color[u] = BLACK
        return None

    for node in list(deps):
        if color[node] == WHITE:
            found = dfs(node)
            if found:
                return found
    return None


def _check_effort(task: dict, tid: str) -> list[PlanIssue]:
    """估算字段：{value, unit, basis}。只报警告，不阻断——估算质量靠人工看。"""
    effort = F.pick(task, F.TASK, "estimated_effort")
    if effort is None:
        return [PlanIssue("W004", "warning", "缺少工作量估算", tid)]
    if not isinstance(effort, dict):
        return [PlanIssue("W004", "warning", "估算应为 {value, unit, basis} 对象", tid)]

    out = []
    value = F.pick(effort, F.EFFORT, "value")
    unit = str(F.pick(effort, F.EFFORT, "unit", "") or "")
    basis = str(F.pick(effort, F.EFFORT, "basis", "") or "").strip()

    # 契约要求 value 为正整数（见 task_plan.schema.json 的 Estimate）
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        out.append(PlanIssue("W004", "warning", "估算值应为正整数", tid))
    if not unit:
        out.append(PlanIssue("W004", "warning", "估算缺少单位", tid))
    if not basis:
        out.append(PlanIssue("W004", "warning", "估算缺少依据", tid))
    return out


def errors(issues: list[PlanIssue]) -> list[PlanIssue]:
    return [i for i in issues if i.severity == "error"]
