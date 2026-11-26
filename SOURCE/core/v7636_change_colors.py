"""V7636 (2026-11-26 형 지시): 종목표의 '등락률' 칸만 상승=빨강 · 하락=파랑으로 칠한다.

Tk 표(Treeview)는 줄 단위로만 글자색을 바꿀 수 있어서, 등락률 칸 위에 같은 배경의 작은 글자칸(Label)을
겹쳐 그린다. 화면 표시 전용 — 매매·장부·차트 연동 로직은 건드리지 않는다.
* 0.3초마다 보이는 줄만 다시 맞춘다(스크롤·크기 변경·새로고침 모두 따라감, 바뀐 것만 다시 칠함).
* 겹친 글자칸을 눌러도 아래 표가 눌린 것과 똑같이 동작하도록 클릭·더블클릭·휠을 표로 넘긴다.
"""
from __future__ import annotations

import re
from typing import Any

UP_COLOR = "#ffa3a3"     # V7637: 어두운 줄 색 위에서도 잘 보이는 밝은 빨강(대비 4:1 이상)
DOWN_COLOR = "#a8d8ff"   # V7637: 밝은 파랑
FLAT_COLOR = "#c8d2dc"
TICK_MS = 300
_NUMBER = re.compile(r"[-+]?\d+(?:[.,]\d+)?")


def change_color(text: Any) -> str | None:
    """'▲+3.25%' / '-1.10' / '0.00%' → 빨강/파랑/회색. 숫자가 없으면 None(칠하지 않음)."""
    raw = str(text or "").strip()
    if not raw or raw in {"-", "—"}:
        return None
    if raw.startswith("▲"):
        return UP_COLOR
    if raw.startswith("▼"):
        return DOWN_COLOR
    match = _NUMBER.search(raw.replace(",", ""))
    if not match:
        return None
    try:
        value = float(match.group(0))
    except ValueError:
        return None
    return UP_COLOR if value > 0 else DOWN_COLOR if value < 0 else FLAT_COLOR


def percent_text(text: Any) -> str:
    """표에 '1.18'처럼 % 없이 들어온 값은 '+1.18%'로 보이게 한다(이미 %·▲▼가 있으면 그대로)."""
    raw = str(text or "").strip()
    if not raw or raw in {"-", "—"} or "%" in raw or raw[:1] in {"▲", "▼"}:
        return raw
    try:
        value = float(raw.replace(",", ""))
    except ValueError:
        return raw
    return f"{value:+.2f}%"


