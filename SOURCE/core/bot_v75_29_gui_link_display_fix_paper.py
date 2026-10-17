#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib, importlib.util, queue, sys, threading
from pathlib import Path
from types import ModuleType
from typing import Any

VERSION = "V75.29-GUI-LINK-DISPLAY-FIX-PAPER"
BASE_DIR = Path(__file__).resolve().parent
BASE_FILENAME = "bot_v75_28_powershell_sendkeys_hero_paper.py"
BASE_SHA256 = "eb3de16775f0d8922bc86ed251945cdfce23b823abf28008da477b0f5455748f"

def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def load_base() -> ModuleType:
    path = BASE_DIR / BASE_FILENAME
    if sha256_file(path) != BASE_SHA256:
        raise RuntimeError("V75.28 기준본 SHA256 불일치")
    spec = importlib.util.spec_from_file_location("v7528_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("V75.28 로드 실패")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod

v7528 = load_base()
v7527, v7525, v7524 = v7528.v7527, v7528.v7525, v7528.v7524
PowerShellHeroLink = v7528.PowerShellHeroLink
KST = v7525.KST

STAGE_PRIORITY = {
    "매수종목": 0, "매수직전": 1, "매수가능성": 2,
    "시장보류": 3, "관찰후보": 4, "관심종목": 5, "매도종목": 6
}

def price_pair(row: dict[str, Any]) -> str:
    raw = str(row.get("price", "")).replace(",", "").replace("원", "").strip()
    try:
        current = float(raw)
    except Exception:
        return str(row.get("price", ""))
    reason = str(row.get("reason", ""))
    if row.get("stage") == "매수종목" and "평단 " in reason:
        try:
            buy = float(reason.split("평단 ",1)[1].split("원",1)[0].replace(",",""))
        except Exception:
            buy = current
    else:
        buy = current
    target = buy * 1.015
    return f"매수 {buy:,.0f} / 매도 {target:,.0f}"

class EnhancedEngine(v7525.V7525Engine):
    def _gui_snapshot(self) -> dict[str, Any]:
        snap = super()._gui_snapshot()
        rows = list(snap.get("rows", []))

        # 같은 종목은 한 번만 표시한다. 더 높은 단계만 남긴다.
        chosen: dict[str, dict[str, Any]] = {}
        sells: list[dict[str, Any]] = []
        for row in rows:
            stage = str(row.get("stage", ""))
            symbol = str(row.get("symbol", ""))
            if stage == "매도종목":
                sells.append(row)
                continue
            prev = chosen.get(symbol)
            if prev is None or STAGE_PRIORITY.get(stage, 99) < STAGE_PRIORITY.get(str(prev.get("stage","")), 99):
                chosen[symbol] = row

        clean = list(chosen.values()) + sells
        for row in clean:
            row["display_price"] = price_pair(row)
            # 후보에는 아직 매수시각이 없으므로 빈칸, 보유종목만 실제 매수시각 표시
            if row.get("stage") != "매수종목":
                row["buy_time"] = "-"
            else:
                row["buy_time"] = row.get("time", "-")

            reason = str(row.get("reason", ""))
            if "거래량급증" in reason:
                row["reason"] = reason.replace("거래량급증", "대량 음봉 거래량 급증")
            name = str(row.get("name", ""))
            symbol = str(row.get("symbol", ""))
            # 종목명 앞에 코드가 중복된 경우 제거
            if name.startswith(symbol):
                name = name[len(symbol):].lstrip(" -_/()")
            row["name"] = name

        clean.sort(key=lambda r: (STAGE_PRIORITY.get(str(r.get("stage","")),99), str(r.get("name","")), str(r.get("symbol",""))))
        snap["rows"] = clean
        return snap

class FixedGui(v7525.SimpleDoubleClickGui):
    def _run(self) -> None:
        try:
            import tkinter as tk
            from tkinter import messagebox, ttk
            root = tk.Tk()
        except Exception as exc:
            self.logger.warning("GUI 시작 실패: %s", exc)
            return

        root.title("V75.29 관심·후보·매수·매도 — 더블클릭 영웅문 연동")
        root.geometry("1280x680")
        hero = PowerShellHeroLink(v7525.HERO_LINK_FILE)

        toolbar = ttk.Frame(root, padding=8); toolbar.pack(fill="x")
        status_var = tk.StringVar(value="종목 줄을 더블클릭하세요.")
        time_var = tk.StringVar(value="갱신 전")
        ttk.Button(toolbar, text="영웅문 위치 저장", command=lambda: begin_capture()).pack(side="left")
        ttk.Label(toolbar, text="  더블클릭 → 영웅문 자동전환").pack(side="left")
        ttk.Label(toolbar, textvariable=time_var).pack(side="right")

        cols = ("no","stage","buy_time","name","symbol","price","reason")
        tree = ttk.Treeview(root, columns=cols, show="headings", selectmode="browse")
        heads = {
            "no":"순번","stage":"단계","buy_time":"매수시각","name":"종목명",
            "symbol":"코드","price":"매수가 / +1.5% 매도가","reason":"이유"
        }
        widths = {"no":55,"stage":95,"buy_time":85,"name":190,"symbol":75,"price":220,"reason":500}
        for c in cols:
            tree.heading(c, text=heads[c])
            tree.column(c, width=widths[c], anchor="center" if c not in ("name","reason","price") else ("w" if c in ("name","reason") else "e"), stretch=c in ("name","reason"))
        sy = ttk.Scrollbar(root, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sy.set); sy.pack(side="right", fill="y")
        tree.pack(fill="both", expand=True, padx=(8,0), pady=4)
        ttk.Label(root, textvariable=status_var, anchor="w", padding=8).pack(fill="x")

        def switch_symbol(symbol: str) -> None:
            ok, msg = hero.send_symbol(symbol)
            status_var.set(msg if ok else f"자동전환 실패: {msg}")

        def on_double_click(event: Any) -> None:
            iid = tree.identify_row(event.y)
            if not iid: return
            vals = tree.item(iid, "values")
            if len(vals) >= 5:
                switch_symbol(str(vals[4]).strip())

        def finish_capture() -> None:
            ok, msg = hero.capture_cursor_target()
            status_var.set(msg if ok else f"위치 저장 실패: {msg}")

        def countdown(n: int) -> None:
            if n <= 0:
                finish_capture(); return
            status_var.set(f"{n}초 안에 영웅문 종목코드 입력칸을 클릭하세요.")
            root.after(1000, lambda: countdown(n-1))

        def begin_capture() -> None:
            messagebox.showinfo("영웅문 위치 저장","확인을 누른 뒤 5초 안에 영웅문 종목코드 입력칸을 클릭하세요.")
            countdown(5)

        def apply_snapshot(snap: dict[str, Any]) -> None:
            for iid in tree.get_children(): tree.delete(iid)
            rows = list(snap.get("rows", []))
            counters: dict[str,int] = {}
            for row in rows:
                stage = str(row.get("stage",""))
                counters[stage] = counters.get(stage,0)+1
                tree.insert("", "end", values=(
                    counters[stage], stage, row.get("buy_time","-"), row.get("name",""),
                    row.get("symbol",""), row.get("display_price",row.get("price","")), row.get("reason","")
                ))
            time_var.set(str(snap.get("updated","")))
            status_var.set(f"시장 {snap.get('market','-')} | 중복 제거 후 {len(rows)}종목")

        def poll() -> None:
            try:
                while True:
                    kind, payload = self.events.get_nowait()
                    if kind == "snapshot": apply_snapshot(payload)
                    elif kind == "close": root.destroy(); return
            except queue.Empty:
                pass
            if not self.closed.is_set(): root.after(300, poll)

        tree.bind("<Double-1>", on_double_click)
        root.protocol("WM_DELETE_WINDOW", lambda: root.withdraw())
        root.after(200, poll)
        root.mainloop()

# 핵심 버그 수정:
# 이전 버전은 상속된 _run이 PowerShellHeroLink를 다시 구형 링크로 덮어썼다.
# 이번 버전은 GUI가 PowerShellHeroLink를 직접 생성한다.
v7525.VERSION = VERSION
v7525.SimpleDoubleClickGui = FixedGui
v7525.V7525Engine = EnhancedEngine

def main() -> int:
    return int(v7525.main())

if __name__ == "__main__":
    raise SystemExit(main())
