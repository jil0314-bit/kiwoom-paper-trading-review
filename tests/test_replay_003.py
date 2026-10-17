from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "SOURCE" / "core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from v7600_three_stage_logic import (  # noqa: E402
    active_interest_pullback_decision,
    persistent_market_decline_decision,
    prominent_bear_interest_decision,
    three_bear_rising_volume_decision,
)


CASE_DIR = ROOT / "tests" / "replay_data" / "STR-01"
CONTEXT_DIR = CASE_DIR / "context"
MAIN_BARS_PATH = CASE_DIR / "completed_1m_chronological.json"
PREVIOUS_BARS_PATH = CONTEXT_DIR / "previous_session_1m.json"
DAILY_REFS_PATH = CONTEXT_DIR / "daily_volume_refs.json"
KODEX200_PATH = CONTEXT_DIR / "market_proxy_069500.json"
KODEX150_PATH = CONTEXT_DIR / "market_proxy_229200.json"
CONTEXT_MANIFEST_PATH = CONTEXT_DIR / "manifest.json"
ENGINE_PATH = CORE / "bot_v76_00_three_stage_ctrlv_paper.py"

KST = timezone(timedelta(hours=9))
TARGET_STAMP = datetime(2026, 8, 7, 10, 3, tzinfo=KST)
TARGET_DECISION_STAMP = TARGET_STAMP + timedelta(minutes=1)
TARGET_TEXT = "20260807100300"
PREVIOUS_TARGET_TEXT = "20260806100300"

