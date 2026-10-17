#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V75.25 PAPER
- V75.24.1의 PAPER 매매, 장중명령, 자동손절 없음, 목표 +1.5% 고정을 유지한다.
- 조정 후 거래바닥 양봉 패턴은 선행상승 구간 안의 고거래 장대양봉을 필수로 확인한다.
- 거래량이 마른 짧은 양봉 또는 아래꼬리 짧은 음봉에서 즉시 진입한다.
- 같은 형태가 여러 번 출현하면 지지확인 횟수를 신뢰도에 반영한다.
- GUI 종목 줄을 더블클릭하면 바로 영웅문 종목을 전환한다.
- 5분 콘솔 목록은 고정 폭 열로 정렬한다.
- PAPER 전용이며 실제 주문 함수는 없다.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import os
import queue
import re
import shutil
import sys
import threading
import time
import traceback
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any, Sequence

VERSION = "V75.25-IMPULSE-SUPPORT-DOUBLECLICK-PAPER"
BASE_DIR = Path(__file__).resolve().parent
PACKAGE_DIR = BASE_DIR.parent
V7524_FILENAME = "bot_v75_24_1_hero_relative_click_fallback_paper.py"
V7524_SHA256 = "40f1ac1705298bd8be90afbb1e6ad2b6a7110132ae7fb42d3d45b42f4488a3ac"
RUNTIME_DIR = PACKAGE_DIR / "v75_25_runtime"
DAILY_LOG_DIR = PACKAGE_DIR / "v75_25_daily_logs"
COMMAND_FILE = PACKAGE_DIR / "장중명령.txt"
# 버전이 바뀌어도 한 번 저장한 영웅문 위치를 계속 사용한다.
HERO_LINK_FILE = Path.home() / ".v75_hero_input_position.json"
FIXED_TAKE_PROFIT_PCT = 0.015

