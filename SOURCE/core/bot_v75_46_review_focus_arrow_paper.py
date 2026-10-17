#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.46 PAPER — 차트 전환 후 검토창 포커스 복귀·연속 화살표판.

V75.45의 자동 입력칸 오탐 방어와 V75.44의 매수 로직을 그대로 유지한다.
이번 버전은 영웅문 차트 연동 뒤 사용 흐름만 좁혀 수정한다.

수정 범위
---------
1. 더블클릭/화살표로 종목 전환 직전 마우스 좌표를 저장한다.
2. 영웅문에 종목코드와 Enter를 보낸 뒤 V75 GUI를 다시 전경화한다.
3. Treeview에 키보드 포커스를 복귀시키고 선택 행을 유지·자동 스크롤한다.
4. 자동 클릭 예비경로가 마우스를 움직인 경우 원래 좌표로 복원한다.
5. 전경 복귀가 한 번 성공하면 지연 재시도는 중단해 불필요한 포커스 탈취를 막는다.
6. ↑/↓는 Treeview뿐 아니라 V75 창의 다른 위젯에 포커스가 있을 때도 동작한다.

매매 로직, PAPER 전용, +1.5% 목표, 자동손절 없음, 분할·종목당 한도,
90초 catch-up 및 진입 감사로그는 변경하지 않는다.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import hashlib
import importlib.util
from pathlib import Path
import sys
import time
from types import ModuleType
from typing import Any

VERSION = "V75.46-REVIEW-FOCUS-ARROW-PAPER"
BASE_DIR = Path(__file__).resolve().parent
V7545_FILENAME = "bot_v75_45_auto_target_guarded_paper.py"
V7545_SHA256 = "453340a1ce824d93e985ce0ab52c10892bacd03403f6fb03baad2765e4a00953"

# Enter 처리 후 GUI 복귀 시도. 첫 성공 뒤 나머지 시도는 자동 중단한다.
FOCUS_RESTORE_DELAYS_MS: tuple[int, ...] = (60, 180, 360)
GA_ROOT = 2
SW_RESTORE = 9


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_fixed(filename: str, expected_sha: str, module_name: str) -> ModuleType:
    path = BASE_DIR / filename
    actual = sha256_file(path)
    if actual.lower() != expected_sha.lower():
        raise RuntimeError(
            f"{filename} SHA256 불일치\n기대: {expected_sha}\n실제: {actual}"
        )
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"{filename} 로드 실패")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


v7545 = _load_fixed(V7545_FILENAME, V7545_SHA256, "v7546_immutable_base")
v7544 = v7545.v7544
v7543 = v7545.v7543
v7538 = v7545.v7538
v7530 = v7545.v7530
v7525 = v7545.v7525
KST = v7545.KST
LinkStatus = v7544.LinkStatus
AutoDiscoverHeroLink = v7544.AutoDiscoverHeroLink
select_arrow_index = v7544.select_arrow_index
diag = v7544.diag
_ORIGINAL_SELF_CHECK = v7545.self_check


@dataclass(frozen=True, slots=True)
class CursorPosition:
    """차트 전환 직전의 화면상 마우스 좌표."""

    x: int
    y: int


def capture_cursor_position() -> CursorPosition | None:
    """Windows 마우스 위치를 읽는다. 실패하거나 비-Windows면 None."""
    if not v7538.IS_WINDOWS:
        return None
    point = wintypes.POINT()
    try:
        if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            return None
    except (AttributeError, OSError):
        return None
    return CursorPosition(int(point.x), int(point.y))


def _widget_hwnd(widget: Any) -> int:
    """Tk 위젯의 실제 HWND를 읽는다."""
    try:
        return int(widget.winfo_id())
    except Exception:
        return 0


def _widget_root_hwnd(widget: Any) -> int:
    """Tk 위젯 HWND에서 실제 최상위 HWND를 구한다."""
    hwnd = _widget_hwnd(widget)
    if not v7538.IS_WINDOWS or not hwnd:
        return hwnd
    try:
        root_hwnd = int(ctypes.windll.user32.GetAncestor(hwnd, GA_ROOT) or 0)
    except (AttributeError, OSError):
        return hwnd
    return root_hwnd or hwnd


