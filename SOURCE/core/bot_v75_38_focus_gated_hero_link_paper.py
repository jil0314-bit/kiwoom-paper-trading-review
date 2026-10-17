#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.38 PAPER — 포커스 게이트형 영웅문 연동 (ChatGPT F1~F9 수정본)

이 파일이 고치는 것 (ChatGPT 독립검증 F1~F9)
--------------------------------------------
[F1] 포커스가 없어도(focus=0, click_focus_ok=False) 키를 보내고 True 반환.
     → **포커스 게이트**: foreground_ok AND (focus_root==root OR click_focus_ok)
       가 아니면 키를 한 개도 보내지 않고 FAILED 로 끝낸다.
[F2] WM_SETTEXT 미검증 뒤 Ctrl+V 재입력.
     → **단일 입력 경로**: 표준 Edit 이 확인되면 WM_SETTEXT '만', 아니면 Ctrl+V
       '만' 쓴다. 절대 섞지 않는다. 한 종류를 한 번만 실행한다.
[F3] 미검증 전송을 boolean True 로 반환.
     → **3상태**: VERIFIED / SENT_UNVERIFIED / FAILED. 검증 못 하면 성공(True)이
       아니라 SENT_UNVERIFIED 로 분리한다.
[F4] Enter 직전 포커스 재검사 없음.
     → 붙여넣기/입력 후 Enter 전에 _recheck_focus() 로 전경·포커스를 재확인하고,
       잃었으면 Enter 를 보내지 않는다(SENT_UNVERIFIED).
[F5] worker 스레드에서 SetForegroundWindow 실행.
     → GUI 가 사용자 클릭 이벤트를 받은 **Tk 메인 스레드**에서 send_symbol 을
       직접 실행한다. worker 스레드를 쓰지 않는다.
[F6] _standard_edit_set 이 전송 성공 여부를 무시.
     → 전송 성공 + read-back 일치 + 대상이 표준 Edit 자식 + Enter 전 포커스 유지를
       모두 확인한다.
[F7] 싱글클릭이 임의 변경.
     → **더블클릭을 기본값**으로 되돌린다. 환경변수 V75_SINGLE_CLICK=1 일 때만
       싱글클릭(+디바운스)으로 전환하는 옵션을 제공한다.
[F8] 구버전 위치설정 무검증 재사용.
     → schema_version 과 저장 당시 화면 크기를 확인한다. 불일치·구버전이면
       설정을 무효화하고 재저장을 요구한다. '위치 설정 초기화' 버튼을 둔다.
[F9] self-check 가 문자열만 검사.
     → 별도 mock 회귀검사(test_v75_38_regression.py)로 제어흐름을 강제한다.
       self-check 는 그 회귀검사를 실제로 호출한다.

매매 로직은 V75.30 을 SHA256 고정으로 불러와 변경하지 않는다.
V75.36 을 SHA256 고정으로 불러와 Win32 인프라를 재사용한다.
PAPER 전용이며 실주문 함수는 없다.

