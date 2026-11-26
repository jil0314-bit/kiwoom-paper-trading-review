#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""IDEM-001: durable PAPER order-intent idempotency.

The guard owns no trading decisions, prices, quantities, or broker behavior.
It only serializes an already-approved PAPER order intent and records whether
the final fill boundary was reached.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import threading
from typing import Any, Iterator, Mapping


BLOCKING_STATUSES = frozenset({"CLAIMED", "ACCEPTING", "ACCEPTED"})


class DuplicateOrderIntent(ValueError):
    """Raised when the same durable order intent is submitted twice."""


@dataclass(frozen=True)
class OrderIdentity:
    engine_version: str
    signal_id: str
    order_intent_id: str
    side: str
    symbol: str
    source: str
    approval_id: str = ""


@dataclass(frozen=True)
class OrderClaim:
    identity: OrderIdentity
    payload_hash: str
    payload: Mapping[str, Any]
    evidence: Mapping[str, Any]


def stable_id(*parts: Any) -> str:
    material = "|".join(str(part) for part in parts)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def make_identity(
    *,
    engine_version: str,
    side: str,
    symbol: str,
    source: str,
    source_id: str,
    approval_id: str = "",
) -> OrderIdentity:
    normalized_side = str(side).upper()
    normalized_symbol = str(symbol).zfill(6)
    signal_id = stable_id(
        "SIGNAL", engine_version, normalized_side, normalized_symbol, source, source_id
    )
    order_intent_id = stable_id("PAPER_ORDER", normalized_side, signal_id)
    return OrderIdentity(
        engine_version=str(engine_version),
        signal_id=signal_id,
        order_intent_id=order_intent_id,
        side=normalized_side,
        symbol=normalized_symbol,
        source=str(source),
        approval_id=str(approval_id),
    )


