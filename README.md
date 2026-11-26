# SE_TeamWork
软件工程与项目实践_TeamWork

## 工作流控制台（新增主入口）

运行 `python -m backend`（Python 3.11+），打开 <http://127.0.0.1:8010/frontend/studio.html>。
本机现有兼容环境可用 `.venv-new/bin/python -m backend`。主页主按钮已指向新入口，原 `/frontend/` 编排工作台继续保留。

新控制台覆盖原始需求 / 规格导入、需求分析、产品设计、系统架构、原 PM 规划、原校验与编排、人工确认和计划导出。
三个设计阶段分别确认；Agent 独立提案，主持记录采纳、拒绝与待补证据，独立审查通过后等待人工确认。
支持暂停 / 继续、单步、显式重新规划、产物版本与差异、事件时间线及只读回放。
现有 PM、规格提取和代码审查 prompt 未改；新增讨论 prompt 独立存放。

任务规划支持点击任务查看侧边详情。计划确认支持设置总并发和角色容量，修改后需重新计算批次才能确认。确认后进入编排执行，提供工作包、依赖图、批次图与侧边详情；点击「开始执行」才派发 Agent。验证交付已接入原代码审查与人工审核模块。Agent 开发沿用现有 Lab1 服务（默认 8051）；原页面继续保留。更新后需重启后端以加载新增资源重算命令。

详细接口、数据、运行限制与验证方式见 [工作流控制台说明](docs/studio-workflow.md)。

## 原编排工作台运行

系统已打通「设计规格输入（JSON / Markdown）→ 缺口检查与规格确认 → PM Agent / 结构化规则任务分解 → 契约与图校验 → 确定性资源编排 → 交互式工作台与 DAG 图谱可视化 → 修订控制与计划包导出」全链路：

```sh
python -m pip install -r backend/requirements.txt
# 可选：复制 .env.example 为 .env 并填入 PM_BASE_URL / PM_API_KEY / PM_MODEL 以启用实时 PM Agent
python -m backend
```

自然语言需求可粘贴到工作台，或通过「导入文件」加载 TXT / Markdown。使用「AI_Review · 生成范围」得到范围草稿，人工编辑确认后再生成其余规格。导入完整说明书可直接使用「AI_Review · 完整规格」。人工核对报告与「AI 补充假设」后确认规格，再生成工作流。

启动后直接打开 http://127.0.0.1:8010/ （或直接进入工作台 http://127.0.0.1:8010/frontend/ ）：
- **输入规格**：内置「聊天 MVP」「内容社区 MVP」「校园失物招领 MVP（Markdown 现场规格）」，也支持粘贴或导入任意自定义产品的 `.json` / `.md` 设计规格。
- **工作台生成方式**：使用实时 PM Agent。启动前配置 PM_BASE_URL、PM_API_KEY、PM_MODEL。
- **验证全部测试**：
  - `python -m unittest discover -s tests -p "test_*.py" -v`（API、持久化与端到端全链路测试）
  - `python -m unittest discover -s backend/tests -t . -v`（契约、校验器、PM 规划与编排器单元测试）
  - `npm test`（前端状态机、缺口检查、依赖连线、排批与视图测试）

### 编排画布与人工介入

左栏填写或导入设计规格、执行规格 AI_Review 并人工确认，再点「提交规格并生成工作流」。默认画布显示 PM Agent 根据这份规格产出的工作包；每张卡片展开小任务、进度、跨包依赖和讨论。工作包是已生成任务的分组，箭头由小任务依赖汇总；实际排程仍以小任务图为准。没有工作包字段的历史计划按已有任务归组。「流程与审核」页签保留规格、计划与审核的阶段总览。画布可展开，按 Esc 恢复布局。

将 Lab1 协作服务启动在本机 8051 端口；若 Lab1 使用其他端口，在启动 Lab2 时设置 `LAB1_PORT`。确认执行计划后，可从任意工作包启动规则演示或 DeepSeek 执行。工作包显示所属小任务的真实运行状态；「打断并加入讨论」会暂停项目当前批次，工作包编号随意见一同保存，用「继续当前批次」恢复。已完成批次会保留；运行结果是待复核草稿，代码审查与人工批准仍需独立完成。

### 审查中心的离线演示

1. 通过后台服务打开工作台。生成并编排任务后，在「执行编排」点击「确认版本」。
2. 选择「代码 AI_Review」卡片并提交演示开发产物。可以提交模拟通过报告，也可以模拟阻断或超时，观察返工和重试。
3. 自动门禁通过后显示「待人工审核」。填写意见，选择批准、要求修改或拒绝；只有批准会解除下游审核阻塞。
4. 提交下一版，检查之前的放行立即失效。历史轮次仍可以查看，刷新页面后状态从 SQLite 恢复。

