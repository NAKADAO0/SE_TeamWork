"""适配主链的插槽：backend/workflow.py 里 `planner(spec)` 是一个普通函数。

它只在 mode == "live_llm" 时被调用，返回任务列表；抛 ValueError 会被接口层转成 422。

用法（在 backend/api.py 的 create_app 里）：

    from app.planner.adapter import make_planner
    planner = make_planner()                      # 真实模型
    # planner = make_planner(provider=OfflineProvider())   # 无模型时也能跑通
"""
from . import fields as F
from .models import GenerationResult, ProviderError
from .providers import HttpProvider, OfflineProvider, Provider
from .service import generate

DEFAULT_TEMPLATE = {
    "template_id": "default",
    "version": 1,
    "stages": ["design", "implement", "verify", "integrate"],
    "allowed_roles": ["backend", "frontend", "qa"],
    "require_verification": True,
}

DEFAULT_CONFIG = {
    "max_concurrency": 2,
    "role_capacity": {"backend": 1, "frontend": 1, "qa": 1},
    "ordering": ["priority", "task_id"],
}


def make_planner(
    provider: Provider | None = None,
    template: dict | None = None,
    config: dict | None = None,
    strict: bool = True,
):
    """返回一个符合 `planner(spec) -> list[dict]` 的可调用对象。

    strict=True 时，规格不足或输出不合法都会抛 ValueError，由接口层转 422；
    strict=False 时只在完全无法生成时抛错，其余情况返回空列表（不推荐用于正式运行）。
    """
    provider = provider or HttpProvider.from_env()
    template = template or DEFAULT_TEMPLATE
    config = config or DEFAULT_CONFIG

    def planner(spec):
        result: GenerationResult = generate(spec, template, config, provider)
        return _to_task_list(result, strict)

    planner.__name__ = "planner"
    planner.provider = provider
    return planner


def _to_task_list(result: GenerationResult, strict: bool) -> list[dict]:
    if result.status == "ok":
        return F.pick(result.plan, F.PLAN, "tasks", []) or []

    if result.status == "needs_clarification":
        detail = "；".join(f"{g.code} {g.field_path}: {g.message}" for g in result.gaps[:5])
        raise ValueError("规格信息不足，请先补齐：" + detail)

    if result.status == "invalid_output":
        detail = "；".join(f"{i.code} {i.object_id or i.field_path}: {i.message}"
                          for i in result.issues if i.severity == "error")[:300]
        if strict:
            raise ValueError("规划输出不合法（已重试一次）：" + detail)
        return []

    raise ValueError("规划服务不可用：" + "；".join(i.message for i in result.issues))


def probe() -> str:
    """给 API 的健康检查用：返回当前 planner 的可用状态。"""
    try:
        HttpProvider.from_env()
        return "live_llm ready"
    except ProviderError as e:
        return f"live_llm unavailable: {e}"
