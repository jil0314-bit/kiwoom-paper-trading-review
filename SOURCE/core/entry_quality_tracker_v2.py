#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.59 진입품질 관찰기 v2.

자동 청산 판단에는 개입하지 않고, PAPER 포지션의 MAE/MFE, 목표 터치,
수중 체류시간, 실현손익 및 열린 손실의 실현수익 잠식 정도를 기록한다.

V75.58 대비 핵심 보강:
- 활성 관찰을 원자적 JSON으로 보존해 재시작 뒤 이어서 관찰한다.
- 청산 CSV에 수수료·세금·실제 순손익을 기록한다.
- 목표가 도달 틱을 청산 전에 관측할 수 있도록 호출 순서를 지원한다.
- 열린 손실을 보고서에 포함해 청산 거래만 보는 생존편향을 줄인다.
- 파일 오류를 조용히 삼키지 않고 경고 큐와 복구 파일로 남긴다.
"""
from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import statistics
import threading
import time
from typing import Any, Callable, Iterable, Mapping
import uuid

SCHEMA_VERSION = 2
DEPTH_LEVELS = (-1.0, -2.0, -3.0, -5.0, -8.0, -10.0)


def _iso(value: datetime | str | None) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value or "")


def _parse_dt(value: str | datetime | None) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return ordered[low]
    weight = position - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


@dataclass(slots=True)
class TradeObservation:
    schema_version: int
    trade_id: str
    symbol: str
    name: str
    entry_order_id: str
    entry_time: str
    entry_price: float
    quantity: int
    entry_fee: float
    entry_cost: float
    target_price: float
    pattern: str = ""
    entry_reason: str = ""
    highest_price: float = 0.0
    lowest_price: float = 0.0
    last_price: float = 0.0
    last_observed_time: str = ""
    target_touched: bool = False
    target_touched_time: str = ""
    max_adverse_before_target: float = 0.0
    ticks: int = 0
    underwater_since: str = ""
    longest_underwater_sec: float = 0.0
    depth_first_seen: dict[str, str] | None = None
    history_complete: bool = True
    recovery_note: str = ""
    exit_order_id: str = ""
    exit_time: str = ""
    exit_price: float = 0.0
    exit_fee: float = 0.0
    exit_tax: float = 0.0
    net_pnl: float = 0.0
    net_pnl_pct: float = 0.0
    exit_reason: str = ""
    target_filled: bool = False

    def __post_init__(self) -> None:
        if self.depth_first_seen is None:
            self.depth_first_seen = {}
        if self.highest_price <= 0:
            self.highest_price = self.entry_price
        if self.lowest_price <= 0:
            self.lowest_price = self.entry_price
        if self.last_price <= 0:
            self.last_price = self.entry_price

    def mfe_pct(self) -> float:
        if self.entry_price <= 0:
            return 0.0
        return (self.highest_price - self.entry_price) / self.entry_price * 100.0

    def mae_pct(self) -> float:
        if self.entry_price <= 0:
            return 0.0
        return (self.lowest_price - self.entry_price) / self.entry_price * 100.0

    def gross_exit_pct(self) -> float:
        if self.entry_price <= 0 or self.exit_price <= 0:
            return 0.0
        return (self.exit_price - self.entry_price) / self.entry_price * 100.0

    def open_pnl_pct(self) -> float:
        if self.entry_price <= 0 or self.last_price <= 0:
            return 0.0
        return (self.last_price - self.entry_price) / self.entry_price * 100.0

    def holding_seconds(self) -> float:
        start = _parse_dt(self.entry_time)
        end = _parse_dt(self.exit_time or self.last_observed_time)
        if start is None or end is None:
            return 0.0
        return max(0.0, (end - start).total_seconds())

    def time_to_target_seconds(self) -> float:
        start = _parse_dt(self.entry_time)
        end = _parse_dt(self.target_touched_time)
        if start is None or end is None:
            return 0.0
        return max(0.0, (end - start).total_seconds())


CSV_COLUMNS = [
    "schema_version", "trade_id", "symbol", "name", "entry_order_id", "entry_time",
    "entry_price", "quantity", "entry_fee", "entry_cost", "target_price", "pattern", "entry_reason",
    "mfe_pct", "mae_pct", "target_touched", "target_touched_time",
    "time_to_target_sec", "max_adverse_before_target_pct", "longest_underwater_sec",
    "exit_order_id", "exit_time", "exit_price", "exit_fee", "exit_tax",
    "gross_exit_pct", "net_pnl", "net_pnl_pct", "exit_reason", "target_filled",
    "ticks", "history_complete", "recovery_note",
]

SNAPSHOT_COLUMNS = [
    "timestamp", "open_count", "evaluated_count", "unavailable_count", "open_pnl",
    "open_loss_only", "daily_realized_pnl", "cumulative_realized_pnl", "equity",
    "daily_erosion_pct", "cumulative_erosion_pct", "worst_symbol", "worst_pnl_pct",
]


class EntryQualityTrackerV2:
    """스레드 안전·재시작 복구형 진입품질 관찰기."""

    def __init__(
        self,
        trades_csv: Path,
        active_state_json: Path,
        snapshots_csv: Path,
        anomalies_jsonl: Path,
        *,
        target_pct: float = 1.5,
        persist_interval_sec: float = 3.0,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self.trades_csv = Path(trades_csv)
        self.active_state_json = Path(active_state_json)
        self.snapshots_csv = Path(snapshots_csv)
        self.anomalies_jsonl = Path(anomalies_jsonl)
        self.target_pct = float(target_pct)
        self.persist_interval_sec = max(0.0, float(persist_interval_sec))
        self._logger = logger
        self._lock = threading.RLock()
        self._active: dict[str, TradeObservation] = {}
        self._warnings: list[str] = []
        self._dirty = False
        self._last_persist_mono = 0.0
        self._closed_trade_ids = self._load_closed_trade_ids()
        self._load_active_state()

    def set_logger(self, logger: Callable[[str], None] | None) -> None:
        with self._lock:
            self._logger = logger

    def _warn(self, message: str) -> None:
        text = str(message)
        self._warnings.append(text)
        if self._logger is not None:
            try:
                self._logger(text)
            except Exception:
                pass

    def drain_warnings(self) -> list[str]:
        with self._lock:
            out = list(self._warnings)
            self._warnings.clear()
            return out

    def _load_closed_trade_ids(self) -> set[str]:
        if not self.trades_csv.exists():
            return set()
        ids: set[str] = set()
        try:
            with self.trades_csv.open("r", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    trade_id = str(row.get("trade_id", "")).strip()
                    if trade_id:
                        ids.add(trade_id)
        except OSError as exc:
            self._warn(f"진입품질 CSV 읽기 실패: {exc}")
        return ids

    def _load_active_state(self) -> None:
        if not self.active_state_json.exists():
            return
        try:
            payload = json.loads(self.active_state_json.read_text(encoding="utf-8"))
            raw_active = payload.get("active", []) if isinstance(payload, dict) else []
            allowed = {item.name for item in fields(TradeObservation)}
            for raw in raw_active:
                if not isinstance(raw, dict):
                    continue
                cleaned = {k: v for k, v in raw.items() if k in allowed}
                obs = TradeObservation(**cleaned)
                if obs.trade_id not in self._closed_trade_ids:
                    self._active[obs.symbol] = obs
        except Exception as exc:
            backup = self.active_state_json.with_suffix(".corrupt.json")
            try:
                backup.write_bytes(self.active_state_json.read_bytes())
            except OSError:
                pass
            self._warn(f"활성 진입품질 상태 복구 실패: {type(exc).__name__}: {exc}")

    def _atomic_write_json(self, path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        with temp.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        temp.replace(path)

    def _persist_locked(self, *, force: bool = False) -> None:
        if not self._dirty and not force:
            return
        now_mono = time.monotonic()
        if not force and now_mono - self._last_persist_mono < self.persist_interval_sec:
            return
        payload = {
            "schema_version": SCHEMA_VERSION,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "active": [asdict(obs) for obs in self._active.values()],
        }
        try:
            self._atomic_write_json(self.active_state_json, payload)
            self._dirty = False
            self._last_persist_mono = now_mono
        except OSError as exc:
            self._warn(f"활성 진입품질 상태 저장 실패: {exc}")
            try:
                fallback = self.active_state_json.with_suffix(".recovery.json")
                self._atomic_write_json(fallback, payload)
            except OSError:
                pass

    def flush(self) -> None:
        with self._lock:
            self._persist_locked(force=True)

    def _new_trade_id(self, symbol: str, entry_order_id: str, entry_time: datetime | str) -> str:
        if entry_order_id:
            return f"{symbol}:{entry_order_id}"
        return f"{symbol}:{_iso(entry_time)}:{uuid.uuid4().hex[:10]}"

    def on_entry(
        self,
        symbol: str,
        name: str,
        entry_price: float,
        entry_time: datetime | str,
        *,
        quantity: int = 0,
        entry_fee: float = 0.0,
        entry_cost: float = 0.0,
        target_price: float | None = None,
        pattern: str = "",
        entry_reason: str = "",
        entry_order_id: str = "",
        history_complete: bool = True,
        recovery_note: str = "",
    ) -> TradeObservation:
        with self._lock:
            symbol = str(symbol)
            existing = self._active.get(symbol)
            trade_id = self._new_trade_id(symbol, str(entry_order_id), entry_time)
            if existing is not None:
                same_trade = (
                    (entry_order_id and existing.entry_order_id == entry_order_id)
                    or (
                        abs(existing.entry_price - float(entry_price)) < 1e-9
                        and existing.entry_time == _iso(entry_time)
                    )
                )
                if same_trade:
                    if name and existing.name in {"", existing.symbol}:
                        existing.name = str(name)
                    return existing
                self._record_anomaly_locked(
                    "ACTIVE_REPLACED",
                    {"symbol": symbol, "old_trade_id": existing.trade_id, "new_trade_id": trade_id},
                )
            price = max(0.0, float(entry_price))
            qty = max(0, int(quantity))
            cost = float(entry_cost) if entry_cost > 0 else price * qty + float(entry_fee)
            target = float(target_price) if target_price and target_price > 0 else price * (1 + self.target_pct / 100)
            obs = TradeObservation(
                schema_version=SCHEMA_VERSION,
                trade_id=trade_id,
                symbol=symbol,
                name=str(name or symbol),
                entry_order_id=str(entry_order_id),
                entry_time=_iso(entry_time),
                entry_price=price,
                quantity=qty,
                entry_fee=float(entry_fee),
                entry_cost=cost,
                target_price=target,
                pattern=str(pattern),
                entry_reason=str(entry_reason),
                highest_price=price,
                lowest_price=price,
                last_price=price,
                last_observed_time=_iso(entry_time),
                history_complete=bool(history_complete),
                recovery_note=str(recovery_note),
            )
            self._active[symbol] = obs
            self._dirty = True
            self._persist_locked(force=True)
            return obs

    def observe(
        self,
        symbol: str,
        price: float,
        now: datetime | str | None = None,
        *,
        name: str = "",
    ) -> TradeObservation | None:
        with self._lock:
            obs = self._active.get(str(symbol))
            value = _finite(price)
            if obs is None or value <= 0:
                return None
            current_time = _parse_dt(now) or datetime.now(timezone.utc)
            now_iso = current_time.isoformat()
            if name and obs.name in {"", obs.symbol}:
                obs.name = str(name)
            obs.ticks += 1
            obs.last_price = value
            obs.last_observed_time = now_iso
            obs.highest_price = max(obs.highest_price, value)
            obs.lowest_price = min(obs.lowest_price, value)
            pnl_pct = obs.open_pnl_pct()
            if not obs.target_touched:
                obs.max_adverse_before_target = min(obs.max_adverse_before_target, pnl_pct)
                if value >= obs.target_price:
                    obs.target_touched = True
                    obs.target_touched_time = now_iso
            for level in DEPTH_LEVELS:
                key = f"{level:.1f}"
                if pnl_pct <= level and key not in obs.depth_first_seen:
                    obs.depth_first_seen[key] = now_iso
            if value < obs.entry_price:
                if not obs.underwater_since:
                    obs.underwater_since = now_iso
                start = _parse_dt(obs.underwater_since)
                if start is not None:
                    obs.longest_underwater_sec = max(
                        obs.longest_underwater_sec,
                        max(0.0, (current_time - start).total_seconds()),
                    )
            elif obs.underwater_since:
                start = _parse_dt(obs.underwater_since)
                if start is not None:
                    obs.longest_underwater_sec = max(
                        obs.longest_underwater_sec,
                        max(0.0, (current_time - start).total_seconds()),
                    )
                obs.underwater_since = ""
            self._dirty = True
            self._persist_locked(force=obs.target_touched and obs.target_touched_time == now_iso)
            return obs

    def close(
        self,
        symbol: str,
        exit_price: float,
        exit_reason: str,
        now: datetime | str | None = None,
        *,
        exit_order_id: str = "",
        exit_fee: float = 0.0,
        exit_tax: float = 0.0,
        net_pnl: float = 0.0,
        net_pnl_pct: float = 0.0,
        entry_price: float = 0.0,
        entry_time: datetime | str | None = None,
        quantity: int = 0,
        entry_cost: float = 0.0,
        pattern: str = "",
        entry_reason: str = "",
        name: str = "",
    ) -> TradeObservation | None:
        with self._lock:
            symbol = str(symbol)
            when = _parse_dt(now) or datetime.now(timezone.utc)
            obs = self._active.get(symbol)
            if obs is None:
                if entry_price <= 0:
                    self._record_anomaly_locked(
                        "CLOSE_WITHOUT_ACTIVE",
                        {"symbol": symbol, "exit_order_id": exit_order_id, "reason": exit_reason},
                    )
                    return None
                obs = self.on_entry(
                    symbol,
                    name or symbol,
                    entry_price,
                    entry_time or when,
                    quantity=quantity,
                    entry_cost=entry_cost,
                    target_price=entry_price * (1 + self.target_pct / 100),
                    pattern=pattern,
                    entry_reason=entry_reason,
                    entry_order_id=f"RECOVERED-{exit_order_id or uuid.uuid4().hex[:8]}",
                    history_complete=False,
                    recovery_note="청산 시 관찰상태를 복구함; 중간 시세 이력 불완전",
                )
            self.observe(symbol, float(exit_price), when)
            obs = self._active.pop(symbol, obs)
            obs.exit_order_id = str(exit_order_id)
            obs.exit_time = when.isoformat()
            obs.exit_price = float(exit_price)
            obs.exit_fee = float(exit_fee)
            obs.exit_tax = float(exit_tax)
            obs.net_pnl = float(net_pnl)
            obs.net_pnl_pct = float(net_pnl_pct) * 100.0 if abs(float(net_pnl_pct)) <= 1.0 else float(net_pnl_pct)
            obs.exit_reason = str(exit_reason)
            obs.target_filled = str(exit_reason).startswith("예약목표")
            if obs.underwater_since:
                start = _parse_dt(obs.underwater_since)
                if start is not None:
                    obs.longest_underwater_sec = max(
                        obs.longest_underwater_sec,
                        max(0.0, (when - start).total_seconds()),
                    )
                obs.underwater_since = ""
            self._append_trade_locked(obs)
            self._dirty = True
            self._persist_locked(force=True)
            return obs

    def _append_trade_locked(self, obs: TradeObservation) -> None:
        if obs.trade_id in self._closed_trade_ids:
            return
        row = {
            "schema_version": SCHEMA_VERSION,
            "trade_id": obs.trade_id,
            "symbol": obs.symbol,
            "name": obs.name,
            "entry_order_id": obs.entry_order_id,
            "entry_time": obs.entry_time,
            "entry_price": f"{obs.entry_price:.4f}",
            "quantity": obs.quantity,
            "entry_fee": f"{obs.entry_fee:.4f}",
            "entry_cost": f"{obs.entry_cost:.4f}",
            "target_price": f"{obs.target_price:.4f}",
            "pattern": obs.pattern,
            "entry_reason": obs.entry_reason,
            "mfe_pct": f"{obs.mfe_pct():.4f}",
            "mae_pct": f"{obs.mae_pct():.4f}",
            "target_touched": "Y" if obs.target_touched else "N",
            "target_touched_time": obs.target_touched_time,
            "time_to_target_sec": f"{obs.time_to_target_seconds():.3f}",
            "max_adverse_before_target_pct": f"{obs.max_adverse_before_target:.4f}",
            "longest_underwater_sec": f"{obs.longest_underwater_sec:.3f}",
            "exit_order_id": obs.exit_order_id,
            "exit_time": obs.exit_time,
            "exit_price": f"{obs.exit_price:.4f}",
            "exit_fee": f"{obs.exit_fee:.4f}",
            "exit_tax": f"{obs.exit_tax:.4f}",
            "gross_exit_pct": f"{obs.gross_exit_pct():.4f}",
            "net_pnl": f"{obs.net_pnl:.4f}",
            "net_pnl_pct": f"{obs.net_pnl_pct:.4f}",
            "exit_reason": obs.exit_reason,
            "target_filled": "Y" if obs.target_filled else "N",
            "ticks": obs.ticks,
            "history_complete": "Y" if obs.history_complete else "N",
            "recovery_note": obs.recovery_note,
        }
        try:
            self.trades_csv.parent.mkdir(parents=True, exist_ok=True)
            new_file = not self.trades_csv.exists() or self.trades_csv.stat().st_size == 0
            with self.trades_csv.open("a", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
                if new_file:
                    writer.writeheader()
                writer.writerow(row)
                handle.flush()
                os.fsync(handle.fileno())
            self._closed_trade_ids.add(obs.trade_id)
        except OSError as exc:
            self._warn(f"진입품질 거래 CSV 저장 실패: {exc}")
            self._record_anomaly_locked("TRADE_CSV_WRITE_FAILED", row)

    def _record_anomaly_locked(self, kind: str, payload: Mapping[str, Any]) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "kind": str(kind),
            "payload": dict(payload),
        }
        try:
            self.anomalies_jsonl.parent.mkdir(parents=True, exist_ok=True)
            with self.anomalies_jsonl.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            self._warn(f"진입품질 복구기록 저장 실패: {exc}")

    def reconcile_positions(
        self,
        positions: Mapping[str, Any],
        *,
        names: Mapping[str, str] | None = None,
    ) -> dict[str, int]:
        """브로커 복구 포지션과 관찰 상태를 맞춘다.

        관찰 상태가 없는 보유종목은 불완전 이력으로 새로 등록한다. 관찰 상태만
        있고 실제 포지션이 없는 경우에는 자동으로 수익을 추정하지 않고 이상기록에
        남긴 뒤 활성 목록에서 제거한다.
        """
        names = names or {}
        with self._lock:
            created = 0
            orphaned = 0
            for symbol, position in positions.items():
                if symbol not in self._active:
                    self.on_entry(
                        symbol,
                        names.get(symbol, symbol),
                        float(getattr(position, "entry_price", 0.0)),
                        getattr(position, "entry_time", datetime.now(timezone.utc)),
                        quantity=int(getattr(position, "quantity", 0)),
                        entry_fee=float(getattr(position, "entry_fee", 0.0)),
                        entry_cost=float(getattr(position, "entry_cost", 0.0)),
                        target_price=float(getattr(position, "target_price", 0.0)) or None,
                        pattern=str(getattr(position, "pattern", "")),
                        entry_order_id=f"RECOVERED-{symbol}-{_iso(getattr(position, 'entry_time', ''))}",
                        history_complete=False,
                        recovery_note="프로그램 재시작 시 보유상태에서 복구; 재시작 전 MAE/MFE 일부 누락 가능",
                    )
                    created += 1
                else:
                    obs = self._active[symbol]
                    if names.get(symbol) and obs.name in {"", symbol}:
                        obs.name = names[symbol]
                    obs.quantity = int(getattr(position, "quantity", obs.quantity))
                    obs.entry_fee = float(getattr(position, "entry_fee", obs.entry_fee))
                    obs.entry_cost = float(getattr(position, "entry_cost", obs.entry_cost))
                    obs.target_price = float(getattr(position, "target_price", obs.target_price))
            for symbol in list(self._active):
                if symbol not in positions:
                    obs = self._active.pop(symbol)
                    self._record_anomaly_locked(
                        "ORPHAN_ACTIVE_STATE",
                        {"symbol": symbol, "trade_id": obs.trade_id, "last_price": obs.last_price},
                    )
                    orphaned += 1
            self._dirty = True
            self._persist_locked(force=True)
            return {"created": created, "orphaned": orphaned, "active": len(self._active)}

    def snapshot_active(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "trade_id": obs.trade_id,
                    "symbol": obs.symbol,
                    "name": obs.name,
                    "entry_price": obs.entry_price,
                    "last_price": obs.last_price,
                    "mfe_pct": round(obs.mfe_pct(), 4),
                    "mae_pct": round(obs.mae_pct(), 4),
                    "open_pnl_pct": round(obs.open_pnl_pct(), 4),
                    "target_touched": obs.target_touched,
                    "max_adverse_before_target": round(obs.max_adverse_before_target, 4),
                    "longest_underwater_sec": round(obs.longest_underwater_sec, 3),
                    "ticks": obs.ticks,
                    "history_complete": obs.history_complete,
                }
                for obs in self._active.values()
            ]

    def append_portfolio_snapshot(self, snapshot: Mapping[str, Any]) -> None:
        row = {key: snapshot.get(key, "") for key in SNAPSHOT_COLUMNS}
        try:
            self.snapshots_csv.parent.mkdir(parents=True, exist_ok=True)
            new_file = not self.snapshots_csv.exists() or self.snapshots_csv.stat().st_size == 0
            with self.snapshots_csv.open("a", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=SNAPSHOT_COLUMNS)
                if new_file:
                    writer.writeheader()
                writer.writerow(row)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            with self._lock:
                self._warn(f"포트폴리오 위험 스냅샷 저장 실패: {exc}")


def read_trade_rows(v2_csv: Path, legacy_csv: Path | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if Path(v2_csv).exists():
        try:
            with Path(v2_csv).open("r", encoding="utf-8-sig", newline="") as handle:
                for raw in csv.DictReader(handle):
                    rows.append(dict(raw))
        except OSError:
            pass
    if legacy_csv is not None and Path(legacy_csv).exists():
        try:
            with Path(legacy_csv).open("r", encoding="utf-8-sig", newline="") as handle:
                for index, raw in enumerate(csv.DictReader(handle), start=1):
                    rows.append({
                        "schema_version": "1",
                        "trade_id": f"LEGACY-{index}-{raw.get('종목', '')}-{raw.get('매수시각', '')}",
                        "symbol": raw.get("종목", ""),
                        "name": raw.get("종목명", ""),
                        "entry_time": raw.get("매수시각", ""),
                        "entry_price": raw.get("매수가", "0"),
                        "target_price": raw.get("목표가", "0"),
                        "pattern": "",
                        "entry_reason": "V75.58 구형자료",
                        "mfe_pct": raw.get("MFE최대상승%", "0"),
                        "mae_pct": raw.get("MAE최대하락%", "0"),
                        "target_touched": raw.get("목표도달", "N"),
                        "max_adverse_before_target_pct": raw.get("도달전최대하락%", "0"),
                        "exit_time": raw.get("청산시각", ""),
                        "exit_price": raw.get("청산가", "0"),
                        "gross_exit_pct": raw.get("청산손익%", "0"),
                        "net_pnl_pct": raw.get("청산손익%", "0"),
                        "exit_reason": raw.get("청산사유", ""),
                        "target_filled": "Y" if raw.get("청산사유", "").startswith("예약목표") else "N",
                        "ticks": raw.get("관측수", "0"),
                        "history_complete": "Y",
                        "recovery_note": "V75.58 구형 CSV; 수수료·세금 순손익 미포함",
                    })
        except OSError:
            pass
    return rows


def _latest_snapshot(path: Path) -> dict[str, str]:
    if not Path(path).exists():
        return {}
    last: dict[str, str] = {}
    try:
        with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                last = dict(row)
    except OSError:
        return {}
    return last


def analyze_quality(
    trades_csv: Path,
    active_state_json: Path,
    snapshots_csv: Path,
    *,
    legacy_csv: Path | None = None,
) -> str:
    rows = read_trade_rows(trades_csv, legacy_csv)
    active: list[dict[str, Any]] = []
    try:
        payload = json.loads(Path(active_state_json).read_text(encoding="utf-8"))
        active = [dict(item) for item in payload.get("active", []) if isinstance(item, dict)]
    except (OSError, ValueError, TypeError):
        active = []

    lines = ["=" * 78, "진입품질 리포트 V2 — 자동손절 없는 PAPER 관찰", "=" * 78]
    lines.append(f"완료 거래 {len(rows)}건 | 현재 보유 관찰 {len(active)}건")
    complete = sum(1 for row in rows if str(row.get("history_complete", "Y")) == "Y")
    if rows:
        lines.append(f"완전한 시세이력 {complete}/{len(rows)}건 | 재시작·복구 불완전 {len(rows)-complete}건")
    if len(rows) < 30:
        lines.append("⚠ 표본 30건 미만: 손절 수준이나 전략 변경의 근거로 사용하기 이릅니다.")
    elif len(rows) < 100:
        lines.append("ℹ 표본 100건 미만: 초기 경향으로만 해석하고 장세별 분리를 계속해야 합니다.")

    if rows:
        touched = [row for row in rows if str(row.get("target_touched", "N")) == "Y"]
        not_touched = [row for row in rows if str(row.get("target_touched", "N")) != "Y"]
        net_pcts = [_finite(row.get("net_pnl_pct")) for row in rows]
        maes = [_finite(row.get("mae_pct")) for row in rows]
        mfes = [_finite(row.get("mfe_pct")) for row in rows]
        lines.append("\n[전체 성과]")
        lines.append(f"  목표가 터치 {len(touched)}/{len(rows)}건 ({len(touched)/len(rows)*100:.1f}%)")
        lines.append(f"  거래당 순손익률 평균 {statistics.fmean(net_pcts):+.2f}% | 중앙값 {_percentile(net_pcts, 0.5):+.2f}%")
        lines.append(f"  MAE 중앙 {_percentile(maes,0.5):+.2f}% · 하위10% {_percentile(maes,0.1):+.2f}% · 최악 {min(maes):+.2f}%")
        lines.append(f"  MFE 중앙 {_percentile(mfes,0.5):+.2f}% · 상위10% {_percentile(mfes,0.9):+.2f}%")

        lines.append("\n[빠졌다가 목표까지 회복한 거래]")
        if touched:
            adverse = [_finite(row.get("max_adverse_before_target_pct")) for row in touched]
            lines.append(
                f"  목표 터치 전 최대하락: 중앙 {_percentile(adverse,0.5):+.2f}% | "
                f"75% {_percentile(adverse,0.25):+.2f}% | 90% {_percentile(adverse,0.1):+.2f}% | 최악 {min(adverse):+.2f}%"
            )
        else:
            lines.append("  아직 목표 터치 완료 거래가 없습니다.")

        lines.append("\n[하락 깊이별 목표 회복률 — 열린 거래는 별도 표시]")
        for level in (-1.0, -2.0, -3.0, -5.0, -8.0):
            crossed = [row for row in rows if _finite(row.get("mae_pct")) <= level]
            recovered = [row for row in crossed if str(row.get("target_touched", "N")) == "Y"]
            open_crossed = 0
            for item in active:
                entry = _finite(item.get("entry_price"))
                low = _finite(item.get("lowest_price"))
                mae = (low-entry)/entry*100 if entry > 0 and low > 0 else 0.0
                if mae <= level:
                    open_crossed += 1
            rate = (len(recovered) / len(crossed) * 100.0) if crossed else 0.0
            lines.append(
                f"  {level:>5.1f}% 도달 완료거래 {len(crossed):>3}건 중 목표회복 {len(recovered):>3}건 "
                f"({rate:>5.1f}%) | 아직 열린 거래 {open_crossed}건"
            )

        lines.append("\n[가상 손절선의 관측상 상충관계]")
        for stop in (-2.0, -3.0, -5.0, -8.0):
            recovered_but_cut = sum(
                1 for row in touched
                if _finite(row.get("max_adverse_before_target_pct")) <= stop
            )
            non_recovered_cut = sum(
                1 for row in not_touched
                if _finite(row.get("mae_pct")) <= stop
            )
            lines.append(
                f"  손절 {stop:.0f}% 가정: 이후 목표에 닿은 {recovered_but_cut}건도 잘렸을 수 있음 / "
                f"목표 미도달 손실 {non_recovered_cut}건은 더 일찍 제한했을 수 있음"
            )
        lines.append("  ※ 호가·슬리피지·갭을 재현한 체결 백테스트가 아니라 관측 경로 비교입니다.")

        lines.append("\n[손실이 커진 조건 찾기 — 패턴·진입시간별]")
        pattern_groups: dict[str, list[dict[str, Any]]] = {}
        time_groups: dict[str, list[dict[str, Any]]] = {
            "09~10시": [], "10~12시": [], "12~14시": [], "14시이후": [], "시간미상": [],
        }
        for row in rows:
            pattern = str(row.get("pattern", "") or "미분류")
            pattern_groups.setdefault(pattern, []).append(row)
            parsed = _parse_dt(str(row.get("entry_time", "")))
            if parsed is None:
                band = "시간미상"
            elif parsed.hour < 10:
                band = "09~10시"
            elif parsed.hour < 12:
                band = "10~12시"
            elif parsed.hour < 14:
                band = "12~14시"
            else:
                band = "14시이후"
            time_groups[band].append(row)
        for pattern, group in sorted(pattern_groups.items(), key=lambda item: (-len(item[1]), item[0])):
            if len(group) < 2 and len(rows) >= 10:
                continue
            target_count = sum(1 for row in group if str(row.get("target_touched", "N")) == "Y")
            avg_net = statistics.fmean(_finite(row.get("net_pnl_pct")) for row in group)
            worst_mae = min(_finite(row.get("mae_pct")) for row in group)
            lines.append(
                f"  패턴 {pattern}: {len(group)}건 | 목표터치 {target_count/len(group)*100:.1f}% | "
                f"평균순손익 {avg_net:+.2f}% | 최악MAE {worst_mae:+.2f}%"
            )
        for band, group in time_groups.items():
            if not group:
                continue
            avg_net = statistics.fmean(_finite(row.get("net_pnl_pct")) for row in group)
            worst_mae = min(_finite(row.get("mae_pct")) for row in group)
            lines.append(f"  시간 {band}: {len(group)}건 | 평균순손익 {avg_net:+.2f}% | 최악MAE {worst_mae:+.2f}%")
        deepest = sorted(rows, key=lambda row: _finite(row.get("mae_pct")))[:5]
        if deepest:
            lines.append("  깊은 하락 사례:")
            for row in deepest:
                lines.append(
                    f"    {row.get('name') or row.get('symbol')} | MAE {_finite(row.get('mae_pct')):+.2f}% | "
                    f"순손익 {_finite(row.get('net_pnl_pct')):+.2f}% | {row.get('pattern') or '미분류'} | "
                    f"{row.get('entry_reason') or '-'}"
                )

    lines.append("\n[현재 열린 위험 — 생존편향 방지]")
    if active:
        open_rows: list[tuple[float, str, str]] = []
        incomplete = 0
        for item in active:
            entry = _finite(item.get("entry_price"))
            last = _finite(item.get("last_price"))
            pct = (last-entry)/entry*100 if entry > 0 and last > 0 else 0.0
            open_rows.append((pct, str(item.get("symbol", "")), str(item.get("name", ""))))
            incomplete += int(not bool(item.get("history_complete", True)))
        open_rows.sort()
        lines.append(f"  최악 열린 손익률 {open_rows[0][0]:+.2f}% ({open_rows[0][2] or open_rows[0][1]})")
        for level in (-2.0, -3.0, -5.0, -8.0, -10.0):
            count = sum(1 for pct, _, _ in open_rows if pct <= level)
            lines.append(f"  {level:>5.1f}% 이하 {count}종목")
        if incomplete:
            lines.append(f"  ⚠ 재시작 전 경로가 불완전한 보유 {incomplete}종목")
    else:
        lines.append("  현재 열린 관찰 포지션이 없습니다.")

    latest = _latest_snapshot(snapshots_csv)
    if latest:
        lines.append("\n[실현수익 잠식 감시 — 최근 5분 스냅샷]")
        lines.append(
            f"  미실현손익 {_finite(latest.get('open_pnl')):+,.0f}원 | 손실 종목 합계 "
            f"{_finite(latest.get('open_loss_only')):+,.0f}원"
        )
        daily = _finite(latest.get("daily_erosion_pct"))
        cumulative = _finite(latest.get("cumulative_erosion_pct"))
        lines.append(f"  오늘 실현수익 잠식률 {daily:.1f}% | 누적 실현수익 잠식률 {cumulative:.1f}%")
        lines.append(
            f"  최악 보유 {_finite(latest.get('worst_pnl_pct')):+.2f}% "
            f"({latest.get('worst_symbol','-')})"
        )

    lines.append("\n[해석 원칙]")
    lines.append("  자동손절을 켜거나 끄는 결론은 이 보고서가 자동으로 내리지 않습니다.")
    lines.append("  최소 100건 이상, 상승장·하락장·급락일을 분리한 뒤 기대손익과 최대손실을 함께 비교해야 합니다.")
    return "\n".join(lines)
