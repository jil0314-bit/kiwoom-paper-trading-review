"""V7633 시험(2026-11-25 형 지시): 에이피알 실제 1분봉으로 돌파 전고점·갭 시가·재무장, 추가매수 간격, 보유표 차수 줄.
주문·네트워크 없음."""
from __future__ import annotations
import sys, types, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

_s = types.ModuleType("msvcrt"); _s.LK_NBLCK = 2; _s.LK_LOCK = 1; _s.LK_UNLCK = 0; _s.LK_NBRLCK = 4; _s.LK_RLCK = 3
_s.locking = lambda *a, **k: None; sys.modules.setdefault("msvcrt", _s)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "SOURCE" / "core"))
import v7609_prebuy_rules as P
import v7613_split_rules as S

KST = timezone(timedelta(hours=9))
# 에이피알(278470) 2026-11-25 08:00~11:26 실제 1분봉(NXT 프리장 포함, 봇이 받는 그대로) (시각 시 고 저 종 거래량). 전일 종가 261,000 · 전일 거래량 359,703
APR = "0800 262500 272000 262500 270500 3638|0801 270500 271500 268000 268500 4359|0802 269000 269000 266500 268000 3466|0803 268000 268000 267000 267500 1256|0804 267500 269500 267000 268500 1773|0805 269000 271000 268500 270500 3388|0806 270000 270500 269000 269500 1282|0807 269500 269500 269000 269500 1070|0808 269500 270000 267500 267500 1402|0809 268000 270000 267000 270000 1651|0810 269500 272000 269000 271500 3225|0811 271500 272000 271000 271000 1177|0812 271000 271500 270500 271000 405|0813 271000 274000 271000 274000 3503|0814 274000 280500 273500 276500 16501|0815 276500 277500 276000 277000 4311|0816 277000 277500 276500 277500 1684|0817 277500 278000 276000 276500 3005|0818 276500 277000 276000 276500 837|0819 276500 277000 276000 276500 851|0820 276500 276500 273000 274000 3130|0821 273000 275000 273000 274500 404|0822 274500 276000 274500 276000 1270|0823 276000 276500 275000 275000 1323|0824 275500 276500 275000 276500 1208|0825 276000 277000 276000 276000 1327|0826 276500 277500 276000 277500 1443|0827 277500 277500 277000 277500 1000|0828 277500 277500 274500 275000 9984|0829 276000 276000 274000 274500 799|0830 274500 275000 273500 274500 592|0831 275000 275000 274500 275000 176|0832 275000 275000 274000 274500 704|0833 274000 274500 274000 274500 200|0834 274500 276000 274000 276000 793|0835 275500 276000 275000 276000 505|0836 276000 276000 275000 275500 1302|0837 275500 276000 274500 275000 954|0838 275500 275500 275000 275000 180|0839 275000 275500 274500 275000 467|0840 275000 276500 275000 276000 912|0841 276500 276500 275500 276000 958|0842 276500 276500 275500 276000 901|0843 276000 276500 275500 276500 823|0844 276000 276500 274500 276000 1025|0845 275500 276000 275000 275000 337|0846 275500 275500 275000 275500 260|0847 275500 275500 275000 275000 104|0848 275000 276500 275000 276000 1364|0849 276000 276000 275500 276000 488|0900 274000 277000 272000 275000 22802|0901 275000 275500 271000 272500 11167|0902 273000 276000 270500 271500 11317|0903 271500 275500 271000 274500 6648|0904 274500 277500 273500 275000 8890|0905 275000 276500 273000 274000 5506|0906 274000 277000 273000 276000 4227|0907 276000 276500 273500 274500 3934|0908 275000 275000 273000 274500 4650|0909 274500 277500 274000 277500 5714|0910 277500 278000 276500 278000 2690|0911 278000 278000 277000 277500 3401|0912 278000 283000 278000 282500 15584|0913 282500 285000 272500 274500 20327|0914 274500 277000 274000 276500 7261|0915 277000 279000 276000 278500 6676|0916 278500 278500 275500 276500 5191|0917 276500 278500 275000 277500 5667|0918 277000 279500 276500 278500 4318|0919 278500 281500 277500 279000 6400|0920 279000 281000 276500 279000 7649|0921 279000 280500 277500 279500 3444|0922 280000 281000 278500 280000 3115|0923 280000 281000 278500 279500 5215|0924 280000 281000 279000 280500 3387|0925 280000 283500 280000 283000 7080|0926 282000 284000 281000 281500 6526|0927 281000 283000 280000 281500 5516|0928 281500 284500 281000 284500 7438|0929 284000 286000 282500 284000 19893|0930 283500 286000 282500 284000 7295|0931 284000 285500 282500 284500 5370|0932 284500 286500 284000 285500 7790|0933 285500 286000 282500 283500 6235|0934 284000 285000 281000 283000 5968|0935 282750 286500 282000 286000 7483|0936 286000 286500 284500 285000 4659|0937 285000 286500 284500 286000 7628|0938 286000 286500 284500 285000 4610|0939 284500 288000 284000 287500 11571|0940 286500 288000 285500 286500 6594|0941 287000 287500 285500 287500 6576|0942 286000 288000 286000 287000 4654|0943 287000 287500 285500 287000 4855|0944 287000 287500 285500 286000 7334|0945 286500 288500 286000 288500 5371|0946 288500 288500 287000 287500 6571|0947 287500 289000 287000 288500 5448|0948 288500 289000 287000 288500 7744|0949 288500 289000 287500 289000 6504|0950 288500 290000 288000 289500 7195|0951 289500 290000 288000 288500 8236|0952 288500 289500 288000 289000 5728|0953 289000 289000 287000 287000 8158|0954 287500 291500 287000 290500 14643|0955 291000 292500 290000 291000 7812|0956 291000 292500 290000 291500 6163|0957 291500 293000 291000 293000 5957|0958 293000 295500 292500 293000 14972|0959 293500 294000 288500 289500 11839|1000 289500 291000 288000 288500 8627|1001 288500 292500 287000 288500 4610|1002 288000 289000 288000 288500 2469|1003 289000 289000 286500 286500 3333|1004 286500 289000 286500 288500 4309|1005 288500 290500 288000 290000 2365|1006 290000 291500 290000 291500 2628|1007 291500 293000 291000 292750 4291|1008 292500 293000 291500 292500 2673|1009 292500 292500 290500 291000 2203|1010 290500 291000 289500 290000 2481|1011 290500 292500 290000 292000 2207|1012 292000 293500 291000 293250 2367|1013 293250 294000 292500 293500 2567|1014 293500 294000 293000 293500 2553|1015 294000 296000 293500 295500 9612|1016 295500 296500 294500 295000 6377|1017 294500 297000 294500 296000 4761|1018 296000 297000 295500 296000 3881|1019 295500 296500 294500 295000 4686|1020 295000 295500 293500 294000 3277|1021 294000 294500 292500 293500 3159|1022 293500 294500 293000 294500 1503|1023 294500 295500 294000 295000 1222|1024 294500 295000 294000 294500 1534|1025 295000 295000 294000 294500 2105|1026 295000 295000 293500 293500 1691|1027 293500 294500 293000 293500 2297|1028 293500 293500 291000 291000 4090|1029 291500 292000 288000 290500 6287|1030 291000 292000 290000 291000 4669|1031 291000 291500 289500 290500 4009|1032 290500 290500 288500 289000 3517|1033 289000 290000 288500 288500 2193|1034 289000 289500 288500 289000 1987|1035 289000 291500 288500 291000 5389|1036 291000 291000 289500 289500 3063|1037 289500 290000 289000 289500 1068|1038 289500 290000 288000 289000 2180|1039 289500 290000 288500 289500 1313|1040 289000 289500 288500 289500 1319|1041 289000 289500 288500 289000 1104|1042 289000 290000 288500 289500 1092|1043 289500 290000 288500 289000 1231|1044 289000 290000 289000 290000 1029|1045 289750 291000 289000 289500 1141|1046 290000 291000 289500 290500 1505|1047 290500 290500 289500 289500 1288|1048 289750 290000 289500 289500 654|1049 289500 290000 289000 289500 1035|1050 289500 290500 289500 290000 1729|1051 289500 290500 289000 290000 1056|1052 289500 291000 289000 289000 1057|1053 289500 290000 289000 290000 1118|1054 290000 290500 289000 290000 1210|1055 290000 290500 289500 290000 1045|1056 289500 290000 289000 289500 1134|1057 289500 290000 289000 290000 1039|1058 290000 290000 289000 290000 1442|1059 290000 290000 289500 290000 935|1100 289500 290000 289000 289000 2155|1101 289250 289500 288500 288500 1759|1102 288000 289500 288000 289500 2743|1103 289500 289500 289000 289500 1176|1104 289000 290000 288500 289500 1347|1105 289500 290000 289000 290000 865|1106 289500 290000 288500 289500 1869|1107 289500 290000 289000 289000 865|1108 289000 290000 288500 288500 1213|1109 288500 289000 288000 288000 680|1110 288000 289000 288000 288000 1225|1111 288000 288500 287500 287500 1218|1112 287500 289000 287500 288500 1751|1113 288500 289000 288000 288500 1258|1114 289000 289500 288500 289500 1022|1115 289500 290000 288500 288500 1168|1116 289000 290000 288500 290000 1070|1117 289500 290000 289000 289500 1433|1118 289000 290000 289000 290000 787|1119 289500 290000 289000 289500 916|1120 289500 290000 288500 290000 1124|1121 289500 290500 289000 290500 1950|1122 290500 292500 290000 292000 5084|1123 292000 292500 291000 292000 2257|1124 292000 292000 291000 291500 1370|1125 291500 293000 291500 293000 2437|1126 292500 294500 292500 294000 6844"
PREV = dict(previous_close=261000.0, previous_volume=359703.0, previous_day="2026-11-24")


