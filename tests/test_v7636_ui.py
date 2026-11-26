"""V7636 (2026-11-26 형 지시) 화면 수정 — 차트(십자선 유지·스크롤바·왼쪽 끌기 전체보기·시간글씨·1분봉·이평 굵기)
· 등락률 빨강/파랑 · 단계카드↔합계칸 크기 맞바꿈 · 종목표 열 맞춤. 매매 로직은 건드리지 않는다."""
from __future__ import annotations
import os, sys, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "SOURCE" / "core"))
import v7614_chart_panel as C
import v7636_change_colors as V

KST = timezone(timedelta(hours=9))


class ChartPureTests(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(C.DEFAULT_TF, "m1")
        self.assertEqual({p: C.MA_BOLD.get(p) for p in (5, 20, 60)}, {5: 2.0, 20: 2.0, 60: 2.0})
        self.assertNotIn(10, C.MA_BOLD)
        self.assertGreaterEqual(C.AXIS_TIME_FONT, 9 * 1.2)

    def test_fit_all_span(self):
        self.assertEqual(C.fit_all_span(600, 700), 600)
        self.assertEqual(C.fit_all_span(5000, 700), 1400)      # 봉 폭 0.5px 까지만
        self.assertEqual(C.fit_all_span(2, 700), C.ZOOM_MIN_BARS)

    def test_scroll_mapping_roundtrip(self):
        total, count = 600, 120
        for offset in (0, 37, 480):
            start, end = total - offset - count, total - offset
            first, last = C.scroll_fractions(start, end, total)
            self.assertAlmostEqual(last - first, count / total)
            self.assertEqual(C.offset_for_fraction(first, count, total), offset)
        self.assertEqual(C.offset_for_fraction(-1, count, total), total - count)   # 왼쪽 끝 넘어감 → 가장 과거
        self.assertEqual(C.offset_for_fraction(2, count, total), 0)                # 오른쪽 끝 넘어감 → 최신

    def test_appended_bars_keeps_past_view(self):
        t0 = datetime(2026, 11, 26, 13, 0, tzinfo=KST)
        old = [dict(t=t0 + timedelta(minutes=i)) for i in range(10)]
        new = old[2:] + [dict(t=t0 + timedelta(minutes=10)), dict(t=t0 + timedelta(minutes=11))]
        self.assertEqual(C.appended_bars(old, new), 2)
        self.assertEqual(C.appended_bars([], new), 0)


class ChangeColorTests(unittest.TestCase):
    def test_colors(self):
        self.assertEqual(V.change_color("▲+3.25%"), V.UP_COLOR)
        self.assertEqual(V.change_color("▼-1.10%"), V.DOWN_COLOR)
        self.assertEqual(V.change_color("+0.40%"), V.UP_COLOR)
        self.assertEqual(V.change_color("-0.4"), V.DOWN_COLOR)
        self.assertEqual(V.change_color("0.00%"), V.FLAT_COLOR)
        self.assertIsNone(V.change_color("-"))
        self.assertIsNone(V.change_color(""))

    def test_percent_text(self):
        self.assertEqual(V.percent_text("1.18"), "+1.18%")
        self.assertEqual(V.percent_text("-0.45"), "-0.45%")
        self.assertEqual(V.percent_text("+2.00%"), "+2.00%")
        self.assertEqual(V.percent_text("▲+2.00%"), "▲+2.00%")
        self.assertEqual(V.percent_text("-"), "-")
        self.assertEqual(V.percent_text("수집 불가"), "수집 불가")


@unittest.skipUnless(os.environ.get("V7609_TEST_TK") == "1", "opt-in Tk widget check")
class OverlayTkTests(unittest.TestCase):
    def test_overlay_colors_cells_and_forwards_clicks(self):
        import tkinter as tk
        from tkinter import ttk
        root = tk.Tk()
        self.addCleanup(root.destroy)
        root.geometry("500x240")
        tree = ttk.Treeview(root, columns=("no", "change", "reason"), show="headings")
        tree.pack(fill="both", expand=True)
        for c in ("no", "change", "reason"):
            tree.column(c, width=120, anchor="e")
        tree.tag_configure("a", background="#0d4656")
        for i, value in enumerate(("▲+3.25%", "▼-1.10%", "-")):
            tree.insert("", "end", iid=f"r{i}", values=(i, value, "x"), tags=("a",))
        events = []
        tree.bind("<Double-Button-1>", lambda e: events.append(tree.identify_row(e.y)))
        overlay = V.attach(tree)
        self.assertIs(V.attach(tree), overlay)
        root.update()
        overlay.refresh()
        root.update()
        shown = [s for s in overlay.shown if s]
        self.assertEqual(len(shown), 2)                       # '-' 칸은 칠하지 않음
        self.assertEqual(overlay.labels[0].cget("fg"), V.UP_COLOR)
        self.assertEqual(overlay.labels[1].cget("fg"), V.DOWN_COLOR)
        self.assertEqual(overlay.labels[0].cget("bg"), "#0d4656")
        label = overlay.labels[1]
        label.event_generate("<ButtonPress-1>", x=4, y=4, time=1000)
        label.event_generate("<ButtonRelease-1>", x=4, y=4, time=1005)
        root.update()
        self.assertEqual(tree.selection(), ("r1",))           # 겹친 칸을 눌러도 그 줄이 선택됨
        label.event_generate("<ButtonPress-1>", x=4, y=4, time=1100)
        root.update()
        self.assertEqual(events, ["r1"])                      # 더블클릭도 표로 전달


if __name__ == "__main__":
    unittest.main()
