"""数据结构：只定义形状，不依赖任何模型或框架。"""
from dataclasses import dataclass, field, asdict
from typing import Any, Literal

Status = Literal["ok", "needs_clarification", "invalid_output", "failed"]
Severity = Literal["error", "warning"]


@dataclass
class Gap:
    """规格本身缺信息，不能生成计划。"""
    code: str
    message: str
    field_path: str = ""


@dataclass
class PlanIssue:
    """计划输出不合法（error）或可疑（warning）。"""
    code: str
    severity: Severity
    message: str
    object_id: str = ""
    field_path: str = ""


@dataclass
class Trace:
    """一次生成的可观测记录，写进作业记录与导出。"""
    mode: str = "offline_rule"          # live_llm / offline_rule / fixture_replay
    model: str = ""
    prompt_version: str = "p1"
    attempts: int = 0                   # 1 = 首次成功；2 = 用过一次修复
    latency_ms: int = 0
    tokens: int = 0
    trace_id: str = ""


@dataclass
class GenerationResult:
    status: Status
    plan: dict[str, Any] | None = None
    gaps: list[Gap] = field(default_factory=list)
    issues: list[PlanIssue] = field(default_factory=list)
    trace: Trace = field(default_factory=Trace)
    # 修复仍失败时保留模型最后一次的原文，便于人工排查，不对外当成果
    rejected_output: str = ""

    @property
    def has_error(self) -> bool:
        return any(i.severity == "error" for i in self.issues)

    @property
    def warnings(self) -> list[PlanIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["has_error"] = self.has_error
        return d


class ProviderError(RuntimeError):
    """模型调用失败（网络、鉴权、限流、超时）。"""
