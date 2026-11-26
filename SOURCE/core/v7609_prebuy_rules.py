"""2026-09-15 requested PREBUY signals; no broker, GUI or network access."""
from datetime import datetime, time, timedelta
from math import isfinite
from statistics import mean, median
from types import SimpleNamespace

import v7609_volume_prebuy as volume5
import v7600_three_stage_logic as structure
import v7612_pattern_rules as pattern12  # V7612 형 지정 참고패턴 A/B

RULE_ID = "V7609_PREBUY_20260915"

# V7615 (2026-11-09 형 지시·재현시험 반영)
# 1) 첫 +5% 상승파를 09시 이전(NXT 프리장)으로만 인정하던 제한을 없앤다.
#    기존: 프리장에서 +5%가 없던 종목은 하루 종일 바닥매수·돌파 경로가 열리지 않았다.
WAVE_FIRST_ANYTIME = True
# 2) '그 시각까지 누적거래량 ≥ 전일 전체 2배' 조건은 기준 음봉이 10:00 이후일 때만 적용한다.
#    재현(바닥매수 경로, 1:3:9 분할 운용, 2026-07~11 466종목일):
#      현행 182건 +992만 · 11시 이후만 적용 324건 +1,065만(10시대 신호가 약함) · 10시 이후만 적용 220건 +1,384만
FLOOR_PREV_VOLUME_FROM = time(10)
# V7635 (2026-11-26 에이치브이엠 10:34 오매수 수정, 형 지시: 1분봉 5선 '안착' 기준)
#   그날 10:16 대량음봉 → 10:17 거래바닥(저가 59,800) 뒤 10:32에 57,800까지 바닥이 깨졌는데도
#   ① 옛 바닥을 계속 유효로 보고 ② 14분 전 10:19 양봉을 '반전'으로 재사용하고
#   ③ 종가가 1분 MA5보다 20원 높은 '걸침' 한 봉을 'MA5회복'으로 통과시켜 10:33 봉에 매수했다.
# 1) 바닥 이탈 무효: 거래바닥 봉 이후 저가가 (대량음봉·거래바닥 봉 중 낮은 저가) 아래로 내려가면
#    그 바닥 신호는 버리고 새 대량음봉부터 다시 찾는다. 1~2호가 살짝 밑꼬리(쌍바닥)는 허용하도록
#    FLOOR_BREAK_TOL 만큼 더 내려가야 '이탈'로 본다 (에이치브이엠: 10:18 59,500은 허용, 10:28 59,000부터 이탈).
FLOOR_BREAK_RESET = True
FLOOR_BREAK_TOL = 0.01
# 2) 반전은 '마지막 저점' 이후에 나온 봉만 인정 (바닥 직후 반전이 한 번 켜지면 계속 켜져 있던 문제).
FLOOR_REVERSAL_AFTER_LOW = True
# 3) 5선 기준 = 1분봉 종가가 1분 MA5(그 봉 포함 5개 종가 평균) 위에 연속 N봉.
#    V7635b (2026-11-26 형 결정): 2봉 → 1봉. 재현(1:3:9, A 7/27~11/6 · B 11/13~11/24)
#      수정 전 +392만/-261만 · ①②③(2봉) +401만/-508만 · 2봉만 +379만/-495만 · ①②+1봉 +379만/-259만
#      2봉 대기는 1분 늦은 비싼 매수(89건 중 49건)로 1차 +2%를 놓치고 2·3차까지 물려 손해.
#      에이치브이엠 10:33 오매수는 ①②만으로 막힌다.
FLOOR_MA5_SETTLE_BARS = 1

