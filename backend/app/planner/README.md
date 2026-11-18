# PM 规划模块 · 接口与协作说明

**面向**：第十二组全体
**主责**：成员 2（PM 规划与模型适配）
**当前状态**：可运行；任务字段名等成员 3 定稿，只改 `fields.py` 一处

这份文档写给全组，不是写给某一个人的。任何人接手这个模块，按本文就能跑起来、改得动、交得出去。

---

## 1 它在整条链路里的位置

```
设计规格（成员 1 录入 / 成员 3 定格式）
        │
        ▼
   本模块：PM 生成任务计划          ← 成员 2
        │
        ▼
   规则校验（成员 3）── 不过 → 回到本模块修一次
        │
        ▼
   确定性编排（成员 4）→ 批次与角色槽
        │
        ▼
   依赖图与看板（成员 6 / 成员 1）
        │
        ▼
   版本保存与导出（成员 5）
```

本模块只做一件事：**把规格翻译成任务**。不排班、不画图、不存库。所有上下游都是通过一份 JSON 交接，没有别的耦合。

---

## 2 对外只有一个函数

```python
from planner import generate, JOB_STATUS

result = generate(spec, template, config, provider)

result.status    # ok / needs_clarification / invalid_output / failed
result.plan      # 仅 ok 时有值，就是任务计划
result.gaps      # needs_clarification 时：规格缺什么
result.issues    # 校验问题，severity 为 error / warning
result.trace     # mode / attempts / latency_ms / trace_id
```

四个状态的含义，全组统一按这个理解，不要各自解释：

| 状态 | 含义 | 下一步 |
|---|---|---|
| `ok` | 拿到合格计划 | 交给校验与编排 |
| `needs_clarification` | 规格本身缺信息，**没有调模型** | 回到成员 1 的界面，让用户补 |
| `invalid_output` | 模型输出两次都不合格 | 保留原文与问题清单，交人工 |
| `failed` | 模型调用本身失败 | 标记失败，可重试或切离线模式 |

### 2.1 接主链：`adapter.py`

`backend/workflow.py` 已经留好插槽：`workflow_router(store, planner)` 里的
`planner` 是一个普通函数 `planner(spec) -> list[dict]`，只在 `mode == "live_llm"` 时被调用，
为 `None` 时接口返回 503「实时 PM 尚未配置」。

所以接入只需要一行：

```python
from app.planner.adapter import make_planner
planner = make_planner()          # 内部读 PM_BASE_URL / PM_API_KEY / PM_MODEL
# 没有模型时也能演示：make_planner(provider=OfflineProvider())
```

适配器负责把 `GenerationResult` 的四种状态翻成接口层认识的东西：

| 结果 | adapter 的行为 | 接口层表现 |
|---|---|---|
| `ok` | 返回任务列表 | 202，作业成功 |
| `needs_clarification` / `invalid_output` | 抛 `ValueError`（带缺口或错误明细） | 422，前端显示具体问题 |
| `failed` | 抛 `ValueError` | 422 / 503 |

> 注意：`workflow.py` 目前保存的是简化版任务（`id/title/role/depends_on/acceptance/output/source`），
> 与成员 3 的 15 字段 TaskPlan 还不完全一致。适配器直接返回 15 字段版本，多出的字段不会破坏读取，
> 但两边最终用哪套要组里定一次。

---

## 3 各成员从这里拿什么

| 成员 | 从这里拿 | 什么时候需要 | 出问题找谁 |
|---|---|---|---|
| **成员 3** | 一份合格输出 `good_plan.json`、一份不合格输出 `bad_plan.json`；字段清单 | 写校验器之前就要 | 成员 2 |
| **成员 4** | 任务里的 `depends_on`、`priority`、`role` 三个字段 | 写编排算法之前 | 成员 2（字段）+ 成员 3（含义） |
| **成员 5** | `JOB_STATUS` 四个状态；`result.trace` 要落库的字段 | 做作业接口之前 | 成员 2 |
| **成员 1** | `--mode offline` 跑出来的一份真实计划样例 | 做任务列表与详情界面之前 | 成员 2 |
| **成员 6** | 同一份计划样例 + `depends_on` 的边方向 | 画依赖图之前 | 成员 4（边语义） |

本模块不需要等任何人就能跑通，所以上面五个人都可以现在就来拿。

---

## 4 怎么跑

