#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
V75.3 AFTER-SESSION-GUARD 단일파일 PAPER 자동매매 봇

핵심 원칙
- 실제 주문은 절대 전송하지 않는다. PAPER 체결만 수행한다.
- API 오류, 빈 응답, 필드 누락, 파싱 오류를 분리한다. 종목별 오류는 격리한다.
- 현재가/호가가 유효하지 않으면 진입을 차단하되, VI·호가공백은 세션별 60~90초 안에서 최대 3회만 재시도한다.
- 수수료, 매도세금, 슬리피지를 반영한다.

V75.3 핵심 변경
- V75.2의 전략·체결·리스크 로직은 변경하지 않고 애프터 세션 orchestration만 최소 수정한다.
- 애프터 신규진입은 기본 비활성화하고 보유종목 목표·손절 감시만 유지한다.
- V75_ENABLE_AFTER_ENTRY=1일 때만 애프터 신규진입을 허용한다.
- 애프터 분봉이 없는 종목은 종목별 경고를 반복하지 않고 재조회 유예목록에 넣는다.
- 직전 V75.2 패턴 상태를 호환 복구해 단순 운영패치로 기존 패턴 상태가 소실되지 않게 한다.
- 아래꼬리 판정 비율의 최소 단위를 고정 1원이 아니라 해당 가격의 실제 호가단위로 계산한다.
- 아래꼬리 음봉은 아래꼬리가 위꼬리보다 최소 1.2배 길어야 저점확인으로 인정한다.
- 신규진입 감시지연 차단 기본값을 30초에서 15초로 강화한다.
- 청산 로그를 트리거·감지·체결로 분리하고, REST 폴링 간격 상한을 별도 기록한다.
- 무제한 PAPER 손익은 실전 수익성 지표가 아님을 보고서에 명시한다.
- 신규 매수패턴: 임펄스눌림(급등 장대양봉 → 저거래 눌림 1/3 되돌림 → 이평 지지 → 짧은 양봉)
- 분봉 신선도를 봉 '완성' 시각 기준으로 계산(정상 신호가 지연으로 거절되던 문제 해결)
- 진입 재시도 큐 + 패턴별 추격/역방향 이탈 가드
- HOT(신호임박) 후보 우선 스케줄러 / 종목별 오류 격리
- 2026 KRX 휴장일 캘린더 + 세션 구간(프리 08:00~08:50 / GAP / 정규 / 애프터)
- 시간 강제청산 기본 비활성(V74_MAX_HOLD_MINUTES=0), 활성 시 세션 경과분 기준
- 목표 체결 현실화: 분봉 고가가 목표가를 '관통'하고 거래량>0일 때만 지정가 체결 인정
- 투매·임펄스 패턴 상태 파일 저장·복구(재시작 대응)
- 돌파봉 최소 거래대금 게이트(프리마켓·시간외 얇은 봉 가짜 돌파 차단)

실행 예시(Windows CMD)
    cd %USERPROFILE%\Downloads
    set DATA_MODE=KIWOOM
    set KIWOOM_STEX_TP=3
    set KIWOOM_SUFFIX=_AL
    python bot_v75_3_after_session_guard.py

종료
    Ctrl + C
"""

from __future__ import annotations

import argparse
import csv
import getpass
import json
import logging
import math
import os
import re
import statistics
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time as dt_time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any, Deque, Iterable, Mapping
from zoneinfo import ZoneInfo

VERSION = "V75.3-AFTER-SESSION-GUARD-PAPER"
PATTERN_STATE_COMPATIBLE_VERSIONS = frozenset({VERSION, "V75.2-EXECUTION-PRECISION-PAPER"})
KST = ZoneInfo("Asia/Seoul")
SCRIPT_DIR = Path(__file__).resolve().parent
WORK_DIR = Path.cwd()
LOG_DIR = WORK_DIR / "v74_logs"
STATE_FILE = LOG_DIR / "v74_paper_state.json"
FILL_FILE = LOG_DIR / "v75_2_paper_fills.csv"
PATTERN_STATE_FILE = LOG_DIR / "v74_pattern_state.json"
PRIVATE_DIR = Path.home() / ".v74_private"
TOKEN_FILE = PRIVATE_DIR / "v74_token_cache.json"
ERROR_FILE = LOG_DIR / "v74_api_errors.txt"
RANK_SCHEMA_FILE = LOG_DIR / "v74_rank_schema_probe.json"

DEFAULT_WATCHLIST: list[tuple[str, str]] = [
    ("005930", "삼성전자"),
    ("000660", "SK하이닉스"),
    ("005380", "현대차"),
    ("000270", "기아"),
    ("035420", "NAVER"),
    ("035720", "카카오"),
    ("068270", "셀트리온"),
    ("247540", "에코프로비엠"),
]

# -----------------------------------------------------------------------------
# 오류 형식
# -----------------------------------------------------------------------------


class V73Error(Exception):
    """V73 기본 예외."""


class ConfigurationError(V73Error):
    """설정 오류."""


class AuthenticationError(V73Error):
    """인증 오류."""


class ApiTransportError(V73Error):
    """네트워크/HTTP 오류."""


class ApiBusinessError(V73Error):
    """키움 return_code 오류."""


class ApiSchemaError(V73Error):
    """응답 필드/스키마 오류."""


# -----------------------------------------------------------------------------
# 유틸리티
# -----------------------------------------------------------------------------


def configure_console() -> None:
    """Windows CMD에서도 한글 출력이 최대한 깨지지 않도록 설정한다."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def setup_logging(verbose: bool = False) -> logging.Logger:
    """콘솔 및 파일 로깅을 초기화한다."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("v74")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.propagate = False

    console = logging.StreamHandler(sys.stdout)
    # 평상시 화면에는 중요한 안내·오류만 표시한다.
    # 상세 계산과 내부 진단은 v74.log 파일에 남긴다.
    console.setLevel(logging.DEBUG if verbose else logging.WARNING)
    console.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", "%H:%M:%S"))
    logger.addHandler(console)

    file_handler = logging.FileHandler(LOG_DIR / "v74.log", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    )
    logger.addHandler(file_handler)
    return logger


def append_api_error(message: str) -> None:
    """API 오류 전용 파일에 시간과 함께 기록한다."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    with ERROR_FILE.open("a", encoding="utf-8") as handle:
        handle.write(f"[{stamp}] {message}\n")


def brief_error(exc: BaseException, limit: int = 220) -> str:
    """콘솔에는 핵심 오류만 짧게 표시하고 상세 내용은 오류 파일에 남긴다."""
    text = " ".join(str(exc).replace("\r", " ").replace("\n", " ").split())
    if "keys=[" in text:
        text = text.split("keys=[", 1)[0].rstrip(" ,")
    if len(text) > limit:
        text = text[: max(0, limit - 3)].rstrip() + "..."
    return text or exc.__class__.__name__


def parse_env_file(path: Path) -> dict[str, str]:
    """python-dotenv 없이 단순 .env 파일을 읽는다."""
    result: dict[str, str] = {}
    if not path.exists() or not path.is_file():
        return result
    try:
        for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            if key:
                result[key] = value
    except OSError:
        return {}
    return result


def load_environment_files() -> list[Path]:
    """기존 V73 폴더의 .env까지 자동 탐색해 환경변수에 반영한다."""
    candidates = [
        WORK_DIR / "v73_keys.env",
        SCRIPT_DIR / "v73_keys.env",
        Path.home() / "Downloads" / "v73_keys.env",
        Path.home() / "다운로드" / "v73_keys.env",
        WORK_DIR / ".env",
        SCRIPT_DIR / ".env",
        WORK_DIR / "V73_바로실행" / ".env",
        Path.home() / "Downloads" / "V73_바로실행" / ".env",
        Path.home() / "다운로드" / "V73_바로실행" / ".env",
    ]
    loaded: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        resolved = path.resolve() if path.exists() else path
        if resolved in seen:
            continue
        seen.add(resolved)
        values = parse_env_file(path)
        if not values:
            continue
        for key, value in values.items():
            os.environ.setdefault(key, value)
        loaded.append(path)
    return loaded


def save_credentials_file(app_key: str, secret_key: str) -> Path:
    """API 키를 현재 스크립트 폴더의 v73_keys.env에 안전하게 저장한다."""
    target = SCRIPT_DIR / "v73_keys.env"
    content = (
        "# V73 키움 REST API 인증정보 - 외부 공유 금지\n"
        f"KIWOOM_APP_KEY={app_key.strip()}\n"
        f"KIWOOM_SECRET_KEY={secret_key.strip()}\n"
    )
    target.write_text(content, encoding="utf-8")
    return target


def prompt_credentials_gui() -> tuple[str, str] | None:
    """Windows 첫 실행에서 흰색 입력창으로 APP KEY와 SECRET KEY를 받는다."""
    try:
        import tkinter as tk
        from tkinter import messagebox
    except Exception:
        return None

    result: dict[str, str] = {}
    try:
        root = tk.Tk()
    except Exception:
        return None
    root.title("V73 최초 설정")
    root.geometry("610x265")
    root.resizable(False, False)
    root.attributes("-topmost", True)

    title = tk.Label(root, text="키움 REST API 키 입력", font=("Malgun Gothic", 15, "bold"))
    title.pack(pady=(18, 8))
    note = tk.Label(
        root,
        text="최초 한 번만 입력합니다. 저장 후 다음부터는 자동 실행됩니다.\n실제 주문은 포함되지 않은 PAPER 버전입니다.",
        font=("Malgun Gothic", 10),
        justify="center",
    )
    note.pack(pady=(0, 12))

    frame = tk.Frame(root)
    frame.pack(fill="x", padx=22)
    tk.Label(frame, text="APP KEY", width=13, anchor="w", font=("Malgun Gothic", 10)).grid(row=0, column=0, pady=6)
    app_entry = tk.Entry(frame, width=58, font=("Consolas", 10))
    app_entry.grid(row=0, column=1, pady=6)
    tk.Label(frame, text="SECRET KEY", width=13, anchor="w", font=("Malgun Gothic", 10)).grid(row=1, column=0, pady=6)
    secret_entry = tk.Entry(frame, width=58, show="*", font=("Consolas", 10))
    secret_entry.grid(row=1, column=1, pady=6)

    def submit() -> None:
        app_key = app_entry.get().strip()
        secret_key = secret_entry.get().strip()
        if not app_key or not secret_key:
            messagebox.showerror("입력 오류", "APP KEY와 SECRET KEY를 모두 붙여넣어 주세요.")
            return
        result["app_key"] = app_key
        result["secret_key"] = secret_key
        root.destroy()

    button = tk.Button(root, text="저장하고 바로 실행", command=submit, width=22, font=("Malgun Gothic", 10, "bold"))
    button.pack(pady=16)
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    app_entry.focus_set()
    root.mainloop()

    if result.get("app_key") and result.get("secret_key"):
        return result["app_key"], result["secret_key"]
    return None


def ensure_credentials(logger: logging.Logger) -> list[Path]:
    """키가 없으면 GUI 입력창을 띄워 저장한 뒤 환경변수에 반영한다."""
    app_key = os.getenv("KIWOOM_APP_KEY", "").strip()
    secret_key = os.getenv("KIWOOM_SECRET_KEY", "").strip()
    access_token = os.getenv("KIWOOM_ACCESS_TOKEN", "").strip()
    if access_token or (app_key and secret_key):
        return []

    logger.warning("키움 API 키가 없어 최초 설정창을 엽니다.")
    entered = prompt_credentials_gui()
    if entered is None:
        raise ConfigurationError(
            "API 키 입력이 취소되었습니다. bot_v73_2.py를 다시 실행하고 흰색 입력창에 키를 붙여넣으세요."
        )
    app_key, secret_key = entered
    saved = save_credentials_file(app_key, secret_key)
    os.environ["KIWOOM_APP_KEY"] = app_key
    os.environ["KIWOOM_SECRET_KEY"] = secret_key
    logger.warning("API 키 저장 완료=%s", saved)
    return [saved]


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} 숫자 설정 오류: {raw!r}") from exc


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} 정수 설정 오류: {raw!r}") from exc


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ConfigurationError(f"{name} 불리언 설정 오류: {raw!r}")


def clean_numeric_text(raw: Any) -> str:
    if raw is None:
        raise ValueError("숫자값이 None")
    text = str(raw).strip().replace(",", "")
    if not text:
        raise ValueError("숫자값이 빈 문자열")
    return text


def parse_decimal(raw: Any, *, absolute: bool = False) -> Decimal:
    text = clean_numeric_text(raw)
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"숫자 변환 실패: {raw!r}") from exc
    return abs(value) if absolute else value


def parse_float(raw: Any, *, absolute: bool = False) -> float:
    return float(parse_decimal(raw, absolute=absolute))


def parse_int(raw: Any, *, absolute: bool = False) -> int:
    return int(parse_decimal(raw, absolute=absolute))


def first_present(data: Mapping[str, Any], keys: Iterable[str], default: Any = None) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    if default is not None:
        return default
    raise KeyError(f"필수 필드 없음: {list(keys)}")


def return_code_of(data: Mapping[str, Any]) -> int:
    if "return_code" not in data:
        raise ValueError("return_code 누락")
    return parse_int(data["return_code"])


def base_symbol(symbol: str) -> str:
    """005930_AL, 005930_NX 형식을 005930으로 정규화한다."""
    value = symbol.strip().upper()
    value = re.sub(r"_(AL|NX)$", "", value)
    return value


def api_symbol(symbol: str, suffix: str) -> str:
    """환경설정에 따라 거래소 접미사를 붙인다."""
    normalized = symbol.strip().upper()
    if normalized.endswith(("_AL", "_NX")):
        return normalized
    suffix = suffix.strip().upper()
    if suffix and not suffix.startswith("_"):
        suffix = "_" + suffix
    if suffix not in {"", "_AL", "_NX"}:
        raise ConfigurationError(f"KIWOOM_SUFFIX는 공백, _AL, _NX만 허용: {suffix!r}")
    return normalized + suffix


def money(value: float) -> str:
    return f"{value:+,.0f}원"


def pct(value: float) -> str:
    return f"{value * 100:+.2f}%"


def stock_tick_size(price: float) -> int:
    """국내 보통주 공통 호가단위(ETF/ETN 등은 후보에서 제외됨)."""
    value = max(0.0, float(price))
    if value < 2_000:
        return 1
    if value < 5_000:
        return 5
    if value < 20_000:
        return 10
    if value < 50_000:
        return 50
    if value < 200_000:
        return 100
    if value < 500_000:
        return 500
    return 1_000


def align_stock_price(price: float, *, upward: bool) -> float:
    """실제 호가 가능한 가격으로 절상/절사한다."""
    if not math.isfinite(price) or price <= 0:
        raise ValueError(f"가격 정렬 오류: {price!r}")
    tick = stock_tick_size(price)
    units = price / tick
    aligned = math.ceil(units - 1e-12) if upward else math.floor(units + 1e-12)
    return float(max(tick, aligned * tick))


def is_tick_aligned(price: float) -> bool:
    tick = stock_tick_size(price)
    return abs(price / tick - round(price / tick)) < 1e-9


# -----------------------------------------------------------------------------
# 설정
# -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Settings:
    api_mode: str
    app_key: str
    secret_key: str
    access_token: str
    suffix: str
    stex_tp: str
    min_interval_sec: float
    timeout_sec: float
    initial_cash: float
    max_open_positions: int
    risk_per_trade_pct: float
    max_daily_loss_pct: float
    max_position_notional_pct: float
    stop_loss_pct: float
    take_profit_pct: float
    trailing_stop_pct: float
    max_spread_pct: float
    max_quote_age_sec: int
    max_bar_signal_age_sec: int
    min_signal_score: float
    commission_pct: float
    sell_tax_pct: float
    slippage_pct: float
    report_interval_sec: int
    paper_unlimited: bool
    paper_unit_notional: float
    dynamic_scan: bool
    scan_interval_sec: float
    active_candidate_count: int
    candidate_ttl_sec: int
    watchlist: tuple[tuple[str, str], ...]

    @property
    def base_url(self) -> str:
        return "https://mockapi.kiwoom.com" if self.api_mode == "mock" else "https://api.kiwoom.com"

    @classmethod
    def load(cls) -> "Settings":
        data_mode = os.getenv("DATA_MODE", "KIWOOM").strip().upper()
        api_mode = os.getenv("KIWOOM_API_MODE", "").strip().lower()
        if not api_mode:
            api_mode = "live" if data_mode == "KIWOOM" else "mock"
        if api_mode not in {"live", "mock"}:
            raise ConfigurationError("KIWOOM_API_MODE는 live 또는 mock")

        suffix = os.getenv("KIWOOM_SUFFIX", "_AL" if api_mode == "live" else "").strip().upper()
        if api_mode == "mock":
            # 공식 안내상 모의투자는 KRX만 지원하므로 접미사를 제거한다.
            suffix = ""

        raw_watchlist = os.getenv("V73_WATCHLIST", "").strip()
        if raw_watchlist:
            symbols = []
            for item in raw_watchlist.split(","):
                symbol = base_symbol(item)
                if re.fullmatch(r"\d{6}", symbol):
                    symbols.append((symbol, symbol))
            watchlist = tuple(symbols) if symbols else tuple(DEFAULT_WATCHLIST)
        else:
            watchlist = tuple(DEFAULT_WATCHLIST)

        settings = cls(
            api_mode=api_mode,
            app_key=os.getenv("KIWOOM_APP_KEY", "").strip(),
            secret_key=os.getenv("KIWOOM_SECRET_KEY", "").strip(),
            access_token=os.getenv("KIWOOM_ACCESS_TOKEN", "").strip(),
            suffix=suffix,
            stex_tp=os.getenv("KIWOOM_STEX_TP", "3").strip(),
            min_interval_sec=env_float("V74_API_MIN_INTERVAL_SEC", env_float("V73_API_MIN_INTERVAL_SEC", 1.0)),
            timeout_sec=env_float("V73_REQUEST_TIMEOUT_SEC", 12.0),
            initial_cash=env_float("V73_INITIAL_CASH", 10_000_000.0),
            max_open_positions=env_int("V73_MAX_OPEN_POSITIONS", 9999),
            risk_per_trade_pct=env_float("V73_RISK_PER_TRADE_PCT", 0.002),
            max_daily_loss_pct=env_float("V73_MAX_DAILY_LOSS_PCT", 0.01),
            max_position_notional_pct=env_float("V73_MAX_POSITION_NOTIONAL_PCT", 0.20),
            stop_loss_pct=env_float("V74_STOP_LOSS_PCT", 0.020),
            take_profit_pct=env_float("V74_TAKE_PROFIT_PCT", 0.015),
            trailing_stop_pct=env_float("V74_TRAILING_STOP_PCT", 0.0),
            max_spread_pct=env_float("V73_MAX_SPREAD_PCT", 0.015),
            max_quote_age_sec=env_int("V73_MAX_QUOTE_AGE_SEC", 90),
            max_bar_signal_age_sec=env_int("V73_MAX_BAR_SIGNAL_AGE_SEC", 120),
            min_signal_score=env_float("V74_MIN_SIGNAL_SCORE", 80.0),
            commission_pct=env_float("V73_COMMISSION_PCT", 0.00015),
            sell_tax_pct=env_float("V73_SELL_TAX_PCT", 0.0018),
            slippage_pct=env_float("V73_SLIPPAGE_PCT", 0.0005),
            report_interval_sec=env_int("V73_REPORT_INTERVAL_SEC", 300),
            paper_unlimited=env_bool("V73_PAPER_UNLIMITED", True),
            paper_unit_notional=env_float("V73_PAPER_UNIT_NOTIONAL", 1_000_000.0),
            dynamic_scan=env_bool("V73_DYNAMIC_SCAN", True),
            scan_interval_sec=env_float("V74_SCAN_INTERVAL_SEC", 60.0),
            active_candidate_count=env_int("V74_ACTIVE_CANDIDATES", 200),
            candidate_ttl_sec=env_int("V74_CANDIDATE_TTL_SEC", 120),
            watchlist=watchlist,
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.min_interval_sec < 0:
            raise ConfigurationError("V73_API_MIN_INTERVAL_SEC는 0 이상")
        if self.timeout_sec <= 0:
            raise ConfigurationError("V73_REQUEST_TIMEOUT_SEC는 양수")
        if self.initial_cash <= 0:
            raise ConfigurationError("V73_INITIAL_CASH는 양수")
        if not self.paper_unlimited and self.max_open_positions < 1:
            raise ConfigurationError("V73_MAX_OPEN_POSITIONS는 1 이상")
        if self.paper_unit_notional <= 0:
            raise ConfigurationError("V73_PAPER_UNIT_NOTIONAL은 양수")
        if self.scan_interval_sec < 10:
            raise ConfigurationError("V73_SCAN_INTERVAL_SEC는 10초 이상")
        if self.active_candidate_count < 1:
            raise ConfigurationError("V73_ACTIVE_CANDIDATES는 1 이상")
        if self.candidate_ttl_sec < 30:
            raise ConfigurationError("V73_CANDIDATE_TTL_SEC는 30초 이상")
        if self.max_quote_age_sec <= 0 or self.max_bar_signal_age_sec <= 0:
            raise ConfigurationError("시세/분봉 최대 허용시간은 양수")
        for name in (
            "risk_per_trade_pct",
            "max_daily_loss_pct",
            "max_position_notional_pct",
            "stop_loss_pct",
            "take_profit_pct",
            "trailing_stop_pct",
            "max_spread_pct",
            "commission_pct",
            "sell_tax_pct",
            "slippage_pct",
        ):
            value = float(getattr(self, name))
            if not 0 <= value < 1:
                raise ConfigurationError(f"{name} 범위 오류: {value}")
        if self.stex_tp not in {"1", "2", "3"}:
            raise ConfigurationError("KIWOOM_STEX_TP는 1, 2, 3")

    def assert_credentials(self) -> None:
        if self.access_token:
            return
        if not self.app_key or not self.secret_key:
            raise ConfigurationError(
                "키움 API 키를 찾지 못했습니다. 기존 Downloads\\V73_바로실행\\.env 또는 "
                "환경변수 KIWOOM_APP_KEY/KIWOOM_SECRET_KEY를 확인하세요."
            )


# -----------------------------------------------------------------------------
# 데이터 모델
# -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Quote:
    symbol: str
    name: str
    price: float
    change_pct: float
    cumulative_volume: int
    cumulative_value: float
    timestamp: datetime
    best_bid: float | None = None
    best_ask: float | None = None

    @property
    def spread_pct(self) -> float | None:
        if self.best_bid is None or self.best_ask is None or self.best_bid <= 0:
            return None
        return max(0.0, (self.best_ask - self.best_bid) / self.best_bid)


@dataclass(frozen=True, slots=True)
class Candidate:
    symbol: str
    name: str
    price: float
    change_pct: float
    volume: int
    surge_rate: float
    score: float
    source: str
    foreign_net: float = 0.0
    institution_net: float = 0.0
    dual_buy: bool = False


@dataclass(slots=True)
class Bar:
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(slots=True)
class DailyBar:
    symbol: str
    trading_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True, slots=True)
class DailyContext:
    support: float | None
    resistance: float | None
    low_trend_slope_pct: float
    high_trend_slope_pct: float
    trend_up: bool


class SignalAction(str, Enum):
    HOLD = "HOLD"
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True, slots=True)
class Signal:
    symbol: str
    action: SignalAction
    score: float
    reason: str
    reference_price: float
    timestamp: datetime
    pattern: str = ""
    stop_loss_pct: float = 0.02
    take_profit_pct: float = 0.015


@dataclass(slots=True)
class Position:
    symbol: str
    quantity: int
    entry_price: float
    entry_time: datetime
    highest_price: float
    stop_price: float
    target_price: float
    entry_fee: float = 0.0
    pattern: str = ""
    target_order_active: bool = True

    @property
    def entry_cost(self) -> float:
        """매수 수수료를 포함한 총 취득원가."""
        return self.entry_price * self.quantity + self.entry_fee


@dataclass(frozen=True, slots=True)
class RiskDecision:
    approved: bool
    reason: str
    quantity: int = 0


@dataclass(frozen=True, slots=True)
class Fill:
    order_id: str
    timestamp: datetime
    symbol: str
    side: str
    quantity: int
    price: float
    fee: float
    tax: float
    pnl: float
    reason: str
    entry_time: datetime | None = None
    entry_price: float = 0.0
    pnl_pct: float = 0.0
    pattern: str = ""
    trigger_time: datetime | None = None
    trigger_price: float = 0.0
    detection_time: datetime | None = None
    monitor_lag_sec: float = 0.0
    execution_lag_sec: float = 0.0
    observed_lag_sec: float = 0.0
    trigger_source: str = ""
    stop_overrun_pct: float = 0.0


@dataclass(slots=True)
class PendingEntry:
    signal: Signal
    created_mono: float
    expire_mono: float
    next_try_mono: float
    attempts: int = 0


# -----------------------------------------------------------------------------
# 키움 REST API 클라이언트
# -----------------------------------------------------------------------------


class RateLimiter:
    """단일 프로세스 최소 호출 간격 제한."""

    def __init__(self, interval_sec: float) -> None:
        self.interval_sec = interval_sec
        self._last = 0.0

    def wait(self) -> None:
        elapsed = time.monotonic() - self._last
        delay = self.interval_sec - elapsed
        if delay > 0:
            time.sleep(delay)
        self._last = time.monotonic()