# V7622 (2026-11-11 형 지시·재현시험 반영): 상승 눌림목 경로(큰 음봉 없이도 매수)
#   1차 상승: 고가가 min(전일종가, 장중저점) 대비 +5% 이상 (09:00 이후 정규장 봉만)
#   눌림: 그 뒤 고점 대비 3~8% 밀림, 오늘 시가는 지킴(8% 넘거나 시가 깨지면 그날 이 경로는 끝)
#   거래바닥: 첫 상승 구간 최대 거래량의 15% 이하이면서 직전 5봉 최소 (5봉 안)
#   매수: 그 뒤 5분선 위 양봉 종가 → 13:00 전, 하루 한 번
#   재현(466종목일, 1:3:9, 1차 100만): 160건 승률 94% 건당 +1.4만 · 바닥매수와 안 겹치는 104건 +1.1만
#   (형 처음 안 2~6% 눌림은 건당 +0.3만으로 약해 3~8%로 정함)
PULLBACK_ENABLED = True
PULLBACK_RISE = 0.05
PULLBACK_MIN = 0.03
PULLBACK_MAX = 0.08
PULLBACK_DRY = 0.15
PULLBACK_WITHIN = 5
PULLBACK_UNTIL = time(13)
# V7630 (2026-11-17 노랑풍선 09:14 신호): 첫 눌림 신호가 09:20 전이면 개장 직후 급등·VI 뒤 되밀림이라
# 거래바닥 비교가 무의미하다 → 그 종목은 그날 눌림목 경로를 쓰지 않는다.
# 재현(158건): 09:20 전 신호 23건 -56만 / 포기 시 합계 +221만 → +277만, 최악 -58만 → -29만.
PULLBACK_FROM = time(9, 20)
# V7627 (2026-11-13): 신호 봉 종가가 전일 종가 이상일 때만 매수. -14% 갭하락 뒤 저점에서 5% 튄 종목
# (큐리옥스바이오시스템즈)이 '1차 상승'으로 잡혀 3차까지 물린 사례 차단. 재현 160건 중 해당 3건(+6만)뿐.
PULLBACK_ABOVE_PREV_CLOSE = True
# V7631 (2026-11-17 밤 형 지시): 거래바닥 무렵의 바닥 신호는 한 가지가 아니다.
#   ① 작은 양봉 2~3개 ② 음봉이어도 아래꼬리(길든 짧든) ③ 작은/큰 거래로 5분선 돌파
#   → 그 뒤 양봉이든 음봉이든 5분선 위에서 지지되면 매수(전에는 '5분선 위 양봉'만).
#   재현(466종목일, 1:3:9, 신호봉 저가 확인·09:20 포함): 현행 134건 +292만 최악 -29만
#     → 다형 신호 133건 +313만 최악 -16만. (신호 뒤 1~2봉 더 확인하면 +280만/+226만으로 오히려 나빠짐)
PULLBACK_ANY_SIGNAL = True
PULLBACK_SIGNAL_WITHIN = 5      # 거래바닥 봉부터 이 봉 수 안에 바닥 신호
PULLBACK_HOLD_WITHIN = 5        # 바닥 신호부터 이 봉 수 안에 5분선 위 지지 봉
PULLBACK_SMALL_BODY = 0.6       # '작은 양봉' = 몸통이 1차 상승 전후 봉 길이 중앙값의 60% 이하

