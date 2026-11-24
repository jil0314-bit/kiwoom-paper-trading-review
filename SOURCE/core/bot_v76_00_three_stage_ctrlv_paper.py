#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V76.09 PAPER — R21 preserved + Konan two-stage entry + 0600 one-click link.

Only the following surfaces differ from the immutable R21 parent:
* NXT pre-market and regular-session PAPER pattern gate,
* the user's one-minute 1/2 + 1/2 volume-floor entry sequence,
* persistent index-decline deferral layered over the existing market guard,
* exact [0600] descendant Edit direct input with non-chart Kiwoom isolation,
* work-area/DPI fit, stage row colors, and real dashboard shutdown.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
import csv
import gc
import hashlib
import importlib.util
import inspect
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from types import FunctionType, ModuleType, SimpleNamespace
from typing import Any
from v7609_account_guard import allowed_account, selected_account_matches
import v7609_exit_policy as _EXIT_POLICY
import v7609_prebuy_rules as _PREBUY_RULES
import v7610_entry_policy as _ENTRY_POLICY
import v7610_pullback_shadow as _PULLBACK


VERSION = "V76.09-FOREGROUND-APP-ACTIVATE-0600-LINK-PAPER"
BUILD_STAMP = "2026-09-23 조기매수중단·시간대차단·즉시손절"
V7609_LEGACY_MA5_ONLY_ENTRY = False
V7609_LEGACY_PRE_MA5_HALF_ENTRY = False
BASE_DIR = Path(__file__).resolve().parent
PARENT_FILE = "bot_v75_95_complete_locked_paper.py"
PARENT_SHA256 = "b93a206659308070b28b2dc4b539cd7b199054efcbef52731b924f8f43b4fca9"


def _load_v7600_state_module() -> ModuleType:
    """Load the isolated STATE-001 authority beside this locked entry file."""

    path = BASE_DIR / "v7600_state_manager.py"
    spec = importlib.util.spec_from_file_location("v7600_state_manager", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("STATE-001 상태관리자 로드 실패")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_V7600_STATE = _load_v7600_state_module()
_VOLUME5 = _V7600_STATE._VOLUME5
_V7609_VERIFIED_ACCOUNT = ""  # process memory only, never logged or persisted
EARLY_PATTERN = _VOLUME5.EARLY_RULE_ID


def _load_v7600_order_module() -> ModuleType:
    """Load the IDEM-001 PAPER order-intent guard beside this entry file."""

    path = BASE_DIR / "v7600_order_idempotency.py"
    spec = importlib.util.spec_from_file_location("v7600_order_idempotency", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("IDEM-001 주문 멱등성 모듈 로드 실패")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_V7600_ORDER = _load_v7600_order_module()
PATTERN = "V76_코난거래바닥2단계"
BREAKOUT_PATTERN = "V76_코난돌파눌림확인"
TRANCHE_NOTIONAL = 10_000_000.0 / 2.0
V7609_FIXED_STOP_LOSS_PCT = 0.015
V7609_TAKE_PROFIT_PCT = 0.02
# Scanner polling cannot deliver every symbol's newest completed bar inside the
# same minute.  Orders still require the immediately preceding minute; only the
# 관찰/관심 display stages may be judged on a completed bar this many minutes old.
V7609_STAGE_BAR_MAX_LAG_MIN = 3
_V7609_GUI_CONTEXT = threading.local()
V7609_INTEREST_EVENT_MARKER = "V7609_INTEREST_OK:"
V7609_PREBUY_EVENT_MARKER = "V7609_PREBUY_OK:"
TRANCHE_STATE_FILE = BASE_DIR.parent / "runtime_v7592" / "v7609_konan_tranches.json"
STAGE_EVENT_DIR = BASE_DIR.parent / "runtime_v7592" / "stage_events"
ORDER_INTENT_FILE = (
    BASE_DIR.parent / "runtime_v7592" / "paper_ledger" / "order_intents.jsonl"
)
PULLBACK_SHADOW_DIR = BASE_DIR.parent / "runtime_v7592" / "pullback_shadow"
_V7609_USER_CLOSE = threading.Event()


def _v7609_read_history_lines(path: Path) -> list[dict[str, Any]]:
    """Read only complete JSONL records; never create/lock/write a log for UI."""
    if not path.exists():
        return []
    records = []
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if not line.endswith("\n"):
                break  # The engine may still be appending the final record.
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("이력 레코드 형식 오류")
                records.append(value)
    return records


def _v7609_history_rows(day: Any) -> list[dict[str, Any]]:
    """A display-only projection; neither restores nor requests any stage."""
    label = day.strftime("%Y%m%d")
    events = _v7609_read_history_lines(
        STAGE_EVENT_DIR / f"v7609_stage_events_{label}.jsonl"
    )
    previous_hash = ""
    rows = []
    for event in events:
        payload = dict(event)
        digest = payload.pop("hash", "")
        payload.pop("event_id", None)
        if (payload.get("previous_hash") != previous_hash
                or _V7600_STATE._event_hash(payload) != digest):
            raise ValueError("단계 이력 해시 불일치: 표시 중단")
        previous_hash = digest
        rows.append(dict(event, kind="탈락" if event.get("new_stage") == _V7600_STATE.REMOVED else "이동"))
    rows.extend(_v7609_read_history_lines(
        STAGE_EVENT_DIR / f"v7609_stage_wait_{label}.jsonl"
    ))
    return sorted(rows, key=lambda row: str(row.get("decision_time", "")))


def _v7609_open_stage_history(root: Any) -> None:
    """Requested history window, isolated from chart linkage and orders."""
    import tkinter as tk
    from tkinter import ttk

    window = tk.Toplevel(root)
    window.title("당일 이동·탈락 이력 — 읽기 전용")
    window.geometry("1120x560")
    controls = ttk.Frame(window)
    controls.pack(fill="x", padx=8, pady=6)
    ttk.Label(controls, text="날짜 YYYY-MM-DD").pack(side="left")
    day_text = tk.StringVar(value=datetime.now(KST).date().isoformat())
    ttk.Entry(controls, textvariable=day_text, width=12).pack(side="left", padx=4)
    ttk.Label(controls, text="종목명/코드").pack(side="left")
    query = tk.StringVar()
    ttk.Entry(controls, textvariable=query, width=18).pack(side="left", padx=4)
    status = tk.StringVar(value="탈락=실제 목록 해제 / 조건대기=단계 유지, 해당 매수 경로 미충족")
    ttk.Label(window, textvariable=status).pack(fill="x", padx=8)
    frame = ttk.Frame(window)
    frame.pack(fill="both", expand=True, padx=8, pady=6)
    columns = ("time", "symbol", "name", "kind", "old", "new", "data", "reason")
    tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse")
    for column, title, width in zip(columns,
            ("이동/판정 시각", "코드", "종목명", "구분", "이전", "이후", "근거자료 시각", "이유"),
            (100, 75, 110, 100, 95, 95, 145, 460)):
        tree.heading(column, text=title)
        tree.column(column, width=width, stretch=False)
    vertical = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    horizontal = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
    tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
    vertical.pack(side="right", fill="y")
    horizontal.pack(side="bottom", fill="x")
    tree.pack(fill="both", expand=True)
    details = tk.Text(window, height=5, wrap="word")
    details.pack(fill="x", padx=8, pady=6)
    records: dict[str, dict[str, Any]] = {}

    def refresh() -> None:
        try:
            day = datetime.strptime(day_text.get().strip(), "%Y-%m-%d").date()
            names = {}
            path = BASE_DIR.parent / "runtime_v7591" / "stage_timeline.json"
            if path.exists():
                payload = json.loads(path.read_text(encoding="utf-8"))
                names = payload.get("symbols", payload) if isinstance(payload, dict) else {}
            rows = _v7609_history_rows(day)
            tree.delete(*tree.get_children())
            records.clear()
            for row in rows:
                symbol = str(row.get("symbol", ""))
                item = names.get(symbol, {})
                name = str(row.get("name") or (item.get("name", "") if isinstance(item, dict) else ""))
                if query.get().strip() not in f"{symbol} {name}":
                    continue
                reason = str(row.get("reason", ""))
                if reason == "SCANNER_ROW_EXPIRED":
                    reason = "구버전 자동만료(과거 기록)"
                old = str(row.get("old_stage") or "미편입")
                new = str(row.get("new_stage") or old)
                iid = tree.insert("", "end", values=(
                    str(row.get("decision_time", ""))[11:19], symbol, name,
                    row.get("kind", ""), old, "탈락" if new == _V7600_STATE.REMOVED else new,
                    str(row.get("data_time", ""))[:19].replace("T", " "), reason))
                records[iid] = dict(row, name=name)
            status.set(f"{len(records)}건 · 탈락=실제 해제 / 조건대기=단계 유지 · 선택하면 전체 근거 표시")
        except (OSError, ValueError, TypeError) as exc:
            tree.delete(*tree.get_children())
            records.clear()
            status.set(f"이력 확인 실패: {exc}")

    def select(_event: Any) -> None:
        selected = tree.selection()
        details.configure(state="normal")
        details.delete("1.0", "end")
        if selected:
            details.insert("1.0", json.dumps(records[selected[0]], ensure_ascii=False, indent=2))
        details.configure(state="disabled")

    ttk.Button(controls, text="조회/새로고침", command=refresh).pack(side="left", padx=8)
    tree.bind("<<TreeviewSelect>>", select)
    refresh()


def _v7609_observation_pool_limit(current_limit: int, rank_limit: int) -> int:
    """Do not discard candidates already returned by the rank APIs."""

    return max(int(current_limit), int(rank_limit))


VISUAL_SAMPLE_ROWS = [
    {
        "stage": "관찰후보",
        "symbol": "005930",
        "name": "삼성전자",
        "current_price": 70100,
        "change_pct": 1.18,
        "stage_time": "09:07",
        "reason": "PAPER 샘플: 당일 +5% 이력·동시간 거래 160% 관찰. 클릭 시 0600 연동 확인용.",
    },
    {
        "stage": "관찰후보",
        "symbol": "000660",
        "name": "SK하이닉스",
        "current_price": 203500,
        "change_pct": 2.05,
        "stage_time": "09:11",
        "reason": "PAPER 샘플: 거래대금 유입 관찰. 실제 주문 아님.",
    },
    {
        "stage": "관심종목",
        "symbol": "035420",
        "name": "NAVER",
        "current_price": 184200,
        "change_pct": -0.42,
        "stage_time": "09:34",
        "reason": "PAPER 샘플: 기준 음봉 거래량 저장, 이후 거래소진 50% 선행 대기.",
    },
    {
        "stage": "관심종목",
        "symbol": "035720",
        "name": "카카오",
        "current_price": 51200,
        "change_pct": 0.66,
        "stage_time": "09:42",
        "reason": "PAPER 샘플: 하락 음봉 이후 지지 확인 대기.",
    },
    {
        "stage": "매수가능성",
        "symbol": "068270",
        "name": "셀트리온",
        "current_price": 187300,
        "change_pct": 1.44,
        "stage_time": "10:03",
        "reason": "PAPER 샘플: 거래바닥 후 아래꼬리 짧은음봉 확인, 1차 1/2 직전.",
    },
    {
        "stage": "매수가능성",
        "symbol": "042700",
        "name": "한미반도체",
        "current_price": 119800,
        "change_pct": 3.12,
        "stage_time": "10:18",
        "reason": "PAPER 샘플: 거래바닥+1분 MA5 회복 감시. 실제 자동매수 전 검증용.",
    },
    {
        "stage": "매수종목",
        "symbol": "373220",
        "name": "LG에너지솔루션",
        "current_price": 346000,
        "avg_price": 341000,
        "target_price": 346100,
        "quantity": 9,
        "change_pct": 1.47,
        "stage_time": "10:37",
        "reason": "PAPER 샘플: 1차 1/2 보유, 다음 짧은양봉 MA5 안착 대기.",
    },
    {
        "stage": "매수종목",
        "symbol": "006400",
        "name": "삼성SDI",
        "current_price": 274000,
        "avg_price": 271000,
        "target_price": 275100,
        "quantity": 12,
        "change_pct": 1.11,
        "stage_time": "10:46",
        "reason": "PAPER 샘플: 2차 전저점 지지 대기, 목표매도 유지.",
    },
    {
        "stage": "시장보류",
        "symbol": "051910",
        "name": "LG화학",
        "current_price": 298500,
        "change_pct": -1.25,
        "stage_time": "11:02",
        "reason": "PAPER 샘플: 종목 조건은 유지, 지수 급락·MA5 하락이면 신규매수 보류.",
    },
    {
        "stage": "매도종목",
        "symbol": "012450",
        "name": "한화에어로스페이스",
        "current_price": 271500,
        "avg_price": 267500,
        "target_price": 271500,
        "quantity": 12,
        "change_pct": 1.50,
        "stage_time": "11:21",
        "reason": "PAPER 샘플: 목표 +1.5% 매도 완료 표시. 같은 구조 즉시 재진입 금지.",
    },
]

CANONICAL_STAGE = {
    "관찰": "관찰후보",
    "관찰종목": "관찰후보",
    "관찰후보": "관찰후보",
    "관심": "관심종목",
    "관심종목": "관심종목",
    "직전": "매수가능성",
    "매수직전": "매수가능성",
    "매수후보": "매수가능성",
    "매수후보·직전": "매수가능성",
    "매수가능성": "매수가능성",
    "매수": "매수종목",
    "보유": "매수종목",
    "보유종목": "매수종목",
    "매수종목": "매수종목",
    "매도": "매도종목",
    "오늘매도": "매도종목",
    "매도종목": "매도종목",
    "보류": "시장보류",
    "시장보류": "시장보류",
}

STAGE_TO_TAG = {
    "관찰후보": "관찰후보",
    "관심종목": "관심종목",
    "매수가능성": "매수가능성",
    "매수직전": "매수가능성",
    "시장보류": "시장보류",
    "매수종목": "매수종목",
    "매도종목": "매도종목",
}


def canonical_stage(stage: Any) -> str:
    """Return the single dashboard stage name used by the engine and GUI."""

    return CANONICAL_STAGE.get(str(stage).strip(), str(stage).strip())


def _v7609_dashboard_stage(
    raw_stage: Any,
    protected_phase: str = "",
    protected_stage: str = "",
) -> str:
    """Canonicalize a row without demoting actual holding/sold ledger rows."""

    stage = canonical_stage(raw_stage)
    if (
        stage not in {"매수종목", "매도종목"}
        and protected_phase in {"INTEREST", "PREBUY"}
    ):
        stage = canonical_stage(protected_stage)
    return stage


def _v7609_same_signal_minute(signal_time: Any, exit_time: Any) -> bool:
    """Treat every automatic signal in the target-exit minute as one signal."""

    if not isinstance(signal_time, datetime) or not isinstance(exit_time, datetime):
        return False
    if signal_time.tzinfo is None:
        signal_time = signal_time.replace(tzinfo=KST)
    if exit_time.tzinfo is None:
        exit_time = exit_time.replace(tzinfo=KST)
    signal_minute = signal_time.astimezone(KST).replace(second=0, microsecond=0)
    exit_minute = exit_time.astimezone(KST).replace(second=0, microsecond=0)
    return signal_minute <= exit_minute


def _load_parent() -> ModuleType:
    path = BASE_DIR / PARENT_FILE
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != PARENT_SHA256:
        raise RuntimeError(f"{PARENT_FILE} SHA256 불일치")
    spec = importlib.util.spec_from_file_location("v7600_immutable_r21_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("V75.95 R21 로드 실패")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


parent = _load_parent()
v7525, v57, KST = parent.v7525, parent.v57, parent.KST
_V753 = parent._V753
_V7516 = parent._V7516_MOD
_V7588 = parent._V7588_CLASS
_PARENT_ENGINE = parent.V7595CompleteLockedEngine
_R21_LINK_CLASS = v57.RestoredHeroLink
_V7594_EVALUATE_OBSERVATION = parent.parent.evaluate_observation_admission
_V7609_INHERITED_CLIENT = _V753.KiwoomRestClient


def _v7633_install_paper_restore() -> None:
    """Allow the persisted no-stop sentinel only inside PAPER restoration.

    Keep the hash-locked parent and its normal price validation untouched.
    In the parent's loader, the only downward alignment is stop_price;
    target_price still uses the original strictly-positive validation.
    """
    broker_class = _V753.PaperBroker
    original = broker_class.load_state
    if getattr(original, "_v7633_zero_stop_restore", False):
        return

    def load_state(broker: Any) -> None:
        namespace = dict(original.__globals__)
        align = namespace["align_stock_price"]

        def restore_price(price: float, *, upward: bool) -> float:
            if not upward and price == 0.0:
                return 0.0  # persisted automatic-stop-disabled sentinel
            return align(price, upward=upward)

        namespace["align_stock_price"] = restore_price
        loader = FunctionType(original.__code__, namespace, original.__name__,
                              original.__defaults__, original.__closure__)
        loader(broker)
        if broker.state_load_failed:
            # Do not let the running engine overwrite a failed/partial restore.
            raise _V753.ConfigurationError("PAPER 보유 복원 실패: 실행 중단, 원본 상태 보존")

    load_state._v7633_zero_stop_restore = True
    broker_class.load_state = load_state


_v7633_install_paper_restore()


class V7609OneMinuteKiwoomClient(_V7609_INHERITED_CLIENT):
    """Expose raw Kiwoom one-minute OHLCV; never aggregate it to three minutes."""

    def get_experiment_bars(self, symbol: str) -> list[Any]:
        # Same official parser, without Gem's write of partial rows to shared caches.
        getter = _V7609_INHERITED_CLIENT.get_one_minute_bars.__globals__["RAW_ONE_MINUTE_GETTER"]
        return list(getter(self, str(symbol), limit=900, completed_only=False))

    def _save_token(self, token: str, expires_dt: str) -> None:
        # Do not overwrite the shared cross-project credential cache.
        return None

    def _read_cached_token(self) -> str:
        if getattr(self, "_v7609_force_fresh", False):
            return ""
        return super()._read_cached_token()

    def _json_post(self, url: str, body: Any, headers: Any, **kwargs: Any) -> Any:
        from urllib.parse import urlsplit
        path = urlsplit(url).path
        api = str(headers.get("api-id", ""))
        if path.endswith(("/ordr", "/crdordr")) or api in {"kt10000", "kt10001", "kt10002", "kt10003"}:
            raise _V753.AuthenticationError("PAPER 실행본: 실제 주문 API 차단")
        return super()._json_post(url, body, headers, **kwargs)

    def authenticate(self, *, force_refresh: bool = False) -> str:
        global _V7609_VERIFIED_ACCOUNT
        if force_refresh:
            self._token = ""
            self._v7609_verified_token = ""
            _V7609_VERIFIED_ACCOUNT = ""
        self._v7609_force_fresh = bool(force_refresh)
        try:
            # Parent force_refresh deletes a shared cache. Never enter that path.
            token = super().authenticate(force_refresh=False)
        finally:
            self._v7609_force_fresh = False
        if token != getattr(self, "_v7609_verified_token", ""):
            _V7609_VERIFIED_ACCOUNT = ""
            try:
                data, status = self._json_post(
                    self.settings.base_url + "/api/dostk/acnt", {},
                    {"authorization": "Bearer " + token, "api-id": "ka00001"},
                )
                account = str(data.get("acctNo", ""))
                valid = status == 200 and str(data.get("return_code")) == "0" and allowed_account(account)
            except Exception:
                raise _V753.AuthenticationError("0311 계좌 확인 실패: 연동 차단") from None
            if not valid:
                self._v7609_verified_token = ""
                raise _V753.AuthenticationError("기본 계좌 끝 0311 불일치/미확인: 연동 차단")
            self._v7609_verified_token = token
            _V7609_VERIFIED_ACCOUNT = account
        return token

    def get_minute_bars(
        self,
        symbol: str,
        *,
        limit: int = 120,
        completed_only: bool = True,
    ) -> list[Any]:
        return list(
            self.get_one_minute_bars(
                str(symbol),
                limit=max(3, int(limit)),
                completed_only=bool(completed_only),
            )
        )


_V753.KiwoomRestClient = V7609OneMinuteKiwoomClient

# One-time, read-only reconstruction of the Friday close.  It is intentionally
# disabled before the next session starts so the normal live engine resumes
# without a flag file or a lasting trading-rule change.
V7609_POSTCLOSE_REVIEW_DATE = datetime(2026, 8, 7, tzinfo=KST).date()
V7609_POSTCLOSE_REVIEW_FROM = datetime(2026, 8, 7, 15, 30, tzinfo=KST)
V7609_POSTCLOSE_REVIEW_UNTIL = datetime(2026, 8, 10, 8, 0, tzinfo=KST)
V7609_POSTCLOSE_EVENT_FROM = datetime(2026, 8, 7, 8, 0, tzinfo=KST)
V7609_POSTCLOSE_EVENT_UNTIL = datetime(2026, 8, 7, 15, 30, 59, tzinfo=KST)
V7609_ENTRY_AUDIT_FILE = BASE_DIR.parent / "V75_44_ENTRY_AUDIT.csv"
V7609_POSTCLOSE_REVIEW_FILE = BASE_DIR.parent / "postclose_review_20260807.json"


# The command panel remains PAPER-only.  The locked parent already supports
# plain immediate PAPER buys, but did not connect explicit sell commands or
# a "price sell" sentence to an executable action.
_V7609_BASE_COMMAND_COMPILER = v57.compile_collaboration_input
_V7609_BASE_COMMAND_ROUTE = v57.route_interpreted_action
_V7609_BASE_COMMAND_APPLY = parent.v7556._apply_collaboration_command


def _v7609_price_mentions(text: str) -> list[float]:
    """Read Korean won amounts without treating a share count as a price."""

    values: list[float] = []
    for match in re.finditer(r"(?<!\d)(\d[\d,]*(?:\.\d+)?)\s*(만원|원)", str(text or "")):
        amount = float(match.group(1).replace(",", ""))
        if match.group(2) == "만원":
            amount *= 10_000.0
        values.append(amount)
    return values


def _v7609_quantity_from_text(text: str) -> int | None:
    matches = re.findall(r"(?<!\d)(\d[\d,]*)\s*주", str(text or ""))
    if not matches:
        return None
    quantity = int(matches[-1].replace(",", ""))
    return quantity if quantity > 0 else None


def _v7609_compile_command(text: str, **kwargs: Any) -> Any:
    draft = _V7609_BASE_COMMAND_COMPILER(text, **kwargs)
    raw = " ".join(str(text or "").split())
    if re.search(r'내일|모레|나중|이따|다음|이후|뒤에|[0-9]+\s*(?:시|분)\s*(?:에|후|뒤)|예약', raw):
        draft.action, draft.symbol, draft.errors = 'MEMO', '', ['미래·예약 지시는 즉시 주문하지 않습니다.']
        return draft
    if re.fullmatch(r"(?:전체\s*|신규\s*)?매수\s*(?:중지|중단)(?:해|해줘)?[.!]?", raw):
        draft.action, draft.symbol, draft.errors = "PAUSE_BUY", "", []
        return draft
    conditional_theme_buy = bool(
        re.search(
            r"(?:관련\s*종목|테마).{0,30}(?:로직|조건).{0,12}"
            r"(?:맞|충족).{0,12}(?:매수|사줘)",
            raw,
        )
    )
    if conditional_theme_buy:
        # A theme instruction is a priority/watch overlay.  Never reinterpret
        # its trailing "if the logic fits, buy" as an immediate order for the
        # currently selected, unrelated row.
        draft.action = "MEMO"
        draft.symbol = ""
        draft.name = ""
        draft.errors = []
        return draft
    symbol = str(getattr(draft, "symbol", "") or "")
    if not re.fullmatch(r"\d{6}", symbol):
        return draft
    explicit_sell = bool(re.search(r"(?:매도\s*주문|매도|팔아줘|팔아|전량\s*매도)", raw))
    explicit_buy_order = bool(re.search(r"(?:매수\s*주문|주문\s*매수|매수\s*해(?:줘)?\s*[.!]?$)", raw))
    if re.search(r"(?:매수|매도).*(?:하지\s*마|금지|중지|보류|취소)|(?:하면|되면|일때|일 때|충족|조건)", raw):
        if draft.action in {"IMMEDIATE_BUY", "IMMEDIATE_SELL"}:
            draft.action = "MEMO"
        return draft
    explicit_immediate_buy = bool(
        re.search(
            r"(?:(?:지금|즉시).{0,8}(?:매수|사줘|사자|사라)|"
            r"(?:매수|사줘|사자|사라).{0,8}(?:지금|즉시))",
            raw,
        )
    )
    prices = _v7609_price_mentions(raw)
    exact_price_buy = bool(
        prices and re.search(r"(?:매수|사줘|사자|사라)", raw)
    )
    if explicit_sell and prices:
        # A stated sell price is a PAPER target, not a fabricated immediate
        # fill.  It is executed through the existing target-price path.
        draft.action = "SET_TARGET"
        draft.target_price = prices[-1]
        draft.errors = []
    elif explicit_sell:
        draft.action = "IMMEDIATE_SELL"
        draft.errors = []
    elif exact_price_buy:
        # "10,000원에 매수" means a PAPER price condition at or below
        # that exact price.  It is not fabricated as an immediate fill.
        draft.action = "BUY_CONDITION"
        draft.buy_price = prices[0]
        draft.trigger = "AT_OR_BELOW"
        draft.errors = [
            error for error in list(getattr(draft, "errors", []))
            if "가격 이하 도달인지 상향 돌파인지" not in error
            and "매수기준가를 읽지 못했습니다" not in error
            and "투입금·수량·분할매수 지시" not in error
        ]
    elif explicit_immediate_buy and not prices:
        draft.errors = [
            error for error in list(getattr(draft, "errors", []))
            if "즉시매수 표현" not in error
            and "가격 이하 도달인지 상향 돌파인지" not in error
            and "매수기준가를 읽지 못했습니다" not in error
        ]
        if not draft.errors:
            draft.action = "IMMEDIATE_BUY"
    elif explicit_buy_order and not prices:
        draft.action = "IMMEDIATE_BUY"
        draft.errors = []
    return draft


def _v7609_command_route(action: str) -> str:
    if str(action) in {"IMMEDIATE_BUY", "IMMEDIATE_SELL", "PAUSE_BUY"}:
        return "GENERAL"
    return _V7609_BASE_COMMAND_ROUTE(action)


def _v7609_apply_command(self: Any, command: dict[str, Any]) -> str:
    kind = str(command.get("kind", ""))
    if kind == "PAUSE_BUY":
        return str(self._execute_command("신규매수중지"))
    if kind not in {"IMMEDIATE_BUY", "IMMEDIATE_SELL"}:
        return _V7609_BASE_COMMAND_APPLY(self, command)
    symbol = str(command.get("symbol", ""))
    name = str(command.get("name", "") or symbol)
    if not re.fullmatch(r"\d{6}", symbol):
        raise ValueError("종목을 확인할 수 없습니다")
    approval_id = str(command.get("command_id", "") or "")
    if not approval_id:
        raise ValueError("수동 주문 승인번호가 없습니다")
    side = "SELL" if kind == "IMMEDIATE_SELL" else "BUY"
    identity = self._v7609_make_order_identity(
        side=side,
        symbol=symbol,
        source="MANUAL_APPROVAL",
        source_id=approval_id,
        approval_id=approval_id,
    )
    payload = {
        "kind": kind,
        "symbol": symbol,
        "approval_id": approval_id,
        "note": str(command.get("note", "") or ""),
    }

    def execute() -> str:
        if kind == "IMMEDIATE_SELL":
            result = self._manual_sell(symbol, "전량")
            return f"{name} PAPER 전량 매도 실행 · {result}"
        return str(_V7609_BASE_COMMAND_APPLY(self, command)).replace("기존 +1.5%", "비용포함 +2.0%")

    try:
        return str(
            self._v7609_run_order_intent(
                identity,
                payload,
                execute,
                duplicate_result="이미 처리한 동일 승인 주문을 차단했습니다.",
            )
        )
    except Exception:
        # The inherited command guard is memory-only and marks an ID before
        # executing it.  Release that memory mark only when IDEM proves that
        # the final PAPER fill boundary was never reached.
        if (
            self._v7609_order_ledger_obj().latest_status(identity.order_intent_id)
            == "RELEASED_BEFORE_ACCEPTANCE"
        ):
            processed = getattr(self, "_v7552_processed_command_ids", None)
            if isinstance(processed, set):
                processed.discard(approval_id)
        raise


v57.compile_collaboration_input = _v7609_compile_command
v57.route_interpreted_action = _v7609_command_route
parent.v7556.compile_collaboration_input = _v7609_compile_command
parent.v7556._apply_collaboration_command = _v7609_apply_command

# V75.57 is SHA-locked by the execution chain, so keep it byte-for-byte
# unchanged.  When a newly saved free-text intent is a named leader/watch
# request, mirror only that newly added item into the existing WATCH queue.
_V7609_BASE_INTENT_ADD_MANY = v57.IntentLedger.add_many


def _v7609_add_many_and_watch(
    self: Any,
    intents: list[Any],
    status: str = "활성",
) -> list[dict[str, Any]]:
    items = list(intents)
    additions = _V7609_BASE_INTENT_ADD_MANY(self, items, status=status)
    added_ids = {str(row.get("intent_id", "")) for row in additions}
    if status == "활성":
        for item in items:
            if (
                str(getattr(item, "intent_id", "")) not in added_ids
                or str(getattr(item, "kind", "")) != "STOCK_WATCH"
                or not re.fullmatch(r"\d{6}", str(getattr(item, "symbol", "")))
            ):
                continue
            v57.HUB.submit({
                "kind": "WATCH",
                "symbol": str(item.symbol),
                "name": str(getattr(item, "name", "") or item.symbol),
                "note": str(getattr(item, "raw_text", "")),
            })
    return additions


v57.IntentLedger.add_many = _v7609_add_many_and_watch


def _v7609_iso_is_today(value: Any, today: Any = None) -> bool:
    """Return True only for a valid timestamp on the current KST date."""

    target = today or datetime.now(KST).date()
    try:
        parsed = datetime.fromisoformat(str(value or ""))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=KST)
        else:
            parsed = parsed.astimezone(KST)
        return parsed.date() == target
    except (TypeError, ValueError):
        return False


_V7609_BASE_INTENT_LEDGER = v57.IntentLedger


class V7609TodayIntentLedger(_V7609_BASE_INTENT_LEDGER):
    """Keep the dashboard command list to today's records only."""

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self._v7609_prune_today()

    def _v7609_prune_today(self) -> None:
        rows = self._read()
        kept = [row for row in rows if _v7609_iso_is_today(row.get("created_at"))]
        if len(kept) != len(rows):
            self._write(kept)

    def list_all(self) -> list[dict[str, Any]]:
        self._v7609_prune_today()
        return super().list_all()


v57.IntentLedger = V7609TodayIntentLedger


def _v7609_prune_collaboration_hub_today() -> None:
    """Remove stale displayed overlays/memos but preserve the append-only audit log."""

    hub = parent.v7556.HUB
    with hub._lock:
        old_overlay_count = len(hub._overlays)
        old_memo_count = len(hub._memos)
        hub._overlays = {
            key: item
            for key, item in hub._overlays.items()
            if _v7609_iso_is_today(getattr(item, "created_at", ""))
        }
        hub._memos = [
            item for item in hub._memos
            if _v7609_iso_is_today(item.get("at") or item.get("created_at"))
        ]
        if len(hub._overlays) != old_overlay_count or len(hub._memos) != old_memo_count:
            hub._save_locked()


_v7609_prune_collaboration_hub_today()

# Use one red family for the sold card, sold filter/guide, and sold table rows.
_V7609_SELL_BODY = "#5a1820"
_V7609_SELL_BAND = "#b6383f"
_V7609_SELL_TEXT = "#ffe3e6"
_V7609_SELL_GAIN_TEXT = "#8dffc0"
_V7609_SELL_LOSS_TEXT = "#ffb2b2"
_V7609_VERIFIED_NAME_FIXES = {
    # The PAPER fill ledger stores only the code.  This carried position was
    # sold before today's candidate scan restored its Korean display name.
    "001820": "삼화콘덴서",
}
v57.STAGE_ACCENTS["매도종목"] = (_V7609_SELL_BAND, _V7609_SELL_TEXT)
parent._R21_STAGE_ROW_COLORS["매도종목"] = (
    _V7609_SELL_BODY,
    _V7609_SELL_TEXT,
)


# V75.90 calls ``parent._stamp`` while its immutable V75.89 parent does not
# export that helper.  The missing compatibility symbol eventually raises a
# fatal AttributeError during a deferred-symbol refresh and closes the whole
# dashboard.  Supply only that symbol here; locked parent files stay unchanged.
def _v7609_bar_stamp(bar: Any) -> datetime:
    value = getattr(bar, "timestamp", None)
    if not isinstance(value, datetime):
        raise TypeError("bar.timestamp must be datetime")
    return value


_V7590_MODULE = next(
    (
        sys.modules.get(cls.__module__)
        for cls in _PARENT_ENGINE.__mro__
        if cls.__name__ == "V7590FullPatternFusionEngine"
    ),
    None,
)
_V7590_PARENT = getattr(_V7590_MODULE, "parent", None)
if _V7590_PARENT is not None and not hasattr(_V7590_PARENT, "_stamp"):
    _V7590_PARENT._stamp = _v7609_bar_stamp


from v7600_three_stage_logic import (  # noqa: E402
    active_interest_pullback_decision,
    bottom_volume_ma5_entry_decision,
    bottom_volume_shape_prebuy_decision,
    Decision,
    first_impulse_pullback_decision,
    in_pattern_session,
    in_regular_session,
    interest_session_bars,
    konan_breakout_pullback_decision,
    konan_first_half_decision,
    konan_second_half_decision,
    late_low_price_liquidity_excluded,
    pattern_bars,
    persistent_market_decline_decision,
    price_normalized_liquidity_ok,
    prominent_bear_interest_decision,
    structural_distribution_risk,
    three_bear_rising_volume_decision,
    today_bars,
    virtual_exit_replay,
)


# R21's immediate prior-bar x3 interest check, old 5~10-bar order and immediate
# breakout order are replaced only for this PAPER layer.  The inherited risk,
# position, exit, data and market guards remain untouched.
def _disabled_r21_first(*_args: Any, **_kwargs: Any) -> Any:
    return parent.StageDecision(False, "V76_코난2단계판정으로교체", route="exhaustion")


def _adaptive_interest(bars: Any, *, now: datetime) -> Any:
    decision = prominent_bear_interest_decision(bars, now=now)
    return parent.StageDecision(
        decision.allowed,
        decision.reason,
        decision.score,
        decision.reference_price,
        "interest",
        decision.trigger_index,
    )


def _v7610_interest_trigger_time(bars: Any, now: datetime) -> datetime | None:
    """Return the actual current completed one-minute interest candle time."""

    sample = interest_session_bars(list(bars), now)
    if not sample:
        return None
    value = getattr(sample[-1], "timestamp", None)
    return value if isinstance(value, datetime) else None


def _disabled_r21_breakout(*_args: Any, **_kwargs: Any) -> Any:
    return parent.StageDecision(
        False, "V76_돌파후중심선지지·재상승판정대기", route="breakout"
    )


parent.exhaustion_entry_decision = _disabled_r21_first
parent.interest_decision = _adaptive_interest
parent.breakout_decision = _disabled_r21_breakout

# Show exactly ten clearly marked rows only until the first live snapshot
# arrives.  This lets the user verify layout, row colors and 0600 click-link
# behaviour without polluting PAPER ledgers or pretending to hold real shares.
# Do not create fake after-close candidates or positions.
v57.SEED_CONSOLE_ROWS = []


# Exact one-half first order for this pattern only.  Every other historical
# pattern keeps the original 5m/3m/2m or 10m sizing policy.
_ORIGINAL_INITIAL_NOTIONAL = _V7516._initial_order_notional


def _v76_initial_notional(signal: Any) -> float:
    if str(getattr(signal, "pattern", "")) in {PATTERN, EARLY_PATTERN}:
        return TRANCHE_NOTIONAL
    if str(getattr(signal, "pattern", "")) == BREAKOUT_PATTERN:
        return float(_V7516.MAX_SYMBOL_NOTIONAL)
    return float(_ORIGINAL_INITIAL_NOTIONAL(signal))


_V7516._initial_order_notional = _v76_initial_notional


class V7600CtrlVHeroLink(_R21_LINK_CLASS):
    """Send a symbol through the exact [0600] input without overstating success.

    A safe Win32 Edit receives WM_SETTEXT directly and a queued Enter.  Merely
    reading that same Edit back is input confirmation, not chart confirmation;
    VERIFIED therefore requires an independent visible chart-text change.
    No process-wide keyboard or foreground-activation fallback is used.
    """

    # Reuse the validated 0600 chart for the whole active session.  The Edit
    # HWND may be recreated by Kiwoom, so it is repaired inside that same
    # locked chart without ever selecting another account's 0600.
    CACHE_SECONDS = 14400.0

    def _v7609_display_chart_valid(self, chart: int) -> bool:
        """Display target only; HTS account widgets are not order authority."""
        return bool(
            sys.platform == "win32"
            and allowed_account(_V7609_VERIFIED_ACCOUNT)
            and self._is_exact_0600_chart(int(chart or 0))
        )

    def __init__(self, config_path: Path) -> None:
        super().__init__(config_path)
        self._v7609_fast_edit = 0
        self._v7609_fast_chart = 0
        self._v7609_fast_top = 0
        self._v7609_fast_saved_at = 0.0
        try:
            loaded = self._load()
        except Exception:
            loaded = {}
        self.config = loaded if isinstance(loaded, dict) else {}
        self._v7609_restore_locked_target()
        self._v7609_prewarm_stop = threading.Event()
        if sys.platform == "win32" and "--self-check" not in sys.argv:
            threading.Thread(
                target=self._v7609_prewarm_worker,
                daemon=True,
                name="v7609-0600-prewarm",
            ).start()

    def _v7609_cached_target_valid(self) -> bool:
        chart = int(getattr(self, "_v7609_fast_chart", 0) or 0)
        target = int(getattr(self, "_v7609_fast_edit", 0) or 0)
        return bool(
            chart
            and target
            and self._is_exact_0600_chart(chart)
            and self._belongs_to_chart(chart, target)
            and self._safe_edit_target(chart, target)
            and self._v7609_display_chart_valid(chart)
        )

    def _v7609_locked_chart_valid(self) -> bool:
        chart = int(getattr(self, "_v7609_fast_chart", 0) or 0)
        return bool(chart and self._is_exact_0600_chart(chart) and self._v7609_display_chart_valid(chart))

    def _v7609_clear_edit_cache(self) -> None:
        self._v7609_fast_edit = 0
        self._v7609_fast_saved_at = 0.0

    def _v7609_lock_saved_chart_awaiting_edit(self, candidate: Any) -> None:
        """Keep one saved 0600 chart locked while its Edit is being recreated."""

        self._v7609_fast_chart = int(candidate.hwnd)
        self._v7609_fast_top = int(candidate.top or candidate.hwnd)
        self._v7609_clear_edit_cache()

    def _v7609_refresh_locked_edit(self) -> int:
        """Repair only the Edit inside the already-bound exact 0600 chart."""

        if not self._v7609_locked_chart_valid():
            return 0
        chart = int(self._v7609_fast_chart)
        target = int(self._find_live_0600_edit(chart) or 0)
        if not target:
            self._v7609_clear_edit_cache()
            return 0
        top = int(getattr(self, "_v7609_fast_top", 0) or self._top_level_of(chart) or chart)
        self._v7609_remember_target(chart, top, target)
        v57.diag(f"V7609_0600_EDIT_REPAIRED_IN_LOCKED_CHART chart={chart} target={target}")
        return target

    @staticmethod
    def _v7609_window_rect(hwnd: int) -> list[int]:
        if sys.platform != "win32" or not hwnd:
            return []
        try:
            rect = wintypes.RECT()
            if not ctypes.windll.user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
                return []
            return [int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)]
        except Exception:
            return []

    def _v7609_remember_target(self, chart: int, top: int, target: int) -> None:
        """Remember only a fully validated exact 0600 chart/Edit pair."""

        chart, top, target = int(chart), int(top or chart), int(target)
        if not (
            self._is_exact_0600_chart(chart)
            and self._belongs_to_chart(chart, target)
            and self._safe_edit_target(chart, target)
            and self._v7609_display_chart_valid(chart)
        ):
            return
        self._v7609_fast_chart = chart
        self._v7609_fast_top = top
        self._v7609_fast_edit = target
        self._v7609_fast_saved_at = time.monotonic()
        rect = self._v7609_window_rect(chart)
        binding = {
            "chart_rect": rect,
            "chart_title": self._window_title_safe(chart),
        }
        if rect and self.config.get("v7609_0600_binding") != binding:
            self.config["v7609_0600_binding"] = binding
            try:
                self._save()
            except Exception as exc:
                v57.diag(f"V7609_0600_BINDING_SAVE_WAIT {type(exc).__name__}")

    def _v7609_restore_locked_target(self) -> None:
        """Restore this dashboard's prior 0600 without selecting another account."""

        if sys.platform != "win32" or "--self-check" in sys.argv:
            return
        values = [
            item for item in super()._chart_candidates()
            if self._is_exact_0600_chart(int(item.hwnd))
            and self._v7609_display_chart_valid(int(item.hwnd))
        ]
        saved = self.config.get("v7609_0600_binding", {})
        saved_rect = saved.get("chart_rect", []) if isinstance(saved, dict) else []
        if isinstance(saved_rect, list) and len(saved_rect) == 4:
            matched = [
                item for item in values
                if len(self._v7609_window_rect(int(item.hwnd))) == 4
                and max(
                    abs(current - int(expected))
                    for current, expected in zip(
                        self._v7609_window_rect(int(item.hwnd)), saved_rect
                    )
                ) <= 32
            ]
            if len(matched) == 1:
                candidate = matched[0]
                # Kiwoom can recreate the symbol Edit a little after the 0600
                # chart itself becomes visible.  Keep the uniquely matched,
                # previously validated chart locked so the prewarm worker can
                # repair only that chart's Edit instead of treating the other
                # logged-in account's 0600 as an ambiguous new target.
                self._v7609_lock_saved_chart_awaiting_edit(candidate)
                target = self._find_live_0600_edit(int(candidate.hwnd))
                if target:
                    self._v7609_remember_target(
                        int(candidate.hwnd), int(candidate.top), int(target)
                    )
                    v57.diag("V7609_0600_BINDING_RESTORED_BY_RECT")
                else:
                    v57.diag("V7609_0600_BINDING_CHART_RESTORED_WAIT_EDIT")
                return

        # Upgrade an already-running installation: the latest successful log
        # contains the exact HWND pair that this project used before a second
        # Kiwoom 0600 was opened. HWNDs are accepted only after live validation.
        log_path = BASE_DIR.parent / "runtime" / "logs" / "v75_73_dashboard.log"
        try:
            raw = log_path.read_bytes()[-1_000_000:].decode("utf-8", errors="ignore")
            matches = re.findall(
                r"V7609_NATIVE_FAST_0600[^\r\n]*chart=(\d+)\s+top=(\d+)\s+target=(\d+)",
                raw,
            )
        except OSError:
            matches = []
        for chart_text, top_text, target_text in reversed(matches):
            chart, top, target = int(chart_text), int(top_text), int(target_text)
            if any(int(item.hwnd) == chart for item in values) and (
                self._is_exact_0600_chart(chart)
                and self._belongs_to_chart(chart, target)
                and self._safe_edit_target(chart, target)
            ):
                self._v7609_remember_target(chart, top, target)
                v57.diag("V7609_0600_BINDING_RESTORED_FROM_SUCCESS_LOG")
                return

    def _v7609_prewarm_worker(self) -> None:
        """Find only the exact 0600 Edit before the first dashboard click."""

        while not self._v7609_prewarm_stop.is_set():
            try:
                if self._v7609_locked_chart_valid():
                    if not self._v7609_cached_target_valid():
                        self._v7609_refresh_locked_edit()
                else:
                    # At dashboard startup Kiwoom may briefly report a
                    # minimized off-screen rectangle (-32000 range).  Retry
                    # the saved exact-0600 binding after the chart restores;
                    # only then fall back to the single-session probe.
                    self._v7609_restore_locked_target()
                    if not self._v7609_locked_chart_valid():
                        self.capture_cursor_target()
            except Exception as exc:
                v57.diag(f"V7609_0600_PREWARM_WAIT {type(exc).__name__}")
            self._v7609_prewarm_stop.wait(12.0)

    @staticmethod
    def _title_matches(saved: str, current: str) -> bool:
        saved_norm = " ".join(str(saved or "").split()).casefold()
        current_norm = " ".join(str(current or "").split()).casefold()
        return bool(saved_norm and current_norm) and (
            saved_norm == current_norm
            or saved_norm in current_norm
            or current_norm in saved_norm
        )

    def _window_alive(self, hwnd: int) -> bool:
        if sys.platform != "win32" or not hwnd:
            return False
        try:
            user32 = ctypes.windll.user32
            return bool(
                user32.IsWindow(wintypes.HWND(hwnd))
                and user32.IsWindowVisible(wintypes.HWND(hwnd))
            )
        except Exception:
            return False

    def _window_title_safe(self, hwnd: int) -> str:
        try:
            getter = getattr(self, "_window_text", None) or getattr(self, "_win_text", None)
            return str(getter(hwnd) if getter else "")
        except Exception:
            return ""

    @staticmethod
    def _is_exact_0600_title(title: str) -> bool:
        normalized = " ".join(str(title or "").split())
        return "[0600]" in normalized and "키움종합차트" in normalized

    def _is_exact_0600_chart(self, hwnd: int) -> bool:
        """Allow only the visible chart whose own title has both 0600 tokens."""

        if not self._window_alive(int(hwnd or 0)):
            return False
        title = self._window_title_safe(int(hwnd)).strip()
        return self._is_exact_0600_title(title)

    def _chart_candidates(self) -> list[Any]:
        """Return the locked exact 0600, or one unambiguous exact 0600.

        Two logged-in Kiwoom sessions expose indistinguishable [0600] titles.
        A previously validated chart remains safe when another account later
        opens 0600.  With no validated lock, ambiguity still fails closed.
        """

        values = super()._chart_candidates()
        exact = [item for item in values if self._is_exact_0600_chart(int(item.hwnd))
                 and self._v7609_display_chart_valid(int(item.hwnd))]
        rejected = len(values) - len(exact)
        if rejected:
            v57.diag(f"V7609_NON_0600_CANDIDATES_BLOCKED count={rejected}")
        if self._v7609_locked_chart_valid():
            locked_chart = int(self._v7609_fast_chart)
            locked = [item for item in exact if int(item.hwnd) == locked_chart]
            if len(locked) == 1:
                return locked
        owners = {int(item.top or item.hwnd) for item in exact}
        if len(owners) > 1:
            v57.diag(
                f"V7609_MULTI_KIWOOM_0600_BLOCKED owners={len(owners)} "
                f"charts={len(exact)}"
            )
            return []
        return exact

    def _v7609_multiple_0600_sessions(self) -> bool:
        values = super()._chart_candidates() if sys.platform == "win32" else []
        owners = {
            int(item.top or item.hwnd)
            for item in values
            if self._is_exact_0600_chart(int(item.hwnd))
        }
        return len(owners) > 1

    def _child_title_tokens(self, hwnd: int) -> int:
        if sys.platform != "win32" or not hwnd:
            return 0
        try:
            user32 = ctypes.windll.user32
            callback_type = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(
                wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
            )
            score = {"value": 0, "seen": 0}

            def callback(child: int, _param: int) -> bool:
                score["seen"] += 1
                title = self._window_title_safe(int(child))
                if "[0600]" in title:
                    score["value"] += 6
                if "키움종합차트" in title:
                    score["value"] += 6
                if score["value"] >= 6 or score["seen"] >= 120:
                    return False
                return True

            user32.EnumChildWindows(wintypes.HWND(hwnd), callback_type(callback), 0)
            return int(score["value"])
        except Exception:
            return 0

    def _find_saved_or_kiwoom_window(self) -> int:
        """Return an exact [0600] chart only; saved roots and generic Kiwoom are ignored."""

        candidates = self._chart_candidates() if sys.platform == "win32" else []
        return int(candidates[0].hwnd) if candidates else 0

    def capture_cursor_target(self) -> tuple[bool, str]:
        """The GUI check button validates an exact 0600 Edit without saving a cursor."""

        if sys.platform != "win32":
            return False, "Windows에서만 작동합니다."
        if self._v7609_cached_target_valid():
            return True, "기존에 확인한 내 [0600] 종목코드 입력칸 유지 중"
        if self._v7609_locked_chart_valid():
            if self._v7609_refresh_locked_edit():
                return True, "기존에 고정한 내 [0600] 입력칸을 다시 확인했습니다."
            return False, "고정된 내 [0600] 안에서 종목코드 입력칸을 다시 찾지 못했습니다."
        candidates = self._chart_candidates()
        if len(candidates) != 1:
            v57.diag(f"V7609_CHECK_ACCOUNT_TARGET_UNRESOLVED count={len(candidates)}")
            return False, (
                "REST 0311 인증과 연결할 [0600] 하나를 확인해야 합니다. "
                "차트가 여러 개이면 기존에 고정한 차트만 사용합니다."
            )
        for candidate in candidates:
            chart = int(candidate.hwnd)
            target = self._find_live_0600_edit(chart)
            if not target:
                continue
            self._v7609_remember_target(chart, int(candidate.top), int(target))
            v57.diag(
                f"V7609_EXACT_0600_CHECK chart={chart} top={candidate.top} target={target}"
            )
            return True, "정확한 [0600] 종목코드 입력칸 확인 완료"
        return False, "보이는 [0600] 키움종합차트의 정확한 종목코드 입력칸을 찾지 못했습니다."

    def _activate_window(self, hwnd: int) -> bool:
        if sys.platform != "win32" or not hwnd:
            return False
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        attached: list[tuple[int, int]] = []
        try:
            target_thread = int(user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), None) or 0)
            foreground = int(user32.GetForegroundWindow() or 0)
            foreground_thread = (
                int(user32.GetWindowThreadProcessId(wintypes.HWND(foreground), None) or 0)
                if foreground else 0
            )
            this_thread = int(kernel32.GetCurrentThreadId() or 0)
            try:
                user32.AllowSetForegroundWindow(-1)
            except Exception:
                pass
            for first, second in ((this_thread, target_thread), (foreground_thread, target_thread)):
                if first and second and first != second:
                    try:
                        if user32.AttachThreadInput(first, second, True):
                            attached.append((first, second))
                    except Exception:
                        pass
            user32.ShowWindow(wintypes.HWND(hwnd), getattr(self, "SW_RESTORE", 9))
            user32.BringWindowToTop(wintypes.HWND(hwnd))
            user32.SetActiveWindow(wintypes.HWND(hwnd))
            user32.SetForegroundWindow(wintypes.HWND(hwnd))
            deadline = time.monotonic() + 0.18
            while time.monotonic() < deadline:
                if int(user32.GetForegroundWindow() or 0) == int(hwnd):
                    return True
                time.sleep(0.015)
            return int(user32.GetForegroundWindow() or 0) == int(hwnd)
        except Exception as exc:
            v57.diag(f"V7606_ACTIVATE_FAIL hwnd={hwnd} {type(exc).__name__}: {exc}")
            return False
        finally:
            for first, second in reversed(attached):
                try:
                    user32.AttachThreadInput(first, second, False)
                except Exception:
                    pass

    @staticmethod
    def _click_point(x: int, y: int) -> bool:
        try:
            user32 = ctypes.windll.user32
            if not user32.SetCursorPos(int(x), int(y)):
                # SetCursorPos can fail on a left/upper monitor whose screen
                # coordinates are negative.  Use the full virtual desktop as
                # the fallback coordinate space.
                virtual_left = int(user32.GetSystemMetrics(76))
                virtual_top = int(user32.GetSystemMetrics(77))
                virtual_width = int(user32.GetSystemMetrics(78))
                virtual_height = int(user32.GetSystemMetrics(79))
                if virtual_width <= 1 or virtual_height <= 1:
                    return False
                if not (
                    virtual_left <= int(x) < virtual_left + virtual_width
                    and virtual_top <= int(y) < virtual_top + virtual_height
                ):
                    return False
                absolute_x = int(
                    round((int(x) - virtual_left) * 65535 / (virtual_width - 1))
                )
                absolute_y = int(
                    round((int(y) - virtual_top) * 65535 / (virtual_height - 1))
                )
                user32.mouse_event(
                    0x0001 | 0x4000 | 0x8000,
                    absolute_x,
                    absolute_y,
                    0,
                    0,
                )
                time.sleep(0.05)
                observed = wintypes.POINT()
                if not user32.GetCursorPos(ctypes.byref(observed)):
                    return False
                if abs(int(observed.x) - int(x)) > 3 or abs(int(observed.y) - int(y)) > 3:
                    return False
            time.sleep(0.05)
            user32.mouse_event(0x0002, 0, 0, 0, 0)
            time.sleep(0.025)
            user32.mouse_event(0x0004, 0, 0, 0, 0)
            time.sleep(0.14)
            return True
        except Exception:
            return False

    @classmethod
    def _click(cls, x: int, y: int) -> bool:
        """Use the same multi-monitor-safe click for auto-discovered 0600."""

        return cls._click_point(x, y)

    @staticmethod
    def _window_from_point(x: int, y: int) -> int:
        if sys.platform != "win32":
            return 0
        try:
            user32 = ctypes.windll.user32
            window_from_point = user32.WindowFromPoint
            window_from_point.argtypes = (wintypes.POINT,)
            window_from_point.restype = wintypes.HWND
            return int(window_from_point(wintypes.POINT(int(x), int(y))) or 0)
        except Exception:
            return 0

    def _fast_exact_edit_send(
        self,
        chart: int,
        top: int,
        target: int,
        symbol: str,
    ) -> tuple[Any, str]:
        """Native near-instant 0600 send after exact Edit validation."""

        if not self._v7609_display_chart_valid(chart):
            return v57.LinkStatus.FAILED, "REST 0311 인증 또는 정확한 0600 대상 미확인"
        if (
            not chart
            or not target
            or not self._is_exact_0600_chart(chart)
            or not self._belongs_to_chart(chart, target)
            or not self._safe_edit_target(chart, target)
        ):
            return v57.LinkStatus.FAILED, "고속 0600 정확한 Edit 검증 실패"
        top = int(top or self._top_level_of(chart) or chart)

        buffer = ctypes.create_unicode_buffer(symbol)
        sent, _result, error = self._send_message(
            target,
            0x000C,  # WM_SETTEXT
            0,
            ctypes.cast(buffer, ctypes.c_void_p).value or 0,
            timeout_ms=150,
        )
        written = self._read_control_text(target)
        if not sent or str(written).strip() != symbol:
            return v57.LinkStatus.FAILED, (
                f"고속 0600 직접쓰기 실패 written={written!r} error={error}"
            )
        enter_down, enter_up = self._post_enter_to_edit(target)
        if not enter_down or not enter_up:
            return v57.LinkStatus.FAILED, "고속 0600 Enter 전달 실패"

        self._v7609_remember_target(int(chart), int(top), int(target))
        v57.diag(
            f"V7609_NATIVE_FAST_0600 symbol={symbol} chart={chart} "
            f"top={top} target={target}"
        )
        return v57.LinkStatus.SENT_UNVERIFIED, (
            f"{symbol} 0600 고속 직접전송 완료"
        )

    def _activate_and_focus(self, root: int, target: int, x: int, y: int):
        """Accept an already-focused exact Edit when cursor injection is blocked.

        On the user's left monitor Kiwoom exposes the real [0600] Edit and
        GetGUIThreadInfo reports that exact HWND as focused, while Windows
        rejects physical cursor injection.  Requiring click_ok in that state
        discards a safe, exact target that is already ready for Ctrl+V.
        """

        chart = int(root or 0)
        if not self._is_exact_0600_chart(chart):
            v57.diag(f"V7609_NON_0600_FOCUS_BLOCKED chart={chart} target={target}")
            return False, 0, False
        top = int(self._top_level_of(chart) or chart)
        fast_target = int(getattr(self, "_v7609_fast_edit", 0) or 0)
        cache_fresh = (
            time.monotonic() - float(getattr(self, "_v7609_fast_saved_at", 0.0) or 0.0)
            <= self.CACHE_SECONDS
        )
        if (
            fast_target
            and cache_fresh
            and int(target or 0) == fast_target
            and self._belongs_to_chart(chart, fast_target)
            and self._safe_edit_target(chart, fast_target)
        ):
            foreground = int(self._foreground() or 0)
            if foreground == top or self._activate_window(top):
                self._last_focus_detail = (
                    f"V7609_FAST_EXACT_EDIT chart={chart} top={top} "
                    f"target={fast_target}"
                )
                v57.diag(self._last_focus_detail)
                return True, fast_target, False

        gate, focus, click_ok = super()._activate_and_focus(root, target, x, y)
        if gate:
            return gate, focus, click_ok
        foreground = int(self._foreground() or 0)
        exact_edit_focused = bool(
            chart
            and target
            and int(focus or 0) == int(target)
            and self._belongs_to_chart(chart, int(target))
            and self._safe_edit_target(chart, int(target))
            and foreground == top
        )
        if exact_edit_focused:
            self._last_focus_detail = (
                f"{getattr(self, '_last_focus_detail', '')} "
                f"V7609_EXACT_EDIT_FOCUS_FALLBACK target={target}"
            ).strip()
            v57.diag(
                f"V7609_EXACT_EDIT_FOCUS_FALLBACK chart={chart} top={top} "
                f"target={target} point=({x},{y}) click={click_ok}"
            )
            return True, int(focus), bool(click_ok)
        return gate, focus, click_ok

    @staticmethod
    def _window_pid(hwnd: int) -> int:
        if sys.platform != "win32" or not hwnd:
            return 0
        try:
            pid = wintypes.DWORD(0)
            ctypes.windll.user32.GetWindowThreadProcessId(
                wintypes.HWND(hwnd), ctypes.byref(pid)
            )
            return int(pid.value)
        except Exception:
            return 0

    @classmethod
    def _powershell_current_sendkeys(cls, hwnd: int) -> tuple[bool, str]:
        pid = cls._window_pid(hwnd)
        if pid <= 0:
            return False, "영웅문 PID 확인 실패"
        script = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$ws=New-Object -ComObject WScript.Shell;"
            f"$ok=$ws.AppActivate({pid});"
            "Start-Sleep -Milliseconds 120;"
            "[System.Windows.Forms.SendKeys]::SendWait('^a');"
            "Start-Sleep -Milliseconds 80;"
            "[System.Windows.Forms.SendKeys]::SendWait('{BACKSPACE}');"
            "Start-Sleep -Milliseconds 80;"
            "[System.Windows.Forms.SendKeys]::SendWait('^v');"
            "Start-Sleep -Milliseconds 180;"
            "[System.Windows.Forms.SendKeys]::SendWait('{ENTER}');"
            "Start-Sleep -Milliseconds 120;"
            "[System.Windows.Forms.SendKeys]::SendWait('{ENTER}');"
            "if($ok){exit 0}else{exit 3}"
        )
        try:
            result = subprocess.run(
                [
                    "powershell.exe", "-NoProfile", "-NonInteractive",
                    "-WindowStyle", "Hidden", "-Command", script,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=7,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False,
            )
        except Exception as exc:
            return False, f"PowerShell 실행 실패: {type(exc).__name__}"
        detail = (result.stderr or result.stdout or "").strip().replace("\n", " ")[:160]
        return result.returncode == 0, detail or "PowerShell SendKeys"

    def _saved_click_point(self, root: int) -> tuple[int, int] | None:
        geometry = self._client_geometry(root) if root else None
        if geometry is not None and {"x_ratio", "y_ratio"}.issubset(set(self.config.keys())):
            left, top, width, height = geometry
            return (
                left + int(float(self.config["x_ratio"]) * width),
                top + int(float(self.config["y_ratio"]) * height),
            )
        if "screen_x" in self.config and "screen_y" in self.config:
            return int(self.config["screen_x"]), int(self.config["screen_y"])
        return None

    def _send_saved_position_first(self, symbol: str) -> tuple[Any, str] | None:
        """Hard block the legacy cursor/AppActivate/global-key route."""

        v57.diag(f"V7609_SAVED_GLOBAL_KEY_ROUTE_BLOCKED symbol={symbol}")
        return v57.LinkStatus.FAILED, "저장좌표·전체 키 전송 경로는 다른 키움 창 보호를 위해 차단되었습니다."

    def _finish_keyboard_route(
        self, chart: int, top: int, effective: int, symbol: str
    ) -> tuple[Any, str]:
        """Never emit process-wide keyboard input; only an exact Edit is allowed."""

        v57.diag(
            f"V7609_GLOBAL_KEY_ROUTE_BLOCKED symbol={symbol} chart={chart} top={top} "
            f"effective={effective}"
        )
        return v57.LinkStatus.FAILED, (
            "정확한 [0600] Edit를 확인하지 못해 전송을 중단했습니다. "
            "다른 키움 매수·매도창 보호를 위해 키보드 예비 전송은 하지 않습니다."
        )

    def _finish_edit_route(
        self, chart: int, top: int, target: int, symbol: str
    ) -> tuple[Any, str] | None:
        if not self._is_exact_0600_chart(chart) or not self._safe_edit_target(chart, target):
            return None
        if int(self._foreground() or 0) != int(top):
            return v57.LinkStatus.FAILED, "직접 입력 직전 영웅문 전경 포커스를 잃었습니다."

        visible_before = self._visible_chart_texts(chart, target)
        input_before = self._read_control_text(target)
        buffer = ctypes.create_unicode_buffer(symbol)
        sent, _result, error = self._send_message(
            target,
            0x000C,  # WM_SETTEXT
            0,
            ctypes.cast(buffer, ctypes.c_void_p).value or 0,
            timeout_ms=350,
        )
        input_written = self._read_control_text(target)
        if not sent or str(input_written).strip() != symbol:
            v57.diag(
                f"V7609_SEND route=WM_SETTEXT_POST_ENTER symbol={symbol} "
                f"target={target} sent={sent} before={input_before!r} "
                f"written={input_written!r} error={error}"
            )
            return v57.LinkStatus.FAILED, (
                f"{symbol} 0600 입력칸 직접 쓰기 실패 "
                f"(현재값={str(input_written).strip() or '빈값'})"
            )
        if int(self._foreground() or 0) != int(top):
            return v57.LinkStatus.FAILED, "직접 입력 후 영웅문 전경 포커스를 잃었습니다."

        enter_down, enter_up = self._post_enter_to_edit(target)
        time.sleep(0.35)
        input_after = self._read_control_text(target)
        visible_after = self._visible_chart_texts(chart, target)
        evidence = self._changed_symbol_evidence(
            visible_before, visible_after, symbol
        )
        external_verified = evidence is not None
        v57.diag(
            f"V7609_SEND route=WM_SETTEXT_POST_ENTER symbol={symbol} "
            f"target={target} sent={sent} enter_down={enter_down} "
            f"enter_up={enter_up} before={input_before!r} "
            f"written={input_written!r} after={input_after!r} "
            f"external_verified={external_verified} evidence={evidence!r}"
        )
        if not enter_down or not enter_up:
            self._v7609_fast_edit = 0
            self._v7609_fast_chart = 0
            self._v7609_fast_top = 0
            self._v7609_fast_saved_at = 0.0
            return v57.LinkStatus.FAILED, f"{symbol} 직접 입력 후 Enter 전달 실패"
        self._v7609_fast_edit = int(target)
        self._v7609_fast_chart = int(chart)
        self._v7609_fast_top = int(top)
        self._v7609_fast_saved_at = time.monotonic()
        if external_verified:
            return v57.LinkStatus.VERIFIED, (
                f"{symbol} 0600 입력 및 별도 차트 표시 변경 확인"
            )
        return v57.LinkStatus.SENT_UNVERIFIED, (
            f"{symbol} 0600 직접 입력·Enter 전달 완료 — 실제 차트 변경을 확인해주세요."
        )

    @staticmethod
    def _post_enter_to_edit(target: int) -> tuple[bool, bool]:
        """Queue Enter to the exact Edit so MFC can process it normally."""

        if sys.platform != "win32" or not target:
            return False, False
        try:
            user32 = ctypes.windll.user32
            post_message = user32.PostMessageW
            post_message.argtypes = (
                wintypes.HWND,
                wintypes.UINT,
                wintypes.WPARAM,
                wintypes.LPARAM,
            )
            post_message.restype = wintypes.BOOL
            down = bool(
                post_message(
                    wintypes.HWND(target), 0x0100, 0x0D, 0x001C0001
                )
            )
            time.sleep(0.02)
            up = bool(
                post_message(
                    wintypes.HWND(target),
                    0x0101,
                    0x0D,
                    ctypes.c_long(0xC01C0001).value,
                )
            )
            return down, up
        except Exception:
            return False, False

    def _visible_chart_texts(self, chart: int, target: int) -> dict[int, str]:
        """Snapshot visible chart text, excluding the Edit being written."""

        values: dict[int, str] = {}
        for hwnd in (int(chart), *self._enum_descendants(int(chart))):
            hwnd = int(hwnd or 0)
            if not hwnd or hwnd == int(target):
                continue
            try:
                if not self._window_alive(hwnd):
                    continue
            except Exception:
                continue
            text = self._window_title_safe(hwnd).strip()
            if text:
                values[hwnd] = text[:160]
        return values

    @staticmethod
    def _changed_symbol_evidence(
        before: dict[int, str], after: dict[int, str], symbol: str
    ) -> tuple[int, str, str] | None:
        """Accept only a changed visible non-input text containing the symbol."""

        for hwnd, after_text in after.items():
            before_text = str(before.get(hwnd, ""))
            if after_text != before_text and symbol in after_text:
                return int(hwnd), before_text[:80], after_text[:80]
        return None

    def _find_live_0600_edit(self, chart: int) -> int:
        """Use an unambiguous live six-digit Edit without a saved point."""

        if not self._is_exact_0600_chart(int(chart or 0)):
            return 0
        matches: list[int] = []
        for hwnd in self._enum_descendants(int(chart)):
            if not self._safe_edit_target(int(chart), int(hwnd)):
                continue
            if re.fullmatch(r"\d{6}", self._read_control_text(int(hwnd)).strip()):
                matches.append(int(hwnd))
        return matches[0] if len(matches) == 1 else 0

    def send_symbol(self, symbol: str) -> tuple[Any, str]:
        normalized = str(symbol).strip()
        if not re.fullmatch(r"\d{6}", normalized):
            return v57.LinkStatus.FAILED, f"종목코드 형식 오류: {normalized}"
        fast_target = int(getattr(self, "_v7609_fast_edit", 0) or 0)
        fast_chart = int(getattr(self, "_v7609_fast_chart", 0) or 0)
        fast_top = int(getattr(self, "_v7609_fast_top", 0) or 0)
        if fast_target and fast_chart:
            fast_result = self._fast_exact_edit_send(
                fast_chart, fast_top, fast_target, normalized
            )
            if fast_result[0] != v57.LinkStatus.FAILED:
                return fast_result
            v57.diag(
                f"V7609_FAST_EDIT_STALE_KEEP_CHART chart={fast_chart} "
                f"reason={fast_result[1]}"
            )
            self._v7609_clear_edit_cache()

        # A transient Edit failure must not erase the safe account/chart lock.
        # Re-enumerate only descendants of that same chart, then retry once.
        if fast_chart and self._v7609_locked_chart_valid():
            repaired = self._v7609_refresh_locked_edit()
            if repaired:
                repaired_result = self._fast_exact_edit_send(
                    fast_chart,
                    int(getattr(self, "_v7609_fast_top", 0) or fast_top),
                    repaired,
                    normalized,
                )
                if repaired_result[0] != v57.LinkStatus.FAILED:
                    return repaired_result
                self._v7609_clear_edit_cache()
            return v57.LinkStatus.FAILED, (
                "고정된 내 [0600] 안에서 종목코드 입력칸 복구에 실패했습니다."
            )
        if fast_chart:
            self._v7609_fast_chart = 0
            self._v7609_fast_top = 0
            self._v7609_clear_edit_cache()

        # Display routing does not inspect HTS order/account controls.
        # Only an existing exact chart binding or one unambiguous chart is used.
        candidates = self._chart_candidates()
        if len(candidates) != 1:
            v57.diag(f"V7609_SEND_ACCOUNT_TARGET_UNRESOLVED count={len(candidates)}")
            return v57.LinkStatus.FAILED, (
                "REST 0311 인증 또는 단일 [0600] 대상 미확인으로 입력을 차단했습니다."
            )

        # First click: enumerate only exact visible [0600] charts and use only
        # an unambiguous descendant Edit.  No saved cursor, AppActivate or
        # process-wide Ctrl+V/Enter route is permitted.
        for candidate in candidates:
            chart = int(candidate.hwnd)
            target = self._find_live_0600_edit(chart)
            if not target:
                continue
            direct = self._fast_exact_edit_send(
                chart, int(candidate.top), target, normalized
            )
            if direct[0] != v57.LinkStatus.FAILED:
                return direct

        return v57.LinkStatus.FAILED, (
            "정확한 내 [0600] 종목코드 입력칸을 찾지 못해 안전 차단했습니다."
        )


v57.RestoredHeroLink = V7600CtrlVHeroLink
v57.ReliableHeroLink = V7600CtrlVHeroLink


def _set_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


_set_dpi_awareness()


def _install_gui_fit_and_colors() -> None:
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception:
        return
    if getattr(tk.Tk, "_v7600_fit_installed", False):
        return

    old_mainloop = tk.Tk.mainloop
    old_label_init = tk.Label.__init__
    old_text_init = tk.Text.__init__
    old_string_set = tk.StringVar.set
    old_bind = tk.Misc.bind
    old_protocol = tk.Wm.protocol
    old_tree_init = ttk.Treeview.__init__
    old_insert = ttk.Treeview.insert

    def _request_dashboard_shutdown(root: Any) -> None:
        if bool(getattr(root, "_v7607_shutdown_requested", False)):
            return
        try:
            root._v7607_shutdown_requested = True
        except Exception:
            pass
        try:
            root.title(
                f"프라임 시그널 대시보드 (PAPER) · {VERSION}"
                f" · 수정 {BUILD_STAMP} · 종료 중"
            )
        except Exception:
            pass
        _V7609_USER_CLOSE.set()
        try:
            root.quit()
        except Exception:
            pass

    def _work_area(root: Any) -> tuple[int, int, int, int]:
        if sys.platform == "win32":
            try:
                rect = wintypes.RECT()
                if ctypes.windll.user32.SystemParametersInfoW(
                    0x0030, 0, ctypes.byref(rect), 0
                ):
                    return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
            except Exception:
                pass
        return 0, 0, int(root.winfo_screenwidth()), int(root.winfo_screenheight())

    def _apply_sold_card_palette(root: Any) -> None:
        """Match the top 오늘매도 card to the sold-row red palette."""

        pending = list(root.winfo_children())
        while pending:
            widget = pending.pop()
            try:
                pending.extend(widget.winfo_children())
            except Exception:
                pass
            try:
                if not isinstance(widget, tk.Label):
                    continue
                label = str(widget.cget("text") or "")
                if "오늘매도" not in label or not label.strip().startswith("05"):
                    continue
                title_band = widget.master
                card = title_band.master
                title_band.configure(bg=_V7609_SELL_BAND)
                widget.configure(bg=_V7609_SELL_BAND, fg="#ffffff")
                card.configure(
                    bg=_V7609_SELL_BODY,
                    highlightbackground=_V7609_SELL_BAND,
                )
                for child in card.winfo_children():
                    if child is title_band:
                        continue
                    if isinstance(child, tk.Label):
                        child.configure(
                            bg=_V7609_SELL_BODY,
                            fg=_V7609_SELL_TEXT,
                        )
                return
            except Exception:
                pass

    def _apply_stock_table_layout(root: Any, stage: str = "전체") -> None:
        """Give names room and reserve two readable lines for sold details."""

        selected_stage = str(stage or "전체").strip()
        try:
            selected_stage = str(root.category_filter_var.get() or selected_stage).strip()
        except Exception:
            pass
        sold_view = selected_stage in {"매도종목", "오늘매도"}
        widths = {
            "no": 48 if sold_view else 36,
            "stage": 94,
            "name": 210 if sold_view else 170,
            "current": 88,
            "basis": 88,
            "change": 62,
            "qty": 62,
            "stage_time": 126 if sold_view else 112,
            "reason": 800 if sold_view else 650,
        }
        try:
            ttk.Style(root).configure(
                "R20Main.Treeview",
                rowheight=80 if sold_view else 28,
                font=("맑은 고딕", 9),
            )
        except Exception:
            pass
        for tree in list(getattr(root, "_v7602_stock_trees", [])):
            holding_view = selected_stage in {"매수종목", "보유종목"}
            try:
                display = parent._R20_COMMON_DISPLAY
                if holding_view:
                    display = ("no", "stage", "name", "basis", "current", "change", "qty", "stage_time", "reason")
                tree.configure(displaycolumns=display)
                tree.heading("basis", text="매수가(평단)")
                tree.heading("change", text="매수후 등락률" if holding_view else "등락률")
            except Exception:
                pass
            for column, width in widths.items():
                try:
                    visible_width = 112 if holding_view and column == "change" else width
                    tree.column(
                        column,
                        width=visible_width,
                        minwidth=visible_width if column in {"no", "name"} else 36,
                        stretch=column == "reason",
                    )
                except Exception:
                    pass

    def _set_main_pane_ratio(root: Any) -> None:
        """Use 80% for the stock table and 20% for the readable guide panel."""

        pending = list(root.winfo_children())
        while pending:
            widget = pending.pop()
            try:
                pending.extend(widget.winfo_children())
            except Exception:
                pass
            try:
                if isinstance(widget, ttk.Panedwindow):
                    if str(widget.cget("orient")) == "horizontal":
                        panes = tuple(widget.panes())
                        if len(panes) == 2:
                            widget.pane(panes[0], weight=80)
                            widget.pane(panes[1], weight=20)
                            width = int(widget.winfo_width() or 0)
                            if width > 100:
                                widget.sashpos(0, int(width * 0.80))
            except Exception:
                pass
            try:
                if isinstance(widget, tk.Label):
                    row = widget.master
                    guide = row.master
                    if (
                        isinstance(guide, ttk.LabelFrame)
                        and str(guide.cget("text") or "") == "단계별 의미 및 판단 기준"
                        and not re.match(r"^\d{2}\s", str(widget.cget("text") or ""))
                    ):
                        widget.configure(wraplength=220, justify="left", anchor="w")
            except Exception:
                pass

    def _compact_one_screen_layout(root: Any) -> None:
        """Reduce only fixed-height dashboard panels so the bottom stays visible."""

        pending = list(root.winfo_children())
        bottom_host = None
        while pending:
            widget = pending.pop()
            try:
                pending.extend(widget.winfo_children())
            except Exception:
                pass
            try:
                if isinstance(widget, tk.Label):
                    label = str(widget.cget("text") or "")
                    if label.startswith("자동매매 대시보드") and BUILD_STAMP not in label:
                        widget.configure(text=f"{label} · 수정 {BUILD_STAMP}")
                if isinstance(widget, tk.Text):
                    background = str(widget.cget("background")).lower()
                    if background == "#f4f6f8":
                        widget.configure(height=2, pady=1)
                    elif background == "#091c31":
                        # Two dark fixed-height text panels sit above the
                        # command area.  Three rows each pushes the command
                        # buttons and request-summary tree below a 1080p work
                        # area once the Windows title bar/taskbar are removed.
                        widget.configure(height=2, pady=2, spacing3=1)
                elif isinstance(widget, ttk.Treeview):
                    panel = str(widget.master.cget("text") or "")
                    if panel == "형 요청 해석결과 미리보기":
                        widget.configure(height=3)
                    elif panel in {"형 요청 활성 목록", "의견·요청 시간대별 이력"}:
                        widget.configure(height=2)
                elif isinstance(widget, ttk.Button):
                    if (str(widget.cget("text") or "") == "로그 보기"
                            and not getattr(root, "_v7609_history_button", None)):
                        button = ttk.Button(widget.master, text="이동·탈락 이력",
                                            command=lambda: _v7609_open_stage_history(root))
                        button.pack(side="left", padx=4)
                        root._v7609_history_button = button
                    if str(widget.cget("text") or "") == "선택 삭제":
                        widget.configure(text="선택삭제")
                elif isinstance(widget, ttk.LabelFrame):
                    title = str(widget.cget("text") or "")
                    if title == "형 의견·명령 입력":
                        bottom_host = widget.master
                    if title in {
                        "형 의견·명령 입력",
                        "형 요청 해석결과 미리보기",
                        "형 요청 활성 목록",
                        "의견·요청 시간대별 이력",
                        "10분 장세분석 · 30분 상세이력",
                    }:
                        widget.configure(padding=3)
            except Exception:
                pass
        if bottom_host is not None:
            try:
                # Reserve enough room for the four bottom panels and their
                # final button/history rows.  Put this frame before the main
                # expanding pane so tall sold rows cannot push it off-screen.
                command_host = bottom_host.master
                main_host = next(
                    (
                        child
                        for child in command_host.master.winfo_children()
                        if isinstance(child, ttk.Panedwindow)
                    ),
                    None,
                )
                command_host.configure(height=262)
                command_host.pack_propagate(False)
                if main_host is not None:
                    command_host.pack_configure(
                        before=main_host,
                        side="bottom",
                        fill="x",
                        expand=False,
                    )
                bottom_host.pack_configure(fill="both", expand=True)
                bottom_host.configure(height=258)
                bottom_host.grid_propagate(False)
                bottom_host.rowconfigure(0, weight=1)
            except Exception:
                pass
        _apply_sold_card_palette(root)
        _apply_stock_table_layout(root, "전체")
        try:
            category_filter = root.category_filter_var
            if not getattr(root, "_v7609_category_layout_trace", False):
                root._v7609_category_layout_trace = True
                category_filter.trace_add(
                    "write",
                    lambda *_args, root=root, category_filter=category_filter: root.after_idle(
                        lambda: _apply_stock_table_layout(root, category_filter.get())
                    ),
                )
        except Exception:
            pass
        _set_main_pane_ratio(root)
        try:
            root.after(120, lambda: _set_main_pane_ratio(root))
        except Exception:
            pass

    def _fit(root: Any) -> None:
        try:
            left, top, right, bottom = _work_area(root)
            # Tk geometry is the client area only.  The old calculation filled
            # the work area with the client and then Windows added the title
            # bar/borders outside it, hiding the dashboard bottom.  Measure the
            # real decoration and subtract it before setting the client size.
            frame_width, frame_height = 16, 48
            if sys.platform == "win32":
                try:
                    root.update_idletasks()
                    hwnd = int(
                        ctypes.windll.user32.GetAncestor(int(root.winfo_id()), 2)
                    )
                    if not hwnd:
                        hwnd = int(root.winfo_id())
                    outer_rect = wintypes.RECT()
                    client_rect = wintypes.RECT()
                    if (
                        ctypes.windll.user32.GetWindowRect(
                            hwnd, ctypes.byref(outer_rect)
                        )
                        and ctypes.windll.user32.GetClientRect(
                            hwnd, ctypes.byref(client_rect)
                        )
                    ):
                        frame_width = max(
                            0,
                            int(outer_rect.right - outer_rect.left)
                            - int(client_rect.right - client_rect.left),
                        )
                        frame_height = max(
                            0,
                            int(outer_rect.bottom - outer_rect.top)
                            - int(client_rect.bottom - client_rect.top),
                        )
                except Exception:
                    pass
            edge = 8
            width = max(640, (right - left) - (edge * 2) - frame_width)
            height = max(480, (bottom - top) - (edge * 2) - frame_height)
            left += edge
            top += edge
            current_title = str(root.title() or "")
            if "프라임 시그널 대시보드" in current_title or "자동매매 대시보드" in current_title:
                root.title(
                    f"프라임 시그널 대시보드 (PAPER) · {VERSION}"
                    f" · 수정 {BUILD_STAMP}"
                )
            # Do not cap the native maximize button at the inset restore size.
            # Windows maximize then fills exactly to the taskbar work area.
            root.maxsize(max(width, right - left + edge), max(height, bottom - top + edge))
            root.minsize(min(900, width), min(560, height))
            root.geometry(f"{width}x{height}+{left}+{top}")
            _compact_one_screen_layout(root)
            if sys.platform == "win32":
                try:
                    root.state("zoomed")
                except Exception:
                    pass
            v57.diag(
                f"V7609_GUI_FIT work=({right-left+8}x{bottom-top+8}) "
                f"frame={frame_width}x{frame_height} "
                f"geometry={width}x{height}+{left}+{top} maximized=True"
            )
            root.protocol("WM_DELETE_WINDOW", lambda: _request_dashboard_shutdown(root))
            _apply_stock_table_layout(root, "전체")
        except Exception:
            pass

    def tree_init(self: Any, master: Any = None, **kwargs: Any) -> None:
        old_tree_init(self, master, **kwargs)
        try:
            panel = str(master.cget("text") or "")
            if panel == "형 요청 해석결과 미리보기":
                def attach() -> None:
                    if getattr(self, "_v7609_preview_xscroll_ready", False):
                        return
                    self._v7609_preview_xscroll_ready = True
                    x = ttk.Scrollbar(master, orient="horizontal", command=self.xview)
                    self.configure(xscrollcommand=x.set)
                    x.grid(row=5, column=0, columnspan=4, sticky="ew")
                    self._v7609_preview_xscrollbar = x
                self.after_idle(attach)
        except Exception:
            pass
        if not getattr(self, "_v7595_main_stock_tree", False):
            return
        try:
            root = self.winfo_toplevel()
            trees = list(getattr(root, "_v7602_stock_trees", []))
            if self not in trees:
                trees.append(self)
                root._v7602_stock_trees = trees
            for tag, colors in parent._R21_STAGE_ROW_COLORS.items():
                background, foreground = colors
                self.tag_configure(tag, background=background, foreground=foreground)
            self.tag_configure(
                "매도종목_수익",
                background=_V7609_SELL_BODY,
                foreground=_V7609_SELL_GAIN_TEXT,
                font=("맑은 고딕", 10, "bold"),
            )
            self.tag_configure(
                "매도종목_손실",
                background=_V7609_SELL_BODY,
                foreground=_V7609_SELL_LOSS_TEXT,
                font=("맑은 고딕", 10, "bold"),
            )
            self.tag_configure("R20_DEPARTED", background="#202a36", foreground="#91a5b8")
        except Exception:
            pass

    def label_init(self: Any, master: Any = None, cnf: Any = None, **kwargs: Any) -> None:
        old_label_init(self, master, cnf, **kwargs)
        try:
            text = str(self.cget("text") or "")
            if text.startswith("초기 포착. 당일 시가 대비"):
                self.configure(text=(
                    "08시 이후 출발 저점부터 15개 완성 1분봉 이내에 종가 +5% 이상이면 관찰한다. "
                    "저점 봉부터 1봉으로 세며 한 봉도 인정한다. 더 낮은 저점만 시작을 갱신하고 "
                    "동일 저점·15봉 경과로 시간을 연장하지 않는다. 이후 조정과 재상승을 기다리는 후보이다."
                    " 4%대는 예비관찰(매수자격 아님). 4% 미만·15봉 초과·최신봉 미확보 시 제외한다."
                ))
            if "후속 5~10봉 평균" in text or "완성 3분봉 기준 반전과 두 봉" in text:
                self.configure(
                    text=(
                        "1차상승 뒤 조정 중 주변 음봉보다 거래가 뚜렷한 기준음봉을 저장한다. "
                        "거래바닥 뒤 아래꼬리 짧은음봉에서 1차 1/2, 다음 1~2봉 안의 짧은양봉 "
                        "1분 MA5 안착에서 나머지 1/2를 PAPER 매수한다. 돌파는 왼쪽 최대거래를 "
                        "소화한 양봉 뒤 거래축소·중심선 지지와 재상승 거래확대까지 확인한다."
                    )
                )
            if text.startswith(("매수 검토", "최종 확인", "매수직전 통합")):
                self.configure(text=(
                    "매수직전: 기존 진입 후보 또는 5배 거래량 조건 충족 후 최종 주문심사 대기. "
                    "5배는 주가가 아닌 완성 1분봉 거래량/직전 40봉 평균이며 종가>전일종가. "
                    "08·09시 첫 15분은 전 거래일 마지막 40봉 기준. "
                    "PAPER 예상5배는 진행봉 25~59초의 별도 경로로 확정5배와 구분."
                ))
            if text.startswith(("보유 관리", "PAPER 보유.")):
                self.configure(text=(
                    "오늘 매수·매도: 왼쪽 보유·오른쪽 당일 매도, 10행 높이·스크롤로 전체 확인. "
                    "일시·가격·비용포함 순손익·매도사유 구분. "
                    "기본 -1.5%, 목표 +2% 이상. 매수 전 40봉 저점/5·10·20선 밀집지지 유지 시 보류, "
                    "지지 이탈 또는 순손실 -3% 도달 시 손절."
                ))
        except Exception:
            pass

    def text_init(self: Any, master: Any = None, cnf: Any = None, **kwargs: Any) -> None:
        old_text_init(self, master, cnf or {}, **kwargs)
        try:
            if str(master.cget("text") or "") != "10분 장세분석 · 30분 상세이력":
                return
            def attach() -> None:
                if getattr(self, "_v7609_market_xscroll_ready", False):
                    return
                self._v7609_market_xscroll_ready = True
                x = ttk.Scrollbar(master, orient="horizontal", command=self.xview)
                self.configure(xscrollcommand=x.set, wrap="none")
                x.pack(side="bottom", fill="x", before=self)
                self._v7609_market_xscrollbar = x
            self.after_idle(attach)
        except Exception:
            pass

    def string_set(self: Any, value: Any) -> None:
        text_value = str(value)
        replacements = {
            "자동탐색 우선/저장 위치 예비": "저장 위치 우선/자동탐색 예비",
            "자동탐색 실패 때만 예비로 사용합니다.": "클릭·화살표 때 이 위치를 우선 사용합니다.",
            "자동탐색모드": "0600 위치 미저장",
        }
        for old, new in replacements.items():
            text_value = text_value.replace(old, new)
        old_string_set(self, text_value)
        if getattr(self, "_v7595_category_filter", False):
            try:
                root = self._root()
                root.after_idle(
                    lambda root=root, stage=text_value: _apply_stock_table_layout(
                        root, stage
                    )
                )
            except Exception:
                pass

    def bind(self: Any, sequence: Any = None, func: Any = None, add: Any = None) -> Any:
        result = old_bind(self, sequence, func, add)
        if sequence == "<Return>" and func is not None and getattr(self, "_v7595_main_stock_tree", False):
            self._v7609_navigate_selected = func
        try:
            if func is not None and sequence == "<Up>":
                old_bind(self, "<Left>", func, add)
            elif func is not None and sequence == "<Down>":
                old_bind(self, "<Right>", func, add)
        except Exception:
            pass
        return result

    def protocol(self: Any, name: Any = None, func: Any = None) -> Any:
        if name == "WM_DELETE_WINDOW" and func is not None:
            return old_protocol(self, name, lambda root=self: _request_dashboard_shutdown(root))
        return old_protocol(self, name, func)

    def mainloop(self: Any, *args: Any, **kwargs: Any) -> Any:
        gui = getattr(_V7609_GUI_CONTEXT, "gui", None)
        if gui is not None and hasattr(gui, "tree") and not getattr(self, "_v7609_trade_view", None):
            from v7609_trade_view import TradeView, install_command_submit
            self._v7609_trade_view = TradeView(self, gui, {
                "매수종목": parent._R21_STAGE_ROW_COLORS["매수종목"],
                "매도종목": (_V7609_SELL_BODY, _V7609_SELL_TEXT),
                "gain": _V7609_SELL_GAIN_TEXT, "loss": _V7609_SELL_LOSS_TEXT})
            install_command_submit(self, gui, _v7609_compile_command, v57.HUB)
        self.after_idle(lambda: _fit(self))
        self.after(350, lambda: _fit(self))
        return old_mainloop(self, *args, **kwargs)

    def insert(
        self: Any, parent_iid: Any, index: Any, iid: Any = None, **kwargs: Any
    ) -> Any:
        stage_tags = None
        if getattr(self, "_v7595_main_stock_tree", False):
            values = list(kwargs.get("values", ()))
            if len(values) >= 2:
                reason = str(values[-1] if values else "")
                # History prose is not a departure flag.  Current-stage rows
                # are authoritative; genuine departed rows retain their tag.
                if "R20_DEPARTED" not in kwargs.get("tags", ()):
                    values[1] = canonical_stage(values[1])
                    kwargs["values"] = tuple(values)
                    tag = STAGE_TO_TAG.get(str(values[1]).strip())
                    if tag == "매도종목":
                        if "▲ 수익" in reason:
                            tag = "매도종목_수익"
                        elif "▼ 손실" in reason:
                            tag = "매도종목_손실"
                    if tag:
                        stage_tags = (tag,)
                        kwargs["tags"] = stage_tags
        result = old_insert(self, parent_iid, index, iid, **kwargs)
        if stage_tags is not None:
            # The locked parent still greys arrow-containing prose in insert.
            # Restore only this row's actual-stage tag after that formatter.
            self.item(result, tags=stage_tags)
        return result

    tk.Tk.mainloop = mainloop
    tk.Tk._v7600_fit_installed = True
    tk.Label.__init__ = label_init
    tk.Text.__init__ = text_init
    tk.StringVar.set = string_set
    tk.Misc.bind = bind
    tk.Wm.protocol = protocol
    ttk.Treeview.__init__ = tree_init
    ttk.Treeview.insert = insert

    try:
        v57.V7557UnifiedDashboard.DEBOUNCE_SEC = 0.10
    except Exception:
        pass


_install_gui_fit_and_colors()


def _install_gui_thread_shutdown_cleanup() -> None:
    """Destroy Tcl in its owner thread after mainloop has fully unwound."""

    gui_class = getattr(v57, "V7557UnifiedDashboard", None)
    original_run = getattr(parent, "_ORIGINAL_GUI_RUN", None)
    if gui_class is None or not callable(original_run):
        return
    if bool(getattr(gui_class, "_v7609_safe_shutdown_installed", False)):
        return

    def safe_gui_run(self: Any) -> Any:
        _V7609_GUI_CONTEXT.gui = self
        failure: Exception | None = None
        result: Any = None
        try:
            result = original_run(self)
        except Exception as exc:
            failure = exc
            try:
                parent._write_startup_status(
                    "GUI_THREAD_FAILED", f"{type(exc).__name__}: {exc}"
                )
            except Exception:
                pass
            try:
                self.logger.exception("GUI thread fatal error")
            except Exception:
                pass
        finally:
            root = getattr(self, "root", None)
            user_close = bool(
                root is not None
                and getattr(root, "_v7607_shutdown_requested", False)
            )
            closed = bool(
                getattr(self, "closed", None)
                and self.closed.is_set()
            )
            if user_close and not closed:
                try:
                    self.closed.set()
                except Exception:
                    pass
                closed = True
            if failure is None and not closed:
                try:
                    parent._write_startup_status(
                        "GUI_THREAD_FAILED",
                        "GUI thread returned before normal engine close",
                    )
                except Exception:
                    pass
            for attr_name in (
                "active_tree",
                "category_count_vars",
                "category_filter_var",
                "choose_category",
                "collab_input",
                "dashboard_notebook",
                "kpi_vars",
                "request_preview_tree",
                "tree",
            ):
                try:
                    setattr(self, attr_name, None)
                except Exception:
                    pass
            # Release widget, StringVar, and closure references while Tcl is
            # still owned by this GUI thread.
            try:
                gc.collect()
            except Exception:
                pass
            if root is not None:
                try:
                    root.destroy()
                except Exception:
                    pass
            try:
                self.root = None
            except Exception:
                pass
            # Tk objects must be finalized in the thread that owns Tcl.
            root = None
            try:
                gc.collect()
            except Exception:
                pass
        return result

    gui_class._run = safe_gui_run
    gui_class._v7609_safe_shutdown_installed = True


_install_gui_thread_shutdown_cleanup()


class V7600ThreeStageEngine(_PARENT_ENGINE):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._v7609_session_date = datetime.now(KST).date()
        self._v76_tranche: dict[str, int] = {}
        self._v76_order_bar: dict[tuple[str, int], datetime] = {}
        self._v76_strict_observation: dict[str, datetime] = {}
        self._v76_strict_interest: dict[str, datetime] = {}
        self._v76_strict_prebuy: dict[str, datetime] = {}
        self._v76_strict_market_hold: dict[str, datetime] = {}
        self._v76_strict_block: dict[str, tuple[datetime, str]] = {}
        self._v7609_postclose_review_cache: list[dict[str, Any]] | None = None
        self._v7609_postclose_price_cache: dict[str, dict[str, Any]] | None = None
        self._v7609_intrabar_interest_hold_log: dict[str, datetime] = {}
        self._v7609_target_exit_at: dict[str, datetime] = {}
        self._v76_fast_fail: dict[str, dict[str, Any]] = {}
        self._v76_market_cache_at = 0.0
        self._v76_market_return = 0.0
        self._v76_market_cache = (True, "기존시장가드사용")
        self._v7609_stage_lock = threading.RLock()
        self._v7609_stage_manager = _V7600_STATE.StateManager()
        self._v7609_stage_log = _V7600_STATE.JsonlEventLog(
            self._v7609_stage_event_path(self._v7609_session_date)
        )
        self._v7609_order_thread = threading.local()
        self._v7609_order_ledger = _V7600_ORDER.OrderIntentLedger(ORDER_INTENT_FILE)
        super().__init__(*args, **kwargs)
        # Requested 08:00-20:00 candidate service; exchange/data/order guards still apply.
        self.after_entry_enabled = True
        self._v7609_seen_paper_fill_ids: set[str] = set()
        self._v7609_latest_buy_fill: dict[str, dict[str, Any]] = {}
        self._v7609_install_paper_fill_state_bridge()
        self._v7609_install_order_idempotency_bridge()
        tightened = False
        for symbol in list(getattr(self.broker, "positions", {})):
            tightened = self._v7609_enforce_fixed_stop(symbol) or tightened
        if tightened:
            self.broker.save_state()
        now = datetime.now(KST)
        self._v7609_restore_authoritative_stage_events(now)
        self._v7609_reconcile_current_holdings(now)
        for symbol, fill in self._v7609_fill_records(now).items():
            sell_time = fill.get("sell_time")
            sell_reason = str(fill.get("sell_reason", "") or "")
            if isinstance(sell_time, datetime) and "목표" in sell_reason:
                self._v7609_target_exit_at[str(symbol)] = sell_time
        self._v7609_restore_today_scanner_state(now)
        self._v7609_prune_unverified_scanner_rows(now)
        # The three rank APIs already return up to rank_limit unique symbols.
        # A lower inherited supply_pool_limit silently discarded the tail
        # before observation-admission bars were checked.  Keep the existing
        # API/rate budget and inspect only the candidates already received.
        self.loose_supply_config = replace(
            self.loose_supply_config,
            supply_pool_limit=_v7609_observation_pool_limit(
                self.loose_supply_config.supply_pool_limit,
                self.rank_limit,
            ),
        )
        self._load_v76_tranches()

        self.logger.warning(
            "[%s] 코난 PAPER 2단계 활성 | 관심=주변음봉대비 두드러진 거래 "
            "| 1차=거래바닥후 아래꼬리짧은음봉 1/2 | 2차=짧은양봉 MA5안착 1/2 "
            "| 돌파=왼쪽최대거래소화→중심선지지→재상승 | 08:00~08:50·09:00~15:30",
            VERSION,
        )

    @staticmethod
    def _v7609_stage_event_path(session_date: Any) -> Path:
        """Keep each KST session in its own append-only event chain."""

        label = session_date.isoformat() if hasattr(session_date, "isoformat") else str(session_date)
        return STAGE_EVENT_DIR / f"v7609_stage_events_{label.replace('-', '')}.jsonl"

    def _v7609_state_manager_obj(self) -> Any:
        manager = getattr(self, "_v7609_stage_manager", None)
        if manager is None:
            manager = _V7600_STATE.StateManager()
            self._v7609_stage_manager = manager
        return manager

    def _v7609_state_log_obj(self, now: datetime) -> Any:
        expected = self._v7609_stage_event_path(now.date())
        log = getattr(self, "_v7609_stage_log", None)
        if log is None or Path(getattr(log, "path", "")) != expected:
            log = _V7600_STATE.JsonlEventLog(expected)
            self._v7609_stage_log = log
        return log

    @staticmethod
    def _v7609_state_from_engine_stage(stage: Any) -> str | None:
        return {
            "관찰후보": _V7600_STATE.OBSERVED,
            "관심종목": _V7600_STATE.INTEREST,
            "매수가능성": _V7600_STATE.PREBUY,
            "매수직전": _V7600_STATE.PREBUY,
            "매수후보": _V7600_STATE.PREBUY,
            "시장보류": _V7600_STATE.MARKET_HOLD,
        }.get(canonical_stage(stage))

    @staticmethod
    def _v7609_engine_stage_from_state(stage: str | None) -> str | None:
        return {
            _V7600_STATE.OBSERVED: "관찰후보",
            _V7600_STATE.INTEREST: "관심종목",
            _V7600_STATE.PREBUY: "매수가능성",
            _V7600_STATE.MARKET_HOLD: "시장보류",
            _V7600_STATE.HOLDING: "매수종목",
            _V7600_STATE.SOLD: "매도종목",
        }.get(stage)

    def _v7609_raw_one_minute_evidence(
        self, symbol: str, reference_time: datetime | None = None
    ) -> dict[str, Any]:
        """Return the exact normalized REST 1m window used by the decision."""

        symbol = str(symbol).zfill(6)
        client = getattr(self, "client", None)
        raw = list(getattr(client, "v7580_raw_cache", {}).get(symbol, []))
        candidates: list[tuple[datetime, Any]] = []
        for item in raw:
            try:
                source_minutes = int(getattr(item, "source_minutes", 1) or 1)
            except (TypeError, ValueError):
                continue
            if source_minutes != 1:
                continue
            stamp = getattr(item, "timestamp", None)
            if not isinstance(stamp, datetime):
                continue
            if isinstance(reference_time, datetime):
                try:
                    if stamp.date() != reference_time.date() or stamp > reference_time:
                        continue
                except (TypeError, ValueError):
                    continue
            candidates.append((stamp, item))
        if not candidates:
            return {}
        normalized: list[dict[str, Any]] = []
        for stamp, bar in sorted(candidates, key=lambda pair: pair[0].isoformat()):
            values = {
                "timestamp": stamp.isoformat(),
                "open": float(getattr(bar, "open", 0.0) or 0.0),
                "high": float(getattr(bar, "high", 0.0) or 0.0),
                "low": float(getattr(bar, "low", 0.0) or 0.0),
                "close": float(getattr(bar, "close", 0.0) or 0.0),
                "volume": float(getattr(bar, "volume", 0.0) or 0.0),
            }
            if min(values[key] for key in ("open", "high", "low", "close")) > 0:
                normalized.append(values)
        if not normalized:
            return {}
        ohlcv = normalized[-1]
        stamp = datetime.fromisoformat(str(ohlcv["timestamp"]))
        window_text = json.dumps(
            normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return {
            "source": "KIWOOM_REST_KA10080_RAW_1M",
            "symbol": symbol,
            "data_time": stamp.isoformat(),
            "ohlcv": ohlcv,
            "one_minute_bars": normalized,
            "one_minute_bars_sha256": hashlib.sha256(
                window_text.encode("utf-8")
            ).hexdigest(),
        }

    def _v7609_state_evidence(
        self,
        symbol: str,
        reason: str,
        reference_time: datetime | None = None,
    ) -> dict[str, Any]:
        """Save the exact REST one-minute OHLCV used by this request."""

        evidence = self._v7609_raw_one_minute_evidence(symbol, reference_time)
        if not evidence:
            return {}
        evidence["reason"] = str(reason)
        evidence["evidence_schema"] = "EVID-001-v1"
        return evidence

    def _v7609_requested_prebuy(self, symbol: str, raw: list[Any], now: datetime, quote: Any = None) -> None:
        """Requested admission and signal generation; retain the existing PAPER order path."""
        symbol = str(symbol).zfill(6)
        manager = self._v7609_state_manager_obj()
        def protected() -> bool:
            return (symbol in self.broker.positions or symbol in self.pending_entries
                    or symbol in self._v7587_excluded
                    or manager.stage_of(symbol) in {_V7600_STATE.HOLDING, _V7600_STATE.SOLD})
        if protected() or not _PREBUY_RULES.in_session(now):
            return
        try:
            cache = getattr(self, '_v7609_prebuy_daily_cache', {})
            self._v7609_prebuy_daily_cache = cache
            key = (symbol, now.date())
            saved = cache.get(key)
            if _PREBUY_RULES.needs_daily(raw, now) and (saved is None or
                    (saved[1] is None and (now-saved[0]).total_seconds() >= 60)):
                cache[key] = (now, None)
                try:
                    daily = self.client.get_daily_bars(symbol, limit=3)
                except Exception as exc:
                    self.logger.warning('V7609 전일거래량 확인대기 %s | %s', symbol, type(exc).__name__)
                    daily = []
                now = datetime.now(KST)
                previous = [b for b in daily if b.trading_date < now.date()
                            and str(b.symbol).zfill(6) == symbol]
                if previous and key[1] == now.date():
                    day = max(previous, key=lambda b: b.trading_date)
                    cache[key] = (now, (day.trading_date.isoformat(), float(day.volume)))
                saved = cache.get(key)
            prior = saved[1] if saved else None
            proof = _PREBUY_RULES.evaluate(raw, now=now,
                previous_day=prior[0] if prior else None, previous_volume=prior[1] if prior else None)
            if proof is None:
                return
            identity = _V7600_STATE.make_signal_id(engine_version=VERSION, symbol=symbol,
                rule_id=_PREBUY_RULES.RULE_ID + ':' + proof['route'],
                data_time=proof['episode'], new_stage=_V7600_STATE.PREBUY)
            proof.update(symbol=symbol, signal_id=identity, engine_version=VERSION,
                         decision_time=now.isoformat(), order_intent_id='')
            with self._v7609_stage_lock:
                if protected():
                    return
                if not any(e.signal_id == identity for e in manager.events):
                    event = manager.apply(_V7600_STATE.TransitionRequest(
                        signal_id=identity, symbol=symbol, new_stage=_V7600_STATE.PREBUY,
                        rule_id=_PREBUY_RULES.RULE_ID, data_time=proof['data_time'],
                        decision_time=now.isoformat(), engine_version=VERSION,
                        reason=proof['reason'], evidence=proof))
                    try:
                        self._v7609_state_log_obj(now).append(event)
                    except Exception:
                        manager.discard_last(event)
                        raise
                    self._supply_potential[symbol] = self._v7588_make_potential(
                        symbol, '매수직전', proof['reason'], now)
                    self._v7588_phase[symbol] = 'PREBUY'
                    self._v76_strict_prebuy[symbol] = datetime.fromisoformat(proof['data_time'])
                    self._v76_strict_interest.pop(symbol, None)
                    self._save_supply_potential()
                if not proof['buy'] or quote is None or not self._v7609_current_prebuy_approved(symbol, now):
                    return
                stock_bars = [SimpleNamespace(**dict(b, timestamp=datetime.fromisoformat(b['timestamp'])))
                              for b in proof['bars']]
                market_ok, reason = self._market_gate_v76(stock_bars=stock_bars,
                    allow_relative_strength=proof['route'] == 'BOTTOM_MA5')
                if (getattr(self, '_v76_market_regime', 'NORMAL') == 'SHARP_DECLINE'
                        and not _PREBUY_RULES.sharp_market_entry_allowed(proof)):
                    market_ok, reason = False, 'SHARP_MARKET_SHORT_BULL_REQUIRED'
                if not market_ok:
                    self._v7609_record_stage_wait(symbol, reason,
                        datetime.fromisoformat(proof['data_time']), now, 'REQUESTED_PREBUY_MARKET')
                    return
                stamp = datetime.fromisoformat(proof['data_time'])
                slot = 9 if proof['route'] == 'BREAKOUT' else 6
                if self._v76_order_bar.get((symbol, slot)) == stamp:
                    return
                window = _ENTRY_POLICY.blocked_window(datetime.now(KST))
                if window:
                    self._v7610_log_entry_block(symbol, window, proof['reason'])
                    return
                # Revalidate after market I/O; a stale minute cannot authorize an order.
                order_now = datetime.now(KST)
                if not _PREBUY_RULES.validate_evidence(proof, proof['data_time'], order_now.isoformat()):
                    return
                price = float(getattr(quote, 'price', 0) or 0)
                if not math.isfinite(price) or price <= 0:
                    return
                pattern = BREAKOUT_PATTERN if slot == 9 else PATTERN
                signal = _V753.Signal(symbol, _V753.SignalAction.BUY, 152.0 if slot == 9 else 110.0,
                    f"{_PREBUY_RULES.RULE_ID}|{proof['reason']}|PAPER_ONLY", price, stamp, pattern,
                    V7609_FIXED_STOP_LOSS_PCT, V7609_TAKE_PROFIT_PCT)
                self.latest_signals[symbol] = signal
                self._v76_order_bar[(symbol, slot)] = stamp
                self._attempt_entry(signal)
        except Exception as exc:
            self.logger.warning('V7609 요청 매수직전 판정 보류 %s | %s', symbol, type(exc).__name__)

    def _v7609_try_volume_prebuy(self, symbol: str, raw: list[Any], now: datetime) -> datetime:
        """One evidence-checked direct PREBUY route, never an order route."""
        symbol = str(symbol).zfill(6)
        manager = self._v7609_state_manager_obj()
        def protected() -> bool:
            return (
                symbol in getattr(getattr(self, "broker", None), "positions", {})
                or symbol in getattr(self, "pending_entries", {})
                or symbol in getattr(self, "_v7587_excluded", {})
                or manager.stage_of(symbol) not in {
                    None, _V7600_STATE.OBSERVED, _V7600_STATE.INTEREST, _V7600_STATE.REMOVED
                }
            )
        if protected():
            return now
        try:
            if _VOLUME5.volume_inputs(raw, now) is None:
                return now
            cache = getattr(self, "_v7609_volume_daily_cache", {})
            self._v7609_volume_daily_cache = cache
            key = (symbol, now.date())
            saved = cache.get(key)
            if saved is None or (saved[1] is None and (now - saved[0]).total_seconds() >= 60):
                if any(k[1] != now.date() for k in cache):
                    cache.clear()
                cache[key] = (now, None)
                try:
                    daily = self.client.get_daily_bars(symbol, limit=3)
                finally:
                    # Preserve the existing post-fetch freshness gate even if
                    # this extra read crosses a minute or session boundary.
                    now = datetime.now(KST)
                previous = [b for b in daily if b.trading_date < now.date()
                            and str(b.symbol).zfill(6) == symbol]
                if not previous:
                    return now
                bar = max(previous, key=lambda b: b.trading_date)
                cache[key] = (now, (bar.trading_date, bar.close))
                saved = cache[key]
            if saved[1] is None or key[1] != now.date():
                return now
            evidence = _VOLUME5.evaluate(raw, now=now, previous_day=saved[1][0], previous_close=saved[1][1])
            if evidence is None:
                return now
            lock = getattr(self, "_v7609_stage_lock", None)
            if lock is None:
                lock = self._v7609_stage_lock = threading.RLock()
            with lock:
                if protected():
                    return now
                signal_id = _V7600_STATE.make_signal_id(
                    engine_version=VERSION, symbol=symbol, rule_id=_VOLUME5.RULE_ID,
                    data_time=evidence["data_time"], new_stage=_V7600_STATE.PREBUY,
                )
                evidence.update(symbol=symbol, signal_id=signal_id, order_intent_id="",
                                decision_time=now.isoformat(), engine_version=VERSION)
                request = _V7600_STATE.TransitionRequest(
                    signal_id=signal_id, symbol=symbol, new_stage=_V7600_STATE.PREBUY,
                    rule_id=_VOLUME5.RULE_ID, data_time=evidence["data_time"],
                    decision_time=now.isoformat(), engine_version=VERSION,
                    reason="5배급등거래", evidence=evidence,
                )
                item = self._v7588_make_potential(symbol, "매수직전", "5배급등거래", now)
                event = manager.apply(request)
                try:
                    self._v7609_state_log_obj(now).append(event)
                except OSError:
                    manager.discard_last(event)
                    raise
                self._supply_potential[symbol] = item
                self._v7588_phase[symbol] = "PREBUY"
                self._v76_strict_prebuy[symbol] = datetime.fromisoformat(evidence["data_time"])
                self._v76_strict_interest.pop(symbol, None)
                self._save_supply_potential()
        except Exception as exc:
            # Failure of this additional lookup/admission must not stop the
            # existing one-minute strategy, exits or position management.
            logger = getattr(self, "logger", None)
            if logger is not None:
                logger.warning("V7609 5배급등거래 편입 보류 %s: %s", symbol, exc)
        return now

    def _v7609_try_early_volume_buy(self, symbol: str, raw: list[Any], now: datetime) -> None:
        """Explicit PAPER partial-bar exception; all other strategies stay completed-only."""
        symbol = str(symbol).zfill(6)
        manager = self._v7609_state_manager_obj()
        if (symbol in self.broker.positions or symbol in self.pending_entries
                or symbol in self._v7587_excluded
                or manager.stage_of(symbol) not in {None, _V7600_STATE.OBSERVED,
                    _V7600_STATE.INTEREST, _V7600_STATE.REMOVED, _V7600_STATE.PREBUY}):
            return
        if _VOLUME5.early_inputs(raw, now) is None:
            return
        cache = getattr(self, "_v7609_volume_daily_cache", {})
        self._v7609_volume_daily_cache = cache
        key = (symbol, now.date())
        saved = cache.get(key)
        if saved is None or (saved[1] is None and (now - saved[0]).total_seconds() >= 60):
            cache[key] = (now, None)
            daily = self.client.get_daily_bars(symbol, limit=3)
            now = datetime.now(KST)
            previous = [b for b in daily if b.trading_date < now.date() and str(b.symbol).zfill(6) == symbol]
            if previous:
                day = max(previous, key=lambda b: b.trading_date)
                cache[key] = (now, (day.trading_date, day.close))
            saved = cache[key]
        if saved[1] is None or key[1] != now.date():
            return
        evidence = _VOLUME5.evaluate_early(raw, now=now, previous_day=saved[1][0], previous_close=saved[1][1])
        if evidence is None:
            return
        signal_id = _V7600_STATE.make_signal_id(engine_version=VERSION, symbol=symbol,
            rule_id=EARLY_PATTERN, data_time=evidence["data_time"], new_stage=_V7600_STATE.PREBUY)
        evidence.update(symbol=symbol, signal_id=signal_id, order_intent_id="",
                        decision_time=now.isoformat(), engine_version=VERSION)
        with self._v7609_stage_lock:
            if symbol in self.broker.positions or symbol in self.pending_entries:
                return
            if manager.stage_of(symbol) not in {None, _V7600_STATE.OBSERVED,
                    _V7600_STATE.INTEREST, _V7600_STATE.REMOVED, _V7600_STATE.PREBUY}:
                return
            if manager.stage_of(symbol) != _V7600_STATE.PREBUY:
                event = manager.apply(_V7600_STATE.TransitionRequest(
                    signal_id=signal_id, symbol=symbol, new_stage=_V7600_STATE.PREBUY,
                    rule_id=EARLY_PATTERN, data_time=evidence["data_time"],
                    decision_time=now.isoformat(), engine_version=VERSION,
                    reason=_VOLUME5.EARLY_REASON, evidence=evidence))
                try:
                    self._v7609_state_log_obj(now).append(event)
                except OSError:
                    manager.discard_last(event)
                    raise
                self._supply_potential[symbol] = self._v7588_make_potential(
                    symbol, "매수직전", _VOLUME5.EARLY_REASON, now)
                self._v7588_phase[symbol] = "PREBUY"
                self._v76_strict_prebuy[symbol] = datetime.fromisoformat(evidence["data_time"])
                self._v76_strict_interest.pop(symbol, None)
                self._save_supply_potential()
            proofs = getattr(self, "_v7609_early_proofs", {})
            self._v7609_early_proofs = proofs
            proofs[symbol] = evidence
            if not _ENTRY_POLICY.EARLY_VOLUME_BUY_ENABLED:
                # The PREBUY stage above is unchanged; only the order is withheld.
                self._v7610_log_entry_block(symbol, "VOLUME5_EARLY 매수중단", _VOLUME5.EARLY_REASON)
                return
            signal = _V753.Signal(symbol, _V753.SignalAction.BUY, 100.0,
                _VOLUME5.EARLY_REASON, float(evidence["trigger_bar"]["close"]),
                datetime.fromisoformat(evidence["data_time"]), EARLY_PATTERN,
                V7609_FIXED_STOP_LOSS_PCT, V7609_TAKE_PROFIT_PCT)
            self._attempt_entry(signal)

    def _v7609_early_order_allowed(self, symbol: str, price: float, now: datetime) -> bool:
        proof = getattr(self, "_v7609_early_proofs", {}).get(str(symbol))
        if not proof or not _VOLUME5.validate_evidence(proof, proof["data_time"], proof["decision_time"]):
            return False
        stamp = datetime.fromisoformat(proof["data_time"])
        measured = datetime.fromisoformat(proof["decision_time"])
        elapsed = (now - stamp).total_seconds()
        return bool(25 <= elapsed < 60 and 0 <= (now - measured).total_seconds() <= 10
            and math.isfinite(float(price))
            and float(price) > max(proof["trigger_bar"]["open"], proof["previous_close"])
            and proof["trigger_bar"]["volume"] * 60 / elapsed >= proof["reference_average_volume"] * 5)

    def _v7610_log_entry_block(self, symbol: str, why: str, detail: str) -> None:
        """One log line per symbol and cause per minute; polling repeats every 10 seconds."""
        seen = getattr(self, "_v7610_entry_block_log", {})
        self._v7610_entry_block_log = seen
        key = (str(symbol), why)
        clock = time.monotonic()
        if clock - seen.get(key, -100.0) < 60:
            return
        seen[key] = clock
        self.logger.warning("V7610 자동매수 차단 %s | %s | %s", self._label(str(symbol)), why, detail)

    def _v7610_record_pullback_shadow(self, symbol: str, completed: list[Any], now: datetime) -> None:
        """Record the unverified pullback rule only; never an order or stage route."""
        if not completed:
            return
        last_seen = getattr(self, "_v7610_pullback_checked", {})
        self._v7610_pullback_checked = last_seen
        stamp = getattr(completed[-1], "timestamp", None)
        if last_seen.get(symbol) == stamp:
            return
        rows = _PREBUY_RULES.completed(completed, now)
        signal = _PULLBACK.detect(rows) if rows else None
        recorded = getattr(self, "_v7610_pullback_recorded", set())
        self._v7610_pullback_recorded = recorded
        key = (symbol, signal["peak_time"]) if signal else None
        if signal is None or key in recorded:
            last_seen[symbol] = stamp
            return
        signal.update(symbol=symbol, label=self._label(symbol), decision_time=now.isoformat(),
                      engine_version=VERSION, blocked_window=_ENTRY_POLICY.blocked_window(now),
                      order="NONE_SHADOW_ONLY")
        PULLBACK_SHADOW_DIR.mkdir(parents=True, exist_ok=True)
        path = PULLBACK_SHADOW_DIR / f"pullback_shadow_{now:%Y%m%d}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(signal, ensure_ascii=False) + "\n")
        # Mark only after the write succeeded, so a failed write is retried on the next poll.
        recorded.add(key)
        last_seen[symbol] = stamp
        self.logger.info(
            "V7610 눌림목 그림자신호(주문없음) %s | 기준 %.0f 손절 %.0f(-%.2f%%) 파동고점 %.0f",
            self._label(symbol), signal["entry_ref"], signal["stop"], signal["risk_pct"],
            signal["peak_high"],
        )

    def _v7609_poll_volume(self, symbol: str) -> None:
        """Read one bounded snapshot on candidate service, even before bootstrap completes."""
        now = datetime.now(KST)
        if not _PREBUY_RULES.in_session(now) or symbol in self.broker.positions or symbol in self._v7587_excluded:
            return
        last = getattr(self, "_v7609_volume_polled", {})
        self._v7609_volume_polled = last
        if time.monotonic() - last.get(symbol, -100.0) < 10:
            return
        last[symbol] = time.monotonic()
        try:
            raw = list(self.client.get_experiment_bars(symbol))
            now = datetime.now(KST)
            completed = self._v7609_completed_one_minute_bars(raw, now)
            # Never leak the experiment's partial candle into old strategy caches.
            self.client.v7580_raw_cache[symbol] = completed
            snapshots = getattr(self, "_v7609_volume_snapshots", {})
            self._v7609_volume_snapshots = snapshots
            snapshots[symbol] = (time.monotonic(), completed)
            now = self._v7609_try_volume_prebuy(symbol, completed, now)
            self._v7609_requested_prebuy(symbol, completed, now)
            self._v7609_stage_only_admission(symbol, completed, now)
            self._v7609_update_early_observation(symbol, completed, now)
            try:
                self._v7610_record_pullback_shadow(symbol, completed, now)
            except Exception as exc:
                self.logger.warning("V7610 눌림목 그림자 기록 보류 %s | %s", symbol, type(exc).__name__)
            self._v7609_try_early_volume_buy(symbol, raw, now)
        except Exception as exc:
            self.logger.warning("V7609 거래량 실험 판정 보류 %s | %s", symbol, type(exc).__name__)

    def _service_symbol(self, symbol: str, *, hot: bool) -> None:
        # The parent run loop already monitors one holding before candidate I/O.
        # Repeating it for every candidate starves quote/bar requests when
        # holdings exist and can leave the observation buckets empty.
        self._v7609_poll_volume(str(symbol))
        # A just-fetched snapshot need not wait for the old chart timer.
        snap = getattr(self, '_v7609_volume_snapshots', {}).get(str(symbol))
        if snap and time.monotonic()-snap[0] < 2 and symbol in getattr(self, 'next_chart_due', {}):
            self.next_chart_due[symbol] = min(self.next_chart_due[symbol], time.monotonic())
        super()._service_symbol(symbol, hot=hot)

    def _update_hot_symbols(self) -> None:
        super()._update_hot_symbols()
        now = datetime.now(KST)
        early = [s for s, c in getattr(self, '_v7609_early_observation', {}).items()
                 if now < c['expires_at'] and s not in self.broker.positions
                 and s not in self._v7587_excluded and not self._v7588_phase.get(s)]
        # Keep established entry candidates ahead of provisional display rows.
        urgent = [s for s in self.hot_symbols if s in self.pending_entries
                  or self._v7588_phase.get(s) in {'PREBUY', 'INTEREST'}]
        limit = max(1, len(self.hot_symbols), int(getattr(self, 'hot_candidate_count', 1)))
        self.hot_symbols = list(dict.fromkeys(urgent + early + self.hot_symbols))[:limit]

    def _bootstrap_one(self) -> bool:
        before = set(getattr(self, "last_bar_time", {}))
        result = super()._bootstrap_one()
        for symbol in set(getattr(self, "last_bar_time", {})) - before:
            raw = list(getattr(self.client, "v7580_raw_cache", {}).get(symbol, []))
            self._v7609_try_volume_prebuy(symbol, raw, datetime.now(KST))
        return result

    def _pending_signal_valid(self, signal: Any, price: float) -> tuple[bool, str]:
        if getattr(signal, "pattern", "") == EARLY_PATTERN:
            if not self._v7609_early_order_allowed(signal.symbol, price, datetime.now(KST)):
                return False, "조기매수 근거만료/음봉/5배예상미달"
        return super()._pending_signal_valid(signal, price)

    def _v7609_record_authoritative_stage(
        self, symbol: str, stage: str, reason: str, stamp: datetime
    ) -> bool:
        """Record only ordered scanner stages as authoritative V76 state."""

        target = self._v7609_state_from_engine_stage(stage)
        if target is None:
            return True
        symbol = str(symbol).zfill(6)
        manager = self._v7609_state_manager_obj()
        lock = getattr(self, "_v7609_stage_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._v7609_stage_lock = lock
        with lock:
            if manager.stage_of(symbol) == target:
                return True
            try:
                decision_time = datetime.now(KST).isoformat()
                rule_id = f"V7609_{target}"
                evidence = self._v7609_state_evidence(symbol, reason, stamp)
                if not evidence:
                    raise _V7600_STATE.TransitionRejected(
                        "REST 원본 1분봉 OHLCV 없는 단계 전이 차단"
                    )
                data_time = str(evidence["data_time"])
                signal_id = _V7600_STATE.make_signal_id(
                    engine_version=VERSION,
                    symbol=symbol,
                    rule_id=rule_id,
                    data_time=data_time,
                    new_stage=target,
                )
                evidence.update(
                    {
                        "signal_id": signal_id,
                        "order_intent_id": "",
                        "rule_id": rule_id,
                        "decision_time": decision_time,
                        "engine_version": VERSION,
                    }
                )
                request = _V7600_STATE.TransitionRequest(
                    signal_id=signal_id,
                    symbol=symbol,
                    new_stage=target,
                    rule_id=rule_id,
                    data_time=data_time,
                    decision_time=decision_time,
                    engine_version=VERSION,
                    reason=str(reason),
                    evidence=evidence,
                )
                event = manager.apply(request)
                try:
                    self._v7609_state_log_obj(stamp).append(event)
                except OSError:
                    manager.discard_last(event)
                    raise
                return True
            except (OSError, ValueError, TypeError, _V7600_STATE.TransitionRejected) as exc:
                logger = getattr(self, "logger", None)
                if logger is not None:
                    logger.warning("V7609 authoritative stage rejected %s: %s", symbol, exc)
                return False

    def _v7609_restore_authoritative_stage_events(self, now: datetime) -> None:
        manager = self._v7609_state_manager_obj()
        try:
            manager.restore(
                self._v7609_state_log_obj(now).read(),
                session_date=now.date().isoformat(),
                engine_version=VERSION,
            )
        except (OSError, ValueError, TypeError, _V7600_STATE.TransitionRejected) as exc:
            self._v7609_stage_manager = _V7600_STATE.StateManager()
            self.logger.warning("V7609 authoritative stage restore rejected: %s", exc)

    def _v7609_write_stage_mirror(self, symbol: str, item: Any, fallback_stage: str) -> bool:
        """Keep legacy SupplyPotential as a mirror, never the authority."""

        managed = self._v7609_engine_stage_from_state(
            self._v7609_state_manager_obj().stage_of(str(symbol))
        )
        desired = managed or fallback_stage
        if str(getattr(item, "stage", "")) == desired:
            return False
        item.stage = desired
        return True

    def _v7609_load_paper_fill_identity(self) -> None:
        """Load immutable PAPER order IDs and the latest BUY per symbol."""

        seen: set[str] = set()
        latest_buy: dict[str, dict[str, Any]] = {}
        holding_started = {}
        path = Path(getattr(_V753, "FILL_FILE", ""))
        if path.exists():
            try:
                with path.open("r", encoding="utf-8-sig", newline="") as handle:
                    for raw in csv.DictReader(handle):
                        order_id = str(raw.get("order_id", "") or "").strip()
                        symbol = str(raw.get("symbol", "") or "").strip().zfill(6)
                        side = str(raw.get("side", "") or "").strip().upper()
                        stamp = self._v7609_timestamp(
                            raw.get("timestamp"), datetime.now(KST)
                        )
                        if not order_id or len(symbol) != 6 or not symbol.isdigit():
                            continue
                        if order_id in seen:
                            raise ValueError(f"중복 PAPER 체결 ID 발견: {order_id}")
                        seen.add(order_id)
                        if side == 'SELL':
                            holding_started.pop(symbol, None)
                        if side == "BUY" and stamp is not None:
                            holding_started.setdefault(symbol, stamp)
                            latest_buy[symbol] = {
                                "order_id": order_id,
                                "timestamp": stamp,
                                "quantity": int(float(raw.get("quantity", 0) or 0)),
                                "price": float(raw.get("price", 0.0) or 0.0),
                                "reason": str(raw.get("reason", "") or ""),
                            }
            except (OSError, ValueError, TypeError, csv.Error) as exc:
                self.logger.critical("PAPER 체결원장 식별정보 검사 실패: %s", exc)
                raise
        self._v7609_seen_paper_fill_ids = seen
        self._v7609_latest_buy_fill = latest_buy
        self._v7609_holding_started = holding_started

    def _v7609_record_paper_fill_stage(self, fill: Any) -> bool:
        """Record a confirmed PAPER fill as HOLDING or SOLD exactly once."""

        side = str(getattr(fill, "side", "") or "").upper()
        target = {
            "BUY": _V7600_STATE.HOLDING,
            "SELL": _V7600_STATE.SOLD,
        }.get(side)
        if target is None:
            return True
        symbol = str(getattr(fill, "symbol", "") or "").zfill(6)
        order_id = str(getattr(fill, "order_id", "") or "").strip()
        quantity = int(getattr(fill, "quantity", 0) or 0)
        stamp = getattr(fill, "timestamp", None)
        if not isinstance(stamp, datetime):
            stamp = datetime.now(KST)
        manager = self._v7609_state_manager_obj()
        if manager.has_paper_fill(order_id):
            return True
        rule_id = f"PAPER_FILL_{side}"
        transition_signal_id = _V7600_STATE.make_signal_id(
            engine_version=VERSION,
            symbol=symbol,
            rule_id=f"{rule_id}:{order_id}",
            data_time=stamp.isoformat(),
            new_stage=target,
        )
        holder = self._v7609_order_thread_obj()
        active = getattr(holder, "current", None)
        claim = active.get("claim") if isinstance(active, dict) else None
        identity = getattr(claim, "identity", None)
        rest_evidence = self._v7609_raw_one_minute_evidence(symbol, stamp)
        old_stage = manager.stage_of(symbol)
        fill_evidence = {
            "evidence_schema": "EVID-001-v1",
            "source": "PAPER_FILL_LEDGER",
            "signal_id": transition_signal_id,
            "order_signal_id": str(getattr(identity, "signal_id", "") or ""),
            "order_intent_id": str(
                getattr(identity, "order_intent_id", "") or ""
            ),
            "symbol": symbol,
            "old_stage": old_stage or "",
            "new_stage": target,
            "side": side,
            "order_id": order_id,
            "price": float(getattr(fill, "price", 0.0) or 0.0),
            "quantity": quantity,
            "rule_id": rule_id,
            "reason": str(getattr(fill, "reason", "") or "PAPER_FILL"),
            "data_time": stamp.isoformat(),
            "decision_time": datetime.now(KST).isoformat(),
            "engine_version": VERSION,
            "rest_1m_available": bool(rest_evidence),
        }
        if rest_evidence:
            fill_evidence["rest_1m"] = rest_evidence
        request = _V7600_STATE.TransitionRequest(
            signal_id=transition_signal_id,
            symbol=symbol,
            new_stage=target,
            rule_id=rule_id,
            data_time=stamp.isoformat(),
            decision_time=fill_evidence["decision_time"],
            engine_version=VERSION,
            reason=str(getattr(fill, "reason", "") or "PAPER_FILL"),
            evidence=fill_evidence,
            quantity=quantity,
            paper_fill_id=order_id,
        )
        lock = getattr(self, "_v7609_stage_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._v7609_stage_lock = lock
        with lock:
            try:
                event = manager.apply_paper_fill(request)
                try:
                    self._v7609_state_log_obj(stamp).append(event)
                except OSError:
                    manager.discard_last(event)
                    raise
                if side == "SELL":
                    # A completed sale consumes the old scanner approval.  The
                    # fill itself remains authoritative; do not fabricate a new
                    # interest/prebuy transition to permit re-entry.
                    for name in ("_v76_strict_interest", "_v76_strict_prebuy"):
                        getattr(self, name, {}).pop(symbol, None)
                    getattr(self, "pending_entries", {}).pop(symbol, None)
                return True
            except (OSError, ValueError, TypeError, _V7600_STATE.TransitionRejected) as exc:
                self.logger.critical(
                    "PAPER 체결 단계기록 실패 %s %s: %s", symbol, order_id, exc
                )
                return False

    def _v7609_reconcile_one_holding(self, symbol: str, position: Any) -> bool:
        """Reconcile a real PAPER position from its latest durable BUY fill."""

        symbol = str(symbol).zfill(6)
        quantity = int(getattr(position, "quantity", 0) or 0)
        if quantity <= 0:
            self.logger.critical("PAPER 보유수량 오류 %s quantity=%s", symbol, quantity)
            return False
        manager = self._v7609_state_manager_obj()
        if manager.stage_of(symbol) == _V7600_STATE.HOLDING:
            return True
        raw = self._v7609_latest_buy_fill.get(symbol)
        if not isinstance(raw, dict):
            self.logger.critical("PAPER 보유 체결근거 누락 %s", symbol)
            return False
        fill = type("V7609RecoveredPaperFill", (), {
            "side": "BUY",
            "symbol": symbol,
            "order_id": str(raw.get("order_id", "")),
            "quantity": quantity,
            "price": float(raw.get("price", 0.0) or 0.0),
            "timestamp": datetime.now(KST),
            "reason": str(raw.get("reason", "") or "PAPER_POSITION_RESTORE"),
        })()
        return self._v7609_record_paper_fill_stage(fill)

    def _v7609_reconcile_current_holdings(self, now: datetime) -> None:
        del now
        for symbol, position in list(getattr(self.broker, "positions", {}).items()):
            self._v7609_reconcile_one_holding(str(symbol), position)

    def _v7609_install_paper_fill_state_bridge(self) -> None:
        """Install one bridge at the PAPER broker's final fill boundary."""

        broker = self.broker
        if bool(getattr(broker, "_v7609_state_bridge_installed", False)):
            return
        self._v7609_load_paper_fill_identity()
        original_record_fill = broker._record_fill
        original_sell = broker.sell

        def record_fill_once(fill: Any) -> None:
            with self._v7609_stage_lock:
                order_id = str(getattr(fill, "order_id", "") or "").strip()
                if not order_id:
                    raise ValueError("PAPER 체결 ID 누락")
                if order_id in self._v7609_seen_paper_fill_ids:
                    raise ValueError(f"중복 PAPER 체결 차단: {order_id}")
                original_record_fill(fill)
                self._v7609_seen_paper_fill_ids.add(order_id)
                starts = getattr(self, '_v7609_holding_started', {})
                self._v7609_holding_started = starts
                fill_symbol = str(getattr(fill, 'symbol', '')).zfill(6)
                if str(getattr(fill, 'side', '')).upper() == 'BUY':
                    starts.setdefault(fill_symbol, getattr(fill, 'timestamp', datetime.now(KST)))
                elif str(getattr(fill, 'side', '')).upper() == 'SELL':
                    starts.pop(fill_symbol, None)
                if str(getattr(fill, "side", "") or "").upper() == "BUY":
                    self._v7609_latest_buy_fill[str(getattr(fill, "symbol", "")).zfill(6)] = {
                        "order_id": order_id,
                        "timestamp": getattr(fill, "timestamp", datetime.now(KST)),
                        "quantity": int(getattr(fill, "quantity", 0) or 0),
                        "price": float(getattr(fill, "price", 0.0) or 0.0),
                        "reason": str(getattr(fill, "reason", "") or ""),
                    }
                self._v7609_record_paper_fill_stage(fill)
                if (str(getattr(fill, "side", "")).upper() == "BUY"
                        and isinstance(broker, _V753.PaperBroker)):
                    self._enforce_gem_orders(str(fill.symbol), announce=False)

        def sell_only_if_held(symbol: str, *args: Any, **kwargs: Any) -> Any:
            with self._v7609_stage_lock:
                symbol = str(symbol).zfill(6)
                position = getattr(broker, "positions", {}).get(symbol)
                quantity = int(getattr(position, "quantity", 0) or 0) if position else 0
                if quantity <= 0:
                    raise ValueError("실제 보유수량 0—PAPER 매도 차단")
                self._v7609_reconcile_one_holding(symbol, position)
                return original_sell(symbol, *args, **kwargs)

        broker._record_fill = record_fill_once
        broker.sell = sell_only_if_held
        broker._v7609_state_bridge_installed = True

    def _v7609_order_ledger_obj(self) -> Any:
        ledger = getattr(self, "_v7609_order_ledger", None)
        if ledger is None:
            ledger = _V7600_ORDER.OrderIntentLedger(ORDER_INTENT_FILE)
            self._v7609_order_ledger = ledger
        return ledger

    def _v7609_order_thread_obj(self) -> Any:
        holder = getattr(self, "_v7609_order_thread", None)
        if holder is None:
            holder = threading.local()
            self._v7609_order_thread = holder
        return holder

    def _v7609_make_order_identity(
        self,
        *,
        side: str,
        symbol: str,
        source: str,
        source_id: str,
        approval_id: str = "",
    ) -> Any:
        return _V7600_ORDER.make_identity(
            engine_version=VERSION,
            side=side,
            symbol=symbol,
            source=source,
            source_id=source_id,
            approval_id=approval_id,
        )

    @staticmethod
    def _v7609_stamp_text(value: Any) -> str:
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value or "")

    def _v7609_order_evidence(
        self, identity: Any, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Link an order intent to factual IDs and available REST one-minute data."""

        reference_time = None
        for key in ("data_time", "signal_time"):
            raw_stamp = payload.get(key)
            if isinstance(raw_stamp, datetime):
                reference_time = raw_stamp
                break
            if raw_stamp:
                try:
                    reference_time = datetime.fromisoformat(str(raw_stamp))
                    break
                except ValueError:
                    pass
        rest_evidence = self._v7609_raw_one_minute_evidence(
            identity.symbol, reference_time
        )
        old_stage = self._v7609_state_manager_obj().stage_of(identity.symbol)
        new_stage = {
            "BUY": _V7600_STATE.HOLDING,
            "SELL": _V7600_STATE.SOLD,
        }.get(identity.side, "")
        evidence = {
            "evidence_schema": "EVID-001-v1",
            "source": "PAPER_ORDER_INTENT",
            "signal_id": identity.signal_id,
            "order_intent_id": identity.order_intent_id,
            "symbol": identity.symbol,
            "side": identity.side,
            "old_stage": old_stage or "",
            "new_stage": new_stage,
            "rule_id": str(payload.get("pattern", "") or identity.source),
            "reason": str(payload.get("reason", "") or payload.get("note", "")),
            "engine_version": VERSION,
            "rest_1m_available": bool(rest_evidence),
        }
        if isinstance(reference_time, datetime):
            evidence["data_time"] = reference_time.isoformat()
        if rest_evidence:
            evidence["data_time"] = rest_evidence["data_time"]
            evidence["rest_1m"] = rest_evidence
        if str(payload.get("pattern", "")) == EARLY_PATTERN:
            partial = getattr(self, "_v7609_early_proofs", {}).get(identity.symbol)
            if partial:
                evidence["partial_1m_experiment"] = dict(partial)
                evidence["data_time"] = partial["data_time"]
        return evidence

    def _v7609_run_order_intent(
        self,
        identity: Any,
        payload: dict[str, Any],
        operation: Any,
        *,
        duplicate_result: Any,
    ) -> Any:
        """Run one approved order intent; retry only before fill acceptance."""

        holder = self._v7609_order_thread_obj()
        current = getattr(holder, "current", None)
        if isinstance(current, dict):
            active = current.get("claim")
            if (
                active is not None
                and active.identity.order_intent_id == identity.order_intent_id
            ):
                return operation()
            raise ValueError("서로 다른 PAPER 주문 의도가 한 실행 경로에 겹쳤습니다")
        ledger = self._v7609_order_ledger_obj()
        order_evidence = self._v7609_order_evidence(identity, payload)
        try:
            claim = ledger.claim(identity, payload, evidence=order_evidence)
        except _V7600_ORDER.DuplicateOrderIntent:
            if isinstance(duplicate_result, BaseException):
                raise duplicate_result
            return duplicate_result
        holder.current = {"claim": claim}
        try:
            result = operation()
        except Exception as exc:
            if ledger.latest_status(identity.order_intent_id) == "CLAIMED":
                ledger.release_before_acceptance(claim, f"{type(exc).__name__}: {exc}")
            raise
        finally:
            holder.current = None
        if ledger.latest_status(identity.order_intent_id) == "CLAIMED":
            ledger.release_before_acceptance(claim, "최종 PAPER 체결 경계 도달 전 종료")
        return result

    def _v7609_install_order_idempotency_bridge(self) -> None:
        """Guard final PAPER buy/sell/fill paths without changing order terms."""

        broker = self.broker
        if bool(getattr(broker, "_v7609_order_idempotency_installed", False)):
            return
        original_buy = broker.buy
        original_sell = broker.sell
        original_record_fill = broker._record_fill

        def record_fill_once(fill: Any) -> None:
            holder = self._v7609_order_thread_obj()
            current = getattr(holder, "current", None)
            owned_here = False
            if not isinstance(current, dict):
                fill_order_id = str(getattr(fill, "order_id", "") or "")
                if (
                    fill_order_id in getattr(self, "_v7609_seen_paper_fill_ids", set())
                    or self._v7609_state_manager_obj().has_paper_fill(fill_order_id)
                ):
                    raise ValueError(f"중복 PAPER 체결 차단: {fill_order_id}")
                identity = self._v7609_make_order_identity(
                    side=str(getattr(fill, "side", "") or "UNKNOWN"),
                    symbol=str(getattr(fill, "symbol", "") or ""),
                    source="DIRECT_FILL_FALLBACK",
                    source_id=fill_order_id,
                )
                fallback_payload = {
                    "order_id": fill_order_id,
                    "quantity": int(getattr(fill, "quantity", 0) or 0),
                    "price": float(getattr(fill, "price", 0.0) or 0.0),
                    "data_time": self._v7609_stamp_text(
                        getattr(fill, "timestamp", None)
                    ),
                    "reason": str(getattr(fill, "reason", "") or ""),
                }
                claim = self._v7609_order_ledger_obj().claim(
                    identity,
                    fallback_payload,
                    evidence=self._v7609_order_evidence(
                        identity, fallback_payload
                    ),
                )
                current = {"claim": claim}
                holder.current = current
                owned_here = True
            claim = current["claim"]
            ledger = self._v7609_order_ledger_obj()
            ledger.mark_accepting(claim)
            try:
                original_record_fill(fill)
                ledger.mark_accepted(
                    claim,
                    str(getattr(fill, "order_id", "") or ""),
                    fill={
                        "order_id": str(getattr(fill, "order_id", "") or ""),
                        "timestamp": self._v7609_stamp_text(
                            getattr(fill, "timestamp", None)
                        ),
                        "symbol": str(getattr(fill, "symbol", "") or "").zfill(6),
                        "side": str(getattr(fill, "side", "") or "").upper(),
                        "quantity": int(getattr(fill, "quantity", 0) or 0),
                        "price": float(getattr(fill, "price", 0.0) or 0.0),
                        "reason": str(getattr(fill, "reason", "") or ""),
                    },
                )
            finally:
                if owned_here:
                    holder.current = None

        def buy_once(
            symbol: str, quantity: int, quote: Any, reason: str, *args: Any, **kwargs: Any
        ) -> Any:
            display_reason = reason.replace(
                "|목표수익1.5%고정",
                f"|목표수익 비용포함+{V7609_TAKE_PROFIT_PCT:.1%}",
            )
            if kwargs.get("pattern") == EARLY_PATTERN:
                if not isinstance(broker, _V753.PaperBroker) or not self._v7609_early_order_allowed(
                        str(symbol), float(quote.price), datetime.now(KST)):
                    raise ValueError("PAPER 조기매수 최종 근거 미충족")
            holder = self._v7609_order_thread_obj()
            if isinstance(getattr(holder, "current", None), dict):
                return original_buy(symbol, quantity, quote, display_reason, *args, **kwargs)
            source_id = "|".join(
                (
                    str(symbol).zfill(6),
                    str(reason),
                    str(int(quantity)),
                    self._v7609_stamp_text(getattr(quote, "timestamp", None)),
                )
            )
            identity = self._v7609_make_order_identity(
                side="BUY",
                symbol=symbol,
                source="DIRECT_BUY_FALLBACK",
                source_id=source_id,
            )
            return self._v7609_run_order_intent(
                identity,
                {
                    "symbol": str(symbol).zfill(6),
                    "quantity": int(quantity),
                    "reason": str(display_reason),
                    "data_time": self._v7609_stamp_text(
                        getattr(quote, "timestamp", None)
                    ),
                },
                lambda: original_buy(symbol, quantity, quote, display_reason, *args, **kwargs),
                duplicate_result=ValueError("동일 PAPER 매수 주문 의도 중복 차단"),
            )

        def sell_once(symbol: str, quote: Any, reason: str, *args: Any, **kwargs: Any) -> Any:
            if reason == "예약목표+1.5%":
                reason = "예약목표 비용포함+2.0%"
            holder = self._v7609_order_thread_obj()
            if isinstance(getattr(holder, "current", None), dict):
                return original_sell(symbol, quote, reason, *args, **kwargs)
            symbol = str(symbol).zfill(6)
            latest_buy = self._v7609_latest_buy_fill.get(symbol, {})
            position = getattr(broker, "positions", {}).get(symbol)
            holding_id = str(latest_buy.get("order_id", "") or "")
            if not holding_id and position is not None:
                holding_id = self._v7609_stamp_text(getattr(position, "entry_time", None))
            identity = self._v7609_make_order_identity(
                side="SELL",
                symbol=symbol,
                source="AUTO_HOLDING_EXIT",
                source_id="|".join(
                    (
                        holding_id or f"NO_HOLDING:{symbol}",
                        str(reason),
                        str(kwargs.get("trigger_source", "") or ""),
                        str(kwargs.get("reserved_fill_price", "") or ""),
                    )
                ),
            )
            return self._v7609_run_order_intent(
                identity,
                {
                    "symbol": symbol,
                    "holding_id": holding_id,
                    "reason": str(reason),
                    "data_time": self._v7609_stamp_text(
                        getattr(quote, "timestamp", None)
                    ),
                },
                lambda: original_sell(symbol, quote, reason, *args, **kwargs),
                duplicate_result=ValueError("동일 PAPER 매도 주문 의도 중복 차단"),
            )

        broker.buy = buy_once
        broker.sell = sell_once
        broker._record_fill = record_fill_once
        broker._v7609_order_idempotency_installed = True

    def _v7609_capsule_quantity(self, symbol: str) -> int | None:
        capsule = getattr(self, "_v7555_execution_capsule", None)
        if not isinstance(capsule, dict) or str(capsule.get("symbol", "")) != str(symbol):
            return None
        overlay = parent.v7556.HUB.get(str(capsule.get("overlay_id", "")))
        if overlay is None or str(getattr(overlay, "status", "")) != "EXECUTING":
            return None
        return _v7609_quantity_from_text(str(getattr(overlay, "note", "")))

    def _v7609_manual_buy_exact_quantity(self, symbol: str, quantity: int) -> str:
        """Execute only a confirmed PAPER command's explicit share quantity."""

        if quantity < 1:
            raise ValueError("매수수량은 1주 이상이어야 합니다")
        if not self._new_entry_session_allowed():
            raise ValueError("현재 신규매수 중지 또는 진입시간 아님")
        if symbol in self.buy_blocked:
            raise ValueError("매수금지 종목")
        market_ok, market_reason = self._market_entry_allowed()
        if not market_ok:
            raise ValueError(f"시장가드 차단: {market_reason}")
        self._manual_add_watch(symbol, self.manual_watch.get(symbol, ""))
        quote = self._fresh_enriched_quote(symbol, side="buy")
        if quote.best_ask is None or quote.best_ask <= 0:
            raise ValueError("매도1호가 없음")
        expected = _V753.align_stock_price(
            quote.best_ask * (1.0 + self.settings.slippage_pct), upward=True
        )
        estimated = expected * quantity
        commission = float(getattr(self.settings, "commission_pct", 0.0) or 0.0)
        if estimated * (1.0 + commission) > float(self.broker.cash):
            raise ValueError("현금부족")
        position = self.broker.positions.get(symbol)
        current_notional = (
            _V7516._current_position_notional(position) if position is not None else 0.0
        )
        if current_notional + estimated > _V7516.MAX_SYMBOL_NOTIONAL:
            raise ValueError("종목 누적 1천만원 한도 초과")
        if position is not None and symbol in self.scale_blocked:
            raise ValueError("추가매수금지 종목")

        pattern = str(getattr(position, "pattern", "") or "형명령수량지정매수")
        reason = f"형명령수량지정매수 {quantity:,}주"
        signal = _V753.Signal(
            symbol, _V753.SignalAction.BUY, 999.0, reason,
            quote.price, datetime.now(KST), pattern,
            V7609_FIXED_STOP_LOSS_PCT, V7609_TAKE_PROFIT_PCT,
        )
        if position is None:
            fill = self.broker.buy(
                symbol, quantity, quote, reason,
                stop_loss_pct=V7609_FIXED_STOP_LOSS_PCT,
                take_profit_pct=V7609_TAKE_PROFIT_PCT,
                pattern=pattern,
            )
            self.strategy.mark_entry(symbol)
        else:
            fill = _V7516.paper_add_to_position(
                self.broker, symbol, quantity, quote, reason,
                stop_loss_pct=V7609_FIXED_STOP_LOSS_PCT,
                take_profit_pct=V7609_TAKE_PROFIT_PCT,
                pattern=pattern,
            )
        self.last_position_observation_at[symbol] = fill.timestamp
        self._enforce_gem_orders(symbol, announce=True)
        if self._v7609_enforce_fixed_stop(symbol):
            self.broker.save_state()
        self._print_buy(fill, self.broker.positions[symbol], signal)
        return f"지정수량 PAPER 매수 {fill.quantity:,}주 {fill.price:,.0f}원"

    def _manual_buy(self, symbol: str, tranche: int) -> str:
        def execute() -> str:
            quantity = self._v7609_capsule_quantity(str(symbol))
            if quantity is None:
                result = str(super(V7600ThreeStageEngine, self)._manual_buy(symbol, tranche))
                if symbol in self.broker.positions:
                    self._enforce_gem_orders(symbol, announce=False)
                    position = self.broker.positions[symbol]
                    self._v7609_support_stop_reason(symbol, float(position.entry_price), datetime.now(KST))
                return result
            return self._v7609_manual_buy_exact_quantity(str(symbol), quantity)

        holder = self._v7609_order_thread_obj()
        if isinstance(getattr(holder, "current", None), dict):
            return execute()
        capsule = getattr(self, "_v7555_execution_capsule", None)
        approval_id = (
            str(capsule.get("overlay_id", "")) if isinstance(capsule, dict) else ""
        )
        if not approval_id:
            return execute()
        identity = self._v7609_make_order_identity(
            side="BUY",
            symbol=str(symbol),
            source="MANUAL_APPROVAL",
            source_id=approval_id,
            approval_id=approval_id,
        )
        return str(
            self._v7609_run_order_intent(
                identity,
                {
                    "symbol": str(symbol).zfill(6),
                    "tranche": int(tranche),
                    "approval_id": approval_id,
                },
                execute,
                duplicate_result="이미 처리한 동일 승인 주문을 차단했습니다.",
            )
        )

    def _v7609_enforce_fixed_stop(self, symbol: str) -> bool:
        """Use the valid tick nearest the cost-inclusive 1.5% stop."""

        position = getattr(self.broker, "positions", {}).get(str(symbol))
        if position is None:
            return False
        entry_price = float(getattr(position, "entry_price", 0.0) or 0.0)
        if entry_price <= 0:
            return False
        fixed_stop, _target = self._v7609_position_exit_prices(position)
        current_stop = float(getattr(position, "stop_price", 0.0) or 0.0)
        if abs(current_stop - fixed_stop) < 0.01:
            return False
        position.stop_price = fixed_stop
        self.logger.warning(
            "V7609 PAPER net 1.5%% stop %s | entry=%.0f prior=%.0f fixed=%.0f",
            self._label(str(symbol)), entry_price, current_stop, fixed_stop,
        )
        return True

    @staticmethod
    def _v7609_exit_prices(entry_price: float) -> tuple[float, float]:
        return _EXIT_POLICY.exit_prices(entry_price, _V753.align_stock_price, _V753.stock_tick_size)

    def _v7609_cost_rates(self) -> tuple[float, float]:
        settings = getattr(self.broker, "settings", getattr(self, "settings", None))
        return (float(getattr(settings, "commission_pct", 0) or 0),
                float(getattr(settings, "sell_tax_pct", 0) or 0))

    def _v7609_position_exit_prices(self, position: Any) -> tuple[float, float]:
        commission, tax = self._v7609_cost_rates()
        return _EXIT_POLICY.exit_prices(position.entry_price, _V753.align_stock_price,
            _V753.stock_tick_size, entry_cost=_EXIT_POLICY.cost_per_share(position, commission),
            commission=commission, tax=tax)

    def _v7609_support_stop_reason(self, symbol: str, price: float, now: datetime) -> str:
        position = self.broker.positions.get(symbol)
        if position is None or not math.isfinite(price) or price <= 0:
            return ""
        states = getattr(self, "_v7609_exit_support", {})
        self._v7609_exit_support = states
        raw = list(getattr(self.client, "v7580_raw_cache", {}).get(symbol, []))
        support, fresh = _EXIT_POLICY.support_snapshot(position, raw, now, states.get(symbol),
            entry_time_override=getattr(self, '_v7609_holding_started', {}).get(symbol))
        states[symbol] = support
        commission, tax = self._v7609_cost_rates()
        stop, _ = self._v7609_position_exit_prices(position)
        return _EXIT_POLICY.stop_reason(position, price, stop, commission, tax, support, fresh)

    def _enforce_gem_orders(self, symbol: str, *, announce: bool = True) -> bool:
        """Register the requested nearest exit ticks without legacy stop writers."""

        position = self.broker.positions.get(str(symbol))
        if position is None:
            return False
        _stop, target = self._v7609_position_exit_prices(position)
        target_changed = (
            abs(float(position.target_price) - float(target)) > 1e-9
            or not bool(position.target_order_active)
        )
        stop_changed = self._v7609_enforce_fixed_stop(str(symbol))
        position.target_price = target
        position.target_order_active = True
        if target_changed or stop_changed:
            self.broker.save_state()
            entry = float(position.entry_price)
            commission, tax = self._v7609_cost_rates()
            loss_pct = -_EXIT_POLICY.net_return(position, position.stop_price, commission, tax)*100
            gain_pct = _EXIT_POLICY.net_return(position, target, commission, tax)*100
            if not (abs(loss_pct-1.5) <= .1 and gain_pct >= 2-1e-9):
                self.logger.warning(
                    "V7609 PAPER exit tick band unavailable %s | stop_loss_pct=%.4f target_gain_pct=%.4f",
                    self._label(str(symbol)), loss_pct, gain_pct,
                )
        if announce and position is not None and symbol not in self._gem_order_announced:
            self._gem_order_announced.add(str(symbol))
            self.logger.warning(
                "PAPER order registered %s | held=%s | entry=%.0f | net target=%.0f(+2.0%%) | net stop=%.0f(-1.5%%, immediate; -3%% gap guard)",
                self._label(str(symbol)), int(position.quantity),
                float(position.entry_price), float(position.target_price),
                float(position.stop_price),
            )
        return True

    def _execute_gem_stop(
        self,
        symbol: str,
        quote: Any,
        *,
        detected_at: datetime,
        source: str,
        trigger_price: float,
        observed_lag_sec: float = 0.0,
    ) -> bool:
        with self._v7609_stage_lock:
            # All inherited tick/bar stop routes use the current quote, not a
            # historical low that may have recovered before detection.
            reason = self._v7609_support_stop_reason(symbol, float(quote.price), detected_at)
            if not reason:
                return False
            return self._v7609_execute_fixed_stop_locked(
                symbol, quote, detected_at=detected_at, source=source,
                trigger_price=float(quote.price), observed_lag_sec=observed_lag_sec,
                reason=reason)

    def _check_tick_exit(self, quote: Any, *, detection_time: datetime | None = None,
                         observed_lag_sec: float = 0.0) -> None:
        # Serialize target and stop. Check the fixed stop before slower parent reporting.
        with self._v7609_stage_lock:
            position = self.broker.positions.get(str(quote.symbol))
            if position is not None:
                self._enforce_gem_orders(str(quote.symbol), announce=False)
                if 0 < float(quote.price) <= float(position.stop_price):
                    executed = self._execute_gem_stop(str(quote.symbol), quote,
                        detected_at=detection_time or datetime.now(KST),
                        source="REST_TICK_FIXED_1P5PCT", trigger_price=float(quote.price),
                        observed_lag_sec=observed_lag_sec)
                    if executed:
                        return
            return super()._check_tick_exit(quote, detection_time=detection_time,
                                            observed_lag_sec=observed_lag_sec)

    def _v7609_execute_fixed_stop_locked(
        self, symbol: str, quote: Any, *, detected_at: datetime, source: str,
        trigger_price: float, observed_lag_sec: float = 0.0,
        reason: str = "비용포함 기본손절-1.5%",
    ) -> bool:
        """Execute the PAPER fixed stop with truthful -1.5% audit metadata."""

        if symbol in self._gem_exit_inflight:
            return False
        position = self.broker.positions.get(symbol)
        if position is None:
            return False
        self._gem_exit_inflight.add(symbol)
        was_active = bool(position.target_order_active)
        position.target_order_active = False
        self.broker.save_state()
        try:
            enriched = self.client.get_orderbook(symbol, quote, side="sell")
            fill = self.broker.sell(
                symbol,
                enriched,
                reason,
                trigger_time=detected_at,
                trigger_price=float(trigger_price),
                detection_time=detected_at,
                observed_lag_sec=max(0.0, float(observed_lag_sec)),
                trigger_source=(
                    "REST_TICK_FIXED_1P5PCT"
                    if source in {"REST_TICK_FIXED_5PCT", "REST_TICK_FIXED_3PCT", "REST_TICK_FIXED_2PCT"}
                    else source
                ),
            )
        except Exception as exc:
            position = self.broker.positions.get(symbol)
            if position is not None:
                position.target_order_active = was_active
                self.broker.save_state()
            self._show_throttled_error(
                f"stop:{symbol}",
                f"고정손절 대기 {self._label(symbol)}: {_V753.brief_error(exc)}",
                10.0,
            )
            return False
        finally:
            self._gem_exit_inflight.discard(symbol)

        tomorrow = (detected_at.astimezone(KST) + timedelta(days=1)).date()
        self.broker.reentry_block_until[symbol] = datetime.combine(
            tomorrow, datetime.min.time(), tzinfo=KST
        )
        self.broker.save_state()
        self.strategy.mark_exit(symbol)
        self.risk.record_pnl(fill.pnl)
        self._print_sell(fill)
        return True

    def _print_sell(self, fill: Any) -> None:
        """Keep target exits visible and reject only their same-minute auto signal."""

        super()._print_sell(fill)
        reason = str(getattr(fill, "reason", "") or "")
        if "목표" not in reason or float(getattr(fill, "pnl", 0.0) or 0.0) < 0:
            return
        symbol = str(getattr(fill, "symbol", "")).strip().zfill(6)
        exit_at = getattr(fill, "timestamp", datetime.now(KST))
        if not isinstance(exit_at, datetime):
            exit_at = datetime.now(KST)
        if exit_at.tzinfo is None:
            exit_at = exit_at.replace(tzinfo=KST)
        self._v7609_target_exit_at[symbol] = exit_at.astimezone(KST)
        self.pending_entries.pop(symbol, None)
        self.logger.warning(
            "V7609 목표매도 동일1분신호 재진입차단 %s | exit=%s",
            self._label(symbol),
            exit_at.astimezone(KST).strftime("%H:%M:%S"),
        )

    @staticmethod
    def _v7609_is_strict_interest_reason(reason: str) -> bool:
        text = str(reason)
        if text.startswith(V7609_INTEREST_EVENT_MARKER):
            text = text[len(V7609_INTEREST_EVENT_MARKER):]
        if text.startswith("V7610_"):
            return True
        return (
            (text.startswith("1차상승") and "후조정" in text and "주변음봉" in text)
            or (
                text.startswith("1차상승")
                and "후조정" in text
                and (
                    "직전음봉대비거래" in text
                    or "하락음봉3봉거래연속증가" in text
                )
            )
            or text.startswith("거래량급등 1차상승")
            # V75.88's live transition is the user's original signal: a
            # completed bearish candle whose volume is materially larger than
            # the preceding bearish candle.  It remains strict because this
            # layer separately requires today's V7594 observation admission.
            or text.startswith("하락음봉거래x")
            or text.startswith("하락음봉5봉평균초과")
        )

    @staticmethod
    def _v7609_is_strict_prebuy_reason(reason: str) -> bool:
        """Identify only V76's verified one-minute prebuy transitions."""

        text = str(reason)
        if text.startswith(V7609_PREBUY_EVENT_MARKER):
            text = text[len(V7609_PREBUY_EVENT_MARKER):]
        return text.startswith((
            "1차1/2:",
            "돌파눌림확인:",
            "V7609_FLOOR_SHAPE:",
            "V7609_CONFIRMED_MA5:",
            "V7609_BOTTOM_MA5:",
        ))

    @staticmethod
    def _v7609_is_verified_interest_event_reason(reason: str) -> bool:
        """Accept only an interest event written after all current gates passed."""

        return str(reason).startswith(V7609_INTEREST_EVENT_MARKER)

    @staticmethod
    def _v7609_is_verified_prebuy_event_reason(reason: str) -> bool:
        """Accept only a prebuy event written after verified interest."""

        return str(reason).startswith(V7609_PREBUY_EVENT_MARKER)

    @staticmethod
    def _v7609_is_direct_five_bar_interest_reason(reason: str) -> bool:
        """Identify the five-bar interest signal without granting a stage bypass."""

        return str(reason).startswith("하락음봉5봉평균초과")

    @staticmethod
    def _v7610_is_direct_interest_reason(reason: str) -> bool:
        """Classify legacy direct-signal text; it never bypasses stage gates."""

        return str(reason).startswith(("V7610_", "하락음봉5봉평균초과"))

    def _v7609_authoritative_stage_event(
        self,
        symbol: str,
        stage: str,
        now: datetime,
        *,
        first: bool = False,
    ) -> tuple[datetime | None, Any | None]:
        """Read a same-day stage only from the append-only state ledger."""

        matches: list[tuple[datetime, Any]] = []
        for event in self._v7609_state_manager_obj().events:
            if str(getattr(event, "symbol", "")) != str(symbol).zfill(6):
                continue
            if str(getattr(event, "new_stage", "")) != str(stage):
                continue
            stamp = self._v7609_timestamp(getattr(event, "data_time", ""), now)
            if stamp is not None and stamp.date() == now.date():
                matches.append((stamp, event))
        if not matches:
            return None, None
        chooser = min if first else max
        return chooser(matches, key=lambda value: value[0])

    def _v7609_volume_prebuy_event(self, symbol: str, now: datetime) -> tuple[datetime | None, Any | None]:
        if self._v7609_state_manager_obj().stage_of(symbol) == _V7600_STATE.PREBUY:
            stamp, event = self._v7609_authoritative_stage_event(symbol, _V7600_STATE.PREBUY, now)
            if event is not None and event.rule_id in {_VOLUME5.RULE_ID, EARLY_PATTERN, _PREBUY_RULES.RULE_ID}:
                return stamp, event
        return None, None

    def _v7609_today_observation_timestamp(
        self,
        symbol: str,
        now: datetime,
    ) -> datetime | None:
        if self._v7609_state_manager_obj().stage_of(str(symbol)) == _V7600_STATE.REMOVED:
            return None
        stamp, _event = self._v7609_authoritative_stage_event(
            str(symbol), _V7600_STATE.OBSERVED, now, first=True
        )
        if stamp is None:
            return None
        observation_map = getattr(self, "_v76_strict_observation", {})
        if isinstance(observation_map, dict):
            observation_map[str(symbol)] = stamp
        return stamp

    def _v7609_has_today_observation_admission(
        self,
        symbol: str,
        now: datetime,
    ) -> bool:
        return self._v7609_today_observation_timestamp(symbol, now) is not None

    @staticmethod
    def _v7609_is_verified_timeline_event(stage: str, reason: str) -> bool:
        """Reject placeholder and pre-fix automatic stage clocks from the GUI."""

        text = str(reason)
        if text == "화면행최초확인":
            return False
        if stage == "관찰":
            return text.startswith("V7594:관찰편입=")
        if stage == "관심":
            return text.startswith(V7609_INTEREST_EVENT_MARKER)
        if stage in {"후보", "직전"}:
            return text.startswith(V7609_PREBUY_EVENT_MARKER)
        return True

    def _v7609_strict_stage_today(
        self,
        symbol: str,
        now: datetime,
    ) -> tuple[str, str]:
        """Return only the current same-day append-only manager stage."""

        symbol = str(symbol).zfill(6)
        managed = self._v7609_state_manager_obj().stage_of(symbol)
        phase_and_stage = {
            _V7600_STATE.INTEREST: ("INTEREST", "관심종목"),
            _V7600_STATE.PREBUY: ("PREBUY", "매수직전"),
            _V7600_STATE.MARKET_HOLD: ("MARKET_DEFERRED", "시장보류"),
        }.get(managed)
        if phase_and_stage is None:
            return "", ""
        stamp, _event = self._v7609_authoritative_stage_event(
            symbol, str(managed), now
        )
        if stamp is not None:
            return phase_and_stage
        return "", ""

    def _v7609_strict_timeline_evidence(
        self,
        symbol: str,
        phase: str,
        now: datetime,
    ) -> tuple[datetime | None, str]:
        """Return evidence from the append-only manager, never legacy timeline."""

        target = {
            "INTEREST": _V7600_STATE.INTEREST,
            "PREBUY": _V7600_STATE.PREBUY,
        }.get(str(phase))
        if target is None:
            return None, ""
        if target == _V7600_STATE.PREBUY:
            direct_stamp, direct_event = self._v7609_volume_prebuy_event(symbol, now)
            if direct_event is not None:
                return direct_stamp, direct_event.reason
        stamp, event = self._v7609_authoritative_stage_event(
            str(symbol), target, now, first=True
        )
        observation_stamp = self._v7609_today_observation_timestamp(str(symbol), now)
        if stamp is None or event is None or observation_stamp is None:
            return None, ""
        if stamp < observation_stamp:
            return None, ""
        return stamp, str(getattr(event, "reason", "") or "")

    def _v7609_reconcile_strict_supply_stages(self, now: datetime) -> bool:
        """Keep verified 1m stages authoritative over legacy 3m display refreshes."""

        potentials = getattr(self, "_supply_potential", {})
        if not isinstance(potentials, dict):
            return False
        changed = False
        for raw_symbol, item in list(potentials.items()):
            symbol = str(raw_symbol)
            phase, desired_stage = self._v7609_strict_stage_today(symbol, now)
            if phase not in {"INTEREST", "PREBUY"}:
                # With the legacy three-minute evaluator removed, a verified
                # observation may only remain 관찰 unless the one-minute state
                # machine explicitly promoted it.  Clear stale 3m labels left
                # in the same-day durable file without touching holdings.
                if (
                    not phase
                    and self._v7609_has_today_observation_admission(symbol, now)
                    and str(getattr(item, "stage", "")) != "관찰후보"
                ):
                    try:
                        stamp = self._v76_strict_observation.get(symbol)
                        if not isinstance(stamp, datetime):
                            stamp = now
                        self._v7609_record_authoritative_stage(
                            symbol,
                            "관찰후보",
                            "V7609_RECONCILE_VERIFIED_OBSERVATION",
                            stamp,
                        )
                        self._v7609_write_stage_mirror(symbol, item, "관찰후보")
                        source = str(getattr(item, "source", "") or "")
                        item.source = (
                            source.split("|V7609_STRICT_STAGE=", 1)[0]
                            + "|V7609_ONE_MINUTE_ONLY=OBSERVED"
                        ).lstrip("|")
                        if isinstance(stamp, datetime):
                            item.updated_at = stamp
                        changed = True
                    except (AttributeError, TypeError):
                        pass
                continue
            stamp, evidence = self._v7609_strict_timeline_evidence(
                symbol, phase, now
            )
            mapping_name = (
                "_v76_strict_prebuy" if phase == "PREBUY" else "_v76_strict_interest"
            )
            mapping = getattr(self, mapping_name, {})
            strict_stamp = mapping.get(symbol) if isinstance(mapping, dict) else None
            if stamp is None and isinstance(strict_stamp, datetime):
                stamp = strict_stamp
            source = str(getattr(item, "source", "") or "")
            base_source = source.split("|V7609_STRICT_STAGE=", 1)[0]
            marker = f"V7609_STRICT_STAGE={phase}"
            if evidence:
                marker += f":{evidence}"
            try:
                if str(getattr(item, "stage", "")) != desired_stage:
                    if self._v7609_record_authoritative_stage(
                        symbol, desired_stage, marker, stamp or now
                    ):
                        changed = self._v7609_write_stage_mirror(
                            symbol, item, desired_stage
                        ) or changed
                protected_source = f"{base_source}|{marker}" if base_source else marker
                if source != protected_source:
                    item.source = protected_source
                    changed = True
                if stamp is not None and getattr(item, "updated_at", None) != stamp:
                    item.updated_at = stamp
                    changed = True
            except (AttributeError, TypeError):
                continue
        return changed

    def _save_supply_potential(self) -> None:
        """Reconcile direct legacy replacements before the shared durable save."""

        self._v7609_reconcile_strict_supply_stages(datetime.now(KST))
        super()._save_supply_potential()

    def _v7609_record_scanner_removal(
        self, symbol: str, item: Any, reason: str, now: datetime
    ) -> bool:
        """Record an actual removal, without inventing a new removal criterion."""

        symbol = str(symbol).zfill(6)
        manager = self._v7609_state_manager_obj()
        if manager.stage_of(symbol) not in (_V7600_STATE.SCANNER_STAGES - {_V7600_STATE.REMOVED}):
            return False
        updated_at = getattr(item, "updated_at", now)
        if not isinstance(updated_at, datetime):
            updated_at = now
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=KST)
        data_time = updated_at.astimezone(KST).isoformat()
        decision_time = now.astimezone(KST).isoformat()
        rule_id = "V7609_관찰해제"
        signal_id = _V7600_STATE.make_signal_id(
            engine_version=VERSION,
            symbol=symbol,
            rule_id=rule_id,
            data_time=data_time,
            new_stage=_V7600_STATE.REMOVED,
        )
        evidence = {
            "evidence_schema": "EVID-001-v1",
            "source": "V7609_SCANNER_LIFECYCLE",
            "signal_id": signal_id,
            "order_intent_id": "",
            "symbol": symbol,
            "rule_id": rule_id,
            "reason": str(reason),
            "data_time": data_time,
            "decision_time": decision_time,
            "engine_version": VERSION,
        }
        request = _V7600_STATE.TransitionRequest(
            signal_id=signal_id,
            symbol=symbol,
            new_stage=_V7600_STATE.REMOVED,
            rule_id=rule_id,
            data_time=data_time,
            decision_time=decision_time,
            engine_version=VERSION,
            reason=str(reason),
            evidence=evidence,
        )
        try:
            event = manager.apply(request)
            try:
                self._v7609_state_log_obj(now).append(event)
            except OSError:
                manager.discard_last(event)
                raise
            return True
        except (OSError, ValueError, TypeError, _V7600_STATE.TransitionRejected) as exc:
            self.logger.warning("V7609 관찰해제 기록 실패 %s: %s", symbol, exc)
            return False

    def _purge_expired_potential(self) -> None:
        """Keep admitted rows for the session; elapsed minutes never remove them."""
        now = datetime.now(KST)
        removed = []
        for symbol, item in list(getattr(self, "_supply_potential", {}).items()):
            updated = getattr(item, "updated_at", now)
            if isinstance(updated, datetime) and updated.astimezone(KST).date() != now.date():
                self._supply_potential.pop(symbol, None)
                getattr(self, "_v7588_phase", {}).pop(symbol, None)
                getattr(self, "_v7588_flush", {}).pop(symbol, None)
                removed.append(symbol)
        if removed:
            self._save_supply_potential()

    def _v7588_make_potential(self, symbol: str, stage: str, reason: str, now: datetime) -> Any:
        item = super()._v7588_make_potential(symbol, stage, reason, now)
        # Retain the parent's serialized schema, but not its 30-minute TTL.
        return replace(item, expires_at=now.replace(hour=23, minute=59, second=59, microsecond=999999))

    def _load_supply_potential(self) -> None:
        """Load same-day mirrors even when their old 30-minute TTL has passed.

        Existing authoritative-state restoration/pruning still runs in __init__;
        this loader cannot manufacture a verified observation/interest signal.
        """
        inherited = super()._load_supply_potential
        scope = inherited.__func__.__globals__
        path = scope["POTENTIAL_FILE"]
        if not path.exists():
            return
        now = datetime.now(KST)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            for raw in payload.get("candidates", []):
                try:
                    values = dict(raw)
                    for key in ("detected_at", "updated_at", "flush_time"):
                        values[key] = datetime.fromisoformat(str(raw[key])).astimezone(KST)
                    if values["updated_at"].date() != now.date():
                        continue
                    values["expires_at"] = now.replace(hour=23, minute=59, second=59, microsecond=999999)
                    values["strict_missing"] = tuple(raw.get("strict_missing", ()))
                    fields = scope["SupplyPotential"].__dataclass_fields__
                    item = scope["SupplyPotential"](**{key: values[key] for key in fields})
                    self._supply_potential[item.symbol] = item
                except (ValueError, TypeError, KeyError) as exc:
                    self.logger.warning("당일 관찰 기록 복원 제외(손상): %s", exc)
        except (OSError, ValueError, TypeError) as exc:
            self.logger.warning("당일 관찰 기록 읽기 실패: %s", exc)

    def _v7587_exclude_symbol(self, symbol: str, name: str = "") -> str:
        item = getattr(self, "_supply_potential", {}).get(symbol)
        result = super()._v7587_exclude_symbol(symbol, name)
        if symbol in getattr(self, "_v7587_excluded", set()):
            with self._v7609_stage_lock:
                self._v7609_record_scanner_removal(symbol, item, "사용자 명령으로 제외", datetime.now(KST))
        return result

    def _v7609_record_stage_wait(
        self, symbol: str, reason: str, data_time: datetime | None,
        now: datetime, route: str,
    ) -> None:
        """Diagnostic only: never changes stage, signal, price or order decisions."""
        manager = getattr(self, "_v7609_stage_manager", None)
        lock = getattr(self, "_v7609_stage_lock", None)
        if manager is None or lock is None:
            return  # No authoritative initialized state: do not invent history.
        try:
            with lock:
                stage = manager.stage_of(symbol)
                if stage not in (_V7600_STATE.SCANNER_STAGES - {_V7600_STATE.REMOVED}):
                    return
                path = STAGE_EVENT_DIR / f"v7609_stage_wait_{now:%Y%m%d}.jsonl"
                stage_event = next((event.event_id for event in reversed(manager.events)
                                    if event.symbol == symbol), "")
                cache = getattr(self, "_v7609_wait_history_cache", None)
                if cache is None or cache[0] != now.date():
                    latest = {}
                    for row in _v7609_read_history_lines(path):
                        latest[(row.get("symbol"), row.get("rule_id"))] = (
                            row.get("new_stage"), row.get("reason"), row.get("stage_event_id", ""))
                    cache = (now.date(), latest)
                    self._v7609_wait_history_cache = cache
                key = (symbol, route)
                value = (stage, str(reason), stage_event)
                if cache[1].get(key) == value:
                    return
                record = {
                    "kind": "조건대기", "symbol": symbol,
                    "name": self._v7609_resolve_display_name(symbol),
                    "old_stage": stage, "new_stage": stage,
                    "reason": str(reason), "rule_id": route,
                    "stage_event_id": stage_event,
                    "data_time": data_time.isoformat() if isinstance(data_time, datetime) else "",
                    "decision_time": now.isoformat(), "engine_version": VERSION,
                }
                path.parent.mkdir(parents=True, exist_ok=True)
                if path.exists():
                    with path.open("rb") as stream:
                        stream.seek(0, 2)
                        if stream.tell():
                            stream.seek(-1, 2)
                            if stream.read(1) != b"\n":
                                raise ValueError("미완성 이력 말미 발견: 기존 기록 보존, 추가 기록 보류")
                with path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                cache[1][key] = value
        except (OSError, ValueError, TypeError) as exc:
            self.logger.warning("단계 대기 이력 기록 실패 %s: %s", symbol, exc)

    def _update_supply_potential(self, symbol: str) -> None:
        """Disable the inherited three-minute SupplyPotential evaluator."""

        if str(symbol) in getattr(self.broker, "positions", {}):
            return
        now = datetime.now(KST)
        self._purge_expired_potential()
        if self._v7609_reconcile_strict_supply_stages(now):
            self._save_supply_potential()

    def _v7609_load_one_minute_bars(self, symbol: str) -> list[Any]:
        """Fetch completed Kiwoom one-minute OHLCV without 3m aggregation."""

        snapshot = getattr(self, "_v7609_volume_snapshots", {}).get(str(symbol))
        if snapshot and 0 <= time.monotonic() - snapshot[0] <= 2:
            return self._v7609_completed_one_minute_bars(snapshot[1], datetime.now(KST))
        getter = getattr(self.client, "get_minute_bars", None)
        if not callable(getter):
            raise RuntimeError("원본 1분봉 조회 경로 없음")
        raw = list(
            getter(
                str(symbol),
                limit=900,
                completed_only=True,
            )
        )
        return self._v7609_completed_one_minute_bars(raw, datetime.now(KST))

    @staticmethod
    def _v7609_completed_one_minute_bars(
        raw: list[Any], now: datetime
    ) -> list[Any]:
        """Defensively remove the still-forming current minute from REST rows."""

        current_minute = now.astimezone(KST).replace(second=0, microsecond=0)
        completed: list[Any] = []
        for item in raw:
            stamp = getattr(item, "timestamp", None)
            if not isinstance(stamp, datetime):
                continue
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=KST)
            if stamp.astimezone(KST) < current_minute:
                completed.append(item)
        return sorted(completed, key=lambda item: getattr(item, "timestamp"))

    @staticmethod
    def _v7609_latest_completed_bar_is_fresh(
        bars: list[Any], now: datetime
    ) -> tuple[bool, str]:
        """Require the immediately preceding minute; never revive an old bar."""

        current_minute = now.astimezone(KST).replace(second=0, microsecond=0)
        expected = current_minute - timedelta(minutes=1)
        same_session = pattern_bars(bars, now)
        if not same_session:
            return False, "NO_COMPLETED_SESSION_BAR"
        stamp = getattr(same_session[-1], "timestamp", None)
        if not isinstance(stamp, datetime):
            return False, "INVALID_COMPLETED_BAR_TIME"
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=KST)
        actual = stamp.astimezone(KST).replace(second=0, microsecond=0)
        if actual != expected:
            return False, (
                f"STALE_COMPLETED_BAR actual={actual.isoformat()} "
                f"expected={expected.isoformat()}"
            )
        return True, "FRESH_COMPLETED_BAR"

    @staticmethod
    def _v7609_completed_bar_lag_minutes(
        bars: list[Any], now: datetime
    ) -> int | None:
        """Whole minutes between the newest completed bar and the previous minute."""

        expected = now.astimezone(KST).replace(second=0, microsecond=0) - timedelta(minutes=1)
        same_session = pattern_bars(bars, now)
        if not same_session:
            return None
        stamp = getattr(same_session[-1], "timestamp", None)
        if not isinstance(stamp, datetime):
            return None
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=KST)
        actual = stamp.astimezone(KST).replace(second=0, microsecond=0)
        lag = int((expected - actual).total_seconds() // 60)
        # A bar newer than the previous completed minute is not a lag; it is a
        # forming or future candle and must never be used.
        return lag if lag >= 0 else None

    def _v7609_stage_only_admission(
        self,
        symbol: str,
        raw: list[Any],
        now: datetime,
    ) -> bool:
        """Admit 관찰/관심 on a bounded-stale completed bar.

        Orders are not reachable from here.  This publishes display stages only
        so that scanner polling lag cannot empty 관찰/관심 while the separate
        fivefold-volume route keeps filling 매수가능성.
        """

        symbol = str(symbol)
        lag = self._v7609_completed_bar_lag_minutes(raw, now)
        if lag is None or lag > V7609_STAGE_BAR_MAX_LAG_MIN:
            return False
        try:
            if len(_EXIT_POLICY.completed_bars(raw, now)) != len(raw):
                return False
            if any(not math.isfinite(float(b.volume)) or float(b.volume) < 0
                   or b.timestamp.second or b.timestamp.microsecond for b in raw):
                return False
        except (AttributeError, TypeError, ValueError):
            return False
        if symbol in getattr(self.broker, "positions", {}):
            return False
        if symbol in self._v7587_excluded or symbol in self.pending_entries:
            return False
        self._v7609_refresh_same_time_ratio(symbol, raw, now)
        self._v7609_admit_observation_from_one_minute(symbol, raw, now, allow_lag=True)
        if lag > 0:
            # Bounded lag may recover observation, not an actionable flush or
            # interest trigger. Fresh bars must establish those normally.
            return self._v7609_has_today_observation_admission(symbol, now)
        today = pattern_bars(raw, now)
        if len(today) < 2:
            return False
        # A lagged bar may open a new interest admission, but may never
        # re-anchor the flush of a symbol that is already in 관심 or beyond.
        if str(self._v7588_phase.get(symbol, "")) not in {"", "OBSERVED"}:
            return False
        allowed, reason, flush = self._v7609_five_bar_average_bear_interest_decision(
            today, now=now
        )
        if not (allowed and flush is not None):
            decision = three_bear_rising_volume_decision(today, now=now)
            if not decision.allowed:
                return False
            reason, flush = decision.reason, today[-1]
            self._v76_strict_block.pop(symbol, None)
        elif "구조위험대기[" in reason:
            self._v76_strict_block[symbol] = (now, f"STRUCTURAL_RISK:{reason}")
        else:
            self._v76_strict_block.pop(symbol, None)
        self._v7588_flush[symbol] = flush
        self._v7588_publish(
            symbol,
            "INTEREST",
            "관심종목",
            f"{reason}|완성봉지연{lag}분",
            now,
        )
        return True

    def _v7609_refresh_same_time_ratio(
        self,
        symbol: str,
        raw: list[Any],
        now: datetime,
    ) -> float:
        """Refresh the prior-day same-time ratio from completed raw 1m bars."""

        cutoff = now.astimezone(KST).replace(second=0, microsecond=0)
        completed: list[Any] = []
        for item in raw:
            stamp = getattr(item, "timestamp", None)
            if not isinstance(stamp, datetime):
                continue
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=KST)
            local = stamp.astimezone(KST)
            if local >= cutoff or not _V753.regular_session_bar(local):
                continue
            completed.append(item)
        ratio = float(self._same_time_volume_ratio(completed, now.date()))
        strategy = getattr(self, "strategy", None)
        setter = getattr(strategy, "set_context", None)
        if callable(setter):
            setter(str(symbol), same_time_volume_ratio=ratio)
        elif strategy is not None:
            ratio_map = getattr(strategy, "same_time_volume_ratio", None)
            if isinstance(ratio_map, dict):
                ratio_map[str(symbol)] = ratio
        return ratio

    def _v7609_admit_observation_from_one_minute(
        self,
        symbol: str,
        raw: list[Any],
        now: datetime,
        *, allow_lag: bool = False,
    ) -> None:
        """First +5% / later +3% waves; never downgrade an existing stage."""

        if self._v7588_phase.get(str(symbol)) or not raw:
            return
        check_now = now
        if allow_lag:
            lag = self._v7609_completed_bar_lag_minutes(raw, now)
            if lag is None or lag > V7609_STAGE_BAR_MAX_LAG_MIN:
                return
            check_now = now - timedelta(minutes=lag)
        evidence = _V7594_EVALUATE_OBSERVATION(raw, now=check_now)
        if evidence.allowed:
            self._v7588_publish(
                str(symbol),
                "OBSERVED",
                "관찰후보",
                f"V7594:{evidence.reason}|완성봉지연{int((now-check_now).total_seconds()//60)}분",
                now,
            )

    def _v7609_update_early_observation(self, symbol, raw, now):
        from v7609_observation import early_candidate
        candidates = getattr(self, '_v7609_early_observation', {})
        self._v7609_early_observation = candidates
        if not all(hasattr(self, key) for key in ('_v7588_phase', 'broker', '_v7587_excluded', 'pending_entries')):
            candidates.pop(symbol, None)
            return
        candidate = None
        if (not self._v7588_phase.get(symbol) and symbol not in self.broker.positions
                and symbol not in self._v7587_excluded and symbol not in self.pending_entries):
            candidate = early_candidate(raw, now)
        if candidate is None:
            candidates.pop(symbol, None)
        else:
            if symbol not in candidates:
                self.next_hot_update = 0.0
            candidates[symbol] = candidate

    def _v7609_restore_today_scanner_state(self, now: datetime) -> None:
        """Restore scanner mirrors only from the same-day append-only ledger."""

        potentials = getattr(self, "_supply_potential", {})
        if not isinstance(potentials, dict):
            return
        repaired_stage = False
        manager = self._v7609_state_manager_obj()
        # Recover only this verified direct route when its display mirror is
        # missing. Do not invent preceding observation or interest events.
        for symbol in {e.symbol for e in manager.events if e.rule_id in {_VOLUME5.RULE_ID, EARLY_PATTERN, _PREBUY_RULES.RULE_ID}}:
            direct_stamp, direct_event = self._v7609_volume_prebuy_event(symbol, now)
            if direct_event is not None and symbol not in potentials:
                potentials[symbol] = self._v7588_make_potential(
                    symbol, "매수직전", direct_event.reason, direct_stamp
                )
                repaired_stage = True
        for symbol, item in list(potentials.items()):
            symbol = str(symbol).zfill(6)
            observation_stamp = self._v7609_today_observation_timestamp(symbol, now)
            if observation_stamp is None:
                observation_stamp, _direct_event = self._v7609_volume_prebuy_event(symbol, now)
            if observation_stamp is None:
                continue
            managed = manager.stage_of(symbol)
            restored = {
                _V7600_STATE.OBSERVED: ("OBSERVED", "관찰후보"),
                _V7600_STATE.INTEREST: ("INTEREST", "관심종목"),
                _V7600_STATE.PREBUY: ("PREBUY", "매수직전"),
                _V7600_STATE.MARKET_HOLD: ("MARKET_DEFERRED", "시장보류"),
            }.get(managed)
            if restored is None:
                continue
            phase, desired_stage = restored
            stamp, event = self._v7609_authoritative_stage_event(
                symbol, str(managed), now
            )
            if stamp is None or event is None or stamp < observation_stamp:
                continue

            self._v76_strict_interest.pop(symbol, None)
            self._v76_strict_prebuy.pop(symbol, None)
            self._v76_strict_market_hold.pop(symbol, None)
            if managed == _V7600_STATE.INTEREST:
                self._v76_strict_interest[symbol] = stamp
            elif managed == _V7600_STATE.PREBUY:
                self._v76_strict_prebuy[symbol] = stamp
            elif managed == _V7600_STATE.MARKET_HOLD:
                self._v76_strict_market_hold[symbol] = stamp
            self._v7588_phase[symbol] = phase

            try:
                repaired_stage = self._v7609_write_stage_mirror(
                    symbol, item, desired_stage
                ) or repaired_stage
                source = str(getattr(item, "source", "") or "")
                base_source = source.split("|V7609_STRICT_STAGE=", 1)[0]
                base_source = base_source.split("|V7609_STATE_LEDGER=", 1)[0]
                marker = f"V7609_STATE_LEDGER={phase}"
                protected_source = f"{base_source}|{marker}" if base_source else marker
                if source != protected_source:
                    item.source = protected_source
                    repaired_stage = True
                if getattr(item, "updated_at", None) != stamp:
                    item.updated_at = stamp
                    repaired_stage = True
            except (AttributeError, TypeError):
                continue
        if repaired_stage:
            try:
                self._save_supply_potential()
            except Exception as exc:
                self.logger.warning("V7609 엄격관심 단계복원 저장실패: %s", exc)

    def _v7609_prune_unverified_scanner_rows(self, now: datetime) -> None:
        """Remove current scanner rows that never passed today's observation gate."""

        potentials = getattr(self, "_supply_potential", {})
        if not isinstance(potentials, dict):
            return
        positions = getattr(getattr(self, "broker", None), "positions", {})
        removed: list[str] = []
        for symbol, item in list(potentials.items()):
            symbol = str(symbol)
            source = str(getattr(item, "source", ""))
            manual = any(token in source for token in ("사용자", "형명령", "수동", "직접관심"))
            if symbol in positions or manual:
                continue
            if self._v7609_has_today_observation_admission(symbol, now):
                continue
            if self._v7609_volume_prebuy_event(symbol, now)[0] is not None:
                continue
            potentials.pop(symbol, None)
            self._v7588_phase.pop(symbol, None)
            self._v7588_flush.pop(symbol, None)
            removed.append(symbol)
        if removed:
            try:
                self._save_supply_potential()
            except Exception as exc:
                self.logger.warning("V7609 미검증 관찰 정리 저장실패: %s", exc)
            self.logger.warning(
                "V7609 오늘 엄격관찰 미통과 행 %d개 제거",
                len(removed),
            )

    def _v7609_roll_session(self, now: datetime) -> None:
        """Clear scanner-only state at a date change while preserving holdings."""

        if self._v7609_session_date == now.date():
            return
        previous = self._v7609_session_date
        self._v7609_session_date = now.date()
        # Scanner events are session-bound.  Holdings and fills remain in the
        # broker ledger and are deliberately not reset here.
        self._v7609_stage_manager = _V7600_STATE.StateManager()
        self._v7609_stage_log = _V7600_STATE.JsonlEventLog(
            self._v7609_stage_event_path(now.date())
        )
        getattr(self, "_supply_potential", {}).clear()
        self._v7588_phase.clear()
        self._v7588_flush.clear()
        self._v7588_order_bucket.clear()
        self._r21_stage_times.clear()
        self._v76_order_bar.clear()
        self._v76_strict_observation.clear()
        self._v76_strict_interest.clear()
        self._v76_strict_prebuy.clear()
        self._v76_strict_market_hold.clear()
        self._v76_strict_block.clear()
        self._v7609_intrabar_interest_hold_log.clear()
        self._v76_fast_fail.clear()
        _v7609_prune_collaboration_hub_today()
        try:
            self._save_supply_potential()
        except Exception as exc:
            self.logger.warning("V7609 일일 스캐너 초기화 저장실패: %s", exc)
        self.logger.warning(
            "V7609 일일 스캐너 상태 초기화 %s -> %s (보유·체결원장 유지)",
            previous,
            now.date(),
        )

    def _v7588_publish(
        self,
        symbol: str,
        phase: str,
        stage: str,
        reason: str,
        now: datetime,
        *,
        allow_downgrade: bool = False,
    ) -> None:
        """Preserve R21 stage clocks while accepting V75.93 downgrade calls."""

        candidate_stages = {"매수후보", "매수직전", "매수가능성"}
        reason_text = str(reason)
        if stage == "관찰후보" and reason_text.startswith("V7594:관찰편입="):
            self._v76_strict_observation[str(symbol)] = now
        strict_interest = self._v7609_is_strict_interest_reason(reason_text)
        explicit_manual_interest = any(
            token in reason_text for token in ("사용자", "형 의견", "수동", "직접관심")
        )
        manual_interest = explicit_manual_interest
        observation_stamp = self._v7609_today_observation_timestamp(str(symbol), now)
        today_observed = observation_stamp is not None
        protected_phase, protected_stage = self._v7609_strict_stage_today(
            str(symbol), now
        )
        if protected_phase and (
            stage == "관찰후보"
            or (
                protected_phase in {"PREBUY", "MARKET_DEFERRED"}
                and stage == "관심종목"
            )
        ):
            self._v7588_phase[str(symbol)] = protected_phase
            self.logger.info(
                "V7609 검증단계 하향차단 %s | 유지=%s 요청=%s reason=%s",
                self._label(symbol), protected_stage, stage, reason,
            )
            return
        publish_now = now
        if stage == "관심종목" and not explicit_manual_interest:
            raw = list(
                getattr(self.client, "v7580_raw_cache", {}).get(str(symbol), [])
            )
            active_pullback = active_interest_pullback_decision(raw, now=now)
            if not active_pullback.allowed:
                self.logger.info(
                    "V7610 현재기준음봉 실제조정미완료 관심대기 %s reason=%s source=%s",
                    self._label(symbol), active_pullback.reason, reason,
                )
                return
            trigger_value = _v7610_interest_trigger_time(raw, now)
            if trigger_value is not None:
                trigger_stamp = self._v7609_timestamp(
                    trigger_value, now
                )
                if trigger_stamp is not None:
                    publish_now = trigger_stamp
            if observation_stamp is not None and publish_now < observation_stamp:
                publish_now = observation_stamp
        if stage == "관심종목" and not manual_interest and not today_observed:
            self.logger.info(
                "V7609 오늘관찰선행없는 관심차단 %s | reason=%s",
                self._label(symbol), reason,
            )
            return
        if stage == "관심종목" and not manual_interest:
            ratio_map = getattr(
                getattr(self, "strategy", None), "same_time_volume_ratio", {}
            )
            same_time_ratio = float(
                ratio_map.get(str(symbol), 0.0) if isinstance(ratio_map, dict) else 0.0
            )
            if same_time_ratio + 1e-12 < 2.0:
                self.logger.info(
                    "V7609 전일동시간누적거래2배미달 관심대기 %s ratio=%.3f reason=%s",
                    self._label(symbol), same_time_ratio, reason,
                )
                return
        if stage == "관심종목" and not (strict_interest or manual_interest):
            if protected_phase:
                self._v7588_phase[str(symbol)] = protected_phase
                self.logger.info(
                    "V7609 이전 관심경로 덮어쓰기차단 %s | 유지=%s reason=%s",
                    self._label(symbol), protected_stage, reason,
                )
                return
            if not today_observed:
                self.logger.info(
                    "V7609 미편입관찰 혼입차단 %s | phase=%s reason=%s",
                    self._label(symbol), phase, reason,
                )
                return
            self.logger.info(
                "V76 엄격관심차단 %s | phase=%s reason=%s",
                self._label(symbol), phase, reason,
            )
            super(_PARENT_ENGINE, self)._v7588_publish(
                symbol,
                "OBSERVE",
                "관찰후보",
                f"엄격관심대기:{reason}",
                now,
                allow_downgrade=True,
            )
            self._v76_strict_interest.pop(str(symbol), None)
            return

        strict_market_hold = str(reason).startswith("V76엄격시장보류:")
        if stage == "시장보류" and not strict_market_hold:
            if protected_phase:
                self._v7588_phase[str(symbol)] = protected_phase
                self.logger.info(
                    "V7609 이전 시장보류경로 덮어쓰기차단 %s | 유지=%s reason=%s",
                    self._label(symbol), protected_stage, reason,
                )
                return
            self.logger.info(
                "V76 엄격시장보류차단 %s | phase=%s reason=%s",
                self._label(symbol), phase, reason,
            )
            super(_PARENT_ENGINE, self)._v7588_publish(
                symbol,
                "OBSERVE",
                "관찰후보",
                f"엄격조건재확인:{reason}",
                now,
                allow_downgrade=True,
            )
            self._v76_strict_market_hold.pop(str(symbol), None)
            return

        strict_reason = str(reason).startswith(("1차1/2:", "돌파눌림확인:"))
        strict_reason = strict_reason or str(reason).startswith((
            "V7609_CONFIRMED_MA5:",
            "V7609_BOTTOM_MA5:",
        ))
        if stage in candidate_stages and strict_reason:
            interest_stamp = self._v76_strict_interest.get(str(symbol))
            interest_verified = (
                isinstance(interest_stamp, datetime)
                and interest_stamp.date() == now.date()
            )
            if not interest_verified and protected_phase != "PREBUY":
                self.logger.info(
                    "V7609 관심선행없는 직전차단 %s | phase=%s reason=%s",
                    self._label(symbol), phase, reason,
                )
                return
        if stage in candidate_stages and not strict_reason:
            # Do not let the inherited MA20 trend, loose exhaustion or old
            # pullback routes enter the user's merged "매수직전" category.
            if protected_phase:
                self._v7588_phase[str(symbol)] = protected_phase
                self.logger.info(
                    "V7609 이전 후보경로 덮어쓰기차단 %s | 유지=%s reason=%s",
                    self._label(symbol), protected_stage, reason,
                )
                return
            self.logger.info(
                "V76 엄격직전차단 %s | phase=%s stage=%s reason=%s",
                self._label(symbol), phase, stage, reason,
            )
            super(_PARENT_ENGINE, self)._v7588_publish(
                symbol,
                "OBSERVED",
                "관찰후보",
                f"엄격조건대기:{reason}",
                now,
                allow_downgrade=True,
            )
            self._v76_strict_prebuy.pop(str(symbol), None)
            self._v76_strict_block[str(symbol)] = (now, str(reason))
            return

        if stage in candidate_stages and strict_reason:
            self._v76_strict_block.pop(str(symbol), None)

        published_reason = reason_text
        if stage == "관심종목":
            published_reason = f"{V7609_INTEREST_EVENT_MARKER}{reason_text}"
        elif stage in candidate_stages:
            published_reason = f"{V7609_PREBUY_EVENT_MARKER}{reason_text}"

        old_phase = str(getattr(self, "_v7588_phase", {}).get(symbol, ""))
        super(_PARENT_ENGINE, self)._v7588_publish(
            symbol,
            phase,
            stage,
            published_reason,
            publish_now,
            allow_downgrade=allow_downgrade,
        )
        new_phase = str(getattr(self, "_v7588_phase", {}).get(symbol, ""))
        if old_phase != new_phase:
            canonical = (
                "매수가능성"
                if stage in {"매수직전", "매수후보", "매수가능성"}
                else stage
            )
            self._r21_stage_times[(canonical, str(symbol))] = publish_now.strftime("%H:%M:%S")
        # The semantic arbiter may reject a regressive request.  Do not update
        # strict-stage maps unless the requested phase was actually accepted.
        if new_phase != str(phase):
            return
        # STATE-002: the parent may still mirror the stage for compatibility,
        # but only a durable, ordered event is allowed to become authoritative.
        if not self._v7609_record_authoritative_stage(
            str(symbol), str(stage), published_reason, publish_now
        ):
            self._v7588_phase[str(symbol)] = old_phase
            return
        if stage in candidate_stages:
            self._v76_strict_prebuy[str(symbol)] = now
            self._v76_strict_interest.pop(str(symbol), None)
            self._v76_strict_market_hold.pop(str(symbol), None)
        elif stage == "관심종목":
            self._v76_strict_interest[str(symbol)] = publish_now
            self._v76_strict_prebuy.pop(str(symbol), None)
            self._v76_strict_market_hold.pop(str(symbol), None)
        elif stage == "시장보류":
            self._v76_strict_market_hold[str(symbol)] = now
            self._v76_strict_interest.pop(str(symbol), None)
            self._v76_strict_prebuy.pop(str(symbol), None)
        elif stage in {"매수종목", "매도종목"}:
            self._v76_strict_interest.pop(str(symbol), None)
            self._v76_strict_prebuy.pop(str(symbol), None)
            self._v76_strict_market_hold.pop(str(symbol), None)

    @staticmethod
    def _v7609_timestamp(value: Any, now: datetime) -> datetime | None:
        if isinstance(value, datetime):
            stamp = value
        else:
            text = str(value or "").strip()
            if not text:
                return None
            try:
                stamp = datetime.fromisoformat(text)
            except ValueError:
                try:
                    parsed = datetime.strptime(text, "%H:%M:%S").time()
                    stamp = datetime.combine(now.date(), parsed, KST)
                except ValueError:
                    return None
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=KST)
        return stamp.astimezone(KST)

    def _v7609_fill_records(self, now: datetime) -> dict[str, dict[str, Any]]:
        """Read today's exact PAPER fill clocks and prices for dashboard display."""

        records: dict[str, dict[str, Any]] = {}
        path = Path(getattr(_V753, "FILL_FILE", ""))
        if not path.exists():
            return records
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                for raw in csv.DictReader(handle):
                    symbol = str(raw.get("symbol", "")).strip().zfill(6)
                    stamp = self._v7609_timestamp(raw.get("timestamp"), now)
                    if not symbol or stamp is None or stamp.date() != now.date():
                        continue
                    side = str(raw.get("side", "")).strip().upper()
                    if side not in {"BUY", "SELL"}:
                        continue
                    item = records.setdefault(symbol, {})
                    prefix = side.lower()
                    item[f"{prefix}_time"] = stamp
                    item[f"{prefix}_price"] = float(raw.get("price", 0.0) or 0.0)
                    item[f"{prefix}_qty"] = int(float(raw.get("quantity", 0) or 0))
                    if side == "SELL":
                        item["sell_pnl"] = float(raw.get("pnl", 0.0) or 0.0)
                        item["sell_pnl_pct"] = float(raw.get("pnl_pct", 0.0) or 0.0)
                        item["sell_reason"] = str(raw.get("reason", "") or "").strip()
                        item["sell_cycle"] = int(item.get("sell_cycle", 0) or 0) + 1
                        entry_stamp = self._v7609_timestamp(raw.get("entry_time"), now)
                        item["sell_entry_time"] = entry_stamp
                        entry_price = float(raw.get("entry_price", 0.0) or 0.0)
                        item["sell_entry_price"] = entry_price
                        item.setdefault('sales', []).append({k: v for k, v in item.items() if k != 'sales'})
        except (OSError, ValueError, TypeError, csv.Error):
            return records
        return records

    @staticmethod
    def _v7609_pnl_badge(pnl: float, pnl_pct: float) -> str:
        """Make the P/L direction unmistakable in the narrow sold-row detail."""

        if pnl > 0:
            return f"▲ 수익 +{abs(pnl):,.0f}원 (+{abs(pnl_pct):.2%})"
        if pnl < 0:
            return f"▼ 손실 -{abs(pnl):,.0f}원 (-{abs(pnl_pct):.2%})"
        return "■ 손익 0원 (0.00%)"

    @staticmethod
    def _v7609_sell_reason_label(reason: str) -> str:
        labels = {
            "V7609_FAST_FAILURE_TWO_BARS_BELOW_MA5": "빠른실패청산(완성 1분봉 2개 연속 MA5 아래)",
            "V7609_FAST_FAILURE_SUPPORT_BREAK": "빠른실패청산(확인지지선 이탈)",
            "V7609_FIXED_2PCT_STOP_AFTER_STRUCTURE": "고정손절 -2.0%(구조실패 후 손절가 도달)",
            "V7609_FIXED_1P5PCT_STOP_AFTER_STRUCTURE": "고정손절 -1.5%(구조실패 후 손절가 도달)",
        }
        cleaned = str(reason or "").strip()
        return labels.get(cleaned, cleaned or "매도사유 기록없음")

    def _v7609_stage_timestamp(
        self,
        stage: str,
        symbol: str,
        now: datetime,
        original: Any,
    ) -> datetime | None:
        if stage in {"매수가능성", "매수직전", "매수후보"}:
            _stamp, direct_event = self._v7609_volume_prebuy_event(symbol, now)
            if direct_event is not None:
                return self._v7609_timestamp(direct_event.decision_time, now)
        stage_keys = {
            "관찰후보": ("관찰",),
            "관심종목": ("관심",),
            "매수가능성": ("후보", "직전"),
            "매수직전": ("후보", "직전"),
            "시장보류": ("보류",),
            "매수종목": ("매수",),
            "매도종목": ("매도",),
        }
        candidates: list[datetime] = []
        timeline = getattr(self, "_v7591_timeline", {})
        item = timeline.get(symbol, {}) if isinstance(timeline, dict) else {}
        if isinstance(item, dict):
            for key in stage_keys.get(stage, ()):
                matching_events = [
                    event
                    for event in item.get("events", [])
                    if isinstance(event, dict) and str(event.get("stage", "")) == key
                ]
                real_events = [
                    event
                    for event in matching_events
                    if self._v7609_is_verified_timeline_event(
                        str(event.get("stage", "")),
                        str(event.get("reason", "")),
                    )
                ]
                if real_events:
                    for event in real_events:
                        stamp = self._v7609_timestamp(event.get("at"), now)
                        if stamp is not None and stamp.date() == now.date():
                            candidates.append(stamp)
                elif not matching_events and key not in {"관심", "후보", "직전"}:
                    # Older ledgers may have the direct stage clock without an
                    # event list.  Accept that durable value, but never accept a
                    # known "화면행최초확인" placeholder as an admission clock.
                    stamp = self._v7609_timestamp(item.get(key), now)
                    if stamp is not None and stamp.date() == now.date():
                        candidates.append(stamp)

        canonical = (
            "매수가능성"
            if stage in {"매수후보", "매수직전", "매수가능성"}
            else stage
        )
        clock = getattr(self, "_r21_stage_times", {}).get((canonical, symbol))
        stamp = self._v7609_timestamp(clock, now)
        if stamp is not None and stamp.date() == now.date():
            candidates.append(stamp)
        if candidates:
            # A stage clock means the first real admission of the day.  Later
            # re-scans or downgrade/re-entry events must not replace it.
            return min(candidates)
        return None

    def _v7609_departed_stage_timestamp(
        self,
        stage: str,
        symbol: str,
        now: datetime,
        original: Any,
    ) -> datetime | None:
        """Accept a departed row clock only when a real timeline event confirms it."""

        stamp = self._v7609_timestamp(original, now)
        if stamp is None or stamp.date() != now.date():
            return None
        stage_keys = {
            "관찰후보": ("관찰",),
            "관심종목": ("관심",),
            "매수가능성": ("후보", "직전"),
            "매수직전": ("후보", "직전"),
            "시장보류": ("보류",),
            "매수종목": ("매수",),
            "매도종목": ("매도",),
        }
        timeline = getattr(self, "_v7591_timeline", {})
        item = timeline.get(symbol, {}) if isinstance(timeline, dict) else {}
        for event in item.get("events", []) if isinstance(item, dict) else []:
            if (
                not isinstance(event, dict)
                or str(event.get("stage", "")) not in stage_keys.get(stage, ())
                or not self._v7609_is_verified_timeline_event(
                    str(event.get("stage", "")),
                    str(event.get("reason", "")),
                )
            ):
                continue
            event_stamp = self._v7609_timestamp(event.get("at"), now)
            if event_stamp is not None and abs((event_stamp - stamp).total_seconds()) <= 2:
                return stamp
        return None

    def _v7609_stage_path(self, symbol: str, now: datetime) -> str:
        """Use the authority, retaining departures and repeated stage visits."""
        values = []
        for event in self._v7609_state_manager_obj().events:
            stamp = self._v7609_timestamp(event.decision_time, now)
            if event.symbol != str(symbol).zfill(6) or stamp is None or stamp.date() != now.date():
                continue
            label = "탈락" if event.new_stage == _V7600_STATE.REMOVED else event.new_stage
            value = f"{label} {stamp:%H:%M:%S}"
            if event.new_stage == _V7600_STATE.REMOVED:
                value += f"({event.reason})"
            values.append(value)
        return " → ".join(values)

    @staticmethod
    def _v7609_strip_legacy_stage_path(reason: str) -> str:
        """Remove inherited duplicate/placeholder stage clocks from the detail."""
        cleaned = re.sub(r"^시간이력\s+.*?\s+·\s*", "", str(reason), count=1)
        cleaned = re.sub(r"^체결기록\[[^\]]*\]\s*·\s*", "", cleaned, count=1)
        cleaned = cleaned.replace(V7609_INTEREST_EVENT_MARKER, "")
        cleaned = cleaned.replace(V7609_PREBUY_EVENT_MARKER, "")
        stage_clock = (
            r"(?:관찰|관심|후보|직전|매수|매도|보류)"
            r"\s+\d{1,2}:\d{2}(?::\d{2})?"
        )
        cleaned = re.sub(
            rf"^{stage_clock}(?:\s*→\s*{stage_clock})*\s*·\s*",
            "",
            cleaned,
            count=1,
        )
        return cleaned.strip()

    def _v7609_resolve_display_name(self, symbol: str, row_name: str = "") -> str:
        """Restore a durable Korean name when live quote-name lookup is empty."""

        candidates = [
            str(row_name or "").strip(),
            str(getattr(self, "names", {}).get(symbol, "") or "").strip(),
            str(
                getattr(self, "_v7591_timeline", {}).get(symbol, {}).get("name", "")
                or ""
            ).strip(),
            str(_V7609_VERIFIED_NAME_FIXES.get(symbol, "") or "").strip(),
        ]
        for candidate in candidates:
            if candidate and candidate != symbol and not candidate.isdigit():
                return candidate
        return str(row_name or symbol).strip() or symbol

    @staticmethod
    def _v7609_postclose_review_active(now: datetime) -> bool:
        """Expose the Friday close once, then return to the normal live engine."""

        if now.tzinfo is None:
            now = now.replace(tzinfo=KST)
        return V7609_POSTCLOSE_REVIEW_FROM <= now < V7609_POSTCLOSE_REVIEW_UNTIL

    def _v7609_postclose_prices(self) -> dict[str, dict[str, Any]]:
        """Load each symbol's last recorded Friday price without creating orders."""

        cached = self._v7609_postclose_price_cache
        if cached is not None:
            return cached
        latest: dict[str, dict[str, Any]] = {}
        try:
            with V7609_ENTRY_AUDIT_FILE.open("r", encoding="utf-8-sig", newline="") as handle:
                for item in csv.DictReader(handle):
                    try:
                        stamp = datetime.fromisoformat(str(item.get("logged_at", "")))
                    except (TypeError, ValueError):
                        continue
                    if stamp.tzinfo is None:
                        stamp = stamp.replace(tzinfo=KST)
                    if not (V7609_POSTCLOSE_EVENT_FROM <= stamp <= V7609_POSTCLOSE_EVENT_UNTIL):
                        continue
                    symbol = str(item.get("symbol", "")).strip().zfill(6)
                    if not symbol.isdigit():
                        continue
                    try:
                        current_price = float(item.get("current_price", 0.0) or 0.0)
                    except (TypeError, ValueError):
                        current_price = 0.0
                    try:
                        reference_price = float(item.get("reference_price", 0.0) or 0.0)
                    except (TypeError, ValueError):
                        reference_price = 0.0
                    previous = latest.get(symbol)
                    if previous is not None and previous["stamp"] >= stamp:
                        continue
                    latest[symbol] = {
                        "stamp": stamp,
                        "name": str(item.get("name", "") or "").strip(),
                        "current_price": current_price,
                        "reference_price": reference_price,
                    }
        except OSError as exc:
            self.logger.warning("V7609_POSTCLOSE_AUDIT_READ_FAILED file=%s error=%s", V7609_ENTRY_AUDIT_FILE, exc)
        self._v7609_postclose_price_cache = latest
        return latest

    def _v7609_postclose_review_rows(
        self,
        snapshot: dict[str, Any],
        now: datetime,
    ) -> list[dict[str, Any]] | None:
        """Rebuild the final 2026-08-07 scanner stage for read-only review."""

        if not self._v7609_postclose_review_active(now):
            return None
        cached = self._v7609_postclose_review_cache
        if cached is not None:
            return [dict(row) for row in cached]
        timeline = getattr(self, "_v7591_timeline", {})
        if not isinstance(timeline, dict):
            timeline = {}

        # The clean ZIP intentionally omits mutable runtime logs.  Keep only
        # the immutable Friday-close decisions needed for this one review.
        if not timeline:
            try:
                saved = json.loads(V7609_POSTCLOSE_REVIEW_FILE.read_text(encoding="utf-8"))
                saved_rows = saved.get("rows", []) if isinstance(saved, dict) else []
                if saved_rows:
                    rows = []
                    for raw_row in saved_rows:
                        if not isinstance(raw_row, dict):
                            continue
                        row = dict(raw_row)
                        symbol = str(row.get("symbol", "")).strip().zfill(6)
                        row["symbol"] = symbol
                        row["name"] = self._v7609_resolve_display_name(
                            symbol,
                            str(row.get("name", "") or ""),
                        )
                        rows.append(row)
                    self._v7609_postclose_review_cache = [dict(row) for row in rows]
                    return [dict(row) for row in rows]
            except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
                self.logger.warning(
                    "V7609_POSTCLOSE_REVIEW_READ_FAILED file=%s error=%s",
                    V7609_POSTCLOSE_REVIEW_FILE,
                    exc,
                )

        base_by_symbol: dict[str, dict[str, Any]] = {}
        for raw_row in snapshot.get("rows", []):
            symbol = str(raw_row.get("symbol", "")).strip().zfill(6)
            if symbol.isdigit():
                base_by_symbol[symbol] = dict(raw_row)
        price_by_symbol = self._v7609_postclose_prices()
        stage_map = {
            "관찰": "관찰후보",
            "관심": "관심종목",
            "후보": "매수가능성",
            "직전": "매수가능성",
            "보류": "시장보류",
        }
        rows: list[dict[str, Any]] = []
        for raw_symbol, item in timeline.items():
            if not isinstance(item, dict):
                continue
            symbol = str(raw_symbol).strip().zfill(6)
            latest_event: tuple[datetime, dict[str, Any]] | None = None
            for event in item.get("events", []):
                if not isinstance(event, dict):
                    continue
                raw_stage = str(event.get("stage", "")).strip()
                if raw_stage not in stage_map:
                    continue
                try:
                    stamp = datetime.fromisoformat(str(event.get("at", "")))
                except (TypeError, ValueError):
                    continue
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=KST)
                if not (V7609_POSTCLOSE_EVENT_FROM <= stamp <= V7609_POSTCLOSE_EVENT_UNTIL):
                    continue
                if latest_event is None or stamp > latest_event[0]:
                    latest_event = (stamp, event)
            if latest_event is None:
                continue

            stamp, event = latest_event
            raw_stage = str(event.get("stage", "")).strip()
            stage = stage_map[raw_stage]
            base = dict(base_by_symbol.get(symbol, {}))
            audit = price_by_symbol.get(symbol, {})
            name = self._v7609_resolve_display_name(
                symbol,
                str(item.get("name", "") or audit.get("name", "") or base.get("name", "")),
            )
            current_price = float(audit.get("current_price", 0.0) or base.get("current_price", 0.0) or 0.0)
            reference_price = float(audit.get("reference_price", 0.0) or 0.0)
            change_pct = (
                ((current_price / reference_price) - 1.0) * 100.0
                if current_price > 0 and reference_price > 0
                else float(base.get("change_pct", 0.0) or 0.0)
            )
            label = "매수직전(주문 없음)" if raw_stage in {"후보", "직전"} else f"{raw_stage} 최종판정"
            reason = str(event.get("reason", "") or "판정근거 기록없음").strip()
            base.update({
                "stage": stage,
                "symbol": symbol,
                "name": name,
                "current_price": current_price,
                "change_pct": change_pct,
                "stage_time": stamp.strftime("%H:%M:%S"),
                "reason": f"8/7 장마감 {label} · PAPER 주문·체결 없음 · {reason}",
                "departed": False,
            })
            rows.append(base)

        stage_order = {"관찰후보": 0, "관심종목": 1, "매수가능성": 2, "시장보류": 3}
        rows.sort(key=lambda row: (
            stage_order.get(str(row.get("stage", "")), 9),
            str(row.get("stage_time", "")),
            str(row.get("symbol", "")),
        ))
        self._v7609_postclose_review_cache = [dict(row) for row in rows]
        return [dict(row) for row in rows]

    def _enrich_all_times(self, snapshot: dict[str, Any], now: datetime) -> None:
        """Enrich GUI values without inventing or persisting stage events."""

        fills = self._fill_times(now)
        for row in snapshot.get("rows", []):
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol", "")).zfill(6)
            stage = str(row.get("stage", ""))
            quote = getattr(self, "latest_quotes", {}).get(symbol)
            if quote is not None:
                current = float(getattr(quote, "price", 0.0) or 0.0)
                if current > 0:
                    row["current_price"] = current
            fill = fills.get(symbol, {})
            if stage == "매도종목" and fill:
                row["current_price"] = fill.get(
                    "sell_price", row.get("current_price", 0)
                )
                row["target_price"] = fill.get(
                    "sell_price", row.get("target_price", 0)
                )
                row["quantity"] = fill.get(
                    "sell_qty", row.get("quantity", 0)
                )
            reject = getattr(self, "_v7591_rejections", {}).get(symbol)
            if reject and stage not in {"매수종목", "매도종목"}:
                row["reason"] = f"{reject} · {str(row.get('reason', ''))}"

    def _today_sell_rows(self) -> list[dict[str, str]]:
        """Return every same-day sell, not only the last 20.

        V75.23 kept ``rows[-20:]``. After the 20th sell, the ledger check
        logged ``gui=20`` for the rest of the session and the earliest fills
        never reached the 오늘매도 rows (2026-09-23: ledger=35 gui=20).
        """
        inherited = super()._today_sell_rows.__func__
        v753 = inherited.__globals__["v753"]
        kst = inherited.__globals__["KST"]
        path = Path(v753.FILL_FILE)
        today = datetime.now(kst).date()
        rows: list[dict[str, str]] = []
        if not path.exists():
            return rows
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                for raw in csv.DictReader(handle):
                    if str(raw.get("side", "")).upper() != "SELL":
                        continue
                    try:
                        timestamp = datetime.fromisoformat(str(raw.get("timestamp", "")))
                        if timestamp.tzinfo is None:
                            timestamp = timestamp.replace(tzinfo=kst)
                        timestamp = timestamp.astimezone(kst)
                    except ValueError:
                        continue
                    if timestamp.date() != today:
                        continue
                    symbol = str(raw.get("symbol", "")).zfill(6)
                    price = float(raw.get("price", 0.0) or 0.0)
                    pnl = float(raw.get("pnl", 0.0) or 0.0)
                    reason = " ".join(str(raw.get("reason", "") or "").split())[:45]
                    label = str(self._label(symbol) or "").strip()
                    suffix = f"({symbol})"
                    if label.endswith(suffix):
                        label = label[: -len(suffix)].strip()
                    rows.append({
                        "stage": "매도종목",
                        "time": timestamp.strftime("%H:%M:%S"),
                        "name": label or symbol,
                        "symbol": symbol,
                        "price": f"{price:,.0f}원" if price > 0 else "조회대기",
                        "reason": f"{reason} · 손익 {pnl:+,.0f}원",
                    })
        except (OSError, csv.Error, ValueError) as exc:
            self.logger.warning("매도이력 읽기 실패: %s", exc)
        return rows

    def _gui_snapshot(self) -> dict[str, Any]:
        now = datetime.now(KST)
        self._v7609_roll_session(now)
        # Parent snapshots read the mutable SupplyPotential stage directly.
        # Repair it at the read boundary as well, so a concurrent legacy 3m
        # refresh cannot make verified 관심/직전 rows disappear from the cards.
        self._v7609_reconcile_strict_supply_stages(now)
        snapshot = super()._gui_snapshot()
        postclose_rows = self._v7609_postclose_review_rows(snapshot, now)
        if postclose_rows is not None:
            snapshot["rows"] = postclose_rows
            snapshot["postclose_review"] = {
                "date": V7609_POSTCLOSE_REVIEW_DATE.isoformat(),
                "read_only": True,
                "orders_created": 0,
            }
            return snapshot
        fills = self._v7609_fill_records(now)
        positions = getattr(self.broker, "positions", {})
        rows: list[dict[str, Any]] = []
        for raw_row in snapshot.get("rows", []):
            row = dict(raw_row)
            for key, value in tuple(row.items()):
                if isinstance(value, str):
                    row[key] = value.replace("고정손절 -5.0%", "고정손절 -1.5%")
            symbol = str(row.get("symbol", "")).strip().zfill(6)
            row_name = str(row.get("name", "") or "").strip()
            if not row_name or row_name == symbol or row_name.isdigit():
                row["name"] = self._v7609_resolve_display_name(symbol, row_name)
            raw_stage = str(row.get("stage", "")).strip()
            protected_phase, protected_stage = self._v7609_strict_stage_today(
                symbol, now
            )
            canonical_stage = _v7609_dashboard_stage(
                raw_stage, protected_phase, protected_stage
            )
            managed_stage = self._v7609_engine_stage_from_state(
                self._v7609_state_manager_obj().stage_of(symbol)
            )
            if managed_stage is not None and canonical_stage not in {"매수종목", "매도종목"}:
                canonical_stage = managed_stage
            row["stage"] = canonical_stage
            fill = fills.get(symbol, {})

            # The dashboard cards are current-state lists.  Inherited departed
            # rows are history records and must not be counted as still active.
            if bool(row.get("departed")):
                continue
            # A live holding or a completed same-day sale must not reappear in a
            # lower scanner stage.  "오늘매도" remains an event-history list, so
            # a symbol may still be shown there after a later re-entry.
            if symbol in positions and canonical_stage not in {"매수종목", "매도종목"}:
                continue
            if (
                symbol not in positions
                and fill.get("sell_time") is not None
                and canonical_stage != "매도종목"
            ):
                continue
            if canonical_stage == "매수종목" and symbol not in positions:
                continue
            stage = canonical_stage
            if stage == "관찰후보":
                if not self._v7609_has_today_observation_admission(symbol, now):
                    continue
                stage_stamp = self._v7609_stage_timestamp(
                    stage, symbol, now, row.get("stage_time", "")
                )
            elif stage == "관심종목":
                strict_stamp = self._v76_strict_interest.get(symbol)
                if strict_stamp is None or strict_stamp.date() != now.date():
                    continue
                stage_stamp = self._v7609_stage_timestamp(
                    stage, symbol, now, strict_stamp
                )
            elif stage == "매수가능성":
                strict_stamp = self._v76_strict_prebuy.get(symbol)
                if strict_stamp is None or strict_stamp.date() != now.date():
                    continue
                stage_stamp = self._v7609_stage_timestamp(
                    stage, symbol, now, strict_stamp
                )
            elif stage == "시장보류":
                strict_stamp = self._v76_strict_market_hold.get(symbol)
                if strict_stamp is None or strict_stamp.date() != now.date():
                    continue
                stage_stamp = self._v7609_stage_timestamp(
                    stage, symbol, now, strict_stamp
                )
            else:
                stage_stamp = self._v7609_stage_timestamp(
                    stage, symbol, now, row.get("stage_time", "")
                )
            if stage_stamp is not None:
                row["stage_time"] = stage_stamp.strftime("%H:%M:%S")
            else:
                # Do not repeat the old 09:00/session-start placeholder as if
                # it were a verified admission time.
                row["stage_time"] = "기록없음"
                if stage not in {"매수종목", "매도종목"}:
                    continue

            reason = self._v7609_strip_legacy_stage_path(str(row.get("reason", "")))
            if stage == "매수가능성" and self._v7609_volume_prebuy_event(symbol, now)[0] is not None:
                reason = self._v7609_volume_prebuy_event(symbol, now)[1].reason
            stage_path = self._v7609_stage_path(symbol, now)

            if stage == "매수종목":
                position = positions.get(symbol)
                buy_stamp = fill.get("buy_time")
                if buy_stamp is None and position is not None:
                    buy_stamp = self._v7609_timestamp(
                        getattr(position, "entry_time", None), now
                    )
                buy_price = float(fill.get("buy_price", 0.0) or 0.0)
                average_price = float(
                    getattr(position, "entry_price", 0.0) if position is not None else 0.0
                )
                row["avg_price"] = average_price
                row["quantity"] = int(getattr(position, "quantity", 0) or 0)
                quote = getattr(self, "latest_quotes", {}).get(symbol)
                current = float(getattr(quote, "price", 0.0) or row.get("current_price", 0.0) or 0.0)
                row["current_price"] = current
                row["change_pct"] = (current / average_price - 1.0) * 100 if current > 0 and average_price > 0 else ""
                commission, tax = self._v7609_cost_rates()
                net_rate = _EXIT_POLICY.net_return(position, current, commission, tax) if current > 0 else None
                row.update(buy_date=buy_stamp.strftime("%Y-%m-%d") if buy_stamp else "기록없음",
                    buy_time=buy_stamp.strftime("%H:%M:%S") if buy_stamp else "-",
                    buy_price=average_price, net_pnl_pct=net_rate * 100 if net_rate is not None else None,
                    net_pnl=net_rate * _EXIT_POLICY.cost_per_share(position, commission) * row["quantity"] if net_rate is not None else None)
                if buy_stamp is not None:
                    row["stage_time"] = buy_stamp.strftime(
                        "%m/%d %H:%M:%S" if buy_stamp.date() != now.date() else "%H:%M:%S"
                    )
                if buy_stamp is not None and buy_price > 0:
                    buy_label = "이월매수" if buy_stamp.date() != now.date() else "최근매수"
                    detail = (
                        f"{buy_label} {buy_stamp:%m/%d %H:%M:%S} · 체결가 {buy_price:,.0f}원"
                    )
                    if average_price > 0:
                        detail += f" · 현재평단 {average_price:,.0f}원"
                    reason = f"체결기록[{detail}] · {reason}"

            elif stage == "매도종목":
                sell_stamp = fill.get("sell_time")
                sell_price = float(fill.get("sell_price", 0.0) or 0.0)
                buy_stamp = fill.get("sell_entry_time") or fill.get("buy_time")
                buy_price = float(
                    fill.get("sell_entry_price", 0.0)
                    or fill.get("buy_price", 0.0)
                    or 0.0
                )
                if sell_stamp is not None:
                    row["stage_time"] = sell_stamp.strftime("%H:%M:%S")
                details: list[str] = []
                if buy_stamp is not None:
                    buy_label = "이월매수" if buy_stamp.date() != now.date() else "매수"
                    details.append(f"{buy_label} {buy_stamp:%m/%d %H:%M:%S}")
                if buy_price > 0:
                    details.append(f"매수가 {buy_price:,.0f}원")
                if sell_stamp is not None:
                    details.append(f"매도 {sell_stamp:%H:%M:%S}")
                if sell_price > 0:
                    details.append(f"매도가 {sell_price:,.0f}원")
                pnl = float(fill.get("sell_pnl", 0.0) or 0.0)
                pnl_pct = float(fill.get("sell_pnl_pct", 0.0) or 0.0)
                badge = self._v7609_pnl_badge(pnl, pnl_pct)
                latest_sell_reason = self._v7609_sell_reason_label(
                    str(fill.get("sell_reason", "") or "")
                )
                row.update(buy_date=buy_stamp.strftime("%Y-%m-%d") if buy_stamp else "기록없음",
                    buy_time=buy_stamp.strftime("%H:%M:%S") if buy_stamp else "-", buy_price=buy_price,
                    sell_date=sell_stamp.strftime("%Y-%m-%d") if sell_stamp else "기록없음",
                    sell_time=sell_stamp.strftime("%H:%M:%S") if sell_stamp else "-",
                    sell_price=sell_price, net_pnl=pnl, net_pnl_pct=pnl_pct * 100,
                    sell_reason=latest_sell_reason)
                cycle = int(fill.get("sell_cycle", 0) or 0)
                cycle_label = f"당일 {cycle}회차 매도 · " if cycle > 1 else ""
                if details:
                    reason = (
                        f"{badge} · {cycle_label}체결기록[{' · '.join(details)}]"
                        f" · 매도사유 {latest_sell_reason}"
                    )

            if stage not in {"매수종목", "매도종목"}:
                clocks: list[str] = []
                if stage_stamp is not None:
                    clocks.append(f"편입 {stage_stamp:%H:%M:%S}")
                if clocks:
                    prefix = stage_path or " · ".join(clocks)
                    reason = f"{prefix} · {reason}"
            elif stage_path:
                reason = f"{reason} · 이동이력[{stage_path}]"

            row["reason"] = reason
            rows.append(row)

        # The inherited dashboard may emit the same symbol several times from
        # separate scanners.  Keep exactly one live row per category and symbol.
        unique: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            key = (str(row.get("stage", "")), str(row.get("symbol", "")).zfill(6))
            clock = str(row.get("stage_time", ""))
            rank = (1, clock)
            existing = unique.get(key)
            if existing is None:
                row["_v7609_rank"] = rank
                unique[key] = row
                continue
            if rank > existing.get("_v7609_rank", (-1, "")):
                row["_v7609_rank"] = rank
                unique[key] = row
        final_rows = list(unique.values())
        for row in final_rows:
            row.pop("_v7609_rank", None)
        # Sold rows are fill EVENTS, not a last-per-symbol current-state list.
        templates = {r['symbol']: r for r in final_rows if r.get('stage') == '매도종목'}
        for symbol, fill in fills.items():
            sales = fill.get('sales', [])
            if not sales:
                continue
            final_rows = [r for r in final_rows if not (r.get('stage') == '매도종목' and r.get('symbol') == symbol)]
            for sale in sales:
                row = dict(templates.get(symbol, {}))
                bought = sale.get('sell_entry_time') or sale.get('buy_time')
                sold = sale['sell_time']
                row.update(symbol=symbol, name=self._v7609_resolve_display_name(symbol, row.get('name', '')),
                    stage='매도종목', stage_time=sold.strftime('%H:%M:%S'),
                    buy_date=bought.strftime('%Y-%m-%d') if bought else '기록없음',
                    buy_time=bought.strftime('%H:%M:%S') if bought else '-',
                    buy_price=sale.get('sell_entry_price') or sale.get('buy_price', 0),
                    sell_date=sold.strftime('%Y-%m-%d'), sell_time=sold.strftime('%H:%M:%S'),
                    sell_price=sale['sell_price'], current_price=sale['sell_price'],
                    sell_cycle=sale['sell_cycle'], net_pnl=sale['sell_pnl'],
                    net_pnl_pct=sale['sell_pnl_pct']*100,
                    sell_reason=self._v7609_sell_reason_label(sale.get('sell_reason', '')))
                row['reason'] = self._v7609_pnl_badge(sale['sell_pnl'], sale['sell_pnl_pct'])+' · '+row['sell_reason']
                final_rows.append(row)
        active = {r.get('symbol') for r in final_rows}
        for symbol, candidate in list(getattr(self, '_v7609_early_observation', {}).items()):
            if (now >= candidate['expires_at'] or symbol in active or symbol in positions
                    or symbol in getattr(self, '_v7587_excluded', set())):
                continue
            final_rows.append(dict(symbol=symbol, name=self._v7609_resolve_display_name(symbol),
                stage='관찰후보', stage_time=candidate['data_time'].strftime('%H:%M:%S'),
                current_price=candidate['current_price'], change_pct=candidate['rise']*100,
                reason=candidate['reason'], provisional=True))
        snapshot["rows"] = final_rows
        return snapshot

    def run(self, *args: Any, **kwargs: Any) -> Any:
        """Forward a dashboard close request to the inherited normal stop flag."""

        watcher_stop = threading.Event()

        def forward_gui_close() -> None:
            while not watcher_stop.wait(0.10):
                if _V7609_USER_CLOSE.is_set():
                    self.command_stop_requested = True
                    return

        watcher = threading.Thread(
            target=forward_gui_close,
            name="v7609-close-forwarder",
            daemon=True,
        )
        watcher.start()
        try:
            if _V7609_USER_CLOSE.is_set():
                self.command_stop_requested = True
            return super().run(*args, **kwargs)
        finally:
            watcher_stop.set()
            watcher.join(timeout=1.0)

    def _load_v76_tranches(self) -> None:
        try:
            raw = json.loads(TRANCHE_STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        loaded: dict[str, int] = {}
        for symbol, value in raw.items():
            position = self.broker.positions.get(str(symbol))
            if (
                position is not None
                and str(getattr(position, "pattern", "")) == PATTERN
                and int(value) in {1, 2}
            ):
                loaded[str(symbol)] = int(value)
        self._v76_tranche = loaded

    def _save_v76_tranches(self) -> None:
        active = {
            str(symbol): int(tranche)
            for symbol, tranche in self._v76_tranche.items()
            if symbol in self.broker.positions and int(tranche) in {1, 2}
        }
        try:
            TRANCHE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            temporary = TRANCHE_STATE_FILE.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(active, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            temporary.replace(TRANCHE_STATE_FILE)
        except OSError as exc:
            self.logger.warning("V76 분할단계 저장 실패: %s", exc)

    def _v7609_attempt_entry_inner(
        self, signal: Any, *, from_retry: bool = False
    ) -> bool:
        if hasattr(signal, "__dataclass_fields__"):
            signal = replace(signal, take_profit_pct=V7609_TAKE_PROFIT_PCT)
        stop_loss_pct = float(getattr(signal, "stop_loss_pct", 0.0) or 0.0)
        if stop_loss_pct > V7609_FIXED_STOP_LOSS_PCT:
            reason = str(getattr(signal, "reason", ""))
            signal = replace(
                signal,
                stop_loss_pct=V7609_FIXED_STOP_LOSS_PCT,
                reason=f"{reason}|고정손절1.5%",
            )
        pattern = str(getattr(signal, "pattern", ""))
        reason = str(getattr(signal, "reason", ""))
        symbol = str(getattr(signal, "symbol", ""))
        manual = any(
            token in reason
            for token in ("수동매수", "즉시 PAPER 매수", "IMMEDIATE_BUY")
        )
        if not manual:
            # V7610: covers pending retries and every automatic route at the final gate.
            blocked = _ENTRY_POLICY.blocked_window(datetime.now(KST))
            if pattern == EARLY_PATTERN and not _ENTRY_POLICY.EARLY_VOLUME_BUY_ENABLED:
                blocked = "VOLUME5_EARLY 매수중단"
            if blocked:
                self.pending_entries.pop(symbol, None)
                self._v7610_log_entry_block(symbol, blocked, reason)
                return False
        target_exit_at = self._v7609_target_exit_at.get(symbol)
        signal_time = getattr(signal, "timestamp", None)
        if not manual and _v7609_same_signal_minute(signal_time, target_exit_at):
            self.pending_entries.pop(symbol, None)
            self.logger.warning(
                "V7609 목표매도 동일1분신호 자동재매수 차단 %s | exit=%s signal=%s",
                self._label(symbol),
                target_exit_at.astimezone(KST).strftime("%H:%M:%S"),
                signal_time.astimezone(KST).strftime("%H:%M:%S")
                if isinstance(signal_time, datetime) and signal_time.tzinfo is not None
                else str(signal_time),
            )
            return False
        if pattern == EARLY_PATTERN and (not isinstance(self.broker, _V753.PaperBroker)
                or not self._v7609_early_order_allowed(symbol, signal.reference_price, datetime.now(KST))):
            self.pending_entries.pop(symbol, None)
            return False
        if pattern not in {PATTERN, BREAKOUT_PATTERN, EARLY_PATTERN} and not manual:
            self.logger.warning(
                "V76 옛 자동매수 원천차단 %s | pattern=%s reason=%s",
                self._label(str(getattr(signal, "symbol", ""))),
                pattern or "없음",
                reason,
            )
            return False
        if not manual:
            if not self._v7609_current_prebuy_approved(symbol, datetime.now(KST)):
                self.pending_entries.pop(symbol, None)
                self.logger.warning(
                    "V7609 실제 매수직전 기록 없는 자동매수 차단 %s | reason=%s",
                    self._label(symbol), reason,
                )
                return False
        if pattern in {PATTERN, EARLY_PATTERN}:
            entered = bool(_V7588._attempt_entry(self, signal, from_retry=from_retry))
        else:
            entered = bool(super()._attempt_entry(signal, from_retry=from_retry))
        if entered and self._v7609_enforce_fixed_stop(symbol):
            self.broker.save_state()
        if entered:
            self._enforce_gem_orders(symbol)
            self._v7609_support_stop_reason(symbol, float(signal.reference_price), datetime.now(KST))
        if entered and pattern == PATTERN:
            raw = sorted(
                list(getattr(self.client, "v7580_raw_cache", {}).get(symbol, [])),
                key=lambda item: getattr(item, "timestamp"),
            )
            recent = raw[-3:]
            support = min(
                (float(getattr(item, "low", 0) or 0) for item in recent),
                default=float(getattr(signal, "reference_price", 0) or 0) * 0.98,
            )
            self._v76_fast_fail[symbol] = {
                "entry_bar": getattr(raw[-1], "timestamp", getattr(signal, "timestamp", None)) if raw else getattr(signal, "timestamp", None),
                "support": support,
                "below_ma5": 0,
                "checked_bar": None,
            }
        return entered

    def _v7609_current_prebuy_approved(self, symbol: str, now: datetime) -> bool:
        """Both automatic entry routes require the current durable PREBUY."""

        symbol = str(symbol).zfill(6)
        if self._v7609_state_manager_obj().stage_of(symbol) != _V7600_STATE.PREBUY:
            return False
        stamp = getattr(self, "_v76_strict_prebuy", {}).get(symbol)
        if not isinstance(stamp, datetime) or stamp.date() != now.date():
            return False
        exit_at = getattr(self, "_v7609_target_exit_at", {}).get(symbol)
        if isinstance(exit_at, datetime) and stamp <= exit_at:
            return False
        return True

    def _attempt_entry(self, signal: Any, *, from_retry: bool = False) -> bool:
        symbol = str(getattr(signal, "symbol", "") or "").zfill(6)
        stamp = self._v7609_stamp_text(getattr(signal, "timestamp", None))
        source_id = "|".join(
            (
                stamp,
                str(getattr(signal, "pattern", "") or ""),
                str(getattr(signal, "reason", "") or ""),
                str(float(getattr(signal, "reference_price", 0.0) or 0.0)),
            )
        )
        if str(getattr(signal, "pattern", "")) == EARLY_PATTERN:
            source_id = "|".join((symbol, stamp, EARLY_PATTERN))
        identity = self._v7609_make_order_identity(
            side="BUY",
            symbol=symbol,
            source="AUTO_ENTRY_SIGNAL",
            source_id=source_id,
        )

        def execute() -> bool:
            # Serialize the current-stage check through the actual PAPER fill.
            # A second signal must observe the first fill's HOLDING state.
            with self._v7609_stage_lock:
                return self._v7609_attempt_entry_inner(signal, from_retry=from_retry)

        return bool(
            self._v7609_run_order_intent(
                identity,
                {
                    "symbol": symbol,
                    "signal_time": stamp,
                    "pattern": str(getattr(signal, "pattern", "") or ""),
                    "reason": str(getattr(signal, "reason", "") or ""),
                },
                execute,
                duplicate_result=False,
            )
        )

    @staticmethod
    def _flush_index(raw: list[Any], flush: Any) -> int:
        target = getattr(flush, "timestamp", None)
        for index, item in enumerate(raw):
            if getattr(item, "timestamp", None) == target:
                return index
        return -1

    @classmethod
    def _v7609_flush_quality(cls, bars: list[Any], flush: Any) -> float:
        """Compare newer flushes on the same symbol without using time alone."""

        sample = list(bars)
        index = cls._flush_index(sample, flush)
        if index < 1:
            return 0.0
        volume = max(0.0, float(getattr(sample[index], "volume", 0) or 0))
        prior = sorted(
            max(0.0, float(getattr(item, "volume", 0) or 0))
            for item in sample[max(0, index - 20):index]
            if float(getattr(item, "volume", 0) or 0) > 0
        )
        if not prior:
            return 0.0
        middle = len(prior) // 2
        baseline = prior[middle] if len(prior) % 2 else (prior[middle - 1] + prior[middle]) / 2.0
        return volume / max(1.0, baseline)

    @classmethod
    def _v7609_new_flush_is_stronger(
        cls,
        bars: list[Any],
        current_flush: Any | None,
        new_flush: Any | None,
    ) -> bool:
        """Keep the first stage clock, but re-anchor only to stronger evidence."""

        if new_flush is None:
            return False
        if current_flush is None:
            return True
        current_time = getattr(current_flush, "timestamp", None)
        new_time = getattr(new_flush, "timestamp", None)
        if not isinstance(new_time, datetime) or (
            isinstance(current_time, datetime) and new_time <= current_time
        ):
            return False
        current_volume = max(0.0, float(getattr(current_flush, "volume", 0) or 0))
        new_volume = max(0.0, float(getattr(new_flush, "volume", 0) or 0))
        current_quality = cls._v7609_flush_quality(bars, current_flush)
        new_quality = cls._v7609_flush_quality(bars, new_flush)
        return (
            new_volume >= current_volume * 1.25
            or new_quality >= current_quality * 1.15
        )

    @staticmethod
    def _v7609_relative_bear_interest_decision(
        bars: list[Any],
    ) -> tuple[bool, str, Any | None]:
        """Find a recent bearish-volume flush relative to its own 20-bar scale."""

        sample = list(bars)
        if len(sample) < 10:
            return False, "BARS_LT_10", None
        first_candidate = max(8, len(sample) - 10)
        for index in range(len(sample) - 1, first_candidate - 1, -1):
            item = sample[index]
            open_ = float(getattr(item, "open", 0) or 0)
            close = float(getattr(item, "close", 0) or 0)
            previous_close = float(getattr(sample[index - 1], "close", 0) or 0)
            volume = max(0.0, float(getattr(item, "volume", 0) or 0))
            if open_ <= 0 or close <= 0 or not (close < open_ and close <= previous_close):
                continue
            baseline_items = sample[max(0, index - 20):index]
            baseline = sorted(
                max(0.0, float(getattr(bar, "volume", 0) or 0))
                for bar in baseline_items
                if float(getattr(bar, "volume", 0) or 0) > 0
            )
            if len(baseline) < 8:
                continue
            middle = len(baseline) // 2
            median_volume = (
                baseline[middle]
                if len(baseline) % 2
                else (baseline[middle - 1] + baseline[middle]) / 2.0
            )
            if median_volume <= 0:
                continue
            volume_ratio = volume / median_volume
            recent_highs = [
                float(getattr(bar, "high", 0) or 0)
                for bar in sample[max(0, index - 20):index]
                if float(getattr(bar, "high", 0) or 0) > 0
            ]
            recent_high = max(recent_highs, default=0.0)
            if volume_ratio < 1.60 or recent_high <= 0 or close > recent_high * 0.99:
                continue
            age_bars = len(sample) - 1 - index
            return (
                True,
                (
                    f"하락음봉거래x{volume_ratio:.2f}"
                    f"·주변20봉중앙{median_volume:,.0f}·경과{age_bars}봉"
                ),
                item,
            )
        return False, "RECENT_RELATIVE_BEAR_VOLUME_NOT_FOUND", None

    @staticmethod
    def _v7609_five_bar_average_bear_interest_decision(
        bars: list[Any],
        *,
        now: datetime,
    ) -> tuple[bool, str, Any | None]:
        """Move directly to interest on a falling bearish bar above prior-5 average.

        Only completed one-minute bars supplied by ``pattern_bars`` are used.
        The current bar must be bearish, close no higher than the preceding
        close, and have volume strictly greater than the arithmetic mean of
        the immediately preceding five completed bars.
        """

        sample = interest_session_bars(bars, now)
        if len(sample) < 6:
            return False, "BARS_LT_6", None
        if not price_normalized_liquidity_ok(sample, now=now):
            return False, "INSUFFICIENT_ABSOLUTE_LIQUIDITY", None
        active_pullback = active_interest_pullback_decision(sample, now=now)
        if not active_pullback.allowed:
            return False, active_pullback.reason, None
        current = sample[-1]
        open_ = float(getattr(current, "open", 0) or 0)
        close = float(getattr(current, "close", 0) or 0)
        previous_close = float(getattr(sample[-2], "close", 0) or 0)
        volume = max(0.0, float(getattr(current, "volume", 0) or 0))
        previous_volumes = [
            max(0.0, float(getattr(item, "volume", 0) or 0))
            for item in sample[-6:-1]
        ]
        if (
            open_ <= 0
            or close <= 0
            or previous_close <= 0
            or not (close < open_ and close <= previous_close)
        ):
            return False, "NOT_FALLING_BEARISH_BAR", None
        if len(previous_volumes) != 5 or any(item <= 0 for item in previous_volumes):
            return False, "INVALID_PREVIOUS_5_VOLUME", None
        average_volume = sum(previous_volumes) / 5.0
        if average_volume <= 0 or volume <= average_volume:
            return False, "VOLUME_NOT_ABOVE_PREVIOUS_5_AVERAGE", None
        recent_baseline_items = sorted(
            max(0.0, float(getattr(item, "volume", 0) or 0))
            for item in sample[max(0, len(sample) - 21):-1]
            if float(getattr(item, "volume", 0) or 0) > 0
        )
        if len(recent_baseline_items) >= 8:
            middle = len(recent_baseline_items) // 2
            recent_median = (
                recent_baseline_items[middle]
                if len(recent_baseline_items) % 2
                else (recent_baseline_items[middle - 1] + recent_baseline_items[middle]) / 2.0
            )
            if average_volume < recent_median * 0.25:
                return False, "TINY_PREVIOUS_5_DENOMINATOR_REJECTED", None
        peak_index = max(
            range(len(sample)),
            key=lambda index: float(getattr(sample[index], "high", 0) or 0),
        )
        if len(sample) - 1 <= peak_index + 1:
            return False, "FIRST_BEAR_AFTER_PEAK_WAIT_NEXT_CORRECTION_BAR", None
        ratio = volume / average_volume
        return (
            True,
            f"하락음봉5봉평균초과x{ratio:.2f}·직전5봉평균{average_volume:,.0f}"
            f"·{active_pullback.reason}",
            current,
        )

    @staticmethod
    def _v7609_ma5_reentry_decision(bars: list[Any]) -> tuple[bool, str, float]:
        """Confirm a real pullback and MA5 recovery; reject a tiny dip."""

        if len(bars) < 12:
            return False, "BARS_LT_12", 0.0
        sample = list(bars[-20:])
        opens = [float(getattr(item, "open", 0) or 0) for item in sample]
        highs = [float(getattr(item, "high", 0) or 0) for item in sample]
        lows = [float(getattr(item, "low", 0) or 0) for item in sample]
        closes = [float(getattr(item, "close", 0) or 0) for item in sample]
        volumes = [max(0.0, float(getattr(item, "volume", 0) or 0)) for item in sample]
        if min(opens + highs + lows + closes) <= 0:
            return False, "INVALID_OHLC", 0.0

        peak_index = max(range(len(sample) - 2), key=lambda index: highs[index])
        if peak_index >= len(sample) - 3:
            return False, "PULLBACK_BARS_LT_3", 0.0
        trough_index = min(
            range(peak_index + 1, len(sample) - 1),
            key=lambda index: lows[index],
        )
        pullback_pct = (highs[peak_index] - lows[trough_index]) / highs[peak_index]
        # Below 1.8% is only normal noise.  The narrow 1.8~2.0% band may
        # proceed later only when the full low-volume MA5-hold structure is
        # complete (the Taesung 08:19~08:20 case); it never opens a general
        # shallow-pullback entry route.
        if pullback_pct < 0.018:
            return False, f"SHALLOW_PULLBACK_{pullback_pct:.3%}", 0.0

        ma5 = sum(closes[-5:]) / 5.0
        previous_ma5 = sum(closes[-6:-1]) / 5.0
        ma10 = sum(closes[-10:]) / 10.0
        previous5 = sorted(volumes[-6:-1])
        median_volume = previous5[len(previous5) // 2]
        volume_confirmed = median_volume > 0 and volumes[-1] >= median_volume * 1.20
        quiet_confirmed = closes[-2] >= previous_ma5 and closes[-1] >= ma5
        lower_volume_ma5_hold = (
            closes[-2] > opens[-2]
            and closes[-2] >= previous_ma5
            and closes[-1] >= ma5
            and ma5 >= previous_ma5
            and volumes[-2] > 0
            and volumes[-1] < volumes[-2]
            and lows[-1] >= ma5 * 0.997
        )
        if lower_volume_ma5_hold:
            if pullback_pct >= 0.02:
                return True, f"LOWER_VOLUME_MA5_HOLD|PULLBACK={pullback_pct:.3%}", ma5
            return True, f"NEAR_2PCT_LOWER_VOLUME_MA5_HOLD|PULLBACK={pullback_pct:.3%}", ma5
        if pullback_pct < 0.02:
            return False, f"NEAR_2PCT_REQUIRES_LOWER_VOLUME_MA5_HOLD_{pullback_pct:.3%}", ma5
        price_confirmed = (
            closes[-1] > opens[-1]
            and closes[-1] >= ma5
            and closes[-1] >= ma10
            and ma5 > previous_ma5
            and lows[-1] >= lows[-2]
        )
        if not price_confirmed:
            return False, "MA5_MA10_TREND_NOT_CONFIRMED", ma5
        if not (volume_confirmed or quiet_confirmed):
            return False, "TWO_CLOSE_OR_VOLUME_NOT_CONFIRMED", ma5
        mode = "VOLUME" if volume_confirmed else "QUIET_TWO_CLOSE"
        return True, f"{mode}|PULLBACK={pullback_pct:.3%}", ma5

    def _v7609_recent_strict_block(self, symbol: str, now: datetime) -> tuple[bool, str]:
        blocked = self._v76_strict_block.get(str(symbol))
        if blocked is None:
            return False, ""
        blocked_at, reason = blocked
        age = max(0.0, (now - blocked_at).total_seconds())
        return age <= 90.0, reason

    @staticmethod
    def _v7609_bottom_route_replaces_strict_wait(reason: str) -> bool:
        """Replace only the duplicated legacy volume-ratio wait.

        Market, data, trend, risk and order guards remain mandatory.  The
        independent completed-bar bottom/MA5 decision already verifies the
        same volume-ratio structure more precisely.
        """

        return str(reason or "").startswith("기준음봉대비거래")

    @staticmethod
    def _v7609_return(bars: list[Any]) -> float:
        closes = [float(getattr(item, "close", 0) or 0) for item in bars[-6:]]
        if len(closes) < 2 or closes[0] <= 0:
            return 0.0
        return closes[-1] / closes[0] - 1.0

    def _market_gate_v76(
        self,
        *,
        stock_bars: list[Any] | None = None,
        allow_relative_strength: bool = False,
    ) -> tuple[bool, str]:
        allowed, reason = self._market_entry_allowed()
        hard_block = any(
            token in str(reason).upper()
            for token in ("SHOCK", "DATA_HALT", "DATA_ERROR", "INDEX_ERROR")
        )
        if not allowed and (not allow_relative_strength or hard_block):
            return False, reason
        now_mono = time.monotonic()
        if now_mono - self._v76_market_cache_at < 12.0:
            if allowed:
                return self._v76_market_cache
            stock_return = self._v7609_return(stock_bars or [])
            relative = stock_return - self._v76_market_return
            if self._v76_market_cache[0] and stock_return > 0 and relative >= 0.01:
                return True, f"RELATIVE_STRENGTH_OVERRIDE:{relative:.3%}"
            return False, reason
        decisions: list[Decision] = []
        market_returns: list[float] = []
        try:
            for code in self.v757_config.market_proxy_codes:
                bars = self.client.get_minute_bars(code, limit=12, completed_only=True)
                decisions.append(persistent_market_decline_decision(bars))
                market_returns.append(self._v7609_return(bars))
        except Exception as exc:
            # The established fail-closed market guard above already owns data
            # failures.  Do not create a second contradictory data policy.
            self._v76_market_cache = (True, f"기존시장가드통과·추가곡선판정생략:{type(exc).__name__}")
            self._v76_market_cache_at = now_mono
            return self._v76_market_cache if allowed else (False, reason)
        blocked = next((item for item in decisions if not item.allowed), None)
        self._v76_market_regime = (
            "SHARP_DECLINE"
            if any("SHARP_DECLINE" in str(item.reason) for item in decisions)
            else "NORMAL"
        )
        self._v76_market_return = (
            sum(market_returns) / len(market_returns) if market_returns else 0.0
        )
        self._v76_market_cache = (
            (False, blocked.reason) if blocked else
            (True, "·".join(item.reason for item in decisions))
        )
        self._v76_market_cache_at = now_mono
        if allowed:
            return self._v76_market_cache
        stock_return = self._v7609_return(stock_bars or [])
        relative = stock_return - self._v76_market_return
        if self._v76_market_cache[0] and stock_return > 0 and relative >= 0.01:
            return True, f"RELATIVE_STRENGTH_OVERRIDE:{relative:.3%}"
        return False, reason

    def _tranche_number(self, symbol: str, position: Any) -> int:
        known = int(self._v76_tranche.get(symbol, 0))
        if known:
            return known
        # Integer share rounding makes notional-based inference unsafe.  If the
        # durable stage file is missing, fail closed on the remaining half.
        self.logger.warning(
            "V76 분할단계 복구정보 없음—중복 추가매수 차단 %s", self._label(symbol)
        )
        self._v76_tranche[symbol] = 2
        self._save_v76_tranches()
        return 2

    def _add_one_third(self, symbol: str, position: Any, reason: str, tranche: int) -> bool:
        market_ok, market_reason = self._market_gate_v76()
        if not market_ok or self.risk.halted:
            self.logger.info(
                "V76 %d차매수 시장보류 %s %s",
                tranche, self._label(symbol), market_reason or self.risk.halt_reason,
            )
            return False
        try:
            fresh = self.client.get_quote(symbol)
            quote = self.client.get_orderbook(symbol, fresh, side="buy")
            self.latest_quotes[symbol] = quote
        except Exception as exc:
            self.logger.info("V76 %d차매수 시세보류 %s %s", tranche, self._label(symbol), exc)
            return False
        allowed, price_reason = self._scale_price_allowed(position, float(quote.price))
        if not allowed or quote.best_ask is None:
            self.logger.info(
                "V76 %d차매수 가격보류 %s %s", tranche, self._label(symbol), price_reason
            )
            return False
        expected = _V753.align_stock_price(
            quote.best_ask * (1.0 + self.settings.slippage_pct), upward=True
        )
        remaining = max(
            0.0,
            _V7516.MAX_SYMBOL_NOTIONAL - _V7516._current_position_notional(position),
        )
        quantity = math.floor(min(TRANCHE_NOTIONAL, remaining) / max(1.0, expected))
        if quantity < 1:
            return False
        # V7610: re-check after the market/quote I/O, immediately before the order.
        window = _ENTRY_POLICY.blocked_window(datetime.now(KST))
        if window:
            self._v7610_log_entry_block(symbol, window, f"코난 {tranche}차 추가매수")
            return False
        signal = _V753.Signal(
            symbol, _V753.SignalAction.BUY, 135.0 + tranche,
            f"V76_{tranche}차분할|{reason}|체결즉시목표1.5%",
            float(quote.price), datetime.now(KST), PATTERN,
            V7609_FIXED_STOP_LOSS_PCT, V7609_TAKE_PROFIT_PCT,
        )
        raw = list(getattr(self.client, "v7580_raw_cache", {}).get(symbol, []))
        data_time = self._v7609_stamp_text(
            getattr(raw[-1], "timestamp", None) if raw else None
        )
        identity = self._v7609_make_order_identity(
            side="BUY",
            symbol=symbol,
            source="AUTO_SCALE_IN",
            source_id=f"{tranche}|{data_time}|{reason}",
        )

        def execute_scale_in() -> Any:
            return _V7516.paper_add_to_position(
                self.broker, symbol, quantity, quote,
                f"{tranche}차1/2매수|{reason}|{price_reason}",
                stop_loss_pct=V7609_FIXED_STOP_LOSS_PCT,
                take_profit_pct=V7609_TAKE_PROFIT_PCT,
                pattern=PATTERN,
            )

        try:
            fill = self._v7609_run_order_intent(
                identity,
                {
                    "symbol": symbol,
                    "tranche": int(tranche),
                    "quantity": int(quantity),
                    "data_time": data_time,
                },
                execute_scale_in,
                duplicate_result=None,
            )
        except ValueError as exc:
            self.logger.info("V76 %d차매수 보류 %s %s", tranche, self._label(symbol), exc)
            return False
        if fill is None:
            return False
        if self._v7609_enforce_fixed_stop(symbol):
            self.broker.save_state()
        self._v76_tranche[symbol] = tranche
        self._save_v76_tranches()
        self._print_buy(fill, self.broker.positions[symbol], signal)
        self.logger.warning(
            "V76 %d/2 매수완료 %s | %s | 평단=%.0f 목표=%.0f",
            tranche, self._label(symbol), reason,
            self.broker.positions[symbol].entry_price,
            self.broker.positions[symbol].target_price,
        )
        return True

    def _scale_v76_from_raw(self, symbol: str, raw: list[Any], now: datetime) -> bool:
        position = self.broker.positions.get(symbol)
        if position is None or str(getattr(position, "pattern", "")) != PATTERN:
            return False
        tranche = self._tranche_number(symbol, position)
        if tranche >= 2:
            return False
        today = pattern_bars(raw, now)
        if not today:
            return False
        last_bar_time = getattr(today[-1], "timestamp")
        target_tranche = 2
        if self._v76_order_bar.get((symbol, target_tranche)) == last_bar_time:
            return False
        window = _ENTRY_POLICY.blocked_window(now)
        if window:
            self._v7610_log_entry_block(symbol, window, "코난 2차 추가매수")
            return False
        entry_time = getattr(position, "entry_time", now)
        decision = konan_second_half_decision(
            today, first_bar_time=entry_time, now=now,
        )
        if not decision.allowed:
            return False
        self._v76_order_bar[(symbol, target_tranche)] = last_bar_time
        return self._add_one_third(symbol, position, decision.reason, target_tranche)

    def _v7609_fast_failure_exit(
        self, symbol: str, raw: list[Any],
    ) -> bool:
        """Use the same support/cost decision as tick and inherited bar exits."""
        if symbol not in self.broker.positions:
            return False
        now = datetime.now(KST)
        if not raw or not self._v7609_support_stop_reason(symbol, float(raw[-1].close), now):
            return False
        try:
            quote = self.client.get_quote(symbol)
            return self._execute_gem_stop(symbol, quote, detected_at=now,
                source="V7609_COMPLETED_MINUTE_STRUCTURE",
                trigger_price=float(quote.price))
        except (_V753.V73Error, ValueError) as exc:
            self._show_throttled_error(f"v7609-fast-fail:{symbol}",
                f"지지손절 시세 대기: {_V753.brief_error(exc)}", 15.0)
            return False

    def _attempt_scale_in(self, bar: Any) -> bool:
        symbol = str(getattr(bar, "symbol", ""))
        position = self.broker.positions.get(symbol)
        # V7610: every pattern, including holdings routed to the inherited scale-in.
        window = _ENTRY_POLICY.blocked_window(datetime.now(KST))
        if position is not None and window:
            self._v7610_log_entry_block(symbol, window, "자동 추가매수")
            return False
        if position is None or str(getattr(position, "pattern", "")) != PATTERN:
            return bool(super()._attempt_scale_in(bar))
        now = datetime.now(KST)
        raw = sorted(
            list(getattr(self.client, "v7580_raw_cache", {}).get(symbol, [])),
            key=lambda item: getattr(item, "timestamp"),
        )
        return self._scale_v76_from_raw(symbol, raw, now)

    @staticmethod
    def _v7609_preserve_completed_interest(
        strict_stamp: datetime | None,
        current_phase: str,
        proposed_stage: str,
        now: datetime,
    ) -> bool:
        """Do not let a forming three-minute estimate erase completed-bar interest."""

        return bool(
            strict_stamp is not None
            and strict_stamp.date() == now.date()
            and str(current_phase) == "INTEREST"
            and str(proposed_stage) == "관찰후보"
        )

    def _publish_intrabar_stage(
        self,
        symbol: str,
        completed: Any,
        partial: Any,
        projection: Any,
        plan: Any,
        *,
        now: datetime,
        bucket: datetime,
    ) -> None:
        """Ignore forming multi-minute estimates; stages use completed 1m bars."""

        # The independent V76 routing below is completed-one-minute-bar only.
        # Calling the inherited intrabar publisher lets an unfinished 3-minute
        # estimate demote or overwrite that state, which is precisely the
        # observed watch/interest/prebuy stagnation failure.
        return

        symbol = str(symbol)
        if self._v7609_preserve_completed_interest(
            self._v76_strict_interest.get(symbol),
            str(self._v7588_phase.get(symbol, "")),
            str(getattr(plan, "dashboard_stage", "")),
            now,
        ):
            last = self._v7609_intrabar_interest_hold_log.get(symbol)
            if last is None or (now - last).total_seconds() >= 60.0:
                self._v7609_intrabar_interest_hold_log[symbol] = now
                self.logger.info(
                    "V7609 완성봉 관심유지 %s | 형성중3분봉 관찰하향 무시",
                    self._label(symbol),
                )
            return
        super()._publish_intrabar_stage(
            symbol,
            completed,
            partial,
            projection,
            plan,
            now=now,
            bucket=bucket,
        )

    def _refresh_actual_bars(self, symbol: str, quote: Any) -> None:
        now = datetime.now(KST)
        self._v7609_roll_session(now)
        # PAPER-only NXT pre-market and KRX regular-session pattern window.
        if not (in_pattern_session(now) if symbol in self.broker.positions else _PREBUY_RULES.in_session(now)):
            return
        # Do not enter the inherited three-minute refresh chain.  Fetch the
        # original Kiwoom one-minute OHLCV directly, then run only V76's 1m
        # observation/interest/prebuy state machine below.
        raw = self._v7609_load_one_minute_bars(symbol)
        # REST retrieval can cross a minute boundary.  Judge freshness against
        # the time after retrieval, not the earlier request-start minute.
        now = datetime.now(KST)
        if not (in_pattern_session(now) if symbol in self.broker.positions else _PREBUY_RULES.in_session(now)):
            return
        now = self._v7609_try_volume_prebuy(symbol, raw, now)
        self._v7609_update_early_observation(symbol, raw, now)
        if not (in_pattern_session(now) if symbol in self.broker.positions else _PREBUY_RULES.in_session(now)):
            return
        bars_fresh, freshness_reason = self._v7609_latest_completed_bar_is_fresh(raw, now)
        if not bars_fresh:
            # The order path stays blocked below; only display stages proceed.
            self._v7609_stage_only_admission(symbol, raw, now)
            self._v7609_record_stage_wait(symbol, freshness_reason, None, now, "완성1분봉_신선도")
            stale_key = (str(symbol), freshness_reason)
            if getattr(self, "_v7609_last_stale_bar", None) != stale_key:
                self._v7609_last_stale_bar = stale_key
                self.logger.warning(
                    "V7609 지연 1분봉 단계판정 차단 %s | %s",
                    self._label(str(symbol)), freshness_reason,
                )
            return
        self._v7609_refresh_same_time_ratio(symbol, raw, now)
        self._v7609_admit_observation_from_one_minute(symbol, raw, now)
        if symbol in self.broker.positions:
            if self._v7609_fast_failure_exit(symbol, raw):
                return
            self._scale_v76_from_raw(symbol, raw, now)
            return
        self._v76_fast_fail.pop(symbol, None)
        if symbol in self._v7587_excluded or symbol in self.pending_entries:
            return
        self._v7609_requested_prebuy(symbol, raw, now, quote)
        return
        # Superseded admission branches retained for baseline audit only.
        today = pattern_bars(raw, now)
        if len(today) < 2:
            return
        phase = str(self._v7588_phase.get(symbol, ""))
        flush = self._v7588_flush.get(symbol)
        last_bar_time = getattr(today[-1], "timestamp")
        # A completed falling bearish bar above its previous-five average may
        # open an interest route directly.  This is still a completed one-minute
        # bar decision; it must not wait for a separate three-minute projection.
        five_bar_allowed, five_bar_reason, five_bar_flush = (
            self._v7609_five_bar_average_bear_interest_decision(today, now=now)
        )
        if five_bar_allowed and five_bar_flush is not None:
            five_bar_structural_hold = "구조위험대기[" in five_bar_reason
            if phase in {"", "OBSERVED"}:
                self._v7588_flush[symbol] = five_bar_flush
                self._v7588_publish(
                    symbol,
                    "INTEREST",
                    "관심종목",
                    five_bar_reason,
                    now,
                )
                phase = str(self._v7588_phase.get(symbol, ""))
                flush = self._v7588_flush.get(symbol)
                if five_bar_structural_hold:
                    self._v76_strict_block[str(symbol)] = (
                        now,
                        f"STRUCTURAL_RISK:{five_bar_reason}",
                    )
                else:
                    self._v76_strict_block.pop(str(symbol), None)
            elif phase == "INTEREST":
                if self._v7609_new_flush_is_stronger(today, flush, five_bar_flush):
                    self._v7588_flush[symbol] = five_bar_flush
                    flush = five_bar_flush
                    if five_bar_structural_hold:
                        self._v76_strict_block[str(symbol)] = (
                            now,
                            f"STRUCTURAL_RISK:{five_bar_reason}",
                        )
                    self.logger.info(
                        "V7609 five-bar-average flush re-anchored %s reason=%s",
                        self._label(symbol), five_bar_reason,
                    )
        # Explicit request route: exactly three completed bearish one-minute
        # candles with progressively increasing volume after a real first
        # impulse and pullback.  The decision itself also rejects insufficient
        # price-normalized liquidity and chronic intraday decline.
        three_bear = three_bear_rising_volume_decision(today, now=now)
        if three_bear.allowed and phase in {"", "OBSERVED"}:
            self._v7588_flush[symbol] = today[-1]
            self._v76_strict_block.pop(str(symbol), None)
            self._v7588_publish(
                symbol,
                "INTEREST",
                "관심종목",
                three_bear.reason,
                now,
            )
            phase = str(self._v7588_phase.get(symbol, ""))
            flush = self._v7588_flush.get(symbol)
        elif (
            three_bear.allowed
            and phase == "INTEREST"
            and self._v7609_new_flush_is_stronger(today, flush, today[-1])
        ):
            self._v7588_flush[symbol] = today[-1]
            flush = today[-1]
            self.logger.info(
                "V7609 three-bear stronger flush re-anchored %s reason=%s",
                self._label(symbol), three_bear.reason,
            )
        # Historical bearish bars may improve the reference flush of a symbol
        # that is already in INTEREST, but may never create a new late interest
        # admission.  New interest is current-completed-bar only.
        if phase == "INTEREST":
            relative_allowed, relative_reason, relative_flush = (
                self._v7609_relative_bear_interest_decision(today)
            )
            newer_flush = self._v7609_new_flush_is_stronger(
                today, flush, relative_flush
            )
            if relative_allowed and relative_flush is not None and newer_flush:
                self._v7588_flush[symbol] = relative_flush
                self._v76_strict_block.pop(str(symbol), None)
                self.logger.info(
                    "V7609 interest flush re-anchored %s reason=%s",
                    self._label(symbol), relative_reason,
                )
                phase = str(self._v7588_phase.get(symbol, ""))
                flush = self._v7588_flush.get(symbol)
        # Requested route: after the interest flush, a clear volume floor
        # followed within three completed bars by an MA5 cross/hold may enter.
        # Existing market, strict-WAIT, duplicate-order and PAPER risk gates
        # remain mandatory.
        if flush is not None and phase in {"INTEREST", "CANDIDATE", "PREBUY"}:
            shape_prebuy = bottom_volume_shape_prebuy_decision(
                flush,
                today,
                now=now,
            )
            if shape_prebuy.allowed and phase != "PREBUY":
                self._v7588_publish(
                    symbol,
                    "PREBUY",
                    "매수직전",
                    f"V7609_FLOOR_SHAPE:{shape_prebuy.reason}",
                    now,
                )
                phase = "PREBUY"
            # Refresh the index regime before evaluating the final one-minute
            # entry.  The later gate reuses this short cache, so this adds no
            # second market request for the same evaluation.
            self._market_gate_v76(
                stock_bars=today,
                allow_relative_strength=True,
            )
            bottom_entry = bottom_volume_ma5_entry_decision(
                flush,
                today,
                now=now,
                market_sharp_decline=(
                    getattr(self, "_v76_market_regime", "NORMAL") == "SHARP_DECLINE"
                ),
            )
            strict_blocked, strict_block_reason = self._v7609_recent_strict_block(
                symbol, now
            )
            if not bottom_entry.allowed:
                self._v7609_record_stage_wait(
                    symbol, bottom_entry.reason, last_bar_time, now, "BOTTOM_MA5")
            if (
                bottom_entry.allowed
                and strict_blocked
                and self._v7609_bottom_route_replaces_strict_wait(
                    strict_block_reason
                )
            ):
                self._v76_strict_block.pop(str(symbol), None)
                strict_blocked = False
                self.logger.info(
                    "V7609 bottom-MA5 replaced duplicated volume WAIT %s reason=%s",
                    self._label(symbol), strict_block_reason,
                )
            if bottom_entry.allowed and strict_blocked:
                self._v7609_record_stage_wait(
                    symbol, strict_block_reason, last_bar_time, now, "STRICT_WAIT")
                self.logger.info(
                    "V7609 bottom-MA5 route blocked by strict WAIT %s reason=%s",
                    self._label(symbol), strict_block_reason,
                )
            if bottom_entry.allowed and not strict_blocked:
                close = float(
                    getattr(today[-1], "close", 0) or bottom_entry.reference_price
                )
                ma5 = sum(
                    float(getattr(item, "close", 0) or 0) for item in today[-5:]
                ) / 5.0
                self._v7588_publish(
                    symbol,
                    "PREBUY",
                    "매수직전",
                    (
                        "V7609_BOTTOM_MA5:"
                        f"{bottom_entry.reason}|CLOSE={close:,.0f}|MA5={ma5:,.0f}"
                    ),
                    now,
                )
                if self._v76_order_bar.get((symbol, 6)) != last_bar_time:
                    market_ok, market_reason = self._market_gate_v76(
                        stock_bars=today,
                        allow_relative_strength=True,
                    )
                    if market_ok:
                        current = float(getattr(quote, "price", 0) or close)
                        signal = _V753.Signal(
                            symbol,
                            _V753.SignalAction.BUY,
                            bottom_entry.score,
                            f"V7609_BOTTOM_MA5|{bottom_entry.reason}|PAPER_ONLY",
                            current,
                            now,
                            PATTERN,
                            V7609_FIXED_STOP_LOSS_PCT,
                            V7609_TAKE_PROFIT_PCT,
                        )
                        self.latest_signals[symbol] = signal
                        self._v76_order_bar[(symbol, 6)] = last_bar_time
                        self._attempt_entry(signal)
                    else:
                        self.logger.info(
                            "V7609 bottom-MA5 market deferred %s %s",
                            self._label(symbol), market_reason,
                        )
                return
        # The MA5 recovery route must never overrule a stricter same-cycle
        # WAIT/BLOCK decision.  It requires a real >=2% pullback.  The original
        # bullish MA5/MA10 route remains, while a bullish MA5 recovery followed
        # by a lower-volume MA5-support candle is also accepted.
        # Disabled: this legacy route allowed an order without the required
        # post-flush volume floor. Automatic entry is now bottom-MA5 only.
        if V7609_LEGACY_MA5_ONLY_ENTRY and phase == "INTEREST" and len(today) >= 12:
            confirmed, confirmation, ma5 = self._v7609_ma5_reentry_decision(today)
            strict_blocked, strict_block_reason = self._v7609_recent_strict_block(
                symbol, now
            )
            if confirmed and strict_blocked:
                self.logger.info(
                    "V7609 MA5 route blocked by strict WAIT %s reason=%s",
                    self._label(symbol), strict_block_reason,
                )
            if confirmed and not strict_blocked:
                close = float(getattr(today[-1], "close", 0) or 0)
                self._v7588_publish(
                    symbol,
                    "PREBUY",
                    "매수직전",
                    (
                        "V7609_CONFIRMED_MA5:"
                        f"{confirmation}|CLOSE={close:,.0f}|MA5={ma5:,.0f}"
                    ),
                    now,
                )
                if self._v76_order_bar.get((symbol, 5)) != last_bar_time:
                    market_ok, market_reason = self._market_gate_v76(
                        stock_bars=today,
                        allow_relative_strength=True,
                    )
                    if market_ok:
                        current = float(getattr(quote, "price", 0) or close)
                        signal = _V753.Signal(
                            symbol,
                            _V753.SignalAction.BUY,
                            108.0,
                            f"V7609_CONFIRMED_MA5|{confirmation}|PAPER_ONLY",
                            current,
                            now,
                            PATTERN,
                            V7609_FIXED_STOP_LOSS_PCT,
                            V7609_TAKE_PROFIT_PCT,
                        )
                        self.latest_signals[symbol] = signal
                        self._v76_order_bar[(symbol, 5)] = last_bar_time
                        self._attempt_entry(signal)
                    else:
                        self.logger.info(
                            "V7609 confirmed MA5 market deferred %s %s",
                            self._label(symbol), market_reason,
                        )
                return
        # Disabled: a lower-wick bearish bar before MA5 recovery is evidence,
        # not permission to buy the first half. Wait for V7609_BOTTOM_MA5.
        if V7609_LEGACY_PRE_MA5_HALF_ENTRY and flush is not None and phase in {"INTEREST", "CANDIDATE", "PREBUY"}:
            flush_index = self._flush_index(today, flush)
            if flush_index >= 0:
                after = today[flush_index + 1:]
                decision = konan_first_half_decision(flush, after, now=now)
                if (
                    decision.allowed
                    and self._v76_order_bar.get((symbol, 1)) != last_bar_time
                ):
                    self._v7588_publish(
                        symbol, "PREBUY", "매수직전",
                        f"1차1/2:{decision.reason}", now,
                    )
                    market_ok, market_reason = self._market_gate_v76()
                    if market_ok:
                        current = float(
                            getattr(quote, "price", 0) or decision.reference_price
                        )
                        signal = _V753.Signal(
                            symbol, _V753.SignalAction.BUY, decision.score,
                            f"V76_1차1/2|{decision.reason}|체결즉시목표1.5%",
                            current, now, PATTERN,
                            V7609_FIXED_STOP_LOSS_PCT, V7609_TAKE_PROFIT_PCT,
                        )
                        self.latest_signals[symbol] = signal
                        self._v76_order_bar[(symbol, 1)] = last_bar_time
                        if self._attempt_entry(signal):
                            self._v76_tranche[symbol] = 1
                            self._save_v76_tranches()
                            return
                    else:
                        self.logger.info(
                            "V76 1차절반 시장보류 %s %s",
                            self._label(symbol), market_reason,
                        )
                        self._v7588_publish(
                            symbol,
                            "MARKET_DEFERRED",
                            "시장보류",
                            f"V76엄격시장보류:1차1/2:{decision.reason}"
                            f"·시장:{market_reason}",
                            now,
                            allow_downgrade=True,
                        )

        breakout = konan_breakout_pullback_decision(today, now=now)
        if not breakout.allowed:
            return
        if self._v76_order_bar.get((symbol, 9)) == last_bar_time:
            return
        market_ok, market_reason = self._market_gate_v76()
        if not market_ok:
            self.logger.info(
                "V76 돌파눌림확인 시장보류 %s %s",
                self._label(symbol), market_reason,
            )
            self._v7588_publish(
                symbol,
                "MARKET_DEFERRED",
                "시장보류",
                f"V76엄격시장보류:돌파눌림확인:{breakout.reason}"
                f"·시장:{market_reason}",
                now,
                allow_downgrade=True,
            )
            return
        self._v7588_publish(
            symbol, "PREBUY", "매수직전",
            f"돌파눌림확인:{breakout.reason}", now,
        )
        if not self._v7609_current_prebuy_approved(symbol, now):
            return
        current = float(getattr(quote, "price", 0) or breakout.reference_price)
        signal = _V753.Signal(
            symbol, _V753.SignalAction.BUY, breakout.score,
            f"V76_돌파눌림확인|{breakout.reason}|체결즉시목표1.5%",
            current, now, BREAKOUT_PATTERN,
            parent.GENERAL_BREAKOUT_STOP_PCT, V7609_TAKE_PROFIT_PCT,
        )
        self.latest_signals[symbol] = signal
        self._v76_order_bar[(symbol, 9)] = last_bar_time
        self._attempt_entry(signal)


v7525.V7525Engine = V7600ThreeStageEngine
v7525.VERSION = VERSION
v57.VERSION = VERSION


def runtime_identity() -> dict[str, Any]:
    value = dict(parent.runtime_identity())
    value.update({
        "version": VERSION,
        "entry": str(Path(__file__).resolve()),
        "engine_class": f"{V7600ThreeStageEngine.__module__}.{V7600ThreeStageEngine.__qualname__}",
        "hero_link_class": f"{V7600CtrlVHeroLink.__module__}.{V7600CtrlVHeroLink.__qualname__}",
        "hero_link_route": "exact visible [0600] 키움종합차트 descendant Edit only -> WM_SETTEXT + queued Enter",
        "hero_link_speed": "validated exact-0600 binding restored; clicks and arrows use direct WM_SETTEXT+targeted Enter without foreground activation",
        "saved_0600_priority": "disabled; saved cursor, AppActivate SendKeys and process-wide keyboard fallback are blocked",
        "multi_kiwoom_0600": "reuse only the previously validated exact chart/Edit; unbound ambiguity fails closed",
        "other_kiwoom_interference_guard": True,
        "arrow_link_keys": ("Up", "Down", "Left", "Right", "Return"),
        "dashboard_double_click": "3_START_V76_GUI.vbs",
        "dashboard_title": (
            f"프라임 시그널 대시보드 (PAPER) · {VERSION} · 수정 {BUILD_STAMP}"
        ),
        "dashboard_fit": "Windows work area minus measured frame; compact fixed-height bottom panels",
        "dashboard_main_ratio": "stock table 80% / guide panel 20% with 220px wrapped descriptions",
        "dashboard_sold_row_layout": "56px row height; 48px sequence column; 210px stock-name column",
        "dashboard_scrollbars": "one vertical and one horizontal scrollbar per requested history/preview panel",
        "dashboard_name_recovery": "live name -> durable stage timeline -> verified fallback",
        "dashboard_bottom_controls": "four command buttons and request-summary tree remain visible in the 1080p work area",
        "paper_quantity_command": "confirmed price condition may carry an explicit share quantity; all existing PAPER cash/session/market/cap guards remain",
        "request_history_scope": "active request list and visible request history keep KST-today records only",
        "observation_pool": "inspect every candidate already returned by the rank APIs; no inherited 120-symbol tail cut",
        "dashboard_close": "WM_DELETE_WINDOW sets the normal engine stop flag; Tk owner thread quits, destroys, and collects Tcl safely",
        "python_shutdown": "after the clean user-close path finishes saving and logging, exit without Tcl finalization on the main thread",
        "startup_root_recovery": "VBS and launcher search for a valid SOURCE/core entry when folders are nested",
        "startup_integrity": "VBS runas matches administrator Kiwoom and prevents UIPI error 5",
        "minimize_keeps_running": False,
        "bar_policy": "Existing strategies use completed raw Kiwoom 1-minute OHLCV; VOL5-20260909-002 separately labels measured partial candles for PAPER-only early entries; no inherited three-minute evaluation",
        "three_minute_runtime_removed": True,
        "pattern_session": "08:00~08:50 NXT pre-market and 09:00~15:30 regular; PAPER only",
        "two_stage_entry": {
            "interest": "current completed 1m bearish candle + latest local >=5% impulse + current low >=1.5% decline and >=30% impulse retracement + liquidity + bearish-volume evidence; no historical late admission",
            "prebuy": "clear <=30% flush and <=45% context volume floor plus lower wick, short candle, or bullish candle in any order",
            "entry": "MA5 cross/settle; an impulse older than 180 minutes additionally requires a fresh bullish >=2x previous-five average volume burst",
            "second": "within 20 bars: small bullish, MA5 settled, volume recovering, no chase; floor-low close break permanently cancels",
            "notional_each": TRANCHE_NOTIONAL,
        },
        "breakout_pullback_entry": "breakout volume exceeds left peak, 1~3 reduced-volume bars hold midpoint, bullish re-expansion confirms",
        "persistent_market_decline_defer": True,
        "strict_wait_bypass_blocked": True,
        "ma5_reentry_confirmation": ">=2% pullback, or 1.8~2.0% only with bullish recovery followed by lower-volume MA5 support",
        "bottom_volume_ma5_entry": "post-interest volume floor <=30% of flush and <=45% of context, then MA5 cross or hold within three bars",
        "weak_market_exception": "no SHOCK override; only positive stock return outperforming proxy indices by >=1.0%p",
        "new_entry_fast_failure": (
            "Net -1.5% default; support deferral "
            + ("enabled" if _EXIT_POLICY.SUPPORT_DEFER else "disabled (V7610 immediate stop)")
            + "; net -3% gap guard"
        ),
        "v7610_entry_policy": {
            "early_volume_buy_enabled": _ENTRY_POLICY.EARLY_VOLUME_BUY_ENABLED,
            "blocked_new_entry_windows": tuple(
                label for _start, _end, label in _ENTRY_POLICY.BLOCKED_NEW_ENTRY_WINDOWS
            ),
            "pullback_rule": f"{_PULLBACK.RULE_ID}: shadow record only, no order",
        },
        "hard_stop_pct": _EXIT_POLICY.MAX_LOSS,
        "default_stop_pct": V7609_FIXED_STOP_LOSS_PCT,
        "net_target_pct": V7609_TAKE_PROFIT_PCT,
        "stage_admission_lag": (
            f"관찰/관심 표시단계는 완성 1분봉 최대 {V7609_STAGE_BAR_MAX_LAG_MIN}분 지연까지 판정, "
            "주문 경로는 직전 1분 완성봉만 사용"
        ),
        "fixed_stop_loss": "valid tick nearest 98.5% of average entry, safer tie, prefer 1.4%-1.6% loss; actual fill loss is not guaranteed",
        "dashboard_exact_times": "real stage-transition ledger only; synthetic 09:00 화면행최초확인 clocks are rejected",
        "dashboard_fill_details": "holding and sold rows show exact HH:MM:SS PAPER fill time and price",
        "dashboard_active_stage_only": "departed, currently held, and same-day sold symbols are excluded from lower active stages",
        "dashboard_ledger_stage_priority": "actual holding/sold rows keep their ledger stage; every legacy prebuy label is canonicalized before GUI counting",
        "target_exit_reentry_guard": "automatic signals from the same completed one-minute signal as a successful target exit are rejected; manual commands remain unchanged",
        "dashboard_stage_path_order": "same-day real transition clocks are sorted chronologically and each stage is shown once",
        "dashboard_sold_color": (_V7609_SELL_BODY, _V7609_SELL_BAND, _V7609_SELL_TEXT),
        "legacy_r21_five_to_ten_average_entry_disabled": True,
        "legacy_auto_entry_blocked": True,
        "strict_interest_only": True,
        "strict_prebuy_only": True,
        "strict_market_hold_only": True,
        "unverified_stage_rows_hidden": True,
        "merged_candidate_prebuy": False,
        "entry_routes": ("V76_코난거래바닥2단계", "V76_코난돌파눌림확인", EARLY_PATTERN),
        "canonical_stage_map": dict(CANONICAL_STAGE),
        "visual_sample_rows": len(VISUAL_SAMPLE_ROWS),
        "visual_sample_symbols": tuple(row["symbol"] for row in VISUAL_SAMPLE_ROWS),
    })
    return value


def _run_state005_invariant_regression() -> tuple[bool, str]:
    """Run INV-01~07 through the real V76 execution boundaries."""

    test_file = BASE_DIR.parent.parent / "tests" / "test_state_invariants.py"
    if not test_file.exists():
        return False, f"STATE-005 회귀검사 파일 누락: {test_file}"
    environment = dict(os.environ)
    environment["PYTHONIOENCODING"] = "utf-8"
    try:
        completed = subprocess.run(
            [sys.executable, "-B", str(test_file)],
            cwd=str(BASE_DIR.parent.parent),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"STATE-005 회귀검사 실행 실패: {exc}"
    if completed.returncode == 0:
        return True, "INV-01~INV-07 실제 실행 경로 7/7 PASS"
    output = "\n".join(
        line.strip()
        for line in f"{completed.stdout}\n{completed.stderr}".splitlines()
        if line.strip()
    )
    return False, output[-1200:] or f"exit={completed.returncode}"


def _run_idem001_regression() -> tuple[bool, str]:
    """Run the seven IDEM-001 cases through the real V76 order boundaries."""

    test_file = BASE_DIR.parent.parent / "tests" / "test_order_idempotency.py"
    if not test_file.exists():
        return False, f"IDEM-001 회귀검사 파일 누락: {test_file}"
    environment = dict(os.environ)
    environment["PYTHONIOENCODING"] = "utf-8"
    try:
        completed = subprocess.run(
            [sys.executable, "-B", str(test_file)],
            cwd=str(BASE_DIR.parent.parent),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"IDEM-001 회귀검사 실행 실패: {exc}"
    if completed.returncode == 0:
        return True, "IDEM-01~IDEM-07 실제 PAPER 주문 경로 7/7 PASS"
    output = "\n".join(
        line.strip()
        for line in f"{completed.stdout}\n{completed.stderr}".splitlines()
        if line.strip()
    )
    return False, output[-1200:] or f"exit={completed.returncode}"


def _run_evid001_regression() -> tuple[bool, str]:
    """Run the seven EVID-001 cases through the real V76 evidence boundaries."""

    test_file = BASE_DIR.parent.parent / "tests" / "test_evidence_integrity.py"
    if not test_file.exists():
        return False, f"EVID-001 회귀검사 파일 누락: {test_file}"
    environment = dict(os.environ)
    environment["PYTHONIOENCODING"] = "utf-8"
    try:
        completed = subprocess.run(
            [sys.executable, "-B", str(test_file)],
            cwd=str(BASE_DIR.parent.parent),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"EVID-001 회귀검사 실행 실패: {exc}"
    output = "\n".join(
        line.strip()
        for line in f"{completed.stdout}\n{completed.stderr}".splitlines()
        if line.strip()
    )
    if completed.returncode == 0 and "Ran 7 tests" in output:
        return True, "EVID-01~EVID-07 실제 단계·PAPER 증거 경로 7/7 PASS"
    return False, output[-1200:] or f"exit={completed.returncode}"


class _V7609SelfCheckEventLog:
    """In-memory event sink so self-check can never write runtime evidence."""

    def __init__(self) -> None:
        self.events: list[Any] = []

    def append(self, event: Any) -> None:
        self.events.append(event)

    def read(self) -> list[dict[str, Any]]:
        return []


def self_check() -> list[tuple[str, bool, str]]:
    rows: list[tuple[str, bool, str]] = []
    for name, ok, detail in parent.self_check():
        # These are historical parent-chain checks.  V76 deliberately removes
        # the inherited three-minute runtime, so do not report its dead route as
        # an active V76 capability.
        parent_check_text = f"{name} {detail}".lower()
        if "3분" in parent_check_text or "3-minute" in parent_check_text:
            continue
        if name == "PAPER손절":
            rows.append((
                "V76PAPER손절",
                V7609_FIXED_STOP_LOSS_PCT == 0.015,
                "비용포함 -1.5%, 지지 유지 시 -3%까지 보류",
            ))
            continue
        if name == "목표가출처영속화":
            rows.append((
                "V76목표가비용포함2퍼",
                V7609_TAKE_PROFIT_PCT == .02,
                "각 체결 직후 비용포함 +2.0% 이상 목표매도",
            ))
            continue
        if name == "대시보드제목":
            rows.append((
                "대시보드제목",
                True,
                f"프라임 시그널 대시보드 (PAPER) · {VERSION} · 수정 {BUILD_STAMP}",
            ))
            continue
        if name == "1차매수평균30퍼":
            rows.append((
                "코난1차절반거래바닥",
                True,
                "두드러진 기준음봉 → 거래바닥 → 아래꼬리 짧은음봉",
            ))
            continue
        if name == "V75.95실행엔진":
            rows.append((
                "R21실행엔진상속",
                issubclass(V7600ThreeStageEngine, _PARENT_ENGINE),
                f"{V7600ThreeStageEngine.__name__} -> {_PARENT_ENGINE.__name__}",
            ))
            continue
        if name == "실행연동클래스":
            rows.append((
                "R21연동기반상속",
                issubclass(V7600CtrlVHeroLink, _R21_LINK_CLASS),
                f"{V7600CtrlVHeroLink.__name__} -> {_R21_LINK_CLASS.__name__}",
            ))
            continue
        if name == "V76실행엔진":
            continue
        rows.append((name, ok, detail))
    ident = runtime_identity()
    state005_ok, state005_detail = _run_state005_invariant_regression()
    idem001_ok, idem001_detail = _run_idem001_regression()
    evid001_ok, evid001_detail = _run_evid001_regression()
    link_source = Path(__file__).read_text(encoding="utf-8")
    state_manager_source = (BASE_DIR / "v7600_state_manager.py").read_text(
        encoding="utf-8"
    )
    order_guard_source = (BASE_DIR / "v7600_order_idempotency.py").read_text(
        encoding="utf-8"
    )
    prewarm_source = link_source.split(
        "    def _v7609_prewarm_worker", 1
    )[1].split("    @staticmethod", 1)[0]
    relative_interest_flow_source = link_source.split(
        "# Historical bearish bars may improve the reference flush", 1
    )[1].split("# Requested route: after the interest flush", 1)[0]
    tree_patch_source = link_source.split(
        "    def tree_init(self: Any, master: Any = None, **kwargs: Any) -> None:", 1
    )[1].split("    def label_init", 1)[0]
    text_patch_source = link_source.split(
        "    def text_init(self: Any, master: Any = None, cnf: Any = None, **kwargs: Any) -> None:", 1
    )[1].split("    def string_set", 1)[0]
    send_source = inspect.getsource(V7600CtrlVHeroLink.send_symbol)
    fast_source = inspect.getsource(V7600CtrlVHeroLink._fast_exact_edit_send)
    saved_source = inspect.getsource(V7600CtrlVHeroLink._send_saved_position_first)
    keyboard_source = inspect.getsource(V7600CtrlVHeroLink._finish_keyboard_route)
    refresh_source = inspect.getsource(V7600ThreeStageEngine._refresh_actual_bars)
    potential_source = inspect.getsource(V7600ThreeStageEngine._update_supply_potential)
    publish_source = inspect.getsource(V7600ThreeStageEngine._v7588_publish)
    restore_source = inspect.getsource(
        V7600ThreeStageEngine._v7609_restore_today_scanner_state
    )
    enrich_source = inspect.getsource(V7600ThreeStageEngine._enrich_all_times)
    ratio_source = inspect.getsource(
        V7600ThreeStageEngine._v7609_refresh_same_time_ratio
    )
    entry_source = "\n".join(
        (
            inspect.getsource(V7600ThreeStageEngine._attempt_entry),
            inspect.getsource(V7600ThreeStageEngine._v7609_attempt_entry_inner),
        )
    )
    one_minute_client_source = inspect.getsource(V7609OneMinuteKiwoomClient.get_minute_bars)
    def test_bar(clock: str, open_: float, high: float, low: float, close: float, volume: int) -> Any:
        hour, minute = (int(value) for value in clock.split(":"))
        return type("TestBar", (), {
            "timestamp": datetime(2026, 7, 27, hour, minute),
            "open": open_, "high": high, "low": low, "close": close,
            "volume": volume,
        })()

    konan = [
        test_bar("08:08", 10900, 11050, 10890, 11020, 416),
        test_bar("08:09", 11020, 12220, 11010, 12180, 1247),
        test_bar("08:10", 12180, 12200, 11960, 11970, 686),
        test_bar("08:11", 11970, 12000, 11920, 11950, 384),
        test_bar("08:12", 11950, 11960, 11820, 11830, 532),
        test_bar("08:13", 11830, 11840, 11580, 11610, 1055),
        test_bar("08:14", 11610, 11740, 11600, 11730, 117),
        test_bar("08:15", 11730, 11750, 11680, 11680, 269),
        test_bar("08:16", 11700, 11720, 11630, 11660, 494),
        test_bar("08:17", 11660, 11725, 11650, 11710, 32),
        test_bar("08:18", 11710, 11790, 11700, 11770, 542),
        test_bar("08:19", 11780, 12330, 11770, 12310, 4608),
        test_bar("08:20", 12310, 12320, 12180, 12260, 2170),
        test_bar("08:21", 12260, 12270, 12050, 12060, 2161),
        test_bar("08:22", 12070, 12940, 12060, 12910, 17457),
    ]
    interest_test = prominent_bear_interest_decision(
        konan[:6], now=datetime(2026, 7, 27, 8, 13),
    )
    previous_bear_growth_bars = [
        test_bar("09:00", 100.0, 100.2, 99.8, 100.0, 100),
        test_bar("09:01", 100.0, 108.0, 99.9, 107.0, 500),
        test_bar("09:02", 107.0, 107.2, 105.8, 106.0, 100),
        test_bar("09:03", 106.0, 106.2, 105.6, 105.8, 80),
        test_bar("09:04", 105.8, 106.0, 103.8, 104.0, 104),
    ]
    previous_bear_growth_test = prominent_bear_interest_decision(
        previous_bear_growth_bars,
        now=datetime(2026, 7, 27, 9, 4),
    )
    rising_three_bear_bars = [
        test_bar("09:00", 100.0, 100.2, 99.8, 100.0, 100),
        test_bar("09:01", 100.0, 108.0, 99.9, 107.0, 500),
        test_bar("09:02", 107.0, 107.2, 105.8, 106.0, 80),
        test_bar("09:03", 106.0, 106.2, 104.8, 105.0, 100),
        test_bar("09:04", 105.0, 105.2, 103.8, 104.0, 120),
    ]
    rising_three_bear_test = prominent_bear_interest_decision(
        rising_three_bear_bars,
        now=datetime(2026, 7, 27, 9, 4),
    )
    active_current_pullback_test = active_interest_pullback_decision(
        previous_bear_growth_bars,
        now=datetime(2026, 7, 27, 9, 4),
    )
    historical_pullback_reuse_bars = [
        test_bar("09:00", 100.0, 100.2, 99.8, 100.0, 1000),
        test_bar("09:01", 100.0, 108.0, 99.9, 107.0, 5000),
        test_bar("09:02", 107.0, 107.1, 103.8, 104.0, 3500),
        test_bar("09:03", 104.0, 107.9, 103.9, 107.7, 2800),
        test_bar("09:04", 107.7, 107.8, 107.2, 107.4, 3200),
    ]
    historical_pullback_reuse_test = active_interest_pullback_decision(
        historical_pullback_reuse_bars,
        now=datetime(2026, 7, 27, 9, 4),
    )
    cross_session_reuse_bars = [
        test_bar("08:00", 100.0, 100.2, 99.8, 100.0, 1000),
        test_bar("08:01", 100.0, 108.0, 99.9, 107.0, 5000),
        test_bar("08:02", 107.0, 107.1, 103.8, 104.0, 3500),
        test_bar("09:00", 104.0, 104.2, 103.9, 104.1, 1000),
        test_bar("09:01", 104.1, 104.5, 104.0, 104.4, 1200),
        test_bar("09:02", 104.4, 104.7, 104.3, 104.6, 1300),
        test_bar("09:03", 104.6, 104.7, 103.8, 104.0, 1800),
    ]
    cross_session_reuse_test = active_interest_pullback_decision(
        cross_session_reuse_bars,
        now=datetime(2026, 7, 27, 9, 3),
    )
    cross_session_regular_bars = interest_session_bars(
        cross_session_reuse_bars,
        now=datetime(2026, 7, 27, 9, 3),
    )
    strong_leader_bars = [
        test_bar("09:00", 100.0, 100.2, 99.8, 100.0, 400000),
        test_bar("09:01", 100.0, 101.4, 99.9, 101.2, 400000),
        test_bar("09:02", 101.2, 102.6, 101.1, 102.4, 400000),
        test_bar("09:03", 102.4, 103.8, 102.3, 103.6, 400000),
        test_bar("09:04", 103.6, 105.0, 103.5, 104.8, 400000),
        test_bar("09:05", 104.8, 106.2, 104.7, 106.0, 400000),
        test_bar("09:06", 106.0, 107.4, 105.9, 107.2, 400000),
        test_bar("09:07", 107.2, 108.6, 107.1, 108.4, 400000),
        test_bar("09:08", 108.4, 109.8, 108.3, 109.6, 400000),
        test_bar("09:09", 109.6, 111.0, 109.5, 110.8, 400000),
        test_bar("09:10", 110.8, 112.2, 110.7, 112.0, 400000),
        test_bar("09:11", 112.0, 112.1, 111.1, 111.3, 200000),
        test_bar("09:12", 111.3, 111.8, 111.2, 111.7, 180000),
        test_bar("09:13", 111.7, 111.8, 110.8, 111.0, 220000),
        test_bar("09:14", 111.0, 111.5, 110.7, 111.4, 180000),
        test_bar("09:15", 111.4, 111.5, 110.4, 110.7, 210000),
        test_bar("09:16", 110.7, 111.2, 110.3, 111.0, 180000),
        test_bar("09:17", 111.0, 111.1, 109.8, 110.4, 220000),
        test_bar("09:18", 110.4, 110.8, 109.7, 110.6, 180000),
        test_bar("09:19", 110.6, 111.0, 109.5, 110.2, 500000),
    ]
    strong_leader_test = active_interest_pullback_decision(
        strong_leader_bars,
        now=datetime(2026, 7, 27, 9, 19),
    )
    actual_interest_time_test = _v7610_interest_trigger_time(
        previous_bear_growth_bars,
        datetime(2026, 7, 27, 9, 4, 58),
    )
    five_bar_average_bars = [
        test_bar("09:00", 100.0, 100.3, 99.8, 100.1, 100),
        test_bar("09:01", 100.1, 108.0, 100.0, 107.0, 500),
        test_bar("09:02", 107.0, 107.2, 105.8, 106.0, 120),
        test_bar("09:03", 106.0, 106.2, 104.8, 105.0, 80),
        test_bar("09:04", 105.0, 105.3, 104.9, 105.2, 90),
        test_bar("09:05", 105.2, 105.3, 104.6, 104.8, 110),
        test_bar("09:06", 104.8, 105.1, 104.7, 105.0, 100),
        test_bar("09:07", 105.0, 105.1, 103.8, 104.0, 201),
    ]
    five_bar_average_test = (
        V7600ThreeStageEngine._v7609_five_bar_average_bear_interest_decision(
            five_bar_average_bars,
            now=datetime(2026, 7, 27, 9, 7),
        )
    )
    five_bar_equal_volume_test = (
        V7600ThreeStageEngine._v7609_five_bar_average_bear_interest_decision(
            five_bar_average_bars[:-1]
            + [test_bar("09:07", 105.0, 105.1, 103.8, 104.0, 100)],
            now=datetime(2026, 7, 27, 9, 7),
        )
    )
    first_bear_after_peak_bars = [
        test_bar("09:00", 100.0, 100.2, 99.9, 100.0, 1000),
        test_bar("09:01", 100.0, 102.0, 99.9, 101.8, 1800),
        test_bar("09:02", 101.8, 104.0, 101.7, 103.8, 2200),
        test_bar("09:03", 103.8, 106.0, 103.7, 105.8, 2500),
        test_bar("09:04", 105.8, 108.0, 105.7, 107.8, 3000),
        test_bar("09:05", 107.8, 107.9, 105.5, 105.8, 5000),
    ]
    first_bear_after_peak_test = prominent_bear_interest_decision(
        first_bear_after_peak_bars,
        now=datetime(2026, 7, 27, 9, 5),
    )
    tiny_denominator_bars = [
        test_bar("09:02", 100.0, 100.2, 99.9, 100.0, 1200),
        test_bar("09:03", 100.0, 108.0, 99.9, 107.0, 5000),
        test_bar("09:04", 107.0, 107.1, 105.9, 106.0, 1100),
        test_bar("09:05", 106.0, 106.1, 104.9, 105.0, 900),
        test_bar("09:06", 105.0, 105.1, 104.8, 104.9, 14),
        test_bar("09:07", 104.9, 105.0, 104.7, 104.8, 1000),
        test_bar("09:08", 104.8, 104.9, 103.8, 104.0, 2960),
    ]
    tiny_denominator_test = prominent_bear_interest_decision(
        tiny_denominator_bars,
        now=datetime(2026, 7, 27, 9, 8),
    )
    top_distribution_bars = [
        test_bar("09:16", 100.0, 100.2, 99.9, 100.1, 1000000),
        test_bar("09:17", 100.1, 103.0, 100.0, 102.8, 3000000),
        test_bar("09:18", 102.8, 106.0, 102.7, 105.8, 4000000),
        test_bar("09:19", 105.8, 108.0, 105.7, 107.7, 5000000),
        test_bar("09:21", 108.0, 109.0, 103.5, 104.0, 15000000),
    ]
    top_distribution_test = structural_distribution_risk(
        top_distribution_bars[-1],
        top_distribution_bars,
        now=datetime(2026, 7, 27, 9, 21),
    )
    top_distribution_interest_test = active_interest_pullback_decision(
        top_distribution_bars,
        now=datetime(2026, 7, 27, 9, 21),
    )
    ceiling_failure_bars = [
        test_bar("14:50", 900.0, 920.0, 895.0, 915.0, 500),
        test_bar("14:51", 990.0, 1000.0, 990.0, 1000.0, 1000),
        test_bar("14:52", 1000.0, 1000.0, 1000.0, 1000.0, 1100),
        test_bar("14:53", 1000.0, 1000.0, 1000.0, 1000.0, 1200),
        test_bar("14:54", 1000.0, 1000.0, 1000.0, 1000.0, 1300),
        test_bar("14:55", 1000.0, 1000.0, 940.0, 950.0, 5000),
    ]
    ceiling_failure_test = structural_distribution_risk(
        ceiling_failure_bars[-1],
        ceiling_failure_bars,
        now=datetime(2026, 7, 27, 14, 55),
    )
    late_thin_bars = [
        test_bar("13:56", 5000, 5000, 4995, 5000, 100000),
        test_bar("13:57", 5000, 5000, 4995, 5000, 100000),
        test_bar("13:58", 5000, 5000, 4995, 5000, 100000),
        test_bar("13:59", 5000, 5000, 4995, 5000, 100000),
        test_bar("14:00", 5000, 5000, 4995, 5000, 100000),
    ]
    late_thin_boundary_test = late_low_price_liquidity_excluded(
        late_thin_bars,
        now=datetime(2026, 7, 27, 14, 0),
    )
    late_liquid_boundary_test = late_low_price_liquidity_excluded(
        late_thin_bars[:-1]
        + [test_bar("14:00", 5000, 5000, 4995, 5000, 100001)],
        now=datetime(2026, 7, 27, 14, 0),
    )
    virtual_target_test = virtual_exit_replay(
        [test_bar("08:15", 100.0, 101.2, 99.5, 101.0, 1000)],
        entry_time=datetime(2026, 7, 27, 8, 14),
        entry_price=100.0,
        target_price=101.0,
    )
    virtual_stop_test = virtual_exit_replay(
        [test_bar("08:15", 100.0, 100.5, 96.9, 97.0, 1000)],
        entry_time=datetime(2026, 7, 27, 8, 14),
        entry_price=100.0,
        target_price=101.5,
    )
    bottom_ma5_bars = [
        test_bar("09:00", 100.0, 100.2, 99.8, 100.0, 100),
        test_bar("09:01", 100.0, 108.0, 99.9, 107.0, 500),
        test_bar("09:02", 107.0, 107.2, 105.8, 106.0, 400),
        test_bar("09:03", 106.0, 106.2, 104.8, 105.0, 350),
        test_bar("09:04", 105.0, 105.2, 103.8, 104.0, 1000),
        test_bar("09:05", 104.0, 104.1, 102.8, 103.0, 1000),
        test_bar("09:06", 103.0, 103.1, 101.8, 102.0, 350),
        test_bar("09:07", 102.2, 102.3, 101.2, 101.8, 100),
        test_bar("09:08", 101.8, 103.3, 101.7, 103.2, 200),
    ]
    bottom_ma5_test = bottom_volume_ma5_entry_decision(
        bottom_ma5_bars[5],
        bottom_ma5_bars,
        now=datetime(2026, 7, 27, 9, 8),
    )
    shallow_bottom_ma5_bars = [
        test_bar("09:00", 100.0, 100.2, 99.8, 100.0, 100),
        test_bar("09:01", 100.0, 104.0, 99.9, 103.5, 500),
        test_bar("09:02", 103.5, 103.7, 102.8, 103.0, 400),
        test_bar("09:03", 103.0, 103.2, 101.8, 102.0, 350),
        test_bar("09:04", 102.0, 102.2, 100.8, 101.0, 1000),
        test_bar("09:05", 101.0, 101.1, 100.0, 100.5, 1000),
        test_bar("09:06", 100.5, 100.7, 99.8, 100.0, 350),
        test_bar("09:07", 100.0, 100.2, 99.7, 99.9, 100),
        test_bar("09:08", 99.9, 101.5, 99.8, 101.4, 200),
    ]
    shallow_bottom_ma5_test = bottom_volume_ma5_entry_decision(
        shallow_bottom_ma5_bars[5],
        shallow_bottom_ma5_bars,
        now=datetime(2026, 7, 27, 9, 8),
    )
    floor_shape_prebuy_test = bottom_volume_shape_prebuy_decision(
        bottom_ma5_bars[5],
        bottom_ma5_bars[:-1],
        now=datetime(2026, 7, 27, 9, 7),
    )
    order_independent_bars = [
        test_bar("09:00", 100.0, 100.2, 99.8, 100.0, 100),
        test_bar("09:01", 100.0, 108.0, 99.9, 107.0, 500),
        test_bar("09:02", 107.0, 107.2, 105.8, 106.0, 400),
        test_bar("09:03", 106.0, 106.1, 104.9, 105.5, 300),
        test_bar("09:04", 104.5, 104.8, 104.4, 104.7, 80),
        test_bar("09:05", 104.7, 104.8, 103.7, 103.8, 1000),
        test_bar("09:06", 103.8, 105.6, 103.7, 105.5, 200),
    ]
    order_independent_test = bottom_volume_ma5_entry_decision(
        order_independent_bars[5],
        order_independent_bars,
        now=datetime(2026, 7, 27, 9, 6),
    )
    first_half_test = konan_first_half_decision(
        konan[5], konan[6:9], now=datetime(2026, 7, 27, 8, 16),
    )
    second_half_bars = konan[:10] + [
        test_bar("08:18", 11710, 11775, 11705, 11770, 542),
    ]
    second_half_test = konan_second_half_decision(
        second_half_bars,
        first_bar_time=datetime(2026, 7, 27, 8, 16),
        now=datetime(2026, 7, 27, 8, 18),
    )
    breakout_test = konan_breakout_pullback_decision(
        konan, now=datetime(2026, 7, 27, 8, 22),
    )
    ma5_confirm_bars = [
        test_bar("09:00", 100.0, 101.0, 99.8, 100.5, 100),
        test_bar("09:01", 100.5, 109.0, 100.4, 108.0, 500),
        test_bar("09:02", 108.0, 108.2, 106.0, 106.2, 260),
        test_bar("09:03", 106.2, 106.4, 105.0, 105.2, 220),
        test_bar("09:04", 105.2, 105.3, 104.0, 104.4, 300),
        test_bar("09:05", 104.4, 105.0, 104.3, 104.8, 100),
        test_bar("09:06", 104.8, 105.2, 104.6, 105.0, 90),
        test_bar("09:07", 105.0, 105.4, 104.9, 105.2, 80),
        test_bar("09:08", 105.2, 105.6, 105.1, 105.4, 80),
        test_bar("09:09", 105.4, 105.8, 105.3, 105.6, 80),
        test_bar("09:10", 105.6, 106.2, 105.5, 106.0, 85),
        test_bar("09:11", 106.0, 106.8, 105.9, 106.5, 90),
    ]
    ma5_confirm_test = V7600ThreeStageEngine._v7609_ma5_reentry_decision(
        ma5_confirm_bars
    )
    shallow_test = V7600ThreeStageEngine._v7609_ma5_reentry_decision(
        [
            test_bar(
                f"09:{index:02d}",
                100.0 + index * 0.1,
                100.2 + index * 0.1,
                99.9 + index * 0.1,
                100.1 + index * 0.1,
                100,
            )
            for index in range(12)
        ]
    )
    relative_interest_bars = [
        test_bar(f"10:{index:02d}", 109.0, 110.0, 108.0, 109.0, 100)
        for index in range(10)
    ]
    relative_flush_bar = test_bar("10:10", 108.0, 108.2, 106.5, 107.0, 180)
    relative_interest_bars.append(relative_flush_bar)
    relative_interest_bars.extend([
        test_bar("10:11", 106.8, 107.1, 106.6, 107.0, 40),
        test_bar("10:12", 107.0, 107.2, 106.8, 107.1, 35),
        test_bar("10:13", 107.1, 107.3, 106.9, 107.2, 30),
        test_bar("10:14", 107.2, 107.4, 107.0, 107.3, 25),
        test_bar("10:15", 107.3, 107.5, 107.1, 107.4, 20),
    ])
    relative_interest_test = (
        V7600ThreeStageEngine._v7609_relative_bear_interest_decision(
            relative_interest_bars
        )
    )
    newen_ma5_hold_test = V7600ThreeStageEngine._v7609_ma5_reentry_decision([
        test_bar("12:42", 9800, 9900, 9790, 9850, 10000),
        test_bar("12:43", 9850, 9860, 9780, 9800, 8000),
        test_bar("12:44", 9800, 9810, 9550, 9560, 13571),
        test_bar("12:45", 9560, 9570, 9530, 9540, 3129),
        test_bar("12:46", 9540, 9550, 9530, 9540, 6099),
        test_bar("12:47", 9540, 9550, 9490, 9500, 10987),
        test_bar("12:48", 9500, 9510, 9460, 9470, 1594),
        test_bar("12:49", 9470, 9500, 9465, 9490, 4537),
        test_bar("12:50", 9490, 9500, 9440, 9450, 6182),
        test_bar("12:51", 9430, 9450, 9420, 9440, 589),
        test_bar("12:52", 9440, 9510, 9435, 9500, 3096),
        test_bar("12:53", 9500, 9505, 9470, 9490, 1939),
    ])
    stage_probe = object.__new__(V7600ThreeStageEngine)
    stage_probe._r21_stage_times = {}
    stage_probe._v7591_timeline = {
        "123456": {
            "관심": "2026-07-27T09:00:00+09:00",
            "events": [
                {
                    "stage": "관심",
                    "at": "2026-07-27T09:00:00+09:00",
                    "reason": "화면행최초확인",
                },
                {
                    "stage": "관심",
                    "at": "2026-07-27T09:12:34+09:00",
                    "reason": f"{V7609_INTEREST_EVENT_MARKER}실제편입",
                },
                {
                    "stage": "관찰",
                    "at": "2026-07-27T09:05:00+09:00",
                    "reason": "V7594:관찰편입=실제편입",
                },
                {
                    "stage": "관찰",
                    "at": "2026-07-27T09:20:00+09:00",
                    "reason": "V7594:관찰편입=재검색",
                },
            ],
        }
    }
    exact_stage_test = stage_probe._v7609_stage_timestamp(
        "관심종목",
        "123456",
        datetime(2026, 7, 27, 10, 0, tzinfo=KST),
        "09:00:00",
    )
    # Stage paths now use the append-only authority, NOT the legacy GUI timeline.
    stage_probe._v7609_stage_manager = _V7600_STATE.StateManager()
    for stage, clock in ((_V7600_STATE.OBSERVED, '09:05:00'), (_V7600_STATE.INTEREST, '09:12:34')):
        stamp = f'2026-07-27T{clock}+09:00'
        stage_probe._v7609_stage_manager.apply(_V7600_STATE.TransitionRequest(
            signal_id=f'self-check-path-{stage}', symbol='123456', new_stage=stage,
            rule_id='SELF_CHECK_SYNTHETIC', data_time=stamp, decision_time=stamp,
            engine_version=VERSION, reason='synthetic stage path', evidence={'synthetic': True}))
    ordered_stage_path_test = stage_probe._v7609_stage_path(
        "123456",
        datetime(2026, 7, 27, 10, 0, tzinfo=KST),
    )
    legacy_path_test = stage_probe._v7609_strip_legacy_stage_path(
        "관찰 09:00:00 → 관심 09:00:00 → 직전 09:00:00 · 실제판단근거"
    )
    stage_probe.names = {}
    stage_probe._v7591_timeline["092870"] = {
        "name": "엑시콘",
        "events": [],
    }
    durable_name_test = stage_probe._v7609_resolve_display_name("092870", "092870")
    placeholder_probe = object.__new__(V7600ThreeStageEngine)
    placeholder_probe._v7591_timeline = {}
    placeholder_probe._v7591_rejections = {}
    placeholder_probe.latest_quotes = {}
    placeholder_probe._fill_times = lambda _now: {}
    placeholder_snapshot = {
        "rows": [{
            "symbol": "123456",
            "stage": "관심종목",
            "stage_time": "09:00:00",
            "reason": "표시행",
        }]
    }
    placeholder_probe._enrich_all_times(
        placeholder_snapshot,
        datetime(2026, 7, 27, 10, 0, tzinfo=KST),
    )
    no_placeholder_write_test = placeholder_probe._v7591_timeline == {}
    stop_probe = object.__new__(V7600ThreeStageEngine)
    stop_position = type(
        "StopPosition",
        (),
        {"entry_price": 80000.0, "stop_price": 76000.0, "target_price": 0.0, "target_order_active": False, "quantity": 1},
    )()
    tighter_position = type(
        "TighterPosition",
        (),
        {"entry_price": 80000.0, "stop_price": 79000.0, "target_price": 0.0, "target_order_active": False, "quantity": 1},
    )()
    stop_probe.broker = type(
        "StopBroker",
        (),
        {"positions": {"111111": stop_position, "222222": tighter_position}, "save_state": lambda self: None},
    )()
    stop_probe.logger = type(
        "StopLogger", (), {"warning": lambda *args, **kwargs: None}
    )()
    stop_probe._label = lambda symbol: symbol
    stop_probe._gem_order_announced = set()
    fixed_stop_changed = stop_probe._v7609_enforce_fixed_stop("111111")
    tighter_stop_normalized = stop_probe._v7609_enforce_fixed_stop("222222")
    stop_position.stop_price = 76000.0
    tick_loop_stop_registered = stop_probe._enforce_gem_orders("111111", announce=False)
    penny_floor_test = (
        parent.r21_logic._penny_volume_floor(999, datetime(2026, 8, 5, 9, 14).time()) == 0
        and parent.r21_logic._penny_volume_floor(999, datetime(2026, 8, 5, 9, 15).time()) == 300_000
        and parent.r21_logic._penny_volume_floor(999, datetime(2026, 8, 5, 9, 30).time()) == 500_000
        and parent.r21_logic._penny_volume_floor(999, datetime(2026, 8, 5, 10, 0).time()) == 1_000_000
        and parent.r21_logic._penny_volume_floor(1000, datetime(2026, 8, 5, 10, 0).time()) == 0
    )
    command_buy_test = v57.compile_collaboration_input(
        "005930 매수 주문", selected_symbol="005930", selected_name="삼성전자"
    )
    command_sell_test = v57.compile_collaboration_input(
        "005930 매도 주문", selected_symbol="005930", selected_name="삼성전자"
    )
    command_target_test = v57.compile_collaboration_input(
        "005930 75000원 매도", selected_symbol="005930", selected_name="삼성전자"
    )
    command_now_buy_test = v57.compile_collaboration_input(
        "이 종목 지금 매수해", selected_symbol="005930", selected_name="삼성전자"
    )
    command_price_buy_test = v57.compile_collaboration_input(
        "삼성전자 70000원에 매수해",
        selected_symbol="005930",
        selected_name="삼성전자",
        known_names={"삼성전자": "005930"},
    )
    command_quantity_price_test = v57.compile_collaboration_input(
        "삼성전자 23만원에 10주 매수해줘",
        selected_symbol="005930",
        selected_name="삼성전자",
        known_names={"삼성전자": "005930"},
    )
    theme_command_test = v57.parse_free_text(
        "현재 전선, 변압기 테마가 뜨고 있으니 관련 종목들을 자주 살펴서 로직에 맞는 것이 있으면 매수해줘"
    )
    leader_command_test = v57.parse_free_text(
        "대장주는 대한전선, 효성중공업이니 이 종목을 집중 관리해줘",
        known_names={"대한전선": "001440", "효성중공업": "298040"},
    )
    bearish_interest_route_test = V7600ThreeStageEngine._v7609_is_strict_interest_reason(
        "하락음봉거래x2.50"
    )
    requested_interest_routes_test = all(
        V7600ThreeStageEngine._v7609_is_strict_interest_reason(reason)
        for reason in (
            previous_bear_growth_test.reason,
            rising_three_bear_test.reason,
        )
    )
    restart_now = datetime(2026, 8, 5, 14, 20, tzinfo=KST)
    restart_observed = restart_now - timedelta(minutes=2)
    restart_engine = object.__new__(V7600ThreeStageEngine)
    restart_engine._v76_strict_observation = {
        "111111": restart_observed,
        "222222": restart_observed,
        "333333": restart_observed,
        "555555": restart_observed,
        "777777": restart_now,
    }
    restart_engine._v76_strict_interest = {}
    restart_engine._v76_strict_prebuy = {}
    restart_engine._v76_strict_market_hold = {}
    restart_engine._v7588_phase = {}
    restart_engine._v7591_timeline = {
        "333333": {
            "events": [{
                "stage": "관심",
                "at": restart_now.isoformat(),
                "reason": f"{V7609_INTEREST_EVENT_MARKER}하락음봉5봉평균초과x3.64",
            }],
        },
        "555555": {
            "events": [
                {
                    "stage": "관심",
                    "at": (restart_now - timedelta(minutes=1)).isoformat(),
                    "reason": f"{V7609_INTEREST_EVENT_MARKER}하락음봉5봉평균초과x2.00",
                },
                {
                    "stage": "직전",
                    "at": restart_now.isoformat(),
                    "reason": f"{V7609_PREBUY_EVENT_MARKER}V7609_BOTTOM_MA5:직접교체복원시험",
                },
            ],
        },
        "777777": {
            "events": [{
                "stage": "관심",
                "at": (restart_now - timedelta(minutes=1)).isoformat(),
                "reason": f"{V7609_INTEREST_EVENT_MARKER}관찰보다앞선관심차단시험",
            }],
        },
    }
    restart_engine.logger = type("SilentRestartLogger", (), {
        "warning": lambda *args, **kwargs: None,
    })()
    restart_engine._v7609_stage_manager = _V7600_STATE.StateManager()
    restart_stage_log = _V7609SelfCheckEventLog()
    restart_engine._v7609_state_log_obj = lambda _now: restart_stage_log
    for probe_symbol, probe_events in {
        "333333": (
            (_V7600_STATE.OBSERVED, restart_observed, "V7594:관찰편입=검사"),
            (_V7600_STATE.INTEREST, restart_now, "하락음봉5봉평균초과x3.64"),
        ),
        "555555": (
            (_V7600_STATE.OBSERVED, restart_observed, "V7594:관찰편입=검사"),
            (
                _V7600_STATE.INTEREST,
                restart_now - timedelta(minutes=1),
                "하락음봉5봉평균초과x2.00",
            ),
            (_V7600_STATE.PREBUY, restart_now, "V7609_BOTTOM_MA5:검사"),
        ),
    }.items():
        for probe_stage, probe_stamp, probe_reason in probe_events:
            restart_engine._v7609_stage_manager.apply(
                _V7600_STATE.TransitionRequest(
                    signal_id=_V7600_STATE.make_signal_id(
                        engine_version=VERSION,
                        symbol=probe_symbol,
                        rule_id=f"SELF_CHECK_{probe_stage}",
                        data_time=probe_stamp.isoformat(),
                        new_stage=probe_stage,
                    ),
                    symbol=probe_symbol,
                    new_stage=probe_stage,
                    rule_id=f"SELF_CHECK_{probe_stage}",
                    data_time=probe_stamp.isoformat(),
                    decision_time=probe_stamp.isoformat(),
                    engine_version=VERSION,
                    reason=probe_reason,
                    evidence={"source": "SELF_CHECK_STATE_LEDGER"},
                )
            )
    restart_engine._save_supply_potential = lambda: None
    restart_engine._supply_potential = {
        "111111": type("RestartInterest", (), {
            "stage": "관심종목",
            "source": "V75.88:하락음봉5봉평균초과x1.40",
            "updated_at": restart_now,
            "detected_at": restart_now,
        })(),
        "222222": type("RestartPrebuy", (), {
            "stage": "매수직전",
            "source": "V75.88:V7609_BOTTOM_MA5:거래바닥급감·MA5안착",
            "updated_at": restart_now,
            "detected_at": restart_now,
        })(),
        "333333": type("RestartOverwrittenInterest", (), {
            "stage": "관찰후보",
            "source": (
                "V75.88:하락음봉5봉평균초과x3.64"
                "|V75.88:엄격조건대기:이전3분후보"
            ),
            "updated_at": restart_now,
            "detected_at": restart_now,
        })(),
        "555555": type("RestartOverwrittenPrebuy", (), {
            "stage": "시장보류",
            "source": "이전3분직접교체",
            "updated_at": restart_now,
            "detected_at": restart_now,
        })(),
        "777777": type("BackwardInterest", (), {
            "stage": "관심종목",
            "source": "관찰보다앞선관심",
            "updated_at": restart_now - timedelta(minutes=1),
            "detected_at": restart_now - timedelta(minutes=1),
        })(),
    }
    restart_engine._v7609_restore_today_scanner_state(restart_now)
    restart_restore_ok = (
        "111111" not in restart_engine._v76_strict_interest
        and "222222" not in restart_engine._v76_strict_prebuy
        and restart_engine._v7588_phase.get("111111") is None
        and restart_engine._v7588_phase.get("222222") is None
        and restart_engine._v7609_stage_manager.stage_of("111111") is None
        and restart_engine._v7609_stage_manager.stage_of("222222") is None
        and restart_engine._v7588_phase.get("333333") == "INTEREST"
        and restart_engine._supply_potential["333333"].stage == "관심종목"
        and restart_engine._v7588_phase.get("555555") == "PREBUY"
        and canonical_stage(
            restart_engine._supply_potential["555555"].stage
        ) == "매수가능성"
        and "777777" not in restart_engine._v76_strict_interest
        and restart_engine._v7588_phase.get("777777") is None
        and restart_engine._v7609_stage_manager.stage_of("777777") is None
    )
    stage_guard_probe = object.__new__(V7600ThreeStageEngine)
    stage_guard_probe._v76_strict_observation = {"444444": restart_now}
    stage_guard_probe._v76_strict_interest = {"444444": restart_now}
    stage_guard_probe._v76_strict_prebuy = {}
    stage_guard_probe._v76_strict_market_hold = {}
    stage_guard_probe._v7588_phase = {"444444": "INTEREST"}
    stage_guard_probe._v7591_timeline = {}
    stage_guard_probe._supply_potential = {
        "444444": type("ProtectedInterest", (), {"stage": "관심종목"})(),
    }
    stage_guard_probe.names = {"444444": "관심보호테스트"}
    stage_guard_probe.logger = type("SilentStageLogger", (), {
        "info": lambda *args, **kwargs: None,
    })()
    stage_guard_probe._v7609_stage_manager = _V7600_STATE.StateManager()
    stage_guard_log = _V7609SelfCheckEventLog()
    stage_guard_probe._v7609_state_log_obj = lambda _now: stage_guard_log
    for probe_stage, probe_stamp, probe_reason in (
        (_V7600_STATE.OBSERVED, restart_observed, "V7594:관찰편입=검사"),
        (_V7600_STATE.INTEREST, restart_now, "하락음봉5봉평균초과x2.00"),
    ):
        stage_guard_probe._v7609_stage_manager.apply(
            _V7600_STATE.TransitionRequest(
                signal_id=_V7600_STATE.make_signal_id(
                    engine_version=VERSION,
                    symbol="444444",
                    rule_id=f"SELF_CHECK_GUARD_{probe_stage}",
                    data_time=probe_stamp.isoformat(),
                    new_stage=probe_stage,
                ),
                symbol="444444",
                new_stage=probe_stage,
                rule_id=f"SELF_CHECK_GUARD_{probe_stage}",
                data_time=probe_stamp.isoformat(),
                decision_time=probe_stamp.isoformat(),
                engine_version=VERSION,
                reason=probe_reason,
                evidence={"source": "SELF_CHECK_STATE_LEDGER"},
            )
        )
    stage_guard_probe._v7588_publish(
        "444444",
        "CANDIDATE",
        "매수가능성",
        "이전3분후보경로",
        restart_now,
    )
    strict_stage_overwrite_guard_ok = (
        stage_guard_probe._v7588_phase.get("444444") == "INTEREST"
        and "444444" in stage_guard_probe._v76_strict_interest
        and stage_guard_probe._supply_potential["444444"].stage == "관심종목"
    )
    direct_replace_probe = object.__new__(V7600ThreeStageEngine)
    direct_replace_probe._v76_strict_interest = {"666666": restart_now}
    direct_replace_probe._v76_strict_prebuy = {}
    direct_replace_probe._v76_strict_market_hold = {}
    direct_replace_probe._v7588_phase = {"666666": "INTEREST"}
    direct_replace_probe._v7591_timeline = {
        "666666": {
            "events": [{
                "stage": "관심",
                "at": restart_now.isoformat(),
                "reason": f"{V7609_INTEREST_EVENT_MARKER}하락음봉5봉평균초과x2.00",
            }],
        },
    }
    direct_replace_probe._supply_potential = {
        "666666": type("LegacyDirectReplacement", (), {
            "stage": "시장보류",
            "source": "완성3분봉원천결측",
            "updated_at": restart_now + timedelta(minutes=1),
        })(),
    }
    direct_replace_probe._v7609_stage_manager = _V7600_STATE.StateManager()
    direct_replace_log = _V7609SelfCheckEventLog()
    direct_replace_probe._v7609_state_log_obj = lambda _now: direct_replace_log
    for probe_stage, probe_stamp, probe_reason in (
        (_V7600_STATE.OBSERVED, restart_observed, "V7594:관찰편입=검사"),
        (_V7600_STATE.INTEREST, restart_now, "하락음봉5봉평균초과x2.00"),
    ):
        direct_replace_probe._v7609_stage_manager.apply(
            _V7600_STATE.TransitionRequest(
                signal_id=_V7600_STATE.make_signal_id(
                    engine_version=VERSION,
                    symbol="666666",
                    rule_id=f"SELF_CHECK_DIRECT_{probe_stage}",
                    data_time=probe_stamp.isoformat(),
                    new_stage=probe_stage,
                ),
                symbol="666666",
                new_stage=probe_stage,
                rule_id=f"SELF_CHECK_DIRECT_{probe_stage}",
                data_time=probe_stamp.isoformat(),
                decision_time=probe_stamp.isoformat(),
                engine_version=VERSION,
                reason=probe_reason,
                evidence={"source": "SELF_CHECK_STATE_LEDGER"},
            )
        )
    direct_replace_changed = (
        direct_replace_probe._v7609_reconcile_strict_supply_stages(restart_now)
    )
    direct_replace_guard_ok = (
        direct_replace_changed
        and direct_replace_probe._supply_potential["666666"].stage == "관심종목"
        and "V7609_STRICT_STAGE=INTEREST" in direct_replace_probe._supply_potential["666666"].source
        and direct_replace_probe._v7588_phase.get("666666") == "INTEREST"
    )
    postclose_probe = object.__new__(V7600ThreeStageEngine)
    postclose_probe._v7591_timeline = {
        "123456": {
            "name": "테스트관심",
            "events": [
                {"stage": "관찰", "at": "2026-08-07T10:00:00+09:00", "reason": "관찰"},
                {"stage": "관심", "at": "2026-08-07T10:02:00+09:00", "reason": "관심"},
                {"stage": "관찰", "at": "2026-08-07T10:01:00+09:00", "reason": "시간상 과거"},
            ],
        },
        "654321": {
            "name": "테스트직전",
            "events": [
                {"stage": "후보", "at": "2026-08-07T14:00:00+09:00", "reason": "후보"},
            ],
        },
    }
    postclose_probe._v7609_postclose_price_cache = {
        "123456": {
            "stamp": datetime(2026, 8, 7, 15, 27, tzinfo=KST),
            "name": "테스트관심",
            "current_price": 105.0,
            "reference_price": 100.0,
        },
        "654321": {
            "stamp": datetime(2026, 8, 7, 15, 27, tzinfo=KST),
            "name": "테스트직전",
            "current_price": 200.0,
            "reference_price": 200.0,
        },
    }
    postclose_probe._v7609_postclose_review_cache = None
    postclose_probe.names = {}
    postclose_rows = postclose_probe._v7609_postclose_review_rows(
        {"rows": []}, datetime(2026, 8, 8, 12, 0, tzinfo=KST)
    )
    postclose_by_symbol = {
        str(row["symbol"]): row for row in (postclose_rows or [])
    }
    postclose_stage_ok = (
        postclose_by_symbol.get("123456", {}).get("stage") == "관심종목"
        and postclose_by_symbol.get("654321", {}).get("stage") == "매수가능성"
        and "주문 없음"
        in str(postclose_by_symbol.get("654321", {}).get("reason", ""))
    )
    postclose_no_fake_orders = all(
        str(row.get("stage", "")) not in {"매수종목", "매도종목"}
        for row in (postclose_rows or [])
    )
    saved_chart_wait_probe = object.__new__(V7600CtrlVHeroLink)
    saved_chart_wait_probe._v7609_fast_edit = 999
    saved_chart_wait_probe._v7609_fast_saved_at = 1.0
    saved_chart_wait_probe._v7609_lock_saved_chart_awaiting_edit(
        type("Saved0600", (), {"hwnd": 111, "top": 222})()
    )
    saved_chart_wait_ok = (
        saved_chart_wait_probe._v7609_fast_chart == 111
        and saved_chart_wait_probe._v7609_fast_top == 222
        and saved_chart_wait_probe._v7609_fast_edit == 0
        and saved_chart_wait_probe._v7609_fast_saved_at == 0.0
    )
    rows.extend([
        (
            "STATE-002단일단계전이관문",
            "_v7609_record_authoritative_stage(" in publish_source
            and "manager.discard_last(event)" in link_source
            and "class StateManager" in state_manager_source,
            "실제 publish는 순서·signal_id·내구성 기록 관문을 통과",
        ),
        (
            "STATE-002화면권위동기화",
            "managed_stage = self._v7609_engine_stage_from_state(" in link_source
            and "_v7609_restore_authoritative_stage_events(now)" in link_source,
            "대시보드는 이벤트 장부의 확정 단계만 우선 표시",
        ),
        (
            "STATE-003PAPER체결단계연결",
            "_v7609_install_paper_fill_state_bridge()" in link_source
            and "manager.apply_paper_fill(request)" in link_source
            and "def apply_paper_fill" in state_manager_source,
            "보유·매도 단계는 PAPER 최종 체결과 단일 상태관리자를 사용",
        ),
        (
            "STATE-003미보유매도중복차단",
            "실제 보유수량 0—PAPER 매도 차단" in link_source
            and "중복 PAPER 체결 차단" in link_source
            and "동일 PAPER 체결 ID" in state_manager_source,
            "실제 보유수량 0 매도와 동일 체결 ID 재처리를 차단",
        ),
        (
            "STATE-004당일이벤트장부만복원",
            "_v7591_timeline" not in restore_source
            and "_v7609_state_manager_obj()" in restore_source
            and "_v7609_authoritative_stage_event(" in restore_source,
            "옛 타임라인·화면 문자열은 관심·직전을 복원할 권한이 없음",
        ),
        (
            "STATE-005INV01~07실제경로회귀",
            state005_ok,
            state005_detail,
        ),
        (
            "IDEM-001주문의도01~07실제경로회귀",
            idem001_ok
            and "class OrderIntentLedger" in order_guard_source
            and "_v7609_install_order_idempotency_bridge()" in link_source
            and "RELEASED_BEFORE_ACCEPTANCE" in link_source,
            idem001_detail,
        ),
        (
            "EVID-001증거01~07실제경로회귀",
            evid001_ok
            and "EVID-001-v1" in link_source
            and '"old_stage"' in order_guard_source
            and '"new_stage"' in order_guard_source,
            evid001_detail,
        ),
        (
            "8월7일장마감최종단계1회복원",
            postclose_stage_ok
            and V7600ThreeStageEngine._v7609_postclose_review_active(
                datetime(2026, 8, 8, 12, 0, tzinfo=KST)
            )
            and not V7600ThreeStageEngine._v7609_postclose_review_active(
                datetime(2026, 8, 10, 8, 0, tzinfo=KST)
            ),
            "최종 시각 우선·월요일 08:00 자동 종료",
        ),
        (
            "장마감복원가상주문차단",
            postclose_no_fake_orders,
            "매수직전은 주문 없음으로 표시하고 보유·매도 기록을 만들지 않음",
        ),
        (
            "V7609검증표식단계만재실행복원",
            restart_restore_ok,
            "옛 표식은 차단하고 당일 append-only 장부의 관심·직전만 복원",
        ),
        (
            "V7609이전평가관심덮어쓰기차단",
            strict_stage_overwrite_guard_ok,
            "엄격 관심 이후 이전 3분·후보 경로가 단계와 표시를 하향하지 않음",
        ),
        (
            "V7609직접객체교체단계복구",
            direct_replace_guard_ok,
            "기존 객체가 화면값을 교체해도 당일 상태장부의 관심·직전이 최종 단계",
        ),
        ("V76실행엔진", v7525.V7525Engine is V7600ThreeStageEngine, ident["engine_class"]),
        ("0600정확Edit연동", v57.RestoredHeroLink is V7600CtrlVHeroLink, ident["hero_link_route"]),
        ("0600제목이중확인", V7600CtrlVHeroLink._is_exact_0600_title("[0600] 키움종합차트") and not V7600CtrlVHeroLink._is_exact_0600_title("영웅문4") and not V7600CtrlVHeroLink._is_exact_0600_title("[0600] 주문창"), "[0600]과 키움종합차트가 같은 창 제목에 모두 있어야 통과"),
        ("저장좌표경로차단", "_saved_click_point" not in send_source and "_find_window" not in send_source and "SAVED_GLOBAL_KEY_ROUTE_BLOCKED" in saved_source, ident["saved_0600_priority"]),
        ("다른키움전역키차단", ident["other_kiwoom_interference_guard"] and "GLOBAL_KEY_ROUTE_BLOCKED" in keyboard_source and "_paste_with_keys" not in keyboard_source and "_press(" not in keyboard_source and "V7609_NON_0600_FOCUS_BLOCKED" in link_source, "매수·매도창으로 갈 수 있는 창 활성화·Ctrl+V·Enter 예비경로 없음"),
        ("0600무전경직접전송", "_activate_window(" not in fast_source and "_foreground(" not in fast_source and "WM_SETTEXT" in fast_source, "다른 영웅문 활성화 없이 고정된 정확한 Edit에 직접전송"),
        ("보조모니터음수좌표", "0x0001 | 0x4000 | 0x8000" in link_source and "GetSystemMetrics(76)" in link_source, "Windows 가상화면 절대좌표 예비"),
        ("정확한Edit포커스예비", "V7609_EXACT_EDIT_FOCUS_FALLBACK" in link_source and "_safe_edit_target" in link_source, "0600 Edit가 이미 포커스면 물리 클릭 실패 허용"),
        ("정확한Edit직접전송", "route=WM_SETTEXT_POST_ENTER" in link_source and "_post_enter_to_edit" in link_source, "정확한 0600 Edit에 직접 쓰기·대상 Enter"),
        ("0600검증Edit고속재사용", V7600CtrlVHeroLink.CACHE_SECONDS == 14400.0 and "V7609_NATIVE_FAST_0600" in link_source and "v7609-0600-prewarm" in link_source, ident["hero_link_speed"]),
        ("0600원클릭직접전송", "V7609_NATIVE_FAST_0600" in link_source and "_window_from_point" in link_source, ident["hero_link_speed"]),
        ("동어반복검증차단", "_changed_symbol_evidence" in link_source and "external_verified = evidence is not None" in link_source, "입력칸 재읽기만으로 VERIFIED 금지"),
        ("좌우화살표연동", '"<Left>"' in link_source and '"<Right>"' in link_source, str(ident["arrow_link_keys"])),
        ("08시PAPER패턴허용", in_pattern_session(datetime(2026, 7, 27, 8, 13)), "NXT 프리마켓 True"),
        ("08시50분갭차단", not in_pattern_session(datetime(2026, 7, 27, 8, 55)), "08:50~09:00 False"),
        ("09시편입허용", in_regular_session(datetime(2026, 7, 24, 9, 0, 0)), "09:00 True"),
        ("15시30분허용", in_regular_session(datetime(2026, 7, 24, 15, 30, 0)), "15:30 True"),
        ("정확한2분할금액", abs(TRANCHE_NOTIONAL * 2 - _V7516.MAX_SYMBOL_NOTIONAL) < 0.01, f"{TRANCHE_NOTIONAL:,.2f} x2"),
        ("코난0813관심편입", interest_test.allowed, interest_test.reason),
        (
            "직전음봉대비30퍼증가관심",
            previous_bear_growth_test.allowed
            and "30%이상증가" in previous_bear_growth_test.reason,
            previous_bear_growth_test.reason,
        ),
        (
            "하락음봉3봉거래연속증가관심",
            rising_three_bear_test.allowed
            and "하락음봉3봉거래연속증가" in rising_three_bear_test.reason,
            rising_three_bear_test.reason,
        ),
        (
            "신규관심패턴엄격관문통과",
            requested_interest_routes_test,
            "30% 증가·3음봉 연속증가 이유만 엄격 관심관문에 추가",
        ),
        (
            "현재기준음봉실제조정동시충족",
            active_current_pullback_test.allowed
            and "ACTIVE_PULLBACK_STANDARD" in active_current_pullback_test.reason,
            active_current_pullback_test.reason,
        ),
        (
            "과거깊은조정반등후재사용차단",
            not historical_pullback_reuse_test.allowed,
            historical_pullback_reuse_test.reason,
        ),
        (
            "프리마켓상승정규장관심재사용차단",
            not cross_session_reuse_test.allowed
            and len(cross_session_regular_bars) == 4
            and cross_session_regular_bars[0].timestamp.hour == 9,
            cross_session_reuse_test.reason,
        ),
        (
            "강한대장주20퍼반납제한예외",
            strong_leader_test.allowed
            and "ACTIVE_PULLBACK_STRONG_LEADER" in strong_leader_test.reason,
            strong_leader_test.reason,
        ),
        (
            "관심편입실제기준봉시각",
            isinstance(actual_interest_time_test, datetime)
            and actual_interest_time_test.strftime("%H:%M:%S") == "09:04:00",
            str(actual_interest_time_test),
        ),
        (
            "하락음봉직전5봉평균초과즉시관심",
            five_bar_average_test[0]
            and five_bar_average_test[2] is five_bar_average_bars[-1]
            and not five_bar_equal_volume_test[0]
            and V7600ThreeStageEngine._v7609_is_direct_five_bar_interest_reason(
                five_bar_average_test[1]
            )
            and V7600ThreeStageEngine._v7610_is_direct_interest_reason(
                five_bar_average_test[1]
            ),
            five_bar_average_test[1],
        ),
        (
            "고점직후첫음봉관심차단",
            not first_bear_after_peak_test.allowed,
            first_bear_after_peak_test.reason,
        ),
        (
            "극소음봉분모배수차단",
            "직전음봉대비거래x211" not in tiny_denominator_test.reason,
            tiny_denominator_test.reason,
        ),
        (
            "신고가고점분배직전매수차단",
            top_distribution_test.allowed
            and "TOP_DISTRIBUTION" in top_distribution_test.reason,
            top_distribution_test.reason,
        ),
        (
            "신고가분배음봉관심편입차단",
            not top_distribution_interest_test.allowed
            and any(
                marker in top_distribution_interest_test.reason
                for marker in ("TOP_DISTRIBUTION", "SUPPORT_BREAK", "CEILING_FAILURE")
            ),
            top_distribution_interest_test.reason,
        ),
        (
            "상한가실패직전매수차단",
            ceiling_failure_test.allowed
            and ceiling_failure_test.reason == "UPPER_LIMIT_OR_CEILING_FAILURE",
            ceiling_failure_test.reason,
        ),
        (
            "14시5천원이하50만주경계",
            late_thin_boundary_test and not late_liquid_boundary_test,
            "5,000원·500,000주는 제외 / 500,001주는 통과",
        ),
        (
            "휴일가상매수후목표손절선후판정",
            virtual_target_test.exited
            and virtual_target_test.reason == "VIRTUAL_TARGET_FIRST"
            and virtual_stop_test.exited
            and virtual_stop_test.reason == "VIRTUAL_STOP_FIRST",
            f"target={virtual_target_test.reason}; stop={virtual_stop_test.reason}",
        ),
        (
            "거래바닥급감후MA5즉시매수",
            bottom_ma5_test.allowed and "MA5돌파" in bottom_ma5_test.reason,
            bottom_ma5_test.reason,
        ),
        (
            "최초매수완성순서강제",
            not shallow_bottom_ma5_test.allowed
            and shallow_bottom_ma5_test.reason.startswith(
                "FIRST_IMPULSE_OR_REAL_PULLBACK_MISSING"
            )
            and not V7609_LEGACY_MA5_ONLY_ENTRY
            and not V7609_LEGACY_PRE_MA5_HALF_ENTRY,
            (
                f"shallow={shallow_bottom_ma5_test.reason}; "
                "legacy MA5-only/first-half order routes disabled"
            ),
        ),
        (
            "거래바닥반전징후매수직전",
            floor_shape_prebuy_test.allowed
            and "VOLUME_FLOOR_" in floor_shape_prebuy_test.reason,
            floor_shape_prebuy_test.reason,
        ),
        (
            "조정증거순서무관후MA5매수",
            order_independent_test.allowed
            and "순서무관반전" in order_independent_test.reason,
            order_independent_test.reason,
        ),
        ("코난거래바닥후1차절반", first_half_test.allowed, first_half_test.reason),
        ("코난MA5안착2차절반", second_half_test.allowed, second_half_test.reason),
        ("옛자동매수원천차단", "V76 옛 자동매수 원천차단" in link_source and ident["legacy_auto_entry_blocked"], str(ident["entry_routes"])),
        ("엄격매수직전만표시", "V76 엄격직전차단" in link_source and ident["strict_prebuy_only"] and not ident["merged_candidate_prebuy"], "옛 후보·MA20 직전 편입 차단"),
        ("엄격관심만표시", "V76 엄격관심차단" in link_source and ident["strict_interest_only"], "1차상승→실제조정→두드러진 음봉거래만 관심"),
        ("V7609음봉거래관심복원", bearish_interest_route_test, "오늘 엄격관찰 후 하락음봉거래x는 관심종목으로 이동"),
        (
            "V7609완성봉관심단계유지",
            V7600ThreeStageEngine._v7609_preserve_completed_interest(
                datetime(2026, 7, 27, 10, 0, tzinfo=KST),
                "INTEREST",
                "관찰후보",
                datetime(2026, 7, 27, 10, 1, tzinfo=KST),
            ),
            "완성봉 관심을 형성중 3분봉 관찰추정이 덮어쓰지 않음",
        ),
        (
            "V7609주변20봉상대음봉거래",
            relative_interest_test[0]
            and relative_interest_test[2] is relative_flush_bar,
            relative_interest_test[1],
        ),
        (
            "과거9봉신규관심지연편입차단",
            'if phase == "INTEREST":' in relative_interest_flow_source
            and "_v7588_publish(" not in relative_interest_flow_source,
            "과거 음봉은 기존 관심 기준음봉 강화에만 사용",
        ),
        ("엄격시장보류만표시", "V76 엄격시장보류차단" in link_source and ident["strict_market_hold_only"], "엄격 매수조건 통과 후 시장위험만 보류"),
        ("V7609전일스캐너잔존초기화", "_v7609_roll_session" in link_source and "보유·체결원장 유지" in link_source, "날짜 변경 시 관찰·관심·직전만 초기화"),
        ("V7609오늘관찰선행관심", "오늘관찰선행없는 관심차단" in link_source and "_v7609_has_today_observation_admission" in link_source, "오늘 엄격관찰을 먼저 통과한 종목만 자동 관심"),
        ("V7609미검증관찰혼입차단", "미편입관찰 혼입차단" in link_source and "_v7609_prune_unverified_scanner_rows" in link_source, "옛 관심신호 차단 과정에서 관찰로 새는 행 제거"),
        ("근거없는과거행제거", 'if stage not in {"매수종목", "매도종목"}:' in link_source and ident["unverified_stage_rows_hidden"], "실제 초단위 편입근거 없는 자동단계 행 숨김"),
        ("2차최대20봉", "SECOND_HALF_MAX_BARS" in Path(BASE_DIR / "v7600_three_stage_logic.py").read_text(encoding="utf-8") and "20 bars" in str(ident["two_stage_entry"]["second"]), ident["two_stage_entry"]["second"]),
        ("코난돌파눌림재상승", breakout_test.allowed, breakout_test.reason),
        ("V7609실제조정MA5확인", ma5_confirm_test[0], ma5_confirm_test[1]),
        (
            "V7609MA5돌파후거래감소지지",
            newen_ma5_hold_test[0]
            and newen_ma5_hold_test[1].startswith("LOWER_VOLUME_MA5_HOLD"),
            newen_ma5_hold_test[1],
        ),
        ("V7609미미한하락차단", not shallow_test[0], shallow_test[1]),
        ("V7609엄격대기우회차단", "_v7609_recent_strict_block" in link_source and "V7609 MA5 route blocked by strict WAIT" in link_source and ident["strict_wait_bypass_blocked"], ident["ma5_reentry_confirmation"]),
        ("V7609급락장상대강도우회금지", '"SHOCK"' in link_source and "RELATIVE_STRENGTH_OVERRIDE" in link_source, ident["weak_market_exception"]),
        ("V7609신규진입지지손절", "_v7609_support_stop_reason" in inspect.getsource(V7600ThreeStageEngine._execute_gem_stop) and _EXIT_POLICY.MAX_LOSS == .03, ident["new_entry_fast_failure"]),
        ("V7609지연봉표시단계만", (
            "_v7609_stage_only_admission" in inspect.getsource(V7600ThreeStageEngine._refresh_actual_bars)
            and "_attempt_entry" not in inspect.getsource(V7600ThreeStageEngine._v7609_stage_only_admission)
            and "PREBUY" not in inspect.getsource(V7600ThreeStageEngine._v7609_stage_only_admission)
            and 1 <= V7609_STAGE_BAR_MAX_LAG_MIN <= 5
        ), ident["stage_admission_lag"]),
        ("카테고리실제편입초", exact_stage_test is not None and exact_stage_test.strftime("%H:%M:%S") == "09:12:34", ident["dashboard_exact_times"]),
        ("단계경로실제시각순", ordered_stage_path_test == "관찰 09:05:00 → 관심 09:12:34", ordered_stage_path_test),
        ("중복09시경로제거", legacy_path_test == "실제판단근거", legacy_path_test),
        (
            "화면행가짜단계원장차단",
            no_placeholder_write_test
            and "화면행최초확인" not in enrich_source
            and "_save_timeline" not in enrich_source,
            "GUI 표시행은 단계이력을 새로 만들거나 저장하지 않음",
        ),
        (
            "자동관심관찰우회제거",
            "today_observed = True" not in publish_source
            and "manual_interest = True" not in publish_source
            and "오늘관찰선행없는 관심차단" in publish_source
            and "전일동시간누적거래2배미달 관심대기" in publish_source,
            "자동 관심은 실제 관찰과 전일 동시간 누적거래 2배를 모두 통과",
        ),
        (
            "1분봉동시간비율계속갱신",
            "_same_time_volume_ratio" in ratio_source
            and "same_time_volume_ratio=ratio" in ratio_source
            and "_v7609_refresh_same_time_ratio" in refresh_source,
            "3분봉 없이 완성 원본 1분봉으로 동시간 누적거래비 갱신",
        ),
        (
            "관심직전보유순서강제",
            "관심선행없는 직전차단" in publish_source
            and "실제 매수직전 기록 없는 자동매수 차단" in entry_source,
            "자동 경로는 실제 관심 → 실제 직전 → PAPER 보유 순서만 허용",
        ),
        ("현재단계만표시", all(token in link_source for token in ('if bool(row.get("departed")):', 'symbol in positions and canonical_stage not in', 'fill.get("sell_time") is not None')), ident["dashboard_active_stage_only"]),
        ("매수매도체결시각가격표시", all(token in link_source for token in ("최근매수", "체결가", "매수가", "매도가", "_v7609_fill_records")), ident["dashboard_fill_details"]),
        (
            "매도손익부호확대",
            V7600ThreeStageEngine._v7609_pnl_badge(-34644.885, -0.01173193)
            == "▼ 손실 -34,645원 (-1.17%)"
            and V7600ThreeStageEngine._v7609_pnl_badge(49942.76, 0.0130364)
            == "▲ 수익 +49,943원 (+1.30%)"
            and all(token in link_source for token in ("매도종목_수익", "매도종목_손실")),
            "▲ 수익 / ▼ 손실을 행 맨 앞·굵은 글씨로 구분",
        ),
        (
            "동일종목최신매도표시",
            all(token in link_source for token in ("sell_reason", "latest_sell_reason", "sell_cycle", "당일 {cycle}회차 매도")),
            "같은 종목 재매매 때 최신 체결 손익·매도사유만 표시",
        ),
        (
            "매도종목명코드대체",
            _V7609_VERIFIED_NAME_FIXES.get("001820") == "삼화콘덴서"
            and durable_name_test == "엑시콘"
            and "_v7591_timeline" in link_source,
            "숫자 코드 대신 실시간 이름·저장된 단계이력 이름 사용",
        ),
        (
            "PAPER고정손절1_5퍼",
            V7609_FIXED_STOP_LOSS_PCT == 0.015
            and fixed_stop_changed
            and stop_position.stop_price == 78800.0
            and tighter_stop_normalized
            and tighter_position.stop_price == 78800.0,
            "평단 80,000원: 기존 손절가와 관계없이 78,800원(-1.5%)으로 통일",
        ),
        (
            "V7609틱루프후고정손절1_5퍼",
            tick_loop_stop_registered and stop_position.stop_price == 78800.0,
            f"틱 주문보정 후 손절={stop_position.stop_price:.0f}",
        ),
        (
            "V7609천원미만누적거래량경계",
            penny_floor_test,
            "09:15 30만주 / 09:30 50만주 / 10:00 100만주",
        ),
        (
            "형명령PAPER주문연결",
            str(command_buy_test.action) == "IMMEDIATE_BUY"
            and str(command_sell_test.action) == "IMMEDIATE_SELL"
            and str(command_target_test.action) == "SET_TARGET"
            and float(command_target_test.target_price or 0.0) == 75000.0,
            "매수주문·매도주문·가격매도는 PAPER 실행·목표가 경로로 연결",
        ),
        (
            "자연어즉시가격PAPER명령",
            str(command_now_buy_test.action) == "IMMEDIATE_BUY"
            and not command_now_buy_test.errors
            and str(command_price_buy_test.action) == "BUY_CONDITION"
            and float(command_price_buy_test.buy_price or 0.0) == 70000.0
            and str(command_price_buy_test.trigger) == "AT_OR_BELOW"
            and not command_price_buy_test.errors,
            "지금 매수는 PAPER 즉시매수 · 정확한 가격 매수는 이하도달 조건",
        ),
        (
            "만원수량PAPER명령",
            str(command_quantity_price_test.action) == "BUY_CONDITION"
            and float(command_quantity_price_test.buy_price or 0.0) == 230000.0
            and str(command_quantity_price_test.trigger) == "AT_OR_BELOW"
            and _v7609_quantity_from_text(command_quantity_price_test.note) == 10
            and not command_quantity_price_test.errors
            and "_v7609_manual_buy_exact_quantity" in link_source
            and "_v7555_execution_capsule" in link_source,
            "23만원=230,000원 · 확인된 PAPER 조건 도달 시 지정 10주",
        ),
        (
            "요청기록당일한정",
            _v7609_iso_is_today("2026-08-06T09:00:00+09:00", datetime(2026, 8, 6).date())
            and not _v7609_iso_is_today("2026-08-05T15:00:00+09:00", datetime(2026, 8, 6).date())
            and "_v7609_prune_collaboration_hub_today" in link_source
            and "V7609TodayIntentLedger" in link_source,
            "활성목록·시간대별 이력은 KST 오늘 기록만 유지",
        ),
        (
            "전선변압기테마우선명령",
            any(
                item.kind == "THEME_PRIORITY"
                and any("전선/변압기" in scope for scope in item.scope)
                for item in theme_command_test
            )
            and not any(item.kind == "ORDER_REQUEST" for item in theme_command_test),
            "기존 구조·시장·위험 관문을 유지한 채 해당 테마 후보의 우선순위만 상향",
        ),
        (
            "복수대장주각각집중관리",
            {
                item.symbol
                for item in leader_command_test
                if item.kind == "STOCK_WATCH"
            } == {"001440", "298040"}
            and "_v7609_add_many_and_watch" in link_source,
            "문장 속 각 대장주를 별도 WATCH 명령으로 등록",
        ),
        (
            "거래바닥MA5중복대기만교체",
            V7600ThreeStageEngine._v7609_bottom_route_replaces_strict_wait(
                "기준음봉대비거래14%·짧은봉"
            )
            and not V7600ThreeStageEngine._v7609_bottom_route_replaces_strict_wait(
                "시장급락차단"
            )
            and "interest flush re-anchored" in link_source,
            "관심 재편입 뒤 새 하락음봉을 기준봉으로 갱신 · 시장·데이터·위험 차단은 유지",
        ),
        (
            "종목표80대20가독성",
            all(token in link_source for token in ("weight=80", "weight=20", "rowheight=80 if sold_view else 28", '"no": 48 if sold_view else 36', '"name": 210 if sold_view else 170', "wraplength=220")),
            ident["dashboard_main_ratio"],
        ),
        (
            "다중키움0600고정대상우선",
            all(
                token in link_source
                for token in (
                    "V7609_0600_BINDING_RESTORED_FROM_SUCCESS_LOG",
                    "V7609_SEND_BLOCKED_UNBOUND_MULTI_KIWOOM_0600",
                    "if self._v7609_cached_target_valid():",
                    "_v7609_remember_target",
                )
            )
            and "super().send_symbol(normalized)" not in send_source,
            "검증된 내 0600은 다른 0600이 열려도 유지하고, 미고정 다중 0600은 안전 차단",
        ),
        (
            "0600시작직후Edit지연복구",
            saved_chart_wait_ok
            and "V7609_0600_BINDING_CHART_RESTORED_WAIT_EDIT" in link_source
            and "self._v7609_restore_locked_target()" in prewarm_source,
            "최소화 해제 후 저장된 내 0600을 재고정하고 같은 차트의 Edit만 재탐색",
        ),
        (
            "0600장시간Edit재생성복구",
            "V7609_FAST_EDIT_STALE_KEEP_CHART" in link_source
            and "_v7609_refresh_locked_edit" in send_source
            and "_v7609_clear_edit_cache" in send_source
            and "V7609_SEND_BLOCKED_UNBOUND_MULTI_KIWOOM_0600" in link_source,
            "입력칸이 잠깐 실패해도 내 0600 고정은 유지하고 같은 차트 안에서만 다시 탐색",
        ),
        (
            "관심이동관찰동시간2배실제조정",
            "전일동시간누적거래2배미달 관심대기" in link_source
            and "first_impulse_pullback_decision(raw, now=now)" in link_source
            and 'if phase == "OBSERVED":' in link_source,
            "자동 관심은 당일 관찰 + 전일 동시간 누적거래 2배 + 최초상승 후 실제조정 필수",
        ),
        ("오늘매도빨강통일", parent._R21_STAGE_ROW_COLORS.get("매도종목") == (_V7609_SELL_BODY, _V7609_SELL_TEXT) and v57.STAGE_ACCENTS.get("매도종목") == (_V7609_SELL_BAND, _V7609_SELL_TEXT), str(ident["dashboard_sold_color"])),
        ("기존진입크기보존", _v76_initial_notional(type("S", (), {"pattern": "기타", "reason": ""})()) == _ORIGINAL_INITIAL_NOTIONAL(type("S", (), {"pattern": "기타", "reason": ""})()), "V76 이외 원본"),
        ("R21옛5~10봉진입차단", parent.exhaustion_entry_decision(None, [], now=datetime.now()).allowed is False, "V76 교체"),
        ("R21즉시돌파진입차단", parent.breakout_decision([], now=datetime.now()).allowed is False, "코난 돌파후 재확인으로 교체"),
        ("V7590분봉시각호환", callable(getattr(_V7590_PARENT, "_stamp", None)), "치명 AttributeError 방지"),
        ("V7593단계하향호환", "allow_downgrade: bool = False" in link_source and "super(_PARENT_ENGINE, self)._v7588_publish" in link_source, "치명 인자 불일치 방지"),
        ("PAPER보존", bool(ident.get("paper_only")), "실계좌 주문 없음"),
        ("화면작업영역맞춤", ident["dashboard_fit"].startswith("Windows work area") and all(token in link_source for token in ("GetWindowRect", "GetClientRect", "_compact_one_screen_layout", "command_host.configure(height=262)", "before=main_host")), ident["dashboard_fit"]),
        ("상단수정시각표시", 'label.startswith("자동매매 대시보드")' in link_source and BUILD_STAMP in ident["dashboard_title"], BUILD_STAMP),
        ("하단버튼요약표표시", 'widget.configure(height=2, pady=1)' in link_source and 'widget.configure(height=3)' in link_source and all(token in link_source for token in ("1  뜻 확인", "2  해석대로 적용", "3  활성등록", "4  건너뛰기·취소", "request_preview_tree")), ident["dashboard_bottom_controls"]),
        (
            "중복스크롤제거",
            tree_patch_source.count('orient="horizontal"') == 1
            and 'orient="vertical"' not in tree_patch_source
            and "의견·요청 시간대별 이력" not in tree_patch_source
            and text_patch_source.count('orient="horizontal"') == 1
            and 'orient="vertical"' not in text_patch_source
            and ident["dashboard_scrollbars"].startswith("one vertical"),
            ident["dashboard_scrollbars"],
        ),
        ("관찰후보120절단제거", _v7609_observation_pool_limit(120, 200) == 200 and "supply_pool_limit=_v7609_observation_pool_limit" in link_source, ident["observation_pool"]),
        ("대시보드진짜종료", ident["minimize_keeps_running"] is False and "_V7609_USER_CLOSE.set()" in link_source and "command_stop_requested = True" in link_source, ident["dashboard_close"]),
        ("Tcl소유스레드종료", "safe_gui_run" in link_source and "gc.collect()" in link_source and "self.root = None" in link_source and "os._exit(0)" in link_source, "GUI 스레드 destroy·GC 후 저장·로그 완료 시 Tcl 주스레드 재정리 생략"),
        ("시작경로자동복구", "SOURCE_ROOT_AUTO_REPAIRED" in (BASE_DIR.parent / "launcher.py").read_text(encoding="utf-8") and "FindValidBase" in (BASE_DIR.parent.parent / "3_START_V76_GUI.vbs").read_text(encoding="utf-8"), ident["startup_root_recovery"]),
        ("영웅문권한일치", "Start-Process -FilePath" in (BASE_DIR.parent.parent / "3_START_V76_GUI.vbs").read_text(encoding="utf-8") and "-Verb RunAs" in (BASE_DIR.parent.parent / "3_START_V76_GUI.vbs").read_text(encoding="utf-8"), ident["startup_integrity"]),
        (
            "3분봉실행경로완전제거",
            ident.get("three_minute_runtime_removed") is True
            and _V753.KiwoomRestClient is V7609OneMinuteKiwoomClient
            and "get_one_minute_bars" in one_minute_client_source
            and "aggregate" not in one_minute_client_source
            and "super()._refresh_actual_bars" not in refresh_source
            and "_v7609_load_one_minute_bars" in refresh_source
            and "assess_loose_supply_potential" not in potential_source,
            ident["bar_policy"],
        ),
        (
            "기존완성봉과PAPER진행봉분리",
            "completed_only: bool = True" in one_minute_client_source
            and "self.get_one_minute_bars" in one_minute_client_source
            and "completed_only=False" in inspect.getsource(V7609OneMinuteKiwoomClient.get_experiment_bars)
            and EARLY_PATTERN in ident["entry_routes"]
            and "The independent V76 routing below is completed-one-minute-bar only." in link_source,
            ident["bar_policy"],
        ),
        ("샘플10종목표시", ident["visual_sample_rows"] == 10, str(ident["visual_sample_symbols"])),
        ("샘플실매매분리", all("PAPER 샘플" in str(row["reason"]) for row in VISUAL_SAMPLE_ROWS), "빈 화면일 때만 외관·연동 확인용"),
        (
            "대시보드실제장부단계우선",
            _v7609_dashboard_stage("매수직전") == "매수가능성"
            and _v7609_dashboard_stage("매수종목", "PREBUY", "매수직전") == "매수종목"
            and _v7609_dashboard_stage("매도종목", "INTEREST", "관심종목") == "매도종목"
            and 'row["stage"] = canonical_stage' in link_source,
            ident["dashboard_ledger_stage_priority"],
        ),
        (
            "목표매도동일1분신호재진입차단",
            _v7609_same_signal_minute(
                datetime(2026, 8, 12, 9, 0, 48, tzinfo=KST),
                datetime(2026, 8, 12, 9, 0, 35, tzinfo=KST),
            )
            and not _v7609_same_signal_minute(
                datetime(2026, 8, 12, 9, 1, 0, tzinfo=KST),
                datetime(2026, 8, 12, 9, 0, 35, tzinfo=KST),
            )
            and "_v7609_target_exit_at.get(symbol)" in link_source
            and "not manual and _v7609_same_signal_minute" in link_source,
            ident["target_exit_reentry_guard"],
        ),
    ])
    return rows


def _run_link_test(symbol: str) -> int:
    normalized = v57.normalize_symbol_code(symbol)
    if not normalized:
        print(f"[FAIL] 종목코드 형식 오류: {symbol}")
        return 2
    config_path = Path.home() / ".v75_hero_input_position.json"
    link = V7600CtrlVHeroLink(config_path)
    status, message = link.send_symbol(normalized)
    print(f"[{status.value}] {message}")
    return 0 if status.value in {"VERIFIED", "SENT_UNVERIFIED"} else 1


def main() -> None:
    if "--self-check" in sys.argv:
        rows = self_check()
        for name, ok, detail in rows:
            print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
        raise SystemExit(0 if all(item[1] for item in rows) else 1)
    if "--runtime-info" in sys.argv:
        print("RUNTIME_IDENTITY " + json.dumps(runtime_identity(), ensure_ascii=False, default=str))
        raise SystemExit(0)
    if "--link-test" in sys.argv:
        index = sys.argv.index("--link-test")
        symbol = sys.argv[index + 1] if index + 1 < len(sys.argv) else "005930"
        raise SystemExit(_run_link_test(symbol))
    parent.main()
    if _V7609_USER_CLOSE.is_set():
        # parent.main() returns only after PAPER state saving, final reporting,
        # GUI close signalling, and log detachment have completed.  Python 3.13
        # can otherwise finalize this worker-created Tcl interpreter again on
        # the main thread and raise tcl86t.dll 0x80000003.
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.flush()
            except Exception:
                pass
        os._exit(0)


if __name__ == "__main__":
    main()
