from __future__ import annotations

import ast
from datetime import datetime
import hashlib
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CASE_DIR = ROOT / "tests" / "replay_data" / "STR-01"
MANIFEST_PATH = CASE_DIR / "manifest.json"
BARS_PATH = CASE_DIR / "completed_1m_chronological.json"
CONTEXT_MANIFEST_PATH = CASE_DIR / "context" / "manifest.json"
ENGINE_PATH = ROOT / "SOURCE" / "core" / "bot_v76_00_three_stage_ctrlv_paper.py"

EXPECTED_BAR_SHA256 = "A49F4B4AB8FD8689A42C83E859C3DE2C367318E8EB5F4C2E5078DAD31C6B8C09"
EXPECTED_BAR_COUNT = 382
EXPECTED_AT = datetime.fromisoformat("2026-08-07T10:03:00+09:00")

# These inputs are read by the current executable path before it can reproduce
# the exact 10:03 stage.  REPLAY-002 must never silently replace any of them
# with screenshots, synthetic values, or an old engine decision.
REQUIRED_EXTERNAL_INPUTS = {
    "previous_session_1m": "전일 동시간 누적거래량 비율",
    "daily_volume_refs": "전일 전체·최근 10일 거래량 관찰 편입 자료",
    "candidate_admission": "카카오가 실제 감시 대상이 된 출처와 시각",
    "market_proxy_069500": "KODEX200 당시 시세·완성 1분봉",
    "market_proxy_229200": "KODEX코스닥150 당시 시세·완성 1분봉",
    "quote_timeline_035720": "실제 엔진 호출 시각별 카카오 시세",
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def load_case() -> tuple[dict, list[dict]]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    bars = json.loads(BARS_PATH.read_text(encoding="utf-8"))
    return manifest, bars


def available_external_inputs() -> set[str]:
    """Return only acquired inputs whose recorded file hash still matches."""

    if not CONTEXT_MANIFEST_PATH.is_file():
        return set()
    manifest = json.loads(CONTEXT_MANIFEST_PATH.read_text(encoding="utf-8"))
    statuses = manifest.get("status_by_required_input", {})
    available: set[str] = set()
    for input_id in REQUIRED_EXTERNAL_INPUTS:
        detail = statuses.get(input_id, {})
        if detail.get("status") != "acquired":
            continue
        path_text = str(detail.get("path", "")).strip()
        expected_hash = str(detail.get("sha256", "")).strip().upper()
        path = CONTEXT_MANIFEST_PATH.parent / path_text
        if path.is_file() and expected_hash and sha256_file(path) == expected_hash:
            available.add(input_id)
    return available


class Replay002InputAudit(unittest.TestCase):
    def test_str01_target_fixture_is_exact(self) -> None:
        manifest, bars = load_case()
        self.assertEqual(manifest["case_id"], "STR-01")
        self.assertEqual(manifest["symbol"], "035720")
        self.assertEqual(manifest["trade_date"], "20260807")
        self.assertEqual(len(bars), EXPECTED_BAR_COUNT)
        self.assertEqual(sha256_file(BARS_PATH), EXPECTED_BAR_SHA256)
        self.assertEqual(manifest["integrity"]["completed_1m_count"], EXPECTED_BAR_COUNT)
        self.assertEqual(
            manifest["integrity"]["completed_1m_sha256"], EXPECTED_BAR_SHA256
        )

    def test_target_fixture_contains_only_same_day_completed_bars(self) -> None:
        _manifest, bars = load_case()
        stamps = [datetime.strptime(row["timestamp"], "%Y%m%d%H%M%S") for row in bars]
        self.assertEqual(stamps, sorted(stamps))
        self.assertEqual({stamp.date().isoformat() for stamp in stamps}, {"2026-08-07"})
        self.assertIn(EXPECTED_AT.replace(tzinfo=None), stamps)

    def test_actual_execution_path_declares_the_missing_inputs(self) -> None:
        source = ENGINE_PATH.read_text(encoding="utf-8")
        required_tokens = (
            "self._v7609_refresh_same_time_ratio(symbol, raw, now)",
            "self._v7609_admit_observation_from_one_minute(symbol, raw, now)",
            "evidence = _V7594_EVALUATE_OBSERVATION(raw, now=check_now)",
            "same_time_ratio + 1e-12 < 2.0",
            "for code in self.v757_config.market_proxy_codes",
            "self.client.get_minute_bars(code, limit=12, completed_only=True)",
        )
        for token in required_tokens:
            self.assertIn(token, source)
        # The historical fixture still includes daily refs, but the accepted
        # September observation rule no longer uses that old volume gate.
        tree = ast.parse(source)
        admission = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                         and n.name == '_v7609_admit_observation_from_one_minute')
        self.assertNotIn('_daily_volume_refs', ast.unparse(admission))

    def test_replay_is_fail_closed_as_input_insufficient(self) -> None:
        available = available_external_inputs()
        missing = sorted(set(REQUIRED_EXTERNAL_INPUTS) - available)
        self.assertEqual(
            available,
            {
                "previous_session_1m",
                "daily_volume_refs",
                "market_proxy_069500",
                "market_proxy_229200",
            },
        )
        self.assertEqual(missing, ["candidate_admission", "quote_timeline_035720"])
        context = json.loads(CONTEXT_MANIFEST_PATH.read_text(encoding="utf-8"))
        for input_id in missing:
            self.assertEqual(
                context["status_by_required_input"][input_id]["status"],
                "official_historical_data_unavailable",
            )
            self.assertFalse(
                context["status_by_required_input"][input_id][
                    "synthetic_or_engine_result_used"
                ]
            )
        manifest, _bars = load_case()
        self.assertEqual(manifest["expected"]["interest_admission"], "forbidden")
        self.assertEqual(manifest["expected"]["stage"], "시장보류")
        # A zero same-time ratio caused only by absent prior-day bars would
        # block interest accidentally.  It cannot prove the expected market
        # hold, so the engine replay must not run in REPLAY-002.
        print(
            "[REPLAY-002] REPLAY_INPUT_INSUFFICIENT | "
            + ", ".join(
                f"{input_id}={REQUIRED_EXTERNAL_INPUTS[input_id]}" for input_id in missing
            )
        )

    def test_test_module_has_no_order_or_network_execution(self) -> None:
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
        self.assertNotIn("requests", imported)
        self.assertNotIn("urllib", imported)
        self.assertFalse(any(name.startswith("fn_kt") for name in called_names))
        self.assertNotIn("_attempt_entry", called_names)
        self.assertTrue(
            called_attributes.isdisjoint(
                {"write_text", "write_bytes", "mkdir", "rename", "unlink"}
            )
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
