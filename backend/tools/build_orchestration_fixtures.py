"""生成成员 4（确定性编排）的联调 fixture。

交付给成员 5、6 的固定输入 ↔ 固定输出，便于对齐图、批次与等待原因：

- ``examples/orchestration/chat/explanation.json``：聊天案例（容量 2、qa=1）的等待原因；
- ``examples/orchestration/community/execution-plan.json`` / ``explanation.json``：
  内容社区案例按计划自带配置（qa=2）的结果；
- ``examples/orchestration/community-qa1/*``：把 qa 容量从 2 降到 1 的对照样例（V08），
  含修改后的任务计划、重新校验的报告、执行计划与等待原因，用于核对
  “降低 qa 容量只改变资源安排，依赖边保持一致”。

聊天案例的执行计划本身是成员 3 的 ``examples/contracts/chat/execution-plan.json``，
本模块只补充等待原因，并断言算法逐字段复现该样例，避免两套口径。

用法::

    python -m backend.tools.build_orchestration_fixtures --write   # 重新生成
    python -m backend.tools.build_orchestration_fixtures --check   # 仅检查是否最新（CI/测试）
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ..app.contracts.models import to_jsonable
from ..app.orchestration import compile_plan
from ..app.validation.validator import validate_execution_plan, validate_task_plan

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_DIR = REPO_ROOT / "examples" / "contracts"
OUTPUT_DIR = REPO_ROOT / "examples" / "orchestration"
TEMPLATE_PATH = REPO_ROOT / "templates" / "default-workflow.json"


def _load(path: Path) -> Any:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return json.load(handle)


def _dump(document: Any) -> str:
    """与成员 3 的 fixture 一致：键排序、两空格缩进、UTF-8、末尾换行。"""
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _compile(plan: dict[str, Any], spec: dict[str, Any], template: dict[str, Any], report: dict[str, Any], execution_plan_id: str):
    result = compile_plan(
        plan,
        spec=spec,
        template=template,
        validation_report=report,
        execution_plan_id=execution_plan_id,
        verify=True,
    )
    checked = validate_execution_plan(
        result.document(), task_plan=plan, validation_report=report, spec=spec, template=template
    )
    if checked.blocked:
        raise BuildError(f"执行计划校验未通过：{to_jsonable(checked.issues)}")
    return result


class BuildError(RuntimeError):
    """fixture 生成前置条件不满足。"""


def build_documents() -> dict[str, Any]:
    """计算全部 fixture，返回 {仓库内相对路径: 文档对象}。"""
    template = _load(TEMPLATE_PATH)
    chat_spec = _load(CONTRACTS_DIR / "chat" / "design-spec.json")
    chat_plan = _load(CONTRACTS_DIR / "chat" / "task-plan.json")
    chat_report = _load(CONTRACTS_DIR / "chat" / "validation-report.json")
    community_spec = _load(CONTRACTS_DIR / "community" / "design-spec.json")
    community_plan = _load(CONTRACTS_DIR / "community" / "task-plan.json")
    community_report = _load(CONTRACTS_DIR / "community" / "validation-report.json")

    outputs: dict[str, Any] = {}

    # 1. 聊天案例：执行计划必须与成员 3 的样例逐字段一致，这里只补等待原因。
    chat = _compile(chat_plan, chat_spec, template, chat_report, "EXEC-CHAT")
    curated = _load(CONTRACTS_DIR / "chat" / "execution-plan.json")
    if chat.document() != curated:
        raise BuildError("聊天执行计划与 examples/contracts/chat/execution-plan.json 不一致")
    outputs["examples/orchestration/chat/explanation.json"] = chat.explanation_document()

    # 2. 内容社区：使用计划自带配置（qa=2）。
    community = _compile(community_plan, community_spec, template, community_report, "EXEC-COMMUNITY")
    outputs["examples/orchestration/community/execution-plan.json"] = community.document()
    outputs["examples/orchestration/community/explanation.json"] = community.explanation_document()

    # 3. 对照样例：只把 qa 容量从 2 降到 1，重新校验后再编排（V08）。
    lowered = json.loads(json.dumps(community_plan))
    lowered["config"]["role_capacity"]["qa"] = 1
    lowered_report = to_jsonable(
        validate_task_plan(lowered, spec=community_spec, template=template)
    )
    if lowered_report["blocked"]:
        raise BuildError(f"qa=1 对照计划的校验未通过：{lowered_report['issues']}")
    lowered_result = _compile(lowered, community_spec, template, lowered_report, "EXEC-COMMUNITY-QA1")
    base_edges = {(edge.from_task, edge.to_task) for edge in community.execution_plan.edges}
    lowered_edges = {(edge.from_task, edge.to_task) for edge in lowered_result.execution_plan.edges}
    if base_edges != lowered_edges:
        raise BuildError("容量变化不应改变依赖边，但两次编排的边不同")
    outputs["examples/orchestration/community-qa1/task-plan.json"] = lowered
    outputs["examples/orchestration/community-qa1/validation-report.json"] = lowered_report
    outputs["examples/orchestration/community-qa1/execution-plan.json"] = lowered_result.document()
    outputs["examples/orchestration/community-qa1/explanation.json"] = (
        lowered_result.explanation_document()
    )
    return outputs


def write_all(outputs: dict[str, Any]) -> list[str]:
    written: list[str] = []
    for relative, document in sorted(outputs.items()):
        path = REPO_ROOT / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_dump(document), encoding="utf-8", newline="")
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
    parser = argparse.ArgumentParser(description="生成成员 4 的编排 fixture")
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
    print(f"编排 fixture 均为最新（{len(outputs)} 个文件）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
