#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.59 PAPER — 진입품질·회복·실현수익 잠식 관찰기 강화판.

V75.57 단일 대시보드를 SHA256으로 고정 상속한다. 기존 자동손절 없음,
+1.5% 예약목표, PAPER 주문, 협업 입력, 영웅문 클릭/더블클릭/화살표 연동은
변경하지 않는다. 이 버전은 매매에 개입하지 않는 관측 레이어만 추가한다.

V75.58 실코드 감사에서 수정한 핵심 결함:
1) 최하위 TradingEngine을 감싸 실제 실행 V7544Engine의 목표청산 메서드에는
   계측이 적용되지 않던 문제를 수정한다.
2) 목표 청산 함수를 먼저 실행해 포지션이 삭제된 뒤 관측하던 순서를 바꿔,
   목표가 도달 틱을 청산 전에 기록한다.
3) 활성 MAE/MFE를 메모리에만 두던 문제를 원자적 JSON 복구 상태로 보강한다.
4) 청산 거래만 분석해 열린 대형 손실을 제외하던 생존편향을 줄인다.
5) 가격 차이만으로 계산한 청산수익률 대신 수수료·세금이 반영된 Fill 순손익을
   별도 기록한다.
6) 파일 오류를 조용히 삼키지 않고 경고·복구 JSONL에 남긴다.

자동손절을 다시 켜지 않으며, 경고도 주문을 발생시키지 않는다. PAPER 전용.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.util
import math
from pathlib import Path
import sys
import tempfile
from types import ModuleType
from typing import Any, Mapping

VERSION = "V75.59-ENTRY-QUALITY-OBSERVER-HARDENED-PAPER"
BASE_DIR = Path(__file__).resolve().parent
V7557_FILENAME = "bot_v75_57_unified_dashboard_collaboration_paper.py"
V7557_SHA256 = "6e7e2e87b55326bed7a067b51939170088291224fd5b31838c5c501df02d4aa3"
RUNTIME_DIR = BASE_DIR.parent / "runtime"
TRADES_V2_CSV = RUNTIME_DIR / "entry_quality_trades_v2.csv"
ACTIVE_STATE_JSON = RUNTIME_DIR / "entry_quality_active_v2.json"
PORTFOLIO_SNAPSHOTS_CSV = RUNTIME_DIR / "entry_quality_portfolio_snapshots_v2.csv"
ANOMALIES_JSONL = RUNTIME_DIR / "entry_quality_recovery_anomalies_v2.jsonl"
LEGACY_V58_CSV = RUNTIME_DIR / "entry_quality_log.csv"
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
    if actual.lower() != expected_sha.lower():
        raise RuntimeError(f"{filename} SHA256 불일치\n기대: {expected_sha}\n실제: {actual}")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"{filename} 로드 실패")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


v7557 = _load_fixed(V7557_FILENAME, V7557_SHA256, "v7559_immutable_v7557_base")
KST = v7557.KST
v7556 = v7557.v7556
v7544 = v7556.v7544
v7543 = v7556.v7543
v753 = v7544.v753

# 실제 main()에서 생성되는 클래스와 브로커를 명시적으로 잡는다.
ACTIVE_ENGINE = v7544.V7544Engine
PAPER_BROKER = v753.PaperBroker

_tracker_path = BASE_DIR / "entry_quality_tracker_v2.py"
_tracker_spec = importlib.util.spec_from_file_location("v7559_entry_quality_tracker", _tracker_path)
if _tracker_spec is None or _tracker_spec.loader is None:
    raise RuntimeError("entry_quality_tracker_v2.py 로드 실패")
_tracker_module = importlib.util.module_from_spec(_tracker_spec)
sys.modules[_tracker_spec.name] = _tracker_module
_tracker_spec.loader.exec_module(_tracker_module)
EntryQualityTrackerV2 = _tracker_module.EntryQualityTrackerV2
analyze_quality = _tracker_module.analyze_quality

RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
TRACKER = EntryQualityTrackerV2(
    TRADES_V2_CSV,
    ACTIVE_STATE_JSON,
    PORTFOLIO_SNAPSHOTS_CSV,
    ANOMALIES_JSONL,
    target_pct=TARGET_PCT,
)