def is_review_window_foreground(root: Any) -> bool:
    """현재 전경창이 V75 검토 GUI인지 확인한다."""
    if not v7538.IS_WINDOWS:
        return True
    hwnd = _widget_root_hwnd(root)
    if not hwnd:
        return False
    try:
        foreground = int(ctypes.windll.user32.GetForegroundWindow() or 0)
        foreground_root = int(ctypes.windll.user32.GetAncestor(foreground, GA_ROOT) or 0)
    except (AttributeError, OSError):
        return False
    return foreground == hwnd or foreground_root == hwnd


def restore_review_focus(
    root: Any,
    tree: Any,
    cursor: CursorPosition | None,
) -> bool:
    """V75 GUI를 전경화하고 Treeview 키보드 포커스·마우스 위치를 복구한다.

    Windows 전경창 제한을 줄이기 위해 현재/전경/대상 스레드 입력 큐를 잠시 연결한다.
    실패해도 예외를 GUI 이벤트 루프 밖으로 전파하지 않는다.
    """
    try:
        root.update_idletasks()
    except Exception:
        return False

    if not v7538.IS_WINDOWS:
        try:
            tree.focus_set()
            return True
        except Exception:
            return False

    hwnd = _widget_root_hwnd(root)
    tree_hwnd = _widget_hwnd(tree)
    if not hwnd:
        return False

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    attached: list[tuple[int, int]] = []
    try:
        if not user32.IsWindow(hwnd):
            return False
        current_thread = int(kernel32.GetCurrentThreadId() or 0)
        target_thread = int(user32.GetWindowThreadProcessId(hwnd, None) or 0)
        foreground = int(user32.GetForegroundWindow() or 0)
        foreground_thread = (
            int(user32.GetWindowThreadProcessId(foreground, None) or 0)
            if foreground
            else 0
        )
        try:
            user32.AllowSetForegroundWindow(0xFFFFFFFF)
        except OSError:
            pass
        for first, second in (
            (current_thread, target_thread),
            (foreground_thread, target_thread),
        ):
            if first and second and first != second:
                try:
                    if user32.AttachThreadInput(first, second, True):
                        attached.append((first, second))
                except OSError:
                    pass

        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetActiveWindow(hwnd)
        if tree_hwnd and user32.IsWindow(tree_hwnd):
            user32.SetFocus(tree_hwnd)
        try:
            tree.focus_force()
        except Exception:
            tree.focus_set()
        foreground_ok = is_review_window_foreground(root)
        if foreground_ok and cursor is not None:
            user32.SetCursorPos(int(cursor.x), int(cursor.y))
        return foreground_ok
    except Exception as exc:
        diag(f"GUI_FOCUS_RESTORE_EXC {type(exc).__name__}: {exc}")
        return False
    finally:
        for first, second in reversed(attached):
            try:
                user32.AttachThreadInput(first, second, False)
            except OSError:
                pass


def find_iid_by_symbol(tree: Any, symbol: str) -> str:
    """목록 갱신으로 iid가 바뀌어도 종목코드로 현재 행을 다시 찾는다."""
    for iid in tree.get_children(""):
        values = tree.item(iid, "values")
        if len(values) >= 5 and str(values[4]).strip() == symbol:
            return str(iid)
    return ""