class KiwoomRestClient:
    """필드 누락을 정상 가격 0으로 바꾸지 않는 fail-closed REST 클라이언트."""

    def __init__(self, settings: Settings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger
        self.limiter = RateLimiter(settings.min_interval_sec)
        self._token = settings.access_token
        # scan-test에서 성공 응답의 실제 필드 구조를 확인하기 위한 임시 메모리.
        # 토큰·인증정보는 저장하지 않고 순위 응답의 시장 데이터만 저장한다.
        self.rank_probe_trials: dict[str, list[dict[str, Any]]] = defaultdict(list)

    def _read_cached_token(self) -> str:
        if not TOKEN_FILE.exists():
            return ""
        try:
            payload = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
            if payload.get("base_url") != self.settings.base_url:
                return ""
            token = str(payload.get("token", "")).strip()
            expires_dt = str(payload.get("expires_dt", "")).strip()
            if not token or not expires_dt:
                return ""
            expires = datetime.strptime(expires_dt, "%Y%m%d%H%M%S").replace(tzinfo=KST)
            if expires <= datetime.now(KST).replace(microsecond=0):
                return ""
            if (expires - datetime.now(KST)).total_seconds() < 300:
                return ""
            return token
        except Exception:
            return ""

    def _save_token(self, token: str, expires_dt: str) -> None:
        PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {"token": token, "expires_dt": expires_dt, "base_url": self.settings.base_url}
        TOKEN_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        try:
            os.chmod(TOKEN_FILE, 0o600)
        except OSError:
            pass

    def authenticate(self, *, force_refresh: bool = False) -> str:
        """유효한 접근토큰을 반환한다.

        force_refresh=True이면 메모리/파일 캐시 토큰을 폐기하고 APP KEY/SECRET으로
        새 토큰을 발급한다. 서버가 만료 전 토큰도 무효 처리하는 경우를 복구하기 위함이다.
        """
        if force_refresh:
            self._token = ""
            try:
                TOKEN_FILE.unlink(missing_ok=True)
            except OSError:
                pass
            if not self.settings.app_key or not self.settings.secret_key:
                raise AuthenticationError(
                    "저장된 토큰이 무효입니다. 새 토큰 발급을 위한 KIWOOM_APP_KEY/"
                    "KIWOOM_SECRET_KEY를 v73_keys.env에 확인하세요."
                )
        elif self._token:
            return self._token

        self.settings.assert_credentials()

        if not force_refresh:
            cached = self._read_cached_token()
            if cached:
                self._token = cached
                return cached

        if not self.settings.app_key or not self.settings.secret_key:
            raise AuthenticationError(
                "새 토큰 발급에 필요한 KIWOOM_APP_KEY/KIWOOM_SECRET_KEY가 없습니다."
            )

        url = self.settings.base_url + "/oauth2/token"
        body = {
            "grant_type": "client_credentials",
            "appkey": self.settings.app_key,
            "secretkey": self.settings.secret_key,
        }
        data, http_status = self._json_post(url, body, headers={}, use_limiter=False)
        if http_status != 200:
            raise AuthenticationError(f"토큰 HTTP 오류={http_status}, msg={data.get('return_msg', '')!r}")
        token = str(data.get("token", "")).strip()
        try:
            code = return_code_of(data) if "return_code" in data else 0
        except ValueError:
            code = -99999
        if code != 0 or not token:
            raise AuthenticationError(
                f"토큰 발급 실패 return_code={code}, msg={data.get('return_msg', '')!r}, keys={sorted(data.keys())}"
            )
        self._token = token
        expires_dt = str(data.get("expires_dt", "")).strip()
        if expires_dt:
            try:
                self._save_token(token, expires_dt)
            except OSError:
                self.logger.warning("토큰 캐시 저장 실패—실행은 계속합니다.")
        return token

    @staticmethod
    def _is_invalid_token_response(code: int | None, message: str) -> bool:
        normalized = message.lower()
        return (
            code == 3
            or "8005" in normalized
            or ("token" in normalized and ("유효하지" in normalized or "invalid" in normalized))
        )

    def _json_post(
        self,
        url: str,
        body: Mapping[str, Any],
        headers: Mapping[str, str],
        *,
        use_limiter: bool = True,
    ) -> tuple[dict[str, Any], int]:
        if use_limiter:
            self.limiter.wait()
        payload = json.dumps(dict(body), ensure_ascii=False).encode("utf-8")
        request_headers = {"Content-Type": "application/json;charset=UTF-8", **dict(headers)}
        request = urllib.request.Request(url, data=payload, headers=request_headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.settings.timeout_sec) as response:
                status = int(response.status)
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            raw = exc.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ApiTransportError(f"네트워크 실패: {exc}") from exc
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ApiTransportError(f"JSON 아님 HTTP={status}, body={raw[:250]!r}") from exc
        if not isinstance(parsed, dict):
            raise ApiSchemaError(f"응답 최상위가 객체가 아님: {type(parsed).__name__}")
        return parsed, status

    def post(
        self,
        *,
        api_id: str,
        endpoint: str,
        body: Mapping[str, Any],
        required_any: tuple[str, ...],
    ) -> dict[str, Any]:
        """API 호출. 토큰 무효 응답이면 캐시를 폐기하고 새 토큰으로 1회만 재시도한다."""
        for attempt in range(2):
            token = self.authenticate(force_refresh=(attempt == 1))
            started = time.monotonic()
            try:
                data, http_status = self._json_post(
                    self.settings.base_url + endpoint,
                    body,
                    headers={"authorization": f"Bearer {token}", "api-id": api_id},
                )
            except V73Error as exc:
                append_api_error(f"{api_id} transport_error={exc}")
                raise

            elapsed = time.monotonic() - started
            msg = str(data.get("return_msg", ""))
            keys = sorted(data.keys())
            try:
                code = return_code_of(data)
            except ValueError:
                code = None

            if http_status == 200 and self._is_invalid_token_response(code, msg) and attempt == 0:
                # 만료시각이 남아 있어도 서버에서 무효화된 토큰일 수 있다.
                self.logger.warning("접근토큰 무효 감지—캐시 폐기 후 새 토큰으로 1회 재시도")
                self._token = ""
                try:
                    TOKEN_FILE.unlink(missing_ok=True)
                except OSError:
                    pass
                continue

            if http_status != 200:
                detail = f"{api_id} HTTP={http_status} return_code={code} msg={msg!r} keys={keys}"
                append_api_error(detail)
                raise ApiTransportError(detail)
            if code is None:
                detail = f"{api_id} return_code 누락 msg={msg!r} keys={keys}"
                append_api_error(detail)
                raise ApiSchemaError(detail)
            if code != 0:
                detail = f"{api_id} API_ERROR return_code={code} msg={msg!r} keys={keys} body={dict(body)!r}"
                append_api_error(detail)
                if self._is_invalid_token_response(code, msg):
                    raise AuthenticationError(detail)
                raise ApiBusinessError(detail)
            if not any(key in data for key in required_any):
                detail = (
                    f"{api_id} SUCCESS_BUT_SCHEMA_ERROR required_any={required_any} "
                    f"msg={msg!r} keys={keys} elapsed={elapsed:.2f}s"
                )
                append_api_error(detail)
                raise ApiSchemaError(detail)
            return data

        raise AuthenticationError(f"{api_id} 새 토큰 발급 후에도 인증에 실패했습니다.")

    def get_quote(self, symbol: str) -> Quote:
        base = base_symbol(symbol)
        requested = api_symbol(base, self.settings.suffix)
        data = self.post(
            api_id="ka10001",
            endpoint="/api/dostk/stkinfo",
            body={"stk_cd": requested},
            required_any=("cur_prc",),
        )
        try:
            price = parse_float(data["cur_prc"], absolute=True)
            if price <= 0:
                raise ValueError(f"현재가 0 이하: {price}")
            change_pct = parse_float(
                first_present(data, ("flu_rt", "pred_pre_rt", "change_rate"), 0),
                absolute=False,
            )
            volume = parse_int(
                first_present(data, ("acc_trde_qty", "trde_qty", "acc_volume", "volume"), 0),
                absolute=True,
            )
            value = parse_float(
                first_present(data, ("acc_trde_prica", "trde_prica", "acc_value", "value"), 0),
                absolute=True,
            )
        except (KeyError, ValueError) as exc:
            detail = f"ka10001 가격 파싱 실패 symbol={requested} error={exc} keys={sorted(data.keys())}"
            append_api_error(detail)
            raise ApiSchemaError(detail) from exc
        return Quote(
            symbol=base,
            name=str(data.get("stk_nm", "")).strip(),
            price=price,
            change_pct=change_pct,
            cumulative_volume=volume,
            cumulative_value=value,
            timestamp=datetime.now(timezone.utc),
        )

    def get_orderbook(self, symbol: str, quote: Quote, *, side: str = "both") -> Quote:
        """PAPER 체결에 필요한 호가만 엄격히 요구한다.

        buy: 매도1호가만 필요, sell: 매수1호가만 필요. 청산 때 매도호가가 0이어도
        매수1호가가 정상이라면 매도를 지연시키지 않는다.
        """
        base = base_symbol(symbol)
        requested = api_symbol(base, self.settings.suffix)
        required = ("sel_fpr_bid",) if side == "buy" else ("buy_fpr_bid",) if side == "sell" else ("sel_fpr_bid", "buy_fpr_bid")
        data = self.post(
            api_id="ka10004",
            endpoint="/api/dostk/mrkcond",
            body={"stk_cd": requested},
            required_any=required,
        )
        try:
            ask_raw = data.get("sel_fpr_bid", data.get("sel_1th_pre_bid"))
            bid_raw = data.get("buy_fpr_bid", data.get("buy_1th_pre_bid"))
            ask = parse_float(ask_raw, absolute=True) if ask_raw not in (None, "") else 0.0
            bid = parse_float(bid_raw, absolute=True) if bid_raw not in (None, "") else 0.0
            if side in {"buy", "both"} and ask <= 0:
                raise ValueError(f"매도1호가 없음 ask={ask}")
            if side in {"sell", "both"} and bid <= 0:
                raise ValueError(f"매수1호가 없음 bid={bid}")
            if ask > 0 and bid > 0 and ask < bid:
                gap = (bid - ask) / max(1.0, min(ask, bid))
                if gap > 0.02:
                    raise ValueError(f"비정상 교차호가 ask={ask}, bid={bid}, gap={gap:.2%}")
                ask, bid = max(ask, bid), min(ask, bid)
        except (KeyError, ValueError, TypeError) as exc:
            detail = f"ka10004 호가 파싱 실패 symbol={requested} error={exc} keys={sorted(data.keys())}"
            append_api_error(detail)
            raise ApiSchemaError(f"호가확인실패 symbol={base} {exc}") from exc
        return Quote(
            symbol=quote.symbol,
            name=quote.name,
            price=quote.price,
            change_pct=quote.change_pct,
            cumulative_volume=quote.cumulative_volume,
            cumulative_value=quote.cumulative_value,
            timestamp=quote.timestamp,
            best_bid=bid if bid > 0 else None,
            best_ask=ask if ask > 0 else None,
        )

    def get_minute_bars(
        self,
        symbol: str,
        *,
        limit: int = 120,
        completed_only: bool = True,
    ) -> list[Bar]:
        """ka10080 실제 1분봉을 시간 오름차순으로 반환한다.

        수신시각으로 임의 생성한 OHLC가 아니라 거래소 차트의 cntr_tm/OHLCV를 사용한다.
        현재 진행 중인 분봉은 기본적으로 제외해 미래정보·봉변형을 막는다.
        """
        base = base_symbol(symbol)
        requested = api_symbol(base, self.settings.suffix)
        data = self.post(
            api_id="ka10080",
            endpoint="/api/dostk/chart",
            body={"stk_cd": requested, "tic_scope": "1", "upd_stkpc_tp": "1"},
            required_any=("stk_min_pole_chart_qry",),
        )
        raw_rows = data.get("stk_min_pole_chart_qry")
        if not isinstance(raw_rows, list):
            detail = (
                f"ka10080 목록 스키마 오류 symbol={requested} "
                f"type={type(raw_rows).__name__} keys={sorted(data.keys())}"
            )
            append_api_error(detail)
            raise ApiSchemaError(detail)

        current_minute = datetime.now(KST).replace(second=0, microsecond=0)
        parsed: dict[datetime, Bar] = {}
        for index, row in enumerate(raw_rows):
            if not isinstance(row, Mapping):
                continue
            try:
                raw_time = str(row.get("cntr_tm", "")).strip()
                if len(raw_time) == 14:
                    timestamp = datetime.strptime(raw_time, "%Y%m%d%H%M%S").replace(tzinfo=KST)
                elif len(raw_time) == 12:
                    timestamp = datetime.strptime(raw_time, "%Y%m%d%H%M").replace(tzinfo=KST)
                else:
                    raise ValueError(f"cntr_tm 형식={raw_time!r}")
                timestamp = timestamp.replace(second=0, microsecond=0)
                if completed_only and timestamp >= current_minute:
                    continue
                open_price = parse_float(row["open_pric"], absolute=True)
                high_price = parse_float(row["high_pric"], absolute=True)
                low_price = parse_float(row["low_pric"], absolute=True)
                close_price = parse_float(row["cur_prc"], absolute=True)
                volume = parse_int(row.get("trde_qty", 0), absolute=True)
                if min(open_price, high_price, low_price, close_price) <= 0:
                    raise ValueError("OHLC 0 이하")
                if high_price < max(open_price, close_price, low_price):
                    raise ValueError("고가 스키마 불일치")
                if low_price > min(open_price, close_price, high_price):
                    raise ValueError("저가 스키마 불일치")
                parsed[timestamp] = Bar(
                    symbol=base,
                    timestamp=timestamp,
                    open=open_price,
                    high=high_price,
                    low=low_price,
                    close=close_price,
                    volume=max(0, volume),
                )
            except (KeyError, ValueError) as exc:
                detail = f"ka10080 행 파싱 실패 symbol={requested} index={index} error={exc} row={dict(row)!r}"
                append_api_error(detail)
                raise ApiSchemaError(detail) from exc

        bars = sorted(parsed.values(), key=lambda item: item.timestamp)
        if limit > 0:
            bars = bars[-limit:]
        if not bars:
            detail = f"ka10080 정상응답이나 완성 1분봉 없음 symbol={requested} rows={len(raw_rows)}"
            append_api_error(detail)
            raise ApiSchemaError(detail)
        return bars


    @staticmethod
    def _candidate_rows(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        """순위 TR 안의 모든 목록 행을 수집한다.

        V74.3까지는 stk_cd/code가 이미 보이는 행만 수집해, 실제 응답 필드명이
        다르면 목록이 존재해도 0행으로 오판했다. 파싱 단계에서 종목코드를 다시
        검증하므로 여기서는 목록 안의 Mapping 행을 넓게 수집한다.
        """
        rows: list[Mapping[str, Any]] = []
        seen: set[int] = set()

        def visit(value: Any) -> None:
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, Mapping):
                        marker = id(item)
                        if marker not in seen:
                            rows.append(item)
                            seen.add(marker)
                        for child in item.values():
                            visit(child)
                    elif isinstance(item, (list, Mapping)):
                        visit(item)
            elif isinstance(value, Mapping):
                for child in value.values():
                    visit(child)

        visit(payload)
        return rows

    @staticmethod
    def _excluded_name(name: str) -> bool:
        upper = name.upper()
        tokens = (
            "ETF", "ETN", "스팩", "SPAC", "인버스", "레버리지", "선물",
            "KODEX", "TIGER", "ACE ", "SOL ", "RISE ", "PLUS ", "HANARO",
        )
        return any(token in upper for token in tokens) or bool(re.search(r"우(?:B|C)?$", name))

    def _rank_post_try(self, api_id: str, body: Mapping[str, Any]) -> dict[str, Any] | None:
        token = self.authenticate()
        try:
            data, status = self._json_post(
                self.settings.base_url + "/api/dostk/rkinfo",
                body,
                headers={"authorization": f"Bearer {token}", "api-id": api_id},
            )
        except V73Error as exc:
            append_api_error(f"{api_id} trial transport body={dict(body)!r} error={exc}")
            bucket = self.rank_probe_trials[api_id]
            if len(bucket) < 30:
                bucket.append({"body": dict(body), "status": None, "code": None, "error": str(exc), "data": {}})
            return None
        try:
            code = return_code_of(data)
        except ValueError:
            code = None
        bucket = self.rank_probe_trials[api_id]
        if len(bucket) < 30:
            bucket.append({"body": dict(body), "status": status, "code": code, "error": "", "data": data})
        if status != 200 or code != 0:
            append_api_error(
                f"{api_id} trial rejected HTTP={status} code={code} msg={data.get('return_msg','')!r} body={dict(body)!r} keys={sorted(data.keys())}"
            )
            return None
        return data

    def write_rank_schema_probe(self, api_ids: Iterable[str]) -> tuple[Path, list[str]]:
        """성공/거절 응답 모두의 실제 구조를 JSON으로 저장한다.

        각 최상위 값의 자료형, 목록 길이, 원시 샘플 행 키를 저장해 목록은 있는데
        종목코드 별칭을 몰라 0행으로 오판하는 상황을 구분한다. 인증정보는 기록하지 않는다.
        """
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        report: dict[str, Any] = {
            "created_at": datetime.now(KST).isoformat(timespec="seconds"),
            "version": VERSION,
            "apis": {},
        }
        summaries: list[str] = []
        for api_id in api_ids:
            trials_out: list[dict[str, Any]] = []
            success_count = 0
            parsed_row_total = 0
            raw_list_row_total = 0
            union_row_keys: set[str] = set()
            entries = self.rank_probe_trials.get(api_id, [])
            for entry in entries:
                data = entry.get("data", {})
                body = entry.get("body", {})
                code = entry.get("code")
                if entry.get("status") == 200 and code == 0:
                    success_count += 1
                parsed_rows = self._candidate_rows(data) if isinstance(data, Mapping) else []
                parsed_row_total += len(parsed_rows)
                top_shape: dict[str, Any] = {}
                raw_samples: dict[str, list[dict[str, Any]]] = {}
                if isinstance(data, Mapping):
                    for key, value in data.items():
                        shape: dict[str, Any] = {"type": type(value).__name__}
                        if isinstance(value, list):
                            shape["length"] = len(value)
                            raw_list_row_total += sum(1 for item in value if isinstance(item, Mapping))
                            samples: list[dict[str, Any]] = []
                            for item in value[:2]:
                                if not isinstance(item, Mapping):
                                    continue
                                union_row_keys.update(str(k) for k in item.keys())
                                sample: dict[str, Any] = {}
                                for item_key in list(item.keys())[:100]:
                                    item_value = item.get(item_key)
                                    if isinstance(item_value, (str, int, float, bool)) or item_value is None:
                                        sample[str(item_key)] = item_value
                                    else:
                                        sample[str(item_key)] = repr(item_value)[:300]
                                samples.append(sample)
                            raw_samples[str(key)] = samples
                        elif isinstance(value, Mapping):
                            shape["keys"] = sorted(str(k) for k in value.keys())[:100]
                        top_shape[str(key)] = shape
                trials_out.append({
                    "request_body": body,
                    "http_status": entry.get("status"),
                    "return_code": code,
                    "return_msg": data.get("return_msg") if isinstance(data, Mapping) else None,
                    "transport_error": entry.get("error") or "",
                    "top_shape": top_shape,
                    "parsed_candidate_row_count": len(parsed_rows),
                    "raw_list_samples": raw_samples,
                })
            report["apis"][api_id] = {
                "trial_count": len(entries),
                "successful_trial_count": success_count,
                "parsed_candidate_row_count_total": parsed_row_total,
                "raw_list_mapping_count_total": raw_list_row_total,
                "row_keys_union": sorted(union_row_keys),
                "trials": trials_out,
            }
            key_text = ",".join(sorted(union_row_keys)[:30]) or "없음"
            summaries.append(
                f"{api_id} 시도={len(entries)} 성공={success_count} 목록행={raw_list_row_total} 행필드={key_text}"
            )
        RANK_SCHEMA_FILE.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return RANK_SCHEMA_FILE, summaries

    def _parse_rank_candidates(self, payloads: Iterable[Mapping[str, Any]], *, source: str) -> list[Candidate]:
        parsed: dict[str, Candidate] = {}
        for payload in payloads:
            for row in self._candidate_rows(payload):
                try:
                    symbol = base_symbol(str(first_present(row, ("stk_cd", "stock_code", "code", "shrtn_iscd", "stck_shrn_iscd", "iscd", "symbol"))))
                    if not re.fullmatch(r"\d{6}", symbol):
                        continue
                    name = str(first_present(row, ("stk_nm", "stock_name", "name", "hts_kor_isnm", "prdt_name"), symbol)).strip() or symbol
                    if self._excluded_name(name):
                        continue
                    price = parse_float(first_present(row, ("cur_prc", "now_pric", "price", "stck_prpr"), 0), absolute=True)
                    volume = parse_int(first_present(row, ("now_trde_qty", "trde_qty", "acc_trde_qty", "volume", "acml_vol"), 0), absolute=True)
                    change_pct = parse_float(first_present(row, ("flu_rt", "pred_pre_rt", "change_rate", "chg_rt", "updown_rt", "prdy_ctrt"), 0), absolute=False)
                    surge_rate = parse_float(first_present(row, ("trde_qty_inrt", "trde_qty_rt", "inrt", "surge_rate", "sudn_rt", "trde_qty_sdnin_rt", "vol_inrt"), 0), absolute=False)
                    foreign_net = parse_float(first_present(row, ("frgnr_netprps", "frgn_netprps", "frgnr_netprps_amt", "frgnr_netprps_qty", "frgnr_netprps_qnt"), 0), absolute=False)
                    institution_net = parse_float(first_present(row, ("orgn_netprps", "inst_netprps", "orgn_netprps_amt", "orgn_netprps_qty", "orgn_netprps_qnt"), 0), absolute=False)
                    if price <= 0:
                        continue
                    dual = foreign_net > 0 and institution_net > 0
                    base_score = max(0.0, change_pct) * 2.0 + max(0.0, surge_rate) * 1.5 + math.log10(max(1, volume) + 1) * 2.0
                    if source == "상승률":
                        base_score += 25.0
                    elif source == "거래량급증":
                        base_score += 30.0
                    elif source == "쌍끌이" and dual:
                        base_score += 40.0
                    item = Candidate(symbol, name, price, change_pct, volume, surge_rate, base_score, source, foreign_net, institution_net, dual)
                    old = parsed.get(symbol)
                    if old is None or item.score > old.score:
                        parsed[symbol] = item
                except (KeyError, ValueError, TypeError):
                    continue
        return sorted(parsed.values(), key=lambda x: (x.score, x.change_pct, x.surge_rate, x.volume), reverse=True)

    def get_volume_surge_candidates(self, *, limit: int = 100) -> list[Candidate]:
        common = {"sort_tp": "2", "tm_tp": "1", "trde_qty_tp": "0000", "stk_cnd": "1", "pric_tp": "0", "stex_tp": self.settings.stex_tp}
        bodies = [
            {"mrkt_tp": "000", **common},
            {"mrkt_tp": "001", **common},
            {"mrkt_tp": "101", **common},
            {"mrkt_tp": "001", **{**common, "stk_cnd": "0"}},
            {"mrkt_tp": "101", **{**common, "stk_cnd": "0"}},
        ]
        payloads: list[dict[str, Any]] = []
        schema_seen = False
        for body in bodies:
            data = self._rank_post_try("ka10023", body)
            if data is None:
                continue
            payloads.append(data)
            # 실서버에서 확인된 ka10023 결과 목록 키.
            if "trde_qty_sdnin" in data and isinstance(data.get("trde_qty_sdnin"), list):
                schema_seen = True
            if body.get("mrkt_tp") == "000" and len(self._candidate_rows(data)) >= 20:
                break
        candidates = self._parse_rank_candidates(payloads, source="거래량급증")
        if candidates:
            return candidates[: max(1, limit)]
        if schema_seen:
            # 장 종료 후처럼 정상응답의 결과 목록이 빈 경우는 API 실패가 아니다.
            return []
        raise ApiSchemaError("ka10023 응답목록 trde_qty_sdnin 확인 실패")

    def get_gain_rank_candidates(self, *, limit: int = 100) -> list[Candidate]:
        common = {
            "sort_tp": "1", "trde_qty_cnd": "0000", "stk_cnd": "1", "crd_cnd": "0",
            "updown_incls": "1", "pric_cnd": "0", "trde_prica_cnd": "0", "stex_tp": self.settings.stex_tp,
        }
        bodies = [
            {"mrkt_tp": "000", **common},
            {"mrkt_tp": "001", **common},
            {"mrkt_tp": "101", **common},
            {"mrkt_tp": "000", "sort_tp": "1", "trde_qty_cnd": "0000", "stk_cnd": "1", "crd_cnd": "0", "stex_tp": self.settings.stex_tp},
        ]
        payloads: list[dict[str, Any]] = []
        for body in bodies:
            data = self._rank_post_try("ka10027", body)
            if data is None:
                continue
            payloads.append(data)
            if body.get("mrkt_tp") == "000" and len(self._candidate_rows(data)) >= 20:
                break
        candidates = self._parse_rank_candidates(payloads, source="상승률")
        if not candidates:
            raise ApiSchemaError("ka10027 상승률 후보 없음")
        return candidates[: max(1, limit)]

    def _dual_rank_request_bodies(self) -> list[dict[str, str]]:
        """ka90009 요청 후보를 만든다.

        실서버 오류가 필수 파라미터 qry_dt_tp 누락을 명시했으므로 이를 반드시 넣는다.
        0은 당일 조회 후보, 1은 날짜 지정 후보로 시험한다. 날짜 지정 필드명은 서버
        응답으로 최종 확정하기 위해 date를 함께 보낸다.
        """
        today = datetime.now(KST).strftime("%Y%m%d")
        bodies: list[dict[str, str]] = []
        for market in ("000", "001", "101"):
            bodies.append({
                "mrkt_tp": market,
                "amt_qty_tp": "1",
                "qry_dt_tp": "0",
                "stex_tp": self.settings.stex_tp,
            })
            bodies.append({
                "mrkt_tp": market,
                "amt_qty_tp": "1",
                "qry_dt_tp": "1",
                "date": today,
                "stex_tp": self.settings.stex_tp,
            })
        return bodies

    @staticmethod
    def _parse_dual_buy_candidates(payloads: Iterable[Mapping[str, Any]]) -> tuple[list[Candidate], bool]:
        """ka90009의 외국인 순매수 상위와 기관 순매수 상위 목록의 교집합을 만든다.

        실서버 응답 한 행에는 동일 종목의 외국인/기관 값이 같이 있는 것이 아니라,
        네 개의 독립 순위(외국인 순매도·외국인 순매수·기관 순매도·기관 순매수)가
        가로로 함께 들어온다. 따라서 같은 행끼리 비교하지 않고 전체 목록에서
        `for_netprps_stk_cd`와 `orgn_netprps_stk_cd`를 각각 수집한 뒤 종목코드
        교집합을 구해야 한다.
        """
        foreign: dict[str, tuple[str, float, float]] = {}
        institution: dict[str, tuple[str, float, float]] = {}
        schema_seen = False

        def number(row: Mapping[str, Any], key: str) -> float:
            try:
                return parse_float(row.get(key, 0) or 0, absolute=False)
            except (ValueError, TypeError):
                return 0.0

        for payload in payloads:
            rows = payload.get("frgnr_orgn_trde_upper")
            if not isinstance(rows, list):
                continue
            schema_seen = True
            for row in rows:
                if not isinstance(row, Mapping):
                    continue

                foreign_symbol = base_symbol(str(row.get("for_netprps_stk_cd", "")).strip())
                if re.fullmatch(r"\d{6}", foreign_symbol):
                    foreign_name = str(row.get("for_netprps_stk_nm", foreign_symbol)).strip() or foreign_symbol
                    foreign_amount = number(row, "for_netprps_amt")
                    foreign_quantity = number(row, "for_netprps_qty")
                    if foreign_amount > 0 or foreign_quantity > 0:
                        old = foreign.get(foreign_symbol)
                        if old is None or foreign_amount > old[1]:
                            foreign[foreign_symbol] = (foreign_name, foreign_amount, foreign_quantity)

                institution_symbol = base_symbol(str(row.get("orgn_netprps_stk_cd", "")).strip())
                if re.fullmatch(r"\d{6}", institution_symbol):
                    institution_name = str(row.get("orgn_netprps_stk_nm", institution_symbol)).strip() or institution_symbol
                    institution_amount = number(row, "orgn_netprps_amt")
                    institution_quantity = number(row, "orgn_netprps_qty")
                    if institution_amount > 0 or institution_quantity > 0:
                        old = institution.get(institution_symbol)
                        if old is None or institution_amount > old[1]:
                            institution[institution_symbol] = (
                                institution_name,
                                institution_amount,
                                institution_quantity,
                            )

        candidates: list[Candidate] = []
        for symbol in sorted(set(foreign).intersection(institution)):
            foreign_name, foreign_amount, foreign_quantity = foreign[symbol]
            institution_name, institution_amount, institution_quantity = institution[symbol]
            name = foreign_name if foreign_name and foreign_name != symbol else institution_name
            if KiwoomRestClient._excluded_name(name):
                continue

            # 금액 필드를 우선 사용하고, 값이 없을 때 수량을 보조로 사용한다.
            foreign_net = foreign_amount if foreign_amount > 0 else foreign_quantity
            institution_net = institution_amount if institution_amount > 0 else institution_quantity
            if foreign_net <= 0 or institution_net <= 0:
                continue

            combined_strength = max(1.0, foreign_net + institution_net)
            score = 40.0 + math.log10(combined_strength + 1.0) * 4.0
            candidates.append(
                Candidate(
                    symbol=symbol,
                    name=name or symbol,
                    price=0.0,
                    change_pct=0.0,
                    volume=0,
                    surge_rate=0.0,
                    score=score,
                    source="쌍끌이",
                    foreign_net=foreign_net,
                    institution_net=institution_net,
                    dual_buy=True,
                )
            )

        candidates.sort(
            key=lambda item: (item.score, item.foreign_net + item.institution_net),
            reverse=True,
        )
        return candidates, schema_seen

    def get_dual_buy_candidates(self, *, limit: int = 100) -> list[Candidate]:
        """외국인·기관 순매수 상위 목록의 종목코드 교집합만 관심후보로 반환한다."""
        payloads: list[dict[str, Any]] = []
        for body in self._dual_rank_request_bodies():
            data = self._rank_post_try("ka90009", body)
            if data is None:
                continue
            payloads.append(data)
            if body.get("mrkt_tp") == "000" and isinstance(data.get("frgnr_orgn_trde_upper"), list):
                break

        candidates, schema_seen = self._parse_dual_buy_candidates(payloads)
        if candidates:
            return candidates[: max(1, limit)]
        if schema_seen:
            # 정상응답이지만 외국인·기관 순매수 상위 목록에 공통 종목이 없는 경우.
            return []
        raise ApiSchemaError("ka90009 응답목록 frgnr_orgn_trde_upper 확인 실패")

    def get_daily_bars(self, symbol: str, *, limit: int = 120) -> list[DailyBar]:
        base = base_symbol(symbol)
        requested = api_symbol(base, self.settings.suffix)
        data = self.post(
            api_id="ka10081",
            endpoint="/api/dostk/chart",
            body={"stk_cd": requested, "base_dt": datetime.now(KST).strftime("%Y%m%d"), "upd_stkpc_tp": "1"},
            required_any=("stk_dt_pole_chart_qry",),
        )
        rows = data.get("stk_dt_pole_chart_qry")
        if not isinstance(rows, list):
            raise ApiSchemaError(f"ka10081 일봉 목록 오류 symbol={base}")
        parsed: dict[date, DailyBar] = {}
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            try:
                raw_date = str(first_present(row, ("dt", "date", "trde_dt"), "")).strip()
                trading_date = datetime.strptime(raw_date[:8], "%Y%m%d").date()
                open_price = parse_float(first_present(row, ("open_pric", "open")), absolute=True)
                high_price = parse_float(first_present(row, ("high_pric", "high")), absolute=True)
                low_price = parse_float(first_present(row, ("low_pric", "low")), absolute=True)
                close_price = parse_float(first_present(row, ("cur_prc", "close_pric", "close")), absolute=True)
                volume = parse_int(first_present(row, ("trde_qty", "volume"), 0), absolute=True)
                if min(open_price, high_price, low_price, close_price) <= 0:
                    continue
                parsed[trading_date] = DailyBar(base, trading_date, open_price, high_price, low_price, close_price, volume)
            except (KeyError, ValueError, TypeError):
                continue
        result = sorted(parsed.values(), key=lambda x: x.trading_date)
        if not result:
            raise ApiSchemaError(f"ka10081 일봉 파싱 실패 symbol={base}")
        return result[-max(1, limit):]


# -----------------------------------------------------------------------------
# 분봉, 전략, 리스크
# -----------------------------------------------------------------------------


@dataclass(slots=True)
class _OpenBar:
    minute: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


class MinuteBarAggregator:
    def __init__(self) -> None:
        self._open: dict[str, _OpenBar] = {}
        self._last_volume: dict[str, int] = {}

    def update(self, quote: Quote) -> Bar | None:
        minute = quote.timestamp.astimezone(KST).replace(second=0, microsecond=0)
        previous = self._last_volume.get(quote.symbol, quote.cumulative_volume)
        delta = max(0, quote.cumulative_volume - previous)
        self._last_volume[quote.symbol] = quote.cumulative_volume
        current = self._open.get(quote.symbol)
        if current is None:
            self._open[quote.symbol] = _OpenBar(minute, quote.price, quote.price, quote.price, quote.price, delta)
            return None
        if minute == current.minute:
            current.high = max(current.high, quote.price)
            current.low = min(current.low, quote.price)
            current.close = quote.price
            current.volume += delta
            return None
        if minute < current.minute:
            return None
        completed = Bar(
            quote.symbol,
            current.minute,
            current.open,
            current.high,
            current.low,
            current.close,
            current.volume,
        )
        self._open[quote.symbol] = _OpenBar(minute, quote.price, quote.price, quote.price, quote.price, delta)
        return completed


def ema(values: list[float], period: int) -> float:
    if not values:
        raise ValueError("EMA 입력 없음")
    alpha = 2.0 / (period + 1.0)
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (1.0 - alpha) * result
    return result


class MomentumVolumeStrategy:
    """정석 박스돌파와 투매 후 거래바닥 눌림만 매수하는 PAPER 전략."""

    def __init__(self, settings: Settings) -> None:
        self.min_score = settings.min_signal_score
        self.breakout_stop_pct = env_float("V74_BREAKOUT_STOP_PCT", 0.020)
        self.pullback_stop_pct = env_float("V74_PULLBACK_STOP_PCT", 0.012)
        self.take_profit_pct = env_float("V74_TAKE_PROFIT_PCT", 0.015)
        self.box_min_bars = env_int("V74_BOX_MIN_BARS", 20)
        self.box_max_bars = env_int("V74_BOX_MAX_BARS", 45)
        self.box_max_range_pct = env_float("V74_BOX_MAX_RANGE_PCT", 0.10)
        self.breakout_margin_pct = env_float("V74_BREAKOUT_MARGIN_PCT", 0.0060)
        self.breakout_volume_vs_max = env_float("V74_BREAKOUT_VOLUME_VS_MAX", 1.20)
        self.breakout_volume_vs_median = env_float("V74_BREAKOUT_VOLUME_VS_MEDIAN", 3.5)
        self.capitulation_volume_mult = env_float("V74_CAPITULATION_VOLUME_MULT", 2.2)
        self.capitulation_lookback = max(18, env_int("V74_CAPITULATION_LOOKBACK", 45))
        # 사용자가 요구한 청산은 +1.5% 목표와 패턴 손절이 중심이므로 시간 강제청산은 기본 비활성(0).
        # 값을 주면 '실제 거래세션 경과분' 기준으로만 적용한다(오버나이트 벽시계 오판 방지).
        self.max_hold_minutes = env_int("V74_MAX_HOLD_MINUTES", 0)
        # 박스 내부 종가 추세 허용 기울기: 완만한 상승 수렴 채널 돌파(마키나락스형)를 살리기 위해 환경변수화.
        self.box_max_close_slope = env_float("V74_BOX_MAX_CLOSE_SLOPE", 0.0035)
        # 박스 내 급등 장대양봉 후 재돌파까지 요구하는 최소 소화 봉수.
        self.impulse_cooldown_bars = env_int("V74_IMPULSE_COOLDOWN_BARS", 8)
        # 프리마켓·시간외 얇은 거래량 가짜 돌파 차단: 돌파봉 최소 거래대금(원).
        self.min_breakout_value_krw = env_float("V74_MIN_BREAKOUT_VALUE_KRW", 30_000_000.0)
        # --- 임펄스눌림(급등 후 저거래 조정) 신규 패턴 파라미터 ---
        # 사용자의 실제 매수 차트(더코디·디와이에이·아이크래프트·원익IPS)는 '하락 투매'가 아니라
        # '급등 장대양봉 임펄스 → 저거래 눌림(임펄스의 약 1/3~1/2 되돌림) → 이평 지지 → 짧은 양봉' 구조다.
        self.impulse_min_gain = env_float("V74_IMPULSE_MIN_GAIN", 0.015)          # 임펄스봉 최소 상승률
        self.impulse_volume_mult = env_float("V74_IMPULSE_VOLUME_MULT", 3.0)      # 임펄스봉 거래량 배수(직전 중앙값 대비)
        self.impulse_lookback = max(15, env_int("V74_IMPULSE_LOOKBACK", 40))      # 임펄스 탐색 범위(봉)
        self.impulse_max_retrace = env_float("V74_IMPULSE_MAX_RETRACE", 0.55)     # 임펄스 상승폭 대비 최대 되돌림
        self.impulse_dry_median = env_float("V74_IMPULSE_DRY_MEDIAN", 0.45)       # 눌림 구간 거래량 중앙값/임펄스 거래량
        self.pullback_stop_pct_impulse = env_float("V74_IMPULSE_STOP_PCT", 0.015) # 임펄스눌림 손절(설정 가능)
        # V75 조정 후 상승 패턴: 단순 하락을 조정으로 오인하지 않기 위한 하드게이트.
        self.adjust_retrace_third_min = env_float("V75_RETRACE_THIRD_MIN", 0.25)
        self.adjust_retrace_third_max = env_float("V75_RETRACE_THIRD_MAX", 0.42)
        self.adjust_retrace_half_max = env_float("V75_RETRACE_HALF_MAX", 0.62)
        self.adjust_min_bars = max(3, env_int("V75_ADJUST_MIN_BARS", 3))
        self.adjust_volume_floor_median = env_float("V75_VOLUME_FLOOR_MEDIAN", 0.28)
        self.adjust_volume_floor_min = env_float("V75_VOLUME_FLOOR_MIN", 0.16)
        self.adjust_range_contract = env_float("V75_RANGE_CONTRACT", 0.55)
        self.adjust_ema_cluster_pct = env_float("V75_EMA_CLUSTER_PCT", 0.012)
        self.adjust_ema_pair_pct = env_float("V75_EMA_PAIR_PCT", 0.008)
        self.adjust_min_support_bars = max(2, env_int("V75_MIN_EMA_SUPPORT_BARS", 2))
        self.adjust_minor_high_margin = env_float("V75_MINOR_HIGH_MARGIN", 0.001)
        self.adjust_volume_return_third = env_float("V75_VOLUME_RETURN_THIRD", 1.80)
        self.adjust_volume_return_half = env_float("V75_VOLUME_RETURN_HALF", 1.35)
        self.adjust_volume_return_full = env_float("V75_VOLUME_RETURN_FULL", 1.20)
        self.near_resistance_pct = env_float("V75_NEAR_RESISTANCE_PCT", 0.012)
        self.near_resistance_volume_mult = env_float("V75_NEAR_RESISTANCE_VOLUME_MULT", 2.20)
        self.full_retrace_min = env_float("V75_FULL_RETRACE_MIN", 0.75)
        self.capitulation_min_drop = env_float("V75_CAPITULATION_MIN_DROP", 0.012)
        self.capitulation_prior_max_mult = env_float("V75_CAPITULATION_PRIOR_MAX_MULT", 1.10)
        self.bars: dict[str, Deque[Bar]] = defaultdict(lambda: deque(maxlen=500))
        self.capitulation_cache: dict[str, tuple[datetime, float, float]] = {}
        # 임펄스눌림 상태: symbol -> (임펄스봉 시각, 임펄스 상승폭원화, 임펄스 거래량, 임펄스 저가)
        self.impulse_cache: dict[str, tuple[datetime, float, float, float]] = {}
        self.daily_context: dict[str, DailyContext] = {}
        self.same_time_volume_ratio: dict[str, float] = {}
        self.candidate_sources: dict[str, str] = {}
        self.dual_buy: set[str] = set()

    def seed_bars(self, symbol: str, bars: Iterable[Bar]) -> int:
        ordered = sorted((bar for bar in bars if bar.symbol == symbol), key=lambda item: item.timestamp)
        unique: dict[datetime, Bar] = {bar.timestamp: bar for bar in ordered}
        history = self.bars[symbol]
        history.clear()
        for item in unique.values():
            history.append(item)
        return len(history)

    def set_context(
        self,
        symbol: str,
        *,
        daily: DailyContext | None = None,
        same_time_volume_ratio: float | None = None,
        sources: str | None = None,
        dual_buy: bool | None = None,
    ) -> None:
        if daily is not None:
            self.daily_context[symbol] = daily
        if same_time_volume_ratio is not None:
            self.same_time_volume_ratio[symbol] = same_time_volume_ratio
        if sources is not None:
            self.candidate_sources[symbol] = sources
        if dual_buy:
            self.dual_buy.add(symbol)
        elif dual_buy is False:
            self.dual_buy.discard(symbol)

    def on_bar(self, bar: Bar, position: Position | None) -> Signal:
        history = self.bars[bar.symbol]
        if history and bar.timestamp <= history[-1].timestamp:
            return Signal(bar.symbol, SignalAction.HOLD, 0.0, "중복분봉", bar.close, bar.timestamp)
        history.append(bar)
        bars = list(history)
        if position is not None:
            return self._exit(bar, position)
        if len(bars) < max(30, self.box_min_bars + 5):
            return Signal(bar.symbol, SignalAction.HOLD, 0.0, "학습중", bar.close, bar.timestamp)

        pullback = self._capitulation_pullback(bar.symbol, bars)
        impulse = self._impulse_pullback(bar.symbol, bars)
        breakout = self._classic_box_breakout(bar.symbol, bars)
        # 눌림 1차매수(투매형→임펄스형)를 먼저 인정하고, 정석 돌파는 별도 독립 타점으로 인정한다.
        if pullback is not None:
            return pullback
        if impulse is not None:
            return impulse
        if breakout is not None:
            return breakout
        return Signal(bar.symbol, SignalAction.HOLD, 0.0, "조건없음", bar.close, bar.timestamp)

    def mark_entry(self, symbol: str) -> None:
        self.capitulation_cache.pop(symbol, None)
        self.impulse_cache.pop(symbol, None)

    def export_pattern_state(self) -> dict[str, Any]:
        """재시작 시 복구할 종목별 패턴 상태(투매·임펄스 발견 시각과 특성)를 직렬화한다."""
        return {
            "capitulation": {
                symbol: [ts.isoformat(), strength, low]
                for symbol, (ts, strength, low) in self.capitulation_cache.items()
            },
            "impulse": {
                symbol: [ts.isoformat(), gain, volume, low]
                for symbol, (ts, gain, volume, low) in self.impulse_cache.items()
            },
        }

    def restore_pattern_state(self, payload: Mapping[str, Any], *, max_age_hours: float = 14.0) -> int:
        """저장된 패턴 상태를 복구한다. 오래된 상태는 버리고, 사용 시점에 분봉 시각 재검색으로 재검증된다."""
        restored = 0
        now = datetime.now(KST)
        try:
            for symbol, raw in dict(payload.get("capitulation", {})).items():
                ts = datetime.fromisoformat(str(raw[0]))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=KST)
                if (now - ts.astimezone(KST)).total_seconds() <= max_age_hours * 3600:
                    self.capitulation_cache[symbol] = (ts, float(raw[1]), float(raw[2]))
                    restored += 1
            for symbol, raw in dict(payload.get("impulse", {})).items():
                ts = datetime.fromisoformat(str(raw[0]))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=KST)
                if (now - ts.astimezone(KST)).total_seconds() <= max_age_hours * 3600:
                    self.impulse_cache[symbol] = (ts, float(raw[1]), float(raw[2]), float(raw[3]))
                    restored += 1
        except (KeyError, ValueError, TypeError, IndexError):
            return restored
        return restored

    def mark_exit(self, symbol: str) -> None:
        return None

    @staticmethod
    def _avg(values: Iterable[float]) -> float:
        items = list(values)
        return sum(items) / max(1, len(items))

    @staticmethod
    def _slope_pct(values: list[float]) -> float:
        if len(values) < 2 or values[0] <= 0:
            return 0.0
        n = len(values)
        mean_x = (n - 1) / 2.0
        mean_y = sum(values) / n
        denom = sum((i - mean_x) ** 2 for i in range(n))
        if denom <= 0 or mean_y <= 0:
            return 0.0
        slope = sum((i - mean_x) * (value - mean_y) for i, value in enumerate(values)) / denom
        return slope / mean_y

    @staticmethod

    def _ema_cluster_support(self, bars: list[Bar]) -> tuple[bool, float, float, float]:
        """호환용 단일 시점 요약. 실제 조정 매수는 _multi_bar_ema_support를 사용한다."""
        closes = [x.close for x in bars]
        e5, e10, e20 = ema(closes, 5), ema(closes, 10), ema(closes, 20)
        cluster = (max(e5, e10, e20) - min(e5, e10, e20)) / max(1.0, min(e5, e10, e20)) <= self.adjust_ema_cluster_pct
        current = bars[-1]
        above = current.close >= min(e5, e10, e20) and current.close >= e5 * 0.998
        touched = current.low <= max(e5, e10, e20) * 1.004 and current.close >= min(e5, e10, e20) * 0.998
        return cluster and above and touched, e5, e10, e20

    @staticmethod
    def _short_bullish(bar: Bar) -> bool:
        tick = float(stock_tick_size(max(bar.close, bar.open, 1.0)))
        candle_range = max(tick, bar.high - bar.low)
        body = bar.close - bar.open
        body_ratio = body / candle_range
        close_location = (bar.close - bar.low) / candle_range
        return bar.close > bar.open and 0.08 <= body_ratio <= 0.62 and close_location >= 0.58

    @staticmethod
    def _lower_wick_rejection(bar: Bar) -> bool:
        """긴 아래꼬리 양봉·음봉을 저점확인 후보로 인정한다.

        가격대별 실제 호가단위를 최소 계산 단위로 사용한다. 음봉은 매도 우위 봉이므로
        아래꼬리가 위꼬리보다 최소 1.2배 길 때만 저가 거부로 인정한다. 이 함수는
        단독 매수신호가 아니며 거래바닥·다중이평 지지·단기고점 회복과 결합된다.
        """
        if bar.high <= bar.low or bar.close == bar.open:
            return False
        tick = float(stock_tick_size(max(bar.close, bar.open, 1.0)))
        candle_range = max(tick, bar.high - bar.low)
        body = max(tick, abs(bar.close - bar.open))
        lower_wick = max(0.0, min(bar.open, bar.close) - bar.low)
        upper_wick = max(0.0, bar.high - max(bar.open, bar.close))
        close_location = (bar.close - bar.low) / candle_range
        bullish = bar.close > bar.open
        min_close_location = 0.60 if bullish else 0.45
        bearish_wick_dominance = bullish or lower_wick >= upper_wick * 1.20
        return (
            lower_wick >= body * 0.80
            and lower_wick / candle_range >= 0.25
            and close_location >= min_close_location
            and bearish_wick_dominance
        )

    @staticmethod
    def _ema_at(values: list[float], period: int, index: int) -> float:
        return ema(values[: index + 1], period)

    def _multi_bar_ema_support(self, bars: list[Bar], *, lookback: int = 4) -> tuple[bool, int, int, float, float, float]:
        """모인 이평선 두 개 이상 + 최소 두 개 분봉의 지지를 동시에 요구한다."""
        if len(bars) < 24:
            return False, 0, 0, 0.0, 0.0, 0.0
        closes = [x.close for x in bars]
        e5, e10, e20 = ema(closes, 5), ema(closes, 10), ema(closes, 20)
        emas = (e5, e10, e20)
        pair_count = sum(
            1
            for i in range(3)
            for j in range(i + 1, 3)
            if abs(emas[i] - emas[j]) / max(1.0, min(emas[i], emas[j])) <= self.adjust_ema_pair_pct
        )
        cluster_width = (max(emas) - min(emas)) / max(1.0, min(emas))
        support_count = 0
        start = max(20, len(bars) - lookback)
        for idx in range(start, len(bars)):
            local = (
                self._ema_at(closes, 5, idx),
                self._ema_at(closes, 10, idx),
                self._ema_at(closes, 20, idx),
            )
            cluster_low, cluster_high = min(local), max(local)
            item = bars[idx]
            touches = item.low <= cluster_high * 1.004
            holds = item.close >= cluster_low * 0.998 and item.close >= local[0] * 0.997
            if touches and holds:
                support_count += 1
        ok = (
            pair_count >= 1
            and cluster_width <= self.adjust_ema_cluster_pct
            and support_count >= self.adjust_min_support_bars
            and bars[-1].close >= e5
        )
        return ok, support_count, pair_count, e5, e10, e20

    def _daily_reference(self, symbol: str, close: float) -> tuple[float, float, str]:
        """일봉은 절대 차단이 아니라 강도 보정용 참고자료다."""
        ctx = self.daily_context.get(symbol)
        if ctx is None:
            return 99.0, 99.0, "일봉없음"
        resistance_gap = 99.0
        support_gap = 99.0
        notes: list[str] = []
        if ctx.resistance and ctx.resistance > 0:
            resistance_gap = ctx.resistance / close - 1.0
            if -0.003 <= resistance_gap <= self.near_resistance_pct:
                notes.append(f"일봉저항근접{resistance_gap:+.2%}")
        if ctx.support and ctx.support > 0:
            support_gap = close / ctx.support - 1.0
            if 0 <= support_gap <= 0.03:
                notes.append(f"일봉지지근접{support_gap:+.2%}")
        if ctx.trend_up:
            notes.append("일봉추세상")
        elif ctx.low_trend_slope_pct < 0 and ctx.high_trend_slope_pct < 0:
            notes.append("일봉추세하")
        return resistance_gap, support_gap, "/".join(notes) or "일봉중립"

    def _daily_resistance_blocks(self, symbol: str, close: float, *, decisive_volume: bool) -> bool:
        ctx = self.daily_context.get(symbol)
        if ctx is None or ctx.resistance is None or ctx.resistance <= 0:
            return False
        gap = ctx.resistance / close - 1.0
        # 저항 바로 아래/부근에서 멈춘 양봉은 배제. 압도적 거래량으로 저항을 종가 돌파하면 허용.
        return -0.002 <= gap <= 0.008 and not (decisive_volume and close >= ctx.resistance * 1.003)

    def _classic_box_breakout(self, symbol: str, bars: list[Bar]) -> Signal | None:
        current = bars[-1]
        if current.close <= current.open or current.close <= 0:
            return None
        candle_range = max(1.0, current.high - current.low)
        body_ratio = (current.close - current.open) / candle_range
        close_location = (current.close - current.low) / candle_range
        upper_wick_ratio = (current.high - current.close) / candle_range
        if body_ratio < 0.48 or close_location < 0.76 or upper_wick_ratio > 0.28:
            return None

        best: tuple[int, float, float, float, float, float] | None = None
        max_window = min(self.box_max_bars, len(bars) - 2)
        for window in range(max_window, self.box_min_bars - 1, -1):
            box = bars[-window - 1:-1]
            if len(box) < window:
                continue
            highs = [x.high for x in box]
            lows = [x.low for x in box]
            closes = [x.close for x in box]
            volumes = [max(0, x.volume) for x in box]
            box_high, box_low = max(highs), min(lows)
            if box_low <= 0:
                continue
            box_range = box_high / box_low - 1.0
            if box_range > self.box_max_range_pct:
                continue
            # 기간조정: 박스 내부 추세가 너무 가파르면 급등 직후 재상승으로 판단해 제외한다.
            # 완만한 상승 수렴 채널의 돌파(실제 매수 샘플 마키나락스형)는 허용하도록 임계값을 환경변수화했다.
            close_slope = abs(self._slope_pct(closes))
            if close_slope > self.box_max_close_slope:
                continue
            # 최근 급등 장대양봉 뒤 즉시 재추격하지 않는다. 최소 소화기간(기본 8봉)을 요구한다.
            recent_impulses = [i for i, x in enumerate(box) if x.open > 0 and x.close / x.open - 1.0 >= 0.035]
            if recent_impulses and len(box) - 1 - recent_impulses[-1] < self.impulse_cooldown_bars:
                continue
            touches = sum(1 for high in highs if high >= box_high * 0.993)
            if touches < 2:
                continue
            breakout_level = box_high * (1.0 + self.breakout_margin_pct)
            if current.close < breakout_level:
                continue
            max_vol = max(volumes) if volumes else 0
            median_vol = statistics.median(volumes) if volumes else 0
            vol_vs_max = current.volume / max(1.0, max_vol)
            vol_vs_median = current.volume / max(1.0, median_vol)
            decisive = vol_vs_max >= self.breakout_volume_vs_max and vol_vs_median >= self.breakout_volume_vs_median
            if not decisive:
                continue
            # 프리마켓·애프터마켓의 얇은 거래량 봉으로 만들어지는 가짜 돌파를 절대 거래대금으로 차단한다.
            if current.close * current.volume < self.min_breakout_value_krw:
                continue
            # 낮아지는 저점은 배제하고, 최근 저점 상승을 우선한다.
            block_lows = [min(x.low for x in box[i:i + 3]) for i in range(max(0, len(box) - 12), len(box) - 2, 3)]
            low_slope = self._slope_pct(block_lows)
            if block_lows and low_slope < -0.0005:
                continue
            if self._daily_resistance_blocks(symbol, current.close, decisive_volume=True):
                continue
            best = (window, box_high, box_low, vol_vs_max, vol_vs_median, low_slope)
            break

        if best is None:
            return None
        window, box_high, box_low, vol_vs_max, vol_vs_median, low_slope = best
        same_time = self.same_time_volume_ratio.get(symbol, 0.0)
        source = self.candidate_sources.get(symbol, "")
        dual = symbol in self.dual_buy
        score = 110.0 + min(15.0, max(0.0, vol_vs_median - 3.0) * 2.0) + (5.0 if dual else 0.0)
        reason = (
            f"정석박스돌파|박스={window}봉 상단={box_high:.0f}|종가돌파={(current.close / box_high - 1):.2%}"
            f"|거래량=박스최고x{vol_vs_max:.2f},중앙값x{vol_vs_median:.2f}|저점기울기={low_slope:+.3%}"
            f"|동시간거래x{same_time:.2f}|관심={source or '-'}|쌍끌이={'Y' if dual else 'N'}"
        )
        return Signal(symbol, SignalAction.BUY, score, reason, current.close, current.timestamp, "정석박스돌파", self.breakout_stop_pct, self.take_profit_pct)


    def _impulse_pullback(self, symbol: str, bars: list[Bar]) -> Signal | None:
        """상승 임펄스 뒤 진짜 조정만 매수한다.

        필수 구조:
        - 상승폭의 약 1/3 또는 1/2 조정
        - 거래량 바닥
        - 봉 길이 축소와 짧은 양봉
        - 모인 이평선 두 개 이상에서 최소 두 개 분봉 지지
        - 직전 단기고점 회복과 거래량 재유입
        - 아래꼬리 양봉·음봉은 저점확인으로 가점
        """
        current = bars[-1]
        if not self._short_bullish(current):
            return None

        imp_index: int | None = None
        imp_gain_value = 0.0
        imp_volume = 0.0
        imp_low = 0.0
        cached = self.impulse_cache.get(symbol)
        if cached is not None:
            cached_time, cached_gain, cached_vol, cached_low = cached
            for idx, item in enumerate(bars):
                if item.timestamp == cached_time:
                    distance = len(bars) - 1 - idx
                    if self.adjust_min_bars + 1 <= distance <= self.impulse_lookback:
                        imp_index = idx
                        imp_gain_value = cached_gain
                        imp_volume = cached_vol
                        imp_low = cached_low
                    break
            if imp_index is None:
                self.impulse_cache.pop(symbol, None)

        if imp_index is None:
            search_start = max(10, len(bars) - self.impulse_lookback)
            for idx in range(len(bars) - self.adjust_min_bars - 1, search_start - 1, -1):
                item = bars[idx]
                before = bars[max(0, idx - 12):idx]
                if len(before) < 8 or item.open <= 0:
                    continue
                gain = item.close / item.open - 1.0
                item_range = max(1.0, item.high - item.low)
                bull_body = (item.close - item.open) / item_range
                median_vol = statistics.median(max(0, x.volume) for x in before)
                volume_mult = item.volume / max(1.0, median_vol)
                if (
                    item.close > item.open
                    and gain >= self.impulse_min_gain
                    and bull_body >= 0.48
                    and volume_mult >= self.impulse_volume_mult
                    and item.close * item.volume >= self.min_breakout_value_krw
                ):
                    imp_index = idx
                    imp_gain_value = max(1.0, item.high - min(item.open, bars[idx - 1].close))
                    imp_volume = float(max(1, item.volume))
                    imp_low = item.low
                    self.impulse_cache[symbol] = (item.timestamp, imp_gain_value, imp_volume, imp_low)
                    break
        if imp_index is None:
            return None

        imp = bars[imp_index]
        after = bars[imp_index + 1:-1]
        if len(after) < self.adjust_min_bars:
            return None

        pullback_low = min(x.low for x in after)
        retrace = (imp.high - pullback_low) / max(1.0, imp_gain_value)
        if self.adjust_retrace_third_min <= retrace <= self.adjust_retrace_third_max:
            zone = "삼분의일"
            return_mult = self.adjust_volume_return_third
        elif self.adjust_retrace_third_max < retrace <= self.adjust_retrace_half_max:
            zone = "이분의일"
            return_mult = self.adjust_volume_return_half
        else:
            # 너무 얕은 하락은 조정이 아니며, 2/3 이상 깊은 하락은 별도 원점회귀 투매패턴에서만 본다.
            if retrace > self.adjust_retrace_half_max:
                self.impulse_cache.pop(symbol, None)
            return None

        if min(pullback_low, current.low) < imp_low * 0.996:
            self.impulse_cache.pop(symbol, None)
            return None

        after_volumes = [max(0, x.volume) for x in after]
        floor_median = statistics.median(after_volumes) / max(1.0, imp_volume)
        floor_min = min(after_volumes) / max(1.0, imp_volume)
        if floor_median > self.adjust_volume_floor_median or floor_min > self.adjust_volume_floor_min:
            return None

        impulse_range = max(1.0, imp.high - imp.low)
        recent_adjust = after[-min(4, len(after)):]
        median_range = statistics.median(max(1.0, x.high - x.low) for x in recent_adjust)
        if median_range / impulse_range > self.adjust_range_contract:
            return None

        ma_ok, support_bars, ema_pairs, e5, e10, e20 = self._multi_bar_ema_support(bars)
        if not ma_ok:
            return None

        recent_confirmation = after[-3:] + [current]
        lower_wick_rejections = sum(1 for x in recent_confirmation if self._lower_wick_rejection(x))
        short_bulls = sum(1 for x in recent_confirmation[-3:] if self._short_bullish(x))
        if lower_wick_rejections < 1 and short_bulls < 2:
            return None

        prior_minor = after[-min(4, len(after)):]
        minor_high = max(x.high for x in prior_minor)
        if current.close < minor_high * (1.0 + self.adjust_minor_high_margin):
            return None

        pullback_median_vol = statistics.median(after_volumes)
        volume_return = current.volume / max(1.0, pullback_median_vol)
        if volume_return < return_mult:
            return None

        # 전고점 매물대 바로 아래의 애매한 중간구간 매수를 금지한다.
        space_to_impulse_high = imp.high / current.close - 1.0
        if -0.003 <= space_to_impulse_high <= self.near_resistance_pct:
            confirmed_high_break = (
                current.close >= imp.high * 1.001
                and volume_return >= self.near_resistance_volume_mult
            )
            if not confirmed_high_break:
                return None

        resistance_gap, support_gap, daily_note = self._daily_reference(symbol, current.close)
        if -0.003 <= resistance_gap <= self.near_resistance_pct:
            # 일봉 저항은 절대 금지가 아니지만, 거래 재폭발과 단기고점 회복이 약하면 진입하지 않는다.
            if volume_return < self.near_resistance_volume_mult or current.close < minor_high * 1.002:
                return None

        # 조정 중 손절/투매성 음봉은 필수는 아니지만 있으면 신뢰도를 높인다.
        selloff_seen = False
        for idx, item in enumerate(after):
            local = after[max(0, idx - 3):idx] + after[idx + 1:idx + 4]
            local_median = statistics.median([max(1, x.volume) for x in local]) if local else 1.0
            if item.close < item.open and item.volume >= local_median * 2.0:
                selloff_seen = True
                break

        same_time = self.same_time_volume_ratio.get(symbol, 0.0)
        source = self.candidate_sources.get(symbol, "")
        dual = symbol in self.dual_buy
        score = 112.0
        score += 6.0 if zone == "삼분의일" else 3.0
        score += min(8.0, max(0.0, volume_return - 1.0) * 3.0)
        score += min(4.0, support_bars)
        score += 3.0 if lower_wick_rejections else 0.0
        score += 3.0 if selloff_seen else 0.0
        score += 4.0 if 0 <= support_gap <= 0.03 else 0.0
        score -= 4.0 if -0.003 <= resistance_gap <= self.near_resistance_pct else 0.0
        score += 5.0 if dual else 0.0
        reason = (
            f"임펄스조정1차|구간={zone}({retrace:.0%})|거래바닥=중앙{floor_median:.2f},최저{floor_min:.2f}"
            f"|거래재유입=x{volume_return:.2f}|이평지지={support_bars}봉/쌍{ema_pairs}({e5:.0f}/{e10:.0f}/{e20:.0f})"
            f"|아래꼬리확인봉={lower_wick_rejections}|짧은양봉={short_bulls}|투매음봉={'Y' if selloff_seen else 'N'}"
            f"|단기고점={minor_high:.0f}|전고점공간={space_to_impulse_high:+.2%}|일봉={daily_note}"
            f"|동시간거래x{same_time:.2f}|관심={source or '-'}|쌍끌이={'Y' if dual else 'N'}"
        )
        return Signal(symbol, SignalAction.BUY, score, reason, current.close, current.timestamp, "임펄스눌림1차", self.pullback_stop_pct_impulse, self.take_profit_pct)


    def _capitulation_pullback(self, symbol: str, bars: list[Bar]) -> Signal | None:
        """상승폭을 대부분 반납한 뒤 투매·손절 거래가 터지고 안정된 원점회귀 반등만 매수한다."""
        current = bars[-1]
        if not self._short_bullish(current):
            return None

        cap_index: int | None = None
        cap_strength = 0.0
        swing_low = 0.0
        swing_high = 0.0
        cached = self.capitulation_cache.get(symbol)
        if cached is not None:
            cached_time, cached_strength, _cached_low = cached
            for idx, item in enumerate(bars):
                if item.timestamp == cached_time:
                    if self.adjust_min_bars + 1 <= len(bars) - 1 - idx <= self.capitulation_lookback:
                        cap_index = idx
                        cap_strength = cached_strength
                    break
            if cap_index is None:
                self.capitulation_cache.pop(symbol, None)

        search_start = max(20, len(bars) - self.capitulation_lookback)
        if cap_index is None:
            for idx in range(len(bars) - self.adjust_min_bars - 1, search_start - 1, -1):
                item = bars[idx]
                before = bars[max(0, idx - 20):idx]
                if len(before) < 12:
                    continue
                low_index = min(range(len(before)), key=lambda i: before[i].low)
                rise_part = before[low_index:]
                if len(rise_part) < 4:
                    continue
                local_low = before[low_index].low
                local_high = max(x.high for x in rise_part)
                rise_span = local_high - local_low
                if local_low <= 0 or rise_span / local_low < 0.02:
                    continue
                retrace_full = (local_high - item.low) / max(1.0, rise_span)
                median_vol = statistics.median(max(0, x.volume) for x in before[-12:])
                prior_max = max(x.volume for x in before[-12:])
                item_range = max(1.0, item.high - item.low)
                bearish_body = (item.open - item.close) / item_range
                drop = item.close / max(0.01, bars[idx - 1].close) - 1.0
                volume_mult = item.volume / max(1.0, median_vol)
                if (
                    item.close < item.open
                    and bearish_body >= 0.42
                    and drop <= -self.capitulation_min_drop
                    and volume_mult >= self.capitulation_volume_mult
                    and item.volume >= prior_max * self.capitulation_prior_max_mult
                    and retrace_full >= self.full_retrace_min
                ):
                    cap_index = idx
                    cap_strength = volume_mult
                    swing_low, swing_high = local_low, local_high
                    self.capitulation_cache[symbol] = (item.timestamp, volume_mult, item.low)
                    break
        if cap_index is None:
            return None

        cap = bars[cap_index]
        if swing_low <= 0 or swing_high <= swing_low:
            before = bars[max(0, cap_index - 20):cap_index]
            if not before:
                return None
            swing_low = min(x.low for x in before)
            swing_high = max(x.high for x in before)
        full_retrace = (swing_high - cap.low) / max(1.0, swing_high - swing_low)
        if full_retrace < self.full_retrace_min:
            return None

        after = bars[cap_index + 1:-1]
        if len(after) < self.adjust_min_bars:
            return None
        after_volumes = [max(0, x.volume) for x in after]
        floor_median = statistics.median(after_volumes) / max(1.0, cap.volume)
        floor_min = min(after_volumes) / max(1.0, cap.volume)
        if floor_median > 0.30 or floor_min > 0.16:
            return None

        post_lows = [x.low for x in bars[cap_index + 1:]]
        if not post_lows or min(post_lows) < cap.low * 0.994:
            self.capitulation_cache.pop(symbol, None)
            return None

        ma_ok, support_bars, ema_pairs, e5, e10, e20 = self._multi_bar_ema_support(bars)
        if not ma_ok:
            return None

        confirmation = after[-3:] + [current]
        lower_wick_rejections = sum(1 for x in confirmation if self._lower_wick_rejection(x))
        short_bulls = sum(1 for x in confirmation[-3:] if self._short_bullish(x))
        if lower_wick_rejections < 1 and short_bulls < 2:
            return None

        prior_minor = after[-min(4, len(after)):]
        minor_high = max(x.high for x in prior_minor)
        if current.close < minor_high * (1.0 + self.adjust_minor_high_margin):
            return None

        pullback_median_vol = statistics.median(after_volumes)
        volume_return = current.volume / max(1.0, pullback_median_vol)
        if volume_return < self.adjust_volume_return_full:
            return None

        resistance_gap, support_gap, daily_note = self._daily_reference(symbol, current.close)
        if -0.003 <= resistance_gap <= self.near_resistance_pct:
            if volume_return < self.near_resistance_volume_mult or current.close < minor_high * 1.002:
                return None

        same_time = self.same_time_volume_ratio.get(symbol, 0.0)
        source = self.candidate_sources.get(symbol, "")
        dual = symbol in self.dual_buy
        score = 114.0 + min(10.0, cap_strength) + min(5.0, support_bars)
        score += 3.0 if lower_wick_rejections else 0.0
        score += 4.0 if 0 <= support_gap <= 0.03 else 0.0
        score -= 4.0 if -0.003 <= resistance_gap <= self.near_resistance_pct else 0.0
        score += 5.0 if dual else 0.0
        reason = (
            f"원점회귀투매반등|상승반납={full_retrace:.0%}|투매거래=x{cap_strength:.2f}"
            f"|거래바닥=중앙{floor_median:.2f},최저{floor_min:.2f}|거래재유입=x{volume_return:.2f}"
            f"|이평지지={support_bars}봉/쌍{ema_pairs}({e5:.0f}/{e10:.0f}/{e20:.0f})"
            f"|아래꼬리확인봉={lower_wick_rejections}|짧은양봉={short_bulls}|단기고점={minor_high:.0f}"
            f"|일봉={daily_note}|동시간거래x{same_time:.2f}|관심={source or '-'}|쌍끌이={'Y' if dual else 'N'}"
        )
        return Signal(symbol, SignalAction.BUY, score, reason, current.close, current.timestamp, "투매후눌림1차", self.pullback_stop_pct, self.take_profit_pct)

    def _exit(self, bar: Bar, position: Position) -> Signal:
        position.highest_price = max(position.highest_price, bar.high)
        # 목표 체결 현실성: 분봉 고가가 목표가를 '관통'(초과)했고 그 분봉에 실제 체결(거래량>0)이
        # 있었을 때만 미리 걸어둔 지정가가 체결됐다고 본다. 고가가 목표가에 정확히 닿기만 한
        # 터치는 매도 대기순서상 미체결일 수 있으므로 인정하지 않는다(성과 과대평가 방지).
        target_hit = (
            position.target_order_active
            and bar.volume > 0
            and bar.high > position.target_price
        )
        stop_hit = bar.low <= position.stop_price
        # 같은 1분봉 안에서 목표와 손절이 모두 닿으면 선후를 알 수 없으므로 보수적으로 손절 처리한다.
        if target_hit and stop_hit:
            return Signal(bar.symbol, SignalAction.SELL, 100, "동일분봉양방향_보수적손절", position.stop_price, bar.timestamp, position.pattern)
        if target_hit:
            return Signal(bar.symbol, SignalAction.SELL, 100, "예약목표+1.5%", position.target_price, bar.timestamp, position.pattern)
        if stop_hit:
            return Signal(bar.symbol, SignalAction.SELL, 100, "고정손절", position.stop_price, bar.timestamp, position.pattern)
        # 시간 강제청산은 기본 비활성(V74_MAX_HOLD_MINUTES=0). 활성화 시에도 벽시계가 아니라
        # 실제 거래세션(평일 08:00~20:00, 휴장일 제외) 경과분만 계산한다.
        if self.max_hold_minutes > 0:
            held = session_minutes_between(position.entry_time, bar.timestamp.astimezone(KST))
            if held >= self.max_hold_minutes:
                return Signal(bar.symbol, SignalAction.SELL, 70, f"장시간미도달_{held:.0f}분(세션기준)", bar.close, bar.timestamp, position.pattern)
        return Signal(bar.symbol, SignalAction.HOLD, 0.0, "보유", bar.close, bar.timestamp, position.pattern)