这是本地演示，当前不读取 GitHub 差异、不运行真实 CI、不验证账号身份，也不执行合并。API 使用 `/plans/{plan_id}/review` 读取和提交当前轮次，使用 `/reports`、`/retry`、`/decision`、`/stop` 处理证据、重试、审核和异常接管。接口模型可在本地服务的 `/docs` 查看。

## Lab2 离线演示

在仓库根目录运行 `python -m http.server 8000 --bind 127.0.0.1`，打开 http://127.0.0.1:8000/ 。选择产品，编辑需求与验收条件，生成计划。把并发从 2 改成 1 后重新生成，可比较批次数并导出 JSON。用 Node 22 运行 `node --test tests/demo.test.mjs` 检查核心规则。

此版本用浏览器内的规则将每条需求转换为实现和验证任务，展示前置关系及批次。正式设计中的实时 PM、后端存储、角色容量和 DAG 画布列入下一阶段。完整分工和评审约定在 docs 下。

## 从演示基线继续开发

先通过 feat/lab2-demo 到 main 的 PR 评审演示。为保留分功能历史，该基线 PR 可选择 Create a merge commit。后续短期分支按组内约定 squash merge。基线合入前可签出远程 feat/lab2-demo 运行演示。合入后每位成员从最新 main 开分支。

|成员|第一项工作|交接与验证|
|---|---|---|
|1 输入与编辑|把规格、任务编辑拆成独立组件|和 5 对齐请求，与 6 验证选择任务后详情一致|
|2 PM|沿用 decompose 的调用位置接入服务端 PM，保留离线模式|与 3 对齐 TaskPlan，使用第三类规格保存真实生成记录|
|3 契约校验|用设计中的 DesignSpec、TaskPlan 建立共享 Schema 和两个产品的完整 fixture|提供方、编排和界面共同确认，再迁移现有简化 JSON|
|4 编排|从 compile 的输入输出加角色容量、稳定优先级与等待原因|与 3 核对引用，与 6 核对批次展示，补容量回归测试|
|5 API 与存储|提供规格、作业、计划查询 API，保存 SQLite 修订|与 1 联调，用旧修订验证 409|
|6 图与集成|将依赖与批次渲染为 DAG，建立浏览器主流程验证|与 4 核对边方向，组织 V01–V14 验收|

每项工作先开 Issue 写明交付物、接口影响和验收方法。以实际 Issue 号建立分支。

~~~sh
git switch main
git pull --ff-only origin main
git switch -c feat/12-pm-plan
~~~

按改动提交并推送个人分支，向 main 发起 PR，关联 Issue，附实际测试结果。至少一名非作者批准，公共字段由受影响成员复核。密钥放在服务端环境变量。课堂交付验收后再建立 v1.0.0-lab2 标签。

组员开工时先看[六人开发认领表](docs/六人开发认领表.md)，按成员编号领取第一项任务。

## 成员 5 计划服务

在仓库根目录的 Python 3.11+ 虚拟环境中运行：

~~~sh
python -m pip install -r backend/requirements.txt
python -m uvicorn backend.api:app --host 127.0.0.1 --port 8010
~~~

成员 1、3、6 可在 /docs 调用 GET /plans/chat-demo?revision=1。响应包含 plan_id、revision 和 plan；plan.tasks 含 8 个节点，plan.batches 为批次。省略 revision 返回最新版本。数据库默认保存在 .local/plans.sqlite3，可用 PLAN_DB 指定路径。运行 python -m unittest discover -s tests -p test_plans.py 执行 API 测试。

修改计划时先 GET，再用 PUT /plans/chat-demo 提交 {"expected_revision":1,"plan":完整编辑后的计划对象}。成功返回新 revision 和草稿；原修订仍可查询，草稿需重新编排。若返回 409，读取 detail.current_revision，重新载入当前内容，核对差异后再保存。

### 成员 5 联调路径

运行环境需要 Python 和 Node 22（node 在 PATH 中）。服务启动命令同上，在 http://127.0.0.1:8010/docs 可依次调用：

1. POST /specs 提交 examples/chat.json 形状的 JSON，记录 spec_id。POST /specs/{id}/confirm 提交 {"expected_version":1}。
2. POST /planning-jobs 提交 {"spec_ref":{"spec_id":"替换为实际编号","version":1},"mode":"offline_rule","config":{"max_concurrency":2}}。用 GET /planning-jobs/{job_id} 轮询，成功后读取 plan_ref。
3. 向 /plans/{id}/validate 和 /plans/{id}/compile 依次 POST {"revision":1}，保存 execution_plan_id。检查报告及节点、边、批次。
4. POST /plans/{id}/confirm 提交 revision 和 execution_plan_id。通过 GET /plans/{id}/export?revision=1&format=json 下载固定版本，format=markdown 输出可读清单和完整快照。
5. PUT 保存下一版后需重新校验、编排和确认。旧版仍能导出。运行 python -m unittest discover -s tests 验证完整链路。

