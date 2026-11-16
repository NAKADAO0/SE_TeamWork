"""契约层测试：Schema 导出、摘要、强类型模型、旧案例迁移与 fixture 新鲜度。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from backend.app.contracts import (
    CONTRACT_MODELS,
    SCHEMA_DIR,
    check_schemas,
    content_digest,
    contract_schemas,
    from_dict,
    normalize_legacy_examples,
    normalize_legacy_plan,
    normalize_legacy_spec,
    to_jsonable,
)
from backend.app.contracts.models import DesignSpec, ExecutionPlan, TaskPlan, ValidationReport
from backend.app.contracts.schema import schema_documents
from backend.app.validation import codes
from backend.app.validation.structure import check_structure
from backend.app.validation.validator import validate_design_spec, validate_task_plan

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_DIR = REPO_ROOT / "examples" / "contracts"
TEMPLATE_PATH = REPO_ROOT / "templates" / "default-workflow.json"


def _load(relative: str) -> Any:
    with (REPO_ROOT / relative).open("r", encoding="utf-8", newline="") as handle:
        return json.load(handle)


def _code_paths(issues: list[Any]) -> list[tuple[str, str]]:
    return [(issue.code, issue.field_path) for issue in issues]


class SchemaExportTest(unittest.TestCase):
    """模型是单一来源：磁盘 Schema 必须等于模型导出结果。"""

    def test_schema_files_match_models(self) -> None:
        self.assertEqual(check_schemas(), [])

    def test_schema_directory_contains_exactly_exported_files(self) -> None:
        expected = set(schema_documents())
        actual = {path.name for path in SCHEMA_DIR.glob("*.schema.json")}
        self.assertEqual(actual, expected)

    def test_schema_documents_embed_root_model_in_defs(self) -> None:
        """回归：根模型必须进入 ``$defs``，否则结构校验引用解析会 KeyError。"""
        schemas = contract_schemas()
        self.assertEqual(set(schemas), set(CONTRACT_MODELS))
        for key, model in CONTRACT_MODELS.items():
            document = schemas[key]
            self.assertEqual(document["$ref"], f"#/$defs/{model.__name__}")
            self.assertIn(model.__name__, document["$defs"])
            # 文件名契约：<key>.schema.json
            self.assertTrue((SCHEMA_DIR / f"{key}.schema.json").exists())

    def test_all_fixtures_conform_to_schema_structure(self) -> None:
        schemas = contract_schemas()
        fixtures: list[tuple[str, str, str]] = []
        for name in ("chat", "community"):
            fixtures.append(("design_spec", f"examples/contracts/{name}/design-spec.json", "spec_id"))
            fixtures.append(("task_plan", f"examples/contracts/{name}/task-plan.json", "plan_id"))
            fixtures.append(("validation_report", f"examples/contracts/{name}/validation-report.json", "target"))
        fixtures.append(("execution_plan", "examples/contracts/chat/execution-plan.json", "execution_plan_id"))
        fixtures.append(("workflow_template", "templates/default-workflow.json", "template_id"))
        for case in ("cycle", "missing-verification", "unresolved-artifact"):
            fixtures.append(("task_plan", f"examples/contracts/invalid/{case}/task-plan.json", "plan_id"))
            fixtures.append(
                ("validation_report", f"examples/contracts/invalid/{case}/validation-report.json", "target")
            )
        for schema_key, relative, root in fixtures:
            with self.subTest(fixture=relative):
                document = _load(relative)
                self.assertEqual(
                    check_structure(document, schemas[schema_key], object_id=root),
                    [],
                    f"{relative} 不符合 {schema_key} Schema",
                )


class DigestTest(unittest.TestCase):
    def test_canonical_digest_ignores_object_key_order(self) -> None:
        first = {"b": [1, 2], "a": "聊天"}
        second = {"a": "聊天", "b": [1, 2]}
        self.assertEqual(content_digest(first), content_digest(second))
        self.assertRegex(content_digest(first), r"^[0-9a-f]{64}$")

    def test_canonical_digest_preserves_array_order(self) -> None:
        self.assertNotEqual(content_digest({"a": [1, 2]}), content_digest({"a": [2, 1]}))

    def test_fixture_digests_are_consistent(self) -> None:
        chat_spec = _load("examples/contracts/chat/design-spec.json")
        chat_plan = _load("examples/contracts/chat/task-plan.json")
        template = _load("templates/default-workflow.json")
        report = _load("examples/contracts/chat/validation-report.json")
        execution_plan = _load("examples/contracts/chat/execution-plan.json")
        self.assertEqual(execution_plan["spec_digest"], content_digest(chat_spec))
        self.assertEqual(execution_plan["template_digest"], content_digest(template))
        self.assertEqual(execution_plan["config_digest"], content_digest(chat_plan["config"]))
        self.assertEqual(execution_plan["validation_ref"]["report_digest"], content_digest(report))
        self.assertEqual(report["content_digest"], content_digest(chat_plan))


class ModelRoundTripTest(unittest.TestCase):
    def test_design_spec_round_trip_keeps_digest(self) -> None:
        document = _load("examples/contracts/chat/design-spec.json")
        spec = from_dict(DesignSpec, document)
        self.assertEqual(spec.spec_id, "SPEC-CHAT")
        self.assertEqual(content_digest(to_jsonable(spec)), content_digest(document))

    def test_task_plan_round_trip_keeps_digest(self) -> None:
        document = _load("examples/contracts/chat/task-plan.json")
        plan = from_dict(TaskPlan, document)
        self.assertEqual(plan.plan_id, "PLAN-CHAT")
        self.assertEqual(content_digest(to_jsonable(plan)), content_digest(document))

    def test_execution_plan_round_trip_keeps_digest(self) -> None:
        document = _load("examples/contracts/chat/execution-plan.json")
        execution_plan = from_dict(ExecutionPlan, document)
        self.assertEqual(execution_plan.execution_plan_id, "EXEC-CHAT")
        self.assertEqual(content_digest(to_jsonable(execution_plan)), content_digest(document))

    def test_report_round_trip_keeps_digest(self) -> None:
        document = _load("examples/contracts/chat/validation-report.json")
        report = from_dict(ValidationReport, document)
        self.assertEqual(report.target.plan_id, "PLAN-CHAT")
        self.assertEqual(content_digest(to_jsonable(report)), content_digest(document))


class FixtureFreshnessTest(unittest.TestCase):
    def test_derived_fixtures_are_up_to_date(self) -> None:
        from backend.tools.build_fixtures import build_documents, check_all

        outputs = build_documents()
        self.assertGreaterEqual(len(outputs), 9)
        self.assertEqual(check_all(outputs), [])

    def test_committed_invalid_reports_match_validation(self) -> None:
        spec = _load("examples/contracts/chat/design-spec.json")
        template = _load("templates/default-workflow.json")
        for case in ("cycle", "missing-verification", "unresolved-artifact"):
            with self.subTest(case=case):
                plan = _load(f"examples/contracts/invalid/{case}/task-plan.json")
                expected = _load(f"examples/contracts/invalid/{case}/validation-report.json")
                report = validate_task_plan(plan, spec=spec, template=template)
                self.assertTrue(report.blocked)
                self.assertEqual(to_jsonable(report), expected)


class LegacyNormalizationTest(unittest.TestCase):
    def test_legacy_chat_source_becomes_requirement_ids(self) -> None:
        legacy_spec = _load("examples/chat.json")
        legacy_plan = _load("examples/chat-plan.json")
        spec_result, plan_result = normalize_legacy_examples(
            legacy_spec, legacy_plan, spec_id="SPEC-CHAT-LEGACY", plan_id="PLAN-CHAT-LEGACY"
        )
        plan = plan_result.document
        tasks = {task["id"]: task for task in plan["tasks"]}
        self.assertEqual(tasks["C8"]["requirement_ids"], ["R1", "R2"])
        self.assertEqual(tasks["C2"]["requirement_ids"], ["R1"])
        for task in plan["tasks"]:
            for dependency in task["depends_on"]:
                self.assertIsInstance(dependency, dict)
                self.assertIn("task_id", dependency)
                self.assertIn("reason", dependency)
        self.assertIn("C5", [dep["task_id"] for dep in tasks["C7"]["depends_on"]])

    def test_legacy_kind_inference_and_gaps(self) -> None:
        legacy_plan = _load("examples/chat-plan.json")
        result = normalize_legacy_plan(legacy_plan)
        tasks = {task["id"]: task for task in result.document["tasks"]}
        self.assertEqual(tasks["C1"]["kind"], "implementation")
        self.assertEqual(tasks["C6"]["kind"], "verification")
        self.assertEqual(tasks["C8"]["kind"], "integration")
        codes_found = {gap.code for gap in result.gaps}
        self.assertIn(codes.LEGACY_KIND_INFERRED, codes_found)
        self.assertIn(codes.LEGACY_INPUTS_MISSING, codes_found)
        self.assertIn(codes.LEGACY_ESTIMATE_PLACEHOLDER, codes_found)
        self.assertGreater(result.gap_count, 0)

    def test_normalized_legacy_plan_validates_with_traceability_warnings(self) -> None:
        legacy_spec = _load("examples/chat.json")
        legacy_plan = _load("examples/chat-plan.json")
        template = _load("templates/default-workflow.json")
        spec_result, plan_result = normalize_legacy_examples(legacy_spec, legacy_plan)
        report = validate_task_plan(
            plan_result.document, spec=spec_result.document, template=template
        )
        self.assertFalse(report.blocked)
        self.assertEqual(report.summary.errors, 0)
        self.assertGreater(report.summary.warnings, 0)
        self.assertIn(codes.TASK_DESIGN_REF_EMPTY, {issue.code for issue in report.issues})
        self.assertEqual(report.coverage.implementation_ratio, 1.0)
        self.assertEqual(report.coverage.verification_ratio, 1.0)

    def test_normalized_legacy_spec_reports_missing_design_gaps(self) -> None:
        legacy_spec = _load("examples/community.json")
        spec_result = normalize_legacy_spec(legacy_spec, spec_id="SPEC-COMMUNITY-LEGACY")
        report = validate_design_spec(spec_result.document)
        self.assertTrue(report.blocked)
        found = set(_code_paths(report.issues))
        self.assertIn((codes.FIELD_EMPTY, "modules"), found)
        self.assertIn((codes.FIELD_EMPTY, "design_items"), found)
        self.assertIn((codes.SPEC_CONSTRAINT_UNSPECIFIED, "constraints[0].status"), found)
        gap_codes = {gap.code for gap in spec_result.gaps}
        self.assertIn(codes.LEGACY_MODULES_MISSING, gap_codes)
        self.assertIn(codes.LEGACY_DESIGN_ITEMS_MISSING, gap_codes)

    def test_legacy_files_are_not_modified(self) -> None:
        """迁移只读旧案例；旧文件保持简化格式（没有 schema_version）。"""
        for name in ("chat", "chat-plan", "community", "community-plan"):
            legacy = _load(f"examples/{name}.json")
            self.assertNotIn("schema_version", legacy)


if __name__ == "__main__":
    unittest.main()