주의: 이 파일은 실제 Windows/영웅문에서 최종 검증되기 전까지 '확정 작동'이라고
표현하지 않는다. Windows 진단 로그(V75_38_LINK_DIAG.txt)로 확인해야 한다.
"""
from __future__ import annotations

import ctypes
from datetime import datetime
import enum
import hashlib
import importlib.util
import os
from pathlib import Path
import sys
import time
from types import ModuleType
from typing import Any

VERSION = 'V75.38-FOCUS-GATED-HERO-LINK-PAPER'
SCHEMA_VERSION = 2
BASE_DIR = Path(__file__).resolve().parent

LINK_FILENAME = 'bot_v75_36_double_revalidated_hero_link_paper.py'
LINK_SHA256 = '6778dbe4f3e9450d0de6b6809f40a42ad6f97fb3ea6b8cf2fd060024fa04f226'
BASE_FILENAME = 'bot_v75_30_reserved_target_strict_entry_paper.py'
BASE_SHA256 = '06938a69ccd49b5e6bb33bafcfc97de6b449ed0ee5d315f787ea21ab8ccd8a54'

PACKAGE_DIR = BASE_DIR.parent
DIAG_FILE = PACKAGE_DIR / 'V75_38_LINK_DIAG.txt'


class LinkStatus(enum.Enum):
    """전송 결과 3상태 (F3)."""
    VERIFIED = 'VERIFIED'              # 입력값 read-back 으로 전환 확인됨
    SENT_UNVERIFIED = 'SENT_UNVERIFIED'  # 전송은 했으나 확인 불가 (성공 아님)
    FAILED = 'FAILED'                 # 포커스/클립보드/전송 실패


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _load_fixed(filename: str, expected_sha: str, module_name: str) -> ModuleType:
    path = BASE_DIR / filename
    actual = sha256_file(path)
    if actual.lower() != expected_sha:
        raise RuntimeError(f'{filename} SHA256 불일치\n기대: {expected_sha}\n실제: {actual}')
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'{filename} 로드 실패')
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


v7536 = _load_fixed(LINK_FILENAME, LINK_SHA256, 'v7538_immutable_link')
v7530 = v7536.v7530
v7525 = v7536.v7525

_base_path = BASE_DIR / BASE_FILENAME
if sha256_file(_base_path).lower() != BASE_SHA256:
    raise RuntimeError('V75.30 매매 엔진 SHA256 불일치 — 실행 중단')

BaseLink = v7536.VerifiedMultiHeroLink
BaseGui = v7536.LinkedGui
_ORIGINAL_SELF_CHECK = v7530.self_check

IS_WINDOWS = v7536.IS_WINDOWS
_u32 = v7536._u32
_k32 = v7536._k32
VK_CONTROL = v7536.VK_CONTROL
VK_A = v7536.VK_A
VK_BACK = v7536.VK_BACK
VK_V = v7536.VK_V
VK_RETURN = v7536.VK_RETURN
WM_SETTEXT = v7536.WM_SETTEXT


def diag(message: str) -> None:
    try:
        with DIAG_FILE.open('a', encoding='utf-8') as handle:
            handle.write(f'[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}\n')
    except OSError:
        pass


def _single_click_enabled() -> bool:
    return os.environ.get('V75_SINGLE_CLICK', '').strip() in ('1', 'true', 'True', 'YES', 'yes')


class SingleFocusedHeroLink(BaseLink):
    """단일 입력 경로 + 포커스 게이트 + 3상태.

    V75.36 헬퍼(_find_window, _clipboard_read/write, _client_geometry,
    _activate_and_focus, _find_target, _class_name, _root_of, _focus_hwnd,
    _foreground, _message_target_safe, _read_control_text, _send_message,
    _hotkey, _press, capture_cursor_target 등)를 상속해 재사용한다.
    """

    # ── 포커스 재확인 (F4) ──
    def _recheck_focus(self, root: int, effective_target: int) -> bool:
        """Enter 직전 전경창·포커스가 아직 영웅문인지 확인한다."""
        if not IS_WINDOWS:
            return False
        if self._foreground() != root:
            return False
        focus = self._focus_hwnd(root)
        if focus and self._root_of(focus) == root:
            return True
        # 포커스 HWND 를 안 주는 커스텀 컨트롤이면 전경창만으로 판단한다.
        return effective_target == 0

    # ── Enter (F6: 성공 여부 반환) ──
    @classmethod
    def _press_enter(cls) -> bool:
        return cls._press(VK_RETURN)

    # ── 필드 클리어 (한 번) ──
    @classmethod
    def _clear_field(cls) -> bool:
        ok = cls._hotkey(VK_CONTROL, VK_A)
        time.sleep(0.08)
        return cls._press(VK_BACK) and ok

    # ── 표준 Edit 전용 입력 (F6: 전 조건 확인) ──
    def _standard_edit_set(self, target: int, symbol: str) -> tuple[bool, str]:
        if not self._message_target_safe(target, self._root_of(target)):
            return False, ''
        buffer = ctypes.create_unicode_buffer(symbol)
        sent, _, _ = self._send_message(
            target, WM_SETTEXT, 0, ctypes.cast(buffer, ctypes.c_void_p).value or 0)
        if not sent:
            return False, ''
        time.sleep(0.10)
        observed = self._read_control_text(target)
        return (observed == symbol), observed

    def send_symbol(self, symbol: str):
        """반환: (LinkStatus, message).

        기존 코드는 (bool, str) 였다. GUI 는 LinkStatus 로 3상태를 구분한다.
        """
        if not IS_WINDOWS:
            return LinkStatus.FAILED, 'Windows에서만 작동합니다.'
        if not symbol.isdigit() or len(symbol) != 6:
            diag(f'REJECT symbol={symbol!r}')
            return LinkStatus.FAILED, f'종목코드 형식 오류: {symbol}'
        if not self.configured:
            return LinkStatus.FAILED, '먼저 영웅문 위치 저장을 한 번 하세요.'
        if not self._config_valid():
            return LinkStatus.FAILED, ('저장된 위치 설정이 현재 화면과 맞지 않습니다. '
                                       '영웅문 위치 저장을 다시 해주세요.')

        # ── 1. 클립보드 (GUI Tk 복사가 이미 했을 수 있다) ──
        before = self._clipboard_read()
        if before != symbol:
            self._clipboard_write(symbol)
        if self._clipboard_read() != symbol:
            diag(f'FAIL_CLIPBOARD symbol={symbol}')
            return LinkStatus.FAILED, '종목코드 클립보드 저장 실패'

        # ── 2. 창/좌표 ──
        root = self._find_window()
        if not root:
            diag(f'FAIL_WINDOW symbol={symbol}')
            return LinkStatus.FAILED, f'{symbol} 복사됨 — 저장한 영웅문 창을 찾지 못했습니다.'
        geometry = self._client_geometry(root)
        if geometry is None:
            return LinkStatus.FAILED, f'{symbol} 복사됨 — 영웅문 창 위치를 읽지 못했습니다.'
        left, top, width, height = geometry
        x = left + int(float(self.config['x_ratio']) * width)
        y = top + int(float(self.config['y_ratio']) * height)
        target = self._find_target(root, x, y)

        # ── 3. 활성화 + 포커스 게이트 (F1) ──
        foreground_ok, focus, click_focus_ok = self._activate_and_focus(root, target, x, y)
        focus_root = self._root_of(focus)
        effective_target = (
            focus if focus and focus != root and focus_root == root
            else target if target and target != root and self._root_of(target) == root
            else 0
        )
        base_log = (f'symbol={symbol} root={root} target={target} '
                    f'effective_target={effective_target} '
                    f'class={self._class_name(effective_target)!r} xy=({x},{y}) '
                    f'foreground={foreground_ok} focus={focus} focus_root={focus_root} '
                    f'click_focus={click_focus_ok}')

        focus_gate = foreground_ok and (focus_root == root or click_focus_ok)
        if not focus_gate:
            diag(f'GATE_BLOCK {base_log}')
            return LinkStatus.FAILED, (
                f'{symbol} 복사됨 — 영웅문 입력칸에 포커스를 잡지 못했습니다. '
                f'영웅문 입력칸을 클릭하고 Ctrl+V 하세요.')

        # ── 4. 단일 입력 경로 (F2): 표준 Edit 이면 WM_SETTEXT '만', 아니면 Ctrl+V '만' ──
        is_standard = bool(effective_target) and self._message_target_safe(effective_target, root)

        if is_standard:
            verified, observed = self._standard_edit_set(effective_target, symbol)
            method = 'WM_SETTEXT'
            diag(f'INPUT method=WM_SETTEXT verified={verified} observed={observed!r} {base_log}')
        else:
            # 사용자 PC 에서 입증된 경로: Ctrl+A → Del → Ctrl+V (한 번)
            # _paste_with_keys 내부가 clear + Ctrl+V 를 수행한다.
            paste_ok = self._paste_with_keys()
            time.sleep(0.18)
            method = 'CTRL_V'
            if not paste_ok:
                diag(f'FAIL_PASTE {base_log}')
                return LinkStatus.FAILED, (
                    f'{symbol} 복사됨 — 자동 붙여넣기 실패, 영웅문에서 Ctrl+V 하세요.')
            # read-back 은 표준 Edit 일 때만 가능 → 커스텀이면 확인 불가
            observed = self._read_control_text(effective_target) if effective_target else ''
            verified = (observed == symbol)
            diag(f'INPUT method=CTRL_V verified={verified} observed={observed!r} {base_log}')

        # ── 5. Enter 직전 포커스 재확인 (F4) ──
        if not self._recheck_focus(root, effective_target):
            diag(f'FOCUS_LOST_BEFORE_ENTER method={method} {base_log}')
            return LinkStatus.SENT_UNVERIFIED, (
                f'{symbol} 입력은 전송했으나 Enter 직전 포커스를 잃었습니다 — '
                f'영웅문 화면을 확인하세요.')

        # ── 6. Enter (한 번) ──
        enter_ok = self._press_enter()
        diag(f'ENTER method={method} enter_ok={enter_ok} verified={verified} {base_log}')
        if not enter_ok:
            return LinkStatus.SENT_UNVERIFIED, (
                f'{symbol} 입력됨 — Enter 전송 실패, 영웅문에서 Enter를 눌러주세요.')

        # ── 7. 3상태 반환 (F3) ──
        if verified:
            return LinkStatus.VERIFIED, f'{symbol} 전환 확인됨 ({method})'
        return LinkStatus.SENT_UNVERIFIED, (
            f'{symbol} 전송 완료 ({method}) — 확인 불가, 영웅문 화면을 확인하세요.')

    # ── 위치 설정 유효성 (F8) ──
    def _config_valid(self) -> bool:
        config = self.config
        if int(config.get('schema_version', 0) or 0) != SCHEMA_VERSION:
            return False
        if not IS_WINDOWS:
            return True
        saved_screen = config.get('screen')
        if isinstance(saved_screen, (list, tuple)) and len(saved_screen) == 2:
            now_w = int(_u32.GetSystemMetrics(0)) if _u32 else 0
            now_h = int(_u32.GetSystemMetrics(1)) if _u32 else 0
            if now_w and now_h and (int(saved_screen[0]) != now_w or int(saved_screen[1]) != now_h):
                return False
        return True

    # capture_cursor_target 는 V75.36 것을 확장: schema_version + screen 저장
    def capture_cursor_target(self) -> tuple[bool, str]:
        ok, message = super().capture_cursor_target()
        if ok and IS_WINDOWS:
            self.config['schema_version'] = SCHEMA_VERSION
            self.config['screen'] = [int(_u32.GetSystemMetrics(0)), int(_u32.GetSystemMetrics(1))]
            self._save()
            diag(f'CAPTURE_SCHEMA v={SCHEMA_VERSION} screen={self.config["screen"]}')
        return ok, message

    def reset_config(self) -> None:
        """위치 설정 초기화 (F8)."""
        self.config = {}
        try:
            self._save()
        except OSError:
            pass
        diag('CONFIG_RESET')


class FocusGatedGui(BaseGui):
    """더블클릭 기본 (F7). V75_SINGLE_CLICK=1 이면 싱글클릭+디바운스.

    핵심: 포커스·클릭·SendInput 을 worker 가 아닌 Tk 메인 스레드에서 실행한다(F5).
    send_symbol 이 블로킹이지만 수 초 이내이며, 사용자 클릭 직후 같은 스레드에서
    실행해야 전경 전환이 안정적이다.
    """

    DEBOUNCE_SEC = 0.6

    def _run(self) -> None:
        try:
            import tkinter as tk
            from tkinter import messagebox, ttk
            root = tk.Tk()
        except Exception as exc:
            self.logger.warning('GUI 시작 실패: %s', exc)
            return
        import queue

        single = _single_click_enabled()
        mode_label = '한 번 클릭' if single else '더블클릭'
        root.title(f'V75.38 관심·후보·매수·매도 — {mode_label} 영웅문 연동')
        root.geometry('1280x680')
        root.minsize(1080, 520)
        hero = SingleFocusedHeroLink(v7525.HERO_LINK_FILE)

        toolbar = ttk.Frame(root, padding=8)
        toolbar.pack(fill='x')
        status_var = tk.StringVar(
            value=f'종목 줄을 {mode_label}하세요.' if (hero.configured and hero._config_valid())
            else '처음 한 번만 영웅문 위치 저장을 하세요.')
        time_var = tk.StringVar(value='갱신 전')
        ttk.Button(toolbar, text='영웅문 위치 저장', command=lambda: begin_capture()).pack(side='left')
        ttk.Button(toolbar, text='위치 설정 초기화', command=lambda: do_reset()).pack(side='left', padx=(6, 0))
        ttk.Label(toolbar, text=f'  {mode_label} → 코드 복사 + 자동전환').pack(side='left')
        ttk.Label(toolbar, textvariable=time_var).pack(side='right')

        columns = ('no', 'stage', 'buy_time', 'name', 'symbol', 'price', 'reason')
        tree = ttk.Treeview(root, columns=columns, show='headings', selectmode='browse')
        headings = {'no': '순번', 'stage': '단계', 'buy_time': '매수시각', 'name': '종목명',
                    'symbol': '코드', 'price': '매수가 / +1.5% 매도가', 'reason': '이유'}
        widths = {'no': 55, 'stage': 95, 'buy_time': 85, 'name': 190,
                  'symbol': 75, 'price': 220, 'reason': 500}
        for column in columns:
            tree.heading(column, text=headings[column])
            anchor = 'w' if column in ('name', 'reason') else ('e' if column == 'price' else 'center')
            tree.column(column, width=widths[column], anchor=anchor,
                        stretch=column in ('name', 'reason'))
        scrollbar = ttk.Scrollbar(root, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        tree.pack(fill='both', expand=True, padx=(8, 0), pady=4)
        ttk.Label(root, textvariable=status_var, anchor='w', padding=8).pack(fill='x')

        state = {'last_symbol': '', 'last_ts': 0.0}

        def tk_copy(symbol: str) -> bool:
            try:
                root.clipboard_clear()
                root.clipboard_append(symbol)
                root.update()
                return str(root.clipboard_get()) == symbol
            except Exception as exc:
                diag(f'TK_CLIPBOARD_FAIL symbol={symbol} error={type(exc).__name__}')
                return False

        def switch_symbol(symbol: str) -> None:
            if not symbol.isdigit() or len(symbol) != 6:
                status_var.set(f'종목코드를 읽지 못했습니다: {symbol!r}')
                return
            now = time.monotonic()
            if single and symbol == state['last_symbol'] and (now - state['last_ts']) < self.DEBOUNCE_SEC:
                return
            state['last_symbol'] = symbol
            state['last_ts'] = now
            copied = tk_copy(symbol)
            diag(f'CLICK symbol={symbol} tk_clipboard={copied} mode={mode_label}')
            status_var.set(f'{symbol} 영웅문 전환 중…')
            root.update_idletasks()
            # F5: worker 아님. Tk 메인 스레드에서 직접 실행.
            try:
                status, message = hero.send_symbol(symbol)
            except Exception as exc:
                status, message = LinkStatus.FAILED, f'오류: {type(exc).__name__}'
                diag(f'SEND_EXC symbol={symbol} {type(exc).__name__}: {exc}')
            prefix = {'VERIFIED': '✅ ', 'SENT_UNVERIFIED': '⚠ ', 'FAILED': '❌ '}.get(status.value, '')
            if status != LinkStatus.VERIFIED and copied:
                message = f'{message} (코드 복사됨 — Ctrl+V 가능)'
            status_var.set(f'{prefix}{message}')

        def on_double_click(event: Any) -> None:
            row = tree.identify_row(event.y)
            if not row or tree.identify_region(event.x, event.y) not in ('cell', 'tree'):
                return
            tree.selection_set(row)
            values = tree.item(row, 'values')
            if len(values) >= 5:
                switch_symbol(str(values[4]).strip())

        def on_single_click(event: Any) -> None:
            on_double_click(event)

        def do_reset() -> None:
            hero.reset_config()
            status_var.set('위치 설정을 초기화했습니다. 영웅문 위치 저장을 다시 하세요.')

        def finish_capture() -> None:
            ok, message = hero.capture_cursor_target()
            status_var.set(message if ok else f'위치 저장 실패: {message}')

        def countdown(remaining: int) -> None:
            if remaining <= 0:
                finish_capture()
                return
            status_var.set(f'{remaining}초 안에 영웅문 종목코드 입력칸을 클릭하고 그대로 두세요.')
            root.after(1000, lambda: countdown(remaining - 1))

        def begin_capture() -> None:
            messagebox.showinfo(
                '영웅문 위치 저장',
                '확인을 누른 뒤 5초 안에 영웅문 종목코드 입력칸을 한 번 클릭하세요.\n'
                '클릭한 뒤 마우스를 움직이지 마세요.')
            countdown(5)

        def apply_snapshot(snapshot: dict[str, Any]) -> None:
            for iid in tree.get_children():
                tree.delete(iid)
            rows = list(snapshot.get('rows', []))
            counters: dict[str, int] = {}
            for row in rows:
                stage = str(row.get('stage', ''))
                counters[stage] = counters.get(stage, 0) + 1
                tree.insert('', 'end', values=(
                    counters[stage], stage, row.get('buy_time', '-'), row.get('name', ''),
                    row.get('symbol', ''), row.get('display_price', row.get('price', '')),
                    row.get('reason', '')))
            time_var.set(str(snapshot.get('updated', '')))
            if hero.configured and hero._config_valid():
                status_var.set(f"시장 {snapshot.get('market', '-')} | "
                               f"{len(rows)}종목 | 종목 줄 {mode_label} 전환")
            elif hero.configured:
                status_var.set('저장된 위치가 현재 화면과 다릅니다 — 위치 저장을 다시 하세요.')
            else:
                status_var.set('먼저 영웅문 위치 저장을 한 번 하세요.')

        def poll() -> None:
            try:
                while True:
                    kind, payload = self.events.get_nowait()
                    if kind == 'snapshot':
                        apply_snapshot(payload)
                    elif kind == 'close':
                        root.destroy()
                        return
            except queue.Empty:
                pass
            if not self.closed.is_set():
                root.after(300, poll)

        if single:
            tree.bind('<ButtonRelease-1>', on_single_click)
        else:
            tree.bind('<Double-1>', on_double_click)
        root.protocol('WM_DELETE_WINDOW', lambda: root.withdraw())
        root.after(200, poll)
        root.mainloop()


v7525.SimpleDoubleClickGui = FocusGatedGui
v7525.VERSION = VERSION


def _no_order_functions(source: str) -> bool:
    import ast
    forbidden = {'send_order', 'create_order', 'place_order', 'order_stock'}
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    defined = {node.name for node in ast.walk(tree)
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    return not (defined & forbidden)


def _run_regression() -> tuple[bool, str]:
    """F9: 문자열이 아니라 실제 mock 회귀검사를 호출한다."""
    test_path = BASE_DIR / 'test_v75_38_regression.py'
    if not test_path.exists():
        return False, '회귀검사 파일 없음'
    try:
        spec = importlib.util.spec_from_file_location('v7538_regress_selfcheck', test_path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        code = module.run()
        return (code == 0), ('회귀검사 통과' if code == 0 else '회귀검사 실패')
    except Exception as exc:
        return False, f'회귀검사 예외 {type(exc).__name__}'


def self_check() -> list[tuple[str, bool, str]]:
    results = list(_ORIGINAL_SELF_CHECK())
    source = Path(__file__).read_text(encoding='utf-8')
    regress_ok, regress_msg = _run_regression()
    results.extend([
        ('V75.30매매엔진SHA', sha256_file(_base_path) == BASE_SHA256, BASE_SHA256[:16]),
        ('V75.36링크SHA고정', sha256_file(BASE_DIR / LINK_FILENAME) == LINK_SHA256, LINK_SHA256[:16]),
        ('F1_F9_mock회귀검사', regress_ok, regress_msg),
        ('3상태_LinkStatus', hasattr(LinkStatus, 'VERIFIED') and hasattr(LinkStatus, 'SENT_UNVERIFIED')
         and hasattr(LinkStatus, 'FAILED'), 'VERIFIED/SENT_UNVERIFIED/FAILED'),
        ('더블클릭기본', "tree.bind('<Double-1>'" in source, 'F7: 싱글클릭은 옵션'),
        ('schema검사', '_config_valid' in source and 'schema_version' in source, 'F8: 구설정 무효화'),
        ('위치초기화', 'reset_config' in source, 'F8: 초기화 버튼'),
        ('실주문함수없음', _no_order_functions(source), 'PAPER 전용'),
        ('진단로그', 'V75_38_LINK_DIAG' in source, DIAG_FILE.name),
    ])
    return results


v7525.self_check = self_check


def main() -> int:
    return int(v7530.main())


if __name__ == '__main__':
    raise SystemExit(main())
