"""PM 规划模块的规则测试。

跑法（在 backend/ 下）：python -m unittest discover -s tests -v
不依赖 pytest，避免为一次演示装依赖。
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # backend/
sys.path.insert(0, str(ROOT / "app"))

from planner import (  # noqa: E402
    OfflineProvider, ReplayProvider, generate, precheck, summary, validate,
)
from planner import fields as F  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
TEMPLATE = {
    "allowed_roles": ["backend", "frontend", "qa"],
    "require_verification": True,
}
CONFIG = {"max_concurrency": 2}


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def tasks_of(result):
    return F.pick(result.plan, F.PLAN, "tasks", [])


class TestPrecheck(unittest.TestCase):
    """规格不够用时不调模型，直接告诉用户缺什么。"""

    def test_missing_acceptance_is_gap(self):
        spec = load("spec_chat.json")
        spec["requirements"][0].pop("acceptance")
        gaps = precheck(spec)
        self.assertTrue(any(g.code == "G005" for g in gaps))

    def test_missing_modules_is_gap(self):
        spec = load("spec_chat.json")
        spec.pop("modules")
        self.assertTrue(any(g.code == "G006" for g in precheck(spec)))

    def test_empty_product_is_gap(self):
        spec = load("spec_chat.json")
        spec["product"] = "  "
        self.assertTrue(any(g.code == "G001" for g in precheck(spec)))

    def test_good_spec_has_no_gap(self):
        for name in ("spec_chat.json", "spec_community.json", "spec_unseen.json"):
            self.assertEqual(precheck(load(name)), [], name)


class TestOfflineGenerate(unittest.TestCase):
    """接模型前的主路径：三条需求各自有实现和验证。"""

    def test_ok_and_covers_every_requirement(self):
        spec = load("spec_chat.json")
        result = generate(spec, TEMPLATE, CONFIG, OfflineProvider())
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.trace.attempts, 1)
        self.assertEqual(len(tasks_of(result)), 2 * len(spec["requirements"]))

    def test_three_specs_same_code_path(self):
        for name in ("spec_chat.json", "spec_community.json", "spec_unseen.json"):
            result = generate(load(name), TEMPLATE, CONFIG, OfflineProvider())
            self.assertEqual(result.status, "ok", name)
            self.assertGreater(len(tasks_of(result)), 0, name)

    def test_needs_clarification_skips_model(self):
        spec = load("spec_chat.json")
        spec["requirements"][0]["acceptance"] = []
        provider = ReplayProvider("{}")
        result = generate(spec, TEMPLATE, CONFIG, provider)
        self.assertEqual(result.status, "needs_clarification")
        self.assertEqual(provider.calls, 0)


class TestRepairOnce(unittest.TestCase):
    """坏输出只修一次，仍坏就报 invalid_output（对应 V03）。"""

    def test_bad_output_repaired_once_then_fails(self):
        bad = (FIX / "bad_plan.json").read_text(encoding="utf-8")
        provider = ReplayProvider(bad)
        result = generate(load("spec_chat.json"), TEMPLATE, CONFIG, provider)
        self.assertEqual(result.status, "invalid_output")
        self.assertEqual(provider.calls, 2)          # 首次 + 修复，不会第三次
        self.assertEqual(result.trace.attempts, 2)
        self.assertTrue(result.rejected_output)      # 原文留给人工排查
        codes = {i.code for i in result.issues}
        self.assertIn("E015", codes)                 # 验收条件过短
        self.assertIn("E017", codes)                 # 引用不存在的需求
        self.assertIn("E021", codes)                 # 依赖不存在的任务

    def test_good_replay_passes_without_repair(self):
        good = (FIX / "good_plan.json").read_text(encoding="utf-8")
        provider = ReplayProvider(good)
        result = generate(load("spec_chat.json"), TEMPLATE, CONFIG, provider)
        self.assertEqual(result.status, "ok")
        self.assertEqual(provider.calls, 1)

    def test_live_plan_without_any_role_handoff_is_repaired(self):
        linked = load("good_plan.json")
        flat = load("good_plan.json")
        for task in flat["tasks"]:
            task["role"] = "backend"

        class TwoOutputs:
            name = "live_llm"

            def __init__(self):
                self.calls = []

            def complete(self, messages):
                self.calls.append(messages)
                return json.dumps(flat if len(self.calls) == 1 else linked)

        provider = TwoOutputs()
        result = generate(load("spec_chat.json"), TEMPLATE, CONFIG, provider)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.trace.attempts, 2)
        self.assertIn("跨角色", provider.calls[1][-1]["content"])


class TestProviderFailure(unittest.TestCase):
    """模型挂了要报 failed，不能伪装成成功（对应 V13）。"""

    def test_provider_error_returns_failed(self):
        from planner.models import ProviderError

        class Boom:
            name = "live_llm"

            def complete(self, messages):
                raise ProviderError("连接超时")

        result = generate(load("spec_chat.json"), TEMPLATE, CONFIG, Boom())
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.trace.mode, "live_llm")


class TestValidate(unittest.TestCase):
    """校验器本身的边界。"""

    def test_cycle_reports_real_path(self):
        plan = {
            "tasks": [
                {"id": "A", "title": "a", "kind": "implementation", "role": "backend",
                 "requirement_ids": ["R-MSG-01"], "acceptance": ["可用"],
                 "depends_on": [{"task_id": "B", "reason": "b"}], "priority": 1},
                {"id": "B", "title": "b", "kind": "implementation", "role": "backend",
                 "requirement_ids": ["R-MSG-01"], "acceptance": ["可用"],
                 "depends_on": [{"task_id": "A", "reason": "a"}], "priority": 1},
            ],
            "open_questions": [],
        }
        codes = {i.code for i in validate(plan, load("spec_chat.json"), TEMPLATE)}
        self.assertIn("E030", codes)

    def test_missing_verification_detected(self):
        spec = load("spec_chat.json")
        plan = {
            "tasks": [
                {"id": "A", "title": "a", "kind": "implementation", "role": "backend",
                 "requirement_ids": ["R-MSG-01", "R-MSG-02"], "acceptance": ["可用"],
                 "depends_on": [], "priority": 1},
            ],
            "open_questions": [],
        }
        codes = {i.code for i in validate(plan, spec, TEMPLATE)}
        self.assertIn("E032", codes)


class TestFieldAliases(unittest.TestCase):
    """成员 3 若改了字段名，只要加别名就能读，不用改业务代码。"""

    def test_alias_task_id(self):
        task = {"task_id": "X1", "name": "标题", "type": "implementation"}
        self.assertEqual(F.pick(task, F.TASK, "id"), "X1")
        self.assertEqual(F.pick(task, F.TASK, "kind"), "implementation")


class TestBackendAdapter(unittest.TestCase):
    """主链插槽 planner(spec) -> list，对接 backend/workflow.py。"""

    def setUp(self):
        from planner.adapter import make_planner
        self.planner = make_planner(provider=OfflineProvider())

    def test_returns_plain_task_list(self):
        tasks = self.planner(load("spec_chat.json"))
        self.assertIsInstance(tasks, list)
        self.assertEqual(len(tasks), 4)
        # 必须带成员 5 存库要用的最小字段
        for key in ("id", "title", "role", "depends_on", "acceptance"):
            self.assertIn(key, tasks[0])

    def test_missing_spec_raises_value_error(self):
        """接口层把 ValueError 转成 422，所以这里必须抛 ValueError 而不是自定义异常。"""
        spec = load("spec_chat.json")
        spec["requirements"] = []
        with self.assertRaises(ValueError):
            self.planner(spec)


if __name__ == "__main__":
    unittest.main(verbosity=2)
