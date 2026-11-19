"""提示词。

要求：全文不出现任何具体产品（聊天、社区之类），否则三类规格会拆出同构任务，
“通用可定制”这条就证不出来了。示例只放在 fixtures 里，不进提示词。
"""
import json

from . import fields as F

PROMPT_VERSION = "p3"

_LIMITS = {
    "max_tasks": 80,
}


def _task_field_doc() -> str:
    """把字段清单写进提示词，字段名随 fields.py 自动变化。"""
    rows = [
        (F.out(F.TASK, "id"), "任务编号，形如 T-MSG-I，本计划内唯一"),
        (F.out(F.TASK, "title"), "任务标题，一句话说清交付什么"),
        (F.out(F.TASK, "description"), "一到两句补充说明，写清做法或边界"),
        (F.out(F.TASK, "kind"), "任务类型：" + "、".join(F.KINDS)),
        (F.out(F.TASK, "stage"), "所属阶段，取自模板的 stages"),
        (F.out(F.TASK, "role"), "承担角色，必须来自模板的允许角色"),
        (F.out(F.TASK, "requirement_ids"), "关联的需求编号数组，必须存在于规格"),
        (F.out(F.TASK, "module_ids"), "关联的模块编号数组，没有就给空数组"),
        (F.out(F.TASK, "design_refs"), "引用的设计条目编号数组，没有就给空数组"),
        (F.out(F.TASK, "inputs"),
         '输入引用数组，每项仅含 {"source": "design|task_output", "ref": "设计条目ID 或 <前置任务ID>/<产物ID>", "type": "design_contract|implementation|verification_report|integration_report|support_artifact"}；注意 source 为 design 时 type 必须固定填 "design_contract"'),
        (F.out(F.TASK, "outputs"),
         '产出数组，至少一项，每项仅含 {"id": "全局唯一产物ID", "type": "design_contract|implementation|verification_report|integration_report|support_artifact", "description": "产物说明"}；所有任务的 outputs[].id 在整个计划内不得重复'),
        (F.out(F.TASK, "acceptance"), "验收条件数组，至少一条，可被检查"),
        (F.out(F.TASK, "depends_on"),
         "前置任务数组，每项仅含 " + F.out(F.DEPEND, "task_id") + " 和 " + F.out(F.DEPEND, "reason")),
        (F.out(F.TASK, "priority"), "优先级，非负整数，越小越先"),
        (F.out(F.TASK, "estimated_effort"),
         '估算，形如 {"value": 8, "unit": "人时", "basis": "接口与鉴权"}；value 必须是正整数'),
    ]
    return "\n".join(f"- {name}：{doc}" for name, doc in rows)


SYSTEM = f"""你是软件工程中的任务规划者（PM）。

你的唯一工作：阅读用户给出的设计规格，把它翻译成一份可执行的开发任务计划。
你没有关于任何具体产品的预设知识，也不得套用任何固定的任务清单——任务必须由这份规格推导出来。

输入包含两部分：
1. 设计规格：产品范围、需求条目（含验收条件）、模块、设计条目、约束。
2. 工作流模板：阶段划分、允许角色、是否要求验证。

输出必须是一个顶层 JSON 对象，顶层仅包含以下四个键：
1. "tasks"：任务数组。数组中的每个任务对象【只能】包含以下 15 个字段（禁止在单个任务内放 assumptions、clarifications 或其他多余字段）：
{_task_field_doc()}
2. "assumptions"：字符串数组（顶层字段）。你为了让计划成立而做的假设，例如“默认复用现有登录态”。
3. "clarifications"：对象数组（顶层字段），每项 {{"id": "Q1", "question": "…", "related_ids": ["R-MSG-01"]}}。
   规格里没写清、又会影响拆分的问题放这里，不要自己编答案。
4. "workstreams"：项目级工作包数组，每项为 {{"id":"WP1","title":"贴合项目的交付目标","summary":"一句话说明这个包的产出","task_ids":["本计划中的小任务 ID"]}}。通常 3–6 个，最多 8 个；有四个或更多小任务时至少 2 个。工作包按可识别的业务能力、模块交付或联调目标组织，不要机械套用通用流程阶段名。每个小任务在工作包中恰好出现一次。跨包依赖只写在小任务的 depends_on 中，由编排器汇总。

硬性规则：
1. 每条需求至少有一个实现类任务（kind=implementation）和一个验证类任务（kind=verification）。
2. 每个任务至少一条可检查的验收条件（不少于 4 个字），不能写“完成”“正常”这类无法判断的话。
3. 依赖必须写原因，且只能指向本计划里存在的任务编号，不得成环。
4. 只能引用规格里真实存在的需求编号、模块编号、设计条目编号。不确定的内容写进顶层 clarifications，不要编造。
5. 一个任务对应一个可独立验收的交付物。不要按函数拆任务，也不要写“完成后端”这种无法验收的标题。
6. 若 inputs 消费了上游任务的产物（source="task_output", ref="任务ID/产物ID"），必须满足三点：(a) 该任务ID与产物ID真实存在；(b) inputs[].type 与上游 outputs[].type 一致；(c) 【即使已有间接依赖，也必须在当前任务的 depends_on 中显式列出该上游任务ID】。
7. 若 inputs 引用设计条目（source="design"），ref 必须是规格 design_items 中存在的 ID，且 type 必须为 "design_contract"。
8. 不要输出任何 JSON 之外的内容，不要用 markdown 代码块包裹。
9. 把不存在前置关系的工作放在独立分支，不要为表现阶段顺序把所有任务串成单链。根据规格标出实际存在的跨角色交接：例如验证任务消费实现产物、集成任务汇合两个独立模块的产出；用 depends_on 写明依赖原因。仅当确实需要上游产物时才连线，不能添加假依赖或为了画图虚构任务。

只输出 JSON。"""


def build_messages(spec: dict, template: dict, config: dict) -> list[dict]:
    payload = {
        "design_spec": spec,
        "workflow_template": template,
        "config": config,
    }
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
    ]


def build_repair_messages(
    spec: dict, template: dict, config: dict, previous_output: str, issues: list
) -> list[dict]:
    """带错误明细再问一次。要求最小改动，避免整篇重写引入新问题。"""
    lines = []
    for i in issues:
        where = i.field_path or i.object_id or "-"
        lines.append(f"- [{i.code}] {where}: {i.message}")
    feedback = (
        "你上一次的输出存在以下问题：\n"
        + "\n".join(lines)
        + "\n\n请只修正这些问题，保持其余内容和结构不变，重新输出完整 JSON。"
    )
    messages = build_messages(spec, template, config)
    messages.append({"role": "assistant", "content": previous_output})
    messages.append({"role": "user", "content": feedback})
    return messages