# V7633 (2026-11-25 형 지시 — 에이피알 11:27 전고점 바로 아래 매수 사례)
# ※ 재현 결과(1:3:9, 설계기간 7/27~11/6 527종목일 · 최근 11/13~11/24 287종목일, 현행 +331만 / -415만):
#    ①만 켬 +309만 / -443만 · ①② +310만 / -537만 · ①③ +243만 / -587만 · ①②③ +218만 / -542만
#    → 세 가지 모두 두 기간 다 손해라 코드만 넣고 꺼 둔다(True/값으로 바꾸면 바로 켜짐).
#    특히 ②③은 눌림목 경로가 더 이른 봉에서 먼저 사 버려 더 나은 바닥매수 자리를 빼앗았다.
# 1) 돌파 경로의 '전고점'을 직전 10분 고점이 아니라 그날(09:00 이후) 고점으로 본다.
#    에이피알: 직전 10분 고점 290,500을 넘었다고 '돌파'로 봤지만 당일 고점은 297,000 →
#    고점 바로 아래 295,000에 매수, 목표가 302,000은 전고점을 뚫어야만 닿는 자리였다.
BREAKOUT_DAY_HIGH = False
# 2) 눌림목 경로의 '시가 지킴'을 갭상승 종목에 맞게 고친다. 시가 대신 '시가와 개장 첫 3분 저가 중 낮은 값'.
#    에이피알: 시가 274,000(갭 +5%) → 09:02 저가 270,500이 시가를 깼다고 그날 경로가 09:02에 끝났다.
PULLBACK_OPEN_GRACE_BARS = 0   # 켜려면 3
# 3) 하루 첫 신호 한 번만 보던 것을, 앞 신호 뒤에 '거래바닥이 새로 한 번 더' 나오면 다시 볼 수 있게(재무장) 한다.
#    09:20 전 이른 신호도 그날 포기 대신 '새 거래바닥이 나올 때까지 대기'로 바뀐다.
#    (지수 급락 보류 등으로 첫 신호를 놓치면 그날 다시는 못 사던 문제 — 에이피알 11:18~20 재바닥)
PULLBACK_REARM = False


def in_session(now):
    return time(8) <= volume5._stamp(now).time() < time(20)


def completed(raw, now):
    """Validate actual bars; never synthesize a missing minute."""
    now = volume5._stamp(now)
    cutoff = now.replace(second=0, microsecond=0)
    rows = []
    try:
        for bar in raw:
            stamp = volume5._stamp(bar.get('timestamp') if isinstance(bar, dict) else bar.timestamp)
            if stamp.date() == now.date() and time(8) <= stamp.time() < time(20) and stamp < cutoff:
                rows.append(volume5._bar(bar))
        rows.sort(key=lambda b: b['timestamp'])
        if not rows or len({b['timestamp'] for b in rows}) != len(rows):
            return []
        if volume5._stamp(rows[-1]['timestamp']) != cutoff - timedelta(minutes=1):
            return []
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return []
    return rows


def _contiguous(rows):
    return all(volume5._stamp(b['timestamp']) - volume5._stamp(a['timestamp']) == timedelta(minutes=1)
               for a, b in zip(rows, rows[1:]))


def waves(rows):
    """First +5% candle before 09:00, then fresh +3% legs after falling closes."""
    result = []
    low, anchor, rising = float('inf'), 0, False
    for i, b in enumerate(rows):
        if i and not _contiguous(rows[i-1:i+1]):
            low, anchor, rising = b['low'], i, False
            continue
        if rising and b['close'] < rows[i-1]['close']:
            low, anchor, rising = b['low'], i, False
            continue
        if b['low'] < low:
            low, anchor = b['low'], i
        threshold = 3 if result else 5
        before_nine = volume5._stamp(b['timestamp']).time() < time(9)
        if (not rising and (result or before_nine or WAVE_FIRST_ANYTIME) and i-anchor+1 <= 15
                and b['volume'] > 0 and b['close'] * 100 >= low * (100 + threshold)):
            result.append(dict(index=i, anchor=anchor, threshold=threshold))
            rising = True
    return result


def _above_ma(rows, index, periods=(5, 10)):
    return any(index+1 >= n and rows[index]['close'] >= mean(b['close'] for b in rows[index-n+1:index+1])
               for n in periods)


def _structure_ok(rows, flush_index, now):
    bars = [SimpleNamespace(**dict(b, timestamp=volume5._stamp(b['timestamp']))) for b in rows]
    risk = structure.structural_distribution_risk(bars[flush_index], bars, now=now, session_bars=bars)
    return not risk.allowed or structure.structural_reclaim_confirmed(risk, bars, now=now, session_bars=bars)


