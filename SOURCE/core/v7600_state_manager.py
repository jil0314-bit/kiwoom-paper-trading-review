#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""STATE-001: deterministic, append-only stage transitions.

This module is intentionally independent from the running V76 engine until a
later, separately reviewed integration change.  It contains no broker, REST,
dashboard, VBS, or 0600 code.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any, Iterator, Mapping

_volume_spec = importlib.util.spec_from_file_location(
    "v7609_volume_prebuy", Path(__file__).with_name("v7609_volume_prebuy.py")
)
if _volume_spec is None or _volume_spec.loader is None:
    raise RuntimeError("5배급등거래 판정 모듈 누락")
_VOLUME5 = importlib.util.module_from_spec(_volume_spec)
_volume_spec.loader.exec_module(_VOLUME5)
VOLUME5_RULE = _VOLUME5.RULE_ID
validate_volume5 = _VOLUME5.validate_evidence


OBSERVED = "관찰"
INTEREST = "관심"
PREBUY = "매수직전"
HOLDING = "보유"
SOLD = "매도"
MARKET_HOLD = "시장보류"
REMOVED = "관찰해제"

SCANNER_STAGES = frozenset({OBSERVED, INTEREST, PREBUY, MARKET_HOLD, REMOVED})
TRADE_STAGES = frozenset({HOLDING, SOLD})
_ALLOWED: dict[str | None, frozenset[str]] = {
    None: frozenset({OBSERVED}),
    OBSERVED: frozenset({INTEREST, MARKET_HOLD, REMOVED}),
    INTEREST: frozenset({PREBUY, MARKET_HOLD, REMOVED}),
    PREBUY: frozenset({HOLDING, MARKET_HOLD, REMOVED}),
    HOLDING: frozenset({SOLD}),
    MARKET_HOLD: frozenset({OBSERVED, INTEREST, REMOVED}),
    REMOVED: frozenset({OBSERVED}),
    SOLD: frozenset(),
}


class TransitionRejected(ValueError):
    """Raised when a requested stage transition is structurally unsafe."""


@dataclass(frozen=True)
class TransitionRequest:
    signal_id: str
    symbol: str
    new_stage: str
    rule_id: str
    data_time: str
    decision_time: str
    engine_version: str
    reason: str
    evidence: Mapping[str, Any]
    quantity: int = 0
    paper_fill_id: str = ""


@dataclass(frozen=True)
class StateEvent:
    event_id: str
    signal_id: str
    symbol: str
    old_stage: str | None
    new_stage: str
    rule_id: str
    data_time: str
    decision_time: str
    engine_version: str
    reason: str
    evidence: Mapping[str, Any]
    quantity: int
    paper_fill_id: str
    previous_hash: str
    hash: str


