"""PM 规划模块的对外入口。

其他模块只需要认识三个名字：
    generate(spec, template, config, provider) -> GenerationResult
    GenerationResult.status   # ok / needs_clarification / invalid_output / failed
    JOB_STATUS                # 状态到成员 5 作业状态的映射
"""
from .models import GenerationResult, Gap, PlanIssue, ProviderError, Trace
from .providers import HttpProvider, OfflineProvider, ReplayProvider
from .rules import SEMANTIC_CHECKLIST, precheck, validate
from .service import JOB_STATUS, generate, summary
from .smart_rules import StructuredRuleProvider, plan_with_provider, smart_decompose

__all__ = [
    "generate", "summary", "JOB_STATUS",
    "GenerationResult", "Gap", "PlanIssue", "Trace", "ProviderError",
    "OfflineProvider", "ReplayProvider", "HttpProvider", "StructuredRuleProvider",
    "smart_decompose", "plan_with_provider",
    "precheck", "validate", "SEMANTIC_CHECKLIST",
]