IMPULSE_LOOKBACK_BARS = 30
IMPULSE_MIN_BODY_RATIO = 0.45
IMPULSE_MIN_CLOSE_LOCATION = 0.70
IMPULSE_MAX_UPPER_WICK = 0.25
IMPULSE_MIN_PRICE_GAIN = 0.003
IMPULSE_VOLUME_VS_MEDIAN = 1.35
IMPULSE_VOLUME_VS_P75 = 1.05
SUPPORT_LOOKBACK_BARS = 8
SUPPORT_EXTRA_SCORE = 5.0
SUPPORT_EXTRA_SCORE_CAP = 15.0
FORBIDDEN_ORDER_NAMES = {"send_order", "create_order", "place_order", "order_stock", "submit_order"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_v7524() -> ModuleType:
    path = BASE_DIR / V7524_FILENAME
    if not path.exists():
        raise RuntimeError(f"{V7524_FILENAME}이 같은 폴더에 없습니다.")
    actual = sha256_file(path)
    if actual.lower() != V7524_SHA256:
        raise RuntimeError(
            "V75.24.1 SHA256 불일치—기준본이 아니므로 중단합니다.\n"
            f"기대: {V7524_SHA256}\n실제: {actual}"
        )
    spec = importlib.util.spec_from_file_location("v7524_immutable_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("V75.24.1 모듈 로더를 만들 수 없습니다.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


v7524 = load_v7524()
BASELINE_V7524_SELF_CHECK = v7524.self_check()
v7523 = v7524.v7523
v7522 = v7524.v7522
v7521 = v7524.v7521
v7520 = v7524.v7520
v7518 = v7524.v7518
v7517 = v7524.v7517
v7516 = v7524.v7516
v7515 = v7524.v7515
v7514 = v7524.v7514
v756 = v7515.v756
v754 = v7515.v754
v753 = v7524.v753
KST = v7524.KST
STAGE_ORDER = v7523.STAGE_ORDER

# 버전별 PAPER 상태·로그 분리
v753.LOG_DIR = RUNTIME_DIR
v753.STATE_FILE = RUNTIME_DIR / "v75_25_paper_state.json"
v753.FILL_FILE = RUNTIME_DIR / "v75_25_paper_fills.csv"
v753.PATTERN_STATE_FILE = RUNTIME_DIR / "v75_25_pattern_state.json"
v753.ERROR_FILE = RUNTIME_DIR / "v75_25_api_errors.txt"
v753.RANK_SCHEMA_FILE = RUNTIME_DIR / "v75_25_rank_schema_probe.json"
v7517.RUNTIME_DIR = RUNTIME_DIR
v7517.DAILY_LOG_DIR = DAILY_LOG_DIR
v7517.COMMAND_FILE = COMMAND_FILE
v7517.COMMAND_STATE_FILE = RUNTIME_DIR / "v75_25_command_state.json"
v7516.RUNTIME_DIR = RUNTIME_DIR
v7516.DAILY_LOG_DIR = DAILY_LOG_DIR
v7515.RUNTIME_DIR = RUNTIME_DIR
v7515.DAILY_LOG_DIR = DAILY_LOG_DIR
v7515.POTENTIAL_FILE = RUNTIME_DIR / "v75_25_supply_potential.json"
v7514.RUNTIME_DIR = RUNTIME_DIR
v7514.DAILY_LOG_DIR = DAILY_LOG_DIR
v7514.POTENTIAL_FILE = v7515.POTENTIAL_FILE

_ORIGINAL_FIND_HEAVY = v7515.find_heavy_structures
_ORIGINAL_DETECT = v7515.detect_precision_supply
_ORIGINAL_ASSESS = v7515.assess_precision_watch


def _prior_impulse(
    bars: Sequence[Any], structure: Any, cfg: Any
) -> dict[str, float | int] | None:
    """대량 음봉 전에 실제 상승을 만든 고거래 장대양봉을 찾는다."""
    anchor = int(structure.anchor_index)
    start = max(0, anchor - min(IMPULSE_LOOKBACK_BARS, int(cfg.rise_lookback_bars)))
    best: dict[str, float | int] | None = None
    for index in range(start, anchor):
        bar = bars[index]
        span, signed, body, close_location, _, upper_wick = v7515.candle_metrics(bar)
        del span
        if signed <= 0 or body < IMPULSE_MIN_BODY_RATIO:
            continue
        if close_location < IMPULSE_MIN_CLOSE_LOCATION or upper_wick > IMPULSE_MAX_UPPER_WICK:
            continue
        gain = float(bar.close) / max(0.01, float(bar.open)) - 1.0
        if gain < IMPULSE_MIN_PRICE_GAIN:
            continue
        left_count = min(int(cfg.max_left_bars), index)
        if left_count < int(cfg.min_left_bars):
            continue
        left = list(bars[index - left_count:index])
        volumes = [max(0.0, float(item.volume)) for item in left]
        median = float(v7515.statistics.median(volumes))
        p75 = float(v7515.percentile75(volumes))
        volume = max(0.0, float(bar.volume))
        vs_median = volume / max(1.0, median)
        vs_p75 = volume / max(1.0, p75)
        if vs_median < IMPULSE_VOLUME_VS_MEDIAN or vs_p75 < IMPULSE_VOLUME_VS_P75:
            continue
        score = body * 30.0 + close_location * 20.0 + min(vs_median, 4.0) * 10.0 + gain * 500.0
        candidate: dict[str, float | int] = {
            "index": index,
            "body": body,
            "close_location": close_location,
            "upper_wick": upper_wick,
            "gain": gain,
            "volume_vs_median": vs_median,
            "volume_vs_p75": vs_p75,
            "score": score,
        }
        if best is None or float(candidate["score"]) > float(best["score"]):
            best = candidate
    return best


def find_qualified_heavy_structures(bars: Sequence[Any], cfg: Any) -> list[Any]:
    structures = _ORIGINAL_FIND_HEAVY(bars, cfg)
    return [structure for structure in structures if _prior_impulse(bars, structure, cfg) is not None]


def _matching_structure(bars: Sequence[Any], evidence: Any, cfg: Any) -> Any | None:
    structures = find_qualified_heavy_structures(bars, cfg)
    flush_time = getattr(evidence, "flush_time", None)
    for structure in structures:
        bar_time = v754.normalize_kst(bars[int(structure.last_heavy_index)].timestamp)
        if flush_time is not None and bar_time == flush_time:
            return structure
    return structures[0] if structures else None


def _support_count(bars: Sequence[Any], structure: Any, cfg: Any) -> int:
    """대량 매도 뒤 거래가 마른 짧은 양봉/아래꼬리 음봉의 반복 횟수."""
    start = int(structure.last_heavy_index) + 1
    post = list(bars[start:])[-SUPPORT_LOOKBACK_BARS:]
    count = 0
    for bar in post:
        span, signed, _, close_location, lower_wick, _ = v7515.candle_metrics(bar)
        dry = (
            float(bar.volume) <= float(structure.heavy_volume) * float(cfg.dry_max_volume_vs_heavy_cluster)
            and float(bar.volume) <= float(structure.context_volume) * float(cfg.dry_max_volume_vs_context)
        )
        short = span <= float(structure.heavy_range) * float(cfg.short_max_range_vs_heavy)
        confirmation = signed > 0 or (
            signed <= 0
            and lower_wick >= float(cfg.lower_wick_min_ratio)
            and close_location >= float(cfg.bearish_close_min_location)
        )
        if dry and short and confirmation:
            count += 1
    return count


def detect_v7525(strategy: Any, symbol: str, *, config: Any) -> tuple[Any | None, str, Any | None]:
    signal, state, evidence = _ORIGINAL_DETECT(strategy, symbol, config=config)
    if signal is None or evidence is None:
        return signal, state, evidence
    cfg = v7515.PrecisionConfig.load()
    bars = list(v756._same_session_bars(strategy.bars.get(symbol, [])))
    structure = _matching_structure(bars, evidence, cfg)
    if structure is None:
        return None, "선행매수장대양봉없음", None
    impulse = _prior_impulse(bars, structure, cfg)
    if impulse is None:
        return None, "선행매수장대양봉없음", None
    supports = max(1, _support_count(bars, structure, cfg))
    extra_score = min(SUPPORT_EXTRA_SCORE_CAP, max(0, supports - 1) * SUPPORT_EXTRA_SCORE)
    reason = re.sub(r"\|손절=[^|]+", "", str(signal.reason))
    reason = re.sub(r"\|목표=[^|]+", "", reason)
    reason += (
        f"|선행매수양봉=몸통{float(impulse['body']):.0%}/"
        f"거래x{float(impulse['volume_vs_median']):.2f}"
        f"|지지확인봉={supports}|자동손절없음|목표=1.5%고정"
    )
    fixed = v753.Signal(
        signal.symbol,
        signal.action,
        float(signal.score) + extra_score,
        reason,
        float(signal.reference_price),
        signal.timestamp,
        str(signal.pattern),
        float(signal.stop_loss_pct),  # V75.23 청산기에서 사용하지 않음
        FIXED_TAKE_PROFIT_PCT,
    )
    return fixed, state, evidence


def assess_v7525(
    strategy: Any,
    symbol: str,
    *,
    loose: Any,
    strict: Any,
    now: datetime,
    name: str,
    source: str,
) -> tuple[Any | None, str]:
    item, state = _ORIGINAL_ASSESS(
        strategy, symbol, loose=loose, strict=strict, now=now, name=name, source=source
    )
    if item is None:
        return None, state
    return replace(item, source=f"{source}|선행매수장대양봉"), state


# 상속 경로 전체가 보강 탐지기를 사용하도록 교체
v7515.find_heavy_structures = find_qualified_heavy_structures
v7515.detect_precision_supply = detect_v7525
v7515.assess_precision_watch = assess_v7525
v756.detect_supply_exhaustion_pullback = detect_v7525
v7514.assess_loose_supply_potential = assess_v7525


def _concise_reason(value: Any) -> str:
    text = " ".join(str(value or "").split())
    replacements = (
        ("거래량60%감소대기", "거래감소 대기"),
        ("짧은양봉/아래꼬리음봉대기", "반전봉 대기"),
        ("짧은양봉/아래꼬리음봉없음", "반전봉 대기"),
        ("대량음봉묶음발견", "대량매도 확인"),
        ("관심종목-매수가능성", "거래감소 확인"),
        ("즉시매수조건근접", "매수직전"),
    )
    for old, new in replacements:
        text = text.replace(old, new)
    return text[:56]


class SimpleDoubleClickGui:
    """설정 후에는 종목 줄 더블클릭만으로 영웅문 종목을 전환한다."""

    def __init__(self, logger: Any) -> None:
        self.logger = logger
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=4)
        self.closed = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, name="v7525-gui", daemon=True)
        self.thread.start()

    def publish(self, snapshot: dict[str, Any]) -> None:
        while True:
            try:
                self.events.put_nowait(("snapshot", snapshot))
                return
            except queue.Full:
                try:
                    self.events.get_nowait()
                except queue.Empty:
                    return

    def close(self) -> None:
        self.closed.set()
        try:
            self.events.put_nowait(("close", None))
        except queue.Full:
            pass

    def _run(self) -> None:
        try:
            import tkinter as tk
            from tkinter import messagebox, ttk
            root = tk.Tk()
        except Exception as exc:
            self.logger.warning("GUI 시작 실패: %s", exc)
            return

        root.title("V75.25 관심·후보·매수·매도 — 더블클릭 영웅문 연동")
        root.geometry("1160x650")
        root.minsize(980, 500)
        hero = v7524.WindowsHeroRelativeLink(HERO_LINK_FILE)

        toolbar = ttk.Frame(root, padding=(8, 8, 8, 4))
        toolbar.pack(fill="x")
        status_var = tk.StringVar(
            value=("종목 줄을 더블클릭하세요." if hero.configured else "처음 한 번만 영웅문 입력위치를 저장하세요.")
        )
        time_var = tk.StringVar(value="갱신 전")
        ttk.Button(toolbar, text="처음 1회: 영웅문 위치 저장", command=lambda: begin_capture()).pack(side="left")
        ttk.Label(toolbar, text="  종목 줄 더블클릭 → 영웅문 전환").pack(side="left")
        ttk.Label(toolbar, textvariable=time_var).pack(side="right")

        columns = ("stage", "time", "name", "symbol", "price", "reason")
        tree = ttk.Treeview(root, columns=columns, show="headings", selectmode="browse")
        headings = {
            "stage": "단계", "time": "시각", "name": "종목명", "symbol": "코드",
            "price": "현재가/매도가", "reason": "이유",
        }
        for key, text in headings.items():
            tree.heading(key, text=text)
        tree.column("stage", width=95, anchor="center", stretch=False)
        tree.column("time", width=85, anchor="center", stretch=False)
        tree.column("name", width=210, anchor="w")
        tree.column("symbol", width=80, anchor="center", stretch=False)
        tree.column("price", width=120, anchor="e", stretch=False)
        tree.column("reason", width=530, anchor="w")
        scroll_y = ttk.Scrollbar(root, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll_y.set)
        scroll_y.pack(side="right", fill="y")
        tree.pack(fill="both", expand=True, padx=(8, 0), pady=(4, 4))
        ttk.Label(root, textvariable=status_var, anchor="w", padding=(8, 4, 8, 8)).pack(fill="x")

        def copy_symbol(symbol: str) -> None:
            root.clipboard_clear()
            root.clipboard_append(symbol)
            root.update_idletasks()

        def switch_symbol(symbol: str) -> None:
            if not symbol:
                status_var.set("종목을 찾지 못했습니다.")
                return
            copy_symbol(symbol)
            ok, message = hero.send_symbol(symbol)
            status_var.set(message if ok else f"자동전환 실패 · {symbol} 복사됨")

        def on_double_click(event: Any) -> None:
            item_id = tree.identify_row(event.y)
            if not item_id:
                return
            tree.selection_set(item_id)
            tree.focus(item_id)
            values = tree.item(item_id, "values")
            symbol = str(values[3]).strip() if len(values) >= 4 else ""
            switch_symbol(symbol)

        def finish_capture() -> None:
            ok, message = hero.capture_cursor_target()
            status_var.set(message if ok else f"위치 저장 실패: {message}")

        def countdown(remaining: int) -> None:
            if remaining <= 0:
                finish_capture()
                return
            status_var.set(f"{remaining}초 안에 영웅문 종목코드 입력칸을 한 번 클릭하고 그대로 두세요.")
            root.after(1000, lambda: countdown(remaining - 1))

        def begin_capture() -> None:
            messagebox.showinfo(
                "영웅문 위치 저장",
                "확인을 누른 뒤 5초 안에 영웅문 종목코드 입력칸을 한 번 클릭하세요.\n"
                "클릭한 뒤 마우스를 움직이지 말고 기다리세요.",
            )
            countdown(5)

        def apply_snapshot(snapshot: dict[str, Any]) -> None:
            selected = tree.selection()
            current_symbol = ""
            if selected:
                values = tree.item(selected[0], "values")
                current_symbol = str(values[3]).strip() if len(values) >= 4 else ""
            for item_id in tree.get_children():
                tree.delete(item_id)
            selected_id = ""
            rows = list(snapshot.get("rows", []))
            for index, row in enumerate(rows):
                item_id = tree.insert("", "end", values=(
                    row.get("stage", ""), row.get("time", ""), row.get("name", ""),
                    row.get("symbol", ""), row.get("price", ""), _concise_reason(row.get("reason", "")),
                ))
                if current_symbol and row.get("symbol") == current_symbol:
                    selected_id = item_id
                elif index == 0 and not selected_id:
                    selected_id = item_id
            if selected_id:
                tree.selection_set(selected_id)
                tree.focus(selected_id)
            time_var.set(str(snapshot.get("updated", "")))
            status_var.set(f"시장 {snapshot.get('market', '-')} | {len(rows)}종목 | 종목 줄을 더블클릭하세요")

        def poll_events() -> None:
            try:
                while True:
                    kind, payload = self.events.get_nowait()
                    if kind == "snapshot":
                        apply_snapshot(payload)
                    elif kind == "close":
                        root.destroy()
                        return
            except queue.Empty:
                pass
            if not self.closed.is_set():
                root.after(300, poll_events)

        tree.bind("<Double-1>", on_double_click)
        root.protocol("WM_DELETE_WINDOW", lambda: root.withdraw())
        root.after(200, poll_events)
        root.mainloop()