# 대시보드 용어 탭에 관찰 지표를 추가한다. 기존 GUI 구조는 그대로다.
_QUALITY_TERMS = """

[진입품질 관찰]
MAE: 매수 후 가장 크게 하락했던 폭. 음수가 깊을수록 불리한 움직임이 컸다는 뜻
MFE: 매수 후 가장 크게 상승했던 폭. 실제 청산 전 얻을 수 있었던 최대 유리폭
목표 전 최대하락: +1.5% 목표에 닿기 전에 먼저 얼마나 빠졌는지 나타내는 값
수중 체류시간: 현재가가 평단 아래에 머문 가장 긴 시간
목표 터치: 시세가 목표가에 닿은 사실. 실제 호가 체결과는 구분해서 기록
실현수익 잠식률: 열린 손실 합계가 이미 실현한 수익의 몇 %인지 나타내는 관찰값
열린 거래 편향: 아직 팔지 않은 큰 손실도 분석에 포함해 완료 거래만 보는 착시를 방지
관찰 경고: 손실 확대를 알려주는 표시일 뿐 자동매도·자동손절 명령이 아님
"""
if _QUALITY_TERMS.strip() not in v7557.DASHBOARD_TERMS:
    v7557.DASHBOARD_TERMS += _QUALITY_TERMS

_ORIG_BROKER_BUY = PAPER_BROKER.buy
_ORIG_BROKER_SELL = PAPER_BROKER.sell
_ORIG_ENGINE_INIT = ACTIVE_ENGINE.__init__
_ORIG_ACTIVE_TICK_EXIT = ACTIVE_ENGINE._check_tick_exit
_ORIG_COMPACT_REPORT = ACTIVE_ENGINE._compact_report


def _warn_from_engine(engine: Any, message: str) -> None:
    try:
        engine.logger.warning("[진입품질] %s", message)
    except Exception:
        pass


def _wrapped_engine_init(self: Any, *args: Any, **kwargs: Any) -> None:
    _ORIG_ENGINE_INIT(self, *args, **kwargs)
    TRACKER.set_logger(lambda message: _warn_from_engine(self, message))
    try:
        result = TRACKER.reconcile_positions(
            getattr(self.broker, "positions", {}),
            names=getattr(self, "names", {}),
        )
        if result.get("created") or result.get("orphaned"):
            self.logger.warning(
                "[진입품질] 재시작 복구 created=%s orphaned=%s active=%s",
                result.get("created"), result.get("orphaned"), result.get("active"),
            )
    except Exception as exc:
        _warn_from_engine(self, f"재시작 포지션 대조 실패: {type(exc).__name__}: {exc}")


def _wrapped_buy(
    self: Any,
    symbol: str,
    quantity: int,
    quote: Any,
    reason: str,
    **kwargs: Any,
) -> Any:
    """원본 PAPER 매수 완료 뒤 실제 Fill/Position 값으로 관찰을 시작한다."""
    fill = _ORIG_BROKER_BUY(self, symbol, quantity, quote, reason, **kwargs)
    try:
        position = self.positions.get(symbol)
        if position is not None:
            TRACKER.on_entry(
                symbol=symbol,
                name=str(getattr(quote, "name", "") or symbol),
                entry_price=float(position.entry_price),
                entry_time=position.entry_time,
                quantity=int(position.quantity),
                entry_fee=float(position.entry_fee),
                entry_cost=float(position.entry_cost),
                target_price=float(position.target_price),
                pattern=str(getattr(position, "pattern", "")),
                entry_reason=str(reason),
                entry_order_id=str(getattr(fill, "order_id", "")),
                history_complete=True,
            )
    except Exception as exc:
        try:
            self.logger.warning("[진입품질] 매수 관찰 시작 실패: %s", exc)
        except Exception:
            pass
    return fill


def _wrapped_sell(self: Any, symbol: str, *args: Any, **kwargs: Any) -> Any:
    """원본 PAPER 매도를 방해하지 않고 실제 순손익으로 관찰을 종료한다."""
    position = self.positions.get(symbol)
    fill = _ORIG_BROKER_SELL(self, symbol, *args, **kwargs)
    try:
        TRACKER.close(
            symbol=symbol,
            exit_price=float(getattr(fill, "price", 0.0)),
            exit_reason=str(getattr(fill, "reason", "") or "청산"),
            now=getattr(fill, "timestamp", None) or datetime.now(KST),
            exit_order_id=str(getattr(fill, "order_id", "")),
            exit_fee=float(getattr(fill, "fee", 0.0)),
            exit_tax=float(getattr(fill, "tax", 0.0)),
            net_pnl=float(getattr(fill, "pnl", 0.0)),
            net_pnl_pct=float(getattr(fill, "pnl_pct", 0.0)),
            entry_price=float(getattr(fill, "entry_price", 0.0) or getattr(position, "entry_price", 0.0)),
            entry_time=getattr(fill, "entry_time", None) or getattr(position, "entry_time", None),
            quantity=int(getattr(fill, "quantity", 0) or getattr(position, "quantity", 0)),
            entry_cost=float(getattr(position, "entry_cost", 0.0)),
            pattern=str(getattr(fill, "pattern", "") or getattr(position, "pattern", "")),
        )
    except Exception as exc:
        try:
            self.logger.warning("[진입품질] 청산 관찰 저장 실패: %s", exc)
        except Exception:
            pass
    return fill


