#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.42 PAPER — API 예산·시세신선도·재진입 무결성 통합판.

핵심
- V75.40의 PAPER +1.5% 목표매도, 자동손절 없음, 10억원/종목당 1천만원,
  500/300/200만원 분할매수 및 영웅문 연동을 SHA256 고정으로 상속한다.
- 같은 종목은 보유 중 중복 진입하지 않지만, 목표매도 후 **새 분봉에서 새 조건이
  다시 완성되면 횟수 제한 없이 재매수**할 수 있다. 같은 과거 신호의 즉시 재사용은
  막는다.
- 급락(SHOCK), 하락진행(CAUTION), 회복확인대기(RECOVERY_WAIT), 지수오류(DATA_HALT)
  중에는 신규매수를 막고 후보를 버리지 않는다. RECOVERY_READY/NORMAL에서 다시 본다.
- 감시 범위는 상승률·거래량급증·외국인기관쌍끌이 순위의 합집합이며 단일 상승률
  상위 100종목만 보는 구조가 아니다. 요청 상한과 유지 풀을 확대한다.
- 5분 요약에 보유종목 매수시각·평단·현재가·수익/손실·수익률·평가손익·목표가를 표시한다.
- GUI의 관심/관찰/매수가능성/매수직전/시장보류/보유에 **단계 편입 또는 매수 시각**을 표시한다.
- 2배 거래량은 약한 신호로 유지한다. 2.5~3배는 선행 대량 양봉과 지지 확인이 있을 때만
  중간 강도로 허용하고, 3배 이상은 핵심 강신호다.