def apr_rows(until=None):
    out = []
    for part in APR.split("|"):
        hm, *nums = part.split()
        o, h, l, c, v = (float(x) for x in nums)
        stamp = datetime(2026, 11, 25, int(hm[:2]), int(hm[2:]), tzinfo=KST)
        if until and stamp.strftime("%H:%M") > until:
            break
        out.append(dict(timestamp=stamp.isoformat(), open=o, high=h, low=l, close=c, volume=int(v)))
    return out


def judge(until):
    rows = apr_rows(until)
    now = datetime.fromisoformat(rows[-1]["timestamp"]) + timedelta(minutes=1, seconds=5)
    return P.evaluate(rows, now=now, **PREV)


class V7633DefaultsTests(unittest.TestCase):
    def test_entry_switches_off_by_default_and_add_gap_on(self):
        # 재현에서 손해였던 세 가지는 꺼 두고, 추가매수 1% 간격만 켠다
        self.assertFalse(P.BREAKOUT_DAY_HIGH)
        self.assertEqual(P.PULLBACK_OPEN_GRACE_BARS, 0)
        self.assertFalse(P.PULLBACK_REARM)
        self.assertEqual(S.ADD_MIN_GAP, 0.01)
        self.assertTrue(S.SECOND_DRY)
        with mock.patch.object(P, "BREAKOUT_ENABLED", True):        # V7637: 돌파 경로는 기본 꺼짐 — 켜면 그날 실제 판정 재현
            r = judge("11:26")                                      # 기본값 = 오늘 실제 매수와 같은 판정
        self.assertEqual((r["route"], r["buy"]), ("BREAKOUT", True))
        self.assertFalse(P.BREAKOUT_ENABLED)                        # V7637 형 결정: 1차 진입은 눌림목·통합만


