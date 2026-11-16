# 前端接口契约（草案）

状态：**待成员 5 确认**。作者：成员 1。最后更新：2026-11-16。

本文是工作台（`frontend/`）唯一依赖的接口清单。字段名取自设计文档第 4.2、5.1、5.4、7.1 节，均为 snake_case；本文只固定**前端消费到的形状**，服务端内部结构可以更细。

请求成功返回 2xx + JSON；失败返回统一错误对象：

```json
{
  "code": "REVISION_CONFLICT",
  "message": "修订已过期，请刷新后重试",
  "details": { "current_revision": 3 },
  "request_id": "req-8f1c"
}
```

| 状态码 | 何时使用 | 前端处理 |
| --- | --- | --- |
| 400 | 请求体不是合法 JSON | 顶部提示，不清空输入 |
| 404 | 规格、计划或修订不存在 | 提示并回到规格视图 |
| 409 | 修订或状态冲突 | 提示“修订已过期”，保留当前草稿 |
| 422 | 字段或领域规则失败 | 渲染 `details.issues[]`，可定位到任务 |
| 503 | 模型暂不可用 | 显示实际错误，允许切换回放模式 |

## 1. 规格

### POST /specs

提交 JSON 规格草稿，返回标准化结果与缺口。

```json
{
  "spec_id": "SPEC-CHAT",
  "version": 2,
  "draft": { "product": {}, "requirements": [], "modules": [], "design": [], "constraints": {} },
  "issues": {
    "errors": [{ "code": "MISSING_SCOPE", "field_path": "product.scope", "message": "产品范围过短" }],
    "warnings": []
  }
}
```

前端用法：`检查缺口` 与 `生成前校验` 都调用它；`errors` 非空时禁止确认。

### POST /specs/{spec_id}/confirm

请求 `{ "expected_version": 2 }`，成功返回：

```json
{ "spec_ref": { "spec_id": "SPEC-CHAT", "version": 2 }, "confirmed_at": "2026-11-16T05:20:00Z" }
```

冲突返回 409。确认后规格只读，前端解除只读即代表“派生新版本”。

### GET /templates

```json
[
  {
    "template_id": "default",
    "version": "1.0",
    "name": "标准交付模板",
    "stages": ["design", "implement", "verify", "integrate"],
    "allowed_roles": ["backend", "frontend", "qa"],
    "require_verification": true,
    "max_concurrency": 2,
    "role_capacity": { "backend": 1, "frontend": 1, "qa": 1 },
    "ordering": ["priority", "task_id"]
  }
]
```

模板是版本化配置，前端只允许改 `max_concurrency` 与 `role_capacity`。

## 2. 规划作业

### POST /planning-jobs

```json
{
  "spec_ref": { "spec_id": "SPEC-CHAT", "version": 2 },
  "template_ref": { "template_id": "default", "version": "1.0" },
  "config": { "max_concurrency": 2, "role_capacity": { "backend": 1, "frontend": 1, "qa": 1 } },
  "mode": "live_llm"
}
```

返回 202：

```json
{ "job_id": "JOB-42", "status": "pending" }
```

### GET /planning-jobs/{job_id}

```json
{ "job_id": "JOB-42", "status": "succeeded", "plan_ref": { "plan_id": "PLAN-7", "revision": 1 } }
```

状态只允许：`pending`、`running`、`needs_clarification`、`succeeded`、`invalid_output`、`failed`。失败时附带 `error: { code, message }`。

前端用法：拿到 `job_id` 后每 450 ms 轮询一次；`needs_clarification` 回到规格编辑，`invalid_output`/`failed` 显示结构化错误，成功后按 `plan_ref` 取计划。

## 3. 计划、校验与编排

### GET /plans/{plan_id}?revision=3

```json
{
  "plan_id": "PLAN-7",
  "revision": 3,
  "spec_ref": { "spec_id": "SPEC-CHAT", "version": 2 },
  "template_ref": { "template_id": "default", "version": "1.0" },
  "generation_mode": "live_llm",
  "config": { "max_concurrency": 2, "role_capacity": { "backend": 1, "frontend": 1, "qa": 1 } },
  "tasks": []
}
```

任务对象（前端用到的字段）：