def _observation_time(quote: Any, kwargs: Mapping[str, Any]) -> datetime:
    candidate = kwargs.get("detection_time") or getattr(quote, "timestamp", None)
    if isinstance(candidate, datetime):
        if candidate.tzinfo is None:
            candidate = candidate.replace(tzinfo=timezone.utc)
        return candidate.astimezone(KST)
    return datetime.now(KST)


def _wrapped_tick_exit(self: Any, quote: Any, **kwargs: Any) -> Any:
    """목표 청산 전에 틱을 관측한다.

    V75.58은 원본 청산을 먼저 호출했고, 더구나 실제 실행 클래스가 아닌 최하위
    TradingEngine을 패치했다. 여기서는 실제 V7544Engine에 오버라이드하고
    관측→원본 청산 순서를 보장한다.
    """
    try:
        TRACKER.observe(
            str(getattr(quote, "symbol", "")),
            float(getattr(quote, "price", 0.0)),
            _observation_time(quote, kwargs),
            name=str(getattr(quote, "name", "")),
        )
    except Exception as exc:
        _warn_from_engine(self, f"틱 관찰 실패: {type(exc).__name__}: {exc}")
    return _ORIG_ACTIVE_TICK_EXIT(self, quote, **kwargs)


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def build_portfolio_quality_snapshot(engine: Any) -> dict[str, Any]:
    """신선한 시세만 사용해 열린 손실과 실현수익 잠식률을 계산한다."""
    open_pnl = 0.0
    open_loss_only = 0.0
    evaluated_count = 0
    unavailable_count = 0
    worst_symbol = "-"
    worst_pnl_pct = 0.0
    liquidation_value = float(getattr(engine.broker, "cash", 0.0))
    for symbol, position in getattr(engine.broker, "positions", {}).items():
        quote = getattr(engine, "latest_quotes", {}).get(symbol)
        current = float(getattr(quote, "price", 0.0)) if quote is not None else 0.0
        metrics = v7543.holding_metrics(
            position,
            current,
            engine.settings,
            quote_available=quote is not None,
            quote_timestamp=getattr(quote, "timestamp", None),
        )
        liquidation_value += float(metrics.get("proceeds", position.entry_cost))
        if not bool(metrics.get("quote_available", False)):
            unavailable_count += 1
            continue
        evaluated_count += 1
        pnl = float(metrics.get("pnl", 0.0))
        pnl_pct = float(metrics.get("pnl_pct", 0.0)) * 100.0
        open_pnl += pnl
        open_loss_only += min(0.0, pnl)
        if pnl_pct < worst_pnl_pct:
            worst_pnl_pct = pnl_pct
            worst_symbol = str(symbol)
        try:
            TRACKER.observe(
                symbol,
                current,
                getattr(quote, "timestamp", None) or datetime.now(KST),
                name=str(getattr(engine, "names", {}).get(symbol, symbol)),
            )
        except Exception:
            pass
    daily_realized = float(getattr(engine.broker, "daily_realized_pnl", 0.0))
    cumulative_realized = float(getattr(engine.broker, "realized_pnl", 0.0))
    loss_magnitude = abs(open_loss_only)
    daily_erosion = loss_magnitude / daily_realized * 100.0 if daily_realized > 0 else 0.0
    cumulative_erosion = loss_magnitude / cumulative_realized * 100.0 if cumulative_realized > 0 else 0.0
    return {
        "timestamp": datetime.now(KST).isoformat(),
        "open_count": len(getattr(engine.broker, "positions", {})),
        "evaluated_count": evaluated_count,
        "unavailable_count": unavailable_count,
        "open_pnl": round(open_pnl, 2),
        "open_loss_only": round(open_loss_only, 2),
        "daily_realized_pnl": round(daily_realized, 2),
        "cumulative_realized_pnl": round(cumulative_realized, 2),
        "equity": round(liquidation_value, 2),
        "daily_erosion_pct": round(daily_erosion, 3),
        "cumulative_erosion_pct": round(cumulative_erosion, 3),
        "worst_symbol": worst_symbol,
        "worst_pnl_pct": round(worst_pnl_pct, 4),
    }


