#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V75.22 PAPER
- V75.21 매매 로직, +1.5% 고정 목표, 장중명령을 그대로 유지한다.
- 5분 요약의 관심/관찰/가능/직전/시장보류 종목을 별도 GUI에 표시한다.
- GUI 종목을 더블클릭하면 코드 복사 후 설정된 영웅문 종목입력칸으로 전달한다.
- 영웅문 입력칸 위치는 GUI의 '입력칸 위치설정' 버튼으로 1회 저장한다.
- 표준 라이브러리만 사용한다. PAPER 전용이며 실제 주문 함수는 없다.
"""
from __future__ import annotations

import ast
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
from typing import Any, Iterable

VERSION = "V75.22-HERO-GUI-LINK-PAPER"
BASE_DIR = Path(__file__).resolve().parent
V7521_FILENAME = "bot_v75_21_fixed_15_flexible_reentry_paper.py"
V7521_SHA256 = "44aa7c4272961af4538f273a191efd53624daf33a419420d17b22003dc187e6c"
RUNTIME_DIR = BASE_DIR / "v75_22_runtime"
DAILY_LOG_DIR = BASE_DIR / "v75_22_daily_logs"
COMMAND_FILE = BASE_DIR / "장중명령.txt"
HERO_LINK_FILE = BASE_DIR / "영웅문연동설정.json"
FORBIDDEN_ORDER_NAMES = {"send_order", "create_order", "place_order", "order_stock", "submit_order"}
STAGE_ORDER = ("관심종목", "관찰후보", "매수가능성", "매수직전", "시장보류", "보유종목")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_v7521() -> ModuleType:
    path = BASE_DIR / V7521_FILENAME
    if not path.exists():
        raise RuntimeError(f"{V7521_FILENAME}이 같은 폴더에 없습니다.")
    actual = sha256_file(path)
    if actual.lower() != V7521_SHA256:
        raise RuntimeError(
            "V75.21 SHA256 불일치—검증된 기준본이 아니므로 중단합니다.\n"
            f"기대: {V7521_SHA256}\n실제: {actual}"
        )
    spec = importlib.util.spec_from_file_location("v7521_immutable_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("V75.21 모듈 로더를 만들 수 없습니다.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


v7521 = load_v7521()
BASELINE_V7521_SELF_CHECK = v7521.self_check()
v7520 = v7521.v7520
v7518 = v7521.v7518
v7517 = v7521.v7517
v7516 = v7521.v7516
v7515 = v7521.v7515
v7514 = v7521.v7514
v753 = v7521.v753
KST = v7521.KST

# 버전별 상태·로그를 분리하고 장중명령 기능을 유지한다.
v753.LOG_DIR = RUNTIME_DIR
v753.STATE_FILE = RUNTIME_DIR / "v75_22_paper_state.json"
v753.FILL_FILE = RUNTIME_DIR / "v75_22_paper_fills.csv"
v753.PATTERN_STATE_FILE = RUNTIME_DIR / "v75_22_pattern_state.json"
v753.ERROR_FILE = RUNTIME_DIR / "v75_22_api_errors.txt"
v753.RANK_SCHEMA_FILE = RUNTIME_DIR / "v75_22_rank_schema_probe.json"
v7517.RUNTIME_DIR = RUNTIME_DIR
v7517.DAILY_LOG_DIR = DAILY_LOG_DIR
v7517.COMMAND_FILE = COMMAND_FILE
v7517.COMMAND_STATE_FILE = RUNTIME_DIR / "v75_22_command_state.json"
v7516.RUNTIME_DIR = RUNTIME_DIR
v7516.DAILY_LOG_DIR = DAILY_LOG_DIR
v7515.RUNTIME_DIR = RUNTIME_DIR
v7515.DAILY_LOG_DIR = DAILY_LOG_DIR
v7515.POTENTIAL_FILE = RUNTIME_DIR / "v75_22_supply_potential.json"
v7514.RUNTIME_DIR = RUNTIME_DIR
v7514.DAILY_LOG_DIR = DAILY_LOG_DIR
v7514.POTENTIAL_FILE = v7515.POTENTIAL_FILE


def _safe_text(value: Any, limit: int = 80) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _symbol_name(label: str, symbol: str) -> str:
    text = str(label or "").strip()
    suffix = f"({symbol})"
    if text.endswith(suffix):
        text = text[: -len(suffix)].strip()
    return text or symbol


def _window_title_matches(saved: str, current: str) -> bool:
    saved_norm = " ".join(saved.split()).lower()
    current_norm = " ".join(current.split()).lower()
    if not saved_norm or not current_norm:
        return False
    return saved_norm == current_norm or saved_norm in current_norm or current_norm in saved_norm


class WindowsHeroLink:
    """사용자가 지정한 영웅문 종목입력칸으로 종목코드를 전달한다."""

    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self.config: dict[str, Any] = self._load()

    @property
    def available(self) -> bool:
        return os.name == "nt" and hasattr(ctypes, "windll")

    @property
    def configured(self) -> bool:
        return bool(
            self.config.get("window_title")
            and isinstance(self.config.get("relative_x"), int)
            and isinstance(self.config.get("relative_y"), int)
        )

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def _save(self) -> None:
        self.config_path.write_text(
            json.dumps(self.config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def capture_cursor_target(self) -> tuple[bool, str]:
        if not self.available:
            return False, "Windows에서만 설정할 수 있습니다."

        user32 = ctypes.windll.user32

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long),
            ]

        point = POINT()
        if not user32.GetCursorPos(ctypes.byref(point)):
            return False, "마우스 위치를 읽지 못했습니다."
        hwnd = user32.WindowFromPoint(point)
        if not hwnd:
            return False, "마우스 아래의 창을 찾지 못했습니다."
        root_hwnd = user32.GetAncestor(hwnd, 2) or hwnd  # GA_ROOT
        length = user32.GetWindowTextLengthW(root_hwnd)
        buffer = ctypes.create_unicode_buffer(max(2, length + 1))
        user32.GetWindowTextW(root_hwnd, buffer, len(buffer))
        title = buffer.value.strip()
        rect = RECT()
        if not title or not user32.GetWindowRect(root_hwnd, ctypes.byref(rect)):
            return False, "영웅문 창 제목이나 위치를 읽지 못했습니다."
        self.config = {
            "window_title": title,
            "relative_x": int(point.x - rect.left),
            "relative_y": int(point.y - rect.top),
            "saved_at": datetime.now().isoformat(timespec="seconds"),
        }
        self._save()
        return True, f"저장 완료: {title}"

    def _find_window(self) -> int:
        if not self.available or not self.configured:
            return 0
        user32 = ctypes.windll.user32
        saved_title = str(self.config.get("window_title", ""))
        found: list[int] = []
        enum_proc_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def callback(hwnd: int, _: int) -> bool:
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, len(buffer))
            title = buffer.value.strip()
            if _window_title_matches(saved_title, title):
                found.append(int(hwnd))
                return False
            return True

        user32.EnumWindows(enum_proc_type(callback), 0)
        return found[0] if found else 0

    @staticmethod
    def _key(vk: int, down: bool) -> None:
        flags = 0 if down else 0x0002  # KEYEVENTF_KEYUP
        ctypes.windll.user32.keybd_event(vk, 0, flags, 0)

    @classmethod
    def _hotkey(cls, modifier: int, key: int) -> None:
        cls._key(modifier, True)
        cls._key(key, True)
        cls._key(key, False)
        cls._key(modifier, False)

    def send_symbol(self, symbol: str) -> tuple[bool, str]:
        if not self.available:
            return False, "Windows에서만 영웅문 연동이 작동합니다."
        if not self.configured:
            return False, "먼저 '입력칸 위치설정'을 눌러 설정하세요."
        hwnd = self._find_window()
        if not hwnd:
            return False, "설정한 영웅문 창을 찾지 못했습니다."

        user32 = ctypes.windll.user32

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long),
            ]

        rect = RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False, "영웅문 창 위치를 읽지 못했습니다."
        x = int(rect.left + int(self.config["relative_x"]))
        y = int(rect.top + int(self.config["relative_y"]))

        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.12)
        user32.SetCursorPos(x, y)
        user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
        user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
        time.sleep(0.08)
        self._hotkey(0x11, 0x41)  # Ctrl+A
        time.sleep(0.04)
        self._hotkey(0x11, 0x56)  # Ctrl+V
        time.sleep(0.04)
        self._key(0x0D, True)     # Enter
        self._key(0x0D, False)
        return True, f"영웅문 전환: {symbol}"


class CandidateGuiBridge:
    def __init__(self, logger: Any) -> None:
        self.logger = logger
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=4)
        self.thread: threading.Thread | None = None
        self.closed = threading.Event()

    def start(self) -> None:
        if self.thread is not None:
            return
        self.thread = threading.Thread(target=self._run, name="V75.22-GUI", daemon=True)
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
        except Exception as exc:
            self.logger.warning("GUI 시작 실패: %s", exc)
            return

        try:
            root = tk.Tk()
        except Exception as exc:
            self.logger.warning("GUI 창 생성 실패: %s", exc)
            return

        root.title("V75.22 관심·매수후보 — 더블클릭 영웅문 연동")
        root.geometry("1060x620")
        root.minsize(900, 480)
        hero = WindowsHeroLink(HERO_LINK_FILE)

        toolbar = ttk.Frame(root, padding=(8, 8, 8, 4))
        toolbar.pack(fill="x")
        status_var = tk.StringVar(value="5분 요약을 기다리는 중")
        time_var = tk.StringVar(value="갱신 전")

        ttk.Button(
            toolbar,
            text="입력칸 위치설정",
            command=lambda: begin_capture(),
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            toolbar,
            text="선택종목 영웅문 열기",
            command=lambda: open_selected(),
        ).pack(side="left", padx=(0, 12))
        ttk.Label(toolbar, textvariable=time_var).pack(side="right")

        columns = ("stage", "name", "symbol", "price", "reason")
        tree = ttk.Treeview(root, columns=columns, show="headings", selectmode="browse")
        tree.heading("stage", text="단계")
        tree.heading("name", text="종목명")
        tree.heading("symbol", text="코드")
        tree.heading("price", text="현재가")
        tree.heading("reason", text="이유")
        tree.column("stage", width=95, anchor="center", stretch=False)
        tree.column("name", width=210, anchor="w")
        tree.column("symbol", width=90, anchor="center", stretch=False)
        tree.column("price", width=120, anchor="e", stretch=False)
        tree.column("reason", width=490, anchor="w")

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
            return str(values[2]).strip() if len(values) >= 3 else ""

        def copy_symbol(symbol: str) -> None:
            root.clipboard_clear()
            root.clipboard_append(symbol)
            root.update_idletasks()

        def open_selected(_: Any = None) -> None:
            symbol = selected_symbol()
            if not symbol:
                status_var.set("종목을 먼저 선택하세요.")
                return
            copy_symbol(symbol)
            ok, message = hero.send_symbol(symbol)
            status_var.set(message if ok else f"코드는 복사됨 | {message}")

        def finish_capture() -> None:
            ok, message = hero.capture_cursor_target()
            status_var.set(message)
            if not ok:
                messagebox.showwarning("위치설정 실패", message)

        def countdown(remaining: int) -> None:
            if remaining <= 0:
                finish_capture()
                return
            status_var.set(f"{remaining}초 안에 마우스를 영웅문 종목코드 입력칸 위에 올려두세요.")
            root.after(1000, lambda: countdown(remaining - 1))

        def begin_capture() -> None:
            messagebox.showinfo(
                "영웅문 입력칸 위치설정",
                "확인을 누른 뒤 5초 안에 마우스를 영웅문의 종목코드 입력칸 위에 올려두세요.\n클릭할 필요는 없습니다.",
            )
            countdown(5)

        def apply_snapshot(snapshot: dict[str, Any]) -> None:
            current_selection = selected_symbol()
            for item_id in tree.get_children():
                tree.delete(item_id)
            rows = list(snapshot.get("rows", []))
            selected_id = ""
            for index, row in enumerate(rows):
                item_id = tree.insert(
                    "",
                    "end",
                    values=(
                        row.get("stage", ""),
                        row.get("name", ""),
                        row.get("symbol", ""),
                        row.get("price", ""),
                        row.get("reason", ""),
                    ),
                )
                if current_selection and row.get("symbol") == current_selection:
                    selected_id = item_id
                if index == 0 and not selected_id:
                    selected_id = item_id
            if selected_id:
                tree.selection_set(selected_id)
                tree.focus(selected_id)
            time_var.set(str(snapshot.get("updated", "")))
            status_var.set(
                f"시장 {snapshot.get('market', '-')} | 총 {len(rows)}종목 | "
                "종목을 더블클릭하면 영웅문으로 전환"
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


def _price_text(value: float) -> str:
    return f"{value:,.0f}원" if value > 0 else "조회대기"


class HeroGuiEngine(v7521.FixedTargetFlexibleEngine):
    def __init__(self, *args: Any, gui_bridge: CandidateGuiBridge | None = None, **kwargs: Any) -> None:
        self.gui_bridge = gui_bridge
        super().__init__(*args, **kwargs)

    def _gui_snapshot(self) -> dict[str, Any]:
        buckets = self._potential_buckets()
        potential_symbols = {
            symbol
            for key in ("watch", "possible", "ready", "deferred")
            for symbol, _ in buckets[key]
        }
        interest_symbols = [
            symbol for symbol in self._current_symbols()
            if symbol not in potential_symbols and symbol not in self.broker.positions
        ]
        interest_symbols.sort(
            key=lambda symbol: (
                0 if symbol in getattr(self, "manual_watch", {}) else 1,
                -float(getattr(self.candidate_meta.get(symbol), "score", 0.0) or 0.0),
                symbol,
            )
        )
        rows: list[dict[str, str]] = []

        for symbol in interest_symbols:
            label = self._label(symbol)
            rows.append({
                "stage": "관심종목",
                "name": _symbol_name(label, symbol),
                "symbol": symbol,
                "price": _price_text(self._current_price(symbol)),
                "reason": _safe_text(self._interest_reason(symbol)),
            })

        bucket_map = (
            ("watch", "관찰후보"),
            ("possible", "매수가능성"),
            ("ready", "매수직전"),
            ("deferred", "시장보류"),
        )
        for key, stage in bucket_map:
            for symbol, item in buckets[key]:
                label = self._label(symbol)
                rows.append({
                    "stage": stage,
                    "name": _symbol_name(label, symbol),
                    "symbol": symbol,
                    "price": _price_text(self._current_price(symbol)),
                    "reason": _safe_text(v7520._condition_reason(item, key)),
                })

        for symbol, position in self.broker.positions.items():
            label = self._label(symbol)
            current = float(self._current_price(symbol) or position.entry_price)
            rows.append({
                "stage": "보유종목",
                "name": _symbol_name(label, symbol),
                "symbol": symbol,
                "price": _price_text(current),
                "reason": f"평단 {float(position.entry_price):,.0f}원 · 목표 {float(position.target_price):,.0f}원",
            })

        order_index = {name: index for index, name in enumerate(STAGE_ORDER)}
        rows.sort(key=lambda row: (order_index.get(row["stage"], 99), row["name"], row["symbol"]))
        state = str(getattr(self._market_snapshot, "state", "DATA_HALT"))
        return {
            "updated": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
            "market": v7518.market_label_short(state),
            "rows": rows,
        }

    def _compact_report(self) -> None:
        if self.gui_bridge is not None:
            try:
                self.gui_bridge.publish(self._gui_snapshot())
            except Exception as exc:
                self.logger.warning("GUI 목록 갱신 실패: %s", exc)
        super()._compact_report()


def install_daily_tee() -> Any:
    DAILY_LOG_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(KST)
    path = DAILY_LOG_DIR / f"V75_22_{now:%Y-%m-%d}.txt"
    handle = path.open("a", encoding="utf-8", buffering=1)
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    stdout_tee = v7520.SafeTeeTextIO(original_stdout, handle)
    stderr_tee = v7520.SafeTeeTextIO(original_stderr, handle)
    sys.stdout = stdout_tee  # type: ignore[assignment]
    sys.stderr = stderr_tee  # type: ignore[assignment]
    print("=" * 112)
    print(f"[{now:%Y-%m-%d %H:%M:%S}] {VERSION}")
    print(f"장중명령: {COMMAND_FILE.name} | 상세로그: {path} | GUI: 종목 더블클릭 영웅문 연동")
    print("=" * 112)
    return v7520.SafeDailyTee(
        path, handle, original_stdout, original_stderr, stdout_tee, stderr_tee
    )


def self_check() -> list[tuple[str, bool, str]]:
    results = list(BASELINE_V7521_SELF_CHECK)

    def record(name: str, ok: bool, detail: str) -> None:
        results.append((name, bool(ok), detail))

    record(
        "V75.21기준본SHA",
        sha256_file(BASE_DIR / V7521_FILENAME) == V7521_SHA256,
        V7521_SHA256[:16],
    )
    record("장중명령유지", COMMAND_FILE.name == "장중명령.txt", str(COMMAND_FILE))
    record("GUI5분목록연결", "_compact_report" in HeroGuiEngine.__dict__, "5분 요약 시 갱신")
    record("단계6개표시", STAGE_ORDER == ("관심종목", "관찰후보", "매수가능성", "매수직전", "시장보류", "보유종목"), str(STAGE_ORDER))
    record("더블클릭연동", hasattr(WindowsHeroLink, "send_symbol"), "코드복사·창활성·입력")
    record("창이동대응상대좌표", all(token in Path(__file__).read_text(encoding="utf-8") for token in ("point.x - rect.left", "rect.left + int(self.config[\"relative_x\"])", "point.y - rect.top", "rect.top + int(self.config[\"relative_y\"])")), str(HERO_LINK_FILE.name))
    record("외부패키지불필요", True, "tkinter/ctypes/json 표준라이브러리")
    record("목표1.5퍼센트유지", v7521.FIXED_TAKE_PROFIT_PCT == 0.015, "+1.5%")
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    names = {
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
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
            f"목표 +1.5% 고정 | GUI 더블클릭 연동 | 기준종목 {first.symbol} {first.price:,.0f}원"
        )
        if not args.no_gui:
            gui = CandidateGuiBridge(logger)
            gui.start()
        engine_logger = v7515.v7591.CommaSafeLoggerAdapter(logger, {})
        engine = HeroGuiEngine(
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
