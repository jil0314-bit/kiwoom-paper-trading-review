#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.82 PAPER — faster forming-3m decisions and exact command identity.

V75.81 remains the byte-pinned parent.  This module changes only three paths:

* publish the 1-minute projection into the dashboard immediately and enter on
  the first qualified two-minute projection instead of waiting for a duplicate
  polling hit;
* resolve stock names case-insensitively and build price warnings from a fresh
  quote for the exact resolved symbol only;
* attach a vertical scrollbar to the request-preview history tree.

The confirmed V75.79 HeroMoon link, approved dashboard layout, PAPER +1.5%
target, -5% stop and every other V75.81 risk/order path are inherited intact.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import statistics
import sys
from types import ModuleType, SimpleNamespace
from typing import Any, Sequence


VERSION = "V75.82-R1-PREEMPTIVE-INTRABAR-SAFE-PAPER"
BASE_DIR = Path(__file__).resolve().parent
PARENT_FILE = "bot_v75_81_complete_profit_guard_paper.py"
PARENT_SHA256 = "3cfa0010455634515b23f390a141b7e098ea74c57a24103578552b282b238fa5"
PREEMPTIVE_ENTRY_MIN_SEC = 105.0
PREEMPTIVE_ENTRY_MAX_SEC = 178.0
REQUEST_PREVIEW_COLUMNS = ("time", "type", "summary", "status")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_parent() -> ModuleType:
    path = BASE_DIR / PARENT_FILE
    actual = sha256_file(path)
    if actual.lower() != PARENT_SHA256.lower():
        raise RuntimeError(
            f"{PARENT_FILE} SHA256 불일치\n기대: {PARENT_SHA256}\n실제: {actual}"
        )
    spec = importlib.util.spec_from_file_location("v7582_immutable_v7581_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"{PARENT_FILE} 로드 실패")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


parent = _load_parent()
v57 = parent.v57
v7556 = v57.v7556
v7525 = parent.v7525
v753 = parent.v753
v73 = parent.v73
v7514 = parent.v7514
KST = parent.KST
diag = parent.diag

BaseEngine = parent.V7580GemEngine
GemDecision = parent.GemDecision
BLOCKED = parent.BLOCKED
WATCH = parent.WATCH
READY = parent.READY
FIRST_BUY = parent.FIRST_BUY
CONFIRM_BUY = parent.CONFIRM_BUY
HARD_STOP_PCT = parent.HARD_STOP_PCT
TAKE_PROFIT_PCT = parent.TAKE_PROFIT_PCT


@dataclass(frozen=True, slots=True)
class PreemptivePlan:
    dashboard_stage: str
    enter_now: bool
    reason: str


def preemptive_plan(projection: Any, *, elapsed_seconds: float, internal_count: int) -> PreemptivePlan:
    """Turn a forming-three-minute projection into an immediate UI/order state.

    Two different one-minute records are the persistence proof.  Requiring the
    same REST snapshot a second time was redundant and caused the V75.81 lag.
    The existing projection still rejects a late one-minute selling collapse,
    missing source bars, fresh-low failure and other reviewed false positives.
    """
    elapsed = max(0.0, float(elapsed_seconds))
    decision = getattr(projection, "decision", None)
    stage = str(getattr(projection, "stage", WATCH))
    eligible = bool(getattr(projection, "eligible", False))

    if stage == BLOCKED:
        return PreemptivePlan("시장보류", False, "1분내부센서_구조붕괴·데이터차단")
    if decision is None:
        return PreemptivePlan("관찰후보", False, "형성중3분봉_관찰")

    decision_stage = str(getattr(decision, "stage", WATCH))
    score = float(getattr(decision, "score", 0.0) or 0.0)
    if eligible:
        dashboard_stage = "매수직전"
    elif decision_stage in {FIRST_BUY, CONFIRM_BUY}:
        dashboard_stage = "매수가능성"
    elif decision_stage == READY and score >= 82.0:
        dashboard_stage = "매수가능성"
    elif decision_stage == READY:
        dashboard_stage = "관심종목"
    else:
        dashboard_stage = "관찰후보"

    time_ready = PREEMPTIVE_ENTRY_MIN_SEC <= elapsed < PREEMPTIVE_ENTRY_MAX_SEC
    enter_now = eligible and int(internal_count) >= 2 and time_ready
    if enter_now:
        reason = "서로다른1분봉2개로_3분봉마감선제판단"
    elif eligible:
        reason = f"선제조건충족_진입시각대기_{elapsed:.0f}초"
    else:
        reason = "형성중3분봉_단계즉시상향"
    return PreemptivePlan(dashboard_stage, enter_now, reason)


def _case_insensitive_named_mentions(
    text: str, known_names: dict[str, str]
) -> list[tuple[int, int, str, str]]:
    """Find canonical names without treating `sk하이닉스` as another stock."""
    candidates: list[tuple[int, int, str, str]] = []
    for name in sorted(known_names, key=len, reverse=True):
        if not name:
            continue
        for match in re.finditer(re.escape(str(name)), str(text), flags=re.IGNORECASE):
            candidates.append((match.start(), match.end(), str(name), str(known_names[name])))
    candidates.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    chosen: list[tuple[int, int, str, str]] = []
    for item in candidates:
        if any(not (item[1] <= old[0] or item[0] >= old[1]) for old in chosen):
            continue
        chosen.append(item)
    return sorted(chosen)


_ORIGINAL_COMPILE_COLLABORATION_INPUT = v7556.compile_collaboration_input
_FRESH_EXACT_PRICE_HINT: dict[str, float] = {}


def _fresh_quote_price(quote: Any, *, now: datetime, max_age_sec: float) -> float | None:
    """Return a usable price only when the quote belongs to the current window."""
    try:
        price = float(getattr(quote, "price"))
        timestamp = v73.v754.normalize_kst(getattr(quote, "timestamp"))
        age = max(0.0, (v73.v754.normalize_kst(now) - timestamp).total_seconds())
    except (AttributeError, TypeError, ValueError):
        return None
    if price <= 0.0 or age > max(1.0, float(max_age_sec)):
        return None
    return price


def _settings_loader_identity() -> str:
    """Identify the runtime owner that actually activates the PAPER limit."""
    loader = v753.Settings.load
    function = getattr(loader, "__func__", loader)
    module = str(getattr(function, "__module__", ""))
    qualname = str(getattr(function, "__qualname__", getattr(function, "__name__", "")))
    return f"{module}.{qualname}".strip(".")


def _manifest_includes_intraday_command() -> bool:
    """The command text is non-executable, but must still survive packaging."""
    path = BASE_DIR.parent / "chain_manifest.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return "SOURCE/장중명령.txt" in dict(payload.get("files", {}))
    except (OSError, ValueError, TypeError):
        return False


def compile_collaboration_input_v7582(
    text: str,
    *,
    selected_symbol: str = "",
    selected_name: str = "",
    known_names: dict[str, str] | None = None,
) -> Any:
    """Compile using an exact resolved symbol and never another row's price."""
    draft = _ORIGINAL_COMPILE_COLLABORATION_INPUT(
        text,
        selected_symbol=selected_symbol,
        selected_name=selected_name,
        known_names=known_names,
    )
    # The parent warning was computed before this wrapper.  Rebuild it solely
    # from the fresh quote keyed by the final, exact symbol.
    draft.warnings = [
        item
        for item in list(getattr(draft, "warnings", []))
        if not (str(item).startswith("입력가 ") and "현재가 " in str(item))
    ]
    if str(getattr(draft, "action", "")) == "BUY_CONDITION" and getattr(draft, "buy_price", None) is not None:
        symbol = str(getattr(draft, "symbol", ""))
        current = _FRESH_EXACT_PRICE_HINT.get(symbol)
        if current is None:
            label = str(getattr(draft, "name", "") or symbol or "해당 종목")
            draft.warnings.append(
                f"{label} 현재가를 아직 새로 받지 못해 다른 종목 가격을 대신 사용하지 않았습니다."
            )
        else:
            buy_price = float(draft.buy_price)
            ratio = buy_price / current
            if ratio >= float(v7556.OUTLIER_RATIO_HIGH) or ratio <= float(v7556.OUTLIER_RATIO_LOW):
                draft.warnings.append(
                    f"입력가 {buy_price:,.0f}원은 {draft.name or symbol} 현재가 "
                    f"{current:,.0f}원의 {ratio:.1f}배입니다. 등록 전에 다시 확인합니다."
                )
    draft.warnings = list(dict.fromkeys(draft.warnings))
    return draft


def _is_request_preview(master: Any, columns: Sequence[str]) -> bool:
    if tuple(columns) != REQUEST_PREVIEW_COLUMNS:
        return False
    try:
        return str(master.cget("text")) == "형 요청 해석결과 미리보기"
    except Exception:
        return False


_TREEVIEW_PATCHED = False
_TREEVIEW_PATCH_ERROR = ""
try:
    from tkinter import ttk as _ttk

    _ORIGINAL_TREEVIEW = _ttk.Treeview

    class V7582PreviewTreeview(_ORIGINAL_TREEVIEW):
        """Normal Treeview plus one scrollbar for the request-history table."""

        def __init__(self, master: Any = None, **kwargs: Any) -> None:
            columns = tuple(kwargs.get("columns", ()))
            super().__init__(master, **kwargs)
            self._v7582_preview_scrollbar = None
            if not _is_request_preview(master, columns):
                return
            scrollbar = _ttk.Scrollbar(master, orient="vertical", command=self.yview)
            self.configure(yscrollcommand=scrollbar.set)
            scrollbar.grid(row=4, column=4, sticky="ns", pady=(0, 2))
            try:
                master.columnconfigure(4, weight=0)
            except Exception:
                pass
            self._v7582_preview_scrollbar = scrollbar

    V7582PreviewTreeview._v7582_patch = True  # type: ignore[attr-defined]
    _ttk.Treeview = V7582PreviewTreeview
    _TREEVIEW_PATCHED = True
except Exception as exc:
    _TREEVIEW_PATCH_ERROR = f"{type(exc).__name__}: {exc}"
    V7582PreviewTreeview = None  # type: ignore[assignment,misc]


class V7582PreemptiveIntrabarEngine(BaseEngine):
    """V75.81 engine with non-lagging projection publication and entry."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._v7582_intrabar_bucket: dict[str, datetime] = {}
        self._v7582_intrabar_signature: dict[str, str] = {}
        self._v7582_order_attempt_bucket: dict[str, datetime] = {}
        super().__init__(*args, **kwargs)
        self.logger.warning(
            "[%s] V75.81 전 기능 고정 | 1분봉2개→형성중3분 선제단계·진입 | "
            "종목명 대소문자 무관·동일코드 최신현재가만 사용 | 요청미리보기 스크롤",
            VERSION,
        )
        self.logger.warning(
            "[정정·출처] PAPER 일일손실한도는 V75.16 Settings.load 패치부터 활성 | "
            "현재로더=%s | V75.81·82는 실제 런타임 활성 여부를 회귀검증",
            _settings_loader_identity(),
        )
        self.logger.warning(
            "[상한주의] 동시보유 %d종목은 수익최적값이 아니라 기본 안전상한 | "
            "상관계수·시장별 표본 검증 전 최적값 주장 금지",
            int(parent.MAX_CONCURRENT_POSITIONS),
        )

    def _gui_snapshot(self) -> dict[str, Any]:
        snapshot = super()._gui_snapshot()
        fresh: dict[str, float] = {}
        now = datetime.now(KST)
        max_age = float(getattr(self.settings, "max_quote_age_sec", 10.0) or 10.0)
        for symbol, quote in dict(getattr(self, "latest_quotes", {})).items():
            price = _fresh_quote_price(quote, now=now, max_age_sec=max_age)
            if re.fullmatch(r"\d{6}", str(symbol)) and price is not None:
                fresh[str(symbol)] = price
        _FRESH_EXACT_PRICE_HINT.clear()
        _FRESH_EXACT_PRICE_HINT.update(fresh)
        # The legacy parser map is kept for compatibility, but stale values are
        # removed so it cannot silently supply another cycle's price.
        v7556._V7555_PRICE_HINT.clear()
        v7556._V7555_PRICE_HINT.update(fresh)
        return snapshot

    def _publish_intrabar_stage(
        self,
        symbol: str,
        completed: Sequence[Any],
        partial: Any,
        projection: Any,
        plan: PreemptivePlan,
        *,
        now: datetime,
        bucket: datetime,
    ) -> None:
        decision = getattr(projection, "decision", None)
        old = getattr(self, "_supply_potential", {}).get(symbol)
        if decision is None:
            # If this same forming bucket was previously promoted and then
            # lost its structure, remove the high category immediately.
            if self._v7582_intrabar_bucket.get(symbol) == bucket and old is not None:
                downgrade_signature = f"{bucket.isoformat()}|시장보류|구조이탈"
                if self._v7582_intrabar_signature.get(symbol) == downgrade_signature:
                    return
                downgraded = replace(
                    old,
                    stage="시장보류",
                    updated_at=now,
                    strict_missing=("형성중3분봉구조이탈",),
                    source=f"{old.source}|1분센서구조이탈",
                )
                self._supply_potential[symbol] = downgraded
                self._v7582_intrabar_signature[symbol] = downgrade_signature
                self._save_supply_potential()
            return

        proxy = SimpleNamespace(bars={symbol: [*completed, partial]})
        item, _reason = parent.assess_v7580(
            proxy,
            symbol,
            loose=self.loose_supply_config,
            strict=self.supply_exhaustion_config,
            now=now,
            name=self.names.get(symbol, symbol),
            source=self._candidate_source(symbol),
        )
        if item is None:
            return

        missing_map = {
            "관찰후보": ("거래바닥·지지확인대기",),
            "관심종목": ("5봉선회복·저점방어확인중",),
            "매수가능성": ("형성중3분봉마감확인중",),
            "매수직전": (),
            "시장보류": (str(getattr(projection, "reason", "구조차단")),),
        }
        detected_at = old.detected_at if old is not None else now
        item = replace(
            item,
            stage=plan.dashboard_stage,
            score=max(float(item.score), float(getattr(projection, "score", 0.0) or 0.0)),
            detected_at=detected_at,
            updated_at=now,
            expires_at=now + timedelta(minutes=24),
            strict_missing=missing_map[plan.dashboard_stage],
            source=f"{item.source}|1분센서→형성중3분예측|{plan.reason}",
        )
        signature = (
            f"{bucket.isoformat()}|{item.stage}|{getattr(decision, 'stage', '')}|"
            f"{getattr(projection, 'eligible', False)}"
        )
        self._supply_potential[symbol] = item
        self._v7582_intrabar_bucket[symbol] = bucket
        if self._v7582_intrabar_signature.get(symbol) != signature:
            self._v7582_intrabar_signature[symbol] = signature
            self.logger.warning(
                "선제단계전환 %s → %s | 형성중=%s초 | 점수=%.1f | %s",
                self._label(symbol),
                plan.dashboard_stage,
                int(max(0.0, (now - bucket).total_seconds())),
                float(item.score),
                plan.reason,
            )
            self._save_supply_potential()
            # Put a newly promoted symbol on the hot path once.  Repeating this
            # on every poll would hammer REST and make the dashboard flicker.
            if plan.dashboard_stage in {"관심종목", "매수가능성", "매수직전"}:
                self.next_chart_due[symbol] = 0.0

    def _refresh_actual_bars(self, symbol: str, quote: Any) -> None:
        # All completed-bar, holding, target, stop and catch-up paths remain the
        # V75.81 implementation.
        # Call the V75.81 parent *below* its old intrabar wrapper.  Dynamic
        # _on_bar dispatch still reaches V75.81 target/stop handling, while its
        # obsolete duplicate-poll intrabar order path cannot also fire.
        super(BaseEngine, self)._refresh_actual_bars(symbol, quote)
        if symbol in self.broker.positions or symbol in self.pending_entries:
            return
        raw = list(getattr(self.client, "v7580_raw_cache", {}).get(symbol, []))
        if not raw:
            return
        now = datetime.now(KST)
        bucket = v73._bucket_start(now)
        minute_map = {
            parent._minute_stamp(bar.timestamp): bar
            for bar in raw
            if v73._bucket_start(bar.timestamp) == bucket
        }
        internals = [minute_map[key] for key in sorted(minute_map)]
        if len(internals) < 2:
            return
        partial = v753.Bar(
            symbol=symbol,
            timestamp=bucket,
            open=float(internals[0].open),
            high=max([float(item.high) for item in internals] + [float(quote.price)]),
            low=min([float(item.low) for item in internals] + [float(quote.price)]),
            close=float(quote.price),
            volume=sum(max(0, int(item.volume)) for item in internals),
        )
        completed = list(v73._same_session_bars(self.strategy, symbol))
        elapsed = (now - bucket).total_seconds()
        projection = parent.evaluate_intrabar_projection(
            completed,
            partial,
            internals,
            elapsed_seconds=elapsed,
        )
        if projection.decision is not None:
            self._gem_last_decisions[symbol] = projection.decision
            decision_store = getattr(self.strategy, "_v7580_decisions", None)
            if not isinstance(decision_store, dict):
                decision_store = {}
                setattr(self.strategy, "_v7580_decisions", decision_store)
            decision_store[symbol] = projection.decision

        plan = preemptive_plan(
            projection,
            elapsed_seconds=elapsed,
            internal_count=len(internals),
        )
        self._publish_intrabar_stage(
            symbol,
            completed,
            partial,
            projection,
            plan,
            now=now,
            bucket=bucket,
        )
        if not plan.enter_now:
            return
        if self._v7582_order_attempt_bucket.get(symbol) == bucket:
            return

        signal = v753.Signal(
            symbol,
            v753.SignalAction.BUY,
            max(105.0, float(projection.score) + 25.0),
            str(projection.reason)
            + "|서로다른1분봉2개선제판단|3분마감전즉시전환|체결즉시목표1.5%|고정손절5.0%",
            float(quote.price),
            now,
            "매물소진눌림1차",
            HARD_STOP_PCT,
            TAKE_PROFIT_PCT,
        )
        self.latest_signals[symbol] = signal
        self._v7582_order_attempt_bucket[symbol] = bucket
        entered = bool(self._attempt_entry(signal))
        if not entered and symbol not in self.pending_entries and symbol not in self.broker.positions:
            # Allow one later retry in the same forming bucket if a transient
            # quote/orderbook failure did not create the normal pending entry.
            self._v7582_order_attempt_bucket.pop(symbol, None)


_PARENT_BASELINE_CHECKS = tuple(parent.self_check())


def _install_runtime() -> None:
    v7556._named_mentions = _case_insensitive_named_mentions
    v7556.compile_collaboration_input = compile_collaboration_input_v7582
    v57.compile_collaboration_input = compile_collaboration_input_v7582
    v7525.V7525Engine = V7582PreemptiveIntrabarEngine
    v57.VERSION = VERSION
    v7525.VERSION = VERSION


_install_runtime()


def runtime_identity() -> dict[str, Any]:
    identity = dict(parent.runtime_identity())
    identity.update({
        "version": VERSION,
        "entry": str(Path(__file__).resolve()),
        "engine_class": f"{v7525.V7525Engine.__module__}.{v7525.V7525Engine.__qualname__}",
        "intrabar_sensor": "two distinct 1-minute bars -> immediate stage; qualified entry from 105s",
        "intrabar_duplicate_poll_wait": False,
        "collaboration_name_matching": "case-insensitive canonical resolution",
        "collaboration_price_context": "fresh exact-symbol quote only; no selected-row substitution",
        "request_preview_scrollbar": True,
        "request_preview_scrollbar_patch_active": _TREEVIEW_PATCHED,
        "request_preview_scrollbar_patch_error": _TREEVIEW_PATCH_ERROR,
        "parent_v7581_sha256": PARENT_SHA256,
        "daily_loss_activation_origin": _settings_loader_identity(),
        "daily_loss_activation_provenance": "inherited from V75.16; V75.81/V75.82 verify active runtime, not newly enabled",
        "max_concurrent_positions_basis": "default safety ceiling; profitability/correlation optimum not yet established",
        "manifest_includes_intraday_command": _manifest_includes_intraday_command(),
        "link_exact_parent": v57.RestoredHeroLink is parent.parent.SmoothContinuousHeroLink,
        "target_registration": "inherited V75.81 immediate +1.5% full held quantity",
        "hard_stop_pct": HARD_STOP_PCT,
        "paper_only": True,
    })
    return identity


def self_check() -> list[tuple[str, bool, str]]:
    rows = list(_PARENT_BASELINE_CHECKS)
    identity = runtime_identity()
    rows.extend([
        ("V75.81기준SHA", sha256_file(BASE_DIR / PARENT_FILE) == PARENT_SHA256, PARENT_SHA256[:16]),
        ("선제엔진실행경로", v7525.V7525Engine is V7582PreemptiveIntrabarEngine, identity["engine_class"]),
        ("중복폴링대기제거", identity["intrabar_duplicate_poll_wait"] is False, "1분봉2개가 지속성 증거"),
        ("선제진입시각", PREEMPTIVE_ENTRY_MIN_SEC == 105.0, "105~177초"),
        ("종목명대소문자무관", v7556._named_mentions is _case_insensitive_named_mentions, "sk하이닉스=SK하이닉스"),
        ("동일종목최신가만", callable(_fresh_quote_price), "다른 선택종목 가격 대체금지"),
        (
            "일손실한도실제출처",
            "v7516" in identity["daily_loss_activation_origin"]
            and "_fixed_settings_load" in identity["daily_loss_activation_origin"]
            and not bool(v753.Settings.load().paper_unlimited),
            identity["daily_loss_activation_origin"],
        ),
        (
            "보유20종목상한성격",
            "optimum not yet established" in identity["max_concurrent_positions_basis"],
            "수익최적값이 아닌 기본 안전상한",
        ),
        (
            "장중명령매니페스트포함",
            identity["manifest_includes_intraday_command"] is True,
            "SOURCE/장중명령.txt",
        ),
        (
            "요청미리보기스크롤",
            _TREEVIEW_PATCHED or bool(_TREEVIEW_PATCH_ERROR),
            "세로 스크롤" if _TREEVIEW_PATCHED else f"현재 검사환경 Tk 지연:{_TREEVIEW_PATCH_ERROR}",
        ),
        ("정상연동그대로", identity["link_exact_parent"] is True, identity["link_class"]),
        ("즉시목표그대로", TAKE_PROFIT_PCT == 0.015, "+1.5%"),
        ("손절그대로", HARD_STOP_PCT == 0.05, "-5.0%"),
        ("PAPER전용", VERSION.endswith("PAPER") and identity["paper_only"] is True, VERSION),
    ])
    return rows


def main() -> int:
    diag("V7582_RUNTIME " + json.dumps(runtime_identity(), ensure_ascii=False, sort_keys=True))
    return int(parent.main())


if __name__ == "__main__":
    if "--runtime-info" in sys.argv or "--identity" in sys.argv:
        print(json.dumps(runtime_identity(), ensure_ascii=False, indent=2, sort_keys=True))
        raise SystemExit(0)
    if "--self-check" in sys.argv:
        checks = self_check()
        for name, ok, detail in checks:
            print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
        raise SystemExit(0 if all(ok for _, ok, _ in checks) else 1)
    raise SystemExit(main())