def _floor(rows, events, now, previous_volume):
    if not events or previous_volume is None or not isfinite(float(previous_volume)) or previous_volume <= 0:
        return None
    # Preserve the original absolute-liquidity guard for the bottom route.
    if sum(b['close']*b['volume'] for b in rows) < structure._minimum_traded_value(now):
        return None
    if now.time() >= time(14) and rows[-1]['close'] <= 5000 and sum(b['volume'] for b in rows) <= 500000:
        return None
    # Existing thirty-bar pullback evidence horizon is retained.
    first = max(5, len(rows)-30, events[-1]['index']+1)
    cumulative = 0
    totals = []
    for b in rows:
        cumulative += b['volume']
        totals.append(cumulative)
    for j in range(len(rows)-2, first-1, -1):
        f = rows[j]
        refs = rows[j-5:j]
        if (not _contiguous(rows[j-5:]) or any(b['volume'] <= 0 for b in refs)
                or not f['close'] < min(f['open'], rows[j-1]['close'])
                or f['volume'] < 3 * mean(b['volume'] for b in refs)
                or (volume5._stamp(f['timestamp']).time() >= FLOOR_PREV_VOLUME_FROM
                    and totals[j] < 2 * previous_volume)):
            continue
        if not _structure_ok(rows, j, now):
            continue
        for k in range(j+1, len(rows)):
            b = rows[k]
            prior = [r['volume'] for r in rows[max(0, k-5):k] if r['volume'] > 0]
            if (b['volume'] <= 0 or not prior or b['volume'] > f['volume']*.35
                    or b['volume'] > median(prior)*.55 or b['volume'] > min(prior)):
                continue
            # Reversal must follow the floor, not precede it.
            # V7635-1: 바닥이 깨지면 이 대량음봉·거래바닥 묶음은 무효 → 더 앞의 음봉을 본다.
            if FLOOR_BREAK_RESET and k+1 < len(rows) and \
                    min(r['low'] for r in rows[k+1:]) < min(f['low'], b['low']) * (1 - FLOOR_BREAK_TOL):
                break
            spans = [r['high']-r['low'] for r in rows[max(0,j-5):j] if r['high'] > r['low']]
            reference = median(spans) if spans else f['high']-f['low']
            reversal = False
            start = k+1
            if FLOOR_REVERSAL_AFTER_LOW:
                # V7635-2: 거래바닥 이후 가장 최근 저점(같은 저가면 나중 봉) 다음/그 봉부터만 반전 인정.
                lows = [r['low'] for r in rows[k:]]
                last_low = k + max(i for i, v in enumerate(lows) if v == min(lows))
                start = max(k+1, last_low)
            for r in rows[start:]:
                span, body = r['high']-r['low'], abs(r['close']-r['open'])
                wick = min(r['close'],r['open'])-r['low']
                reversal |= (r['close'] > r['open'] or
                    (span > 0 and wick >= body*1.5 and wick >= span*.35) or
                    (span > 0 and body <= span*.35 and span <= reference*.65))
            buy = reversal and _ma5_settled(rows, FLOOR_MA5_SETTLE_BARS)
            return dict(route='BOTTOM_MA5' if buy else 'FLOOR', buy=buy,
                        episode=rows[k]['timestamp'], flush_index=j, floor_index=k,
                        reason='대량하락음봉3배·' + ('전일전체거래2배' if volume5._stamp(f['timestamp']).time() >= FLOOR_PREV_VOLUME_FROM else '10시전(전일2배면제)') + '·거래바닥35%/55%' +
                               ('·바닥이후반전·5선안착' if buy else '·매수직전'))
    return None


def _ma5_settled(rows, bars=FLOOR_MA5_SETTLE_BARS):
    """V7635-3: 마지막 `bars`개 1분봉의 종가가 모두 각자의 1분 MA5(그 봉 포함 5개 종가 평균) 이상."""
    last = len(rows) - 1
    return all(_above_ma(rows, last - n, (5,)) for n in range(max(1, int(bars))))


