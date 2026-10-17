#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.68 자유 의견·명령 구조화 엔진.

목표
----
1. 사용자가 정형 문법 없이 길게 작성해도 시장/섹터/테마/종목 의견으로 분해한다.
2. 가격·수량이 명확한 매수/매도 요청은 별도의 주문 요청으로 구조화한다.
3. 애매한 표현을 임의 주문으로 바꾸지 않는다.
4. 시간대별 판단 이력을 원자적으로 저장하고 수정·해제·삭제할 수 있게 한다.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import datetime
import json
import os
from pathlib import Path
import re
import tempfile
import uuid
from typing import Any

KST_OFFSET = "+09:00"

@dataclass
class ParsedIntent:
    intent_id: str
    created_at: str
    raw_text: str
    kind: str
    title: str
    summary: str
    scope: list[str] = field(default_factory=list)
    direction: str = ""
    priority: str = "보통"
    time_scope: str = "해제 전까지"
    symbol: str = ""
    name: str = ""
    buy_price: float | None = None
    sell_price: float | None = None
    quantity: int | None = None
    amount: float | None = None
    status: str = "초안"
    confidence: float = 0.0
    warnings: list[str] = field(default_factory=list)
    source: str = "USER_FREE_TEXT"

def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")

def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())

def _money(token: str) -> float | None:
    token = token.replace(",", "").replace("원", "").strip()
    try:
        if "만" in token:
            return float(token.replace("만", "")) * 10000
        return float(token)
    except ValueError:
        return None

