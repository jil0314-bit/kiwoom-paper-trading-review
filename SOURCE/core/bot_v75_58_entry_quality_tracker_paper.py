#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.58 PAPER — 진입품질(MAE/MFE) 계측 레이어

형의 요청: 손절 없이 매매하면서 "빠졌다 다시 오르는 경우"와 "손실이 커져
실현수익을 까먹는 경우"를 데이터로 관찰한다.

이 파일이 하는 일
------------------
V75.57 을 SHA256 으로 고정 상속하고, PaperBroker.buy / PaperBroker.sell /
TradingEngine._check_tick_exit 를 '감싸서(wrap)' 각 보유 종목의
- MFE (매수 후 최대 상승)
- MAE (매수 후 최대 하락)
- +1.5% 목표 도달 여부와 도달 전 최대 하락
을 기록한다. 청산 때 한 줄씩 CSV 로 남긴다.

절대 하지 않는 것
-----------------
- 매수/매도 판단에 개입하지 않는다. 원본 메서드를 그대로 호출하고 결과도
  그대로 반환한다. 관찰값만 옆에서 기록한다.
- 매매 로직·주문·안전장치·목표(+1.5%)·손절 정책을 바꾸지 않는다.
- 계측 코드에서 예외가 나도 매매를 막지 않는다(전부 try/except 로 격리).

산출물
------
- runtime/entry_quality_log.csv : 청산된 매매 1건당 1행 (MAE/MFE/도달/손익)
- 콘솔 명령 --quality-report : 쌓인 CSV 를 형 가설 관점으로 분석해 출력

PAPER 전용. 실주문 함수는 없다.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import importlib.util
from pathlib import Path
import sys
from types import ModuleType
from typing import Any

VERSION = "V75.58-ENTRY-QUALITY-TRACKER-PAPER"
BASE_DIR = Path(__file__).resolve().parent

V7557_FILENAME = "bot_v75_57_unified_dashboard_collaboration_paper.py"
V7557_SHA256 = "6e7e2e87b55326bed7a067b51939170088291224fd5b31838c5c501df02d4aa3"

RUNTIME_DIR = BASE_DIR.parent / "runtime"
QUALITY_CSV = RUNTIME_DIR / "entry_quality_log.csv"
TARGET_PCT = 1.5


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_fixed(filename: str, expected_sha: str, module_name: str) -> ModuleType:
    path = BASE_DIR / filename
    actual = sha256_file(path)
    if actual.lower() != expected_sha:
        raise RuntimeError(f"{filename} SHA256 불일치\n기대: {expected_sha}\n실제: {actual}")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"{filename} 로드 실패")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


v7557 = _load_fixed(V7557_FILENAME, V7557_SHA256, "v7558_immutable_base")

# 엔진/브로커가 정의된 최하위 모듈(V75.3)을 체인에서 찾는다.
def _resolve(attr: str) -> ModuleType | None:
    mod = v7557
    for _ in range(30):
        if hasattr(mod, attr):
            return mod
        nxt = None
        for name in dir(mod):
            val = getattr(mod, name)
            if isinstance(val, ModuleType) and name.startswith("v75"):
                nxt = val
                break
        if nxt is None or nxt is mod:
            break
        mod = nxt
    return None


# --- 계측기 로드 (동일 폴더의 entry_quality_tracker.py) ---
_tracker_path = BASE_DIR / "entry_quality_tracker.py"
_spec = importlib.util.spec_from_file_location("v7558_tracker", _tracker_path)
_tracker_mod = importlib.util.module_from_spec(_spec)  # type: ignore
assert _spec and _spec.loader
sys.modules["v7558_tracker"] = _tracker_mod  # dataclass 가 모듈을 찾을 수 있게 등록
_spec.loader.exec_module(_tracker_mod)  # type: ignore
EntryQualityTracker = _tracker_mod.EntryQualityTracker
analyze_observations = _tracker_mod.analyze_observations

RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
TRACKER = EntryQualityTracker(QUALITY_CSV, target_pct=TARGET_PCT)


# ─────────────────────────────────────────────────────────
# PaperBroker.buy / sell / TradingEngine._check_tick_exit 래핑
# ─────────────────────────────────────────────────────────
_broker_mod = None
_engine_mod = None
for _m in (v7557,) + tuple(
    getattr(v7557, n) for n in dir(v7557)
    if isinstance(getattr(v7557, n), ModuleType) and n.startswith("v75")
):
    if hasattr(_m, "PaperBroker") and _broker_mod is None:
        _broker_mod = _m
    if hasattr(_m, "TradingEngine") and _engine_mod is None:
        _engine_mod = _m

# 체인 더 깊이 탐색 (V75.3에 있음)
def _deep_find(attr: str) -> Any:
    seen = set()
    stack = [v7557]
    while stack:
        mod = stack.pop()
        if id(mod) in seen:
            continue
        seen.add(id(mod))
        if hasattr(mod, attr):
            return getattr(mod, attr), mod
        for name in dir(mod):
            val = getattr(mod, name, None)
            if isinstance(val, ModuleType) and name.startswith("v75"):
                stack.append(val)
    return None, None


