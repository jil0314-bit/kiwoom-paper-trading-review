from __future__ import annotations

import ast
import json
from pathlib import Path
import unittest

from tests.test_replay_003 import conditional_replay, sha256_file


ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = ROOT / "tests" / "replay_data"
REGISTRY_PATH = DATA_ROOT / "replay_case_001_registry.json"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def cases_by_id() -> dict[str, dict]:
    payload = read_json(REGISTRY_PATH)
    return {case["case_id"]: case for case in payload["cases"]}


def test_methods(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    }


class ReplayCase001Registry(unittest.TestCase):
    def test_01_registry_has_only_exactly_catalogued_cases(self) -> None:
        payload = read_json(REGISTRY_PATH)
        self.assertEqual(payload["schema"], "REPLAY-CASE-001/v1")
        self.assertEqual(payload["summary"]["catalogued_cases"], 5)
        self.assertEqual(payload["summary"]["fully_replayable_cases"], 0)
        self.assertEqual(payload["summary"]["conditionally_replayable_cases"], 1)
        self.assertEqual(payload["summary"]["replay_unavailable_cases"], 4)
        cases = cases_by_id()
        self.assertEqual(set(cases), {"STR-01", "STR-02", "STR-03", "STR-04", "STR-05"})
        self.assertEqual(cases["STR-01"]["symbol"], "035720")
        self.assertEqual(cases["STR-01"]["trade_date"], "20260807")
        self.assertEqual(cases["STR-01"]["target_time"], "2026-08-07T10:03:00+09:00")

    def test_02_only_str01_has_official_one_minute_material(self) -> None:
        cases = cases_by_id()
        replayable = [
            case_id
            for case_id, case in cases.items()
            if case["classification"] == "conditionally_replayable"
        ]
        self.assertEqual(replayable, ["STR-01"])
        self.assertEqual(
            cases["STR-01"]["official_inputs"]["target_completed_1m"]["status"],
            "acquired",
        )
        for case_id in ("STR-02", "STR-03", "STR-04", "STR-05"):
            self.assertEqual(cases[case_id]["classification"], "replay_unavailable")
            self.assertEqual(
                cases[case_id]["official_inputs"]["target_completed_1m"]["status"],
                "missing",
            )

    def test_03_incomplete_cases_are_not_replayed_or_invented(self) -> None:
        cases = cases_by_id()
        for case_id in ("STR-02", "STR-03", "STR-04", "STR-05"):
            case = cases[case_id]
            self.assertTrue(case["missing"])
            self.assertNotIn("actual_result", case)
            self.assertNotEqual(case["classification"], "conditionally_replayable")
        self.assertIsNone(cases["STR-02"]["symbol"])
        self.assertIsNone(cases["STR-02"]["trade_date"])
        self.assertIsNone(cases["STR-04"]["symbol"])
        self.assertIsNone(cases["STR-05"]["target_time"])

    def test_04_str01_interest_is_blocked_on_pure_one_minute_path(self) -> None:
        case = cases_by_id()["STR-01"]
        result = conditional_replay()
        self.assertEqual(case["allowed_assertions"], ["interest_admission_BLOCKED"])
        self.assertEqual(case["expected"]["interest_admission"], "BLOCKED")
        self.assertEqual(result["automatic_interest_transition"], "BLOCKED")
        self.assertFalse(result["active_pullback"].allowed)
        self.assertEqual(
            result["active_pullback"].reason,
            "ACTIVE_PULLBACK_LONG_BEAR_SUPPORT_BREAK",
        )

    def test_05_str01_other_historical_stages_remain_indeterminate(self) -> None:
        case = cases_by_id()["STR-01"]
        result = conditional_replay()
        self.assertEqual(
            result["official_historical_inputs_missing"],
            ["candidate_admission", "quote_timeline_035720"],
        )
        self.assertEqual(result["market_hold_stage"], "INDETERMINATE")
        for key in (
            "observation_admission",
            "market_hold",
            "prebuy_admission",
            "paper_holding",
            "paper_sold",
        ):
            self.assertEqual(case["expected"][key], "INDETERMINATE")
            self.assertIn(key, case["forbidden_assertions"])

    def test_06_requested_structural_checks_link_to_existing_path_tests(self) -> None:
        payload = read_json(REGISTRY_PATH)
        module_paths = {
            "tests.test_state_manager": ROOT / "tests" / "test_state_manager.py",
            "tests.test_state_invariants": ROOT / "tests" / "test_state_invariants.py",
            "tests.test_order_idempotency": ROOT / "tests" / "test_order_idempotency.py",
            "tests.test_evidence_integrity": ROOT / "tests" / "test_evidence_integrity.py",
        }
        expected_keys = {
            "stage_skip_block",
            "unheld_sell_block",
            "restart_same_result",
            "duplicate_signal_block",
            "duplicate_fill_block",
            "evidence_completeness",
        }
        self.assertEqual(set(payload["structural_coverage"]), expected_keys)
        for coverage in payload["structural_coverage"].values():
            path = module_paths[coverage["suite"]]
            self.assertIn(coverage["test"], test_methods(path))

    def test_07_official_input_hashes_match_registry(self) -> None:
        case = cases_by_id()["STR-01"]
        for detail in case["official_inputs"].values():
            if detail.get("status") != "acquired":
                continue
            path = DATA_ROOT / detail["path"]
            self.assertTrue(path.is_file(), path)
            self.assertEqual(sha256_file(path), detail["sha256"], path)

    def test_08_registry_and_test_have_no_forbidden_side_effect_path(self) -> None:
        payload = read_json(REGISTRY_PATH)
        self.assertEqual(
            payload["policy"]["forbidden_answer_inputs"],
            ["화면 캡처", "3분봉", "합성 데이터", "과거 엔진 판정 결과"],
        )
        self.assertFalse(payload["policy"]["missing_values_may_be_invented"])
        self.assertFalse(payload["policy"]["strategy_or_expectation_changes_allowed"])
        self.assertFalse(payload["policy"]["runtime_or_order_api_access_allowed"])

        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
        imported = set()
        called_names = set()
        called_attributes = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".", 1)[0])
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                called_names.add(node.func.id)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                called_attributes.add(node.func.attr)
        self.assertTrue(imported.isdisjoint({"requests", "urllib", "http", "socket"}))
        self.assertFalse(any(name.startswith("fn_kt") for name in called_names))
        self.assertTrue(
            called_attributes.isdisjoint(
                {"write_text", "write_bytes", "mkdir", "rename", "unlink"}
            )
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
