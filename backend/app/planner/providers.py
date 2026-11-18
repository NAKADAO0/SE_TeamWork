"""模型适配层：三种模式实现同一个接口，上层不感知区别。

- OfflineProvider：规则分解，接模型前就能联调，也是现场故障时的降级。
- ReplayProvider：回放固定响应，用于测试修复逻辑和演示兜底。
- HttpProvider：OpenAI 兼容接口，真实调用。
"""
import json
import os
import re
import urllib.error
import urllib.request
from typing import Protocol

from . import fields as F
from .models import ProviderError


class Provider(Protocol):
    name: str

    def complete(self, messages: list[dict]) -> str: ...


def _extract_payload(messages: list[dict]) -> dict:
    for m in messages:
        if m.get("role") == "user":
            try:
                return json.loads(m["content"])
            except (ValueError, KeyError):
                continue
    return {}


class OfflineProvider:
    """不调模型，用固定规则把每条需求拆成一个实现任务和一个验证任务。

    它的作用是让 4/5/6 在模型接通前就能联调，不是最终形态。
    """

    name = "offline_rule"

    def complete(self, messages: list[dict]) -> str:
        payload = _extract_payload(messages)
        plan = decompose(payload.get("design_spec") or {}, payload.get("workflow_template") or {})
        return json.dumps(plan, ensure_ascii=False)


class ReplayProvider:
    """每次返回同一段文本，用于验证“修复一次后仍失败”的路径。"""

    name = "fixture_replay"

    def __init__(self, text: str):
        self._text = text
        self.calls = 0

    @classmethod
    def from_file(cls, path: str) -> "ReplayProvider":
        with open(path, encoding="utf-8") as f:
            return cls(f.read())

    def complete(self, messages: list[dict]) -> str:
        self.calls += 1
        return self._text


class HttpProvider:
    """OpenAI 兼容的 /chat/completions，只用标准库，避免为演示装依赖。"""

    name = "live_llm"

    def __init__(self, base_url: str, api_key: str, model: str, timeout: int = 120):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "HttpProvider":
        base = os.environ.get("PM_BASE_URL", "")
        key = os.environ.get("PM_API_KEY", "")
        model = os.environ.get("PM_MODEL", "")
        missing = [n for n, v in (("PM_BASE_URL", base), ("PM_API_KEY", key), ("PM_MODEL", model)) if not v]
        if missing:
            raise ProviderError("缺少环境变量：" + "、".join(missing))
        return cls(base, key, model)

    def complete(self, messages: list[dict]) -> str:
        body = json.dumps(
            {"model": self.model, "messages": messages, "temperature": 0.2},
            ensure_ascii=False,
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise ProviderError(f"模型返回 HTTP {e.code}") from e
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            raise ProviderError(f"模型调用失败：{e}") from e

        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise ProviderError("模型响应缺少 choices[0].message.content") from e


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", str(text)).strip("-") or "REQ"


def decompose(spec: dict, template: dict) -> dict:
    """规则分解。与 PM 的产出一字段对齐，便于替换。"""
    roles = template.get("allowed_roles") or ["backend", "frontend", "qa"]
    impl_role = roles[0]
    ver_role = "qa" if "qa" in roles else roles[-1]

    reqs = F.pick(spec, F.SPEC, "requirements") or []
    modules = F.pick(spec, F.SPEC, "modules") or []

    def as_list(value) -> list:
        if value is None:
            return []
        return value if isinstance(value, list) else [value]

    def modules_of(rid: str) -> list[str]:
        got = []
        for m in modules:
            if rid in as_list(F.pick(m, F.MODULE, "requirement_ids")):
                mid = F.pick(m, F.MODULE, "id")
                if mid:
                    got.append(str(mid))
        return got

    tasks = []
    for req in reqs:
        rid = str(F.pick(req, F.REQ, "id"))
        goal = str(F.pick(req, F.REQ, "goal") or "")
        acc = as_list(F.pick(req, F.REQ, "acceptance"))
        impl_id = f"T-{_slug(rid)}-I"
        ver_id = f"T-{_slug(rid)}-V"
        out_impl = f"O-{_slug(rid)}-I"
        out_ver = f"O-{_slug(rid)}-V"
        mods = modules_of(rid)

        tasks.append({
            F.out(F.TASK, "id"): impl_id,
            F.out(F.TASK, "title"): f"实现 {goal}",
            F.out(F.TASK, "description"): f"按设计规格实现「{goal}」，产出可被调用的实现产物。",
            F.out(F.TASK, "kind"): "implementation",
            F.out(F.TASK, "stage"): "implement",
            F.out(F.TASK, "role"): impl_role,
            F.out(F.TASK, "requirement_ids"): [rid],
            F.out(F.TASK, "module_ids"): mods,
            F.out(F.TASK, "design_refs"): [],
            F.out(F.TASK, "inputs"): [],
            F.out(F.TASK, "outputs"): [{
                F.out(F.OUTPUT_REF, "id"): out_impl,
                F.out(F.OUTPUT_REF, "type"): "implementation",
                F.out(F.OUTPUT_REF, "description"): f"{goal} 的实现产物",
            }],
            F.out(F.TASK, "acceptance"): acc or [f"{goal} 可被调用"],
            F.out(F.TASK, "depends_on"): [],
            F.out(F.TASK, "priority"): 1,
            F.out(F.TASK, "estimated_effort"): {
                F.out(F.EFFORT, "value"): 8,
                F.out(F.EFFORT, "unit"): "人时",
                F.out(F.EFFORT, "basis"): "按单条需求的实现工作量粗估",
            },
        })
        tasks.append({
            F.out(F.TASK, "id"): ver_id,
            F.out(F.TASK, "title"): f"验证 {goal}",
            F.out(F.TASK, "description"): f"对「{goal}」按验收条件执行验证并给出结论。",
            F.out(F.TASK, "kind"): "verification",
            F.out(F.TASK, "stage"): "verify",
            F.out(F.TASK, "role"): ver_role,
            F.out(F.TASK, "requirement_ids"): [rid],
            F.out(F.TASK, "module_ids"): mods,
            F.out(F.TASK, "design_refs"): [],
            F.out(F.TASK, "inputs"): [{
                F.out(F.INPUT_REF, "source"): "task_output",
                F.out(F.INPUT_REF, "ref"): f"{impl_id}/{out_impl}",
                F.out(F.INPUT_REF, "type"): "implementation",
            }],
            F.out(F.TASK, "outputs"): [{
                F.out(F.OUTPUT_REF, "id"): out_ver,
                F.out(F.OUTPUT_REF, "type"): "verification_report",
                F.out(F.OUTPUT_REF, "description"): f"{goal} 的验证报告",
            }],
            F.out(F.TASK, "acceptance"): acc or [f"{goal} 的验证结果符合预期"],
            F.out(F.TASK, "depends_on"): [
                {F.out(F.DEPEND, "task_id"): impl_id,
                 F.out(F.DEPEND, "reason"): "需要实现产物才能验证"}
            ],
            F.out(F.TASK, "priority"): 2,
            F.out(F.TASK, "estimated_effort"): {
                F.out(F.EFFORT, "value"): 4,
                F.out(F.EFFORT, "unit"): "人时",
                F.out(F.EFFORT, "basis"): "按单条需求的验证工作量粗估",
            },
        })

    return {
        F.out(F.PLAN, "tasks"): tasks,
        F.out(F.PLAN, "assumptions"): [],
        F.out(F.PLAN, "clarifications"): [],
    }