def _payload_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class OrderIntentLedger:
    """Append-only claim ledger with process and Windows file locking."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._status_index: dict[str, dict[str, str]] = {}
        self._index_file = None
        self._index_offset = 0
        self._index_mtime_ns = 0

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

    @staticmethod
    def _records(handle: Any) -> list[dict[str, Any]]:
        handle.seek(0)
        return [json.loads(line) for line in handle if line.strip()]

    @staticmethod
    def _append(handle: Any, record: Mapping[str, Any]) -> None:
        handle.seek(0, 2)
        handle.write(
            json.dumps(
                dict(record), ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            + "\n"
        )
        handle.flush()

    @staticmethod
    def _latest_for(
        records: list[Mapping[str, Any]], order_intent_id: str
    ) -> Mapping[str, Any] | None:
        for record in reversed(records):
            if str(record.get("order_intent_id", "")) == order_intent_id:
                return record
        return None

    def _latest_locked(self, handle: Any, order_intent_id: str) -> Mapping[str, Any] | None:
        """Refresh compact status from appended rows under the existing file lock.

        Re-reading/parsing the whole evidence ledger at every order boundary
        delayed exits as the ledger grew.  The file remains the authority:
        every process reads all unseen rows before deciding, and a replacement
        or truncation invalidates this memory-only index. Malformed rows raise.
        """
        info = os.fstat(handle.fileno())
        identity = (info.st_dev, info.st_ino)
        if (identity != self._index_file or info.st_size < self._index_offset
                or (info.st_size == self._index_offset
                    and info.st_mtime_ns != self._index_mtime_ns)):
            self._status_index.clear()
            self._index_offset = 0
            self._index_file = identity
        handle.seek(self._index_offset)
        while True:
            line = handle.readline()
            if not line:
                break
            if not line.strip():
                continue
            record = json.loads(line)
            self._status_index[str(record["order_intent_id"])] = {
                "payload_hash": str(record.get("payload_hash", "")),
                "status": str(record.get("status", "")),
            }
        self._index_offset = handle.tell()
        self._index_mtime_ns = os.fstat(handle.fileno()).st_mtime_ns
        return self._status_index.get(str(order_intent_id))

    @staticmethod
    def _record(
        claim: OrderClaim,
        status: str,
        *,
        order_id: str = "",
        error: str = "",
        fill: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        identity = claim.identity
        now = datetime.now(timezone.utc).isoformat()
        payload = dict(claim.payload)
        evidence = dict(claim.evidence)
        data_time = str(
            evidence.get("data_time", "")
            or payload.get("data_time", "")
            or payload.get("signal_time", "")
        )
        return {
            "timestamp": now,
            "decision_time": now,
            "data_time": data_time,
            "engine_version": identity.engine_version,
            "old_stage": str(evidence.get("old_stage", "")),
            "new_stage": str(evidence.get("new_stage", "")),
            "rule_id": str(evidence.get("rule_id", "")),
            "reason": str(evidence.get("reason", "")),
            "signal_id": identity.signal_id,
            "order_intent_id": identity.order_intent_id,
            "side": identity.side,
            "symbol": identity.symbol,
            "source": identity.source,
            "approval_id": identity.approval_id,
            "payload_hash": claim.payload_hash,
            "payload": payload,
            "evidence": evidence,
            "status": status,
            "order_id": str(order_id),
            "fill": dict(fill or {}),
            "error": str(error),
        }

    def claim(
        self,
        identity: OrderIdentity,
        payload: Mapping[str, Any],
        *,
        evidence: Mapping[str, Any] | None = None,
    ) -> OrderClaim:
        claim = OrderClaim(
            identity=identity,
            payload_hash=_payload_hash(payload),
            payload=dict(payload),
            evidence=dict(evidence or {}),
        )
        with self._lock, self._locked_file() as handle:
            latest = self._latest_locked(handle, identity.order_intent_id)
            if latest is not None:
                if str(latest.get("payload_hash", "")) != claim.payload_hash:
                    raise DuplicateOrderIntent(
                        "동일 order_intent_id에 다른 주문 내용은 사용할 수 없습니다"
                    )
                if str(latest.get("status", "")) in BLOCKING_STATUSES:
                    raise DuplicateOrderIntent("동일 PAPER 주문 의도는 이미 처리 중이거나 접수됐습니다")
            self._append(handle, self._record(claim, "CLAIMED"))
        return claim

    def mark_accepting(self, claim: OrderClaim) -> None:
        self._change(claim, "ACCEPTING")

    def mark_accepted(
        self,
        claim: OrderClaim,
        order_id: str,
        *,
        fill: Mapping[str, Any] | None = None,
    ) -> None:
        if not str(order_id):
            raise ValueError("접수된 PAPER 주문 ID가 없습니다")
        self._change(
            claim,
            "ACCEPTED",
            order_id=str(order_id),
            fill=fill,
        )

    def release_before_acceptance(self, claim: OrderClaim, error: str = "") -> None:
        """Allow retry only when the final fill boundary was never reached."""

        with self._lock, self._locked_file() as handle:
            latest = self._latest_locked(handle, claim.identity.order_intent_id)
            if latest is None or str(latest.get("status", "")) != "CLAIMED":
                return
            self._append(
                handle, self._record(claim, "RELEASED_BEFORE_ACCEPTANCE", error=error)
            )

    def _change(
        self,
        claim: OrderClaim,
        status: str,
        *,
        order_id: str = "",
        error: str = "",
        fill: Mapping[str, Any] | None = None,
    ) -> None:
        with self._lock, self._locked_file() as handle:
            latest = self._latest_locked(handle, claim.identity.order_intent_id)
            if latest is None:
                raise ValueError("PAPER 주문 의도 선점 기록이 없습니다")
            if str(latest.get("payload_hash", "")) != claim.payload_hash:
                raise DuplicateOrderIntent("PAPER 주문 의도 내용이 변경됐습니다")
            current = str(latest.get("status", ""))
            allowed = {
                "ACCEPTING": {"CLAIMED"},
                "ACCEPTED": {"ACCEPTING"},
            }
            if current not in allowed.get(status, set()):
                if current == status:
                    return
                raise DuplicateOrderIntent(f"PAPER 주문 의도 상태 오류: {current} -> {status}")
            self._append(
                handle,
                self._record(
                    claim,
                    status,
                    order_id=order_id,
                    error=error,
                    fill=fill,
                ),
            )

    def latest_status(self, order_intent_id: str) -> str:
        if not self.path.exists():
            return ""
        with self._lock, self._locked_file() as handle:
            latest = self._latest_locked(handle, str(order_intent_id))
            return str(latest.get("status", "")) if latest is not None else ""

    def records(self) -> list[dict[str, Any]]:
        """Return an immutable snapshot for evidence verification only."""

        if not self.path.exists():
            return []
        with self._lock, self._locked_file() as handle:
            return [dict(record) for record in self._records(handle)]