class V7525Engine(v7524.HeroRelativeEngine):
    def _compact_report(self) -> None:
        self._sync_daily_state()
        self._enforce_fixed_targets()
        snapshot = self._gui_snapshot()
        if self.gui_bridge is not None:
            try:
                self.gui_bridge.publish(snapshot)
            except Exception as exc:
                self.logger.warning("GUI 목록 갱신 실패: %s", exc)

        now = datetime.now(KST)
        state = str(getattr(self._market_snapshot, "state", "DATA_HALT"))
        entry = "가능" if state in {"NORMAL", "RECOVERY_READY"} and not self.manual_new_entries_paused else "보류"
        rows = list(snapshot.get("rows", []))
        grouped = {stage: [row for row in rows if row.get("stage") == stage] for stage in STAGE_ORDER}

        prices = {symbol: quote.price for symbol, quote in self.latest_quotes.items()}
        estimated_pnl = 0.0
        liquidation_value = float(self.broker.cash)
        for symbol, position in self.broker.positions.items():
            current = float(prices.get(symbol, position.entry_price))
            gross = current * int(position.quantity)
            proceeds = gross * (1.0 - self.settings.commission_pct - self.settings.sell_tax_pct)
            estimated_pnl += proceeds - float(position.entry_cost)
            liquidation_value += proceeds

        width = 126
        print("\n" + "=" * width)
        print(
            f"[{now:%H:%M}] 5분 요약  시장={v7518.pad(v7518.market_label_short(state), 12)}  신규매수={entry:<2}  "
            f"관심={len(grouped['관심종목']):>3}  관찰={len(grouped['관찰후보']):>3}  "
            f"가능={len(grouped['매수가능성']):>3}  직전={len(grouped['매수직전']):>3}  "
            f"보류={len(grouped['시장보류']):>3}  보유={len(grouped['매수종목']):>2}"
        )
        print(
            f"평가자산={liquidation_value:>15,.0f}원  현금={float(self.broker.cash):>15,.0f}원  "
            f"실현={float(self.broker.realized_pnl):>+12,.0f}원  평가={estimated_pnl:>+12,.0f}원"
        )

        limits = {"관심종목": 15, "관찰후보": 12, "매수가능성": 12, "매수직전": 10, "시장보류": 10}
        for stage in ("관심종목", "관찰후보", "매수가능성", "매수직전", "시장보류"):
            stage_rows = grouped[stage]
            print("-" * width)
            print(f"[{stage}] {len(stage_rows)}종목")
            if not stage_rows:
                print("  없음")
                continue
            print(
                "  " + v7518.pad("시각", 8) + " " + v7518.pad("종목명", 20) + " "
                + v7518.pad("코드", 6) + " " + v7518.pad("현재가", 12, right=True) + "  이유"
            )
            for row in stage_rows[:limits[stage]]:
                print(
                    "  " + v7518.pad(row.get("time", ""), 8) + " "
                    + v7518.pad(row.get("name", ""), 20) + " "
                    + v7518.pad(row.get("symbol", ""), 6) + " "
                    + v7518.pad(row.get("price", ""), 12, right=True) + "  "
                    + _concise_reason(row.get("reason", ""))
                )
            hidden = len(stage_rows) - limits[stage]
            if hidden > 0:
                print(f"  ... 나머지 {hidden}종목은 GUI·상세로그")

        print("-" * width)
        print(f"[매수종목] {len(grouped['매수종목'])}종목  목표=+1.5% 고정  자동손절=없음")
        if grouped["매수종목"]:
            print(
                "  " + v7518.pad("매수시각", 8) + " " + v7518.pad("종목명", 20) + " "
                + v7518.pad("코드", 6) + " " + v7518.pad("현재가", 12, right=True) + "  상태"
            )
            for row in grouped["매수종목"]:
                print(
                    "  " + v7518.pad(row.get("time", ""), 8) + " "
                    + v7518.pad(row.get("name", ""), 20) + " "
                    + v7518.pad(row.get("symbol", ""), 6) + " "
                    + v7518.pad(row.get("price", ""), 12, right=True) + "  "
                    + _concise_reason(row.get("reason", ""))
                )
        else:
            print("  없음")

        print(f"[오늘 매도종목] {len(grouped['매도종목'])}건")
        if grouped["매도종목"]:
            print(
                "  " + v7518.pad("매도시각", 8) + " " + v7518.pad("종목명", 20) + " "
                + v7518.pad("코드", 6) + " " + v7518.pad("매도가", 12, right=True) + "  결과"
            )
            for row in grouped["매도종목"][-10:]:
                print(
                    "  " + v7518.pad(row.get("time", ""), 8) + " "
                    + v7518.pad(row.get("name", ""), 20) + " "
                    + v7518.pad(row.get("symbol", ""), 6) + " "
                    + v7518.pad(row.get("price", ""), 12, right=True) + "  "
                    + _concise_reason(row.get("reason", ""))
                )
        else:
            print("  없음")
        print("=" * width + "\n")
        self.save_pattern_state()


