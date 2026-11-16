# backend

Lab2 后端工作区。当前已落地**成员 3 的数据契约与校验**模块；规划（成员 2）、编排（成员 4）、API/持久化（成员 5）在此基础上继续开发。

## 快速开始

零第三方依赖，Python 3.11+：

```bash
# 契约与校验测试
python -m unittest discover -s backend/tests -t . -v

# 模型变更后重新导出 JSON Schema（会写入 backend/app/contracts/schema/）
python -m backend.app.contracts.export_schema --write
python -m backend.app.contracts.export_schema --check   # CI 用：只检查是否一致

# 模型/样例变更后重算派生 fixture（执行计划、校验报告、错误样例）
python -m backend.tools.build_fixtures --write
python -m backend.tools.build_fixtures --check
```

命令均在仓库根目录执行。

## 目录

| 路径 | 归属 | 说明 |
| --- | --- | --- |
| `backend/app/contracts/` | 成员 3 | 契约模型、Schema 导出、旧案例迁移 |
| `backend/app/contracts/schema/*.schema.json` | 成员 3（生成物） | 由模型导出，勿手改 |
| `backend/app/validation/` | 成员 3 | 结构检查 + 规格/计划/执行计划校验 |
| `backend/app/planner/` | 成员 2 | PM 规划与模型适配（待开发） |
| `backend/app/orchestration/` | 成员 4 | 编排/排批（待开发） |
| `backend/app/api/`、`repositories/` | 成员 5 | FastAPI 接口与持久化（规划位置；成员 5 当前实现在 `backend/*.py`，见其 PR） |
| `backend/tools/` | 成员 3 | fixture 生成工具 |
| `backend/tests/` | 全员 | 后端测试；成员 3 用例在 `test_contracts.py` / `test_validation.py` |

共享样例在仓库根 `examples/contracts/`、模板在 `templates/`，规范见 [docs/契约与校验规范.md](../docs/契约与校验规范.md)。

## 契约变更流程

1. 改 `backend/app/contracts/models.py`（字段元数据同时驱动 Schema 与结构校验）。
2. `export_schema --write` 更新 Schema；`build_fixtures --write` 更新派生 fixture。
3. `python -m unittest ...` 全部通过。
4. PR 中说明契约影响（字段、错误码、字段路径），并由非作者评审。

约束：不得手改 `.schema.json`；不得在契约外新增未登记字段；错误码统一在 `backend/app/validation/codes.py` 维护。