class V7633BreakoutTests(unittest.TestCase):
    def test_breakout_below_day_high_is_not_bought(self):
        # 11:26봉: 직전 10분 고점(290,500)은 넘었지만 당일 고점 297,000(10:17) 아래 → 켜면 사지 않는다
        with mock.patch.object(P, "BREAKOUT_DAY_HIGH", True):
            r = judge("11:26")
            self.assertFalse(r and r.get("buy") and r.get("route") == "BREAKOUT")


class V7633PullbackTests(unittest.TestCase):
    def test_gap_open_grace_keeps_route_alive(self):
        # 시가 274,000 → 09:02 저가 270,500. 옛 규칙은 여기서 그날 눌림목 경로가 끝났다.
        self.assertIsNone(P._pullback_index(apr_rows("11:26"), 261000.0)[0])
        with mock.patch.object(P, "PULLBACK_OPEN_GRACE_BARS", 3):
            self.assertIsNotNone(P._pullback_index(apr_rows("11:26"), 261000.0)[0])

    def test_rearm_catches_1119_second_bottom(self):
        # 형 지적 자리: 11:18 거래바닥 → 11:19 아래꼬리 + 5선 위 → 그 봉에서 매수 신호
        with mock.patch.object(P, "PULLBACK_OPEN_GRACE_BARS", 3), mock.patch.object(P, "PULLBACK_REARM", True):
            r = judge("11:19")
            self.assertEqual((r["route"], r["buy"]), ("PULLBACK", True))
            self.assertIn("아래꼬리", r["reason"])
        with mock.patch.object(P, "PULLBACK_OPEN_GRACE_BARS", 3):   # 재무장 없으면 09:22 첫 신호 뒤로는 다시 안 본다
            r_old = judge("11:19")
            self.assertFalse(r_old and r_old.get("route") == "PULLBACK" and r_old.get("buy"))

    def test_signal_is_not_reused_without_new_dry_bar(self):
        # 신호 바로 다음 봉(새 거래바닥 없음)에서는 같은 신호로 다시 사지 않는다
        with mock.patch.object(P, "PULLBACK_OPEN_GRACE_BARS", 3), mock.patch.object(P, "PULLBACK_REARM", True):
            r = judge("11:20")
            self.assertFalse(r and r.get("route") == "PULLBACK" and r.get("buy"))