def install_daily_tee() -> Any:
    DAILY_LOG_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(KST)
    path = DAILY_LOG_DIR / f"V75_25_{now:%Y-%m-%d}.txt"
    handle = path.open("a", encoding="utf-8", buffering=1)
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    stdout_tee = v7520.SafeTeeTextIO(original_stdout, handle)
    stderr_tee = v7520.SafeTeeTextIO(original_stderr, handle)
    sys.stdout = stdout_tee  # type: ignore[assignment]
    sys.stderr = stderr_tee  # type: ignore[assignment]
    print("=" * 126)
    print(f"[{now:%Y-%m-%d %H:%M:%S}] {VERSION}")
    print(f"장중명령={COMMAND_FILE.name}  상세로그={path}  GUI=종목 줄 더블클릭")
    print("=" * 126)
    return v7520.SafeDailyTee(path, handle, original_stdout, original_stderr, stdout_tee, stderr_tee)


def _strategy_with_impulse(kind: str = "quiet", *, remove_impulse: bool = False) -> tuple[Any, str]:
    strategy, symbol = v7515.synthetic_case(kind)
    bars = list(strategy.bars[symbol])
    if remove_impulse:
        bar = bars[24]
        bars[24] = v753.Bar(
            bar.symbol, bar.timestamp, 102.95, 103.35, 102.85, 103.10, 1050
        )
    else:
        bar = bars[24]
        bars[24] = v753.Bar(
            bar.symbol, bar.timestamp, 101.55, 103.55, 101.45, 103.30, 2300
        )
    rebuilt = v753.MomentumVolumeStrategy(v753.Settings.load())
    for item in bars:
        rebuilt.on_bar(item, None)
    return rebuilt, symbol