def _breakout(rows, events):
    if not events or len(rows) < 10:
        return None
    c = rows[-1]
    if c['close'] <= c['open']:
        return None
    for j in range(len(rows)-3, max(4, len(rows)-6), -1):
        if not any(e['index'] <= j for e in events):
            continue
        left = rows[max(0,j-10):j]
        b, pullbacks = rows[j], rows[j+1:-1]
        if len(left)<5 or not 1 <= len(pullbacks) <= 3 or not _contiguous(rows[max(0,j-10):]):
            continue
        peak = max(left, key=lambda r: r['high'])
        span = b['high']-b['low']
        if (span <= 0 or b['close']-b['open'] < span*.4
                or b['close'] < peak['high'] + max(1., peak['high']*.0005)
                or b['volume'] < max(r['volume'] for r in left)*1.05
                or b['volume'] < median(r['volume'] for r in left[-5:])*1.8):
            continue
        if BREAKOUT_DAY_HIGH:
            session = [r['high'] for r in rows[:j] if volume5._stamp(r['timestamp']).time() >= time(9)]
            if session and b['close'] < max(session) + max(1., max(session)*.0005):
                continue   # V7633: 그날 고점을 넘지 못한 '10분 고점 돌파'는 전고점 아래 매수라 제외
        midpoint = (b['open']+b['close'])/2
        if not all(r['low'] >= midpoint*.997 and r['close'] >= midpoint
                   and r['volume'] <= b['volume']*.65 and _above_ma(rows,j+1+i)
                   for i,r in enumerate(pullbacks)):
            continue
        prior_peak_volume = peak['volume']
        if (c['close'] > b['close'] and c['volume'] > prior_peak_volume
                and c['volume'] > mean(r['volume'] for r in pullbacks)):
            return dict(route='BREAKOUT', buy=True, episode=b['timestamp'],
                        prior_peak_volume=prior_peak_volume,
                        confidence_multiple=c['volume']/max(1,prior_peak_volume),
                        reason='돌파·1~3봉거래65%이하·MA5/10지지·전고점거래초과재상승')
    return None


def _ma5_close(bars, i):
    return mean(x['close'] for x in bars[i - 4:i + 1]) if i >= 4 else bars[i]['close']


def _bottom_signal_kinds(bars, s, ref_span):
    """V7631: s번째 봉에서 보이는 바닥 신호 이름들(없으면 빈 목록)."""
    b, kinds = bars[s], []
    if b['low'] < min(b['open'], b['close']):
        kinds.append('아래꼬리')
    if s >= 1:
        a = bars[s - 1]
        small = lambda x: x['close'] > x['open'] and (x['close'] - x['open']) <= ref_span * PULLBACK_SMALL_BODY
        if small(a) and small(b):
            kinds.append('작은양봉연속')
        if b['close'] >= _ma5_close(bars, s) and a['close'] < _ma5_close(bars, s - 1):
            kinds.append('5선돌파')
    return kinds