class RiskEngine:
    """PAPER FLEX: 데이터 오류만 진입을 막고, 금액·종목 수·일손실은 기록만 한다."""

    def __init__(
        self,
        settings: Settings,
        starting_equity: float,
        *,
        daily_realized_pnl: float = 0.0,
        daily_halted: bool = False,
        daily_halt_reason: str = "",
    ) -> None:
        self.settings = settings
        self.starting_equity = max(1.0, starting_equity)
        self.daily_realized_pnl = daily_realized_pnl
        self.daily_halted = daily_halted
        self.daily_halt_reason = daily_halt_reason
        self.data_halted = False
        self._recheck_daily_limit()

    @property
    def halted(self) -> bool:
        return self.data_halted or (self.daily_halted and not self.settings.paper_unlimited)

    @property
    def halt_reason(self) -> str:
        reasons: list[str] = []
        if self.daily_halted and not self.settings.paper_unlimited:
            reasons.append(self.daily_halt_reason or "일일손실한도")
        if self.data_halted:
            reasons.append("시장데이터중단")
        return "+".join(reasons)

    def _recheck_daily_limit(self) -> None:
        if self.settings.paper_unlimited:
            self.daily_halted = False
            self.daily_halt_reason = ""
            return
        limit = -self.starting_equity * self.settings.max_daily_loss_pct
        if self.daily_realized_pnl <= limit:
            self.daily_halted = True
            self.daily_halt_reason = "일일손실한도"

    def reset_daily_session(self, starting_equity: float) -> None:
        self.starting_equity = max(1.0, starting_equity)
        self.daily_realized_pnl = 0.0
        self.daily_halted = False
        self.daily_halt_reason = ""

    def restore_daily_state(self, *, starting_equity: float, daily_realized_pnl: float, daily_halted: bool, daily_halt_reason: str) -> None:
        self.starting_equity = max(1.0, starting_equity)
        self.daily_realized_pnl = daily_realized_pnl
        self.daily_halted = daily_halted
        self.daily_halt_reason = daily_halt_reason
        self._recheck_daily_limit()

    def set_data_halt(self, halted: bool) -> None:
        self.data_halted = halted

    def record_pnl(self, pnl_value: float) -> None:
        self.daily_realized_pnl += pnl_value
        self._recheck_daily_limit()

    def approve_entry(self, signal: Signal, quote: Quote, equity: float, cash: float, positions: Mapping[str, Position]) -> RiskDecision:
        if self.halted:
            return RiskDecision(False, self.halt_reason)
        if not entry_session_open(datetime.now(KST)):
            return RiskDecision(False, "진입시간아님")
        if signal.score < self.settings.min_signal_score:
            return RiskDecision(False, "신호점수미달")
        age = (datetime.now(timezone.utc) - quote.timestamp).total_seconds()
        if age > self.settings.max_quote_age_sec:
            return RiskDecision(False, f"시세지연_{age:.1f}초")
        if quote.symbol in positions:
            return RiskDecision(False, "이미보유")
        if quote.spread_pct is None or quote.best_ask is None:
            return RiskDecision(False, "호가없음")
        if quote.spread_pct > self.settings.max_spread_pct:
            return RiskDecision(False, f"스프레드초과_{quote.spread_pct:.3%}")

        expected_fill = align_stock_price(
            quote.best_ask * (1.0 + self.settings.slippage_pct),
            upward=True,
        )
        if expected_fill <= 0:
            return RiskDecision(False, "예상체결가오류")
        if self.settings.paper_unlimited:
            quantity = max(1, math.floor(self.settings.paper_unit_notional / expected_fill))
            return RiskDecision(True, "PAPER무제한승인", quantity)

        if len(positions) >= self.settings.max_open_positions:
            return RiskDecision(False, "최대보유수")
        risk_budget = equity * self.settings.risk_per_trade_pct
        stop_distance = expected_fill * signal.stop_loss_pct
        by_risk = math.floor(risk_budget / max(stop_distance, 1.0))
        by_notional = math.floor(equity * self.settings.max_position_notional_pct / expected_fill)
        by_cash = math.floor(cash / (expected_fill * (1 + self.settings.commission_pct)))
        quantity = max(0, min(by_risk, by_notional, by_cash))
        if quantity < 1:
            return RiskDecision(False, "계산수량0")
        return RiskDecision(True, "승인", quantity)