실제 주문 함수는 없으며 PAPER 전용이다.
"""
from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
import json
import threading
import urllib.error
import urllib.request
import hashlib
import importlib.util
import os
from pathlib import Path
import sys
import time
from types import ModuleType
from typing import Any, Sequence

VERSION = "V75.42-RATE-BUDGET-STALE-GUARD-PAPER"
BASE_DIR = Path(__file__).resolve().parent
BASE_FILENAME = "bot_v75_40_end_to_end_audited_paper.py"
BASE_SHA256 = "3990f0b610b80ac7d07d0226cecc1e679d04e2042972d72d812890baee789b52"
DIAG_FILE = BASE_DIR.parent / "V75_42_LINK_DIAG.txt"

STRONG_VOLUME_RATIO = 3.0
MEDIUM_VOLUME_RATIO = 2.5
WEAK_VOLUME_RATIO = 2.0
PRIOR_BULL_VOLUME_RATIO = 2.0
FIXED_TAKE_PROFIT_PCT = 0.015

ACTIVE_RANK_REQUEST_LIMIT = 200
ACTIVE_POOL_LIMIT = 400
ACTIVE_SUPPLY_POOL_LIMIT = 120
ACTIVE_DISCOVERY_FALLBACK = 80
ACTIVE_HOT_CANDIDATES = 20
ACTIVE_HOT_REFRESH_SEC = 30.0
RETAIN_UNIVERSE_SEC = 20.0 * 60.0
ACTIVE_MAX_MONITOR_DELAY_SEC = 45.0
ACTIVE_RETRY_ATTEMPTS = 5
ACTIVE_RETRY_SEC = 10.0
ACTIVE_MAX_CHASE_PCT = 0.007
ACTIVE_GENERAL_REFRESH_SEC = 300.0
ACTIVE_CANDIDATE_TTL_SEC = 180
HOLDING_QUOTE_MAX_AGE_SEC = 30.0
MOCK_PER_TR_MIN_INTERVAL_SEC = 1.02
GLOBAL_QUERY_MIN_INTERVAL_SEC = 0.21
RANK_MAX_PAGES = 5


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_fixed(filename: str, expected_sha: str, module_name: str) -> ModuleType:
    path = BASE_DIR / filename
    actual = sha256_file(path)
    if actual != expected_sha:
        raise RuntimeError(f"{filename} SHA256 불일치\n기대: {expected_sha}\n실제: {actual}")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"{filename} 로드 실패")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


v7540 = _load_fixed(BASE_FILENAME, BASE_SHA256, "v7541_immutable_base")
v7539 = v7540.v7539
v7538 = v7540.v7538
v7530 = v7540.v7530
v7525 = v7540.v7525
v7521 = v7540.v7521
v7520 = v7540.v7520
v7518 = v7525.v7518
v7515 = v7540.v7515
v7514 = v7540.v7514
v756 = v7540.v756
v754 = v7540.v754
v753 = v7540.v753
KST = v7540.KST
LinkStatus = v7540.LinkStatus
TargetIdentityHeroLink = v7540.TargetIdentityHeroLink
_ORIGINAL_SELF_CHECK = v7540.self_check
_ORIGINAL_PRECISION_LOAD = v7515.PrecisionConfig.load
_BASE_EVALUATE = v7540._evaluate


def diag(message: str) -> None:
    try:
        with DIAG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}\n")
    except OSError:
        pass


# V75.40 기반 연동 계층의 진단파일도 V75.42로 통합한다.
v7540.diag = diag
v7538.diag = diag


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _active_precision_load(cls: type[Any]) -> Any:
    """핵심 3배 기준은 유지하고, 선행 상승 문턱만 완화한다."""
    cfg = _ORIGINAL_PRECISION_LOAD()
    prior_rise = float(os.environ.get("V7541_MIN_PRIOR_RISE", "0.018"))
    prior_rise = min(0.05, max(0.008, prior_rise))
    return replace(cfg, min_prior_rise_pct=prior_rise)


v7515.PrecisionConfig.load = classmethod(_active_precision_load)


def evaluate_v7541(
    bars: Sequence[Any], structure: Any, cfg: Any, signal: Any | None = None
) -> dict[str, Any]:
    """3배 강신호, 2.5배 중간신호, 2배 약신호를 분리한다."""
    base = dict(_BASE_EVALUATE(bars, structure, cfg, signal))
    strength = float(base["strength"])
    supports = int(base["supports"])
    bull_ratio = float(base["bull_ratio"])
    dry_heavy = float(base["dry_heavy"])
    dry_context = float(base["dry_context"])
    dry = (
        dry_heavy <= float(cfg.dry_max_volume_vs_heavy_cluster)
        and dry_context <= float(cfg.dry_max_volume_vs_context)
    )
    reignition = (
        signal is not None
        and str(getattr(signal, "pattern", "")) == str(v7515.PATTERN_REIGNITION)
    )
    base_confirmation = (
        (dry and bool(base["short"]) and bool(base["confirm"])) or reignition
    )

    strong_ok = strength >= STRONG_VOLUME_RATIO and supports >= 1
    # 기존 2~3배 약신호 전체를 사지 않는다. 2.5배 이상이며 선행 대량 양봉,
    # 1개 이상 지지, 70% 이상 거래감소까지 갖춘 경우만 적극형 중간신호로 허용한다.
    medium_ok = (
        MEDIUM_VOLUME_RATIO <= strength < STRONG_VOLUME_RATIO
        and supports >= 1
        and bull_ratio >= PRIOR_BULL_VOLUME_RATIO
        and dry_heavy <= float(cfg.strong_dry_max_volume_vs_heavy_cluster)
    )
    eligible = base_confirmation and (strong_ok or medium_ok)

    missing: list[str] = []
    if strength < WEAK_VOLUME_RATIO:
        missing.append(f"음봉대량거래x{strength:.2f}<2.00")
    elif strength < MEDIUM_VOLUME_RATIO:
        missing.append(f"약한음봉거래x{strength:.2f}<2.50")
    elif strength < STRONG_VOLUME_RATIO and not medium_ok:
        missing.append(f"중간음봉거래x{strength:.2f}<3.00_보강필요")
        if bull_ratio < PRIOR_BULL_VOLUME_RATIO:
            missing.append(f"선행양봉대량x{bull_ratio:.2f}<2.00")
        if supports < 1:
            missing.append("지지확인봉대기")
        if dry_heavy > float(cfg.strong_dry_max_volume_vs_heavy_cluster):
            missing.append("강한거래감소70%대기")
    if not dry and not reignition:
        missing.append("거래감소60%대기")
    if not bool(base["short"]) and not reignition:
        missing.append("짧은봉대기")
    if not bool(base["confirm"]) and not reignition:
        missing.append("반전봉대기")
    if strength >= STRONG_VOLUME_RATIO and supports < 1:
        missing.append("지지확인봉대기")

    grade = (
        "강함" if strength >= STRONG_VOLUME_RATIO
        else "중간" if strength >= MEDIUM_VOLUME_RATIO
        else "약함" if strength >= WEAK_VOLUME_RATIO
        else "미달"
    )
    base.update(
        eligible=eligible,
        grade=grade,
        missing=tuple(dict.fromkeys(missing)),
        strong_ok=strong_ok,
        medium_ok=medium_ok,
    )
    return base


def detect_v7541(strategy: Any, symbol: str, *, config: Any) -> tuple[Any | None, str, Any | None]:
    signal, state, evidence = v7540._ORIGINAL_DETECT(strategy, symbol, config=config)
    if signal is None or evidence is None:
        return signal, state, evidence
    cfg = v7515.PrecisionConfig.load()
    bars = list(v756._same_session_bars(strategy.bars.get(symbol, [])))
    if not bars:
        return None, "분봉없음", None
    if v754.normalize_kst(signal.timestamp) != v754.normalize_kst(bars[-1].timestamp):
        return None, "과거신호차단", evidence
    structure = v7540._matching_structure(bars, evidence, cfg)
    if structure is None:
        return None, "대량음봉시각불일치", evidence
    evaluated = evaluate_v7541(bars, structure, cfg, signal)
    if not evaluated["eligible"]:
        return None, "매수대기_" + "_".join(evaluated["missing"][:3]), evidence

    import re
    reason = re.sub(r"\|손절=[^|]+", "", str(signal.reason))
    reason = re.sub(r"\|목표=[^|]+", "", reason)
    reason = re.sub(r"\|대량거래=[^|]+", "", reason)
    bonus = (
        10.0 if evaluated["strength"] >= STRONG_VOLUME_RATIO else 5.0
    ) + (5.0 if evaluated["bull_ratio"] >= PRIOR_BULL_VOLUME_RATIO else 0.0)
    bonus += max(0, int(evaluated["supports"]) - 1) * 3.0
    reason += (
        f"|조정음봉거래={evaluated['strength']:.2f}배"
        f"({evaluated['basis']}/{evaluated['grade']})"
        f"|선행양봉대량={evaluated['bull_ratio']:.2f}배(가산조건)"
        f"|지지확인봉={evaluated['supports']}|적극형진입"
        f"|자동손절없음|목표=1.5%고정"
    )
    fixed = v753.Signal(
        signal.symbol,
        signal.action,
        float(signal.score) + bonus,
        reason,
        float(signal.reference_price),
        signal.timestamp,
        str(signal.pattern),
        float(signal.stop_loss_pct),
        FIXED_TAKE_PROFIT_PCT,
    )
    return fixed, state, evidence


def assess_v7541(
    strategy: Any,
    symbol: str,
    *,
    loose: Any,
    strict: Any,
    now: datetime,
    name: str,
    source: str,
) -> tuple[Any | None, str]:
    item, state = v7540._ORIGINAL_ASSESS(
        strategy,
        symbol,
        loose=loose,
        strict=strict,
        now=now,
        name=name,
        source=source,
    )
    if item is None:
        return None, state
    cfg = v7515.PrecisionConfig.load()
    bars = list(v756._same_session_bars(strategy.bars.get(symbol, [])))
    structures = v7540.find_v7540_structures(bars, cfg) if bars else []
    if not structures:
        return None, "상승후2배이상음봉거래없음"
    base_signal, _, base_evidence = v7540._ORIGINAL_DETECT(strategy, symbol, config=None)
    structure = structures[0]
    if base_evidence is not None:
        matched = v7540._matching_structure(bars, base_evidence, cfg)
        if matched is not None:
            structure = matched
    evaluated = evaluate_v7541(bars, structure, cfg, base_signal)
    strict_signal, strict_state, _ = detect_v7541(strategy, symbol, config=None)
    if strict_signal is not None:
        stage = "즉시매수조건근접"
        missing: tuple[str, ...] = ()
    elif evaluated["dry_heavy"] <= float(cfg.dry_max_volume_vs_heavy_cluster):
        stage = "관심종목-매수가능성"
        missing = evaluated["missing"] or (strict_state,)
    else:
        stage = "거래바닥관찰"
        missing = evaluated["missing"] or ("거래감소60%대기",)
    marker = (
        f"V7541|음봉거래x{evaluated['strength']:.2f}"
        f"({evaluated['grade']}/{evaluated['basis']})"
        f"|선행양봉x{evaluated['bull_ratio']:.2f}|지지{evaluated['supports']}"
        f"|판정={'진입가능' if strict_signal is not None else '대기'}"
    )
    score = float(item.score)
    score += 14 if evaluated["strength"] >= 3 else 7 if evaluated["strength"] >= 2.5 else 2
    score += 5 if evaluated["bull_ratio"] >= 2 else 0
    return replace(
        item,
        stage=stage,
        strict_missing=tuple(missing),
        score=min(100.0, score),
        source=f"{source}|{marker}",
        updated_at=now,
    ), state


def condition_reason_v7541(item: Any, bucket: str) -> str:
    source = str(getattr(item, "source", ""))
    if "V7541|" not in source:
        return v7540._ORIGINAL_CONDITION_REASON(item, bucket)
    marker = source.split("V7541|", 1)[1]
    parts = marker.split("|")
    summary = " · ".join(parts[:3])
    missing = tuple(getattr(item, "strict_missing", ()) or ())
    if bucket == "ready":
        return summary + " · 적극형매수조건완료"
    if missing:
        return summary + " · 대기:" + ",".join(str(value) for value in missing[:2])
    return summary


# 후보 표시와 실제 진입이 동일한 판정기를 사용하도록 모든 경로를 교체한다.
v7540._evaluate = evaluate_v7541
v7540.detect_v7540 = detect_v7541
v7540.assess_v7540 = assess_v7541
v7540.condition_reason_v7540 = condition_reason_v7541
v7539._evaluate = evaluate_v7541
v7539.detect_v7539 = detect_v7541
v7539.assess_v7539 = assess_v7541
v7515.detect_precision_supply = detect_v7541
v7515.assess_precision_watch = assess_v7541
v7525.detect_v7525 = detect_v7541
v7525.assess_v7525 = assess_v7541
v7540.v756.detect_supply_exhaustion_pullback = detect_v7541
v7514.assess_loose_supply_potential = assess_v7541
v7520._condition_reason = condition_reason_v7541
v7530.strict_detect = detect_v7541
v7530.strict_assess = assess_v7541


def market_entry_allowed(state: str) -> tuple[bool, str]:
    normalized = str(state or "DATA_HALT").upper()
    if normalized in {"NORMAL", "RECOVERY_READY"}:
        return True, "시장안정"
    if normalized == "SHOCK":
        return False, "시장급락진행"
    if normalized == "CAUTION":
        return False, "시장하락진행"
    if normalized == "RECOVERY_WAIT":
        return False, "시장안정확인대기"
    return False, "시장데이터확인대기"


def new_condition_after_exit(signal_time: datetime, last_exit: datetime | None) -> bool:
    if last_exit is None:
        return True
    return v754.normalize_kst(signal_time) > v754.normalize_kst(last_exit)


def quote_age_seconds(
    quote_timestamp: datetime | None,
    *,
    now: datetime | None = None,
) -> float:
    """현재시각 대비 시세 나이. timestamp가 없으면 무한대로 취급한다."""
    if quote_timestamp is None:
        return float("inf")
    timestamp = quote_timestamp
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return max(
        0.0,
        (current.astimezone(timezone.utc) - timestamp.astimezone(timezone.utc)).total_seconds(),
    )


def holding_metrics(
    position: Any,
    current: float,
    settings: Any,
    *,
    quote_available: bool = True,
    quote_timestamp: datetime | None = None,
    now: datetime | None = None,
    max_age_sec: float = HOLDING_QUOTE_MAX_AGE_SEC,
) -> dict[str, float | str | bool]:
    """신선한 현재가만 손익으로 표시한다.

    시세가 없거나 오래됐으면 마지막 가격으로 수익/손실을 주장하지 않고,
    평가자산도 매입원가를 사용해 과대·과소평가를 피한다.
    """
    quantity = int(position.quantity)
    age = quote_age_seconds(quote_timestamp, now=now)
    valid_price = quote_available and float(current) > 0.0
    fresh = valid_price and age <= max(1.0, float(max_age_sec))
    if not fresh:
        last_price = float(current) if valid_price else 0.0
        if not valid_price:
            state = "조회대기"
            current_text = "조회대기"
        else:
            state = "시세지연"
            current_text = f"시세지연 {age:.0f}초"
        return {
            "current": float(position.entry_price),
            "last_price": last_price,
            "current_text": current_text,
            "proceeds": float(position.entry_cost),
            "pnl": 0.0,
            "pnl_pct": 0.0,
            "label": state,
            "quote_available": False,
            "stale": bool(valid_price),
            "quote_age_sec": age,
        }
    gross = float(current) * quantity
    proceeds = gross * (1.0 - float(settings.commission_pct) - float(settings.sell_tax_pct))
    pnl = proceeds - float(position.entry_cost)
    pnl_pct = pnl / max(1.0, float(position.entry_cost))
    label = "수익" if pnl > 0.5 else "손실" if pnl < -0.5 else "보합"
    return {
        "current": float(current),
        "last_price": float(current),
        "current_text": f"{float(current):,.0f}",
        "proceeds": proceeds,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "label": label,
        "quote_available": True,
        "stale": False,
        "quote_age_sec": age,
    }


class BudgetedPagedKiwoomClient(v753.KiwoomRestClient):
    """공식 호출예산을 지키고 순위 연속조회 헤더를 처리한다.

    국내 실서버 조회는 전체 5회/초, 모의투자는 같은 TR 1회/초 제한을 기준으로
    더 엄격하게 동작한다. 기존 전역 1초 간격 때문에 전체 감시가 지나치게 느려지는
    문제는 전역 0.21초 + 모의 TR별 1.02초로 분리한다.
    """

    def __init__(self, settings: Any, logger: Any) -> None:
        super().__init__(settings, logger)
        self.limiter.interval_sec = max(GLOBAL_QUERY_MIN_INTERVAL_SEC, 0.0)
        self._mock_mode = str(settings.api_mode).lower() == "mock"
        self._per_tr_interval = MOCK_PER_TR_MIN_INTERVAL_SEC if self._mock_mode else 0.0
        self._tr_last: dict[str, float] = defaultdict(float)
        self._tr_lock = threading.Lock()
        self._last_response_headers: dict[str, str] = {}

    def _wait_tr(self, api_id: str) -> None:
        interval = float(self._per_tr_interval)
        if interval <= 0.0:
            return
        while True:
            with self._tr_lock:
                now_mono = time.monotonic()
                delay = interval - (now_mono - self._tr_last[str(api_id)])
                if delay <= 0.0:
                    self._tr_last[str(api_id)] = now_mono
                    return
            time.sleep(delay)

    def _json_post(
        self,
        url: str,
        body: Any,
        headers: Any,
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
                self._last_response_headers = {
                    str(key).lower(): str(value) for key, value in response.headers.items()
                }
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            self._last_response_headers = {
                str(key).lower(): str(value) for key, value in exc.headers.items()
            } if exc.headers else {}
            raw = exc.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise v753.ApiTransportError(f"네트워크 실패: {exc}") from exc
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise v753.ApiTransportError(
                f"JSON 아님 HTTP={status}, body={raw[:250]!r}"
            ) from exc
        if not isinstance(parsed, dict):
            raise v753.ApiSchemaError(
                f"응답 최상위가 객체가 아님: {type(parsed).__name__}"
            )
        return parsed, status

    def post(self, *, api_id: str, endpoint: str, body: Any, required_any: tuple[str, ...]):
        self._wait_tr(api_id)
        return super().post(
            api_id=api_id,
            endpoint=endpoint,
            body=body,
            required_any=required_any,
        )

    def _rank_post_page(
        self,
        api_id: str,
        body: Any,
        *,
        cont_yn: str = "",
        next_key: str = "",
    ) -> dict[str, Any] | None:
        self._wait_tr(api_id)
        token = self.authenticate()
        request_headers = {"authorization": f"Bearer {token}", "api-id": api_id}
        if cont_yn and next_key:
            request_headers["cont-yn"] = cont_yn
            request_headers["next-key"] = next_key
        try:
            data, status = self._json_post(
                self.settings.base_url + "/api/dostk/rkinfo",
                body,
                headers=request_headers,
            )
        except v753.V73Error as exc:
            v753.append_api_error(
                f"{api_id} continuation transport body={dict(body)!r} error={exc}"
            )
            return None
        try:
            code = v753.return_code_of(data)
        except ValueError:
            code = None
        bucket = self.rank_probe_trials[api_id]
        if len(bucket) < 50:
            bucket.append(
                {
                    "body": dict(body),
                    "status": status,
                    "code": code,
                    "error": "",
                    "data": data,
                    "cont_yn": self._last_response_headers.get("cont-yn", ""),
                    "next_key_present": bool(self._last_response_headers.get("next-key", "")),
                }
            )
        if status != 200 or code != 0:
            v753.append_api_error(
                f"{api_id} continuation rejected HTTP={status} code={code} "
                f"msg={data.get('return_msg','')!r} body={dict(body)!r}"
            )
            return None
        return data

    def _rank_pages(self, api_id: str, body: Any, *, max_pages: int = RANK_MAX_PAGES):
        pages: list[dict[str, Any]] = []
        cont_yn = ""
        next_key = ""
        seen_keys: set[str] = set()
        for _ in range(max(1, int(max_pages))):
            data = self._rank_post_page(
                api_id,
                body,
                cont_yn=cont_yn,
                next_key=next_key,
            )
            if data is None:
                break
            pages.append(data)
            response_cont = self._last_response_headers.get("cont-yn", "").strip().upper()
            response_key = self._last_response_headers.get("next-key", "").strip()
            if response_cont != "Y" or not response_key or response_key in seen_keys:
                break
            seen_keys.add(response_key)
            cont_yn, next_key = response_cont, response_key
        return pages

    def get_volume_surge_candidates(self, *, limit: int = 100):
        common = {
            "sort_tp": "2", "tm_tp": "1", "trde_qty_tp": "0000",
            "stk_cnd": "1", "pric_tp": "0", "stex_tp": self.settings.stex_tp,
        }
        payloads: list[dict[str, Any]] = []
        for market in ("000", "001", "101"):
            payloads.extend(self._rank_pages("ka10023", {"mrkt_tp": market, **common}))
            candidates = self._parse_rank_candidates(payloads, source="거래량급증")
            if len(candidates) >= limit:
                return candidates[:max(1, limit)]
        candidates = self._parse_rank_candidates(payloads, source="거래량급증")
        if candidates:
            return candidates[:max(1, limit)]
        if any(isinstance(item.get("trde_qty_sdnin"), list) for item in payloads):
            return []
        raise v753.ApiSchemaError("ka10023 응답목록 trde_qty_sdnin 확인 실패")

    def get_gain_rank_candidates(self, *, limit: int = 100):
        common = {
            "sort_tp": "1", "trde_qty_cnd": "0000", "stk_cnd": "1",
            "crd_cnd": "0", "updown_incls": "1", "pric_cnd": "0",
            "trde_prica_cnd": "0", "stex_tp": self.settings.stex_tp,
        }
        payloads: list[dict[str, Any]] = []
        for market in ("000", "001", "101"):
            payloads.extend(self._rank_pages("ka10027", {"mrkt_tp": market, **common}))
            candidates = self._parse_rank_candidates(payloads, source="상승률")
            if len(candidates) >= limit:
                return candidates[:max(1, limit)]
        candidates = self._parse_rank_candidates(payloads, source="상승률")
        if not candidates:
            raise v753.ApiSchemaError("ka10027 상승률 후보 없음")
        return candidates[:max(1, limit)]

    def get_dual_buy_candidates(self, *, limit: int = 100):
        payloads: list[dict[str, Any]] = []
        schema_seen = False
        for body in self._dual_rank_request_bodies():
            pages = self._rank_pages("ka90009", body)
            payloads.extend(pages)
            if any(isinstance(item.get("frgnr_orgn_trde_upper"), list) for item in pages):
                schema_seen = True
            candidates, _ = self._parse_dual_buy_candidates(payloads)
            if len(candidates) >= limit:
                return candidates[:max(1, limit)]
        candidates, parsed_schema = self._parse_dual_buy_candidates(payloads)
        if candidates:
            return candidates[:max(1, limit)]
        if schema_seen or parsed_schema:
            return []
        raise v753.ApiSchemaError("ka90009 응답목록 frgnr_orgn_trde_upper 확인 실패")


v753.KiwoomRestClient = BudgetedPagedKiwoomClient


class V7542Engine(v7540.V7540Engine):
    """적극형 감시·재진입과 투명한 손익/단계 시각을 추가한다."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._v7541_retained_until: dict[str, float] = {}
        self._v7541_last_exit_at: dict[str, datetime] = {}
        self._v7541_completed_cycles: dict[str, int] = {}
        super().__init__(*args, **kwargs)

        self.rank_limit = max(int(self.rank_limit), ACTIVE_RANK_REQUEST_LIMIT)
        self.pool_limit = max(int(self.pool_limit), ACTIVE_POOL_LIMIT)
        # 모의투자 같은 TR 1회/초 제한에서 실제로 지킬 수 있는 감시 예산.
        self.hot_candidate_count = ACTIVE_HOT_CANDIDATES
        self.chart_refresh_hot = ACTIVE_HOT_REFRESH_SEC
        self.chart_refresh = max(float(self.chart_refresh), ACTIVE_GENERAL_REFRESH_SEC)
        self.settings = replace(
            self.settings,
            candidate_ttl_sec=max(
                int(self.settings.candidate_ttl_sec), ACTIVE_CANDIDATE_TTL_SEC
            ),
        )
        self.entry_retry_sec = min(float(self.entry_retry_sec), ACTIVE_RETRY_SEC)
        self.retry_max_attempts = max(int(self.retry_max_attempts), ACTIVE_RETRY_ATTEMPTS)
        self.max_monitor_delay_sec = max(
            float(self.max_monitor_delay_sec), ACTIVE_MAX_MONITOR_DELAY_SEC
        )
        self.max_chase_capitulation = max(
            float(self.max_chase_capitulation), ACTIVE_MAX_CHASE_PCT
        )
        self.loose_supply_config = replace(
            self.loose_supply_config,
            supply_pool_limit=ACTIVE_SUPPLY_POOL_LIMIT,
            discovery_fallback_count=ACTIVE_DISCOVERY_FALLBACK,
            potential_keep_minutes=max(
                30, int(self.loose_supply_config.potential_keep_minutes)
            ),
        )
        # 사용자의 PAPER 실험 기준: 급락장과 데이터/수동 차단을 제외하고 조건 충족 신호를
        # 계속 시험한다. 기존 연속손실/당일손실 가드는 명시적으로 켤 때만 사용한다.
        self.paper_loss_guard_enabled = _env_bool(
            "V7541_ENABLE_LEGACY_LOSS_GUARD", False
        )
        self._load_today_exit_history()
        self._purge_stale_reentry_state()
        self.logger.warning(
            "[%s] 적극형 감시 | 순위요청 상한=%d×3종 | 통합풀=%d | 매물풀=%d | "
            "REST예산 HOT=%d/%0.fs·일반=%0.fs | 동일종목 새조건 재진입=무제한 | 급락중단·안정후재개",
            VERSION,
            self.rank_limit,
            self.pool_limit,
            self.loose_supply_config.supply_pool_limit,
            self.hot_candidate_count,
            self.chart_refresh_hot,
            self.chart_refresh,
        )

    def _purge_stale_reentry_state(self) -> None:
        """매도 이전 시각의 pending/deferred 신호를 실행 경로에서 제거한다."""
        for symbol, pending in list(getattr(self, "pending_entries", {}).items()):
            last_exit = self._v7541_last_exit_at.get(str(symbol))
            signal = getattr(pending, "signal", None)
            if signal is not None and not new_condition_after_exit(signal.timestamp, last_exit):
                self.pending_entries.pop(symbol, None)
        for attribute in ("_deferred_market_signals", "_generic_deferred"):
            storage = getattr(self, attribute, None)
            if not isinstance(storage, dict):
                continue
            for symbol, item in list(storage.items()):
                signal = getattr(item, "signal", None)
                last_exit = self._v7541_last_exit_at.get(str(symbol))
                if signal is not None and not new_condition_after_exit(signal.timestamp, last_exit):
                    storage.pop(symbol, None)

    def _fresh_quote(self, symbol: str) -> tuple[Any | None, float]:
        quote = self.latest_quotes.get(symbol)
        if quote is None:
            return None, float("inf")
        age = quote_age_seconds(getattr(quote, "timestamp", None))
        return quote, age

    def _sync_daily_state(self) -> None:
        # 오래된 시세로 일일자산·손실가드를 왜곡하지 않는다.
        prices: dict[str, float] = {}
        for symbol, quote in self.latest_quotes.items():
            age = quote_age_seconds(getattr(quote, "timestamp", None))
            if age <= HOLDING_QUOTE_MAX_AGE_SEC and float(quote.price) > 0.0:
                prices[symbol] = float(quote.price)
        equity = self.broker.equity(prices)
        if self.broker.roll_session_if_needed(equity):
            self.risk.reset_daily_session(self.broker.day_start_equity)
        self.broker.daily_realized_pnl = self.risk.daily_realized_pnl
        self.broker.update_daily_halt(
            self.risk.daily_halted, self.risk.daily_halt_reason
        )

    def _load_today_exit_history(self) -> None:
        path = v753.FILL_FILE
        if not path.exists():
            return
        today = datetime.now(KST).date()
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                for raw in csv.DictReader(handle):
                    if str(raw.get("side", "")).upper() != "SELL":
                        continue
                    try:
                        timestamp = datetime.fromisoformat(str(raw.get("timestamp", "")))
                        if timestamp.tzinfo is None:
                            timestamp = timestamp.replace(tzinfo=KST)
                        timestamp = timestamp.astimezone(KST)
                    except ValueError:
                        continue
                    if timestamp.date() != today:
                        continue
                    symbol = str(raw.get("symbol", "")).zfill(6)
                    self._v7541_last_exit_at[symbol] = max(
                        timestamp, self._v7541_last_exit_at.get(symbol, timestamp)
                    )
                    self._v7541_completed_cycles[symbol] = (
                        self._v7541_completed_cycles.get(symbol, 0) + 1
                    )
        except (OSError, csv.Error):
            return

    def _refresh_universe(self, *, force: bool = False) -> list[str]:
        previous = list(self.active_symbols)
        added = super()._refresh_universe(force=force)
        now_mono = time.monotonic()
        for symbol in self.active_symbols:
            self._v7541_retained_until[symbol] = now_mono + RETAIN_UNIVERSE_SEC
        retained = [
            symbol
            for symbol in previous
            if symbol not in self.broker.positions
            and self._v7541_retained_until.get(symbol, 0.0) > now_mono
        ]
        potential = [
            symbol
            for symbol in getattr(self, "_supply_potential", {})
            if symbol not in self.broker.positions
        ]
        selected = list(dict.fromkeys(potential + self.active_symbols + retained))
        self.active_symbols = selected[: self.loose_supply_config.supply_pool_limit]
        expired = [
            symbol
            for symbol, deadline in self._v7541_retained_until.items()
            if deadline <= now_mono
        ]
        for symbol in expired:
            self._v7541_retained_until.pop(symbol, None)
        previous_set = set(previous)
        # 부모가 보고한 added가 최종 180종목 절단 뒤 빠졌을 수 있으므로,
        # 실제 최종 active_symbols 기준의 신규 종목만 반환한다.
        return [symbol for symbol in self.active_symbols if symbol not in previous_set]

    def _signal_entry_allowed(self, signal: Any) -> tuple[bool, str]:
        allowed, reason = super()._signal_entry_allowed(signal)
        if not allowed:
            return allowed, reason
        last_exit = self._v7541_last_exit_at.get(str(signal.symbol))
        if not new_condition_after_exit(signal.timestamp, last_exit):
            return False, "재매수는_매도후새분봉·새조건대기"
        return True, "새조건정상"

    def _queue_pending_entry(self, signal: Any) -> None:
        signal_allowed, signal_reason = self._signal_entry_allowed(signal)
        if not signal_allowed:
            self._record_entry_decision(signal.symbol, "BLOCKED", signal_reason)
            self.pending_entries.pop(signal.symbol, None)
            return
        state = str(getattr(self._market_snapshot, "state", "DATA_HALT"))
        allowed, reason = market_entry_allowed(state)
        if not allowed:
            self._record_entry_decision(signal.symbol, "BLOCKED", reason)
            if hasattr(self, "_defer_market_signal") and self._can_defer_signal(signal):
                self._defer_market_signal(signal, reason)
            return
        super()._queue_pending_entry(signal)

    def _attempt_entry(self, signal: Any, *, from_retry: bool = False) -> bool:
        signal_allowed, signal_reason = self._signal_entry_allowed(signal)
        if not signal_allowed:
            self._record_entry_decision(signal.symbol, "BLOCKED", signal_reason)
            self.pending_entries.pop(signal.symbol, None)
            return False
        state = str(getattr(self._market_snapshot, "state", "DATA_HALT"))
        allowed, reason = market_entry_allowed(state)
        if not allowed:
            self._record_entry_decision(signal.symbol, "BLOCKED", reason)
            if hasattr(self, "_defer_market_signal") and self._can_defer_signal(signal):
                self._defer_market_signal(signal, reason)
            self.pending_entries.pop(signal.symbol, None)
            return False
        return super()._attempt_entry(signal, from_retry=from_retry)

    def _print_sell(self, fill: Any) -> None:
        super()._print_sell(fill)
        symbol = str(fill.symbol)
        timestamp = fill.timestamp.astimezone(KST)
        self._v7541_last_exit_at[symbol] = timestamp
        self._v7541_completed_cycles[symbol] = self._v7541_completed_cycles.get(symbol, 0) + 1
        # 목표수익 매도 뒤에는 손절 재진입 쿨다운을 적용하지 않는다. 단, 동일 과거 신호는
        # _signal_entry_allowed에서 차단하고 다음 새 분봉 조건부터 다시 허용한다.
        if float(getattr(fill, "pnl", 0.0)) >= 0:
            self.broker.reentry_block_until.pop(symbol, None)
        self.pending_entries.pop(symbol, None)
        self.logger.warning(
            "재매수대기 %s | 완료사이클=%d | 매도후 새 분봉·새 조건부터 횟수제한 없음",
            self._label(symbol),
            self._v7541_completed_cycles[symbol],
        )

    def _gui_snapshot(self) -> dict[str, Any]:
        snapshot = super()._gui_snapshot()
        rows = list(snapshot.get("rows", []))
        for row in rows:
            row["stage_time"] = str(row.get("time", "-") or "-")
            symbol = str(row.get("symbol", ""))
            if row.get("stage") == "매수종목" and symbol in self.broker.positions:
                position = self.broker.positions[symbol]
                quote = self.latest_quotes.get(symbol)
                raw_current = float(quote.price) if quote is not None else 0.0
                metrics = holding_metrics(
                    position,
                    raw_current,
                    self.settings,
                    quote_available=quote is not None,
                    quote_timestamp=getattr(quote, "timestamp", None),
                )
                quote_available = bool(metrics.get("quote_available", False))
                row["stage_time"] = position.entry_time.astimezone(KST).strftime("%H:%M:%S")
                current_text = str(metrics.get("current_text", "조회대기"))
                row["display_price"] = (
                    f"평단 {float(position.entry_price):,.0f} / 현재 {current_text} / "
                    f"목표 {float(position.target_price):,.0f}"
                )
                if quote_available:
                    pnl_text = (
                        f"{metrics['label']} {float(metrics['pnl']):+,.0f}원 "
                        f"({float(metrics['pnl_pct']):+.2%})"
                    )
                else:
                    pnl_text = f"{metrics['label']} · 손익 계산 보류"
                row["reason"] = (
                    f"{pnl_text} · 자동손절 없음 · "
                    f"완료사이클 {self._v7541_completed_cycles.get(symbol, 0)}회"
                )
            else:
                row.setdefault("display_price", str(row.get("price", "")))
        snapshot["rows"] = rows
        snapshot["universe"] = {
            "rank_request_limit": self.rank_limit,
            "pool_limit": self.pool_limit,
            "active_count": len(self.active_symbols),
            "supply_pool_limit": self.loose_supply_config.supply_pool_limit,
            "hot_count": self.hot_candidate_count,
            "hot_refresh_sec": self.chart_refresh_hot,
            "general_refresh_sec": self.chart_refresh,
            "candidate_ttl_sec": self.settings.candidate_ttl_sec,
            "sources": "상승률+거래량급증+외국인기관쌍끌이+구조유지",
        }
        return snapshot

    def _compact_report(self) -> None:
        self._sync_daily_state()
        self._enforce_fixed_targets()
        snapshot = self._gui_snapshot()
        if self.gui_bridge is not None:
            try:
                self.gui_bridge.publish(snapshot)
            except Exception as exc:
                self.logger.warning("GUI 목록 갱신 실패: %s", exc)

        now = datetime.now(KST)
        state = str(getattr(self._market_snapshot, "state", "DATA_HALT"))
        entry_allowed, entry_reason = market_entry_allowed(state)
        if self.manual_new_entries_paused:
            entry_allowed, entry_reason = False, "장중명령 신규매수중지"
        rows = list(snapshot.get("rows", []))
        grouped = {
            stage: [row for row in rows if row.get("stage") == stage]
            for stage in v7525.STAGE_ORDER
        }

        estimated_pnl = 0.0
        liquidation_value = float(self.broker.cash)
        unavailable_quotes = 0
        stale_quotes = 0
        holdings: list[tuple[str, Any, dict[str, float | str | bool]]] = []
        for symbol, position in self.broker.positions.items():
            quote = self.latest_quotes.get(symbol)
            raw_current = float(quote.price) if quote is not None else 0.0
            metrics = holding_metrics(
                position,
                raw_current,
                self.settings,
                quote_available=quote is not None,
                quote_timestamp=getattr(quote, "timestamp", None),
            )
            if bool(metrics.get("quote_available", False)):
                estimated_pnl += float(metrics["pnl"])
            else:
                unavailable_quotes += 1
                stale_quotes += int(bool(metrics.get("stale", False)))
            liquidation_value += float(metrics["proceeds"])
            holdings.append((symbol, position, metrics))
        holdings.sort(key=lambda item: item[1].entry_time)

        width = 168
        print("\n" + "=" * width)
        print(
            f"[{now:%H:%M}] 5분 요약 | 시장={v7518.market_label_short(state)} | "
            f"신규매수={'가능' if entry_allowed else '보류'}({entry_reason}) | "
            f"관심={len(grouped['관심종목']):>3} 관찰={len(grouped['관찰후보']):>3} "
            f"가능={len(grouped['매수가능성']):>3} 직전={len(grouped['매수직전']):>3} "
            f"보류={len(grouped['시장보류']):>3} 보유={len(holdings):>2}"
        )
        universe = snapshot.get("universe", {})
        print(
            f"감시원천={universe.get('sources', '-')} | 순위요청상한=각 {universe.get('rank_request_limit', 0)} | "
            f"통합풀상한={universe.get('pool_limit', 0)} | 현재감시={universe.get('active_count', 0)} | "
            f"매물전용상한={universe.get('supply_pool_limit', 0)} | "
            f"HOT={universe.get('hot_count', 0)}/{universe.get('hot_refresh_sec', 0):.0f}초 | "
            f"일반분봉={universe.get('general_refresh_sec', 0):.0f}초"
        )
        quote_note = (
            f" (손익계산보류 {unavailable_quotes}종목/시세지연 {stale_quotes}종목)"
            if unavailable_quotes else ""
        )
        print(
            f"평가자산={liquidation_value:>16,.0f}원 | 현금={float(self.broker.cash):>16,.0f}원 | "
            f"실현={float(self.broker.realized_pnl):>+13,.0f}원 | "
            f"평가={estimated_pnl:>+13,.0f}원{quote_note}"
        )

        limits = {
            "관심종목": 15,
            "관찰후보": 15,
            "매수가능성": 15,
            "매수직전": 12,
            "시장보류": 12,
        }
        for stage in ("관심종목", "관찰후보", "매수가능성", "매수직전", "시장보류"):
            stage_rows = grouped[stage]
            print("-" * width)
            print(f"[{stage}] {len(stage_rows)}종목")
            if not stage_rows:
                print("  없음")
                continue
            print(
                "  " + v7518.pad("편입시각", 8) + " " + v7518.pad("종목명", 20) + " "
                + v7518.pad("코드", 6) + " " + v7518.pad("현재가", 12, right=True) + "  이유"
            )
            for row in stage_rows[: limits[stage]]:
                print(
                    "  " + v7518.pad(str(row.get("stage_time", row.get("time", "-"))), 8) + " "
                    + v7518.pad(str(row.get("name", "")), 20) + " "
                    + v7518.pad(str(row.get("symbol", "")), 6) + " "
                    + v7518.pad(str(row.get("price", "")), 12, right=True) + "  "
                    + str(row.get("reason", ""))[:92]
                )
            hidden = len(stage_rows) - limits[stage]
            if hidden > 0:
                print(f"  ... 나머지 {hidden}종목은 GUI·상세로그")

        print("-" * width)
        print(f"[보유종목] {len(holdings)}종목 | 목표=매수가 대비 +1.5% | 자동손절=없음")
        if holdings:
            print(
                "  " + v7518.pad("매수시각", 8) + " " + v7518.pad("종목명", 20) + " "
                + v7518.pad("코드", 6) + " " + v7518.pad("수량", 9, right=True) + " "
                + v7518.pad("평단", 11, right=True) + " " + v7518.pad("현재", 11, right=True) + " "
                + v7518.pad("구분", 5) + " " + v7518.pad("수익률", 9, right=True) + " "
                + v7518.pad("평가손익", 14, right=True) + " " + v7518.pad("목표", 11, right=True) + "  재매수"
            )
            for symbol, position, metrics in holdings:
                quote_available = bool(metrics.get("quote_available", True))
                current_text = str(metrics.get("current_text", "조회대기"))
                rate_text = f"{float(metrics['pnl_pct']):+.2%}" if quote_available else "-"
                pnl_text = f"{float(metrics['pnl']):+,.0f}원" if quote_available else "-"
                print(
                    "  " + v7518.pad(position.entry_time.astimezone(KST).strftime("%H:%M:%S"), 8) + " "
                    + v7518.pad(self.names.get(symbol, symbol), 20) + " "
                    + v7518.pad(symbol, 6) + " "
                    + v7518.pad(f"{int(position.quantity):,}", 9, right=True) + " "
                    + v7518.pad(f"{float(position.entry_price):,.0f}", 11, right=True) + " "
                    + v7518.pad(current_text, 11, right=True) + " "
                    + v7518.pad(str(metrics["label"]), 5) + " "
                    + v7518.pad(rate_text, 9, right=True) + " "
                    + v7518.pad(pnl_text, 14, right=True) + " "
                    + v7518.pad(f"{float(position.target_price):,.0f}", 11, right=True) + "  "
                    + f"완료{self._v7541_completed_cycles.get(symbol, 0)}회/새조건후 무제한"
                )
        else:
            print("  없음")

        print(f"[오늘 매도종목] {len(grouped['매도종목'])}건")
        for row in grouped["매도종목"][-10:]:
            print(
                f"  {row.get('time', '-'):8} {v7518.pad(str(row.get('name', '')), 20)} "
                f"{v7518.pad(str(row.get('symbol', '')), 6)} "
                f"{v7518.pad(str(row.get('price', '')), 12, right=True)}  {row.get('reason', '')}"
            )
        if not grouped["매도종목"]:
            print("  없음")
        print("=" * width + "\n")
        self.save_pattern_state()


