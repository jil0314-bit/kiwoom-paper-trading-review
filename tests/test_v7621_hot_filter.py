"""V7621 시험: 최근 3거래일 +50% 이상 급등 종목 자동 신규매수 제외. 주문·네트워크 없음."""
from __future__ import annotations
import sys, tempfile, types, unittest, subprocess
from datetime import datetime, timedelta, date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

_s = types.ModuleType("msvcrt"); _s.LK_NBLCK = 2; _s.LK_LOCK = 1; _s.LK_UNLCK = 0; _s.LK_NBRLCK = 4; _s.LK_RLCK = 3
_s.locking = lambda *a, **k: None; sys.modules.setdefault("msvcrt", _s)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_v7613_engine as base
engine, KST = base.engine, base.KST


def daily(closes, end=None):
    end = end or datetime.now(KST).date()
    out, d = [], end
    for close in reversed(closes):
        d -= timedelta(days=1)
        out.append(SimpleNamespace(symbol="181710", trading_date=d, open=close, high=close, low=close, close=close, volume=1000))
    today = SimpleNamespace(symbol="181710", trading_date=end, open=1, high=1, low=1, close=999999.0, volume=1)
    return sorted(out, key=lambda b: b.trading_date) + [today]     # 오늘 봉은 계산에서 빠져야 함


class HotFilterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def item(self, closes, fail=False):
        item = base.probe(self.folder)
        calls = []
        def get(symbol, limit):
            calls.append(symbol)
            if fail:
                raise OSError("down")
            return daily(closes)
        item.client = SimpleNamespace(get_daily_bars=get, v7580_raw_cache={})
        return item, calls

    def test_nhn_case_blocked(self):
        item, calls = self.item([48050, 51100, 57800, 60800, 77400])     # 11/05 51,100 → 11/10 77,400
        reason = item._v7621_hot_reason("181710")
        self.assertEqual(reason, "최근3일 +51% 급등 제외")
        item._v7621_hot_reason("181710")
        self.assertEqual(len(calls), 1)                                   # 하루 한 번만 조회

    def test_below_threshold_allowed(self):
        item, _ = self.item([10000, 10000, 12000, 13000, 14900])          # +49%
        self.assertEqual(item._v7621_hot_reason("181710"), "")

    def test_fetch_failure_does_not_block(self):
        item, calls = self.item([1, 1, 1, 1, 1], fail=True)
        self.assertEqual(item._v7621_hot_reason("181710"), "")
        item._v7621_hot_reason("181710")
        self.assertEqual(len(calls), 1)                                   # 60초 안에는 재조회 안 함

    def test_row_label(self):
        item, _ = self.item([48050, 51100, 57800, 60800, 77400])
        item._v7621_hot_reason("181710")
        rows = [{"symbol": "181710", "stage": "관찰후보", "reason": "1차상승"}]
        item._v7613_decorate_rows(rows, datetime.now(KST))
        self.assertTrue(rows[0]["reason"].startswith("[최근3일 +51% 급등 제외] "), rows[0]["reason"])


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], verbosity=1)