class V7633SplitGapTests(unittest.TestCase):
    def _bars(self, add_close):
        start = datetime(2026, 11, 25, 10, 0, tzinfo=KST)
        seq = [(100, 101, 99, 100, 1000)] * 20
        seq += [(100, 100, 97, 97.5, 8000)]                 # ① 거래 음봉
        seq += [(97.5, 98, 97, 97.6, 300)]                  # ② 거래바닥
        seq += [(97.6, 98.2, 97.2, 98.0, 400)]              # ③ 양봉
        seq += [(98.0, 98.8, 97.9, 98.6, 600)]              # ④ 5선 안착 양봉
        seq += [(98.6, 98.7, 98.4, 98.5, 100)]              # ⑤ 재바닥
        seq += [(add_close - 0.3, add_close + 0.1, add_close - 0.4, add_close, 500)]   # 매수 봉
        return [dict(timestamp=(start + timedelta(minutes=k)).isoformat(), open=o, high=h, low=l, close=c, volume=v)
                for k, (o, h, l, c, v) in enumerate(seq)]

    def test_min_gap_blocks_add_near_last_price(self):
        start_after = datetime(2026, 11, 25, 10, 5, tzinfo=KST)
        bars = self._bars(99.0)                              # 직전 매수가 100 대비 -1.0%
        with mock.patch.object(S, "ADD_MIN_GAP", 0.0):
            self.assertTrue(S.replay(bars, start_after, 100.0)["fresh_trigger"])
        with mock.patch.object(S, "ADD_MIN_GAP", 0.015):
            self.assertFalse(S.replay(bars, start_after, 100.0)["fresh_trigger"])
        with mock.patch.object(S, "ADD_MIN_GAP", 0.015):
            self.assertTrue(S.replay(self._bars(98.4), start_after, 100.0)["fresh_trigger"])   # -1.6%면 산다