class ChangeColorOverlay:
    """한 표(Treeview)의 한 열을 상승/하락 색으로 칠한다."""

    # 더블클릭은 따로 묶지 않는다: 눌림 두 번을 '시각' 그대로 표로 넘기면 표가 스스로 더블클릭으로 알아본다.
    FORWARD = ("<ButtonPress-1>", "<ButtonRelease-1>", "<ButtonPress-3>", "<ButtonRelease-3>", "<B1-Motion>")

    def __init__(self, tree: Any, column: str = "change", *, font: Any = None, bold: bool = True) -> None:
        self.tree = tree
        self.column = column
        self.font = font
        self.bold = bold
        self.labels: list[Any] = []
        self.shown: list[tuple] = []
        self.closed = False
        tree.bind("<Destroy>", self._on_destroy, add="+")
        tree.after(TICK_MS, self._tick)

    # ---------------------------------------------------------------- 내부
    def _on_destroy(self, event: Any) -> None:
        if event.widget is self.tree:
            self.closed = True

    def _font(self) -> Any:
        if self.font is not None:
            return self.font
        try:
            import tkinter.font as tk_font
            from tkinter import ttk
            style = str(self.tree.cget("style") or "Treeview")
            base = ttk.Style(self.tree).lookup(style, "font") or "TkDefaultFont"
            font = tk_font.Font(root=self.tree, font=base)
            if self.bold:
                font.configure(weight="bold")
            self.font = font
        except Exception:
            self.font = ("맑은 고딕", 11, "bold")
        return self.font

    def _row_background(self, iid: str) -> str:
        from tkinter import ttk
        try:
            if iid in self.tree.selection():
                style = str(self.tree.cget("style") or "Treeview")
                picked = ttk.Style(self.tree).map(style, "background") or ttk.Style(self.tree).map("Treeview", "background")
                for states, color in _pairs(picked):
                    if "selected" in states:
                        return str(color)
                return "#2f7bd6"
            for tag in self.tree.item(iid, "tags") or ():
                color = str(self.tree.tag_configure(tag, "background") or "")
                if color:
                    return color
        except Exception:
            pass
        try:
            style = str(self.tree.cget("style") or "Treeview")
            return str(ttk.Style(self.tree).lookup(style, "fieldbackground") or "#0b2233")
        except Exception:
            return "#0b2233"

    def _label(self, index: int) -> Any:
        import tkinter as tk
        while len(self.labels) <= index:
            label = tk.Label(self.tree, bd=0, highlightthickness=0, padx=4, takefocus=0)
            for sequence in self.FORWARD:
                label.bind(sequence, lambda e, s=sequence, l=label: self._forward(s, l, e))
            label.bind("<MouseWheel>", lambda e, l=label: self._forward_wheel(l, e))
            self.labels.append(label)
            self.shown.append(())
        return self.labels[index]

    def _forward(self, sequence: str, label: Any, event: Any) -> str:
        try:
            x, y = label.winfo_x() + event.x, label.winfo_y() + event.y
            self.tree.focus_set()
            stamp = int(getattr(event, "time", 0) or 0)
            if sequence == "<ButtonPress-1>":
                last = getattr(self, "_last_press", None)
                self._last_press = (stamp, x, y)
                if last and 0 <= stamp - last[0] <= 500 and abs(y - last[2]) <= 4:
                    # 겹친 글자칸 위 더블클릭: 사이에 다른 창 사건이 끼면 표가 더블클릭을 못 알아보므로
                    # 표에 눌림-뗌-눌림을 연달아 넣어 표의 더블클릭 동작(상세보기 등)을 그대로 일으킨다.
                    self._last_press = None
                    self.tree.event_generate("<ButtonPress-1>", x=x, y=y, time=stamp + 1)
                    self.tree.event_generate("<ButtonRelease-1>", x=x, y=y, time=stamp + 2)
                    self.tree.event_generate("<ButtonPress-1>", x=x, y=y, time=stamp + 3)
                    return "break"
            options = {"x": x, "y": y}
            if stamp:
                options["time"] = stamp
            self.tree.event_generate(sequence, **options)
        except Exception:
            pass
        return "break"

    def _forward_wheel(self, label: Any, event: Any) -> str:
        try:
            self.tree.event_generate("<MouseWheel>", delta=int(event.delta),
                                     x=label.winfo_x() + event.x, y=label.winfo_y() + event.y)
        except Exception:
            pass
        return "break"

    def _visible_items(self) -> list[str]:
        items: list[str] = []
        pending = list(self.tree.get_children(""))
        while pending:
            iid = pending.pop(0)
            items.append(iid)
            try:
                if self.tree.item(iid, "open"):
                    pending[0:0] = list(self.tree.get_children(iid))
            except Exception:
                pass
        return items

    def refresh(self) -> None:
        tree = self.tree
        used = 0
        try:
            displayed = tree.cget("displaycolumns")
            columns = tuple(tree.cget("columns"))
            visible = columns if displayed in ("#all", ("#all",)) else tuple(displayed)
        except Exception:
            visible = ()
        if self.column in visible and tree.winfo_ismapped():
            anchor = str(tree.column(self.column, "anchor") or "e")
            for iid in self._visible_items():
                box = tree.bbox(iid, self.column)
                if not box:
                    continue
                text = str(tree.set(iid, self.column) or "")
                color = change_color(text)
                if color is None:
                    continue
                x, y, w, h = box
                if w < 12 or h < 8:
                    continue
                state = (iid, text, color, self._row_background(iid), x, y, w, h, anchor)
                label = self._label(used)
                if self.shown[used] != state:
                    label.configure(text=text.split("\n")[0], fg=color, bg=state[3], font=self._font(), anchor=anchor)
                    label.place(in_=tree, x=x + 1, y=y + 1, width=w - 2, height=h - 2)
                    self.shown[used] = state
                used += 1
        for index in range(used, len(self.labels)):
            if self.shown[index] != ():
                self.labels[index].place_forget()
                self.shown[index] = ()

    def _tick(self) -> None:
        if self.closed:
            return
        try:
            self.refresh()
        except Exception:
            pass   # 화면 칠하기 실패가 표·매매를 멈추게 하면 안 된다.
        try:
            self.tree.after(TICK_MS, self._tick)
        except Exception:
            self.closed = True


def _pairs(mapping: Any) -> list[tuple[tuple, str]]:
    out = []
    for entry in mapping or ():
        try:
            *states, color = entry
            flat = []
            for s in states:
                flat.extend(s if isinstance(s, (tuple, list)) else str(s).split())
            out.append((tuple(flat), color))
        except Exception:
            continue
    return out


def attach(tree: Any, column: str = "change", **kwargs: Any) -> ChangeColorOverlay | None:
    """같은 표에 두 번 붙지 않게 한 번만."""
    existing = getattr(tree, "_v7636_change_overlay", None)
    if existing is not None and not existing.closed:
        return existing
    try:
        overlay = ChangeColorOverlay(tree, column, **kwargs)
        tree._v7636_change_overlay = overlay
        return overlay
    except Exception:
        return None