def self_check() -> list[tuple[str, bool, str]]:
    baseline_failed = [name for name, ok, _ in BASELINE_V7524_SELF_CHECK if not ok]
    results: list[tuple[str, bool, str]] = [
        ("V75.24.1기존검증", not baseline_failed, f"{len(BASELINE_V7524_SELF_CHECK)}항목 / 실패={baseline_failed or '없음'}")
    ]

    def record(name: str, ok: bool, detail: str) -> None:
        results.append((name, bool(ok), detail))

    record("V75.24.1기준본SHA", sha256_file(BASE_DIR / V7524_FILENAME) == V7524_SHA256, V7524_SHA256[:16])
    good, symbol = _strategy_with_impulse("quiet")
    good_signal, _, _ = detect_v7525(good, symbol, config=None)
    record(
        "선행매수장대양봉필수통과",
        good_signal is not None and "선행매수양봉=" in str(good_signal.reason),
        str(good_signal.reason) if good_signal else "미탐지",
    )
    flat, symbol = _strategy_with_impulse("quiet", remove_impulse=True)
    flat_signal, flat_state, _ = detect_v7525(flat, symbol, config=None)
    record("누운조정대량음봉차단", flat_signal is None, flat_state)
    record(
        "복수지지확인반영",
        good_signal is not None and "지지확인봉=" in str(good_signal.reason),
        str(good_signal.reason) if good_signal else "미탐지",
    )
    wick, symbol = _strategy_with_impulse("wick")
    wick_signal, _, _ = detect_v7525(wick, symbol, config=None)
    record("아래꼬리음봉즉시매수유지", wick_signal is not None, str(wick_signal.reason) if wick_signal else "미탐지")
    record(
        "목표1.5고정",
        good_signal is not None and abs(float(good_signal.take_profit_pct) - FIXED_TAKE_PROFIT_PCT) < 1e-12,
        f"{float(good_signal.take_profit_pct):.2%}" if good_signal else "미탐지",
    )
    record("자동손절없음상속", issubclass(V7525Engine, v7523.NoStopTimedHeroEngine), "V75.23 청산기 상속")
    source = Path(__file__).read_text(encoding="utf-8")
    record("더블클릭직접전환", all(token in source for token in ("identify_row(event.y)", "tree.bind(\"<Double-1>\"", "switch_symbol(symbol)")), "한 번의 더블클릭")
    record("콘솔고정폭정렬", all(token in source for token in ("v7518.pad(\"종목명\", 20)", "v7518.pad(\"코드\", 6)", "v7518.pad(\"현재가\", 12")), "종목명/코드/현재가")
    tree = ast.parse(source)
    names = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    record("실주문함수부재", not (names & FORBIDDEN_ORDER_NAMES), str(sorted(names & FORBIDDEN_ORDER_NAMES)))
    return results