```bash
cd backend
python -m unittest discover -s tests -v        # 13 个用例，全绿才算没改坏

cd app
# 假规则：不调模型，用来联调
python -m planner.cli --spec ../tests/fixtures/spec_chat.json --mode offline

# 回放：固定返回一段坏输出，验证"只修一次"
python -m planner.cli --spec ../tests/fixtures/spec_chat.json --mode replay \
    --fixture ../tests/fixtures/bad_plan.json

# 真模型：缺环境变量会明确报错，不会假装成功
python -m planner.cli --spec ../tests/fixtures/spec_chat.json --mode live
```

真模型需要三个服务端环境变量，**不进代码、不进仓库**：

```
PM_BASE_URL=https://api.example.com/v1
PM_API_KEY=***
PM_MODEL=***
```

---

## 5 字段名从哪来（协作约定）

**唯一权威**：成员 3 的 `backend/app/contracts/schema/task_plan.schema.json`（PR #9 已合并进 main）。
成员 1 的 `docs/frontend-api-contract.md` 是前端消费视角的说明，两者冲突时以 schema 为准。

### 5.1 已按 schema 修正的差异（2026-11-16）

| 项 | 之前 | 现在（按 schema） |
|---|---|---|
| 任务必填字段 | 9 个 | **15 个**：新增 `description`、`design_refs`、`inputs`、`outputs`、`estimated_effort` |
| 估算 | `{value: 1.5, unit: "day"}` | `{value: 8, unit: "人时", basis: "…"}`，**value 必须是正整数** |
| 未决问题 | `open_questions: ["…"]` | `clarifications: [{id, question, related_ids}]`，另有 `assumptions: ["…"]` |
| 计划外层 | 只有 tasks | 还要 `schema_version / plan_id / revision / spec_ref / template_ref / config / generation_mode` |
| 任务对象 | 无约束 | `additionalProperties: false`，多余的键会被判不合法 |

> 成员 1 的接口文档写的是 `{value: 1.5, unit: "day"}`，schema 要求整数——这一处按 schema 处理，用 `unit: "人时"` 表达半天。

### 5.2 以后再改，只动一个文件

**只改 [`fields.py`](fields.py)，其他文件一行不动。**

```python
TASK = {
    "id": ["id", "task_id"],      # 第 1 项是写出去用的；后面是读取时兼容的别名
    "kind": ["kind", "type"],
    ...
}
```

三条约定：

1. 成员 3 定名后，成员 2 加别名，**不删旧名**，这样已经存下的 JSON 仍能读。
2. 改完必须跑测试；全绿才提 PR。
3. 字段增删要在 PR 说明里写清影响谁（3 的校验器、4 的编排、6 的图、5 的库）。

---

## 6 改动要通知谁

| 改什么 | 必须通知 | 为什么 |
|---|---|---|
| 四个状态枚举 | 成员 5 | 作业状态映射会变 |
| 任务字段增删 | 成员 3、4、6 | 校验、编排、图都读这些字段 |
| 提示词版本 | 全组 | 输出质量会变，需要重跑三类规格 |
| 生成模式（live/offline/replay） | 成员 1、6 | 界面和导出必须标明，不能伪装实时 |

---

## 7 与验收编号的对应

| 编号 | 怎么验 | 在本模块里的位置 |
|---|---|---|
| V03 | 坏输出只修一次，仍坏就报错 | `--mode replay --fixture bad_plan.json` |
| V12 | 三份规格走同一份代码 | `test_three_specs_same_code_path` |
| V13 | 没模型时明确失败，回放要标模式 | `--mode live` 无密钥；`trace.mode` |

---

## 8 提交与合并（组内约定）

- 本模块走 PR 合入 main，不直接推 main；分支名按 `feat/<Issue号>-<主题>`。
- 提交前跑一遍本模块测试，不能让 main 变红。
- **共享文件必然冲突**：README、`.gitignore` 这类文件多人同时在改，冲突时**保留双方内容**，不要整篇覆盖成自己那版。
- 合并方式以组内当次约定为准（有的 PR 要求 merge commit 保留历史，其余用 squash）。

本模块的 README 只描述 PM 这一块。若与其他成员的 README 段落冲突，双方内容都留、各自成节即可，不必合并成一段。

---

## 9 还没做的

- **语义质量判断不了**。任务贴不贴合业务、验收能不能用，规则判不出，列在 `rules.SEMANTIC_CHECKLIST` 里，交给人工检查表。
- 真实调用记录要脱敏后归档，这部分等成员 5 的存储接口。
- 提示词还没在真实模型上调过，第一版只保证结构合法，质量要跑过三类规格再迭代。
