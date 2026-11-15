# SE_TeamWork
软件工程与项目实践_TeamWork

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
