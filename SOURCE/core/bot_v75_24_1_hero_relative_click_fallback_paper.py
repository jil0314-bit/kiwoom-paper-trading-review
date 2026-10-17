#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V75.24 PAPER
- V75.23 매매 로직과 장중명령, 시간 표시, +1.5% 목표, 자동손절 없음은 유지한다.
- 영웅문 연동은 표준 입력컨트롤 탐색을 사용하지 않는다.
- 사용자가 클릭한 위치를 영웅문 창 내부 상대좌표로 저장한다.
- 종목 더블클릭 시 영웅문 창을 앞으로 가져와 저장 위치를 클릭하고 종목코드+Enter를 입력한다.
- 자동연동 실패 시 종목코드를 클립보드에 복사하며 경고창을 반복하지 않는다.
- PAPER 전용이며 실제 주문 함수는 없다.
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
from typing import Any

VERSION = "V75.24.1-START-FIX-CLEAN-PAPER"
BASE_DIR = Path(__file__).resolve().parent
V7523_FILENAME = "bot_v75_23_control_link_no_stop_paper.py"
V7523_SHA256 = "d6383832cb29fbbf9ad6ab4afb6bf00ee02113b49a48cfc94caa2f3bdf8e3b35"
PACKAGE_DIR = BASE_DIR.parent
RUNTIME_DIR = PACKAGE_DIR / "v75_24_runtime"
DAILY_LOG_DIR = PACKAGE_DIR / "v75_24_daily_logs"
COMMAND_FILE = PACKAGE_DIR / "장중명령.txt"
HERO_LINK_FILE = PACKAGE_DIR / "영웅문입력위치설정.json"
FORBIDDEN_ORDER_NAMES = {"send_order", "create_order", "place_order", "order_stock", "submit_order"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_v7523() -> ModuleType:
    path = BASE_DIR / V7523_FILENAME
    if not path.exists():
        raise RuntimeError(f"{V7523_FILENAME}이 같은 폴더에 없습니다.")
    actual = sha256_file(path)
    if actual.lower() != V7523_SHA256:
        raise RuntimeError(
            "V75.23 SHA256 불일치—검증된 기준본이 아니므로 중단합니다.\n"
            f"기대: {V7523_SHA256}\n실제: {actual}"
        )
    spec = importlib.util.spec_from_file_location("v7523_immutable_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("V75.23 모듈 로더를 만들 수 없습니다.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


v7523 = load_v7523()
BASELINE_V7523_SELF_CHECK = v7523.self_check()
v7522 = v7523.v7522
v7521 = v7523.v7521
v7520 = v7523.v7520
v7518 = v7523.v7518
v7517 = v7523.v7517
v7516 = v7523.v7516
v7515 = v7523.v7515
v7514 = v7523.v7514
v753 = v7523.v753
KST = v7523.KST

# 버전별 PAPER 상태·로그를 분리한다.
v753.LOG_DIR = RUNTIME_DIR
v753.STATE_FILE = RUNTIME_DIR / "v75_24_paper_state.json"
v753.FILL_FILE = RUNTIME_DIR / "v75_24_paper_fills.csv"
v753.PATTERN_STATE_FILE = RUNTIME_DIR / "v75_24_pattern_state.json"
v753.ERROR_FILE = RUNTIME_DIR / "v75_24_api_errors.txt"
v753.RANK_SCHEMA_FILE = RUNTIME_DIR / "v75_24_rank_schema_probe.json"
v7517.RUNTIME_DIR = RUNTIME_DIR
v7517.DAILY_LOG_DIR = DAILY_LOG_DIR
v7517.COMMAND_FILE = COMMAND_FILE
v7517.COMMAND_STATE_FILE = RUNTIME_DIR / "v75_24_command_state.json"
v7516.RUNTIME_DIR = RUNTIME_DIR
v7516.DAILY_LOG_DIR = DAILY_LOG_DIR
v7515.RUNTIME_DIR = RUNTIME_DIR
v7515.DAILY_LOG_DIR = DAILY_LOG_DIR
v7515.POTENTIAL_FILE = RUNTIME_DIR / "v75_24_supply_potential.json"
v7514.RUNTIME_DIR = RUNTIME_DIR
v7514.DAILY_LOG_DIR = DAILY_LOG_DIR
v7514.POTENTIAL_FILE = v7515.POTENTIAL_FILE


def _window_title_matches(saved: str, current: str) -> bool:
    saved_norm = " ".join(saved.split()).lower()
    current_norm = " ".join(current.split()).lower()
    return bool(saved_norm and current_norm) and (
        saved_norm == current_norm or saved_norm in current_norm or current_norm in saved_norm
    )


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class WindowsHeroRelativeLink:
    """영웅문 창 내부 상대좌표를 저장해 클릭·키입력을 수행한다."""

    SW_RESTORE = 9
    GA_ROOT = 2
    VK_CONTROL = 0x11
    VK_RETURN = 0x0D
    VK_A = 0x41
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004

    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self.config = self._load()

    @property
    def available(self) -> bool:
        return os.name == "nt" and hasattr(ctypes, "windll")

    @property
    def configured(self) -> bool:
        return bool(
            self.config.get("window_title")
            and isinstance(self.config.get("x_ratio"), (int, float))
            and isinstance(self.config.get("y_ratio"), (int, float))
        )

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
    def _window_text(hwnd: int) -> str:
        user32 = ctypes.windll.user32
        length = int(user32.GetWindowTextLengthW(hwnd) or 0)
        buffer = ctypes.create_unicode_buffer(max(2, length + 1))
        user32.GetWindowTextW(hwnd, buffer, len(buffer))
        return buffer.value.strip()

    @staticmethod
    def _client_geometry(hwnd: int) -> tuple[int, int, int, int] | None:
        user32 = ctypes.windll.user32
        rect = RECT()
        if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
            return None
        origin = POINT(0, 0)
        if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
            return None
        width = int(rect.right - rect.left)
        height = int(rect.bottom - rect.top)
        if width <= 0 or height <= 0:
            return None
        return int(origin.x), int(origin.y), width, height

    def capture_cursor_target(self) -> tuple[bool, str]:
        if not self.available:
            return False, "Windows에서만 설정할 수 있습니다."
        user32 = ctypes.windll.user32
        foreground = int(user32.GetForegroundWindow() or 0)
        if not foreground:
            return False, "현재 활성 창을 찾지 못했습니다."
        root = int(user32.GetAncestor(foreground, self.GA_ROOT) or foreground)
        title = self._window_text(root)
        if not title:
            return False, "영웅문 창 제목을 읽지 못했습니다."
        cursor = POINT()
        if not user32.GetCursorPos(ctypes.byref(cursor)):
            return False, "마우스 위치를 읽지 못했습니다."
        geometry = self._client_geometry(root)
        if geometry is None:
            return False, "영웅문 창 크기를 읽지 못했습니다."
        left, top, width, height = geometry
        rel_x = int(cursor.x) - left
        rel_y = int(cursor.y) - top
        if not (0 <= rel_x < width and 0 <= rel_y < height):
            return False, "마우스가 영웅문 창 안에 있지 않습니다."
        self.config = {
            "window_title": title,
            "x_ratio": max(0.0, min(1.0, rel_x / width)),
            "y_ratio": max(0.0, min(1.0, rel_y / height)),
            "saved_at": datetime.now().isoformat(timespec="seconds"),
        }
        self._save()
        return True, "영웅문 종목코드 입력 위치 저장 완료"

    def _find_window(self) -> int:
        if not self.available or not self.configured:
            return 0
        user32 = ctypes.windll.user32
        saved_title = str(self.config.get("window_title", ""))
        found: list[int] = []
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def callback(hwnd: int, _: int) -> bool:
            hwnd_int = int(hwnd)
            if not user32.IsWindowVisible(hwnd_int):
                return True
            title = self._window_text(hwnd_int)
            if _window_title_matches(saved_title, title):
                found.append(hwnd_int)
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

    def send_symbol(self, symbol: str) -> tuple[bool, str]:
        if not self.available:
            return False, "Windows에서만 작동합니다."
        if not self.configured:
            return False, "먼저 '영웅문 입력위치 저장'을 하세요."
        if not symbol.isdigit() or len(symbol) != 6:
            return False, f"종목코드 형식 오류: {symbol}"
        root = self._find_window()
        if not root:
            return False, "저장한 영웅문 창을 찾지 못했습니다."
        geometry = self._client_geometry(root)
        if geometry is None:
            return False, "영웅문 창 위치를 읽지 못했습니다."
        left, top, width, height = geometry
        x = left + int(float(self.config["x_ratio"]) * width)
        y = top + int(float(self.config["y_ratio"]) * height)
        user32 = ctypes.windll.user32
        user32.ShowWindow(root, self.SW_RESTORE)
        user32.SetForegroundWindow(root)
        time.sleep(0.20)
        user32.SetCursorPos(x, y)
        user32.mouse_event(self.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        user32.mouse_event(self.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        time.sleep(0.12)
        self._hotkey(self.VK_CONTROL, self.VK_A)
        time.sleep(0.04)
        for digit in symbol:
            self._key(ord(digit), True)
            self._key(ord(digit), False)
            time.sleep(0.02)
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
            self.thread = threading.Thread(target=self._run, name="V75.24-GUI", daemon=True)
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

        root.title("V75.24 관심·매수·매도 — 영웅문 상대위치 연동")
        root.geometry("1160x650")
        root.minsize(980, 500)
        hero = WindowsHeroRelativeLink(HERO_LINK_FILE)

        toolbar = ttk.Frame(root, padding=(8, 8, 8, 4))
        toolbar.pack(fill="x")
        status_var = tk.StringVar(value="5분 요약을 기다리는 중")
        time_var = tk.StringVar(value="갱신 전")
        ttk.Button(toolbar, text="영웅문 입력위치 저장", command=lambda: begin_capture()).pack(side="left", padx=(0, 6))
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

        def copy_symbol(symbol: str) -> None:
            root.clipboard_clear()
            root.clipboard_append(symbol)
            root.update_idletasks()

        def open_selected(_: Any = None) -> None:
            symbol = selected_symbol()
            if not symbol:
                status_var.set("종목 줄을 먼저 선택하세요.")
                return
            copy_symbol(symbol)
            ok, message = hero.send_symbol(symbol)
            if ok:
                status_var.set(message)
            else:
                status_var.set(f"자동전환 실패 · {symbol} 복사됨 · 영웅문 입력칸 클릭 후 Ctrl+V, Enter")

        def finish_capture() -> None:
            ok, message = hero.capture_cursor_target()
            status_var.set(message)
            if not ok:
                status_var.set(f"위치 저장 실패: {message} · 경고창은 다시 띄우지 않습니다.")

        def countdown(remaining: int) -> None:
            if remaining <= 0:
                finish_capture()
                return
            status_var.set(f"{remaining}초 안에 영웅문 종목코드 입력칸을 한 번 클릭하고 마우스를 그대로 두세요.")
            root.after(1000, lambda: countdown(remaining - 1))

        def begin_capture() -> None:
            messagebox.showinfo(
                "영웅문 입력위치 저장",
                "확인을 누른 뒤 5초 안에 영웅문 종목코드 입력칸을 한 번 클릭하세요.\n"
                "클릭한 뒤 마우스를 움직이지 말고 기다리세요."
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


class HeroRelativeEngine(v7523.NoStopTimedHeroEngine):
    pass


def install_daily_tee() -> Any:
    DAILY_LOG_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(KST)
    path = DAILY_LOG_DIR / f"V75_24_1_{now:%Y-%m-%d}.txt"
    handle = path.open("a", encoding="utf-8", buffering=1)
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    stdout_tee = v7520.SafeTeeTextIO(original_stdout, handle)
    stderr_tee = v7520.SafeTeeTextIO(original_stderr, handle)
    sys.stdout = stdout_tee  # type: ignore[assignment]
    sys.stderr = stderr_tee  # type: ignore[assignment]
    print("=" * 112)
    print(f"[{now:%Y-%m-%d %H:%M:%S}] {VERSION}")
    print(f"장중명령: {COMMAND_FILE.name} | 상세로그: {path} | GUI: 상대위치 연동 + 클립보드 대체")
    print("=" * 112)
    return v7520.SafeDailyTee(path, handle, original_stdout, original_stderr, stdout_tee, stderr_tee)


def self_check() -> list[tuple[str, bool, str]]:
    baseline_failed = [name for name, ok, _ in BASELINE_V7523_SELF_CHECK if not ok]
    results: list[tuple[str, bool, str]] = [
        ("V75.23기존검증요약", not baseline_failed, f"{len(BASELINE_V7523_SELF_CHECK)}항목 / 실패={baseline_failed or '없음'}")
    ]

    def record(name: str, ok: bool, detail: str) -> None:
        results.append((name, bool(ok), detail))

    source = Path(__file__).read_text(encoding="utf-8")
    record("V75.23기준본SHA", sha256_file(BASE_DIR / V7523_FILENAME) == V7523_SHA256, V7523_SHA256[:16])
    record("상대좌표저장", all(token in source for token in ("x_ratio", "y_ratio", "GetCursorPos", "ClientToScreen")), "창 내부 상대좌표")
    link_source = source.split("class WindowsHeroRelativeLink", 1)[1].split("class CandidateGuiBridge", 1)[0]
    gui_source = source.split("class CandidateGuiBridge", 1)[1].split("class HeroRelativeEngine", 1)[0]
    record("표준컨트롤비의존", "GetFocus" not in link_source and "control_path" not in link_source, "영웅문 커스텀 입력칸 대응")
    record("반복경고창제거", "showwarning" not in gui_source, "상태줄 1회 표시")
    record("클립보드대체", all(token in source for token in ("clipboard_clear", "clipboard_append", "Ctrl+V")), "자동전환 실패시 코드 복사")
    record("목표1.5유지", v7521.FIXED_TAKE_PROFIT_PCT == 0.015, "+1.5%")
    record("자동손절없음유지", issubclass(HeroRelativeEngine, v7523.NoStopTimedHeroEngine), "V75.23 상속")
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
        engine = HeroRelativeEngine(
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