def _pullback_index(rows, previous_close, with_kind=False):
    """신호 봉 위치를 돌려준다. 없으면 None. (재무장 끄면 첫 신호, 켜면 재무장 규칙을 지킨 가장 최근 신호)"""
    bars = [b for b in rows if time(9) <= volume5._stamp(b['timestamp']).time() < time(15, 30)]
    none = (None, bars, '') if with_kind else (None, bars)
    if not bars or not previous_close or previous_close <= 0 or len(bars) < 5:
        return none
    day_open = bars[0]['open']
    if PULLBACK_OPEN_GRACE_BARS:
        # V7633: 갭상승 개장 직후 몇 분 흔들림으로 시가를 잠깐 깬 것은 '시가 붕괴'로 보지 않는다.
        day_open = min([day_open] + [x['low'] for x in bars[:PULLBACK_OPEN_GRACE_BARS]])
    low_so_far = float('inf')
    wave_i = None
    spent_i = None   # V7633 재무장: 이미 쓴(또는 너무 이른) 신호 봉 위치. 그 뒤 새 거래바닥이 있어야 다시 본다.
    found = None     # V7633 재무장: 가장 최근의 유효 신호 (봉 위치, 신호 이름)
    for i, b in enumerate(bars):
        low_so_far = min(low_so_far, b['low'])
        ref = min(float(previous_close), low_so_far)
        if wave_i is None:
            if b['high'] >= ref * (1 + PULLBACK_RISE):
                wave_i = i
            continue
        lead_vol = max(x['volume'] for x in bars[:wave_i + 1][-15:])
        peak_i = max(range(wave_i, i + 1), key=lambda k: bars[k]['high'])
        peak = bars[peak_i]['high']
        if peak_i >= i - 1:
            continue
        lo = min(x['low'] for x in bars[peak_i + 1:i + 1])
        pull = 1 - lo / peak
        if pull > PULLBACK_MAX or lo < day_open:
            return none
        if pull < PULLBACK_MIN:
            continue
        dry = None
        for d in range(peak_i + 1, i + 1 if PULLBACK_ANY_SIGNAL else i):
            prior = [x['volume'] for x in bars[max(0, d - 5):d]]
            if bars[d]['volume'] <= PULLBACK_DRY * lead_vol and prior and bars[d]['volume'] <= min(prior):
                dry = d
        if dry is None:
            continue
        if PULLBACK_REARM and spent_i is not None and dry <= spent_i:
            continue   # V7633: 앞 신호 뒤 새 거래바닥이 아직 없다 → 같은 신호를 두 번 쓰지 않는다
        ma5 = _ma5_close(bars, i)
        if volume5._stamp(b['timestamp']).time() >= PULLBACK_UNTIL or b['close'] < ma5:
            continue
        if PULLBACK_ABOVE_PREV_CLOSE and b['close'] < float(previous_close):
            continue   # V7627: 전일 종가 아래(갭하락 뒤 되돌림)는 '상승 눌림'이 아니다
        if PULLBACK_ANY_SIGNAL:
            spans = [x['high'] - x['low'] for x in bars[max(0, peak_i - 10):peak_i + 1] if x['high'] > x['low']]
            ref_span = median(spans) if spans else 1.0
            hit = None
            for s in range(max(dry, peak_i + 1), i + 1):
                if s - dry > PULLBACK_SIGNAL_WITHIN:
                    break
                kinds = _bottom_signal_kinds(bars, s, ref_span)
                if kinds:
                    hit = (s, kinds)
            if hit is None or i - hit[0] > PULLBACK_HOLD_WITHIN:
                continue
            kind = '+'.join(hit[1])
        else:
            if i - dry > PULLBACK_WITHIN or b['close'] <= b['open']:
                continue
            kind = '양봉'
        if PULLBACK_REARM:
            # V7633: 신호를 '쓴 것'으로 두고 그 뒤 새 거래바닥에서 나온 다음 신호만 다시 본다.
            # 09:20 전 신호는 사지 않고(쓴 것으로만 표시) 다음 신호를 기다린다. 결과는 가장 최근 유효 신호.
            if volume5._stamp(b['timestamp']).time() >= PULLBACK_FROM:
                found = (i, kind)
            spent_i = i
            continue
        if volume5._stamp(b['timestamp']).time() < PULLBACK_FROM:
            return (None, bars, '') if with_kind else (None, bars)   # V7630: 첫 신호가 너무 이르면 그날은 포기
        return (i, bars, kind) if with_kind else (i, bars)
    if found is not None:
        return (found[0], bars, found[1]) if with_kind else (found[0], bars)
    return (None, bars, '') if with_kind else (None, bars)


