"""V7615 WAVE(1차상승) 표시 전환 + 바닥경로 10시 기준 시험."""
import sys, types, unittest, subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
_s = types.ModuleType("msvcrt"); _s.LK_NBLCK = 2; _s.LK_LOCK = 1; _s.LK_UNLCK = 0; _s.LK_NBRLCK = 4; _s.LK_RLCK = 3
_s.locking = lambda *a, **k: None; sys.modules.setdefault("msvcrt", _s)
CORE = Path(__file__).resolve().parents[1] / "SOURCE" / "core"
if not CORE.exists():
    CORE = Path("/home/claude/t13/ROOT/SOURCE/core")
sys.path.insert(0, str(CORE))
import bot_v76_00_three_stage_ctrlv_paper as engine
import v7609_prebuy_rules as R
KST = timezone(timedelta(hours=9))


class WaveLabelTests(unittest.TestCase):
    def probe(self, route):
        P = engine._V7600_STATE.PREBUY
        events = [SimpleNamespace(symbol="111111", new_stage=P, evidence={"route": route, "buy": route != "FLOOR"})]
        manager = SimpleNamespace(events=tuple(events), stage_of=lambda s: P if s == "111111" else None)
        item = object.__new__(engine.V7600ThreeStageEngine)
        item._v7609_state_manager_obj = lambda: manager
        return item

    def test_wave_row_shown_as_first_rise(self):
        rows = [{"symbol": "111111", "stage": "매수가능성", "reason": "4차상승"}]
        self.probe("WAVE")._v7615_relabel_wave_rows(rows)
        self.assertEqual(rows[0]["stage"], "관찰후보")
        self.assertIn("1차상승·조정대기", rows[0]["reason"])

    def test_real_buy_routes_stay_prebuy(self):
        for route in ("BOTTOM_MA5", "BREAKOUT", "CARRY_A", "PULLBACK"):
            rows = [{"symbol": "111111", "stage": "매수가능성", "reason": "x"}]
            self.probe(route)._v7615_relabel_wave_rows(rows)
            self.assertEqual(rows[0]["stage"], "매수가능성", route)

    def test_v7627_stale_buy_signal_shown_as_passed(self):
        from datetime import datetime as _dt, timedelta as _td
        P_ = engine._V7600_STATE.PREBUY
        for age, expect in ((60, "매수가능성"), (600, "관찰후보")):
            t = (_dt.now(engine.KST) - _td(seconds=age)).isoformat()
            events = [SimpleNamespace(symbol="111111", new_stage=P_, data_time=t, evidence={"route": "BOTTOM_MA5", "buy": True})]
            item = object.__new__(engine.V7600ThreeStageEngine)
            item._v7609_state_manager_obj = lambda: SimpleNamespace(events=tuple(events), stage_of=lambda s: P_)
            rows = [{"symbol": "111111", "stage": "매수가능성", "reason": "x"}]
            item._v7615_relabel_wave_rows(rows)
            self.assertEqual(rows[0]["stage"], expect, age)
        self.assertIn("매수신호 지남", rows[0]["reason"])

    def test_v7626_floor_without_buy_shown_as_observation(self):
        rows = [{"symbol": "111111", "stage": "매수가능성", "reason": "거래바닥"}]
        self.probe("FLOOR")._v7615_relabel_wave_rows(rows)
        self.assertEqual(rows[0]["stage"], "관찰후보")
        self.assertTrue(rows[0]["reason"].startswith("[바닥형성·반전대기] "))

    def _bars(self, start, closes, vol=1000):
        out = []
        for k, c in enumerate(closes):
            t = start + timedelta(minutes=k)
            out.append(dict(timestamp=t.isoformat(), open=c, high=c + 5, low=c - 5, close=c, volume=vol))
        return out

    def test_first_wave_after_nine_counts(self):
        start = datetime(2026, 11, 9, 9, 30, tzinfo=KST)
        rows = self._bars(start, [10000] * 5 + [10100, 10250, 10400, 10520])
        self.assertTrue(R.waves(rows))
        self.assertTrue(R.WAVE_FIRST_ANYTIME)

    def test_prev_volume_rule_time(self):
        self.assertEqual(R.FLOOR_PREV_VOLUME_FROM.hour, 10)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], verbosity=1)
