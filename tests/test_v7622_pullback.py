"""V7622 시험: 상승 눌림목 경로(큰 음봉 없이 거래바닥 → 5분선 위 양봉). 주문·네트워크 없음."""
from __future__ import annotations
from unittest import mock
import sys, types, unittest, subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

_s = types.ModuleType("msvcrt"); _s.LK_NBLCK = 2; _s.LK_LOCK = 1; _s.LK_UNLCK = 0; _s.LK_NBRLCK = 4; _s.LK_RLCK = 3
_s.locking = lambda *a, **k: None; sys.modules.setdefault("msvcrt", _s)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "SOURCE" / "core"))
import v7609_prebuy_rules as P

KST = timezone(timedelta(hours=9))
# NHN 2026-11-11 실제 1분봉(08:00~08:20)을 09:00부터로 옮김. 전일 종가 77,400(시가와 같음 → 갭 아님)
NHN = """77400 81400 77400 80900 115342|81000 81800 80300 81100 70602|81100 81700 80600 80800 52239|80800 81200 79500 79600 38147
|79600 80300 79600 80200 16923|80200 81300 80100 81000 23985|81100 82500 80200 80200 64871|80200 80600 79400 80000 28974
|80000 80100 79700 79700 10472|79800 80400 79500 80200 13549|80200 80200 79700 79800 5243|79700 79900 79600 79800 4628
|79800 81000 79800 80700 13821|80800 81400 80700 81200 20087|81300 82100 81100 81600 38691"""


def rows(text=NHN, start=datetime(2026, 11, 11, 10, 0, tzinfo=KST)):   # V7630: 09:20 전 신호는 포기 규칙 → 10시로 옮김
    out = []
    for k, part in enumerate(p for p in text.replace("\n", "").split("|") if p.strip()):
        o, h, l, c, v = part.split()
        out.append(dict(timestamp=(start + timedelta(minutes=k)).isoformat(), open=float(o), high=float(h),
                        low=float(l), close=float(c), volume=int(v)))
    return out


class PullbackTests(unittest.TestCase):
    def test_nhn_signal_on_reclaim_bar(self):
        index, _bars = P._pullback_index(rows(), 77400.0)
        self.assertEqual(index, 12)                                   # 09:12(원래 08:12) 80,700 양봉
        r = P._pullback(rows()[:13], 77400.0)
        self.assertEqual((r["route"], r["buy"]), ("PULLBACK", True))
        self.assertIsNone(P._pullback(rows()[:14], 77400.0))           # 지난 신호는 다시 안 씀

    def test_evaluate_and_evidence(self):
        data = rows()[:13]
        now = datetime.fromisoformat(data[-1]["timestamp"]) + timedelta(minutes=1, seconds=2)
        r = P.evaluate(data, now=now, previous_day="2026-11-10", previous_volume=1329068.0, previous_close=77400.0)
        self.assertEqual(r["route"], "PULLBACK")
        self.assertTrue(P.validate_evidence(r, r["data_time"], now.isoformat()))
        # 전일 종가가 없으면 이 경로는 쉬고, 기존 판정은 그대로
        r2 = P.evaluate(data, now=now, previous_day="2026-11-10", previous_volume=1329068.0)
        self.assertTrue(r2 is None or r2["route"] != "PULLBACK")

    def test_v7631_bearish_bar_above_ma5_after_bottom_signal_buys(self):
        data = rows()
        index, bars, kind = P._pullback_index(data, 77400.0, with_kind=True)
        self.assertIsNotNone(index)
        self.assertTrue(kind)                                            # 바닥 신호 이름이 남는다
        # 신호 봉을 '5분선 위 음봉'으로 바꾸면: 예전 규칙은 거르고, 새 규칙은 그 봉에서 매수
        b = data[index]
        changed = [dict(r) for r in data]
        changed[index].update(open=b['close'] + 10, high=max(b['high'], b['close'] + 20))
        self.assertEqual(P._pullback_index(changed, 77400.0)[0], index)
        with mock.patch.object(P, "PULLBACK_ANY_SIGNAL", False):
            self.assertNotEqual(P._pullback_index(changed, 77400.0)[0], index)
        r = P._pullback(data[:index + 1], 77400.0)
        self.assertIn("바닥신호(", r["reason"])

    def test_v7630_first_signal_before_0920_abandons_day(self):
        early = rows(start=datetime(2026, 11, 11, 9, 0, tzinfo=KST))          # 같은 모양이 09:12에 나오면
        self.assertIsNone(P._pullback_index(early, 77400.0)[0])
        at_0920 = rows(start=datetime(2026, 11, 11, 9, 8, tzinfo=KST))        # 신호봉이 09:20이면 허용
        self.assertEqual(P._pullback_index(at_0920, 77400.0)[0], 12)

    def test_too_deep_or_below_open_cancels_day(self):
        deep = rows().copy()
        deep[7] = dict(deep[7], low=75000.0)                           # 고점 대비 9% → 그날 경로 종료
        self.assertIsNone(P._pullback_index(deep, 77400.0)[0])

    def test_shallow_pullback_not_enough(self):
        flat = [dict(r) for r in rows()]
        for r in flat[7:]:
            r["low"] = max(r["low"], 80500.0)                          # 2.4% 눌림 → 3% 미만
        self.assertIsNone(P._pullback_index(flat, 77400.0)[0])

    def test_v7627_below_prev_close_not_a_rising_pullback(self):
        # 같은 봉이라도 전일 종가가 90,000(갭하락 뒤 되돌림)이면 신호 없음
        self.assertIsNone(P._pullback_index(rows(), 90000.0)[0])
        self.assertEqual(P._pullback_index(rows(), 80700.0)[0], 12)     # 종가 = 전일 종가는 허용

    def test_after_13_no_signal(self):
        late = rows(start=datetime(2026, 11, 11, 12, 50, tzinfo=KST))
        self.assertIsNone(P._pullback_index(late, 77400.0)[0])


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], verbosity=1)