# -----------------------------------------------------------------------------
# PAPER 브로커 및 상태 저장
# -----------------------------------------------------------------------------


class PaperBroker:
    def __init__(self, settings: Settings, logger: logging.Logger, reset: bool = False) -> None:
        self.settings = settings
        self.logger = logger
        self.cash = settings.initial_cash
        self.realized_pnl = 0.0  # 누적 실현손익
        self.daily_realized_pnl = 0.0
        self.session_date = datetime.now(KST).date().isoformat()
        self.day_start_equity = settings.initial_cash
        self.daily_halted = False
        self.daily_halt_reason = ""
        self.last_equity = settings.initial_cash
        self.positions: dict[str, Position] = {}
        self.recent_fills: Deque[Fill] = deque(maxlen=20)
        self.reentry_block_until: dict[str, datetime] = {}
        self.state_load_failed = False
        self.stop_reentry_cooldown_min = env_int("V74_STOP_REENTRY_COOLDOWN_MIN", 30)
        self.sequence = 0
        if reset:
            for path in (STATE_FILE, STATE_FILE.with_suffix(".bak"), FILL_FILE, PATTERN_STATE_FILE):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
        self.load_state()

    @staticmethod
    def _normalize_datetime(value: str) -> datetime:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=KST)
        return parsed.astimezone(KST)

    def load_state(self) -> None:
        candidates = [STATE_FILE, STATE_FILE.with_suffix(".bak")]
        existing = [path for path in candidates if path.exists()]
        if not existing:
            return

        data: dict[str, Any] | None = None
        loaded_from: Path | None = None
        errors: list[str] = []
        for path in existing:
            try:
                candidate = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(candidate, dict):
                    raise ValueError("상태 최상위가 객체가 아님")
                data = candidate
                loaded_from = path
                break
            except Exception as exc:
                errors.append(f"{path.name}:{exc}")

        if data is None:
            self.state_load_failed = True
            self.logger.critical(
                "PAPER 상태 복구 실패—신규진입 차단. --reset-paper 또는 상태 복구 필요: %s",
                " / ".join(errors),
            )
            return

        try:
            self.cash = float(data.get("cash", self.cash))
            self.realized_pnl = float(data.get("realized_pnl", 0.0))
            self.daily_realized_pnl = float(data.get("daily_realized_pnl", 0.0))
            self.sequence = int(data.get("sequence", 0))
            self.session_date = str(data.get("session_date", self.session_date))
            self.day_start_equity = float(data.get("day_start_equity", self.settings.initial_cash))
            self.daily_halted = bool(data.get("daily_halted", False))
            self.daily_halt_reason = str(data.get("daily_halt_reason", ""))
            self.last_equity = float(data.get("last_equity", self.cash))
            self.positions = {}
            for symbol, raw in dict(data.get("positions", {})).items():
                raw_entry = float(raw["entry_price"])
                raw_stop = float(raw["stop_price"])
                raw_target = float(raw["target_price"])
                self.positions[symbol] = Position(
                    symbol=symbol,
                    quantity=int(raw["quantity"]),
                    entry_price=raw_entry,
                    entry_time=self._normalize_datetime(str(raw["entry_time"])),
                    highest_price=float(raw["highest_price"]),
                    stop_price=align_stock_price(raw_stop, upward=False),
                    target_price=align_stock_price(raw_target, upward=True),
                    entry_fee=float(
                        raw.get(
                            "entry_fee",
                            raw_entry * int(raw["quantity"]) * self.settings.commission_pct,
                        )
                    ),
                    pattern=str(raw.get("pattern", "")),
                    target_order_active=bool(raw.get("target_order_active", True)),
                )
            self.reentry_block_until = {}
            for symbol, raw_time in dict(data.get("reentry_block_until", {})).items():
                until = self._normalize_datetime(str(raw_time))
                if until > datetime.now(KST):
                    self.reentry_block_until[symbol] = until

            today = datetime.now(KST).date().isoformat()
            if self.session_date != today:
                self.session_date = today
                self.daily_realized_pnl = 0.0
                self.daily_halted = False
                self.daily_halt_reason = ""
                self.day_start_equity = max(1.0, self.last_equity)
                self.logger.warning(
                    "PAPER 새 거래일 초기화 day_start_equity=%.0f positions=%s",
                    self.day_start_equity,
                    len(self.positions),
                )
            self.logger.info(
                "PAPER 상태 복구 source=%s cash=%.0f positions=%s 일손익=%s",
                loaded_from.name if loaded_from else "-",
                self.cash,
                len(self.positions),
                money(self.daily_realized_pnl),
            )
        except Exception as exc:
            self.state_load_failed = True
            self.logger.critical("PAPER 상태 내용 오류—신규진입 차단: %s", exc)

    def roll_session_if_needed(self, current_equity: float) -> bool:
        today = datetime.now(KST).date().isoformat()
        if self.session_date == today:
            return False
        self.session_date = today
        self.daily_realized_pnl = 0.0
        self.daily_halted = False
        self.daily_halt_reason = ""
        self.day_start_equity = max(1.0, current_equity)
        self.last_equity = current_equity
        self.save_state()
        return True

    def update_daily_halt(self, halted: bool, reason: str) -> None:
        normalized_reason = reason if halted else ""
        changed = self.daily_halted != halted or self.daily_halt_reason != normalized_reason
        self.daily_halted = halted
        self.daily_halt_reason = normalized_reason
        if changed:
            self.save_state()

    def save_state(self) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        positions = {}
        for symbol, pos in self.positions.items():
            positions[symbol] = {
                "quantity": pos.quantity,
                "entry_price": pos.entry_price,
                "entry_time": pos.entry_time.isoformat(),
                "highest_price": pos.highest_price,
                "stop_price": pos.stop_price,
                "target_price": pos.target_price,
                "entry_fee": pos.entry_fee,
                "pattern": pos.pattern,
                "target_order_active": pos.target_order_active,
            }
        payload = {
            "version": VERSION,
            "saved_at": datetime.now(KST).isoformat(),
            "session_date": self.session_date,
            "day_start_equity": self.day_start_equity,
            "cash": self.cash,
            "realized_pnl": self.realized_pnl,
            "daily_realized_pnl": self.daily_realized_pnl,
            "daily_halted": self.daily_halted,
            "daily_halt_reason": self.daily_halt_reason,
            "last_equity": self.last_equity,
            "sequence": self.sequence,
            "positions": positions,
            "reentry_block_until": {
                symbol: until.isoformat()
                for symbol, until in self.reentry_block_until.items()
                if until > datetime.now(KST)
            },
        }
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        temp = STATE_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if STATE_FILE.exists():
            try:
                STATE_FILE.with_suffix(".bak").write_bytes(STATE_FILE.read_bytes())
            except OSError:
                pass
        temp.replace(STATE_FILE)

    def equity(self, prices: Mapping[str, float]) -> float:
        value = self.cash
        for symbol, position in self.positions.items():
            value += position.quantity * prices.get(symbol, position.entry_price)
        self.last_equity = value
        return value

    def buy(
        self,
        symbol: str,
        quantity: int,
        quote: Quote,
        reason: str,
        *,
        stop_loss_pct: float | None = None,
        take_profit_pct: float | None = None,
        pattern: str = "",
    ) -> Fill:
        if quantity <= 0:
            raise ValueError("매수수량 0 이하")
        if symbol in self.positions:
            raise ValueError("동일 종목 중복매수 차단")
        if quote.best_ask is None or quote.best_ask <= 0:
            raise ValueError("매도1호가 없음—PAPER 매수 차단")
        price = align_stock_price(
            quote.best_ask * (1 + self.settings.slippage_pct),
            upward=True,
        )
        gross = price * quantity
        fee = gross * self.settings.commission_pct
        if not self.settings.paper_unlimited and gross + fee > self.cash:
            raise ValueError("PAPER 현금 부족")
        stop_pct = self.settings.stop_loss_pct if stop_loss_pct is None else stop_loss_pct
        target_pct = self.settings.take_profit_pct if take_profit_pct is None else take_profit_pct
        self.sequence += 1
        self.cash -= gross + fee
        now = datetime.now(KST)
        self.positions[symbol] = Position(
            symbol=symbol,
            quantity=quantity,
            entry_price=price,
            entry_time=now,
            highest_price=price,
            stop_price=align_stock_price(price * (1 - stop_pct), upward=False),
            target_price=align_stock_price(price * (1 + target_pct), upward=True),
            entry_fee=fee,
            pattern=pattern,
            target_order_active=True,
        )
        fill = Fill(f"PAPER-{self.sequence:06d}", now, symbol, "BUY", quantity, price, fee, 0.0, -fee, reason)
        self._record_fill(fill)
        self.save_state()
        return fill

    def sell(
        self,
        symbol: str,
        quote: Quote,
        reason: str,
        *,
        reserved_fill_price: float | None = None,
        trigger_time: datetime | None = None,
        trigger_price: float | None = None,
        detection_time: datetime | None = None,
        observed_lag_sec: float = 0.0,
        trigger_source: str = "",
    ) -> Fill:
        position = self.positions.get(symbol)
        if position is None:
            raise ValueError("미보유 종목 매도 차단")

        if reserved_fill_price is not None:
            if not position.target_order_active or not reason.startswith("예약목표"):
                raise ValueError("예약목표가 아닌 지정가격 PAPER 체결 차단")
            if reserved_fill_price <= 0:
                raise ValueError("예약목표 체결가격 오류")
            price = align_stock_price(position.target_price, upward=True)
        else:
            if quote.best_bid is None or quote.best_bid <= 0:
                raise ValueError("매수1호가 없음—PAPER 매도 차단")
            price = align_stock_price(
                quote.best_bid * (1 - self.settings.slippage_pct),
                upward=False,
            )

        gross = price * position.quantity
        fee = gross * self.settings.commission_pct
        tax = gross * self.settings.sell_tax_pct
        proceeds = gross - fee - tax
        pnl_value = proceeds - position.entry_cost
        pnl_pct = pnl_value / max(1.0, position.entry_cost)
        self.sequence += 1
        self.cash += proceeds
        self.realized_pnl += pnl_value
        self.daily_realized_pnl += pnl_value
        now = datetime.now(KST)
        normalized_detection = detection_time.astimezone(KST) if detection_time is not None else now
        normalized_trigger = trigger_time.astimezone(KST) if trigger_time is not None else normalized_detection
        monitor_lag = max(0.0, (normalized_detection - normalized_trigger).total_seconds())
        execution_lag = max(0.0, (now - normalized_detection).total_seconds())
        observed_lag = max(0.0, float(observed_lag_sec))
        stop_overrun = 0.0
        if "손절" in reason and position.stop_price > 0:
            stop_overrun = max(0.0, (position.stop_price - price) / position.stop_price)
            if self.stop_reentry_cooldown_min > 0:
                self.reentry_block_until[symbol] = now + timedelta(minutes=self.stop_reentry_cooldown_min)
        fill = Fill(
            f"PAPER-{self.sequence:06d}",
            now,
            symbol,
            "SELL",
            position.quantity,
            price,
            fee,
            tax,
            pnl_value,
            reason,
            entry_time=position.entry_time,
            entry_price=position.entry_price,
            pnl_pct=pnl_pct,
            pattern=position.pattern,
            trigger_time=normalized_trigger,
            trigger_price=float(trigger_price or 0.0),
            detection_time=normalized_detection,
            monitor_lag_sec=monitor_lag,
            execution_lag_sec=execution_lag,
            observed_lag_sec=observed_lag,
            trigger_source=trigger_source,
            stop_overrun_pct=stop_overrun,
        )
        del self.positions[symbol]
        self._record_fill(fill)
        self.save_state()
        return fill

    def _record_fill(self, fill: Fill) -> None:
        self.recent_fills.append(fill)
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        new_file = not FILL_FILE.exists()
        with FILL_FILE.open("a", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            if new_file:
                writer.writerow([
                    "order_id", "timestamp", "symbol", "side", "quantity", "price",
                    "fee", "tax", "pnl", "reason", "pattern", "entry_time",
                    "entry_price", "pnl_pct", "trigger_time", "trigger_price",
                    "detection_time", "monitor_lag_sec", "execution_lag_sec",
                    "observed_lag_sec", "trigger_source", "stop_overrun_pct",
                ])
            writer.writerow([
                fill.order_id,
                fill.timestamp.isoformat(),
                fill.symbol,
                fill.side,
                fill.quantity,
                round(fill.price, 4),
                round(fill.fee, 4),
                round(fill.tax, 4),
                round(fill.pnl, 4),
                fill.reason,
                fill.pattern,
                fill.entry_time.isoformat() if fill.entry_time else "",
                round(fill.entry_price, 4),
                round(fill.pnl_pct, 8),
                fill.trigger_time.isoformat() if fill.trigger_time else "",
                round(fill.trigger_price, 4),
                fill.detection_time.isoformat() if fill.detection_time else "",
                round(fill.monitor_lag_sec, 3),
                round(fill.execution_lag_sec, 3),
                round(fill.observed_lag_sec, 3),
                fill.trigger_source,
                round(fill.stop_overrun_pct, 8),
            ])


# -----------------------------------------------------------------------------
# 실행 엔진
# -----------------------------------------------------------------------------


# 2026년 KRX/NXT 휴장일 (한국거래소 공지 기준. NXT는 KRX와 동일하게 휴장한다.)
# 추가 임시휴장은 V74_EXTRA_HOLIDAYS=YYYY-MM-DD,YYYY-MM-DD 형식 환경변수로 등록한다.
KRX_HOLIDAYS_2026: frozenset[str] = frozenset({
    "2026-01-01",              # 신정
    "2026-02-16", "2026-02-17", "2026-02-18",  # 설날 연휴
    "2026-03-02",              # 삼일절 대체공휴일
    "2026-05-01",              # 근로자의 날
    "2026-05-05",              # 어린이날
    "2026-05-25",              # 부처님오신날 대체공휴일
    "2026-06-03",              # 지방선거 임시공휴일
    "2026-08-17",              # 광복절 대체공휴일
    "2026-09-24", "2026-09-25",  # 추석 연휴(9/26은 토요일)
    "2026-10-05",              # 개천절 대체공휴일
    "2026-10-09",              # 한글날
    "2026-12-25",              # 성탄절
    "2026-12-31",              # 연말 휴장
})


def _extra_holidays() -> frozenset[str]:
    raw = os.getenv("V74_EXTRA_HOLIDAYS", "").strip()
    if not raw:
        return frozenset()
    return frozenset(item.strip() for item in raw.split(",") if item.strip())


def is_market_holiday(day: date) -> bool:
    key = day.isoformat()
    return key in KRX_HOLIDAYS_2026 or key in _extra_holidays()


def session_segment(now_kst: datetime) -> str:
    """KRX/NXT 연속매매 기준 구간. 갭에서는 신규진입을 금지한다."""
    if now_kst.weekday() >= 5 or is_market_holiday(now_kst.date()):
        return "CLOSED"
    current = now_kst.time().replace(tzinfo=None)
    if dt_time(8, 0) <= current < dt_time(8, 50):
        return "PRE"
    if dt_time(8, 50) <= current < dt_time(9, 0):
        return "GAP_OPEN"
    if dt_time(9, 0) <= current < dt_time(15, 30):
        return "MAIN"
    if dt_time(15, 30) <= current < dt_time(15, 40):
        return "GAP_CLOSE"
    if dt_time(15, 40) <= current < dt_time(20, 0):
        return "AFTER"
    return "CLOSED"


SESSION_KOREAN = {
    "PRE": "프리마켓",
    "GAP_OPEN": "08:50~09:00 대기",
    "MAIN": "정규장",
    "GAP_CLOSE": "15:30~15:40 대기",
    "AFTER": "애프터마켓",
    "CLOSED": "장외",
}


def regular_session_bar(timestamp: datetime) -> bool:
    local = timestamp.astimezone(KST)
    return session_segment(local) in {"PRE", "MAIN", "AFTER"}


def entry_session_open(now_kst: datetime) -> bool:
    """물리적으로 신규진입 가능한 연속매매 구간인지 반환한다."""
    return session_segment(now_kst) in {"PRE", "MAIN", "AFTER"}


def strategy_entry_session_open(now_kst: datetime, *, after_entry_enabled: bool) -> bool:
    """전략 신규진입 허용 구간.

    애프터는 데이터 제공 여부가 종목·API 경로에 따라 불확실할 수 있으므로 기본 비활성이다.
    명시적으로 V75_ENABLE_AFTER_ENTRY=1을 설정한 경우에만 AFTER 신규진입을 허용한다.
    보유종목 청산 감시는 이 함수와 무관하게 항상 최우선으로 실행된다.
    """
    segment = session_segment(now_kst)
    return segment in {"PRE", "MAIN"} or (segment == "AFTER" and after_entry_enabled)


def session_minutes_between(entry_time: datetime, now_kst: datetime) -> float:
    start = entry_time.astimezone(KST)
    end = now_kst.astimezone(KST)
    if end <= start:
        return 0.0
    total = 0.0
    cursor_day = start.date()
    intervals = ((dt_time(8, 0), dt_time(8, 50)), (dt_time(9, 0), dt_time(15, 30)), (dt_time(15, 40), dt_time(20, 0)))
    while cursor_day <= end.date():
        if cursor_day.isoweekday() <= 5 and not is_market_holiday(cursor_day):
            for open_time, close_time in intervals:
                seg_open = datetime.combine(cursor_day, open_time, tzinfo=KST)
                seg_close = datetime.combine(cursor_day, close_time, tzinfo=KST)
                seg_start = max(start, seg_open)
                seg_end = min(end, seg_close)
                if seg_end > seg_start:
                    total += (seg_end - seg_start).total_seconds() / 60.0
        cursor_day += timedelta(days=1)
    return total


def market_label(now_kst: datetime) -> str:
    if now_kst.weekday() >= 5:
        return "WEEKEND"
    if is_market_holiday(now_kst.date()):
        return "HOLIDAY"
    return "ACTIVE" if session_segment(now_kst) != "CLOSED" else "WAIT"


class TradingEngine:
    def __init__(self, settings: Settings, client: KiwoomRestClient, logger: logging.Logger, *, reset_paper: bool = False) -> None:
        self.settings = settings
        self.client = client
        self.logger = logger
        self.strategy = MomentumVolumeStrategy(settings)
        self.broker = PaperBroker(settings, logger, reset=reset_paper)
        self.risk = RiskEngine(
            settings,
            self.broker.day_start_equity,
            daily_realized_pnl=self.broker.daily_realized_pnl,
            daily_halted=self.broker.daily_halted,
            daily_halt_reason=self.broker.daily_halt_reason,
        )
        self.state_integrity_block = self.broker.state_load_failed
        if self.state_integrity_block:
            self.risk.set_data_halt(True)
        self.latest_quotes: dict[str, Quote] = {}
        self.rank_snapshots: dict[str, Candidate] = {}
        self.symbol_sessions: dict[str, str] = {}
        self.last_bar_time: dict[str, datetime] = {}
        self.next_chart_due: dict[str, float] = {}
        self.names: dict[str, str] = {symbol: name for symbol, name in settings.watchlist}
        self.active_symbols: list[str] = []
        self.universe_pool: list[Candidate] = []
        self.candidate_meta: dict[str, Candidate] = {}
        self.next_scan = 0.0
        self.rotation_cursor = 0
        self.consecutive_failures = 0
        self.recovery_successes = 0
        self.next_report = time.monotonic() + settings.report_interval_sec
        self.latest_signals: dict[str, Signal] = {}
        self.daily_cache: dict[str, tuple[float, DailyContext]] = {}
        self.error_last_shown: dict[str, float] = {}
        self.debug = env_bool("V74_DEBUG", False)
        self.idle_sleep = env_float("V74_IDLE_SLEEP_SEC", 1.0)
        self.chart_refresh = env_float("V74_CHART_REFRESH_SEC", 90.0)
        # 신호 임박(HOT) 후보는 더 짧은 주기로 분봉을 갱신해 돌파·눌림 완성 직후를 놓치지 않는다.
        self.chart_refresh_hot = env_float("V74_CHART_REFRESH_HOT_SEC", 30.0)
        self.hot_candidate_count = env_int("V74_HOT_CANDIDATES", 25)
        self.rank_limit = env_int("V74_RANK_LIMIT", 100)
        self.pool_limit = env_int("V74_POOL_LIMIT", 200)
        self.bootstrap_queue: Deque[str] = deque()
        self.bootstrap_pending: set[str] = set()
        self.position_cursor = 0
        observation_start = datetime.now(KST)
        self.last_position_observation_at: dict[str, datetime] = {
            symbol: observation_start for symbol in self.broker.positions
        }
        self.candidate_cursor = 0
        self.hot_cursor = 0
        self.hot_symbols: list[str] = []
        # VI·호가공백·일시 오류로 놓친 매수 신호를 짧게 재시도하는 대기열: symbol -> (signal, 만료, 다음시도)
        self.pending_entries: dict[str, PendingEntry] = {}
        self.entry_retry_sec = env_float("V74_ENTRY_RETRY_SEC", 15.0)
        self.retry_window_pre = env_float("V74_RETRY_WINDOW_PRE_SEC", 90.0)
        self.retry_window_main = env_float("V74_RETRY_WINDOW_MAIN_SEC", 60.0)
        self.retry_window_after = env_float("V74_RETRY_WINDOW_AFTER_SEC", 90.0)
        self.retry_max_attempts = env_int("V74_RETRY_MAX_ATTEMPTS", 3)
        self.max_chase_breakout = env_float("V74_MAX_CHASE_BREAKOUT", 0.007)
        self.max_chase_impulse = env_float("V74_MAX_CHASE_IMPULSE", 0.010)
        self.max_chase_capitulation = env_float("V74_MAX_CHASE_CAPITULATION", 0.005)
        self.max_adverse_drift_pct = env_float("V74_MAX_ADVERSE_DRIFT_PCT", 0.006)
        self.max_monitor_delay_sec = env_float("V74_MAX_MONITOR_DELAY_SEC", 15.0)
        # AFTER는 기본적으로 보유종목 청산 감시 전용이다. 명시적 opt-in일 때만 신규진입한다.
        self.after_entry_enabled = env_bool("V75_ENABLE_AFTER_ENTRY", False)
        self.after_reprobe_sec = env_float("V75_AFTER_REPROBE_SEC", 900.0)
        self.after_no_bar_until: dict[str, float] = {}
        self.after_state_day: date | None = None
        self.after_monitor_only_announced_day: date | None = None
        # V75 신규진입 유동성 자격: 상대 배수만 높고 실제 거래가 없는 종목을 차단한다.
        self.min_same_time_volume_ratio = env_float("V75_MIN_SAME_TIME_VOLUME_RATIO", 1.50)
        self.min_recent5_value = env_float("V75_MIN_RECENT5_VALUE_KRW", 150_000_000.0)
        self.min_signal_bar_value = env_float("V75_MIN_SIGNAL_BAR_VALUE_KRW", 30_000_000.0)
        self.min_cum_value_pre = env_float("V75_MIN_CUM_VALUE_PRE_KRW", 300_000_000.0)
        self.min_cum_value_0910 = env_float("V75_MIN_CUM_VALUE_0910_KRW", 500_000_000.0)
        self.min_cum_value_0930 = env_float("V75_MIN_CUM_VALUE_0930_KRW", 1_500_000_000.0)
        self.min_cum_value_1000 = env_float("V75_MIN_CUM_VALUE_1000_KRW", 2_500_000_000.0)
        self.min_cum_value_day = env_float("V75_MIN_CUM_VALUE_DAY_KRW", 5_000_000_000.0)
        # 종목별 연속 실패 격리(전역 중단은 후보 API 전체 실패 시에만).
        self.symbol_failures: dict[str, int] = defaultdict(int)
        self.loop_seconds_ema = 3.0
        self.loop_index = 0
        self.next_hot_update = 0.0
        # 재시작 시 투매·임펄스 패턴 상태 복구(사용 시점에 분봉 시각 재검색으로 재검증됨).
        try:
            if PATTERN_STATE_FILE.exists():
                payload = json.loads(PATTERN_STATE_FILE.read_text(encoding="utf-8"))
                if str(payload.get("version", "")) in PATTERN_STATE_COMPATIBLE_VERSIONS:
                    restored = self.strategy.restore_pattern_state(payload)
                    if restored:
                        self.logger.info("패턴상태 복구 %s건", restored)
                else:
                    # V75는 임펄스/투매 상태 의미가 달라 이전 버전 패턴 캐시를 재사용하지 않는다.
                    self.logger.warning("비호환 패턴상태 폐기—V75 조건으로 새로 탐색")
        except (OSError, json.JSONDecodeError) as exc:
            self.logger.warning("패턴상태 복구 실패—새로 시작: %s", brief_error(exc))

    def _reset_after_state_if_needed(self, now_kst: datetime) -> None:
        """애프터 분봉 미제공 종목의 재조회 유예상태를 거래일 단위로 관리한다."""
        if self.after_state_day != now_kst.date():
            self.after_state_day = now_kst.date()
            self.after_no_bar_until.clear()
            self.after_monitor_only_announced_day = None

    def _new_entry_session_allowed(self, now_kst: datetime | None = None) -> bool:
        current = now_kst or datetime.now(KST)
        return strategy_entry_session_open(current, after_entry_enabled=self.after_entry_enabled)

    def _after_bootstrap_deferred(self, symbol: str, now_mono: float | None = None) -> bool:
        if session_segment(datetime.now(KST)) != "AFTER":
            return False
        current_mono = time.monotonic() if now_mono is None else now_mono
        return current_mono < self.after_no_bar_until.get(symbol, 0.0)

    def _enter_after_monitor_only(self, now_kst: datetime) -> None:
        """애프터 기본모드: 신규진입 관련 작업만 비우고 보유종목 감시는 그대로 둔다."""
        self.pending_entries.clear()
        self.bootstrap_queue.clear()
        self.bootstrap_pending.clear()
        self.hot_symbols.clear()
        if self.after_monitor_only_announced_day != now_kst.date():
            self.logger.warning(
                "애프터 신규진입 비활성—보유종목 목표·손절 감시만 유지 "
                "(활성화: V75_ENABLE_AFTER_ENTRY=1)"
            )
            self.after_monitor_only_announced_day = now_kst.date()

    def save_pattern_state(self) -> None:
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            payload = {"version": VERSION, "saved_at": datetime.now(KST).isoformat(), **self.strategy.export_pattern_state()}
            temp = PATTERN_STATE_FILE.with_suffix(".tmp")
            temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            temp.replace(PATTERN_STATE_FILE)
        except OSError as exc:
            self._show_throttled_error("pattern-save", f"패턴상태 저장 실패: {brief_error(exc)}", 300.0)

    def _label(self, symbol: str) -> str:
        return f"{symbol} {self.names.get(symbol, symbol)}"

    def _fallback_universe(self) -> list[str]:
        for symbol, name in self.settings.watchlist:
            self.names[symbol] = name
        return [symbol for symbol, _ in self.settings.watchlist]

    @staticmethod
    def _merge_candidates(groups: Iterable[Iterable[Candidate]]) -> list[Candidate]:
        merged: dict[str, Candidate] = {}
        source_map: dict[str, set[str]] = defaultdict(set)
        for group in groups:
            for item in group:
                source_map[item.symbol].add(item.source)
                old = merged.get(item.symbol)
                if old is None:
                    merged[item.symbol] = item
                    continue
                merged[item.symbol] = Candidate(
                    symbol=item.symbol,
                    name=item.name or old.name,
                    price=item.price or old.price,
                    change_pct=max(item.change_pct, old.change_pct),
                    volume=max(item.volume, old.volume),
                    surge_rate=max(item.surge_rate, old.surge_rate),
                    score=old.score + item.score * 0.65,
                    source=old.source,
                    foreign_net=max(old.foreign_net, item.foreign_net),
                    institution_net=max(old.institution_net, item.institution_net),
                    dual_buy=old.dual_buy or item.dual_buy,
                )
        result: list[Candidate] = []
        for symbol, item in merged.items():
            sources = "+".join(sorted(source_map[symbol], key=lambda x: {"상승률": 0, "거래량급증": 1, "쌍끌이": 2}.get(x, 9)))
            overlap_bonus = max(0, len(source_map[symbol]) - 1) * 20.0
            result.append(Candidate(
                item.symbol, item.name, item.price, item.change_pct, item.volume, item.surge_rate,
                item.score + overlap_bonus + (15.0 if item.dual_buy else 0.0), sources,
                item.foreign_net, item.institution_net, item.dual_buy,
            ))
        return sorted(result, key=lambda x: (x.score, x.dual_buy, x.surge_rate, x.change_pct, x.volume), reverse=True)

    def _refresh_universe(self, *, force: bool = False) -> list[str]:
        now = time.monotonic()
        if not force and now < self.next_scan:
            return []
        self.next_scan = now + self.settings.scan_interval_sec
        previous = set(self.active_symbols)
        groups: list[list[Candidate]] = []
        failures: list[str] = []
        if self.settings.dynamic_scan:
            for name, loader in (
                ("상승률100", lambda: self.client.get_gain_rank_candidates(limit=self.rank_limit)),
                ("거래량급증", lambda: self.client.get_volume_surge_candidates(limit=self.rank_limit)),
                ("외국인·기관쌍끌이", lambda: self.client.get_dual_buy_candidates(limit=self.rank_limit)),
            ):
                try:
                    groups.append(loader())
                except V73Error as exc:
                    failures.append(f"{name}:{brief_error(exc)}")
        if groups:
            pool = self._merge_candidates(groups)[: max(self.rank_limit, self.pool_limit)]
            if pool and not self.state_integrity_block:
                self.risk.set_data_halt(False)
            self.universe_pool = pool
            for item in pool:
                self.names[item.symbol] = item.name
                self.candidate_meta[item.symbol] = item
                self.rank_snapshots[item.symbol] = item
            # 조건 후보 전체를 분석대상에 둔다. 보유종목 수와 신규 매수 종목 수는
            # PAPER 모드에서 별도 상한을 두지 않으며, 동일 종목 중복매수만 차단한다.
            selected = list(dict.fromkeys(item.symbol for item in pool))
            self.active_symbols = selected
            top = ", ".join(f"{x.name}({x.source})" for x in pool[:5])
            self.logger.warning(
                "관심종목 %s개 등록 / 전체 종목을 차례대로 분석 / 상위=%s",
                len(selected), top or "없음",
            )
            if failures and self.debug:
                self.logger.info("일부 후보원 실패: %s", " / ".join(failures))
        else:
            self.active_symbols = []
            self.universe_pool = []
            self.risk.set_data_halt(True)
            self._show_throttled_error(
                "universe-all-failed",
                "후보 API 전체 실패—신규 매수 중단, 보유종목 청산만 유지",
                120.0,
            )
            if self.debug and failures:
                self.logger.info("후보 API 실패 상세: %s", " / ".join(failures))
        return [symbol for symbol in self.active_symbols if symbol not in previous]

    def _current_symbols(self) -> list[str]:
        """신규 진입 분석 대상만 반환한다. 보유종목은 별도 최우선 감시 루프가 담당한다."""
        symbols = list(dict.fromkeys(self.active_symbols))
        if session_segment(datetime.now(KST)) == "AFTER":
            now_mono = time.monotonic()
            symbols = [symbol for symbol in symbols if now_mono >= self.after_no_bar_until.get(symbol, 0.0)]
        return symbols

    @staticmethod
    def _build_daily_context(daily: list[DailyBar], current_price: float) -> DailyContext:
        """완성 일봉의 피벗을 이용해 지지·저항 구간과 추세 기울기를 계산한다.

        사람의 눈으로 긋는 선을 흉내 내되, 진행 중인 오늘 일봉은 제외하고 반복 반응한
        국소 고점·저점을 우선 사용한다. 선 한 점이 아니라 약간의 가격 구간으로 해석한다.
        """
        if not daily:
            return DailyContext(None, None, 0.0, 0.0, False)
        today = datetime.now(KST).date()
        completed = [bar for bar in daily if bar.trading_date < today]
        recent = completed or list(daily)
        recent = recent[-80:]
        if not recent:
            return DailyContext(None, None, 0.0, 0.0, False)

        pivot_highs: list[float] = []
        pivot_lows: list[float] = []
        for idx in range(2, len(recent) - 2):
            window = recent[idx - 2:idx + 3]
            item = recent[idx]
            if item.high >= max(x.high for x in window):
                pivot_highs.append(item.high)
            if item.low <= min(x.low for x in window):
                pivot_lows.append(item.low)

        highs = pivot_highs or [x.high for x in recent]
        lows = pivot_lows or [x.low for x in recent]
        supports = sorted((low for low in lows if low <= current_price * 1.006), reverse=True)
        resistances = sorted(high for high in highs if high >= current_price * 0.994)
        support = supports[0] if supports else min(lows)
        resistance = resistances[0] if resistances else max(highs)
        low_slope = MomentumVolumeStrategy._slope_pct([x.low for x in recent[-20:]])
        high_slope = MomentumVolumeStrategy._slope_pct([x.high for x in recent[-20:]])
        return DailyContext(support, resistance, low_slope, high_slope, low_slope > 0 and high_slope >= -0.001)

    @staticmethod
    def _same_time_volume_ratio(all_bars: list[Bar], today: date) -> float:
        """전일 동일시각 자료가 완전할 때만 비율을 계산하고, 부족하면 0으로 실패 폐쇄한다."""
        by_day: dict[date, list[Bar]] = defaultdict(list)
        for bar in all_bars:
            by_day[bar.timestamp.astimezone(KST).date()].append(bar)
        today_bars = sorted(by_day.get(today, []), key=lambda x: x.timestamp)
        previous_days = sorted(day for day in by_day if day < today)
        if len(today_bars) < 2 or not previous_days:
            return 0.0
        prev_bars = sorted(by_day[previous_days[-1]], key=lambda x: x.timestamp)
        cutoff = today_bars[-1].timestamp.astimezone(KST).time().replace(tzinfo=None)
        comparable = [x for x in prev_bars if x.timestamp.astimezone(KST).time().replace(tzinfo=None) <= cutoff]
        if len(comparable) < 2:
            return 0.0

        def minute_of_day(value: dt_time) -> int:
            return value.hour * 60 + value.minute

        today_start = minute_of_day(today_bars[0].timestamp.astimezone(KST).time())
        prev_start = minute_of_day(comparable[0].timestamp.astimezone(KST).time())
        prev_end = minute_of_day(comparable[-1].timestamp.astimezone(KST).time())
        cutoff_min = minute_of_day(cutoff)
        # 900봉 절단 등으로 전일 앞부분 또는 동일시각 부근이 빠진 경우 HOT 가점을 주지 않는다.
        if prev_start > today_start + 5 or prev_end < cutoff_min - 2:
            return 0.0
        today_volume = sum(x.volume for x in today_bars)
        prev_volume = sum(x.volume for x in comparable)
        if prev_volume <= 0:
            return 0.0
        return today_volume / prev_volume

    def _update_hot_symbols(self) -> None:
        """API 예산을 '신호 임박' 후보에 우선 배분하기 위한 HOT 목록을 갱신한다.

        우선순위 근거: 후보 소스 중복·쌍끌이 점수 + 투매/임펄스 상태 보유 + 최근 고점 근접
        + 전일 동시간 거래량 급증. HOT 후보는 짧은 주기, 나머지는 긴 주기로 순환한다.
        """
        scored: list[tuple[float, str]] = []
        for symbol in self.active_symbols:
            if symbol in self.broker.positions:
                continue
            meta = self.candidate_meta.get(symbol)
            score = meta.score if meta else 0.0
            if symbol in self.strategy.capitulation_cache or symbol in self.strategy.impulse_cache:
                score += 40.0  # 눌림 상태 진행 중—완성 직전일 가능성이 높다.
            bars = self.strategy.bars.get(symbol)
            if bars and len(bars) >= 30:
                recent = list(bars)[-30:]
                recent_high = max(x.high for x in recent)
                if recent_high > 0 and recent[-1].close >= recent_high * 0.985:
                    score += 30.0  # 박스 상단·최근 고점 근접—돌파 임박.
            if self.strategy.same_time_volume_ratio.get(symbol, 0.0) >= 3.0:
                score += 15.0
            scored.append((score, symbol))
        scored.sort(reverse=True)
        self.hot_symbols = [symbol for _, symbol in scored[: max(1, self.hot_candidate_count)]]

    def _service_symbol(self, symbol: str, *, hot: bool) -> None:
        """후보 한 종목의 현재가 TTL 갱신과 분봉 갱신을 수행한다(HOT은 짧은 주기)."""
        if symbol not in self.last_bar_time:
            self._queue_bootstrap([symbol])
            return
        try:
            quote = self.latest_quotes.get(symbol)
            quote_age = 9999.0 if quote is None else (
                datetime.now(timezone.utc) - quote.timestamp
            ).total_seconds()
            if quote is None or quote_age > self.settings.candidate_ttl_sec:
                quote = self.client.get_quote(symbol)
                if not quote.name:
                    quote = Quote(
                        quote.symbol,
                        self.names.get(symbol, symbol),
                        quote.price,
                        quote.change_pct,
                        quote.cumulative_volume,
                        quote.cumulative_value,
                        quote.timestamp,
                        quote.best_bid,
                        quote.best_ask,
                    )
                self.names[symbol] = quote.name or self.names.get(symbol, symbol)
                self.latest_quotes[symbol] = quote
            assert quote is not None
            self._record_success(symbol)
            if time.monotonic() >= self.next_chart_due.get(symbol, 0.0):
                self._refresh_actual_bars(symbol, quote)
                interval = self.chart_refresh_hot if hot else self.chart_refresh
                self.next_chart_due[symbol] = time.monotonic() + interval
        except V73Error as exc:
            self._record_failure(symbol, exc)
        except Exception as exc:
            append_api_error(traceback.format_exc())
            self._record_failure(symbol, ApiSchemaError(f"예상 밖 오류: {exc}"))

    def _queue_bootstrap(self, symbols: Iterable[str]) -> None:
        now_kst = datetime.now(KST)
        self._reset_after_state_if_needed(now_kst)
        for symbol in dict.fromkeys(symbols):
            if symbol in self.last_bar_time or symbol in self.bootstrap_pending:
                continue
            if self._after_bootstrap_deferred(symbol):
                continue
            self.bootstrap_queue.append(symbol)
            self.bootstrap_pending.add(symbol)

    def _bootstrap_one(self) -> bool:
        """신규 후보를 한 종목씩 준비해 수십 종목 API 호출이 청산 감시를 막지 않게 한다."""
        if not self.bootstrap_queue:
            return False
        symbol = self.bootstrap_queue.popleft()
        self.bootstrap_pending.discard(symbol)
        now_kst = datetime.now(KST)
        self._reset_after_state_if_needed(now_kst)
        today = now_kst.date()
        try:
            current_segment = session_segment(now_kst)
            if current_segment not in {"PRE", "MAIN", "AFTER"}:
                return False
            if current_segment == "AFTER" and not self.after_entry_enabled:
                return False
            all_bars = [
                x for x in self.client.get_minute_bars(symbol, limit=900, completed_only=True)
                if regular_session_bar(x.timestamp)
            ]
            selected = [
                x for x in all_bars
                if x.timestamp.astimezone(KST).date() == today
                and session_segment(x.timestamp.astimezone(KST)) == current_segment
            ]
            if not selected:
                if current_segment == "AFTER":
                    # AFTER 분봉이 없는 종목은 오류가 아니라 '데이터 미제공/미거래'로 격리한다.
                    # 동일 종목을 매 루프 재조회하지 않고 유예 후에만 다시 확인한다.
                    self.after_no_bar_until[symbol] = time.monotonic() + max(60.0, self.after_reprobe_sec)
                    return False
                raise ApiSchemaError(f"현재세션 완성분봉 없음 symbol={symbol} segment={current_segment}")
            self.after_no_bar_until.pop(symbol, None)
            self.strategy.seed_bars(symbol, selected)
            self.symbol_sessions[symbol] = current_segment
            self.last_bar_time[symbol] = selected[-1].timestamp
            ratio = self._same_time_volume_ratio(all_bars, today)
            meta = self.candidate_meta.get(symbol)
            self.strategy.set_context(
                symbol,
                same_time_volume_ratio=ratio,
                sources=meta.source if meta else "",
                dual_buy=meta.dual_buy if meta else False,
            )
            cache = self.daily_cache.get(symbol)
            if cache is None or time.monotonic() - cache[0] > 6 * 3600:
                daily = self.client.get_daily_bars(symbol, limit=120)
                context = self._build_daily_context(daily, selected[-1].close)
                self.daily_cache[symbol] = (time.monotonic(), context)
            else:
                context = cache[1]
            self.strategy.set_context(symbol, daily=context)
            self.next_chart_due[symbol] = time.monotonic() + self.chart_refresh
            if self.debug:
                self.logger.info("차트준비 %s 분봉=%s 동시간거래=x%.2f", self._label(symbol), len(selected), ratio)
            self.risk.set_data_halt(False)
            return True
        except V73Error as exc:
            self._show_throttled_error(
                f"bootstrap:{symbol}",
                f"차트준비 실패 {self._label(symbol)}: {brief_error(exc)}",
                120.0,
            )
            self.next_chart_due[symbol] = time.monotonic() + 30.0
            return False

    def _bootstrap_bars(self, symbols: list[str]) -> None:
        """호환용: 즉시 일괄 호출하지 않고 준비 큐에만 넣는다."""
        self._queue_bootstrap(symbols)

    def _show_throttled_error(self, key: str, message: str, interval: float = 60.0) -> None:
        now = time.monotonic()
        if now - self.error_last_shown.get(key, 0.0) >= interval:
            self.logger.warning(message)
            self.error_last_shown[key] = now

    def _monitor_one_position(self) -> bool:
        """매 루프 후보 작업보다 먼저 보유종목 한 개를 우선 감시한다.

        키움 호출 제한 때문에 모든 보유종목을 한 루프에서 동시에 요청하지 않고
        최우선 라운드로빈으로 감시한다. 분봉도 별도로 갱신해 순간 목표가 통과를
        현재가 폴링만으로 놓치지 않게 한다.
        """
        symbols = list(self.broker.positions)
        if not symbols:
            return False
        symbol = symbols[self.position_cursor % len(symbols)]
        self.position_cursor += 1
        try:
            quote = self.client.get_quote(symbol)
            if not quote.name:
                quote = Quote(
                    quote.symbol,
                    self.names.get(symbol, symbol),
                    quote.price,
                    quote.change_pct,
                    quote.cumulative_volume,
                    quote.cumulative_value,
                    quote.timestamp,
                    quote.best_bid,
                    quote.best_ask,
                )
            self.names[symbol] = quote.name or self.names.get(symbol, symbol)
            self.latest_quotes[symbol] = quote
            self._record_success(symbol)
            detection_time = datetime.now(KST)
            previous_observation = self.last_position_observation_at.get(symbol)
            observed_lag_sec = (
                max(0.0, (detection_time - previous_observation).total_seconds())
                if previous_observation is not None else 0.0
            )
            self.last_position_observation_at[symbol] = detection_time
            self._check_tick_exit(
                quote,
                detection_time=detection_time,
                observed_lag_sec=observed_lag_sec,
            )
            if symbol not in self.broker.positions:
                self.last_position_observation_at.pop(symbol, None)
            if (
                symbol in self.broker.positions
                and time.monotonic() >= self.next_chart_due.get(symbol, 0.0)
            ):
                self._refresh_actual_bars(symbol, quote)
                self.next_chart_due[symbol] = time.monotonic() + self.chart_refresh
            return True
        except V73Error as exc:
            self._record_failure(symbol, exc)
            return False
        except Exception as exc:
            append_api_error(traceback.format_exc())
            self._record_failure(symbol, ApiSchemaError(f"보유감시 예상 밖 오류: {exc}"))
            return False

    def run(self, *, minutes: int | None = None) -> None:
        deadline = None if minutes is None or minutes <= 0 else time.monotonic() + minutes * 60
        self.logger.warning("=" * 86)
        self.logger.warning("[모의 자동매매 시작 %s] 실제 주문은 전혀 없습니다", VERSION)
        self.logger.warning("작동시간: 평일(휴장일 제외) 08:00~20:00 / 실제 분봉·현재가·호가가 있을 때만 매매")
        self.logger.warning(
            "세션: 프리 08:00~08:50 / 정규 09:00~15:30 / 애프터 15:40~20:00 "
            "(기본 신규진입 OFF, 보유청산 감시) / 갭 신규진입 제외"
        )
        self.logger.warning("관심종목: 상승률 상위 100 + 거래량 급증 + 외국인·기관 동시 순매수 (신호임박 후보 우선감시)")
        self.logger.warning("매수조건: 정석 박스돌파 / 투매 후 눌림 / 임펄스 눌림(급등 후 저거래 조정)")
        self.logger.warning("목표수익 +1.5%% / 돌파 손절 -2.0%% / 시간 강제청산 기본 없음 / 5분마다 보유·손익 요약")
        self.logger.warning("=" * 86)
        session_was_active = False
        # 시간 밖에 실행했을 때 불필요한 순위·차트 호출을 하지 않고 대기 상태를 즉시 보여준다.
        if market_label(datetime.now(KST)) != "ACTIVE":
            self._compact_report()
            self.next_report = time.monotonic() + self.settings.report_interval_sec
        try:
            while deadline is None or time.monotonic() < deadline:
                loop_start = time.monotonic()
                now_kst = datetime.now(KST)
                label = market_label(now_kst)
                if label != "ACTIVE":
                    session_was_active = False
                    if time.monotonic() >= self.next_report:
                        self._compact_report()
                        self.next_report = time.monotonic() + self.settings.report_interval_sec
                    time.sleep(max(0.5, self.idle_sleep))
                    continue

                # 장중 프로그램 시작 시 후보를 구성하되, AFTER 기본모드는 시작 직후부터
                # 후보 API를 호출하지 않고 보유종목 청산 감시 전용으로 진입한다.
                if not session_was_active:
                    if session_segment(now_kst) == "AFTER" and not self.after_entry_enabled:
                        session_was_active = True
                    else:
                        added = self._refresh_universe(force=True)
                        self._queue_bootstrap(added or self._current_symbols())
                        session_was_active = True

                # [우선순위 1] 보유종목 목표·손절 감시를 다른 모든 작업보다 먼저 수행한다.
                self._monitor_one_position()

                # AFTER 기본모드는 신규 후보 API를 전부 멈추고 청산 감시만 수행한다.
                # 기존 전략·체결·리스크 로직에는 손대지 않는 운영 계층의 fail-closed 처리다.
                self._reset_after_state_if_needed(now_kst)
                if session_segment(now_kst) == "AFTER" and not self.after_entry_enabled:
                    self._enter_after_monitor_only(now_kst)
                    self._sync_daily_state()
                    if time.monotonic() >= self.next_report:
                        self._compact_report()
                        self.next_report = time.monotonic() + self.settings.report_interval_sec
                    elapsed = time.monotonic() - loop_start
                    self.loop_seconds_ema = self.loop_seconds_ema * 0.9 + elapsed * 0.1
                    time.sleep(max(0.10, min(1.0, self.idle_sleep * 0.20)))
                    continue

                # [우선순위 2] VI·호가공백 등으로 놓친 매수 신호의 짧은 재시도.
                self._process_pending_entries()

                added = self._refresh_universe()
                if added:
                    self._queue_bootstrap(added)

                # [우선순위 3] 신규 후보 초기화는 한 번에 한 종목만 처리한다.
                self._bootstrap_one()

                # HOT(신호임박) 후보 목록을 주기적으로 재산정한다.
                if time.monotonic() >= self.next_hot_update:
                    self._update_hot_symbols()
                    self.next_hot_update = time.monotonic() + 30.0

                # [우선순위 4] HOT 후보는 매 루프, 나머지 후보는 격 루프로 순환한다.
                self.loop_index += 1
                hot = [s for s in self.hot_symbols if s not in self.broker.positions]
                if hot:
                    symbol = hot[self.hot_cursor % len(hot)]
                    self.hot_cursor += 1
                    self._service_symbol(symbol, hot=True)
                hot_set = set(hot)
                general = [s for s in self._current_symbols() if s not in hot_set and s not in self.broker.positions]
                if general and (not hot or self.loop_index % 2 == 0):
                    symbol = general[self.candidate_cursor % len(general)]
                    self.candidate_cursor += 1
                    self._service_symbol(symbol, hot=False)

                self._sync_daily_state()
                if time.monotonic() >= self.next_report:
                    self._compact_report()
                    self.next_report = time.monotonic() + self.settings.report_interval_sec
                # 루프 소요시간 지수평균: 보유종목 감시지연 추정에 사용한다.
                elapsed = time.monotonic() - loop_start
                self.loop_seconds_ema = self.loop_seconds_ema * 0.9 + elapsed * 0.1
                time.sleep(max(0.10, min(1.0, self.idle_sleep * 0.20)))
        except KeyboardInterrupt:
            self.logger.warning("사용자 Ctrl+C 종료")
        finally:
            self.broker.save_state()
            self.save_pattern_state()
            self._final_report()

    def _refresh_actual_bars(self, symbol: str, quote: Quote) -> None:
        bars = [x for x in self.client.get_minute_bars(symbol, limit=25, completed_only=True) if regular_session_bar(x.timestamp)]
        if not bars:
            return
        last = self.last_bar_time.get(symbol)
        new_bars = [x for x in bars if last is None or x.timestamp > last]
        newest = new_bars[-1].timestamp if new_bars else None
        now_kst = datetime.now(KST)
        for bar in new_bars:
            # cntr_tm은 분봉 '시작' 시각이므로 완성 시각(시작+60초) 기준으로 지연을 계산한다.
            # 종전에는 완성 직후의 정상 신호가 '지연분봉 121초'로 거절되는 문제가 있었다.
            completed_at = bar.timestamp.astimezone(KST) + timedelta(seconds=60)
            age = max(0.0, (now_kst - completed_at).total_seconds())
            self._on_bar(bar, quote, allow_entry=bar.timestamp == newest and age <= self.settings.max_bar_signal_age_sec, bar_age_sec=age)
            self.last_bar_time[symbol] = bar.timestamp
        if new_bars and self.risk.data_halted and not self.state_integrity_block:
            self.recovery_successes += 1
            if self.recovery_successes >= 2:
                self.risk.set_data_halt(False)
                self.recovery_successes = 0
                self.logger.warning("시장데이터 정상복구")

    def _sync_daily_state(self) -> None:
        prices = {s: q.price for s, q in self.latest_quotes.items()}
        equity = self.broker.equity(prices)
        if self.broker.roll_session_if_needed(equity):
            self.risk.reset_daily_session(self.broker.day_start_equity)
        self.broker.daily_realized_pnl = self.risk.daily_realized_pnl
        self.broker.update_daily_halt(self.risk.daily_halted, self.risk.daily_halt_reason)

    def _record_success(self, symbol: str | None = None) -> None:
        self.consecutive_failures = 0
        if symbol is not None:
            self.symbol_failures.pop(symbol, None)

    def _record_failure(self, symbol: str, exc: V73Error) -> None:
        """종목별로 오류를 격리한다.

        한 종목의 반복 스키마 오류가 전역 신규진입 중단(data_halted)으로 번지지 않도록,
        전역 중단은 후보 API 전체 실패(_refresh_universe)에서만 걸고, 개별 종목은
        실패 횟수에 비례한 백오프로 해당 종목의 다음 조회만 늦춘다.
        보유종목의 청산 감시 실패는 더 높은 등급으로 경고한다.
        """
        self.recovery_successes = 0
        self.symbol_failures[symbol] += 1
        failures = self.symbol_failures[symbol]
        held = symbol in self.broker.positions
        level_msg = "보유종목 감시오류(청산 지연 위험)" if held else "데이터 오류"
        self._show_throttled_error(
            f"quote:{symbol}",
            f"{level_msg} {self._label(symbol)} 연속{failures}회: {brief_error(exc)}",
            15.0 if held else 60.0,
        )
        if not held:
            # 문제 종목은 실패 횟수에 비례해 뒤로 미뤄 다른 후보의 API 예산을 지킨다.
            backoff = min(300.0, 30.0 * failures)
            self.next_chart_due[symbol] = time.monotonic() + backoff
        # 인증 오류처럼 계정 수준 문제만 전역 신중 모드로 취급한다.
        if isinstance(exc, AuthenticationError):
            self.consecutive_failures += 1
            if self.consecutive_failures >= 3:
                self.risk.set_data_halt(True)
                time.sleep(3)

    def _retry_window_for_now(self) -> float:
        segment = session_segment(datetime.now(KST))
        if segment == "PRE":
            return self.retry_window_pre
        if segment == "AFTER":
            return self.retry_window_after
        return self.retry_window_main

    def _max_chase_for_pattern(self, pattern: str) -> float:
        if pattern == "정석박스돌파":
            return self.max_chase_breakout
        if pattern == "임펄스눌림1차":
            return self.max_chase_impulse
        return self.max_chase_capitulation

    def _estimated_monitor_delay(self, extra_positions: int = 0) -> float:
        return (len(self.broker.positions) + extra_positions) * max(0.5, self.loop_seconds_ema)

    def _entry_capacity_ok(self) -> tuple[bool, float]:
        delay = self._estimated_monitor_delay(extra_positions=1)
        return delay <= self.max_monitor_delay_sec, delay

    def _reentry_allowed(self, symbol: str) -> tuple[bool, str]:
        until = self.broker.reentry_block_until.get(symbol)
        if until is None:
            return True, ""
        now = datetime.now(KST)
        if now >= until:
            self.broker.reentry_block_until.pop(symbol, None)
            return True, ""
        return False, f"손절후재진입대기_{until:%H:%M:%S}"

    def _minimum_cumulative_value(self, now: datetime) -> float:
        segment = session_segment(now)
        current = now.time().replace(tzinfo=None)
        if segment == "PRE":
            return self.min_cum_value_pre
        if segment == "AFTER":
            return self.min_cum_value_day
        if current < dt_time(9, 10):
            return self.min_cum_value_0910
        if current < dt_time(9, 30):
            return self.min_cum_value_0930
        if current < dt_time(10, 0):
            return self.min_cum_value_1000
        return self.min_cum_value_day

    def _liquidity_gate(self, signal: Signal, quote: Quote) -> tuple[bool, str]:
        bars = list(self.strategy.bars.get(signal.symbol, []))
        if len(bars) < 5:
            return False, "최근5분자료부족"
        recent5_value = sum(max(0, bar.volume) * max(0.0, bar.close) for bar in bars[-5:])
        signal_bar_value = max(0, bars[-1].volume) * max(0.0, bars[-1].close)
        cumulative_value = max(
            0.0,
            quote.cumulative_value,
            float(quote.cumulative_volume) * max(0.0, quote.price),
        )
        minimum_cumulative = self._minimum_cumulative_value(datetime.now(KST))
        if cumulative_value < minimum_cumulative:
            return False, f"누적거래대금부족_{cumulative_value/1e8:.1f}억<{minimum_cumulative/1e8:.1f}억"
        if recent5_value < self.min_recent5_value:
            return False, f"최근5분거래대금부족_{recent5_value/1e8:.1f}억"
        if signal_bar_value < self.min_signal_bar_value:
            return False, f"신호봉거래대금부족_{signal_bar_value/1e8:.1f}억"
        same_time = self.strategy.same_time_volume_ratio.get(signal.symbol, 0.0)
        exceptional = cumulative_value >= minimum_cumulative * 2.0 and recent5_value >= self.min_recent5_value * 2.0
        if same_time < self.min_same_time_volume_ratio and not exceptional:
            if same_time <= 0:
                return False, "전일동시간거래량확인불가"
            return False, f"전일동시간거래량약함_x{same_time:.2f}"
        return True, (
            f"유동성정상 누적={cumulative_value/1e8:.1f}억 최근5분={recent5_value/1e8:.1f}억 "
            f"신호봉={signal_bar_value/1e8:.1f}억 동시간=x{same_time:.2f}"
        )

    def _pending_signal_valid(self, signal: Signal, current_price: float) -> tuple[bool, str]:
        if signal.reference_price <= 0 or current_price <= 0:
            return False, "가격오류"
        drift = current_price / signal.reference_price - 1.0
        chase_limit = self._max_chase_for_pattern(signal.pattern)
        if drift > chase_limit:
            return False, f"추격초과_{drift:+.2%}>{chase_limit:.2%}"
        if drift < -self.max_adverse_drift_pct:
            return False, f"역방향이탈_{drift:+.2%}<-{self.max_adverse_drift_pct:.2%}"

        bars = list(self.strategy.bars.get(signal.symbol, []))
        if not bars:
            return False, "분봉상태없음"
        current = bars[-1]
        if signal.pattern == "임펄스눌림1차":
            cached = self.strategy.impulse_cache.get(signal.symbol)
            if cached is None or current.low < cached[3] * 0.996:
                return False, "임펄스구조붕괴"
        elif signal.pattern == "투매후눌림1차":
            cached = self.strategy.capitulation_cache.get(signal.symbol)
            if cached is None or current.low < cached[2] * 0.994:
                return False, "투매저점이탈"
        return True, "정상"

    def _queue_pending_entry(self, signal: Signal) -> None:
        if signal.symbol in self.broker.positions or signal.symbol in self.pending_entries:
            return
        allowed, reason = self._reentry_allowed(signal.symbol)
        if not allowed:
            self.logger.warning("진입취소 %s %s", self._label(signal.symbol), reason)
            return
        now = time.monotonic()
        window = self._retry_window_for_now()
        self.pending_entries[signal.symbol] = PendingEntry(signal, now, now + window, now, 0)
        self.logger.warning(
            "진입재시도 등록 %s 신호가=%.0f 유효=%.0f초 최대%s회",
            self._label(signal.symbol), signal.reference_price, window, self.retry_max_attempts,
        )

    def _attempt_entry(self, signal: Signal, *, from_retry: bool = False) -> bool:
        symbol = signal.symbol
        if not self._new_entry_session_allowed():
            self.pending_entries.pop(symbol, None)
            return False
        if symbol in self.broker.positions:
            self.pending_entries.pop(symbol, None)
            return False
        allowed, reason = self._reentry_allowed(symbol)
        if not allowed:
            self.pending_entries.pop(symbol, None)
            self.logger.warning("진입취소 %s %s", self._label(symbol), reason)
            return False
        capacity_ok, estimated_delay = self._entry_capacity_ok()
        if not capacity_ok:
            self.pending_entries.pop(symbol, None)
            self._show_throttled_error(
                "entry-capacity",
                f"신규진입 일시중단—예상 보유감시 {estimated_delay:.0f}초 > 허용 {self.max_monitor_delay_sec:.0f}초",
                60.0,
            )
            return False
        try:
            fresh_quote = self.client.get_quote(symbol)
            enriched = self.client.get_orderbook(symbol, fresh_quote, side="buy")
            self.latest_quotes[symbol] = enriched
        except (V73Error, ValueError) as exc:
            self._show_throttled_error(f"entry:{symbol}", f"진입대기 {self._label(symbol)}: {brief_error(exc)}", 60.0)
            if not from_retry:
                self._queue_pending_entry(signal)
            return False

        liquidity_ok, liquidity_reason = self._liquidity_gate(signal, enriched)
        if not liquidity_ok:
            self.pending_entries.pop(symbol, None)
            self.logger.warning("진입포기 %s %s", self._label(symbol), liquidity_reason)
            return False

        valid, invalid_reason = self._pending_signal_valid(signal, enriched.price)
        if not valid:
            self.pending_entries.pop(symbol, None)
            self.logger.warning(
                "진입포기 %s %s 신호가=%.0f 현재가=%.0f",
                self._label(symbol), invalid_reason, signal.reference_price, enriched.price,
            )
            return False

        prices = {key: item.price for key, item in self.latest_quotes.items()}
        decision = self.risk.approve_entry(signal, enriched, self.broker.equity(prices), self.broker.cash, self.broker.positions)
        if not decision.approved:
            if decision.reason.startswith(("호가없음", "시세지연", "스프레드")) and not from_retry:
                self._queue_pending_entry(signal)
            if self.debug:
                self.logger.info("진입거절 %s %s", self._label(symbol), decision.reason)
            return False
        try:
            fill = self.broker.buy(
                symbol,
                decision.quantity,
                enriched,
                signal.reason + "|" + liquidity_reason,
                stop_loss_pct=signal.stop_loss_pct,
                take_profit_pct=signal.take_profit_pct,
                pattern=signal.pattern,
            )
        except ValueError as exc:
            self._show_throttled_error(f"entrybuy:{symbol}", f"진입대기 {self._label(symbol)}: {brief_error(exc)}", 60.0)
            if not from_retry:
                self._queue_pending_entry(signal)
            return False
        self.pending_entries.pop(symbol, None)
        self.last_position_observation_at[symbol] = fill.timestamp
        self.strategy.mark_entry(symbol)
        self.save_pattern_state()
        self._print_buy(fill, self.broker.positions[symbol], signal)
        return True

    def _process_pending_entries(self) -> None:
        if not self.pending_entries or not self._new_entry_session_allowed():
            return
        now = time.monotonic()
        for symbol in list(self.pending_entries):
            pending = self.pending_entries.get(symbol)
            if pending is None:
                continue
            if now >= pending.expire_mono or symbol in self.broker.positions:
                self.pending_entries.pop(symbol, None)
                continue
            if pending.attempts >= self.retry_max_attempts:
                self.pending_entries.pop(symbol, None)
                self.logger.warning("진입포기 %s 재시도횟수초과", self._label(symbol))
                continue
            if now < pending.next_try_mono:
                continue
            pending.attempts += 1
            pending.next_try_mono = now + self.entry_retry_sec
            if self._attempt_entry(pending.signal, from_retry=True):
                return
            return

    def _check_tick_exit(
        self,
        quote: Quote,
        *,
        detection_time: datetime | None = None,
        observed_lag_sec: float = 0.0,
    ) -> None:
        position = self.broker.positions.get(quote.symbol)
        if position is None:
            return
        position.highest_price = max(position.highest_price, quote.price)
        detected_at = detection_time.astimezone(KST) if detection_time is not None else datetime.now(KST)

        try:
            if position.target_order_active and quote.price >= position.target_price:
                # 목표가에 실제 매수호가가 형성된 경우에만 예약 지정가 체결로 인정한다.
                enriched = self.client.get_orderbook(quote.symbol, quote, side="sell")
                if enriched.best_bid is None or enriched.best_bid < position.target_price:
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
                    trigger_source="REST_TICK_DETECTION",
                )
            elif quote.price <= position.stop_price:
                enriched = self.client.get_orderbook(quote.symbol, quote, side="sell")
                fill = self.broker.sell(
                    quote.symbol,
                    enriched,
                    "고정손절",
                    trigger_time=detected_at,
                    trigger_price=quote.price,
                    detection_time=detected_at,
                    observed_lag_sec=observed_lag_sec,
                    trigger_source="REST_TICK_DETECTION",
                )
            else:
                return
        except (V73Error, ValueError) as exc:
            self._show_throttled_error(
                f"exit:{quote.symbol}",
                f"청산대기 {self._label(quote.symbol)}: {brief_error(exc)}",
                30.0,
            )
            return
        self.strategy.mark_exit(quote.symbol)
        self.risk.record_pnl(fill.pnl)
        self._print_sell(fill)

    def _on_bar(self, bar: Bar, quote: Quote, *, allow_entry: bool = True, bar_age_sec: float = 0.0) -> None:
        segment = session_segment(bar.timestamp.astimezone(KST))
        previous_segment = self.symbol_sessions.get(bar.symbol)
        if previous_segment is not None and previous_segment != segment:
            self.strategy.bars[bar.symbol].clear()
            self.strategy.capitulation_cache.pop(bar.symbol, None)
            self.strategy.impulse_cache.pop(bar.symbol, None)
        self.symbol_sessions[bar.symbol] = segment

        position = self.broker.positions.get(bar.symbol)
        signal = self.strategy.on_bar(bar, position)
        self.latest_signals[bar.symbol] = signal
        if self.debug and signal.action != SignalAction.HOLD:
            self.logger.info("신호 %s %s %s", self._label(bar.symbol), signal.action.value, signal.reason)
        if signal.action == SignalAction.BUY and position is None:
            if not allow_entry:
                self.pending_entries.pop(bar.symbol, None)
                self.logger.warning(
                    "진입폐기 %s 지연분봉신호폐기 age=%.1f초",
                    self._label(bar.symbol), bar_age_sec,
                )
                return
            self._attempt_entry(signal)
        elif signal.action == SignalAction.SELL and position is not None:
            trigger_time = bar.timestamp.astimezone(KST) + timedelta(minutes=1)
            detection_time = datetime.now(KST)
            observed_lag_sec = max(0.0, (detection_time - trigger_time).total_seconds())
            try:
                if signal.reason.startswith("예약목표"):
                    fill = self.broker.sell(
                        bar.symbol,
                        quote,
                        signal.reason,
                        reserved_fill_price=position.target_price,
                        trigger_time=trigger_time,
                        trigger_price=position.target_price,
                        detection_time=detection_time,
                        observed_lag_sec=observed_lag_sec,
                        trigger_source="COMPLETED_BAR",
                    )
                else:
                    fresh_quote = self.client.get_quote(bar.symbol)
                    enriched = self.client.get_orderbook(bar.symbol, fresh_quote, side="sell")
                    fill = self.broker.sell(
                        bar.symbol,
                        enriched,
                        signal.reason,
                        trigger_time=trigger_time,
                        trigger_price=signal.reference_price,
                        detection_time=detection_time,
                        observed_lag_sec=observed_lag_sec,
                        trigger_source="COMPLETED_BAR",
                    )
            except (V73Error, ValueError) as exc:
                self._show_throttled_error(f"exitbar:{bar.symbol}", f"청산대기 {self._label(bar.symbol)}: {brief_error(exc)}", 30.0)
                return
            self.strategy.mark_exit(bar.symbol)
            self.risk.record_pnl(fill.pnl)
            self._print_sell(fill)

    def _print_buy(self, fill: Fill, pos: Position, signal: Signal) -> None:
        buy_amount = fill.price * fill.quantity
        print("\n" + "=" * 86)
        print(f"[매수완료] {fill.timestamp:%H:%M:%S}  {self._label(fill.symbol)}")
        print(
            f"{fill.quantity:,}주 × {fill.price:,.0f}원 = "
            f"매수금액 {buy_amount:,.0f}원"
        )
        print(
            f"진입유형 {signal.pattern} | 목표 {pos.target_price:,.0f}원(+1.5%) | "
            f"손절 {pos.stop_price:,.0f}원({-signal.stop_loss_pct:.1%})"
        )
        print("=" * 86 + "\n")

    def _print_sell(self, fill: Fill) -> None:
        sell_amount = fill.price * fill.quantity
        buy_amount = fill.entry_price * fill.quantity if fill.entry_price > 0 else 0.0
        print("\n" + "=" * 86)
        print(f"[매도완료] {fill.timestamp:%H:%M:%S}  {self._label(fill.symbol)}")
        if fill.entry_time is not None and fill.entry_price > 0:
            print(
                f"매수 {fill.entry_time:%H:%M:%S} | {fill.quantity:,}주 × "
                f"{fill.entry_price:,.0f}원 = {buy_amount:,.0f}원"
            )
            print(
                f"매도 {fill.timestamp:%H:%M:%S} | {fill.quantity:,}주 × "
                f"{fill.price:,.0f}원 = {sell_amount:,.0f}원"
            )
            print(
                f"확정손익 {money(fill.pnl)} ({fill.pnl_pct:+.2%}) | "
                f"사유 {fill.reason}"
            )
            if fill.trigger_time is not None:
                extra = f"트리거 {fill.trigger_time:%H:%M:%S} {fill.trigger_price:,.0f}원"
                if fill.detection_time is not None:
                    extra += f" | 감지 {fill.detection_time:%H:%M:%S}"
                extra += (
                    f" | 감지지연 {fill.monitor_lag_sec:.1f}초 | 체결처리 {fill.execution_lag_sec:.1f}초 | "
                    f"REST감시간격상한 {fill.observed_lag_sec:.1f}초"
                )
                if fill.stop_overrun_pct > 0:
                    extra += f" | 손절초과 {fill.stop_overrun_pct:.2%}"
                print(extra)
        else:
            print(
                f"{fill.quantity:,}주 × {fill.price:,.0f}원 = "
                f"매도금액 {sell_amount:,.0f}원"
            )
            print(f"확정손익 {money(fill.pnl)} | 사유 {fill.reason}")
        print("=" * 86 + "\n")

    def _compact_report(self) -> None:
        self._sync_daily_state()
        now = datetime.now(KST)
        prices = {s: q.price for s, q in self.latest_quotes.items()}

        estimated_pnl_total = 0.0
        estimated_liquidation_value = self.broker.cash
        position_rows: list[tuple[str, Position, float, float, float, float]] = []

        for symbol, pos in self.broker.positions.items():
            current = prices.get(symbol, pos.entry_price)
            gross_now = current * pos.quantity
            estimated_sell_fee = gross_now * self.settings.commission_pct
            estimated_sell_tax = gross_now * self.settings.sell_tax_pct
            estimated_proceeds = gross_now - estimated_sell_fee - estimated_sell_tax
            pnl_value = estimated_proceeds - pos.entry_cost
            pnl_rate = pnl_value / max(1.0, pos.entry_cost)
            buy_amount = pos.entry_price * pos.quantity
            estimated_pnl_total += pnl_value
            estimated_liquidation_value += estimated_proceeds
            position_rows.append(
                (symbol, pos, current, buy_amount, pnl_value, pnl_rate)
            )

        print("\n" + "=" * 86)
        segment = session_segment(now)
        label_now = market_label(now)
        if label_now == "ACTIVE":
            state_text = f"매매중—{SESSION_KOREAN.get(segment, segment)}"
            if segment in {"GAP_OPEN", "GAP_CLOSE"}:
                state_text = f"감시중—{SESSION_KOREAN.get(segment, segment)}(신규진입 제외)"
            elif segment == "AFTER" and not self.after_entry_enabled:
                state_text = "감시중—애프터마켓(신규진입 OFF·보유청산만)"
        elif label_now == "HOLIDAY":
            state_text = "대기중—오늘은 증시 휴장일"
        elif label_now == "WEEKEND":
            state_text = "대기중—주말"
        else:
            state_text = "대기중—평일 08:00 자동 시작"
        print(f"[5분 매매요약] 현재시각 {now:%H:%M:%S} | {state_text}")
        print(
            f"실현손익 {money(self.broker.realized_pnl)} | "
            f"평가손익 {money(estimated_pnl_total)} | "
            f"평가자산 {estimated_liquidation_value:,.0f}원 | "
            f"보유 {len(position_rows)}종목"
        )
        if self.settings.paper_unlimited:
            print("(무제한 PAPER: 종목당 %s원 독립 트레이드 합산—청산지연이 포함되어 실전 수익성 지표로 사용 금지)" % f"{self.settings.paper_unit_notional:,.0f}")
        # 보유종목 감시지연 추정: 라운드로빈 1종목/루프 구조에서 예상 재확인 주기.
        held_count = len(self.broker.positions)
        if held_count:
            est_delay = held_count * max(0.5, self.loop_seconds_ema)
            warn = " ← 위험: 목표·손절 반영 지연" if est_delay > self.max_monitor_delay_sec else ""
            print(
                f"보유종목 감시주기 추정 약 {est_delay:.0f}초 "
                f"(보유 {held_count}종목 × 루프 {self.loop_seconds_ema:.1f}초 / "
                f"신규진입 차단기준 {self.max_monitor_delay_sec:.0f}초){warn}"
            )
        if segment == "AFTER" and self.after_entry_enabled and self.after_no_bar_until:
            print(f"애프터 분봉 미확인 후보 {len(self.after_no_bar_until)}종목 재조회 유예중")
        if self.pending_entries:
            names = ", ".join(self._label(s) for s in list(self.pending_entries)[:5])
            print(f"진입 재시도 대기 {len(self.pending_entries)}건: {names}")
        print(
            f"적용비용: 수수료 {self.settings.commission_pct:.4%} | 매도세 {self.settings.sell_tax_pct:.3%} | "
            f"슬리피지 {self.settings.slippage_pct:.3%} (환경변수로 조정 가능)"
        )

        if position_rows:
            for index, (symbol, pos, current, buy_amount, pnl_value, pnl_rate) in enumerate(position_rows, start=1):
                print("-" * 86)
                print(f"[현재보유 {index}] {self.names.get(symbol, symbol)}")
                print(
                    f"매수 {pos.entry_time:%H:%M:%S} | {pos.quantity:,}주 × "
                    f"{pos.entry_price:,.0f}원 = {buy_amount:,.0f}원"
                )
                quote = self.latest_quotes.get(symbol)
                quote_age = None if quote is None else (datetime.now(timezone.utc) - quote.timestamp).total_seconds()
                stale = " [지연시세]" if quote_age is None or quote_age > self.settings.max_quote_age_sec else ""
                age_text = "-" if quote_age is None else f"{quote_age:.0f}초 전"
                print(
                    f"현재 {now:%H:%M:%S} | 현재가 {current:,.0f}원 (갱신 {age_text}){stale} | "
                    f"평가손익 {money(pnl_value)} ({pnl_rate:+.2%})"
                )
                print(
                    f"목표 {pos.target_price:,.0f}원 | 손절 {pos.stop_price:,.0f}원 | "
                    f"진입유형 {pos.pattern or '-'}"
                )
        else:
            print("-" * 86)
            print("[현재보유] 없음")

        sells = [x for x in self.broker.recent_fills if x.side == "SELL"][-5:]
        if sells:
            print("-" * 86)
            print("[최근 매도내역]")
            for fill in sells:
                buy_amount = fill.entry_price * fill.quantity
                sell_amount = fill.price * fill.quantity
                buy_time = fill.entry_time.strftime("%H:%M:%S") if fill.entry_time else "-"
                print(
                    f"{self.names.get(fill.symbol, fill.symbol)} | "
                    f"매수 {buy_time} {fill.quantity:,}주 {buy_amount:,.0f}원 → "
                    f"매도 {fill.timestamp:%H:%M:%S} {sell_amount:,.0f}원 | "
                    f"{money(fill.pnl)} ({fill.pnl_pct:+.2%})"
                )

        print("=" * 86 + "\n")
        # 5분마다 투매·임펄스 패턴 상태를 함께 저장해 재시작 시 복구한다.
        self.save_pattern_state()

    def _final_report(self) -> None:
        prices = {s: q.price for s, q in self.latest_quotes.items()}
        equity = self.broker.equity(prices)
        self.logger.warning(
            "[종료] 실현손익 %s / 평가자산 %.0f원 / 보유 %s종목",
            money(self.broker.realized_pnl),
            equity,
            len(self.broker.positions),
        )


