"""V7626 시험: 장 마감 1분봉 저장(한 번에 한 종목 조회, 다 받으면 gz 한 파일). 주문 없음."""
from __future__ import annotations
import gzip, json, sys, tempfile, types, unittest, subprocess
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

_s = types.ModuleType("msvcrt"); _s.LK_NBLCK = 2; _s.LK_LOCK = 1; _s.LK_UNLCK = 0; _s.LK_NBRLCK = 4; _s.LK_RLCK = 3
_s.locking = lambda *a, **k: None; sys.modules.setdefault("msvcrt", _s)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_v7613_engine as base
engine, KST, V753 = base.engine, base.KST, base.V753


class SaveTests(unittest.TestCase):
    def test_saves_once_after_1521(self):
        folder = Path(tempfile.mkdtemp())
        item = base.probe(folder)
        S = engine._V7600_STATE
        events = [SimpleNamespace(symbol="111111", new_stage=S.PREBUY), SimpleNamespace(symbol="222222", new_stage=S.HOLDING),
                  SimpleNamespace(symbol="333333", new_stage="관찰")]
        item._v7609_state_manager_obj = lambda: SimpleNamespace(events=tuple(events), stage_of=lambda s: None)
        now = datetime(2026, 11, 16, 15, 22, tzinfo=KST)          # 월요일(주말에는 저장하지 않는다)
        calls = []
        def bars(symbol):
            calls.append(symbol)
            if symbol == "222222":
                raise OSError("x")
            day = now.replace(hour=9, minute=0)
            return [V753.Bar(symbol, day + timedelta(minutes=k), 100, 110, 90, 105, 10) for k in range(3)] + \
                   [V753.Bar(symbol, day - timedelta(days=1), 1, 1, 1, 1, 1)]
        item.client = SimpleNamespace(get_experiment_bars=bars, v7580_raw_cache={})
        item.names = {"111111": "가나다"}
        with mock.patch.object(engine, "V7626_BAR_DIR", folder / "mb"), mock.patch.object(engine.time, "monotonic", side_effect=iter(range(0, 1000, 5))):
            item._v7626_save_minute_bars_step(now.replace(hour=15, minute=20))
            self.assertEqual(calls, [])                                   # 15:21 전에는 아무것도 안 함
            for _ in range(6):
                item._v7626_save_minute_bars_step(now)
            path = folder / "mb" / f"{now:%Y%m%d}.json.gz"
            data = json.load(gzip.open(path, "rt", encoding="utf-8"))
        self.assertEqual(calls, ["111111", "222222"])                     # 관찰만 한 종목은 제외, 종목당 1번
        self.assertEqual(list(data["bars"]), ["111111"])
        self.assertEqual(data["bars"]["111111"][0], ["09:00", 100.0, 110.0, 90.0, 105.0, 10])
        self.assertEqual(len(data["bars"]["111111"]), 3)                  # 전날 봉 제외


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], verbosity=1)