v7525.V7525Engine = V7542Engine
v7525.VERSION = VERSION


class V7542Gui(v7538.BaseGui):
    """단계 편입시각을 표시하는 V75.42 GUI. 더블클릭 기본, 한 번 클릭 선택 가능."""

    DEBOUNCE_SEC = 0.6

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

        root.title(f"V75.42 관심·관찰·매수·매도 — {mode_label()} 영웅문 연동")
        root.geometry("1420x760")
        root.minsize(1120, 560)
        hero = TargetIdentityHeroLink(v7525.HERO_LINK_FILE)

        toolbar = ttk.Frame(root, padding=8)
        toolbar.pack(fill="x")
        status_var = tk.StringVar(
            value=f"종목 줄을 {mode_label()}하세요."
            if hero.configured and hero._config_valid()
            else "처음 한 번만 영웅문 위치 저장을 하세요."
        )
        time_var = tk.StringVar(value="갱신 전")
        ttk.Button(toolbar, text="영웅문 위치 저장", command=lambda: begin_capture()).pack(side="left")
        ttk.Button(toolbar, text="위치 설정 초기화", command=lambda: do_reset()).pack(side="left", padx=(6, 0))
        ttk.Button(toolbar, text="위치 상태 확인", command=lambda: check_link_state()).pack(side="left", padx=(6, 0))
        single_var = tk.BooleanVar(value=click_mode["single"])
        ttk.Checkbutton(
            toolbar,
            text="한 번 클릭 사용",
            variable=single_var,
            command=lambda: change_click_mode(),
        ).pack(side="left", padx=(10, 0))
        mode_help_var = tk.StringVar(value=f"  {mode_label()} → 코드 복사 + 자동입력 + Enter")
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

        state = {"last_symbol": "", "last_ts": 0.0}

        def tk_copy(symbol: str) -> bool:
            try:
                root.clipboard_clear()
                root.clipboard_append(symbol)
                root.update()
                return str(root.clipboard_get()) == symbol
            except Exception as exc:
                diag(f"TK_CLIPBOARD_FAIL symbol={symbol} error={type(exc).__name__}")
                return False

        def switch_symbol(symbol: str) -> None:
            if not symbol.isdigit() or len(symbol) != 6:
                status_var.set(f"종목코드를 읽지 못했습니다: {symbol!r}")
                return
            now_mono = time.monotonic()
            if click_mode["single"] and symbol == state["last_symbol"] and (
                now_mono - state["last_ts"]
            ) < self.DEBOUNCE_SEC:
                return
            state["last_symbol"] = symbol
            state["last_ts"] = now_mono
            copied = tk_copy(symbol)
            diag(f"CLICK symbol={symbol} tk_clipboard={copied} mode={mode_label()}")
            status_var.set(f"{symbol} 영웅문 입력 중…")
            root.update_idletasks()
            try:
                status, message = hero.send_symbol(symbol)
            except Exception as exc:
                status, message = LinkStatus.FAILED, f"오류: {type(exc).__name__}"
                diag(f"SEND_EXC symbol={symbol} {type(exc).__name__}: {exc}")
            prefix = {
                "VERIFIED": "✅ ",
                "SENT_UNVERIFIED": "⚠ ",
                "FAILED": "❌ ",
            }.get(status.value, "")
            if status != LinkStatus.VERIFIED and copied:
                message = f"{message} (코드 복사됨 — 수동 Ctrl+V 가능)"
            status_var.set(f"{prefix}{message}")

        def selected_symbol(event: Any) -> None:
            row_id = tree.identify_row(event.y)
            if not row_id or tree.identify_region(event.x, event.y) not in ("cell", "tree"):
                return
            tree.selection_set(row_id)
            values = tree.item(row_id, "values")
            if len(values) >= 5:
                switch_symbol(str(values[4]).strip())

        def apply_click_binding() -> None:
            tree.unbind("<ButtonRelease-1>")
            tree.unbind("<Double-1>")
            if click_mode["single"]:
                tree.bind("<ButtonRelease-1>", selected_symbol)
            else:
                tree.bind("<Double-1>", selected_symbol)
            mode_help_var.set(f"  {mode_label()} → 코드 복사 + 자동입력 + Enter")
            root.title(f"V75.42 관심·관찰·매수·매도 — {mode_label()} 영웅문 연동")

        def change_click_mode() -> None:
            click_mode["single"] = bool(single_var.get())
            state["last_symbol"] = ""
            state["last_ts"] = 0.0
            apply_click_binding()
            status_var.set(f"클릭 방식을 {mode_label()}으로 변경했습니다.")

        def do_reset() -> None:
            hero.reset_config()
            status_var.set("위치 설정을 초기화했습니다. 영웅문 위치 저장을 다시 하세요.")

        def check_link_state() -> None:
            if not hero.configured:
                status_var.set("연동 위치가 아직 저장되지 않았습니다.")
            elif not hero._config_valid():
                status_var.set("저장 위치가 현재 화면과 맞지 않습니다. 위치 저장을 다시 하세요.")
            else:
                status_var.set("연동 위치 설정 정상 — 종목 줄을 더블클릭해 실제 전환을 확인하세요.")

        def finish_capture() -> None:
            ok, message = hero.capture_cursor_target()
            status_var.set(
                f"{message} — 이제 종목 줄을 {mode_label()}하세요."
                if ok
                else f"위치 저장 실패: {message}"
            )

        def countdown(remaining: int) -> None:
            if remaining <= 0:
                finish_capture()
                return
            status_var.set(f"{remaining}초 안에 영웅문 종목코드 입력칸을 클릭하고 그대로 두세요.")
            root.after(1000, lambda: countdown(remaining - 1))

        def begin_capture() -> None:
            messagebox.showinfo(
                "영웅문 위치 저장",
                "최초 한 번만 설정합니다. 확인 후 5초 안에 영웅문 종목코드 입력칸을 클릭하세요.\n"
                "클릭한 뒤 저장 완료까지 마우스를 움직이지 마세요.",
            )
            countdown(5)

        def apply_snapshot(snapshot: dict[str, Any]) -> None:
            for iid in tree.get_children():
                tree.delete(iid)
            rows = list(snapshot.get("rows", []))
            counters: dict[str, int] = {}
            for row in rows:
                stage = str(row.get("stage", ""))
                counters[stage] = counters.get(stage, 0) + 1
                tree.insert(
                    "",
                    "end",
                    values=(
                        counters[stage],
                        stage,
                        row.get("stage_time", row.get("time", "-")),
                        row.get("name", ""),
                        row.get("symbol", ""),
                        row.get("display_price", row.get("price", "")),
                        row.get("reason", ""),
                    ),
                )
            time_var.set(str(snapshot.get("updated", "")))
            ready_count = sum(str(row.get("stage", "")) == "매수직전" for row in rows)
            holdings_count = sum(str(row.get("stage", "")) == "매수종목" for row in rows)
            universe = snapshot.get("universe", {})
            if hero.configured and hero._config_valid():
                status_var.set(
                    f"시장 {snapshot.get('market', '-')} | 현재감시 {universe.get('active_count', '-')} | "
                    f"매수직전 {ready_count} | 보유 {holdings_count} | {mode_label()} 연동"
                )
            elif hero.configured:
                status_var.set("저장 위치가 현재 화면과 다릅니다 — 위치 저장을 다시 하세요.")
            else:
                status_var.set("먼저 영웅문 위치 저장을 한 번 하세요.")

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
        root.protocol("WM_DELETE_WINDOW", lambda: root.withdraw())
        root.after(200, poll)
        root.mainloop()