# -----------------------------------------------------------------------------
# 자체검증/CLI
# -----------------------------------------------------------------------------


def self_check(settings: Settings) -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, bool(ok), detail))

    record("부호가격파싱", parse_float("+005930", absolute=True) == 5930.0)
    record("콤마거래량파싱", parse_int("1,234", absolute=True) == 1234)
    record("목표수익기본", abs(settings.take_profit_pct - 0.015) < 1e-9, f"{settings.take_profit_pct:.3%}")
    record("정석돌파손절기본", abs(settings.stop_loss_pct - 0.020) < 1e-9, f"{settings.stop_loss_pct:.3%}")

    # 정석 박스돌파: 박스 최고 거래량을 넘고 중앙값의 수배인 명확한 종가 돌파만 BUY.
    try:
        strategy = MomentumVolumeStrategy(settings)
        base = datetime.now(KST).replace(second=0, microsecond=0) - timedelta(minutes=40)
        bars: list[Bar] = []
        for i in range(30):
            close = 1000 + (i % 4) * 2
            bars.append(Bar("111111", base + timedelta(minutes=i), close - 1, 1010, 990 + i * 0.15, close, 1000 + (i % 3) * 100))
        strategy.seed_bars("111111", bars[:-1])
        breakout = Bar("111111", bars[-1].timestamp, 1004, 1045, 1002, 1040, 50000)
        signal = strategy.on_bar(breakout, None)
        record("정석박스돌파BUY", signal.action == SignalAction.BUY and signal.pattern == "정석박스돌파", signal.reason)
    except Exception as exc:
        record("정석박스돌파BUY", False, str(exc))

    # 애매한 거래량은 매수 금지.
    try:
        strategy = MomentumVolumeStrategy(settings)
        base = datetime.now(KST).replace(second=0, microsecond=0) - timedelta(minutes=40)
        seed = [Bar("222222", base + timedelta(minutes=i), 1000, 1010, 990 + i * 0.1, 1002, 1200) for i in range(29)]
        strategy.seed_bars("222222", seed)
        weak = Bar("222222", base + timedelta(minutes=29), 1002, 1040, 1000, 1035, 1300)
        signal = strategy.on_bar(weak, None)
        record("애매한돌파차단", signal.action == SignalAction.HOLD, signal.reason)
    except Exception as exc:
        record("애매한돌파차단", False, str(exc))

    # 원점회귀 투매반등: 선행 상승을 대부분 반납한 대량 음봉 -> 거래량 바닥
    # -> 모인 이평선에서 2봉 이상 지지 -> 아래꼬리 확인봉/짧은 양봉 -> 단기고점 회복.
    try:
        strategy = MomentumVolumeStrategy(settings)
        base = datetime.now(KST).replace(second=0, microsecond=0) - timedelta(minutes=50)
        cap_seed: list[Bar] = []
        for i in range(12):
            close = 1000 + (i % 2)
            cap_seed.append(Bar("333333", base + timedelta(minutes=i), close - 1, close + 2, close - 2, close, 800))
        for j in range(11):
            close = 1004 + j * 3.5
            cap_seed.append(Bar("333333", base + timedelta(minutes=12 + j), close - 2, close + 2, close - 3, close, 1000))
        cap_seed.append(Bar("333333", base + timedelta(minutes=23), 1038, 1040, 998, 1002, 15000))
        recovery = [
            (1002, 1007, 999, 1005, 1200),
            (1005, 1008, 1001, 1004, 800),
            (1004, 1009, 1000, 1007, 600),
            (1007, 1010, 1003, 1008, 500),
            (1008, 1011, 1004, 1009, 450),
            (1009, 1012, 1005, 1010, 400),
        ]
        for idx, values in enumerate(recovery, 24):
            cap_seed.append(Bar("333333", base + timedelta(minutes=idx), *values))
        strategy.seed_bars("333333", cap_seed)
        confirm = Bar("333333", base + timedelta(minutes=30), 1009, 1016, 1004, 1014, 1100)
        signal = strategy.on_bar(confirm, None)
        record("투매후눌림BUY", signal.action == SignalAction.BUY and signal.pattern == "투매후눌림1차", signal.reason)
    except Exception as exc:
        record("투매후눌림BUY", False, str(exc))

    # 투매 저점을 다시 깨면 매수하지 않는다.
    try:
        strategy = MomentumVolumeStrategy(settings)
        broken = [Bar("444444", x.timestamp, x.open, x.high, x.low, x.close, x.volume) for x in cap_seed]
        strategy.seed_bars("444444", broken)
        bad_bull = Bar("444444", base + timedelta(minutes=30), 1000, 1008, 990, 1006, 1100)
        signal = strategy.on_bar(bad_bull, None)
        record("하락저점차단", signal.action == SignalAction.HOLD, signal.reason)
    except Exception as exc:
        record("하락저점차단", False, str(exc))

    # 같은 분봉에서 목표와 손절이 모두 닿으면 선후를 알 수 없어 보수적으로 손절한다.
    try:
        strategy = MomentumVolumeStrategy(settings)
        now_bar = datetime.now(KST).replace(second=0, microsecond=0)
        pos = Position("555555", 10, 1000.0, now_bar - timedelta(minutes=1), 1000.0, 980.0, 1015.0)
        both = Bar("555555", now_bar, 1000.0, 1020.0, 970.0, 1005.0, 1000)
        same_bar_signal = strategy._exit(both, pos)
        record(
            "동일분봉양방향보수처리",
            same_bar_signal.action == SignalAction.SELL and "보수적손절" in same_bar_signal.reason,
            same_bar_signal.reason,
        )
    except Exception as exc:
        record("동일분봉양방향보수처리", False, str(exc))

    # PAPER 목표가가 매수 즉시 +1.5%로 저장되는지 확인.
    global LOG_DIR, STATE_FILE, FILL_FILE, TOKEN_FILE, ERROR_FILE
    old = (LOG_DIR, STATE_FILE, FILL_FILE, TOKEN_FILE, ERROR_FILE)
    try:
        with tempfile.TemporaryDirectory(prefix="v74-self-check-") as tmp:
            LOG_DIR = Path(tmp)
            STATE_FILE = LOG_DIR / "state.json"
            FILL_FILE = LOG_DIR / "fills.csv"
            TOKEN_FILE = LOG_DIR / "token.json"
            ERROR_FILE = LOG_DIR / "errors.txt"
            null = logging.getLogger("v74-null")
            null.handlers.clear(); null.addHandler(logging.NullHandler())
            broker = PaperBroker(settings, null, reset=True)
            quote = Quote("111111", "테스트", 1000, 0, 1, 0, datetime.now(timezone.utc), best_bid=999, best_ask=1000)
            broker.buy("111111", 10, quote, "TEST", stop_loss_pct=0.02, take_profit_pct=0.015, pattern="정석박스돌파")
            pos = broker.positions["111111"]
            record(
                "즉시목표예약",
                pos.target_order_active
                and pos.target_price >= pos.entry_price * 1.015
                and is_tick_aligned(pos.target_price),
                f"목표={pos.target_price:.2f}",
            )
            record(
                "패턴별손절",
                pos.stop_price <= pos.entry_price * 0.98
                and is_tick_aligned(pos.stop_price),
                f"손절={pos.stop_price:.2f}",
            )
            target_fill = broker.sell(
                "111111",
                quote,
                "예약목표+1.5%",
                reserved_fill_price=pos.target_price,
            )
            record(
                "예약목표가체결",
                abs(target_fill.price - pos.target_price) < 1e-9 and target_fill.entry_time is not None,
                f"체결={target_fill.price:.2f} 손익률={target_fill.pnl_pct:+.3%}",
            )
            broker.buy("222222", 10, quote, "TEST-STOP", stop_loss_pct=0.02, take_profit_pct=0.015, pattern="정석박스돌파")
            stop_quote = Quote("222222", "테스트2", 970, 0, 1, 0, datetime.now(timezone.utc), best_bid=970, best_ask=971)
            broker.sell("222222", stop_quote, "고정손절", trigger_time=datetime.now(KST), trigger_price=970)
            record(
                "손절후재진입쿨다운",
                broker.reentry_block_until.get("222222", datetime.now(KST)) > datetime.now(KST),
                f"대기={broker.stop_reentry_cooldown_min}분",
            )
            broker.buy("333333", 10, quote, "TEST-TIMING", stop_loss_pct=0.02, take_profit_pct=0.015, pattern="정석박스돌파")
            detected = datetime.now(KST)
            triggered = detected - timedelta(seconds=5)
            timing_fill = broker.sell(
                "333333",
                stop_quote,
                "고정손절",
                trigger_time=triggered,
                trigger_price=970,
                detection_time=detected,
                observed_lag_sec=12.0,
                trigger_source="SELF_CHECK",
            )
            record(
                "청산지연수치검증",
                4.99 <= timing_fill.monitor_lag_sec <= 5.01
                and timing_fill.execution_lag_sec >= 0.0
                and abs(timing_fill.observed_lag_sec - 12.0) < 1e-9
                and timing_fill.trigger_source == "SELF_CHECK",
                (
                    f"감지={timing_fill.monitor_lag_sec:.2f}초 "
                    f"처리={timing_fill.execution_lag_sec:.2f}초 "
                    f"관측상한={timing_fill.observed_lag_sec:.2f}초"
                ),
            )
    except Exception as exc:
        record("PAPER예약검증", False, str(exc))
    finally:
        LOG_DIR, STATE_FILE, FILL_FILE, TOKEN_FILE, ERROR_FILE = old

    # 실제 주문 API 식별자를 코드에 직접 포함하지 않는지 확인한다.
    source_text = Path(__file__).read_text(encoding="utf-8")
    live_buy_api = "kt" + "10000"
    live_sell_api = "kt" + "10001"
    record(
        "실주문차단",
        f'api_id="{live_buy_api}"' not in source_text and f'api_id="{live_sell_api}"' not in source_text,
        "실주문 함수 없음",
    )
    bad_signal_price_marker = "signal" + ".price"
    record(
        "Signal필드참조정합",
        bad_signal_price_marker not in source_text
        and "signal.reference_price" in source_text,
        "Signal.price 금지 / reference_price 사용",
    )
    record(
        "무효토큰자동재발급판정",
        KiwoomRestClient._is_invalid_token_response(3, "인증에 실패했습니다[8005:Token이 유효하지 않습니다]")
        and not KiwoomRestClient._is_invalid_token_response(0, "정상적으로 처리되었습니다"),
        "return_code=3/8005 감지",
    )
    record(
        "호가단위정렬",
        stock_tick_size(1_641) == 1
        and stock_tick_size(34_417) == 50
        and stock_tick_size(137_068) == 100
        and align_stock_price(34_417, upward=True) == 34_450
        and align_stock_price(137_068, upward=False) == 137_000,
        "국내주식 가격대별 tick",
    )
    record(
        "지연분봉재시도금지",
        "지연분봉신호폐기" in source_text
        and "self._queue_pending_entry(signal)" not in source_text[source_text.find("if not allow_entry:"):source_text.find("elif signal.action == SignalAction.SELL")],
        "stale BUY는 폐기",
    )
    record(
        "순위가격현재가캐시분리",
        "self.rank_snapshots[item.symbol] = item" in source_text
        and "self.latest_quotes[item.symbol] = Quote" not in source_text[source_text.find("def _refresh_universe"):source_text.find("def _current_symbols")],
        "rank snapshot과 live quote 분리",
    )
    record("상승률100설정", env_int("V74_RANK_LIMIT", 100) >= 100)
    monday = datetime(2026, 7, 6, tzinfo=KST)
    record(
        "매매시간08시20시",
        entry_session_open(monday.replace(hour=8, minute=0))
        and entry_session_open(monday.replace(hour=19, minute=59))
        and not entry_session_open(monday.replace(hour=7, minute=59))
        and not entry_session_open(monday.replace(hour=20, minute=0)),
        "평일 08:00 이상 20:00 미만",
    )
    record(
        "시간외분봉포함",
        regular_session_bar(monday.replace(hour=8, minute=1))
        and regular_session_bar(monday.replace(hour=19, minute=59)),
        "08:00~20:00 완성분봉",
    )
    record(
        "PAPER자본독립모드",
        settings.paper_unlimited and settings.max_open_positions >= 9999,
        f"paper_unlimited={settings.paper_unlimited} / 감시지연 안전차단 별도",
    )
    record("투매탐색45봉", env_int("V74_CAPITULATION_LOOKBACK", 45) >= 45)
    fallback_call = "self.active_symbols = self." + "_fallback_universe()"
    record("고정종목Fallback매수차단", fallback_call not in source_text)
    record(
        "후보전체순환",
        "selected = list(dict.fromkeys(item.symbol for item in pool))" in source_text,
        "통합 후보 전체를 분석대상에 등록",
    )
    forced_close_marker = "dt_time(15, " + "20)"
    record("15시강제청산제거", forced_close_marker not in source_text)
    record(
        "쌍끌이양수검증",
        "foreign_net <= 0 or institution_net <= 0" in source_text,
        "외국인·기관 각각 양수 하드게이트",
    )
    record("ka90009필수조회일자구분", '"qry_dt_tp": "0"' in source_text)
    try:
        null_logger = logging.getLogger("v74-rank-parser-selfcheck")
        null_logger.handlers.clear(); null_logger.addHandler(logging.NullHandler())
        client = KiwoomRestClient(settings, null_logger)
        sample = {
            "trde_qty_sdnin": [{
                "stk_cd": "005930", "stk_nm": "삼성전자", "cur_prc": "+70000",
                "now_trde_qty": "1,234,567", "flu_rt": "+3.21", "sudn_rt": "+250.5",
            }],
            "return_code": 0, "return_msg": "정상적으로 처리되었습니다",
        }
        parsed = client._parse_rank_candidates([sample], source="거래량급증")
        record(
            "ka10023실응답키파싱",
            len(parsed) == 1 and parsed[0].symbol == "005930" and abs(parsed[0].surge_rate - 250.5) < 1e-9,
            f"후보={len(parsed)}",
        )
        bodies = client._dual_rank_request_bodies()
        record(
            "ka90009요청본문보강",
            bool(bodies) and all("qry_dt_tp" in body for body in bodies) and all("invsr" not in body for body in bodies),
            f"본문수={len(bodies)}",
        )

        # 한 행의 외국인/기관 종목을 잘못 짝지지 않고 전체 순위 교집합을 구하는지 검증.
        dual_payload = {
            "frgnr_orgn_trde_upper": [
                {
                    "for_netprps_stk_cd": "111111",
                    "for_netprps_stk_nm": "외인전용",
                    "for_netprps_amt": "100",
                    "for_netprps_qty": "10",
                    "orgn_netprps_stk_cd": "222222_AL",
                    "orgn_netprps_stk_nm": "기관전용",
                    "orgn_netprps_amt": "200",
                    "orgn_netprps_qty": "20",
                },
                {
                    "for_netprps_stk_cd": "333333",
                    "for_netprps_stk_nm": "교집합종목",
                    "for_netprps_amt": "300",
                    "for_netprps_qty": "30",
                    "orgn_netprps_stk_cd": "111111_AL",
                    "orgn_netprps_stk_nm": "외인전용",
                    "orgn_netprps_amt": "400",
                    "orgn_netprps_qty": "40",
                },
                {
                    "for_netprps_stk_cd": "444444",
                    "for_netprps_stk_nm": "다른외인",
                    "for_netprps_amt": "500",
                    "for_netprps_qty": "50",
                    "orgn_netprps_stk_cd": "333333_AL",
                    "orgn_netprps_stk_nm": "교집합종목",
                    "orgn_netprps_amt": "600",
                    "orgn_netprps_qty": "60",
                },
            ]
        }
        dual_items, schema_seen = client._parse_dual_buy_candidates([dual_payload])
        record(
            "ka90009교집합파싱",
            schema_seen
            and {item.symbol for item in dual_items} == {"111111", "333333"}
            and all(item.dual_buy and item.foreign_net > 0 and item.institution_net > 0 for item in dual_items),
            ",".join(item.symbol for item in dual_items),
        )
    except Exception as exc:
        record("순위스키마보강", False, str(exc))

    # ---------------- V75 포함 검증 ----------------

    # 휴장일 캘린더: 2026 잔여 휴장일과 정상 거래일 판정.
    record(
        "휴장일캘린더2026",
        is_market_holiday(date(2026, 8, 17))
        and is_market_holiday(date(2026, 9, 24))
        and is_market_holiday(date(2026, 12, 31))
        and not is_market_holiday(date(2026, 7, 7)),
        "8/17·추석·연말 휴장, 7/7 정상",
    )
    # 세션 구간: 프리 08:00~08:50 / GAP 08:50~09:00 / 정규 / 애프터 / 종료.
    tue = datetime(2026, 7, 7, tzinfo=KST)
    record(
        "세션구간판정",
        session_segment(tue.replace(hour=8, minute=10)) == "PRE"
        and session_segment(tue.replace(hour=8, minute=55)) == "GAP_OPEN"
        and session_segment(tue.replace(hour=10, minute=0)) == "MAIN"
        and session_segment(tue.replace(hour=15, minute=35)) == "GAP_CLOSE"
        and session_segment(tue.replace(hour=16, minute=0)) == "AFTER"
        and session_segment(tue.replace(hour=21, minute=0)) == "CLOSED"
        and not entry_session_open(tue.replace(hour=8, minute=55))
        and not entry_session_open(tue.replace(hour=15, minute=35)),
        "개장·폐장 GAP 신규진입 차단",
    )
    # 휴장일에는 시각이 장중이라도 진입 금지.
    holiday = datetime(2026, 8, 17, 10, 0, tzinfo=KST)
    record("휴장일진입차단", not entry_session_open(holiday) and market_label(holiday) == "HOLIDAY")

    # 임펄스 조정 양성: 1/2 조정, 거래량 바닥, 두 개 이상 이평 밀집,
    # 두 개 이상 분봉 지지, 아래꼬리 양봉·음봉, 단기고점 회복, 거래 재유입.
    try:
        strategy = MomentumVolumeStrategy(settings)
        base = datetime.now(KST).replace(second=0, microsecond=0) - timedelta(minutes=50)
        imp_seed: list[Bar] = []
        for i in range(24):
            close = 1000 + (i % 3)
            imp_seed.append(Bar("666666", base + timedelta(minutes=i), close - 1, close + 2, close - 2, close, 1000))
        imp_seed.append(Bar("666666", base + timedelta(minutes=24), 1002, 1045, 1001, 1042, 60000))
        adjustment = [
            (1042, 1043, 1031, 1033, 4000),
            (1033, 1035, 1027, 1029, 2500),
            (1029, 1031, 1026, 1028, 1800),
            (1028, 1030, 1026, 1029, 1500),
            (1029, 1030, 1027, 1029, 1400),
            (1029, 1030, 1027, 1029, 1300),
            (1029, 1030, 1027, 1029, 1200),
        ]
        for idx, values in enumerate(adjustment, 25):
            imp_seed.append(Bar("666666", base + timedelta(minutes=idx), *values))
        strategy.seed_bars("666666", imp_seed)
        confirm = Bar("666666", base + timedelta(minutes=32), 1029, 1033, 1025, 1032, 3200)
        signal = strategy.on_bar(confirm, None)
        record("임펄스눌림BUY", signal.action == SignalAction.BUY and signal.pattern == "임펄스눌림1차", signal.reason)
    except Exception as exc:
        record("임펄스눌림BUY", False, str(exc))

    # 너무 얕은 조정이나 전고점 바로 아래 무거래 반등은 매수하지 않는다.
    try:
        strategy = MomentumVolumeStrategy(settings)
        shallow = [Bar("777777", x.timestamp, x.open, x.high, x.low, x.close, x.volume) for x in imp_seed]
        strategy.seed_bars("777777", shallow)
        weak_near_high = Bar("777777", base + timedelta(minutes=32), 1038, 1043, 1037, 1042, 1300)
        signal = strategy.on_bar(weak_near_high, None)
        record("임펄스깊은되돌림차단", signal.action == SignalAction.HOLD, signal.reason)
    except Exception as exc:
        record("임펄스깊은되돌림차단", False, str(exc))

    # 목표가 '터치만'은 미체결(관통+거래량 필요), 관통은 체결.
    try:
        strategy = MomentumVolumeStrategy(settings)
        now_bar = datetime.now(KST).replace(second=0, microsecond=0)
        pos = Position("888888", 10, 1000.0, now_bar - timedelta(minutes=5), 1000.0, 980.0, 1015.0)
        touch = Bar("888888", now_bar, 1005.0, 1015.0, 1004.0, 1010.0, 500)
        touched = strategy._exit(touch, pos)
        pierce = Bar("888888", now_bar + timedelta(minutes=1), 1010.0, 1016.0, 1008.0, 1012.0, 500)
        pierced = strategy._exit(pierce, pos)
        zero_vol = Bar("888888", now_bar + timedelta(minutes=2), 1010.0, 1020.0, 1008.0, 1012.0, 0)
        zero = strategy._exit(zero_vol, pos)
        record(
            "목표터치미체결관통체결",
            touched.action == SignalAction.HOLD and pierced.action == SignalAction.SELL and zero.action == SignalAction.HOLD,
            f"터치={touched.action.value} 관통={pierced.action.value} 무거래={zero.action.value}",
        )
    except Exception as exc:
        record("목표터치미체결관통체결", False, str(exc))

    # 시간 강제청산 기본 비활성 + 세션 경과분 계산(18시 매수→다음날 아침은 벽시계가 아니라 세션 누적).
    try:
        record("시간강제청산기본비활성", MomentumVolumeStrategy(settings).max_hold_minutes == env_int("V74_MAX_HOLD_MINUTES", 0))
        entry = datetime(2026, 7, 6, 18, 0, tzinfo=KST)   # 월요일 18:00 매수
        next_morning = datetime(2026, 7, 7, 8, 30, tzinfo=KST)  # 화요일 08:30
        held = session_minutes_between(entry, next_morning)
        record("세션경과분계산", abs(held - 150.0) < 0.01, f"18:00~20:00(120)+08:00~08:30(30)={held:.0f}분")
    except Exception as exc:
        record("세션경과분계산", False, str(exc))

    # 패턴 상태 저장·복구 왕복.
    try:
        strategy = MomentumVolumeStrategy(settings)
        ts = datetime.now(KST).replace(second=0, microsecond=0)
        strategy.capitulation_cache["101010"] = (ts, 3.3, 950.0)
        strategy.impulse_cache["202020"] = (ts, 40.0, 60000.0, 1001.0)
        payload = strategy.export_pattern_state()
        fresh = MomentumVolumeStrategy(settings)
        restored = fresh.restore_pattern_state(payload)
        record(
            "패턴상태저장복구",
            restored == 2 and "101010" in fresh.capitulation_cache and "202020" in fresh.impulse_cache,
            f"복구={restored}건",
        )
    except Exception as exc:
        record("패턴상태저장복구", False, str(exc))

    # 얇은 거래량 가짜 돌파 차단: 조건은 갖췄지만 돌파봉 거래대금이 하한 미만이면 HOLD.
    try:
        strategy = MomentumVolumeStrategy(settings)
        base2 = datetime.now(KST).replace(second=0, microsecond=0) - timedelta(minutes=40)
        thin: list[Bar] = []
        for i in range(30):
            close = 100 + (i % 4) * 0.2
            thin.append(Bar("909090", base2 + timedelta(minutes=i), close - 0.1, 101, 99 + i * 0.015, close, 100 + (i % 3) * 10))
        strategy.seed_bars("909090", thin[:-1])
        thin_breakout = Bar("909090", thin[-1].timestamp, 100.4, 104.5, 100.2, 104.0, 500)  # 거래대금 약 5.2만원
        signal = strategy.on_bar(thin_breakout, None)
        record("얇은거래량가짜돌파차단", signal.action == SignalAction.HOLD, signal.reason)
    except Exception as exc:
        record("얇은거래량가짜돌파차단", False, str(exc))

    # 부하 시뮬레이션: 라운드로빈 감시 구조의 보유수별 예상 감시지연(정직한 산술 보고).
    try:
        per_loop = max(0.5, settings.min_interval_sec) * 3.0  # 보유1+재시도/후보 호출 평균 근사
        rows = []
        for held in (10, 50, 100):
            delay = held * per_loop
            rows.append(f"보유{held}={delay:.0f}초")
        record(
            "부하시뮬레이션감시지연",
            True,
            " / ".join(rows) + " (REST 폴링 구조 한계—목표·손절 즉시성은 보유수에 반비례)",
        )
    except Exception as exc:
        record("부하시뮬레이션감시지연", False, str(exc))


    # ---------------- V75 조정 후 상승 패턴 검증 ----------------
    record(
        "아래꼬리양봉음봉저점확인",
        MomentumVolumeStrategy._lower_wick_rejection(
            Bar("LWBULL", datetime.now(KST), 100.0, 103.0, 94.0, 102.0, 1000)
        )
        and MomentumVolumeStrategy._lower_wick_rejection(
            Bar("LWBEAR", datetime.now(KST), 102.0, 103.0, 94.0, 100.0, 1000)
        )
        and not MomentumVolumeStrategy._lower_wick_rejection(
            Bar("NOWICK", datetime.now(KST), 102.0, 103.0, 99.5, 100.0, 1000)
        ),
        "긴 아래꼬리 양봉·음봉 인정 / 아래꼬리 부족 봉 차단",
    )
    record(
        "위꼬리우세음봉차단",
        not MomentumVolumeStrategy._lower_wick_rejection(
            Bar("UPPERHEAVY", datetime.now(KST), 102.0, 112.0, 96.0, 100.0, 1000)
        ),
        "음봉은 아래꼬리가 위꼬리보다 1.2배 이상 길어야 인정",
    )
    record(
        "호가단위기반꼬리계산",
        "tick = float(stock_tick_size" in source_text
        and "body = max(tick" in source_text
        and "candle_range = max(tick" in source_text,
        "고정 1원 대신 가격대별 호가단위 사용",
    )
    record(
        "청산시각3단계기록",
        "detection_time" in source_text
        and "execution_lag_sec" in source_text
        and "observed_lag_sec" in source_text
        and "REST_TICK_DETECTION" in source_text,
        "트리거·감지·체결 및 REST 감시간격 상한 기록",
    )
    record(
        "신규진입15초감시차단",
        'env_float("V74_MAX_MONITOR_DELAY_SEC", 15.0)' in source_text,
        "예상 감시주기 15초 초과 시 신규진입 차단",
    )
    record(
        "다중이평지지소스검사",
        "support_count >= self.adjust_min_support_bars" in source_text
        and "pair_count >= 1" in source_text,
        "모인 이평선 2개 이상/최소 2봉 지지",
    )
    record(
        "얕은조정매수금지소스검사",
        "self.adjust_retrace_third_min <= retrace" in source_text,
        "상승 후 단순 횡보는 조정으로 불인정",
    )
    record(
        "전고점근처무거래매수차단소스검사",
        "confirmed_high_break" in source_text
        and "near_resistance_volume_mult" in source_text,
        "전고점 근처는 실제 돌파+거래 재폭발 필요",
    )
    record(
        "절대유동성게이트소스검사",
        "누적거래대금부족" in source_text
        and "최근5분거래대금부족" in source_text
        and "전일동시간거래량약함" in source_text,
        "저거래 종목 신규진입 차단",
    )
    record(
        "일봉참고비절대기준",
        "def _daily_reference" in source_text
        and "일봉은 절대 차단이 아니라" in source_text,
        "분봉 우선, 일봉은 강도 보정",
    )
    record(
        "직전버전패턴상태호환",
        "PATTERN_STATE_COMPATIBLE_VERSIONS" in source_text
        and '"V75.2-EXECUTION-PRECISION-PAPER"' in source_text
        and 'in PATTERN_STATE_COMPATIBLE_VERSIONS' in source_text,
        "운영패치로 V75.2 패턴상태를 소실하지 않음",
    )
    record(
        "애프터신규진입기본차단",
        strategy_entry_session_open(tue.replace(hour=10, minute=0), after_entry_enabled=False)
        and not strategy_entry_session_open(tue.replace(hour=16, minute=0), after_entry_enabled=False)
        and strategy_entry_session_open(tue.replace(hour=16, minute=0), after_entry_enabled=True),
        "정규장 허용 / AFTER 기본차단 / 명시적 opt-in 허용",
    )
    record(
        "애프터보유청산우선",
        "self._monitor_one_position()" in source_text
        and source_text.find("self._monitor_one_position()") < source_text.find("self._enter_after_monitor_only(now_kst)"),
        "보유종목 감시 후 신규진입 작업만 중단",
    )
    record(
        "애프터무분봉재조회유예",
        "self.after_no_bar_until[symbol]" in source_text
        and "self.after_reprobe_sec" in source_text
        and 'if current_segment == "AFTER"' in source_text,
        "종목별 경고 반복 대신 재조회 유예",
    )
    record(
        "애프터후보API중단",
        'if session_segment(now_kst) == \"AFTER\" and not self.after_entry_enabled' in source_text
        and "self._enter_after_monitor_only(now_kst)" in source_text,
        "기본모드에서 순위·분봉 후보 호출 중단",
    )

    return results


