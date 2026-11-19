import unittest

from backend.app.planner.workstreams import validate_workstreams


class WorkstreamsTest(unittest.TestCase):
    def test_model_groups_must_partition_real_tasks(self):
        tasks = [{"id": "T1"}, {"id": "T2"}, {"id": "T3"}]
        raw = [{"id": "WP1", "title": "预约链路", "summary": "查询与提交", "task_ids": ["T1","T2"]},
               {"id": "WP2", "title": "回归验收", "summary": "覆盖预约冲突", "task_ids": ["T3"]}]
        self.assertEqual(validate_workstreams(raw, tasks), raw)
        raw[1]["task_ids"] = ["T2", "T4"]
        with self.assertRaises(ValueError):
            validate_workstreams(raw, tasks)
        with self.assertRaises(ValueError):
            validate_workstreams(raw[:1], tasks)
        with self.assertRaises(ValueError):
            validate_workstreams(None, tasks)
