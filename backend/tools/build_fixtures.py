"""生成 examples/contracts 下的派生 fixture。

成员 3 的 fixture 分两类：

1. **人工编写的基线**（直接提交、逐字段可评审）：
   - ``templates/default-workflow.json``
   - ``examples/contracts/chat/design-spec.json`` / ``task-plan.json``
   - ``examples/contracts/community/design-spec.json`` / ``task-plan.json``
2. **本脚本派生的产物**（数字摘要来自契约函数，避免手抄出错）：
   - ``examples/contracts/chat/execution-plan.json``：8 节点 / 11 条边 / 6 批
   - ``examples/contracts/{chat,community}/validation-report.json``：合法样例的校验报告
   - ``examples/contracts/invalid/<场景>/task-plan.json`` 与 ``validation-report.json``：
     错误样例（带环、缺验证任务、产物引用不存在）

执行计划中的批次/绑定是 ``curated_example`` 的预编排结果，只用于联调与导出样例；
正式编排算法由成员 4 实现。脚本会重新校验所有派生结果，断言基线样例无错误、错误
样例命中预期错误码，防止 fixture 悄悄失效。

用法::

    python -m backend.tools.build_fixtures --write   # 重新生成派生 fixture
    python -m backend.tools.build_fixtures --check   # 仅检查磁盘文件是否最新（CI）
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from ..app.contracts.models import (
    SCHEMA_VERSION,
    content_digest,
    to_jsonable,
)
from ..app.validation import codes as C
from ..app.validation.validator import (
    validate_design_spec,
    validate_execution_plan,
    validate_task_plan,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_DIR = REPO_ROOT / "examples" / "contracts"
TEMPLATE_PATH = REPO_ROOT / "templates" / "default-workflow.json"

CHAT_DIR = CONTRACTS_DIR / "chat"
COMMUNITY_DIR = CONTRACTS_DIR / "community"
INVALID_DIR = CONTRACTS_DIR / "invalid"

# 聊天案例的预编排批次（与 docs/第十二组_Lab2设计文档 §1.4 的 8 节点/11 边/6 批一致）。
CHAT_BATCHES: list[list[str]] = [
    ["C1"],
    ["C2", "C3"],
    ["C4", "C6"],
    ["C5"],
    ["C7"],
    ["C8"],
]

# 每个错误样例的期望错误码，作为生成侧自检（测试侧还会再次断言字段路径）。
INVALID_CASES: dict[str, dict[str, Any]] = {
    "cycle": {
        "expected_codes": [C.DEPENDENCY_CYCLE],
        "note": "在 C5 上追加对 C7 的依赖，形成 C5 → C7 → C5 的闭合环。",
    },
    "missing-verification": {
        "expected_codes": [C.COVERAGE_VERIFICATION_MISSING],
        "note": "删除 R2 的验证任务 C7，覆盖检查应定位到 R2 并列出受影响的 C8。",
    },
    "unresolved-artifact": {
        "expected_codes": [C.ARTIFACT_OUTPUT_UNKNOWN],
        "note": "把 C6 的输入从 C2/O-MSG-SERVICE 改成不存在的 C2/O-API。",
    },
}


# ---------------------------------------------------------------------------
# 读写与序列化


def _dump(document: Any) -> str:
    """统一 fixture 落盘格式：键排序、UTF-8、LF、两空格缩进、末尾换行。"""
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _load(path: Path) -> Any:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return json.load(handle)


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


# ---------------------------------------------------------------------------
# 执行计划构造


def _dependency_pairs(plan: dict[str, Any]) -> list[tuple[str, str]]:
    """返回去重后的 (前置任务, 当前任务) 边，顺序稳定。"""
    edges: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for task in plan["tasks"]:
        for dependency in task["depends_on"]:
            pair = (str(dependency["task_id"]), str(task["id"]))
            if pair not in seen:
                seen.add(pair)
                edges.append(pair)
    return edges


def _topological_levels(plan: dict[str, Any]) -> list[list[str]]:
    """按最长路径分层：任务层号 = 1 + 所有前置任务层号的最大值。"""
    tasks = plan["tasks"]
    task_ids = [str(task["id"]) for task in tasks]
    depends: dict[str, list[str]] = {
        str(task["id"]): [str(dep["task_id"]) for dep in task["depends_on"]] for task in tasks
    }
    level: dict[str, int] = {}
    remaining = list(task_ids)
    while remaining:
        progressed = False
        for task_id in list(remaining):
            prerequisites = depends[task_id]
            if all(prerequisite in level for prerequisite in prerequisites):
                level[task_id] = 1 + max((level[p] for p in prerequisites), default=-1)
                remaining.remove(task_id)
                progressed = True
        if not progressed:  # 依赖环无法分层；环样例由校验器报告
            break
    grouped: dict[int, list[str]] = {}
    for task_id in task_ids:
        grouped.setdefault(level.get(task_id, 0), []).append(task_id)
    return [grouped[index] for index in sorted(grouped)]


def _bindings(plan: dict[str, Any], batches: list[list[str]]) -> list[dict[str, Any]]:
    """按批次顺序为任务分配全局槽位与角色槽位。"""
    roles = {str(task["id"]): str(task["role"]) for task in plan["tasks"]}
    bindings: list[dict[str, Any]] = []
    for batch in batches:
        used: dict[str, int] = {}
        for position, task_id in enumerate(batch, start=1):
            role = roles[task_id]
            used[role] = used.get(role, 0) + 1
            bindings.append(
                {"task_id": task_id, "global_slot": position, "role_slot": used[role]}
            )
    return bindings


def _build_execution_plan(
    plan: dict[str, Any],
    spec: dict[str, Any],
    template: dict[str, Any],
    report: dict[str, Any],
) -> dict[str, Any]:
    roles = {str(task["id"]): (str(task["role"]), str(task["stage"]), int(task["priority"])) for task in plan["tasks"]}
    batches = CHAT_BATCHES
    scheduled = [task_id for batch in batches for task_id in batch]
    if sorted(scheduled) != sorted(roles):
        raise BuildError(f"批次调度与任务集合不一致：{scheduled}")
    return {
        "schema_version": SCHEMA_VERSION,
        "execution_plan_id": "EXEC-CHAT",
        "plan_ref": {"plan_id": plan["plan_id"], "revision": plan["revision"]},
        "spec_digest": content_digest(spec),
        "template_digest": content_digest(template),
        "config_digest": content_digest(plan["config"]),
        "nodes": [
            {
                "task_id": task_id,
                "role": role,
                "stage": stage,
                "priority": priority,
            }
            for task_id, (role, stage, priority) in roles.items()
        ],
        "edges": [
            {"from_task": from_task, "to_task": to_task}
            for from_task, to_task in _dependency_pairs(plan)
        ],
        "topological_levels": _topological_levels(plan),
        "batches": batches,
        "bindings": _bindings(plan, batches),
        "validation_ref": {
            "plan_id": plan["plan_id"],
            "revision": plan["revision"],
            "report_digest": content_digest(report),
        },
    }


class BuildError(RuntimeError):
    """fixture 生成前置条件不满足。"""


# ---------------------------------------------------------------------------
# 错误样例


def _mutate(plan: dict[str, Any], case: str) -> dict[str, Any]:
    mutated = copy.deepcopy(plan)
    tasks = {str(task["id"]): task for task in mutated["tasks"]}
    if case == "cycle":
        tasks["C5"]["depends_on"].append(
            {"task_id": "C7", "reason": "错误样例：让历史列表依赖分页验证，形成闭合环"}
        )
    elif case == "missing-verification":
        mutated["tasks"] = [task for task in mutated["tasks"] if task["id"] != "C7"]
    elif case == "unresolved-artifact":
        tasks["C6"]["inputs"][1]["ref"] = "C2/O-API"
    else:  # pragma: no cover - 防御式分支
        raise BuildError(f"未知错误样例：{case}")
    return mutated


# ---------------------------------------------------------------------------
# 构建全部产出


def build_documents() -> dict[str, Any]:
    """计算所有派生 fixture，返回 {输出文件: 文档对象}。"""
    template = _load(TEMPLATE_PATH)
    chat_spec = _load(CHAT_DIR / "design-spec.json")
    chat_plan = _load(CHAT_DIR / "task-plan.json")
    community_spec = _load(COMMUNITY_DIR / "design-spec.json")
    community_plan = _load(COMMUNITY_DIR / "task-plan.json")

    outputs: dict[str, Any] = {}

    # 合法样例的校验报告（成员 1/4/5 联调用的“预期结果”）。
    chat_report = to_jsonable(validate_task_plan(chat_plan, spec=chat_spec, template=template))
    community_report = to_jsonable(
        validate_task_plan(community_plan, spec=community_spec, template=template)
    )
    for name, report in (
        ("chat", chat_report),
        ("community", community_report),
    ):
        spec = chat_spec if name == "chat" else community_spec
        spec_report = to_jsonable(validate_design_spec(spec))
        if spec_report["blocked"]:
            raise BuildError(f"{name} 设计规格校验未通过：{spec_report['issues']}")
        if report["blocked"]:
            raise BuildError(f"{name} 任务计划校验未通过：{report['issues']}")
        outputs[_rel(CONTRACTS_DIR / name / "validation-report.json")] = report

    # 执行计划：引用合法任务计划与它的校验报告摘要。
    execution_plan = _build_execution_plan(chat_plan, chat_spec, template, chat_report)
    execution_report = to_jsonable(
        validate_execution_plan(
            execution_plan,
            task_plan=chat_plan,
            validation_report=chat_report,
            spec=chat_spec,
            template=template,
        )
    )
    if execution_report["blocked"]:
        raise BuildError(f"聊天执行计划校验未通过：{execution_report['issues']}")
    outputs[_rel(CHAT_DIR / "execution-plan.json")] = execution_plan

    # 错误样例：完整任务计划 + 对应的校验报告。
    for case, meta in INVALID_CASES.items():
        mutated_plan = _mutate(chat_plan, case)
        report = to_jsonable(
            validate_task_plan(mutated_plan, spec=chat_spec, template=template)
        )
        found = {issue["code"] for issue in report["issues"]}
        missing = [code for code in meta["expected_codes"] if code not in found]
        if missing:
            raise BuildError(f"错误样例 {case} 未命中预期错误码 {missing}；实际为 {sorted(found)}")
        outputs[_rel(INVALID_DIR / case / "task-plan.json")] = mutated_plan
        outputs[_rel(INVALID_DIR / case / "validation-report.json")] = report

    return outputs


def write_all(outputs: dict[str, Any]) -> list[str]:
    written: list[str] = []
    for relative, document in sorted(outputs.items()):
        path = REPO_ROOT / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        text = _dump(document)
        path.write_text(text, encoding="utf-8", newline="")
        written.append(relative)
    return written


def check_all(outputs: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    for relative, document in sorted(outputs.items()):
        path = REPO_ROOT / relative
        expected = _dump(document)
        if not path.exists():
            problems.append(f"缺少派生 fixture：{relative}")
        elif path.read_text(encoding="utf-8") != expected:
            problems.append(f"派生 fixture 已过期：{relative}（运行 --write 重新生成）")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成成员 3 的契约与校验 fixture")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true", help="写入派生 fixture")
    group.add_argument("--check", action="store_true", help="只检查派生 fixture 是否最新")
    arguments = parser.parse_args(argv)

    try:
        outputs = build_documents()
    except BuildError as error:
        print(f"fixture 生成失败：{error}")
        return 1
    if arguments.write:
        for relative in write_all(outputs):
            print(f"已写入 {relative}")
        return 0
    problems = check_all(outputs)
    if problems:
        for problem in problems:
            print(problem)
        return 1
    print(f"派生 fixture 均为最新（{len(outputs)} 个文件）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
