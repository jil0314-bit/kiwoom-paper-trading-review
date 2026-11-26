"""V7620 형 지시 해석 — 2026-11-10. 순수 계산(주문·파일·네트워크 없음).

형 취지: 보유·매수 예정 종목과 오늘 장 판단을 적으면 자동매매에 바로 반영한다.
  ① "삼성전자 로직대로 팔지 말고 5% 수익나면 팔아"  → 목표 평단 +5%(비용포함), 15:19 정리 안 함(이월), -10% 손절 유지
  ② "삼성전자 지금 손절해 / 정리해 / 팔아"           → 즉시 전량 매도(기존 수동매도 경로)
  ③ "삼성전자 원래 로직대로 / 지시 해제"            → ①을 풀고 원래 로직으로
  ④ "오늘 장 약하다 / 하락장 조심"                   → 오늘 초보수 모드 수동 켜기(신규 1차·추가매수 중단, 매도는 그대로)
  ⑤ "장 회복 / 초보수 해제"                          → ④ 끄기
  ⑥ "오늘 2차전지 테마 강하다 에코프로 포스코퓨처엠" → 적힌 종목을 관심종목으로 올려 우선 감시(우리 패턴이 맞을 때만 매수)
  ⑦ "씨어스 절반 팔아 / 50% 매도"                    → 아직 미지원(오류) — 전량 매도로 잘못 나가지 않게
  ⑧ "씨어스 빼 / 제외해 / 관심에서 빼"               → 감시·자동매수에서 제외('편입'이라고 적을 때까지)
  ⑨ "에코프로 지금 사 / 사줘"                        → 즉시 매수(1차)
  ⑩ 그 밖에 '매수/매도' 단어가 든 문장                → 꾸밈말 무시, 지금 매수 / 지금 전량 매도
     (부정문·조건문·예약·가격 적힌 문장·'순매수/매수세' 같은 시황 낱말은 주문으로 읽지 않음)
여기에 안 걸리는 문장은 None → 기존 해석기로 넘긴다.
조건부 손절("3% 빠지면 손절")은 아직 지원하지 않는다 → 잘못 즉시매도되지 않게 오류로 돌려준다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

MIN_PCT = 0.005
MAX_PCT = 0.30

_SELL = r"(?:팔아|팔자|팔어|매도|익절|정리|청산)"
_COND = r"(?:면|되면|나면|오면|가면|이상|도달|까지|에서|일\s*때)"
_LOSS = r"(?:빠지|떨어지|하락하|손실|깨지|밀리|내려가)"


@dataclass
class Interp:
    kind: str                      # V7620_HOLD_PCT / IMMEDIATE_SELL / V7620_RELEASE / V7620_MARKET_WEAK / V7620_MARKET_OK / V7620_THEME_WATCH / ERROR
    symbol: str = ""
    name: str = ""
    pct: float = 0.0
    summary: str = ""
    errors: list[str] = field(default_factory=list)
    watch: list[tuple[str, str]] = field(default_factory=list)   # (symbol, name)


def _names_in(text: str, known: dict[str, str]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    taken: list[tuple[int, int]] = []
    for name in sorted(known, key=len, reverse=True):          # 긴 이름 먼저(삼성전자우 > 삼성전자)
        if len(name) < 2:
            continue
        start = text.find(name)
        while start >= 0:
            end = start + len(name)
            if not any(a < end and start < b for a, b in taken):
                taken.append((start, end))
                if (known[name], name) not in found:
                    found.append((known[name], name))
                break
            start = text.find(name, end)
    for code in re.findall(r"(?<!\d)(\d{6})(?!\d)", text):
        name = next((n for n, c in known.items() if c == code), code)
        if (code, name) not in found:
            found.append((code, name))
    order = {pair: text.find(pair[1]) if pair[1] in text else text.find(pair[0]) for pair in found}
    return sorted(found, key=lambda pair: order[pair])


def _pct(text: str) -> float | None:
    match = re.search(r"([+-]?\d+(?:\.\d+)?)\s*(?:%|퍼센트|퍼|프로)", text)
    return float(match.group(1)) / 100.0 if match else None


def interpret(text: str, *, selected_symbol: str = "", selected_name: str = "",
              known_names: dict[str, str] | None = None, held: set[str] | None = None) -> Interp | None:
    raw = " ".join(str(text or "").split())
    if not raw:
        return None
    known = dict(known_names or {})
    if selected_name and re.fullmatch(r"\d{6}", selected_symbol or ""):
        known.setdefault(selected_name, selected_symbol)
    held = set(held or ())
    named = _names_in(raw, known)

    def target() -> tuple[str, str]:
        if named:
            return named[0]
        if re.fullmatch(r"\d{6}", selected_symbol or ""):
            return selected_symbol, selected_name or selected_symbol
        return "", ""

    # ⑤ 장 회복 / 초보수 해제
    if re.search(r"초보수\s*(?:모드)?\s*(?:해제|풀어|꺼|끄)|(?:장|시장|지수)\S{0,3}\s*(?:회복|좋아졌|괜찮아졌|정상|살아났)", raw):
        return Interp("V7620_MARKET_OK", summary="초보수 모드 해제 → 신규 1차·추가매수 다시 허용")

    # ④ 장 약함
    if (re.search(r"(?:장|시장|지수|코스피|코스닥)\S{0,3}\s*(?:이|가|은|는)?\s*(?:약하|약해|안\s*좋|나쁘|위험|무너|폭락|급락|하락장)", raw)
            or re.search(r"하락장|초보수\s*(?:모드)?\s*(?:켜|켜줘|해|하자|모드로)", raw)) and not re.search(_SELL, raw):
        return Interp("V7620_MARKET_WEAK",
                      summary="오늘 장 약함 → 초보수 모드 켬: 신규 1차·2·3차 추가매수 중단 (매도는 그대로) · 장 끝나면 자동 해제")

    negative = bool(re.search(r"말고|하지\s*마|마라|금지|말아", raw))
    has_price = bool(re.search(r"\d[\d,]*(?:\.\d+)?\s*(?:만원|원)", raw))

    # ⑦ V7624: 일부 매도(절반·N%·N주)는 아직 미지원 — 전량 매도로 잘못 나가지 않게 막는다
    if re.search(_SELL, raw) and not re.search(_COND + r"|수익|상승|오르", raw) and (
            re.search(r"절반|반만|반\s*(?:매도|팔)|일부|\d+\s*주\s*(?:만\s*)?(?:매도|팔)", raw) or _pct(raw) is not None):
        symbol, name = target()
        return Interp("ERROR", symbol, name, summary="일부 매도 지시",
                      errors=["일부(절반·몇 %·몇 주) 매도는 아직 지원하지 않습니다. 다 팔려면 '전량 매도', 가격을 정하려면 'OO원에 매도'라고 적어주세요."])

    # ⑧ V7624: 빼 / 제외 → 오늘 감시·자동매수에서 제외 (기존 '제외' 경로)
    if re.search(r"제외|빼(?:줘|라|자|\s*줘)?\s*[.!]?$|에서\s*빼|목록에서\s*삭제", raw) and not negative and not has_price:
        symbol, name = target()
        if not symbol:
            return Interp("ERROR", errors=["제외할 종목을 선택하거나 종목명을 적어주세요."])
        if symbol in held:
            return Interp("ERROR", symbol, name, errors=[f"{name}은(는) 보유 중입니다. 팔려면 '전량 매도'라고 적어주세요."])
        return Interp("EXCLUDE", symbol, name, summary=f"{name} 감시·자동매수에서 제외 (다시 넣으려면 '{name} 편입')")

    # ⑨ V7624: '지금 사 / 사줘 / 담아' → 즉시 매수(1차)
    if re.search(r"(?:^|\s)(?:사|사줘|사자|사라|담아|담아줘)\s*[.!]?$", raw) and not negative and not has_price \
            and not re.search(r"(?:면|되면|오면|맞으면|충족)", raw):
        symbol, name = target()
        if not symbol:
            return Interp("ERROR", errors=["매수할 종목을 선택하거나 종목명을 적어주세요."])
        if symbol in held:
            return Interp("ERROR", symbol, name, errors=[f"{name}은(는) 이미 보유 중입니다."])
        return Interp("IMMEDIATE_BUY", symbol, name, summary=f"{name} 지금 매수(1차)")

    # 조건부 손절(%)은 아직 미지원 — 즉시매도로 잘못 읽히지 않게 막는다
    if re.search(r"손절|" + _LOSS, raw) and _pct(raw) is not None and re.search(_COND, raw):
        symbol, name = target()
        return Interp("ERROR", symbol, name, errors=[
            "'몇 % 빠지면 손절'은 아직 자동 반영하지 않습니다. 지금 팔려면 '지금 손절해', 가격으로는 'OO원에 매도'라고 적어주세요."])

    # ① 수익 % 목표 + 로직 매도 대신
    pct = _pct(raw)
    if pct is not None and re.search(_SELL, raw) and (re.search(_COND, raw) or re.search(r"수익|상승|오르", raw)):
        symbol, name = target()
        errors = []
        if not symbol:
            errors.append("종목을 먼저 선택하거나 종목명을 적어주세요.")
        elif symbol not in held:
            errors.append(f"{name}은(는) 지금 보유 중이 아닙니다. 보유 중일 때만 목표 % 지시를 걸 수 있습니다.")
        if pct <= 0:
            errors.append("수익 목표는 + % 로 적어주세요.")
        elif not MIN_PCT <= pct <= MAX_PCT:
            errors.append(f"목표 {pct:.1%}는 범위 밖입니다(0.5%~30%).")
        return Interp("V7620_HOLD_PCT" if not errors else "ERROR", symbol, name, pct,
                      summary=f"{name} 목표 평단 +{pct:.1%}(비용포함) · 로직 매도·15:19 정리 안 함(다음날까지 보유) · 손절 최초가 -10% 유지",
                      errors=errors)

    # ③ 원래 로직으로
    if re.search(r"(?:원래|기존)?\s*로직대로\s*(?:돌려|해|가|복귀|하자)|지시\s*(?:해제|취소|풀어)|목표\s*(?:해제|취소)", raw) \
            and not re.search(r"말고|하지\s*마", raw):
        symbol, name = target()
        if not symbol:
            return Interp("ERROR", errors=["어느 종목의 지시를 풀지 종목을 선택하거나 이름을 적어주세요."])
        return Interp("V7620_RELEASE", symbol, name, summary=f"{name} 형 지시 해제 → 원래 로직(목표·15:19 정리)으로")

    # ② V7626 형 지시(단순화): 가격 없이 '매도·팔아·손절·정리'가 있으면 나머지 말은 무시하고 지금 전량 매도
    if re.search(_SELL + r"|팔(?:아|자|어|고)|손절", raw) and not has_price and not negative:
        symbol, name = target()
        errors = []
        if not symbol:
            errors.append("종목을 먼저 선택하거나 종목명을 적어주세요.")
        elif symbol not in held:
            errors.append(f"{name}은(는) 지금 보유 중이 아닙니다.")
        return Interp("IMMEDIATE_SELL" if not errors else "ERROR", symbol, name,
                      summary=f"{name} 지금 전량 매도", errors=errors)

    # ⑩ V7626 형 지시(단순화): 가격 없이 '매수·사'가 있으면 지금 매수(1차). 중지·허용 같은 설정 말은 기존 해석으로.
    if (re.search(r"매수|(?:^|\s)사(?:줘|자|라)?(?=\s|$|[.!])|담아", raw) and not has_price and not negative
            and not re.search(r"중지|중단|멈춰|허용|재개|해제|직전|종목|로직|신호|왜|\?", raw)
            and not re.search(r"(?:면|되면|오면|맞으면|충족)(?:\s|$)", raw)):
        symbol, name = target()
        if not symbol:
            return Interp("ERROR", errors=["매수할 종목을 선택하거나 종목명을 적어주세요."])
        if symbol in held:
            return Interp("ERROR", symbol, name, errors=[f"{name}은(는) 이미 보유 중입니다."])
        return Interp("IMMEDIATE_BUY", symbol, name, summary=f"{name} 지금 매수(1차)")

    # ⑥ 테마·주도주 의견 + 종목명
    if re.search(r"테마|섹터|업종|주도|대장|관련주|강하|강세|뜬다|움직", raw) and named and not re.search(_SELL + r"|매수|사줘|사자", raw):
        names = ", ".join(n for _c, n in named)
        return Interp("V7620_THEME_WATCH", watch=named,
                      summary=f"테마·장 의견 저장 + {names} 관심종목 우선 감시 (우리 패턴이 맞을 때만 매수)")
    return None


def target_price(cost_per_share: float, rate: float, commission: float, tax: float, align, tick_size) -> float:
    """평단(비용 포함) 대비 순수익 rate가 남는 매도가."""
    proceeds = 1.0 - float(commission) - float(tax)
    if cost_per_share <= 0 or proceeds <= 0:
        raise ValueError("V7620 목표가 계산에 유효한 매입원가가 필요합니다")
    raw = float(cost_per_share) * (1.0 + float(rate)) / proceeds
    price = float(align(raw, upward=True))
    if price + 1e-9 < raw:
        price += float(tick_size(price))
    return price