v7525.SimpleDoubleClickGui = V7542Gui


def _run_test(filename: str, module_name: str) -> tuple[bool, str]:
    path = BASE_DIR / filename
    if not path.exists():
        return False, f"{filename} 없음"
    try:
        spec = importlib.util.spec_from_file_location(module_name, path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        code = module.run()
        return code == 0, ("통과" if code == 0 else "실패")
    except Exception as exc:
        return False, f"예외 {type(exc).__name__}: {exc}"


def self_check() -> list[tuple[str, bool, str]]:
    results = list(_ORIGINAL_SELF_CHECK())
    regression_ok, regression_message = _run_test(
        "test_v75_41_regression.py", "v7541_regression_selfcheck"
    )
    e2e_ok, e2e_message = _run_test(
        "test_v75_41_end_to_end.py", "v7541_e2e_selfcheck"
    )
    stress_ok, stress_message = _run_test(
        "test_v75_41_stress.py", "v7541_stress_selfcheck"
    )
    audit_ok, audit_message = _run_test(
        "test_v75_42_special_audit.py", "v7542_special_audit_selfcheck"
    )
    results.extend(
        [
            ("V75.40기준SHA", sha256_file(BASE_DIR / BASE_FILENAME) == BASE_SHA256, BASE_SHA256[:16]),
            ("V75.42기능회귀", regression_ok, regression_message),
            ("V75.42재진입·손익E2E", e2e_ok, e2e_message),
            ("V75.42_400종목스트레스", stress_ok, stress_message),
            ("V75.42특별교차검증", audit_ok, audit_message),
            ("시세신선도손익", HOLDING_QUOTE_MAX_AGE_SEC == 30.0, "30초 초과 손익보류"),
            ("모의TR호출예산", MOCK_PER_TR_MIN_INTERVAL_SEC >= 1.0, "TR별 1초 이하"),
            ("순위연속조회", hasattr(BudgetedPagedKiwoomClient, "_rank_pages"), "cont-yn/next-key"),
            ("강신호3배유지", STRONG_VOLUME_RATIO == 3.0, "3배"),
            ("중간신호2.5배", MEDIUM_VOLUME_RATIO == 2.5, "2.5~3배 보강형"),
            ("약신호2배매수금지", WEAK_VOLUME_RATIO == 2.0, "2~2.5배 관찰"),
            ("순위합집합확대", ACTIVE_RANK_REQUEST_LIMIT == 200 and ACTIVE_POOL_LIMIT == 400, "각200/통합400"),
            ("단계편입시각", "stage_time" in Path(__file__).read_text(encoding="utf-8"), "GUI/콘솔"),
            ("보유손익표시", "평가손익" in Path(__file__).read_text(encoding="utf-8"), "5분 요약"),
            ("목표1.5유지", FIXED_TAKE_PROFIT_PCT == 0.015, "+1.5%"),
        ]
    )
    return results


v7525.self_check = self_check


def main() -> int:
    return int(v7530.main())


if __name__ == "__main__":
    raise SystemExit(main())