def format_quality_console(snapshot: Mapping[str, Any], active_rows: list[dict[str, Any]]) -> list[str]:
    open_count = int(snapshot.get("open_count", 0))
    worst_pct = _finite(snapshot.get("worst_pnl_pct"))
    loss_only = _finite(snapshot.get("open_loss_only"))
    daily_erosion = _finite(snapshot.get("daily_erosion_pct"))
    cumulative_erosion = _finite(snapshot.get("cumulative_erosion_pct"))
    touched = sum(1 for row in active_rows if bool(row.get("target_touched")))
    incomplete = sum(1 for row in active_rows if not bool(row.get("history_complete", True)))
    lines = [
        "[진입품질 관찰] 자동매도 없음 | "
        f"보유={open_count} 목표터치관찰={touched} 재시작이력불완전={incomplete}",
        f"  열린손익={_finite(snapshot.get('open_pnl')):+,.0f}원 | "
        f"손실종목합계={loss_only:+,.0f}원 | 최악={worst_pct:+.2f}%({snapshot.get('worst_symbol','-')})",
        f"  실현수익 잠식률: 오늘={daily_erosion:.1f}% | 누적={cumulative_erosion:.1f}% "
        "(실현수익이 양수일 때만 계산)",
    ]
    alerts: list[str] = []
    if worst_pct <= -8.0:
        alerts.append("단일종목 -8% 이하 깊은 하락")
    elif worst_pct <= -5.0:
        alerts.append("단일종목 -5% 이하 하락")
    if daily_erosion >= 100.0:
        alerts.append("열린 손실이 오늘 실현수익 이상")
    elif daily_erosion >= 50.0:
        alerts.append("열린 손실이 오늘 실현수익의 50% 이상")
    if cumulative_erosion >= 100.0:
        alerts.append("열린 손실이 누적 실현수익 이상")
    if int(snapshot.get("unavailable_count", 0)):
        alerts.append(f"시세 미확인 {int(snapshot.get('unavailable_count', 0))}종목")
    if alerts:
        lines.append("  ⚠ 관찰경고(주문 개입 없음): " + " / ".join(alerts))
    return lines


def _wrapped_compact_report(self: Any) -> None:
    _ORIG_COMPACT_REPORT(self)
    try:
        snapshot = build_portfolio_quality_snapshot(self)
        TRACKER.append_portfolio_snapshot(snapshot)
        active_rows = TRACKER.snapshot_active()
        for line in format_quality_console(snapshot, active_rows):
            print(line)
        for warning in TRACKER.drain_warnings():
            print(f"  ⚠ 진입품질 기록 경고: {warning}")
    except Exception as exc:
        _warn_from_engine(self, f"5분 진입품질 요약 실패: {type(exc).__name__}: {exc}")


# 실제 실행 객체에만 좁게 적용한다. 매수·매도 원본은 항상 먼저/그대로 호출한다.
PAPER_BROKER.buy = _wrapped_buy
PAPER_BROKER.sell = _wrapped_sell
ACTIVE_ENGINE.__init__ = _wrapped_engine_init
ACTIVE_ENGINE._check_tick_exit = _wrapped_tick_exit
ACTIVE_ENGINE._compact_report = _wrapped_compact_report
v7557.v7556.v7525.VERSION = VERSION


def quality_report() -> str:
    TRACKER.flush()
    return analyze_quality(
        TRADES_V2_CSV,
        ACTIVE_STATE_JSON,
        PORTFOLIO_SNAPSHOTS_CSV,
        legacy_csv=LEGACY_V58_CSV,
    )