PaperBroker, _broker_home = _deep_find("PaperBroker")
TradingEngine, _engine_home = _deep_find("TradingEngine")

if PaperBroker is None or TradingEngine is None:
    raise RuntimeError("PaperBroker/TradingEngine 를 체인에서 찾지 못했습니다.")

_ORIG_BUY = PaperBroker.buy
_ORIG_SELL = PaperBroker.sell
_ORIG_TICK = TradingEngine._check_tick_exit


def _wrapped_buy(self: Any, symbol: str, quantity: int, quote: Any, reason: str,
                 **kwargs: Any) -> Any:
    fill = _ORIG_BUY(self, symbol, quantity, quote, reason, **kwargs)  # 원본 그대로
    try:
        pos = self.positions.get(symbol)
        if pos is not None:
            TRACKER.on_entry(
                symbol=symbol,
                name=getattr(quote, "name", "") or getattr(pos, "name", "") or symbol,
                entry_price=float(pos.entry_price),
                entry_time=pos.entry_time,
                target_price=float(getattr(pos, "target_price", 0)) or None,
            )
    except Exception:
        pass  # 관측 실패가 매매를 막지 않는다
    return fill


def _wrapped_sell(self: Any, symbol: str, *args: Any, **kwargs: Any) -> Any:
    # 청산 전 현재가/사유를 관측에 남기기 위해 포지션을 미리 참조
    pos = self.positions.get(symbol)
    fill = _ORIG_SELL(self, symbol, *args, **kwargs)  # 원본 그대로
    try:
        exit_price = float(getattr(fill, "price", 0.0)) if fill is not None else 0.0
        reason = str(getattr(fill, "reason", "") or (args[2] if len(args) > 2 else "청산"))
        when = getattr(fill, "timestamp", None) or datetime.now()
        if exit_price > 0:
            TRACKER.close(symbol, exit_price, reason, when)
    except Exception:
        pass
    return fill


def _wrapped_tick_exit(self: Any, quote: Any, **kwargs: Any) -> Any:
    # 원본 청산 검사 먼저 (매매 판단은 원본이 전담)
    result = _ORIG_TICK(self, quote, **kwargs)
    try:
        price = float(getattr(quote, "price", 0.0))
        if price > 0:
            TRACKER.observe(str(getattr(quote, "symbol", "")), price, datetime.now())
    except Exception:
        pass
    return result


PaperBroker.buy = _wrapped_buy
PaperBroker.sell = _wrapped_sell
TradingEngine._check_tick_exit = _wrapped_tick_exit


def self_check() -> list[tuple[str, bool, str]]:
    import inspect
    results = [
        ("V75.57기준SHA", sha256_file(BASE_DIR / V7557_FILENAME) == V7557_SHA256, V7557_SHA256[:16]),
        ("buy래핑됨", PaperBroker.buy is _wrapped_buy, "진입 관측"),
        ("sell래핑됨", PaperBroker.sell is _wrapped_sell, "청산 관측"),
        ("tick래핑됨", TradingEngine._check_tick_exit is _wrapped_tick_exit, "시세 관측"),
        ("원본buy보존", _ORIG_BUY is not _wrapped_buy, "원본 호출됨"),
    ]
    # 계측기 동작 확인 (가짜 1건)
    try:
        from datetime import timedelta
        t = EntryQualityTracker(RUNTIME_DIR / "_selfcheck.csv", target_pct=1.5)
        base = datetime(2026, 7, 16, 9, 30)
        t.on_entry("000000", "테스트", 10000.0, base)
        t.observe("000000", 9700.0, base + timedelta(minutes=2))   # -3% 하락
        t.observe("000000", 10150.0, base + timedelta(minutes=8))  # +1.5% 도달
        obs = t.close("000000", 10150.0, "목표도달", base + timedelta(minutes=8))
        ok = (obs is not None and obs.target_reached
              and abs(obs.max_adverse_before_target - (-3.0)) < 0.1
              and abs(obs.mfe_pct() - 1.5) < 0.1)
        results.append(("계측정확성", ok,
                        f"MAE전{obs.max_adverse_before_target:.1f}% MFE{obs.mfe_pct():.1f}%"))
        (RUNTIME_DIR / "_selfcheck.csv").unlink(missing_ok=True)
    except Exception as exc:
        results.append(("계측정확성", False, f"{type(exc).__name__}: {exc}"))
    return results


def quality_report() -> str:
    return analyze_observations(QUALITY_CSV)


def main() -> int:
    return int(v7557.main())


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        rows = self_check()
        for name, ok, detail in rows:
            print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
        raise SystemExit(0 if all(ok for _, ok, _ in rows) else 1)
    if "--quality-report" in sys.argv:
        print(quality_report())
        raise SystemExit(0)
    raise SystemExit(main())