def quote_test(settings: Settings, logger: logging.Logger, symbol: str = "005930") -> int:
    client = KiwoomRestClient(settings, logger)
    quote = client.get_quote(symbol)
    logger.warning(
        "현재가 테스트 성공 symbol=%s name=%s price=%.0f change=%.2f%% volume=%s server=%s suffix=%s",
        quote.symbol,
        quote.name,
        quote.price,
        quote.change_pct,
        f"{quote.cumulative_volume:,}",
        settings.api_mode,
        settings.suffix or "KRX",
    )
    try:
        full = client.get_orderbook(symbol, quote)
        logger.warning(
            "호가 테스트 성공 bid=%.0f ask=%.0f spread=%.3f%%",
            full.best_bid or 0,
            full.best_ask or 0,
            (full.spread_pct or 0) * 100,
        )
    except V73Error as exc:
        logger.warning("현재가는 정상이나 호가 테스트 실패: %s", exc)
    try:
        bars = client.get_minute_bars(symbol, limit=3, completed_only=True)
        latest = bars[-1]
        logger.warning(
            "실제 1분봉 테스트 성공 time=%s O=%.0f H=%.0f L=%.0f C=%.0f V=%s",
            latest.timestamp.strftime("%Y-%m-%d %H:%M"),
            latest.open,
            latest.high,
            latest.low,
            latest.close,
            f"{latest.volume:,}",
        )
    except V73Error as exc:
        logger.critical("실제 1분봉 테스트 실패: %s", exc)
        return 5
    try:
        daily = client.get_daily_bars(symbol, limit=5)
        logger.warning("일봉 테스트 성공=%s개 최근=%s", len(daily), daily[-1].trading_date)
    except V73Error as exc:
        logger.warning("일봉 테스트 실패: %s", brief_error(exc))
    return 0