def _extract_price(text: str, side: str) -> float | None:
    side_words = "매수|사|담아|진입" if side == "BUY" else "매도|팔|정리|청산"
    patterns = [
        rf"(?:{side_words}).{{0,12}}?(\d[\d,]*(?:\.\d+)?)\s*만?\s*원",
        rf"(\d[\d,]*(?:\.\d+)?)\s*만?\s*원.{{0,12}}?(?:{side_words})",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            raw = m.group(1)
            # 패턴 원문에 '만'이 포함됐는지 보완
            span = text[m.start():m.end()]
            if "만" in span and "만원" in span.replace(" ", ""):
                raw += "만"
            return _money(raw)
    return None

def _extract_quantity(text: str) -> int | None:
    m = re.search(r"(\d[\d,]*)\s*주", text)
    return int(m.group(1).replace(",", "")) if m else None

def _extract_amount(text: str) -> float | None:
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(만)?\s*원\s*(?:어치|만큼|비중)", text)
    if not m:
        return None
    value = float(m.group(1).replace(",", ""))
    return value * 10000 if m.group(2) else value

def _time_scope(text: str) -> str:
    compact = text.replace(" ", "")
    if "오전장" in compact or "오전" in compact:
        return "오늘 오전장"
    if "오늘장종료" in compact or "오늘까지만" in compact or "오늘까지" in compact:
        return "오늘 장 종료까지"
    if "이번주" in compact:
        return "이번 주"
    if "취소전" in compact or "해제전" in compact:
        return "해제 전까지"
    return "해제 전까지"

SECTOR_ALIASES = {
    "반도체": ["반도체", "필반", "필라델피아반도체", "semiconductor"],
    "화장품": ["화장품", "뷰티", "미용"],
    "조선/조선협력": ["조선", "조선소", "한국조선소", "조선협력", "선박"],
    "로봇": ["로봇", "휴머노이드"],
    "2차전지": ["2차전지", "이차전지", "배터리"],
    "바이오": ["바이오", "제약"],
    "방산": ["방산", "방위산업"],
    "전선/변압기/전력기기/전력설비/송배전": [
        "전선", "변압기", "전력기기", "전력설비", "송배전", "배전기기",
    ],
}

STOCK_ALIASES = {
    "삼성전자": ("005930", ("삼성전자", "삼전")),
    "SK하이닉스": ("000660", ("SK하이닉스", "에스케이하이닉스", "하이닉스")),
}

def _scopes(text: str) -> list[str]:
    found: list[str] = []
    low = text.lower()
    for canonical, aliases in SECTOR_ALIASES.items():
        if any(alias.lower() in low for alias in aliases):
            found.append(canonical)
    return found


def _stock_entities(
    text: str,
    known_names: dict[str, str] | None = None,
) -> list[tuple[str, str]]:
    """자유문장에 명시된 종목만 추출한다. 현재 선택종목을 임의 대입하지 않는다."""
    candidates: dict[str, tuple[str, tuple[str, ...]]] = dict(STOCK_ALIASES)
    for name, symbol in dict(known_names or {}).items():
        clean_name = str(name).strip()
        clean_symbol = str(symbol).strip()
        if clean_name and re.fullmatch(r"\d{6}", clean_symbol):
            existing = candidates.get(clean_name)
            aliases = tuple(dict.fromkeys((clean_name, *(existing[1] if existing else ()))))
            candidates[clean_name] = (clean_symbol, aliases)
    compact = text.replace(" ", "").lower()
    found: list[tuple[str, str]] = []
    for canonical, (symbol, aliases) in candidates.items():
        if any(alias.replace(" ", "").lower() in compact for alias in aliases):
            pair = (canonical, symbol)
            if pair not in found:
                found.append(pair)
    return found


def _korean_eok_value(number: str, unit: str) -> int:
    value = float(number.replace(",", ""))
    multiplier = {"억": 1, "백억": 100, "천억": 1000}.get(unit, 1)
    return int(value * multiplier)


def _flow_summary(text: str, entities: list[tuple[str, str]]) -> tuple[str, str]:
    compact = _clean(text)
    pieces: list[str] = []
    direction = "RISK_OFF" if "매도" in compact else "RISK_ON" if "매수" in compact else "OBSERVE"
    if "주식선물" in compact and entities:
        name = entities[0][0]
        match = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(천억|백억|억)\s*(?:원)?\s*(매도|매수)", compact)
        amount = f" {_korean_eok_value(match.group(1), match.group(2)):,}억원" if match else ""
        side = match.group(3) if match else ("매도" if "매도" in compact else "매수" if "매수" in compact else "수급")
        return f"{name} 주식선물 외국인{amount} {side}", direction
    if re.search(r"외국인.*선물|선물.*외국인", compact):
        side = "대량매도" if re.search(r"대량\s*매도|매도.*대량", compact) else "매도" if "매도" in compact else "매수" if "매수" in compact else "수급"
        pieces.append(f"외국인 선물 {side}")
    for option in ("콜", "풋"):
        match = re.search(
            rf"{option}.{{0,14}}?(\d[\d,]*(?:\.\d+)?)\s*(천억|백억|억)\s*(?:원)?\s*(매도|매수)",
            compact,
        )
        if match:
            pieces.append(
                f"{option} {_korean_eok_value(match.group(1), match.group(2)):,}억원 {match.group(3)}"
            )
        elif option in compact and ("매도" in compact or "매수" in compact):
            pieces.append(f"{option} {'매도' if '매도' in compact else '매수'}")
    return " · ".join(dict.fromkeys(pieces)) or compact[:120], direction

def _priority(text: str) -> str:
    if re.search(r"비중\s*있게|강하게|최우선|집중|우선", text):
        return "높음"
    if re.search(r"보지\s*말|쳐다보지\s*말|제외|금지|회피", text):
        return "차단"
    if re.search(r"가볍게|참고|약하게", text):
        return "낮음"
    return "보통"

def parse_free_text(
    text: str,
    selected_symbol: str = "",
    selected_name: str = "",
    known_names: dict[str, str] | None = None,
) -> list[ParsedIntent]:
    raw = _clean(text)
    if not raw:
        return []
    created = _now_iso()
    intents: list[ParsedIntent] = []
    scopes = _scopes(raw)
    entities = _stock_entities(raw, known_names)
    time_scope = _time_scope(raw)

    # 명확한 매수/매도 요청. 부정문/질문문은 실행형으로 보지 않는다.
    negated = bool(re.search(r"매수하지\s*마|사지\s*마|매도하지\s*마|팔지\s*마", raw))
    question = raw.endswith("?") or bool(re.search(r"맞나|어떨까|할까", raw))
    has_buy = bool(re.search(r"즉시\s*매수|매수해|매수하자|사자|사줘|담아", raw))
    has_sell = bool(re.search(r"즉시\s*매도|매도해|매도하자|팔자|팔아|정리해", raw))
    conditional_theme_buy = bool(
        re.search(
            r"(?:관련\s*종목|테마).{0,30}(?:로직|조건).{0,12}"
            r"(?:맞|충족).{0,12}(?:매수|사줘)",
            raw,
        )
    )
    buy_price = _extract_price(raw, "BUY")
    sell_price = _extract_price(raw, "SELL")
    qty = _extract_quantity(raw)
    amount = _extract_amount(raw)

    if (has_buy or has_sell) and not conditional_theme_buy and not negated and not question:
        side = "BUY" if has_buy and not has_sell else "SELL" if has_sell and not has_buy else "COMPOSITE_ORDER"
        warnings: list[str] = []
        if not (selected_symbol or selected_name):
            warnings.append("종목이 명확하지 않습니다.")
        if side in {"BUY", "SELL"} and not any((qty, amount)):
            warnings.append("수량 또는 금액이 없어 자동 실행할 수 없습니다.")
        if side == "BUY" and buy_price is None and "지금" not in raw:
            warnings.append("매수가격 또는 '지금 즉시' 표현이 필요합니다.")
        if side == "SELL" and sell_price is None and "지금" not in raw:
            warnings.append("매도가격 또는 '지금 즉시' 표현이 필요합니다.")
        intents.append(ParsedIntent(
            intent_id=str(uuid.uuid4()),
            created_at=created,
            raw_text=raw,
            kind="ORDER_REQUEST",
            title=f"{selected_name or selected_symbol or '종목 미정'} {side}",
            summary=f"{'매수' if side == 'BUY' else '매도' if side == 'SELL' else '복합 주문'} 요청",
            scope=[selected_name or selected_symbol] if (selected_name or selected_symbol) else [],
            direction=side,
            priority="높음",
            time_scope=time_scope,
            symbol=selected_symbol,
            name=selected_name,
            buy_price=buy_price,
            sell_price=sell_price,
            quantity=qty,
            amount=amount,
            confidence=0.95 if not warnings else 0.62,
            warnings=warnings,
        ))

    # 섹터 회피/차단
    if scopes and re.search(r"보지\s*말|쳐다보지\s*말|회피|제외|금지|신규.*제한", raw):
        intents.append(ParsedIntent(
            intent_id=str(uuid.uuid4()), created_at=created, raw_text=raw,
            kind="SECTOR_GUARD", title="섹터 회피·제한",
            summary=f"{', '.join(scopes)} 신규 편입 제한",
            scope=scopes, direction="BLOCK_OR_DOWNWEIGHT", priority="차단",
            time_scope=time_scope, confidence=0.92,
        ))

    # 테마/섹터 우선 관찰
    if scopes and re.search(r"관찰|관심|추천|테마|비중|우선|호재|뉴스|형성\s*가능", raw):
        positive_scopes = []
        blocked = set()
        # 부정 구절에 가까운 섹터는 우선관찰에서 제외
        sentences = [s.strip() for s in re.split(r"[.!?。]+", raw) if s.strip()]
        for scope in scopes:
            aliases = SECTOR_ALIASES[scope]
            related = [s for s in sentences if any(alias.lower() in s.lower() for alias in aliases)]
            negative = any(re.search(r"보지\s*말|쳐다보지\s*말|회피|제외|금지", s) for s in related)
            positive = any(re.search(r"관찰|관심|추천|테마|비중|우선|호재|뉴스|형성\s*가능", s) for s in related)
            if negative and not positive:
                blocked.add(scope)
            elif positive:
                positive_scopes.append(scope)
        if positive_scopes:
            intents.append(ParsedIntent(
                intent_id=str(uuid.uuid4()), created_at=created, raw_text=raw,
                kind="THEME_PRIORITY", title="테마·섹터 우선 관찰",
                summary=f"{', '.join(positive_scopes)} 우선순위 상향",
                scope=positive_scopes, direction="UPWEIGHT",
                priority=_priority(raw), time_scope=time_scope, confidence=0.90,
            ))

    # 종목명이 문장에 명시된 관찰·추천 요청. 선택된 다른 종목으로 잘못 귀속하지 않는다.
    if entities and re.search(r"관찰|관심|추천|지켜|추적|체크|집중\s*관리|대장주", raw):
        reason = "경제TV 추천" if re.search(r"경제\s*(?:TV|티비)|방송", raw, re.IGNORECASE) else "사용자 관찰 요청"
        for name, symbol in entities:
            intents.append(ParsedIntent(
                intent_id=str(uuid.uuid4()), created_at=created, raw_text=raw,
                kind="STOCK_WATCH", title="종목 집중관리",
                summary=f"{name} 관찰·집중관리 편입 ({reason})",
                scope=[name], direction="WATCH", priority=_priority(raw),
                time_scope=time_scope, symbol=symbol, name=name, confidence=0.93,
            ))

    # 지수선물·옵션·종목 주식선물의 외국인 수급은 주문이 아니라 시간대별 위험판단으로 남긴다.
    if "외국인" in raw and re.search(r"주식선물|선물|콜|풋", raw) and re.search(r"매도|매수|수급", raw):
        summary, flow_direction = _flow_summary(raw, entities)
        stock_future = "주식선물" in raw and bool(entities)
        intents.append(ParsedIntent(
            intent_id=str(uuid.uuid4()), created_at=created, raw_text=raw,
            kind="STOCK_FUTURES_FLOW" if stock_future else "DERIVATIVES_FLOW",
            title="종목선물 외국인 수급" if stock_future else "파생 수급 경계",
            summary=summary + (" → 신규매수 경계" if flow_direction == "RISK_OFF" else " → 흐름 관찰"),
            scope=[name for name, _symbol in entities] or ["시장 전체"],
            direction=flow_direction, priority="높음" if flow_direction == "RISK_OFF" else "보통",
            time_scope=time_scope,
            symbol=entities[0][1] if entities else "",
            name=entities[0][0] if entities else "",
            confidence=0.94,
            warnings=["시장·파생 수급 판단으로 저장하며 자동주문은 실행하지 않습니다."],
        ))

    # 시장 판단/메모는 항상 남겨 시간대별 판단 이력으로 활용
    specialised = any(item.kind in {"THEME_PRIORITY", "STOCK_WATCH", "DERIVATIVES_FLOW", "STOCK_FUTURES_FLOW"} for item in intents)
    if not specialised and re.search(r"지수|시장|폭락|급락|강세|약세|장세|전문가|뉴스|트럼프|협력", raw):
        intents.append(ParsedIntent(
            intent_id=str(uuid.uuid4()), created_at=created, raw_text=raw,
            kind="MARKET_NOTE", title="시장·이슈 판단",
            summary=raw[:120] + ("…" if len(raw) > 120 else ""),
            scope=scopes, priority=_priority(raw), time_scope=time_scope,
            confidence=0.82,
        ))

    if not intents:
        intents.append(ParsedIntent(
            intent_id=str(uuid.uuid4()), created_at=created, raw_text=raw,
            kind="MEMO", title="운용 의견",
            summary=raw[:120] + ("…" if len(raw) > 120 else ""),
            scope=scopes, priority=_priority(raw), time_scope=time_scope,
            confidence=0.65,
            warnings=["실행 조건으로 확정되지 않아 의견 이력으로만 저장합니다."],
        ))
    return intents

class IntentLedger:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def _write(self, rows: list[dict[str, Any]]) -> None:
        payload = json.dumps(rows, ensure_ascii=False, indent=2)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self.path)

    def add_many(self, intents: list[ParsedIntent], status: str = "활성") -> list[dict[str, Any]]:
        rows = self._read()
        existing_ids = {str(row.get("intent_id", "")) for row in rows}
        additions = []
        for intent in intents:
            if str(intent.intent_id) in existing_ids:
                continue
            row = asdict(intent)
            row["status"] = status
            row["updated_at"] = _now_iso()
            rows.append(row)
            additions.append(row)
            existing_ids.add(str(intent.intent_id))
        self._write(rows)
        return additions

    def list_all(self) -> list[dict[str, Any]]:
        return sorted(self._read(), key=lambda r: r.get("created_at", ""), reverse=True)

    def list_active(self) -> list[dict[str, Any]]:
        return [r for r in self.list_all() if r.get("status") == "활성"]

    def set_status(self, intent_id: str, status: str) -> bool:
        rows = self._read()
        changed = False
        for row in rows:
            if row.get("intent_id") == intent_id:
                row["status"] = status
                row["updated_at"] = _now_iso()
                changed = True
                break
        if changed:
            self._write(rows)
        return changed

    def delete(self, intent_id: str) -> bool:
        rows = self._read()
        kept = [r for r in rows if r.get("intent_id") != intent_id]
        if len(kept) == len(rows):
            return False
        self._write(kept)
        return True