```json
{
  "id": "C2",
  "title": "实现消息写入与鉴权",
  "kind": "implementation",
  "stage": "implement",
  "role": "backend",
  "requirement_ids": ["R-MSG-01"],
  "module_ids": ["M-MSG"],
  "design_refs": ["I-SEND"],
  "inputs": [{ "source": "task_output", "ref": "C1/O-CONTRACT", "type": "design_contract" }],
  "outputs": [{ "id": "O-API", "type": "implementation", "description": "消息写入接口" }],
  "acceptance": ["非会话成员写入返回 403"],
  "depends_on": [{ "task_id": "C1", "reason": "需要已固定的接口契约" }],
  "priority": 1,
  "estimated_effort": { "value": 1.5, "unit": "day", "basis": "接口与鉴权" }
}
```

### PUT /plans/{plan_id}

请求 `{ "expected_revision": 3, "tasks": [] }`，成功返回 `{ "plan_id": "PLAN-7", "revision": 4 }`；`expected_revision` 不等于当前修订返回 409。

前端用法：界面上每次编辑都会本地 +1 修订并立即标“待重新编排”，真正落库时可批量提交；409 时保留本地草稿并提示。

### POST /plans/{plan_id}/validate

请求 `{ "revision": 4 }`，返回：

```json
{
  "plan_ref": { "plan_id": "PLAN-7", "revision": 4 },
  "blocking": [
    {
      "severity": "error",
      "code": "REQUIREMENT_WITHOUT_VERIFICATION",
      "object_id": "R-MSG-02",
      "field_path": "requirements[R-MSG-02]",
      "message": "需求 R-MSG-02 没有验证任务",
      "related_ids": ["C4", "C5"],
      "suggested_action": "补一项验证任务"
    }
  ],
  "warnings": []
}
```

### POST /plans/{plan_id}/compile

请求 `{ "revision": 4, "config": { "max_concurrency": 2, "role_capacity": {} } }`，返回：

```json
{
  "compiled": true,
  "plan_ref": { "plan_id": "PLAN-7", "revision": 4, "execution_plan_id": "EXE-9", "digest": "sha256:..." },
  "topological_levels": [["C1"], ["C2", "C3"]],
  "batches": [
    {
      "index": 0,
      "tasks": ["C1"],
      "bindings": { "backend": [{ "task_id": "C1", "slot": 1 }] },
      "waiting": [{ "task_id": "C4", "reason": "等待前置 C2" }]
    }
  ],
  "nodes": ["C1", "C2"],
  "edges": [{ "from": "C1", "to": "C2", "reason": "需要已固定的接口契约" }],
  "blocked": []
}
```

`compiled: false` 时 `batches` 为已排出的部分，`blocked[]` 给出每个未安排任务的原因；前端只显示“被阻塞”，不把半成品当成功。

### POST /plans/{plan_id}/confirm

请求 `{ "revision": 4, "execution_plan_id": "EXE-9" }`。服务端需再次核对修订、摘要、错误数与编排引用；成功返回 `{ "confirmed_at": "..." }`，冲突返回 409 或 422。

### GET /plans/{plan_id}/export?revision=4&format=json

返回一个完整计划包；`format=markdown` 返回同样内容的 Markdown。前端下载 `filename`，不自行拼装。

## 4. 与前端状态的对应

| 接口 | reducer action | 界面位置 |
| --- | --- | --- |
| POST /specs | `SPEC_CHECKED` | 规格编辑器问题列表 |
| POST /specs/{id}/confirm | `SPEC_CONFIRMED` | 规格只读 + `spec_ref` |
| POST /planning-jobs + 轮询 | `JOB_STARTED` / `JOB_UPDATED` / `JOB_SUCCEEDED` | 顶部阶段标签与提示 |
| POST /plans/{id}/validate | `VALIDATED` | 校验与批次面板 |
| POST /plans/{id}/compile | `COMPILED` | 依赖图与批次 |
| POST /plans/{id}/confirm | `CONFIRMED` | 阶段变“已确认”，编辑派生新草稿 |

## 5. 换后端时要改的地方

只有 `frontend/src/shared/api.js` 的 `createHttpApi()`。界面与 reducer 不感知 HTTP。切换方式：页面加 `?api=http`，并把 `base` 指到服务端前缀。