EXPECTED_HASHES = {
    MAIN_BARS_PATH: "A49F4B4AB8FD8689A42C83E859C3DE2C367318E8EB5F4C2E5078DAD31C6B8C09",
    PREVIOUS_BARS_PATH: "11F4FCF7961727F310FDCDDC5E0B59AB39B38FA803835C192453D3E5826F821A",
    DAILY_REFS_PATH: "3A48497116CBC0990223F48D2D63D0BCCCCCADB2546DCF3CFE97137CF77322EB",
    KODEX200_PATH: "A3A9D2FCD38D8160228F643E89232AFF8B5A57E3A8C7013520C0FEF714B5CAE6",
    KODEX150_PATH: "4094A7037C5F313CD16EC7124AF62528379CBB7658C6F6CE4FAF26BF871152EB",
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def bar(row: dict) -> SimpleNamespace:
    return SimpleNamespace(
        timestamp=datetime.strptime(row["timestamp"], "%Y%m%d%H%M%S").replace(
            tzinfo=KST
        ),
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=float(row["volume"]),
        acc_volume=int(row.get("acc_volume", 0) or 0),
    )


def rows_through(rows: list[dict], timestamp: str) -> list[dict]:
    return [row for row in rows if str(row["timestamp"]) <= timestamp]


def bars_through(rows: list[dict], timestamp: str) -> list[SimpleNamespace]:
    return [bar(row) for row in rows_through(rows, timestamp)]


def completed_rows(path: Path) -> list[dict]:
    payload = read_json(path)
    if isinstance(payload, list):
        return payload
    return list(payload["completed_1m"])


def class_method_source(path: Path, class_name: str, method_name: str) -> str:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == method_name:
                    return ast.get_source_segment(source, item) or ""
    raise AssertionError(f"method not found: {class_name}.{method_name}")


def conditional_replay() -> dict:
    main_rows = completed_rows(MAIN_BARS_PATH)
    previous_rows = completed_rows(PREVIOUS_BARS_PATH)
    stock_bars = bars_through(main_rows, TARGET_TEXT)
    kodex200_bars = bars_through(completed_rows(KODEX200_PATH), TARGET_TEXT)
    kodex150_bars = bars_through(completed_rows(KODEX150_PATH), TARGET_TEXT)

    active = active_interest_pullback_decision(stock_bars, now=TARGET_DECISION_STAMP)
    prominent = prominent_bear_interest_decision(stock_bars, now=TARGET_DECISION_STAMP)
    three_bear = three_bear_rising_volume_decision(stock_bars, now=TARGET_DECISION_STAMP)
    market_200 = persistent_market_decline_decision(kodex200_bars)
    market_150 = persistent_market_decline_decision(kodex150_bars)

    current_row = next(row for row in main_rows if row["timestamp"] == TARGET_TEXT)
    previous_row = next(
        row for row in previous_rows if row["timestamp"] == PREVIOUS_TARGET_TEXT
    )
    same_time_ratio = float(current_row["acc_volume"]) / float(
        previous_row["acc_volume"]
    )

    context = read_json(CONTEXT_MANIFEST_PATH)
    missing = sorted(
        input_id
        for input_id, detail in context["status_by_required_input"].items()
        if detail.get("status") == "official_historical_data_unavailable"
    )
    publish_source = class_method_source(
        ENGINE_PATH, "V7600ThreeStageEngine", "_v7588_publish"
    )
    refresh_source = class_method_source(
        ENGINE_PATH, "V7600ThreeStageEngine", "_refresh_actual_bars"
    )
    central_guard_present = (
        "active_pullback = active_interest_pullback_decision(raw, now=now)"
        in publish_source
        and "if not active_pullback.allowed:" in publish_source
        and "return" in publish_source
    )

    # The exact historical observation phase and invocation quote are unavailable.
    # This result therefore answers only whether the completed 10:03 candle could
    # authorize a new automatic INTEREST transition on the current 1m path.
    return {
        "target_bar": current_row,
        "same_time_volume_ratio": same_time_ratio,
        "active_pullback": active,
        "prominent_bear": prominent,
        "three_bear": three_bear,
        "central_guard_present": central_guard_present,
        "automatic_interest_transition": (
            "BLOCKED"
            if central_guard_present and not active.allowed
            else "NOT_PROVEN_BLOCKED"
        ),
        "market_proxy_069500": market_200,
        "market_proxy_229200": market_150,
        "market_hold_stage": "INDETERMINATE",
        "official_historical_inputs_missing": missing,
        "refresh_is_direct_one_minute": (
            "self._v7609_load_one_minute_bars(symbol)" in refresh_source
            and "super()._refresh_actual_bars" not in refresh_source
        ),
    }


class Replay003ConditionalInterest(unittest.TestCase):
    def test_01_official_inputs_are_exact_and_unchanged(self) -> None:
        for path, expected in EXPECTED_HASHES.items():
            self.assertTrue(path.is_file(), path)
            self.assertEqual(sha256_file(path), expected, path)
        context = read_json(CONTEXT_MANIFEST_PATH)
        self.assertEqual(context["case_id"], "STR-01")
        self.assertEqual(context["symbol"], "035720")
        self.assertEqual(context["trade_date"], "20260807")
        self.assertFalse(context["secrets_saved"])
        self.assertFalse(context["order_api_called"])

    def test_02_target_uses_only_completed_1003_bar(self) -> None:
        main_rows = completed_rows(MAIN_BARS_PATH)
        sliced = rows_through(main_rows, TARGET_TEXT)
        self.assertEqual(sliced[-1]["timestamp"], TARGET_TEXT)
        self.assertEqual(
            {key: sliced[-1][key] for key in ("open", "high", "low", "close", "volume")},
            {"open": 40100, "high": 40150, "low": 39850, "close": 39850, "volume": 33098},
        )
        self.assertNotIn("20260807100400", {row["timestamp"] for row in sliced})

    def test_03_actual_refresh_path_is_direct_one_minute(self) -> None:
        result = conditional_replay()
        self.assertTrue(result["refresh_is_direct_one_minute"])

    def test_04_prior_same_time_ratio_is_real_and_passes_two_times(self) -> None:
        result = conditional_replay()
        self.assertAlmostEqual(result["same_time_volume_ratio"], 2.7051132934657378)
        self.assertGreaterEqual(result["same_time_volume_ratio"], 2.0)

    def test_05_pure_one_minute_structure_blocks_interest(self) -> None:
        result = conditional_replay()
        self.assertFalse(result["active_pullback"].allowed)
        self.assertEqual(
            result["active_pullback"].reason,
            "ACTIVE_PULLBACK_LONG_BEAR_SUPPORT_BREAK",
        )
        self.assertFalse(result["prominent_bear"].allowed)
        self.assertFalse(result["three_bear"].allowed)
        self.assertTrue(result["central_guard_present"])
        self.assertEqual(result["automatic_interest_transition"], "BLOCKED")
        print(
            "[REPLAY-003] INTEREST_BLOCKED | "
            f"active={result['active_pullback'].reason} | "
            f"same_time_ratio={result['same_time_volume_ratio']:.3f}"
        )

    def test_06_market_hold_is_not_claimed_from_incomplete_inputs(self) -> None:
        result = conditional_replay()
        self.assertEqual(
            result["official_historical_inputs_missing"],
            ["candidate_admission", "quote_timeline_035720"],
        )
        self.assertEqual(result["market_hold_stage"], "INDETERMINATE")
        print(
            "[REPLAY-003] MARKET_HOLD_INDETERMINATE | "
            f"069500={result['market_proxy_069500'].reason} | "
            f"229200={result['market_proxy_229200'].reason} | "
            "missing=candidate_admission,quote_timeline_035720"
        )

    def test_07_test_has_no_network_order_or_write_calls(self) -> None:
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