def self_check() -> list[tuple[str, bool, str]]:
    global TRACKER, _ORIG_ACTIVE_TICK_EXIT
    results: list[tuple[str, bool, str]] = []
    results.append((
        "V75.57기준SHA",
        sha256_file(BASE_DIR / V7557_FILENAME) == V7557_SHA256,
        V7557_SHA256[:16],
    ))
    results.append((
        "실제엔진틱계측적용",
        ACTIVE_ENGINE._check_tick_exit is _wrapped_tick_exit,
        f"{ACTIVE_ENGINE.__module__}.{ACTIVE_ENGINE.__name__}",
    ))
    results.append((
        "최하위엔진오패치아님",
        v753.TradingEngine._check_tick_exit is not _wrapped_tick_exit,
        "실행 V7544Engine만 패치",
    ))
    results.append(("브로커매수계측", PAPER_BROKER.buy is _wrapped_buy, "실제 PaperBroker"))
    results.append(("브로커매도계측", PAPER_BROKER.sell is _wrapped_sell, "실제 PaperBroker"))
    results.append((
        "자동손절없음보존",
        "NoStopTimedHeroEngine" in [cls.__name__ for cls in ACTIVE_ENGINE.__mro__]
        and "NoStopTimedHeroEngine" in _ORIG_ACTIVE_TICK_EXIT.__qualname__,
        _ORIG_ACTIVE_TICK_EXIT.__qualname__,
    ))

    with tempfile.TemporaryDirectory(prefix="v7559_selfcheck_") as tmp:
        root = Path(tmp)
        tracker = EntryQualityTrackerV2(
            root / "trades.csv", root / "active.json", root / "snapshots.csv", root / "anomaly.jsonl",
            persist_interval_sec=0.0,
        )
        base = datetime(2026, 7, 17, 9, 0, tzinfo=KST)
        tracker.on_entry(
            "058610", "에스피지", 10000.0, base,
            quantity=10, entry_fee=10.0, entry_cost=100010.0,
            target_price=10150.0, entry_order_id="PAPER-1",
        )
        tracker.observe("058610", 9700.0, base)
        tracker.observe("058610", 10150.0, base)
        closed = tracker.close(
            "058610", 10150.0, "예약목표+1.5%", base,
            exit_order_id="PAPER-2", exit_fee=10.0, exit_tax=20.0,
            net_pnl=1470.0, net_pnl_pct=0.0146985,
        )
        results.append((
            "목표전MAE및순손익",
            bool(closed and closed.target_touched and abs(closed.max_adverse_before_target + 3.0) < 0.01
                 and abs(closed.net_pnl_pct - 1.46985) < 0.001),
            f"MAE={getattr(closed,'max_adverse_before_target',0):.2f}% net={getattr(closed,'net_pnl_pct',0):.3f}%",
        ))
        # 재시작 복구 검증
        tracker.on_entry("005930", "삼성전자", 70000.0, base, quantity=3, entry_order_id="PAPER-3")
        tracker.observe("005930", 66500.0, base)
        tracker.flush()
        tracker2 = EntryQualityTrackerV2(
            root / "trades.csv", root / "active.json", root / "snapshots.csv", root / "anomaly.jsonl",
            persist_interval_sec=0.0,
        )
        active = tracker2.snapshot_active()
        results.append((
            "활성관찰재시작복구",
            len(active) == 1 and active[0]["symbol"] == "005930" and active[0]["mae_pct"] <= -5.0,
            str(active),
        ))
        report = analyze_quality(root / "trades.csv", root / "active.json", root / "snapshots.csv")
        results.append((
            "열린손실보고포함",
            "현재 열린 위험" in report and "-5.0% 이하 1종목" in report,
            "생존편향 완화",
        ))

    # 실제 래퍼 호출 순서: observe가 원본 청산 호출보다 먼저여야 한다.
    old_tracker = TRACKER
    old_original = _ORIG_ACTIVE_TICK_EXIT
    order: list[str] = []

    class ProbeTracker:
        def observe(self, *args: Any, **kwargs: Any) -> None:
            order.append("observe")

    class Quote:
        symbol = "000001"
        name = "테스트"
        price = 100.0
        timestamp = datetime(2026, 7, 17, 9, 1, tzinfo=KST)

    try:
        TRACKER = ProbeTracker()  # type: ignore[assignment]
        _ORIG_ACTIVE_TICK_EXIT = lambda _self, _quote, **_kwargs: order.append("original")  # type: ignore[assignment]
        _wrapped_tick_exit(object(), Quote())
    finally:
        TRACKER = old_tracker
        _ORIG_ACTIVE_TICK_EXIT = old_original
    results.append(("목표틱선관측순서", order == ["observe", "original"], str(order)))
    return results


def main() -> int:
    try:
        return int(v7557.main())
    finally:
        try:
            TRACKER.flush()
        except Exception:
            pass


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