def main() -> int:
    v753.configure_console()
    loaded = v753.load_environment_files()
    parser = v7515.build_parser()
    parser.add_argument("--no-gui", action="store_true", help="GUI 없이 콘솔만 실행")
    args = parser.parse_args()
    tee = install_daily_tee()
    logger = v753.setup_logging(args.verbose)
    v7514.add_daily_detail_handler(logger, tee.path, verbose=args.verbose)
    v7518.add_daily_warning_handler(logger, tee.path)
    v7518.install_quiet_console_filter(logger)
    exit_code = 9
    gui: SimpleDoubleClickGui | None = None
    try:
        settings = replace(v753.Settings.load(), report_interval_sec=v7520.REPORT_INTERVAL_SEC)
        logger.info("환경파일 자동탐색=%s", [str(path) for path in loaded] or "없음")
        if args.self_check:
            failed = False
            for name, ok, detail in self_check():
                print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
                failed = failed or not ok
            exit_code = 1 if failed else 0
            return exit_code

        newly_saved = v753.ensure_credentials(logger)
        if newly_saved:
            settings = replace(v753.Settings.load(), report_interval_sec=v7520.REPORT_INTERVAL_SEC)
        settings.assert_credentials()
        if args.quote_test:
            exit_code = v753.quote_test(settings, logger, args.symbol)
            return exit_code
        if args.scan_test:
            exit_code = v753.scan_test(settings, logger)
            return exit_code

        client = v753.KiwoomRestClient(settings, logger)
        first = client.get_quote(settings.watchlist[0][0])
        print(
            f"[시작] PAPER | 평가자산 10억원 | 종목당 최대 1천만원 | 목표 +1.5% 고정 | "
            f"자동손절 없음 | 선행매수장대양봉 필수 | 기준종목 {first.symbol} {first.price:,.0f}원"
        )
        if not args.no_gui:
            gui = SimpleDoubleClickGui(logger)
            gui.start()
        engine_logger = v7515.v7591.CommaSafeLoggerAdapter(logger, {})
        engine = V7525Engine(
            settings,
            client,
            engine_logger,
            supply_only=args.supply_only,
            reset_paper=args.reset_paper,
            reset_v754_safety=args.reset_v754_safety,
            gui_bridge=gui,
        )
        engine.latest_quotes[first.symbol] = first
        engine.run(minutes=args.minutes if args.minutes > 0 else None)
        exit_code = 0
        return exit_code
    except v753.ConfigurationError as exc:
        logger.critical("설정 오류: %s", exc)
        exit_code = 2
        return exit_code
    except v753.AuthenticationError as exc:
        logger.critical("인증 오류: %s", exc)
        exit_code = 3
        return exit_code
    except v753.V73Error as exc:
        logger.critical("실행 차단: %s", exc)
        exit_code = 4
        return exit_code
    except Exception as exc:
        logger.critical("예상 밖 치명 오류: %s", exc)
        v753.append_api_error(traceback.format_exc())
        exit_code = 9
        return exit_code
    finally:
        if gui is not None:
            gui.close()
        print(f"[{datetime.now(KST):%Y-%m-%d %H:%M:%S}] {VERSION} 종료코드={exit_code}")
        tee.detach_logger_streams(logger)
        tee.close()


if __name__ == "__main__":
    raise SystemExit(main())
