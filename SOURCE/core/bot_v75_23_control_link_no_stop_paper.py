#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V75.23 PAPER
- V75.22의 매물소진 전용 로직, 5분 후보 GUI, 장중명령을 유지한다.
- 영웅문 연동은 마우스 좌표가 아니라 사용자가 실제로 클릭한 활성 입력 컨트롤을 등록한다.
- 관심/후보는 해당 단계에 처음 들어온 시각, 매수는 체결 시각, 매도는 체결 시각을 표시한다.
- 자동 손절·추적손절·손익보호 청산은 사용하지 않고 +1.5% 예약목표만 자동 청산한다.
- PAPER 전용이며 실제 주문 함수는 없다.
"""
from __future__ import annotations

import ast
import csv
import ctypes
import hashlib
import importlib.util
import json
import os
import queue
import sys
import threading
import time
import traceback
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any

VERSION = "V75.23-CONTROL-LINK-NO-STOP-PAPER"
BASE_DIR = Path(__file__).resolve().parent
V7522_FILENAME = "bot_v75_22_hero_gui_link_paper.py"
V7522_SHA256 = "a12c3f433d16883ff383f6373d62a2b498855fed327dcc20c2496e924cd02b2a"
RUNTIME_DIR = BASE_DIR / "v75_23_runtime"
DAILY_LOG_DIR = BASE_DIR / "v75_23_daily_logs"
COMMAND_FILE = BASE_DIR / "장중명령.txt"
HERO_LINK_FILE = BASE_DIR / "영웅문입력칸설정.json"
FORBIDDEN_ORDER_NAMES = {"send_order", "create_order", "place_order", "order_stock", "submit_order"}
STAGE_ORDER = (
    "관심종목", "관찰후보", "매수가능성", "매수직전", "시장보류", "매수종목", "매도종목"
)
STOP_COMMANDS = {"손절", "손절가격", "추적손절", "추적손절해제", "손익보호"}
TARGET_CHANGE_COMMANDS = {"목표가", "목표가격", "목표가해제", "목표가활성"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_v7522() -> ModuleType:
    path = BASE_DIR / V7522_FILENAME
    if not path.exists():
        raise RuntimeError(f"{V7522_FILENAME}이 같은 폴더에 없습니다.")
    actual = sha256_file(path)
    if actual.lower() != V7522_SHA256:
        raise RuntimeError(
            "V75.22 SHA256 불일치—검증된 기준본이 아니므로 중단합니다.\n"
            f"기대: {V7522_SHA256}\n실제: {actual}"
        )
    spec = importlib.util.spec_from_file_location("v7522_immutable_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("V75.22 모듈 로더를 만들 수 없습니다.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


v7522 = load_v7522()
BASELINE_V7522_SELF_CHECK = v7522.self_check()
v7521 = v7522.v7521
v7520 = v7522.v7520
v7518 = v7522.v7518
v7517 = v7522.v7517
v7516 = v7522.v7516
v7515 = v7522.v7515
v7514 = v7522.v7514
v753 = v7522.v753
KST = v7522.KST

# 버전별 PAPER 상태·로그를 분리한다.
v753.LOG_DIR = RUNTIME_DIR
v753.STATE_FILE = RUNTIME_DIR / "v75_23_paper_state.json"
v753.FILL_FILE = RUNTIME_DIR / "v75_23_paper_fills.csv"
v753.PATTERN_STATE_FILE = RUNTIME_DIR / "v75_23_pattern_state.json"
v753.ERROR_FILE = RUNTIME_DIR / "v75_23_api_errors.txt"
v753.RANK_SCHEMA_FILE = RUNTIME_DIR / "v75_23_rank_schema_probe.json"
v7517.RUNTIME_DIR = RUNTIME_DIR
v7517.DAILY_LOG_DIR = DAILY_LOG_DIR
v7517.COMMAND_FILE = COMMAND_FILE
v7517.COMMAND_STATE_FILE = RUNTIME_DIR / "v75_23_command_state.json"
v7516.RUNTIME_DIR = RUNTIME_DIR
v7516.DAILY_LOG_DIR = DAILY_LOG_DIR
v7515.RUNTIME_DIR = RUNTIME_DIR
v7515.DAILY_LOG_DIR = DAILY_LOG_DIR
v7515.POTENTIAL_FILE = RUNTIME_DIR / "v75_23_supply_potential.json"
v7514.RUNTIME_DIR = RUNTIME_DIR
v7514.DAILY_LOG_DIR = DAILY_LOG_DIR
v7514.POTENTIAL_FILE = v7515.POTENTIAL_FILE


def _safe_text(value: Any, limit: int = 100) -> str:
    return " ".join(str(value or "").split())[:limit]


def _symbol_name(label: str, symbol: str) -> str:
    text = str(label or "").strip()
    suffix = f"({symbol})"
    if text.endswith(suffix):
        text = text[: -len(suffix)].strip()
    return text or symbol


def _price_text(value: float) -> str:
    return f"{value:,.0f}원" if value > 0 else "조회대기"


def _clock(value: datetime | None) -> str:
    if value is None:
        return "--:--:--"
    return value.astimezone(KST).strftime("%H:%M:%S")


def _window_title_matches(saved: str, current: str) -> bool:
    saved_norm = " ".join(saved.split()).lower()
    current_norm = " ".join(current.split()).lower()
    return bool(saved_norm and current_norm) and (
        saved_norm == current_norm or saved_norm in current_norm or current_norm in saved_norm
    )


class WindowsHeroControlLink:
    """사용자가 실제로 클릭한 영웅문 입력 컨트롤을 저장하고 직접 키 입력한다."""

    GW_CHILD = 5
    GW_HWNDNEXT = 2
    GA_ROOT = 2
    SW_RESTORE = 9
    VK_CONTROL = 0x11
    VK_RETURN = 0x0D
    VK_A = 0x41
    WM_SETTEXT = 0x000C
    WM_KEYDOWN = 0x0100
    WM_KEYUP = 0x0101

    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self.config = self._load()

    @property
    def available(self) -> bool:
        return os.name == "nt" and hasattr(ctypes, "windll")

    @property
    def configured(self) -> bool:
        return bool(self.config.get("window_title") and self.config.get("control_path"))

    def _load(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def _save(self) -> None:
        self.config_path.write_text(
            json.dumps(self.config, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @staticmethod
    def _class_name(hwnd: int) -> str:
        buffer = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetClassNameW(hwnd, buffer, len(buffer))
        return buffer.value

    @staticmethod
    def _window_text(hwnd: int) -> str:
        user32 = ctypes.windll.user32
        length = user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(max(2, length + 1))
        user32.GetWindowTextW(hwnd, buffer, len(buffer))
        return buffer.value.strip()

    @classmethod
    def _direct_children(cls, parent: int) -> list[int]:
        user32 = ctypes.windll.user32
        children: list[int] = []
        child = int(user32.GetWindow(parent, cls.GW_CHILD) or 0)
        while child:
            children.append(child)
            child = int(user32.GetWindow(child, cls.GW_HWNDNEXT) or 0)
        return children

    @classmethod
    def _build_control_path(cls, root: int, control: int) -> list[dict[str, Any]]:
        user32 = ctypes.windll.user32
        chain: list[int] = []
        node = int(control)
        while node and node != root:
            chain.append(node)
            node = int(user32.GetParent(node) or 0)
        if node != root:
            return []
        chain.reverse()
        path: list[dict[str, Any]] = []
        parent = root
        for child in chain:
            class_name = cls._class_name(child)
            same_class = [x for x in cls._direct_children(parent) if cls._class_name(x) == class_name]
            try:
                class_index = same_class.index(child)
            except ValueError:
                class_index = 0
            path.append({
                "class": class_name,
                "class_index": class_index,
                "control_id": int(user32.GetDlgCtrlID(child)),
            })
            parent = child
        return path

    @classmethod
    def _resolve_control(cls, root: int, path: list[dict[str, Any]]) -> int:
        user32 = ctypes.windll.user32
        parent = root
        for part in path:
            class_name = str(part.get("class", ""))
            wanted_id = int(part.get("control_id", 0))
            same_class = [x for x in cls._direct_children(parent) if cls._class_name(x) == class_name]
            chosen = 0
            if wanted_id:
                chosen = next((x for x in same_class if int(user32.GetDlgCtrlID(x)) == wanted_id), 0)
            if not chosen:
                index = int(part.get("class_index", 0))
                if 0 <= index < len(same_class):
                    chosen = same_class[index]
            if not chosen:
                return 0
            parent = chosen
        return int(parent)

    def capture_focused_control(self) -> tuple[bool, str]:
        if not self.available:
            return False, "Windows에서만 설정할 수 있습니다."
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        root = int(user32.GetForegroundWindow() or 0)
        if not root:
            return False, "현재 활성 창을 찾지 못했습니다."
        foreground_thread = int(user32.GetWindowThreadProcessId(root, None))
        current_thread = int(kernel32.GetCurrentThreadId())
        attached = False
        try:
            if foreground_thread and foreground_thread != current_thread:
                attached = bool(user32.AttachThreadInput(current_thread, foreground_thread, True))
            control = int(user32.GetFocus() or 0)
        finally:
            if attached:
                user32.AttachThreadInput(current_thread, foreground_thread, False)
        if not control or control == root:
            return False, "종목코드 입력칸이 활성화되지 않았습니다. 입력칸을 직접 클릭한 뒤 다시 등록하세요."
        actual_root = int(user32.GetAncestor(control, self.GA_ROOT) or root)
        title = self._window_text(actual_root)
        path = self._build_control_path(actual_root, control)
        if not title or not path:
            return False, "활성 입력칸의 창 구조를 저장하지 못했습니다."
        self.config = {
            "window_title": title,
            "control_path": path,
            "control_class": self._class_name(control),
            "saved_at": datetime.now().isoformat(timespec="seconds"),
        }
        self._save()
        return True, f"입력칸 등록 완료: {title} / {self._class_name(control)}"

    def _find_window(self) -> int:
        if not self.available or not self.configured:
            return 0
        user32 = ctypes.windll.user32
        saved_title = str(self.config.get("window_title", ""))
        found: list[int] = []
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def callback(hwnd: int, _: int) -> bool:
            if not user32.IsWindowVisible(hwnd):
                return True
            title = self._window_text(int(hwnd))
            if _window_title_matches(saved_title, title):
                found.append(int(hwnd))
                return False
            return True

        user32.EnumWindows(callback_type(callback), 0)
        return found[0] if found else 0

    @staticmethod
    def _key(vk: int, down: bool) -> None:
        ctypes.windll.user32.keybd_event(vk, 0, 0 if down else 0x0002, 0)

    @classmethod
    def _hotkey(cls, modifier: int, key: int) -> None:
        cls._key(modifier, True)
        cls._key(key, True)
        cls._key(key, False)
        cls._key(modifier, False)

    @classmethod
    def _focus_control(cls, root: int, control: int) -> bool:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.ShowWindow(root, cls.SW_RESTORE)
        user32.SetForegroundWindow(root)
        time.sleep(0.15)
        current_thread = int(kernel32.GetCurrentThreadId())
        target_thread = int(user32.GetWindowThreadProcessId(control, None))
        attached = False
        try:
            if target_thread and target_thread != current_thread:
                attached = bool(user32.AttachThreadInput(current_thread, target_thread, True))
            return bool(user32.SetFocus(control) or user32.GetFocus() == control)
        finally:
            if attached:
                user32.AttachThreadInput(current_thread, target_thread, False)

    def send_symbol(self, symbol: str) -> tuple[bool, str]:
        if not self.available:
            return False, "Windows에서만 작동합니다."
        if not self.configured:
            return False, "먼저 '영웅문 입력칸 등록'을 하세요."
        if not symbol.isdigit() or len(symbol) != 6:
            return False, f"종목코드 형식 오류: {symbol}"
        root = self._find_window()
        if not root:
            return False, "등록한 영웅문 창을 찾지 못했습니다."
        path = list(self.config.get("control_path", []))
        control = self._resolve_control(root, path)
        if not control:
            return False, "등록한 입력칸을 다시 찾지 못했습니다. 입력칸을 재등록하세요."
        if not self._focus_control(root, control):
            return False, "영웅문 입력칸 활성화에 실패했습니다. 영웅문과 봇의 실행 권한을 같게 하세요."

        user32 = ctypes.windll.user32
        # 1차: 표준 입력컨트롤이면 직접 문자열을 넣는다.
        user32.SendMessageW(control, self.WM_SETTEXT, 0, ctypes.c_wchar_p(symbol))
        time.sleep(0.05)
        # 2차: 사용자 키입력과 동일한 방식으로 덮어써서 커스텀 입력칸도 처리한다.
        self._hotkey(self.VK_CONTROL, self.VK_A)
        time.sleep(0.03)
        for digit in symbol:
            self._key(ord(digit), True)
            self._key(ord(digit), False)
            time.sleep(0.015)
        self._key(self.VK_RETURN, True)
        self._key(self.VK_RETURN, False)
        return True, f"영웅문 전환 요청: {symbol}"


class CandidateGuiBridge:
    def __init__(self, logger: Any) -> None:
        self.logger = logger
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=4)
        self.thread: threading.Thread | None = None
        self.closed = threading.Event()

    def start(self) -> None:
        if self.thread is None:
            self.thread = threading.Thread(target=self._run, name="V75.23-GUI", daemon=True)
            self.thread.start()

    def publish(self, snapshot: dict[str, Any]) -> None:
        if self.closed.is_set():
            return
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

        root.title("V75.23 관심·매수·매도 — 영웅문 입력칸 직접 연동")
        root.geometry("1160x650")
        root.minsize(980, 500)
        hero = WindowsHeroControlLink(HERO_LINK_FILE)

        toolbar = ttk.Frame(root, padding=(8, 8, 8, 4))
        toolbar.pack(fill="x")
        status_var = tk.StringVar(value="5분 요약을 기다리는 중")
        time_var = tk.StringVar(value="갱신 전")
        ttk.Button(toolbar, text="영웅문 입력칸 등록", command=lambda: begin_capture()).pack(side="left", padx=(0, 6))
        ttk.Button(toolbar, text="선택종목 영웅문 열기", command=lambda: open_selected()).pack(side="left", padx=(0, 12))
        ttk.Label(toolbar, textvariable=time_var).pack(side="right")

        columns = ("stage", "time", "name", "symbol", "price", "reason")
        tree = ttk.Treeview(root, columns=columns, show="headings", selectmode="browse")
        headings = {
            "stage": "단계", "time": "시각", "name": "종목명", "symbol": "코드", "price": "현재가/매도가", "reason": "이유"
        }
        for key, text in headings.items():
            tree.heading(key, text=text)
        tree.column("stage", width=95, anchor="center", stretch=False)
        tree.column("time", width=90, anchor="center", stretch=False)
        tree.column("name", width=205, anchor="w")
        tree.column("symbol", width=85, anchor="center", stretch=False)
        tree.column("price", width=120, anchor="e", stretch=False)
        tree.column("reason", width=520, anchor="w")
        scroll_y = ttk.Scrollbar(root, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll_y.set)
        scroll_y.pack(side="right", fill="y")
        tree.pack(fill="both", expand=True, padx=(8, 0), pady=(4, 4))
        ttk.Label(root, textvariable=status_var, anchor="w", padding=(8, 4, 8, 8)).pack(fill="x")

        def selected_symbol() -> str:
            selected = tree.selection()
            if not selected:
                return ""
            values = tree.item(selected[0], "values")
            return str(values[3]).strip() if len(values) >= 4 else ""

        def open_selected(_: Any = None) -> None:
            symbol = selected_symbol()
            if not symbol:
                status_var.set("종목 줄을 먼저 선택하세요.")
                return
            ok, message = hero.send_symbol(symbol)
            status_var.set(message)
            if not ok:
                messagebox.showwarning("영웅문 연동 실패", message)

        def finish_capture() -> None:
            ok, message = hero.capture_focused_control()
            status_var.set(message)
            if not ok:
                messagebox.showwarning("입력칸 등록 실패", message)

        def countdown(remaining: int) -> None:
            if remaining <= 0:
                finish_capture()
                return
            status_var.set(f"{remaining}초 안에 영웅문 종목코드 입력칸을 직접 클릭하고 그대로 두세요.")
            root.after(1000, lambda: countdown(remaining - 1))

        def begin_capture() -> None:
            messagebox.showinfo(
                "영웅문 입력칸 등록",
                "확인을 누른 뒤 5초 안에 영웅문 종목코드 입력칸을 직접 한 번 클릭하세요.\n"
                "숫자 입력 커서가 깜빡이는 상태로 두고 다른 곳은 클릭하지 마세요."
            )
            countdown(5)

        def apply_snapshot(snapshot: dict[str, Any]) -> None:
            current_symbol = selected_symbol()
            for item_id in tree.get_children():
                tree.delete(item_id)
            selected_id = ""
            rows = list(snapshot.get("rows", []))
            for index, row in enumerate(rows):
                item_id = tree.insert("", "end", values=(
                    row.get("stage", ""), row.get("time", ""), row.get("name", ""),
                    row.get("symbol", ""), row.get("price", ""), row.get("reason", "")
                ))
                if current_symbol and row.get("symbol") == current_symbol:
                    selected_id = item_id
                elif index == 0 and not selected_id:
                    selected_id = item_id
            if selected_id:
                tree.selection_set(selected_id)
                tree.focus(selected_id)
            time_var.set(str(snapshot.get("updated", "")))
            status_var.set(
                f"시장 {snapshot.get('market', '-')} | 총 {len(rows)}종목 | 종목 줄 더블클릭 또는 위 버튼"
            )

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

        tree.bind("<Double-1>", open_selected)
        root.protocol("WM_DELETE_WINDOW", lambda: root.withdraw())
        root.after(200, poll_events)
        root.mainloop()


class NoStopTimedHeroEngine(v7522.HeroGuiEngine):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._stage_seen_at: dict[tuple[str, str], datetime] = {}
        super().__init__(*args, **kwargs)
        self.trailing_stop_pct.clear()
        self._enforce_fixed_targets()
        self.logger.warning(
            "[%s] 자동손절 없음 | 목표 +1.5%% 고정 | 수동매도·전체청산은 장중명령으로 가능",
            VERSION,
        )

    def _seen_at(self, stage: str, symbol: str, now: datetime) -> datetime:
        key = (stage, symbol)
        if key not in self._stage_seen_at:
            self._stage_seen_at[key] = now
        return self._stage_seen_at[key]

    def _execute_command(self, line: str) -> str:
        command = line.split()[0].strip() if line.split() else ""
        if command in STOP_COMMANDS:
            return "V75.23은 자동손절을 사용하지 않아 이 명령을 실행하지 않음"
        if command in TARGET_CHANGE_COMMANDS:
            return "V75.23 목표수익은 +1.5% 고정이라 변경하지 않음"
        return super()._execute_command(line)

    def _check_tick_exit(
        self,
        quote: Any,
        *,
        detection_time: datetime | None = None,
        observed_lag_sec: float = 0.0,
    ) -> None:
        """자동 청산은 +1.5% 예약목표만 허용한다. 손절가격은 참고값일 뿐 사용하지 않는다."""
        position = self.broker.positions.get(quote.symbol)
        if position is None:
            return
        position.highest_price = max(float(position.highest_price), float(quote.price))
        if not bool(position.target_order_active) or float(quote.price) < float(position.target_price):
            return
        detected_at = detection_time.astimezone(KST) if detection_time is not None else datetime.now(KST)
        try:
            enriched = self.client.get_orderbook(quote.symbol, quote, side="sell")
            if enriched.best_bid is None or float(enriched.best_bid) < float(position.target_price):
                return
            fill = self.broker.sell(
                quote.symbol,
                enriched,
                "예약목표+1.5%",
                reserved_fill_price=position.target_price,
                trigger_time=detected_at,
                trigger_price=quote.price,
                detection_time=detected_at,
                observed_lag_sec=observed_lag_sec,
                trigger_source="V75.23_TARGET_ONLY",
            )
        except (v753.V73Error, ValueError) as exc:
            self._show_throttled_error(
                f"target:{quote.symbol}", f"목표매도대기 {self._label(quote.symbol)}: {v753.brief_error(exc)}", 30.0
            )
            return
        self.strategy.mark_exit(quote.symbol)
        self.risk.record_pnl(fill.pnl)
        self._print_sell(fill)

    def _today_sell_rows(self) -> list[dict[str, str]]:
        today = datetime.now(KST).date()
        rows: list[dict[str, str]] = []
        if not v753.FILL_FILE.exists():
            return rows
        try:
            with v753.FILL_FILE.open("r", encoding="utf-8-sig", newline="") as handle:
                for raw in csv.DictReader(handle):
                    if str(raw.get("side", "")).upper() != "SELL":
                        continue
                    try:
                        timestamp = datetime.fromisoformat(str(raw.get("timestamp", "")))
                        if timestamp.tzinfo is None:
                            timestamp = timestamp.replace(tzinfo=KST)
                        timestamp = timestamp.astimezone(KST)
                    except ValueError:
                        continue
                    if timestamp.date() != today:
                        continue
                    symbol = str(raw.get("symbol", "")).zfill(6)
                    price = float(raw.get("price", 0.0) or 0.0)
                    pnl = float(raw.get("pnl", 0.0) or 0.0)
                    reason = _safe_text(raw.get("reason", ""), 45)
                    rows.append({
                        "stage": "매도종목",
                        "time": _clock(timestamp),
                        "name": _symbol_name(self._label(symbol), symbol),
                        "symbol": symbol,
                        "price": _price_text(price),
                        "reason": f"{reason} · 손익 {pnl:+,.0f}원",
                    })
        except (OSError, csv.Error, ValueError) as exc:
            self.logger.warning("매도이력 읽기 실패: %s", exc)
        return rows[-20:]

    def _gui_snapshot(self) -> dict[str, Any]:
        now = datetime.now(KST)
        buckets = self._potential_buckets()
        potential_symbols = {
            symbol for key in ("watch", "possible", "ready", "deferred") for symbol, _ in buckets[key]
        }
        interest_symbols = [
            symbol for symbol in self._current_symbols()
            if symbol not in potential_symbols and symbol not in self.broker.positions
        ]
        interest_symbols.sort(key=lambda symbol: (
            0 if symbol in getattr(self, "manual_watch", {}) else 1,
            -float(getattr(self.candidate_meta.get(symbol), "score", 0.0) or 0.0),
            symbol,
        ))
        rows: list[dict[str, str]] = []
        active_seen: set[tuple[str, str]] = set()

        for symbol in interest_symbols:
            stage = "관심종목"
            active_seen.add((stage, symbol))
            rows.append({
                "stage": stage,
                "time": _clock(self._seen_at(stage, symbol, now)),
                "name": _symbol_name(self._label(symbol), symbol),
                "symbol": symbol,
                "price": _price_text(self._current_price(symbol)),
                "reason": _safe_text(self._interest_reason(symbol)),
            })

        for key, stage in (("watch", "관찰후보"), ("possible", "매수가능성"), ("ready", "매수직전"), ("deferred", "시장보류")):
            for symbol, item in buckets[key]:
                active_seen.add((stage, symbol))
                rows.append({
                    "stage": stage,
                    "time": _clock(self._seen_at(stage, symbol, now)),
                    "name": _symbol_name(self._label(symbol), symbol),
                    "symbol": symbol,
                    "price": _price_text(self._current_price(symbol)),
                    "reason": _safe_text(v7520._condition_reason(item, key)),
                })

        for symbol, position in self.broker.positions.items():
            current = float(self._current_price(symbol) or position.entry_price)
            rows.append({
                "stage": "매수종목",
                "time": _clock(position.entry_time),
                "name": _symbol_name(self._label(symbol), symbol),
                "symbol": symbol,
                "price": _price_text(current),
                "reason": f"평단 {float(position.entry_price):,.0f}원 · 목표 {float(position.target_price):,.0f}원 · 자동손절 없음",
            })

        rows.extend(self._today_sell_rows())
        self._stage_seen_at = {key: value for key, value in self._stage_seen_at.items() if key in active_seen}
        order_index = {name: index for index, name in enumerate(STAGE_ORDER)}
        rows.sort(key=lambda row: (order_index.get(row["stage"], 99), row["time"], row["name"], row["symbol"]))
        state = str(getattr(self._market_snapshot, "state", "DATA_HALT"))
        return {
            "updated": now.strftime("%Y-%m-%d %H:%M:%S"),
            "market": v7518.market_label_short(state),
            "rows": rows,
        }

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

        print("\n" + "=" * 112)
        print(
            f"[{now:%H:%M}] 5분 요약 | 시장 {v7518.market_label_short(state)} | 신규매수 {entry} | "
            f"관심 {len(grouped['관심종목']):>3} | 관찰 {len(grouped['관찰후보']):>3} | "
            f"가능 {len(grouped['매수가능성']):>3} | 직전 {len(grouped['매수직전']):>3} | "
            f"보류 {len(grouped['시장보류']):>3} | 보유 {len(grouped['매수종목']):>2}"
        )
        print(
            f"평가자산 {liquidation_value:>16,.0f}원 | 현금 {float(self.broker.cash):>16,.0f}원 | "
            f"실현 {float(self.broker.realized_pnl):>+12,.0f}원 | 평가 {estimated_pnl:>+12,.0f}원"
        )

        limits = {"관심종목": 15, "관찰후보": 12, "매수가능성": 12, "매수직전": 10, "시장보류": 10}
        for stage in ("관심종목", "관찰후보", "매수가능성", "매수직전", "시장보류"):
            stage_rows = grouped[stage]
            print(f"[{stage}] {len(stage_rows)}종목")
            if not stage_rows:
                print("  없음")
                continue
            print(f"  {'시각':8} {'종목':22} {'현재가':>11}  이유")
            for row in stage_rows[:limits[stage]]:
                label = f"{row['name']}({row['symbol']})"
                print(
                    f"  {row['time']:8} {v7518.pad(label, 22)} "
                    f"{v7518.pad(row['price'], 11, right=True)}  {row['reason']}"
                )
            hidden = len(stage_rows) - limits[stage]
            if hidden > 0:
                print(f"  ... 나머지 {hidden}종목은 GUI·상세로그")

        print("-" * 112)
        print(f"[매수종목] {len(grouped['매수종목'])}종목 | 자동손절 없음 | 목표 +1.5% 고정")
        if grouped["매수종목"]:
            print(f"  {'매수시각':8} {'종목':22} {'현재가':>11}  상태")
            for row in grouped["매수종목"]:
                label = f"{row['name']}({row['symbol']})"
                print(
                    f"  {row['time']:8} {v7518.pad(label, 22)} "
                    f"{v7518.pad(row['price'], 11, right=True)}  {row['reason']}"
                )
        else:
            print("  없음")

        print(f"[오늘 매도종목] {len(grouped['매도종목'])}건")
        if grouped["매도종목"]:
            print(f"  {'매도시각':8} {'종목':22} {'매도가':>11}  결과")
            for row in grouped["매도종목"][-10:]:
                label = f"{row['name']}({row['symbol']})"
                print(
                    f"  {row['time']:8} {v7518.pad(label, 22)} "
                    f"{v7518.pad(row['price'], 11, right=True)}  {row['reason']}"
                )
        else:
            print("  없음")
        print("=" * 112 + "\n")
        self.save_pattern_state()


def install_daily_tee() -> Any:
    DAILY_LOG_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(KST)
    path = DAILY_LOG_DIR / f"V75_23_{now:%Y-%m-%d}.txt"
    handle = path.open("a", encoding="utf-8", buffering=1)
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    stdout_tee = v7520.SafeTeeTextIO(original_stdout, handle)
    stderr_tee = v7520.SafeTeeTextIO(original_stderr, handle)
    sys.stdout = stdout_tee  # type: ignore[assignment]
    sys.stderr = stderr_tee  # type: ignore[assignment]
    print("=" * 112)
    print(f"[{now:%Y-%m-%d %H:%M:%S}] {VERSION}")
    print(f"장중명령: {COMMAND_FILE.name} | 상세로그: {path} | GUI: 관심·매수·매도 시각 표시")
    print("=" * 112)
    return v7520.SafeDailyTee(path, handle, original_stdout, original_stderr, stdout_tee, stderr_tee)


def self_check() -> list[tuple[str, bool, str]]:
    baseline_failed = [name for name, ok, _ in BASELINE_V7522_SELF_CHECK if not ok]
    results: list[tuple[str, bool, str]] = [
        ("V75.22기존검증요약", not baseline_failed, f"{len(BASELINE_V7522_SELF_CHECK)}항목 / 실패={baseline_failed or '없음'}")
    ]

    def record(name: str, ok: bool, detail: str) -> None:
        results.append((name, bool(ok), detail))

    source = Path(__file__).read_text(encoding="utf-8")
    record("V75.22기준본SHA", sha256_file(BASE_DIR / V7522_FILENAME) == V7522_SHA256, V7522_SHA256[:16])
    record("목표1.5퍼센트유지", v7521.FIXED_TAKE_PROFIT_PCT == 0.015, "+1.5%")
    tick_source = source.split("def _check_tick_exit", 1)[1].split("def _today_sell_rows", 1)[0]
    record("자동손절분기부재", "quote.price <= position.stop_price" not in tick_source and "고정손절" not in tick_source, "목표청산만")
    record("목표청산존재", "예약목표+1.5%" in tick_source and "V75.23_TARGET_ONLY" in tick_source, "+1.5% 예약")
    record("손절명령차단", STOP_COMMANDS == {"손절", "손절가격", "추적손절", "추적손절해제", "손익보호"}, str(sorted(STOP_COMMANDS)))
    record("목표변경차단", "목표 +1.5% 고정" in source, str(sorted(TARGET_CHANGE_COMMANDS)))
    record("입력컨트롤등록", all(token in source for token in ("capture_focused_control", "GetFocus", "control_path", "SetFocus")), "좌표 미사용")
    record("시간열표시", '"time": "시각"' in source and '"매수종목"' in source and '"매도종목"' in source, "관심·매수·매도")
    record("장중명령유지", COMMAND_FILE.name == "장중명령.txt", str(COMMAND_FILE))
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
    gui: CandidateGuiBridge | None = None
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
            f"[시작] PAPER | 평가자산 10억원 | 종목당 최대 1천만원 | "
            f"목표 +1.5% 고정 | 자동손절 없음 | 기준종목 {first.symbol} {first.price:,.0f}원"
        )
        if not args.no_gui:
            gui = CandidateGuiBridge(logger)
            gui.start()
        engine_logger = v7515.v7591.CommaSafeLoggerAdapter(logger, {})
        engine = NoStopTimedHeroEngine(
            settings, client, engine_logger,
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
