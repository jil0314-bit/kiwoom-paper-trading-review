"""Same-day sell rows must not stop at the inherited 20-row cap."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from tests.test_state_invariants import engine, engine_probe


def _inherited_sell_rows():
    for cls in engine.V7600ThreeStageEngine.__mro__[1:]:
        function = cls.__dict__.get("_today_sell_rows")
        if function is not None:
            return function
    raise AssertionError("inherited _today_sell_rows missing")


def _ledger_checker():
    for cls in engine.V7600ThreeStageEngine.__mro__:
        function = cls.__dict__.get("_gui_snapshot")
        globals_ = getattr(function, "__globals__", {})
        if "today_sell_ledger" in globals_:
            return globals_["today_sell_ledger"], globals_["v753"]
    raise AssertionError("today_sell_ledger missing")


class TodaySellRowTests(unittest.TestCase):
    def test_dashboard_keeps_every_same_day_sell(self):
        inherited = _inherited_sell_rows()
        v753 = inherited.__globals__["v753"]
        ledger_count, ledger_v753 = _ledger_checker()
        self.assertIs(ledger_v753, v753)
        kst = inherited.__globals__["KST"]
        now = datetime.now(kst).replace(microsecond=0)
        start = now.replace(hour=9, minute=0, second=0)
        original = v753.FILL_FILE
        probe = engine_probe()
        try:
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "paper_fills.csv"
                with path.open("w", encoding="utf-8-sig", newline="") as handle:
                    writer = csv.DictWriter(
                        handle,
                        fieldnames=["side", "timestamp", "symbol", "price", "pnl", "reason"],
                    )
                    writer.writeheader()
                    writer.writerow({
                        "side": "BUY",
                        "timestamp": start.isoformat(),
                        "symbol": "005930",
                        "price": "100",
                        "pnl": "0",
                        "reason": "매수",
                    })
                    writer.writerow({
                        "side": "SELL",
                        "timestamp": (start - timedelta(days=1)).isoformat(),
                        "symbol": "000000",
                        "price": "100",
                        "pnl": "-1",
                        "reason": "어제",
                    })
                    for index in range(25):
                        writer.writerow({
                            "side": "SELL",
                            "timestamp": (start + timedelta(minutes=index)).isoformat(),
                            "symbol": f"{index + 1:06d}",
                            "price": str(1000 + index),
                            "pnl": str(index - 10),
                            "reason": f"매도{index + 1}",
                        })
                v753.FILL_FILE = path
                capped = inherited(probe)
                full = probe._today_sell_rows()
                pnl, count, readable = ledger_count(path, now=now)
        finally:
            v753.FILL_FILE = original

        self.assertEqual([row["symbol"] for row in capped], [f"{index:06d}" for index in range(6, 26)])
        self.assertEqual([row["symbol"] for row in full], [f"{index:06d}" for index in range(1, 26)])
        self.assertEqual(full[0]["time"], "09:00:00")
        self.assertEqual(full[-1]["time"], "09:24:00")
        self.assertTrue(readable)
        self.assertEqual(count, 25)
        self.assertEqual(count, len(full))
        self.assertAlmostEqual(pnl, sum(index - 10 for index in range(25)))


if __name__ == "__main__":
    unittest.main()