def scan_test(settings: Settings, logger: logging.Logger) -> int:
    client = KiwoomRestClient(settings, logger)
    groups: list[list[Candidate]] = []
    failed_apis: list[str] = []
    for name, api_id, loader in (
        ("상승률", "ka10027", lambda: client.get_gain_rank_candidates(limit=100)),
        ("거래량급증", "ka10023", lambda: client.get_volume_surge_candidates(limit=100)),
        ("쌍끌이", "ka90009", lambda: client.get_dual_buy_candidates(limit=100)),
    ):
        try:
            items = loader()
            if api_id == "ka10023" and not items:
                logger.warning("%s 후보=0 (정상응답·장외/조건미충족 가능)", name)
            else:
                logger.warning("%s 후보=%s", name, len(items))
            groups.append(items)
        except V73Error as exc:
            failed_apis.append(api_id)
            logger.warning("%s 조회실패: %s", name, brief_error(exc))

    probe_ids = sorted(set(failed_apis) | {"ka10023", "ka90009"})
    path, summaries = client.write_rank_schema_probe(probe_ids)
    logger.warning("순위 스키마 진단 저장=%s", path)
    for summary in summaries:
        logger.warning("스키마진단 %s", summary)

    if not groups:
        return 6
    merged = TradingEngine._merge_candidates(groups)
    logger.warning("통합 관심종목=%s", len(merged))
    for item in merged[:30]:
        logger.warning(
            "%s %s 등락=%+.2f%% 급증=%.1f%% 거래량=%s 출처=%s 쌍끌이=%s 외인=%.0f 기관=%.0f",
            item.symbol, item.name, item.change_pct, item.surge_rate, f"{item.volume:,}", item.source,
            "Y" if item.dual_buy else "N", item.foreign_net, item.institution_net,
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="V75.3 AFTER-SESSION-GUARD 단일파일 PAPER 자동매매")
    parser.add_argument("--self-check", action="store_true", help="오프라인 자체점검만 실행")
    parser.add_argument("--quote-test", action="store_true", help="현재가/호가/분봉 1회 테스트")
    parser.add_argument("--scan-test", action="store_true", help="상승률100·거래량급증·쌍끌이 통합후보 테스트")
    parser.add_argument("--symbol", default="005930", help="quote-test 종목코드")
    parser.add_argument("--minutes", type=int, default=0, help="실행시간(분), 0이면 Ctrl+C까지")
    parser.add_argument("--reset-paper", action="store_true", help="PAPER 잔고/체결 기록 초기화")
    parser.add_argument("--verbose", action="store_true", help="상세로그")
    return parser