def _pullback(rows, previous_close):
    if not PULLBACK_ENABLED:
        return None
    index, bars, kind = _pullback_index(rows, previous_close, with_kind=True)
    if index is None or index != len(bars) - 1 or bars[index]['timestamp'] != rows[-1]['timestamp']:
        return None
    tail = f'바닥신호({kind})·5분선위지지' if PULLBACK_ANY_SIGNAL else '5분선위양봉'
    return dict(route='PULLBACK', buy=True, episode=bars[index]['timestamp'],
                reason='상승눌림·1차+5%·고점대비3~8%·시가유지·거래바닥15%·' + tail)


def needs_daily(raw, now):
    rows = completed(raw, now)
    return any(_contiguous(rows[j-5:j+1]) and
               rows[j]['close'] < min(rows[j]['open'],rows[j-1]['close']) and
               rows[j]['volume'] >= 3*mean(b['volume'] for b in rows[j-5:j])
               for j in range(max(5,len(rows)-30),len(rows)))


def sharp_market_entry_allowed(proof):
    """Retain the original sharp-decline short-bullish confirmation for bottom entries."""
    if proof['route'] != 'BOTTOM_MA5':
        return True
    rows, j, k = proof['bars'], proof['flush_index'], proof['floor_index']
    spans = [b['high']-b['low'] for b in rows[max(0,j-5):j] if b['high'] > b['low']]
    reference = median(spans) if spans else rows[j]['high']-rows[j]['low']
    # The new >=3x flush already exceeds the original exceptional >=2x flush gate.
    return any(0 < b['close']-b['open'] <= (b['high']-b['low'])*.35
               and 0 < b['high']-b['low'] <= reference*.65 for b in rows[k+1:])


def evaluate(raw, *, now, previous_day=None, previous_volume=None, previous_close=None):
    if not in_session(now):
        return None
    rows = completed(raw, now)
    if not rows:
        return None
    events = waves(rows)
    result = _breakout(rows, events)
    if result is None:
        if previous_day is not None and str(previous_day) < volume5._stamp(now).date().isoformat():
            result = _floor(rows, events, now, previous_volume)
    if result is None or not result.get('buy'):
        # V7612: 기존 경로가 매수 신호를 내지 않을 때만 형 지정 패턴 A/B 를 본다.
        extra = pattern12.evaluate(raw, rows, now=now)
        if extra is not None:
            result = extra
    if result is None or not result.get('buy'):
        # V7622: 다른 경로가 매수 신호를 내지 않을 때만 상승 눌림목 경로를 본다.
        if previous_day is not None and str(previous_day) < volume5._stamp(now).date().isoformat():
            extra = _pullback(rows, previous_close)
            if extra is not None:
                result = extra
    if result is None and events and events[-1]['index'] == len(rows)-1:
        e = events[-1]
        result = dict(route='WAVE', buy=False, episode=rows[e['index']]['timestamp'],
                      reason=f"{len(events)}차상승·15봉이내+{e['threshold']}%·매수직전")
    if result is None:
        return None
    result.update(rule_id=RULE_ID, source=volume5.SOURCE, bars=rows,
                  data_time=rows[-1]['timestamp'], previous_day=str(previous_day) if previous_day else None,
                  previous_volume=previous_volume, previous_close=previous_close)
    return result


def validate_evidence(proof, data_time, decision_time):
    try:
        # V7612: 패턴 A 는 전날 봉(prior_bars)까지 넣어야 같은 판정이 재현된다.
        raw = list(proof.get('prior_bars') or []) + list(proof['bars'])
        result = evaluate(raw, now=volume5._stamp(decision_time),
                          previous_day=proof.get('previous_day'), previous_volume=proof.get('previous_volume'),
                          previous_close=proof.get('previous_close'))
        return bool(result and data_time == result['data_time'] and
                    all(proof.get(k) == v for k,v in result.items()))
    except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
        return False