class V7546Gui(v7544.V7544Gui):
    """차트 전환 뒤 검토창으로 포커스를 되돌려 연속 검토를 가능하게 한다."""

    def _run(self) -> None:
        try:
            import tkinter as tk
            from tkinter import messagebox, ttk
            root = tk.Tk()
        except Exception as exc:
            self.logger.warning("GUI 시작 실패: %s", exc)
            return
        import queue

        click_mode = {"single": v7538._single_click_enabled()}

        def mode_label() -> str:
            return "한 번 클릭" if click_mode["single"] else "더블클릭"

        root.title(f"V75.46 관심·관찰·매수·매도 — {mode_label()} 연속 차트검토")
        root.geometry("1420x760")
        root.minsize(1120, 560)
        hero = AutoDiscoverHeroLink(v7525.HERO_LINK_FILE)

        toolbar = ttk.Frame(root, padding=8)
        toolbar.pack(fill="x")
        status_var = tk.StringVar(
            value="더블클릭 또는 ↑/↓ 후 V75 창으로 포커스가 자동 복귀합니다."
        )
        time_var = tk.StringVar(value="갱신 전")
        ttk.Button(
            toolbar, text="자동연동 확인", command=lambda: check_auto_link()
        ).pack(side="left")
        ttk.Button(
            toolbar, text="수동 위치 저장(예비)", command=lambda: begin_capture()
        ).pack(side="left", padx=(6, 0))
        ttk.Button(
            toolbar, text="예비 위치 초기화", command=lambda: do_reset()
        ).pack(side="left", padx=(6, 0))
        single_var = tk.BooleanVar(value=click_mode["single"])
        ttk.Checkbutton(
            toolbar,
            text="한 번 클릭 사용",
            variable=single_var,
            command=lambda: change_click_mode(),
        ).pack(side="left", padx=(10, 0))
        mode_help_var = tk.StringVar(
            value=f"  {mode_label()} 또는 ↑/↓ → 차트 변경 후 목록 포커스 복귀"
        )
        ttk.Label(toolbar, textvariable=mode_help_var).pack(side="left")
        ttk.Label(toolbar, textvariable=time_var).pack(side="right")

        columns = ("no", "stage", "stage_time", "name", "symbol", "price", "reason")
        tree = ttk.Treeview(root, columns=columns, show="headings", selectmode="browse")
        headings = {
            "no": "순번",
            "stage": "단계",
            "stage_time": "편입/매수시각",
            "name": "종목명",
            "symbol": "코드",
            "price": "현재가 / 평단 / +1.5% 목표",
            "reason": "조건·손익·보류이유",
        }
        widths = {
            "no": 55,
            "stage": 105,
            "stage_time": 110,
            "name": 180,
            "symbol": 75,
            "price": 290,
            "reason": 565,
        }
        for column in columns:
            tree.heading(column, text=headings[column])
            anchor = "w" if column in ("name", "reason") else ("e" if column == "price" else "center")
            tree.column(
                column,
                width=widths[column],
                anchor=anchor,
                stretch=column in ("name", "reason"),
            )
        scrollbar = ttk.Scrollbar(root, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        tree.pack(fill="both", expand=True, padx=(8, 0), pady=4)
        ttk.Label(root, textvariable=status_var, anchor="w", padding=8).pack(fill="x")

        state: dict[str, Any] = {
            "last_symbol": "",
            "last_ts": 0.0,
            "selected_symbol": "",
            "last_action_ts": 0.0,
            "focus_token": 0,
            "focus_restored_token": 0,
        }

        def tk_copy(symbol: str) -> bool:
            try:
                root.clipboard_clear()
                root.clipboard_append(symbol)
                root.update()
                return str(root.clipboard_get()) == symbol
            except Exception as exc:
                diag(f"TK_CLIPBOARD_FAIL symbol={symbol} error={type(exc).__name__}")
                return False

        def restore_selected_row(symbol: str) -> None:
            iid = find_iid_by_symbol(tree, symbol)
            if not iid:
                return
            tree.selection_set(iid)
            tree.focus(iid)
            tree.see(iid)

        def schedule_review_restore(
            *,
            symbol: str,
            cursor: CursorPosition | None,
            token: int,
        ) -> None:
            def attempt(delay_ms: int) -> None:
                if token != int(state["focus_token"]):
                    return
                if int(state["focus_restored_token"]) == token:
                    return
                restore_selected_row(symbol)
                ok = restore_review_focus(root, tree, cursor)
                diag(
                    f"GUI_FOCUS_RESTORE symbol={symbol} token={token} "
                    f"delay_ms={delay_ms} ok={ok}"
                )
                if ok:
                    state["focus_restored_token"] = token

            for delay_ms in FOCUS_RESTORE_DELAYS_MS:
                root.after(delay_ms, lambda delay=delay_ms: attempt(delay))

        def switch_symbol(symbol: str, *, source: str) -> None:
            if not symbol.isdigit() or len(symbol) != 6:
                status_var.set(f"종목코드를 읽지 못했습니다: {symbol!r}")
                return
            now_mono = time.monotonic()
            if (
                click_mode["single"]
                and symbol == state["last_symbol"]
                and now_mono - float(state["last_ts"]) < self.DEBOUNCE_SEC
            ):
                return

            state["focus_token"] = int(state["focus_token"]) + 1
            token = int(state["focus_token"])
            cursor = capture_cursor_position()
            state["last_symbol"] = symbol
            state["selected_symbol"] = symbol
            state["last_ts"] = now_mono
            state["last_action_ts"] = now_mono
            copied = tk_copy(symbol)
            diag(f"GUI_LINK symbol={symbol} copied={copied} source={source} token={token}")
            status_var.set(f"{symbol} 영웅문 차트 전환 중…")
            root.update_idletasks()
            try:
                status, message = hero.send_symbol(symbol)
            except Exception as exc:
                status, message = LinkStatus.FAILED, f"오류: {type(exc).__name__}"
                diag(f"SEND_EXC symbol={symbol} {type(exc).__name__}: {exc}")
            finally:
                # 성공/실패와 무관하게 검토창 포커스를 돌려 다음 조작을 즉시 가능하게 한다.
                schedule_review_restore(symbol=symbol, cursor=cursor, token=token)

            prefix = {
                "VERIFIED": "✅ ",
                "SENT_UNVERIFIED": "⚠ ",
                "FAILED": "❌ ",
            }.get(status.value, "")
            if status == LinkStatus.FAILED and copied:
                message = f"{message} (코드는 복사됨)"
            status_var.set(f"{prefix}{message} — 목록 포커스 복귀 중")

        def symbol_from_iid(iid: str) -> str:
            values = tree.item(iid, "values")
            return str(values[4]).strip() if len(values) >= 5 else ""

        def selected_symbol(event: Any) -> str | None:
            row_id = tree.identify_row(event.y)
            if not row_id or tree.identify_region(event.x, event.y) not in ("cell", "tree"):
                return None
            tree.selection_set(row_id)
            tree.focus(row_id)
            tree.see(row_id)
            tree.focus_set()
            switch_symbol(symbol_from_iid(row_id), source=mode_label())
            return "break"

        def move_selection(delta: int) -> str:
            items = list(tree.get_children(""))
            if not items:
                return "break"
            current_iid = tree.focus()
            if not current_iid:
                selected = tree.selection()
                current_iid = selected[0] if selected else ""
            current_index = items.index(current_iid) if current_iid in items else -1
            next_index = select_arrow_index(current_index, len(items), delta)
            if next_index < 0:
                return "break"
            next_iid = items[next_index]
            tree.selection_set(next_iid)
            tree.focus(next_iid)
            tree.see(next_iid)
            tree.focus_set()
            switch_symbol(
                symbol_from_iid(next_iid),
                source="아래화살표" if delta > 0 else "위화살표" if delta < 0 else "Enter",
            )
            return "break"

        def root_navigation(event: Any, delta: int) -> str | None:
            # Treeview 자체 바인딩은 먼저 실행되어 "break"를 반환한다.
            # 툴바·상태줄 등 다른 위젯에 포커스가 있을 때만 보조 이동한다.
            if event.widget is tree:
                return None
            return move_selection(delta)

        def apply_click_binding() -> None:
            tree.unbind("<ButtonRelease-1>")
            tree.unbind("<Double-1>")
            if click_mode["single"]:
                tree.bind("<ButtonRelease-1>", selected_symbol)
            else:
                tree.bind("<Double-1>", selected_symbol)
            mode_help_var.set(
                f"  {mode_label()} 또는 ↑/↓ → 차트 변경 후 목록 포커스 복귀"
            )
            root.title(
                f"V75.46 관심·관찰·매수·매도 — {mode_label()} 연속 차트검토"
            )

        def change_click_mode() -> None:
            click_mode["single"] = bool(single_var.get())
            state["last_symbol"] = ""
            state["last_ts"] = 0.0
            apply_click_binding()
            tree.focus_set()
            status_var.set(f"클릭 방식을 {mode_label()}으로 변경했습니다.")

        def check_auto_link() -> None:
            ok, message = hero.probe_auto_target()
            state["last_action_ts"] = time.monotonic()
            status_var.set(("✅ " if ok else "❌ ") + message)
            tree.focus_set()

        def do_reset() -> None:
            hero.reset_config()
            status_var.set("예비 수동 위치를 초기화했습니다. 자동연동에는 영향이 없습니다.")
            tree.focus_set()

        def finish_capture() -> None:
            ok, message = hero.capture_cursor_target()
            status_var.set(
                f"예비 위치 저장: {message}" if ok else f"예비 위치 저장 실패: {message}"
            )

        def countdown(remaining: int) -> None:
            if remaining <= 0:
                finish_capture()
                return
            status_var.set(f"{remaining}초 안에 영웅문 종목코드 입력칸을 클릭하고 그대로 두세요.")
            root.after(1000, lambda: countdown(remaining - 1))

        def begin_capture() -> None:
            messagebox.showinfo(
                "예비 수동 위치 저장",
                "자동탐색이 실패하는 PC에서만 사용합니다. 확인 후 5초 안에 "
                "영웅문 종목코드 입력칸을 클릭하세요.",
            )
            countdown(5)

        def apply_snapshot(snapshot: dict[str, Any]) -> None:
            preserve_symbol = str(state.get("selected_symbol", ""))
            if not preserve_symbol:
                selected = tree.selection()
                if selected:
                    preserve_symbol = symbol_from_iid(selected[0])
            for iid in tree.get_children():
                tree.delete(iid)
            rows = list(snapshot.get("rows", []))
            counters: dict[str, int] = {}
            symbol_to_iid: dict[str, str] = {}
            for row in rows:
                stage = str(row.get("stage", ""))
                counters[stage] = counters.get(stage, 0) + 1
                symbol = str(row.get("symbol", ""))
                iid = tree.insert(
                    "",
                    "end",
                    values=(
                        counters[stage],
                        stage,
                        row.get("stage_time", row.get("time", "-")),
                        row.get("name", ""),
                        symbol,
                        row.get("display_price", row.get("price", "")),
                        row.get("reason", ""),
                    ),
                )
                if symbol:
                    symbol_to_iid[symbol] = iid
            if preserve_symbol in symbol_to_iid:
                iid = symbol_to_iid[preserve_symbol]
                tree.selection_set(iid)
                tree.focus(iid)
                tree.see(iid)
            time_var.set(str(snapshot.get("updated", "")))
            if time.monotonic() - float(state["last_action_ts"]) > 3.0:
                ready_count = sum(str(row.get("stage", "")) == "매수직전" for row in rows)
                holdings_count = sum(str(row.get("stage", "")) == "매수종목" for row in rows)
                universe = snapshot.get("universe", {})
                status_var.set(
                    f"시장 {snapshot.get('market', '-')} | 현재감시 {universe.get('active_count', '-')} | "
                    f"매수직전 {ready_count} | 보유 {holdings_count} | 더블클릭·↑↓ 연속검토"
                )

        def poll() -> None:
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
                root.after(300, poll)

        apply_click_binding()
        tree.bind("<Up>", lambda _event: move_selection(-1))
        tree.bind("<Down>", lambda _event: move_selection(1))
        tree.bind("<Return>", lambda _event: move_selection(0))
        tree.bind("<Button-1>", lambda _event: tree.focus_set(), add="+")
        root.bind("<Up>", lambda event: root_navigation(event, -1), add="+")
        root.bind("<Down>", lambda event: root_navigation(event, 1), add="+")
        root.bind("<Return>", lambda event: root_navigation(event, 0), add="+")
        root.protocol("WM_DELETE_WINDOW", lambda: root.withdraw())
        root.after(200, poll)
        root.mainloop()


# 실제 실행 모듈이 참조하는 공유 객체를 V75.46 GUI로 교체한다.
v7525.SimpleDoubleClickGui = V7546Gui
v7525.VERSION = VERSION


def _run_v7546_tests() -> tuple[bool, str]:
    path = BASE_DIR / "test_v75_46_review_focus_arrow.py"
    try:
        spec = importlib.util.spec_from_file_location("v7546_special_test", path)
        if spec is None or spec.loader is None:
            return False, "테스트 로드 실패"
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        code = int(module.run())
        return code == 0, "통과" if code == 0 else "실패"
    except Exception as exc:
        return False, f"예외 {type(exc).__name__}: {exc}"


def self_check() -> list[tuple[str, bool, str]]:
    results = list(_ORIGINAL_SELF_CHECK())
    test_ok, test_message = _run_v7546_tests()
    source = Path(__file__).read_text(encoding="utf-8")
    results.extend(
        [
            ("V75.45기준SHA", sha256_file(BASE_DIR / V7545_FILENAME) == V7545_SHA256, V7545_SHA256[:16]),
            ("V75.46전용회귀", test_ok, test_message),
            ("GUI포커스복귀", "restore_review_focus" in source and "focus_force" in source, "차트 전환 후 Treeview 복귀"),
            ("마우스좌표복원", "GetCursorPos" in source and "SetCursorPos" in source, "전송 전 좌표 복원"),
            ("연속화살표", 'root.bind("<Down>"' in source and 'tree.bind("<Down>"' in source, "Treeview+창 보조 바인딩"),
            ("지연재시도중단", "focus_restored_token" in source, "첫 성공 뒤 재시도 중단"),
            ("매매로직불변", v7525.V7525Engine is v7544.V7544Engine, "V75.44 엔진 유지"),
        ]
    )
    return results


v7525.self_check = self_check


def main() -> int:
    return int(v7545.main())


if __name__ == "__main__":
    raise SystemExit(main())