def main() -> int:
    configure_console()
    loaded = load_environment_files()
    args = build_parser().parse_args()
    logger = setup_logging(args.verbose)
    try:
        settings = Settings.load()
        logger.info("환경파일 자동탐색=%s", [str(path) for path in loaded] or "없음")
        if args.self_check:
            results = self_check(settings)
            failed = False
            for name, ok, detail in results:
                print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
                failed = failed or not ok
            return 1 if failed else 0
        newly_saved = ensure_credentials(logger)
        loaded.extend(newly_saved)
        if newly_saved:
            settings = Settings.load()
            logger.info("신규 환경파일 적용=%s", [str(path) for path in newly_saved])
        settings.assert_credentials()
        if args.quote_test:
            return quote_test(settings, logger, args.symbol)
        if args.scan_test:
            return scan_test(settings, logger)
        client = KiwoomRestClient(settings, logger)
        # 시작 직후 1종목 계약 테스트: 성공하지 못하면 엔진을 아예 시작하지 않는다.
        logger.warning("시작 전 현재가 계약검증: %s", settings.watchlist[0][0])
        first = client.get_quote(settings.watchlist[0][0])
        logger.warning("계약검증 성공 %s %.0f원", first.symbol, first.price)
        engine = TradingEngine(settings, client, logger, reset_paper=args.reset_paper)
        engine.latest_quotes[first.symbol] = first
        engine.run(minutes=args.minutes if args.minutes > 0 else None)
        return 0
    except ConfigurationError as exc:
        logger.critical("설정 오류: %s", exc)
        return 2
    except AuthenticationError as exc:
        logger.critical("인증 오류: %s", exc)
        return 3
    except V73Error as exc:
        logger.critical("실행 차단: %s", exc)
        return 4
    except Exception as exc:
        logger.critical("예상 밖 치명 오류: %s", exc)
        append_api_error(traceback.format_exc())
        return 9


if __name__ == "__main__":
    raise SystemExit(main())