本地演示使用单个 Uvicorn worker。作业状态持久化，服务重启将未结束作业标记失败，可重新提交。成员 2 可向 create_app 注入 planner(spec) 返回任务列表；成员 3 注入 validator(plan) 返回错误列表；成员 4 注入 compiler(plan) 返回批次列表。默认使用 Demo 的 Node 规则服务和基础字段检查。实时模式未配置时返回 503。前端成员通过这些端点替换 sessionStorage 调用。

## v2 工作台（成员 1 交付）

`frontend/` 是规格输入与任务编辑工作台的实现，用 fixture 数据驱动，可直接演示：

~~~sh
python -m http.server 8010 --bind 127.0.0.1
~~~

打开 http://127.0.0.1:8010/frontend/ ，流程为 载入样例 → 检查缺口 → 确认规格 → 生成计划 → 校验 → 编排 → 确认版本 → 导出。
接口契约见 `docs/frontend-api-contract.md`，交接说明见 `docs/handoff/成员1-交接.md`。
Node 22+ 运行 `npm test` 可复核状态机、缺口检查、环检测、排批与视图冒烟；加 `?api=http` 可切到真实服务端。


## 图谱读取数据库计划

图谱可直接读取成员 5 的 SQLite 计划、修订和已保存编排结果。保持前端静态服务在 8000，另一个终端启动后端（Python 3.11+，Node 22+）：

```sh
python -m pip install -r backend/requirements.txt
python -m uvicorn backend.api:app --host 127.0.0.1 --port 8010
```

打开 http://127.0.0.1:8000/graph.html?api=http&plan_id=chat-demo 。顶部计划和版本下拉框读取数据库，最新修订排在前面。`revision=1` 固定读取历史版本；省略时读取最新。自定义服务地址可用 `api=http://127.0.0.1:其他端口`。不带 `api` 参数时继续使用原有本地入口。

图谱新增的只读端点为 `GET /plans`、`GET /plans/{id}/revisions` 和 `GET /plans/{id}/graph?revision=1`，复用原 `plans`、`revisions`、`artifacts` 表，不增加数据库或迁移。未编排的草稿提示先编排；查询历史版本不会重算批次或修改确认状态。任务执行进度尚无服务端来源，数据库模式显示未接入。

运行 `python -m unittest discover -s tests -p 'test_*.py'` 检查版本隔离、读接口、并发保存与完整工作流；`npm test` 检查前端现有规则。

图谱一致性回归覆盖 JSON/Markdown 导出、任务与依赖边、执行批次、报告引用，以及修改容量和依赖后历史修订在服务重启前后的稳定性；已由现有 Plan API CI 自动发现并执行。

## Lab2 AI_Review、人工 Review 与验收记录

- 规格编辑页的「AI_Review」调用 DeepSeek 检查设计说明书，给出按章节定位的问题、建议和可编辑修订稿。可以对照原文；人工 Review 后才确认规格。
- 「审查中心」接收用户主动提供的提交 SHA 与代码差异。真实轮由 DeepSeek 生成规范、逻辑和依赖三类报告；模拟报告不能写入真实轮。服务端规则判定是否进入人工 Review，后者可以批准、要求修改或拒绝。
- 审核记录绑定计划修订、提交 SHA 及所提交差异的摘要。差异由用户提供，原型不会向 GitHub 核验其与 SHA 的对应关系。不会执行代码、测试或自动合并。
- [长篇校园实验设备预约 DesignSpec](examples/campus-device-reservation/design-spec.md) 可用于复测。脚本 `examples/campus-device-reservation/run_live.py` 在新目录保存原文、模型报告、TaskPlan、ExecutionPlan 和 SQLite 阶段快照。

从仓库根目录执行，使用新的记录路径：

```powershell
$env:PYTHONPATH = (Get-Location).Path
python examples/campus-device-reservation/run_live.py --out ..\Lab2\output\DeepSeek验收\新运行目录
```

复原时应复制快照到独立数据库，不要覆盖在用数据库。用 `PLAN_DB` 指向该副本并以空闲端口启动。随后访问 `/frontend/?plan=计划ID&revision=修订号` 重开已确认快照；加 `&stage=flow` 切到执行图谱。