def make_signal_id(
    *, engine_version: str, symbol: str, rule_id: str, data_time: str, new_stage: str
) -> str:
    """Make a stable event identity; no random values or wall-clock time."""

    material = "|".join(
        (engine_version, str(symbol).zfill(6), rule_id, data_time, new_stage)
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _event_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


class StateManager:
    """Single in-memory authority for stage transitions.

    A later integration must route every state write through this object.  It
    deliberately refuses to infer a new trading signal during restoration.
    """

    def __init__(self) -> None:
        self._events: list[StateEvent] = []
        self._stage_by_symbol: dict[str, str] = {}
        self._seen_signal_ids: set[str] = set()
        self._seen_paper_fill_ids: set[str] = set()

    @property
    def events(self) -> tuple[StateEvent, ...]:
        return tuple(self._events)

    def stage_of(self, symbol: str) -> str | None:
        return self._stage_by_symbol.get(str(symbol).zfill(6))

    def has_paper_fill(self, paper_fill_id: str) -> bool:
        return bool(paper_fill_id) and paper_fill_id in self._seen_paper_fill_ids

    def _apply(self, request: TransitionRequest, *, fill_authoritative: bool) -> StateEvent:
        symbol = str(request.symbol).zfill(6)
        if len(symbol) != 6 or not symbol.isdigit():
            raise TransitionRejected("종목코드는 6자리 숫자여야 합니다")
        if request.signal_id in self._seen_signal_ids:
            raise TransitionRejected("동일 signal_id 전이는 중복 처리할 수 없습니다")
        if request.new_stage not in _ALLOWED:
            raise TransitionRejected("알 수 없는 단계입니다")

        old_stage = self._stage_by_symbol.get(symbol)
        volume5 = request.rule_id in {VOLUME5_RULE, _VOLUME5.EARLY_RULE_ID}
        requested_prebuy = request.rule_id == "V7609_PREBUY_20260915"
        if requested_prebuy:
            import v7609_prebuy_rules as _PREBUY_RULES
        if requested_prebuy and (fill_authoritative or request.new_stage != PREBUY
                or old_stage not in {None, OBSERVED, INTEREST, PREBUY, MARKET_HOLD, REMOVED}
                or request.quantity != 0 or request.paper_fill_id
                or not _PREBUY_RULES.validate_evidence(request.evidence, request.data_time, request.decision_time)):
            raise TransitionRejected("요청 매수직전 편입 근거/상태 오류")
        if volume5 and (fill_authoritative or request.new_stage != PREBUY
                       or old_stage not in {None, OBSERVED, INTEREST, REMOVED}
                       or request.quantity != 0 or request.paper_fill_id
                       or not validate_volume5(request.evidence, request.data_time, request.decision_time)):
            raise TransitionRejected("5배급등거래 직접 편입 근거/상태 오류")
        if not fill_authoritative and not volume5 and not requested_prebuy and request.new_stage not in _ALLOWED.get(old_stage, frozenset()):
            raise TransitionRejected(f"허용되지 않은 전이: {old_stage} -> {request.new_stage}")
        if request.paper_fill_id and request.paper_fill_id in self._seen_paper_fill_ids:
            raise TransitionRejected("동일 PAPER 체결 ID는 중복 처리할 수 없습니다")
        if request.new_stage == HOLDING:
            if request.quantity <= 0 or not request.paper_fill_id:
                raise TransitionRejected("보유 전이에는 PAPER 체결 ID와 양수 수량이 필요합니다")
        if request.new_stage == SOLD:
            if old_stage != HOLDING or request.quantity <= 0 or not request.paper_fill_id:
                raise TransitionRejected("매도 전이에는 직전 보유와 PAPER 체결 ID가 필요합니다")
        if request.new_stage in SCANNER_STAGES and not request.evidence:
            raise TransitionRejected("스캐너 전이에는 근거 데이터가 필요합니다")

        previous_hash = self._events[-1].hash if self._events else ""
        body = {
            "signal_id": request.signal_id,
            "symbol": symbol,
            "old_stage": old_stage,
            "new_stage": request.new_stage,
            "rule_id": request.rule_id,
            "data_time": request.data_time,
            "decision_time": request.decision_time,
            "engine_version": request.engine_version,
            "reason": request.reason,
            "evidence": dict(request.evidence),
            "quantity": int(request.quantity),
            "paper_fill_id": request.paper_fill_id,
            "previous_hash": previous_hash,
        }
        digest = _event_hash(body)
        event = StateEvent(
            event_id=f"evt-{len(self._events) + 1:08d}", hash=digest, **body
        )
        self._events.append(event)
        self._stage_by_symbol[symbol] = request.new_stage
        self._seen_signal_ids.add(request.signal_id)
        if request.paper_fill_id:
            self._seen_paper_fill_ids.add(request.paper_fill_id)
        return event

    def apply(self, request: TransitionRequest) -> StateEvent:
        if request.new_stage in TRADE_STAGES:
            raise TransitionRejected(
                "보유·매도 단계는 확인된 PAPER 체결 경로로만 전이할 수 있습니다"
            )
        return self._apply(request, fill_authoritative=False)

    def apply_paper_fill(self, request: TransitionRequest) -> StateEvent:
        """Apply only a confirmed PAPER fill as holding/sold ground truth.

        A confirmed BUY may establish or refresh HOLDING even for a manual
        order or scale-in.  A SELL still requires the immediately preceding
        authoritative state to be HOLDING.
        """

        if request.new_stage not in TRADE_STAGES:
            raise TransitionRejected("PAPER 체결은 보유 또는 매도 단계만 만들 수 있습니다")
        if request.new_stage == SOLD and self.stage_of(request.symbol) != HOLDING:
            raise TransitionRejected("보유 단계가 없는 PAPER 매도 전이는 차단됩니다")
        return self._apply(request, fill_authoritative=True)

    def restore(
        self,
        events: list[Mapping[str, Any]],
        *,
        session_date: str,
        engine_version: str | None = None,
    ) -> None:
        """Rebuild only from valid same-day immutable events.

        Restoration does not accept missing evidence or fabricate a stage.  A
        full event-chain verifier is intentionally part of this operation.
        """

        self._events.clear()
        self._stage_by_symbol.clear()
        self._seen_signal_ids.clear()
        self._seen_paper_fill_ids.clear()
        previous_hash = ""
        for raw in events:
            data_time = str(raw.get("data_time", ""))
            if not data_time.startswith(session_date):
                continue
            if (
                engine_version is not None
                and str(raw.get("engine_version", "")) != str(engine_version)
            ):
                raise TransitionRejected("다른 엔진 버전 이벤트는 복원할 수 없습니다")
            if str(raw.get("previous_hash", "")) != previous_hash:
                raise TransitionRejected("이벤트 해시 연결이 끊겼습니다")
            expected_hash = _event_hash(
                {key: raw.get(key) for key in (
                    "signal_id", "symbol", "old_stage", "new_stage", "rule_id",
                    "data_time", "decision_time", "engine_version", "reason",
                    "evidence", "quantity", "paper_fill_id", "previous_hash",
                )}
            )
            if str(raw.get("hash", "")) != expected_hash:
                raise TransitionRejected("이벤트 해시가 일치하지 않습니다")
            request = TransitionRequest(
                signal_id=str(raw["signal_id"]), symbol=str(raw["symbol"]),
                new_stage=str(raw["new_stage"]), rule_id=str(raw["rule_id"]),
                data_time=data_time, decision_time=str(raw["decision_time"]),
                engine_version=str(raw["engine_version"]), reason=str(raw["reason"]),
                evidence=dict(raw.get("evidence", {})), quantity=int(raw.get("quantity", 0)),
                paper_fill_id=str(raw.get("paper_fill_id", "")),
            )
            event = (
                self.apply_paper_fill(request)
                if request.new_stage in TRADE_STAGES
                else self.apply(request)
            )
            if event.old_stage != raw.get("old_stage") or event.hash != expected_hash:
                raise TransitionRejected("이벤트 전이 내용이 일치하지 않습니다")
            previous_hash = event.hash

    def discard_last(self, event: StateEvent) -> None:
        """Undo only the just-created event when its durable append failed."""

        if not self._events or self._events[-1] != event:
            raise TransitionRejected("마지막 전이만 되돌릴 수 있습니다")
        retained = tuple(self._events[:-1])
        self._events.clear()
        self._stage_by_symbol.clear()
        self._seen_signal_ids.clear()
        self._seen_paper_fill_ids.clear()
        for previous in retained:
            request = TransitionRequest(
                signal_id=previous.signal_id,
                symbol=previous.symbol,
                new_stage=previous.new_stage,
                rule_id=previous.rule_id,
                data_time=previous.data_time,
                decision_time=previous.decision_time,
                engine_version=previous.engine_version,
                reason=previous.reason,
                evidence=previous.evidence,
                quantity=previous.quantity,
                paper_fill_id=previous.paper_fill_id,
            )
            rebuilt = (
                self.apply_paper_fill(request)
                if request.new_stage in TRADE_STAGES
                else self.apply(request)
            )
            if rebuilt.hash != previous.hash:
                raise TransitionRejected("전이 되돌리기 검증에 실패했습니다")


class JsonlEventLog:
    """Append-only JSONL store with a Windows file lock for future integration."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    @contextmanager
    def _locked_file(self) -> Iterator[Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+", encoding="utf-8") as handle:
            try:
                import msvcrt

                if self.path.stat().st_size == 0:
                    handle.write("\n")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                yield handle
            finally:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                except (OSError, UnboundLocalError):
                    pass

    def append(self, event: StateEvent) -> None:
        record = asdict(event)
        with self._locked_file() as handle:
            handle.seek(0, 2)
            handle.write(_canonical_json(record) + "\n")
            handle.flush()

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self._locked_file() as handle:
            handle.seek(0)
            return [json.loads(line) for line in handle if line.strip()]


def utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat()