class V7633TrancheRowTests(unittest.TestCase):
    def test_tranche_rows_and_totals(self):
        try:
            import v7609_trade_view as V
        except ImportError:
            self.skipTest("tkinter 없음")
        row = dict(buy_date="2026-11-25", invested=12992980, tranche_fills=[
            dict(tranche=1, time="2026-11-25T11:27:04.8+09:00", price=5610, quantity=178, notional=998580),
            dict(tranche=2, time="2026-11-25T12:45:05.0+09:00", price=5590, quantity=536, notional=2996240),
            dict(tranche=3, time="2026-11-25T13:49:03.2+09:00", price=5480, quantity=1642, notional=8998160)])
        fills = V.tranche_rows(row)
        self.assertEqual([f["tranche"] for f in fills], [1, 2, 3])
        self.assertEqual(V.tranche_value(fills[2], "name", row), "   └ 3차")
        self.assertEqual(V.tranche_value(fills[2], "buy_time", row), "13:49:03")
        self.assertEqual(V.tranche_value(fills[2], "quantity", row), "1,642")
        self.assertEqual(V.tranche_value(fills[2], "invested", row), "899.8만")   # V7634 만 단위
        self.assertEqual(V.tranche_value(fills[2], "buy_date", row), "")       # 같은 날이면 날짜 생략
        self.assertEqual(V.total_invested([row, dict(invested=885000)]), 13877980)
        self.assertEqual(V.display_value(row, "invested"), "1,299만")

    def test_v7634_age_when_and_labels(self):
        try:
            import v7609_trade_view as V
        except ImportError:
            self.skipTest("tkinter 없음")
        from datetime import date
        today = date(2026, 11, 26)                                             # 목요일
        self.assertEqual(V.trading_days_between(date(2026, 11, 20), today), 4) # 금 → 목 (주말 제외)
        hold = dict(stage="매수종목", buy_date="2026-11-25", buy_time="11:27:04")
        self.assertEqual(V.buy_age(hold, today), 1)
        self.assertEqual(V.buy_when_text(hold, today), "1일전")
        self.assertEqual(V.buy_when_text(dict(stage="매수종목", buy_date="2026-11-26", buy_time="09:41:12"), today), "09:41")
        sold = dict(stage="매도종목", buy_date="2026-11-20", sell_date="2026-11-26", sell_time="09:16:42",
                    net_pnl=74300, net_pnl_pct=0.57, buy_price=26378, current_price=26600)
        self.assertEqual(V.buy_age(sold, today), 4)
        self.assertEqual(V.display_value(sold, "net_combo"), "+74,300  +0.57%")
        self.assertEqual(V.display_value(dict(buy_price=5515, current_price=5460), "price_combo"), "5,515→5,460")
        fill = dict(tranche=3, time="2026-11-23T10:09:00+09:00", price=25700, quantity=350, notional=8995000)
        self.assertEqual(V.tranche_value(fill, "buy_when", sold, today), "11/23")  # 전날 차수는 날짜
        self.assertEqual(V.man(998580), "99.9만")
        self.assertEqual(V.man(12977900), "1,298만")
        self.assertEqual(V.reason_label("예약목표 2차후 평단+0.5%"), "평단+0.5%")
        self.assertEqual(V.reason_label("예약목표 비용포함+2.0%"), "목표+2%")


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], verbosity=1)


class V7634SaleTrancheTests(unittest.TestCase):
    """매도 1건마다 그 보유분의 매수 차수(이월분 포함)를 PAPER 체결장부에서 다시 묶는다."""

    def test_carried_three_tranches_then_sell(self):
        import ast, csv as _csv, tempfile, types
        from typing import Any
        core = Path(__file__).resolve().parents[1] / "SOURCE" / "core"
        tree = ast.parse((core / "bot_v76_00_three_stage_ctrlv_paper.py").read_text(encoding="utf-8-sig"))
        wanted = {"_v7634_sale_tranches", "_v7609_timestamp", "_v7634_tranche_text"}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "paper_fills.csv"
            rows = [("2026-11-20T12:53:00+09:00", "BUY", 35, 28350), ("2026-11-20T13:24:00+09:00", "BUY", 107, 27950),
                    ("2026-11-23T10:09:00+09:00", "BUY", 350, 25700), ("2026-11-26T09:16:42+09:00", "SELL", 492, 26600),
                    ("2026-11-26T09:30:00+09:00", "BUY", 10, 26000), ("2026-11-26T09:40:00+09:00", "SELL", 10, 26500)]
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = _csv.writer(handle)
                writer.writerow(["timestamp", "symbol", "side", "quantity", "price"])
                for stamp, side, qty, price in rows:
                    writer.writerow([stamp, "003495", side, qty, price])
            ns = dict(datetime=datetime, timedelta=timedelta, Path=Path, csv=_csv, Any=Any, KST=KST,
                      _V753=types.SimpleNamespace(FILL_FILE=str(path)))
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name in wanted:
                    node.decorator_list = []
                    exec(compile(ast.Module([node], []), "<v7634>", "exec"), ns)
            fake = type("Fake", (), {"_v7609_timestamp": staticmethod(ns["_v7609_timestamp"]),
                                     "_v7634_sale_tranches": ns["_v7634_sale_tranches"]})()
            out = fake._v7634_sale_tranches(datetime(2026, 11, 26, 15, 0, tzinfo=KST))
        first = out[("003495", "2026-11-26T09:16:42")]
        self.assertEqual([t["quantity"] for t in first], [35, 107, 350])          # 11/20·11/20·11/23 이월 3차
        self.assertEqual(sum(t["notional"] for t in first), 12977900)
        self.assertEqual(len(out[("003495", "2026-11-26T09:40:00")]), 1)           # 매도 뒤 새로 산 1차만
        text = ns["_v7634_tranche_text"](first)
        self.assertTrue(text.startswith("①28,350×35=99.2만 ②27,950×107=299.1만 ③25,700×350=899.5만"), text)
        self.assertTrue(text.endswith("합계1,297.8만"), text)
