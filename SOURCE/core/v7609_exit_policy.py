"""Cost-inclusive PAPER exits; no transport, orders or state-file writes."""
from datetime import timedelta
from decimal import Decimal
from itertools import combinations
import math

STOP = .015
TARGET = .02
MAX_LOSS = .03
LOOKBACK = 40
MA_PERIODS = (5, 10, 20)
MA_CLUSTER_PCT = .005
# V7610 (2026-09-23): 손절가 도달 후 지지선 위라는 이유로 손절을 미루지 않는다.
# 09-15 이후 원장 208건: 유예로 구제된 목표익절 6건 +21.4%p,
# 유예 후 청산 121건이 즉시손절 대비 -57.3%p → 순효과 -35.9%p.
# MAX_LOSS 는 갭하락 대비 안전망으로 남긴다.
SUPPORT_DEFER = False


def cost_per_share(position, commission=0.0):
    entry = float(position.entry_price)
    qty = int(getattr(position, 'quantity', 0) or 0)
    cost = float(getattr(position, 'entry_cost', 0) or 0)
    if qty > 0 and cost > 0:
        return cost / qty
    fee = getattr(position, 'entry_fee', None)
    return entry + (float(fee) / qty if fee is not None and qty > 0 else entry * commission)


def net_return(position, price, commission, tax):
    return float(price) * (1 - commission - tax) / cost_per_share(position, commission) - 1


def exit_prices(entry_price, align, tick_size, *, entry_cost=None, commission=0., tax=0.):
    entry = Decimal(str(entry_price))
    cost = Decimal(str(entry_cost)) if entry_cost is not None else entry * (1 + Decimal(str(commission)))
    proceeds = 1 - Decimal(str(commission)) - Decimal(str(tax))
    if not entry.is_finite() or entry <= 0 or not cost.is_finite() or cost <= 0 or proceeds <= 0:
        raise ValueError('PAPER exit requires valid entry and costs')
    raw_stop = cost * Decimal('0.985') / proceeds
    candidates = [Decimal(str(align(float(raw_stop), upward=up))) for up in (False, True)]
    stop = min(candidates, key=lambda p: (abs(p - raw_stop), -p))
    raw_target = cost * Decimal('1.02') / proceeds
    target = Decimal(str(align(float(raw_target), upward=True)))
    if target < raw_target:
        target += Decimal(str(tick_size(float(target))))
    return float(stop), float(target)


def completed_bars(raw, now):
    """Reject duplicate/invalid bars, and never use a forming/future candle."""
    result, seen = [], set()
    if any(getattr(b, 'timestamp', None) is None or b.timestamp.tzinfo is None for b in raw):
        return []
    for bar in sorted(raw, key=lambda b: b.timestamp):
        stamp = bar.timestamp
        if stamp.tzinfo is None or stamp + timedelta(minutes=1) > now:
            continue
        try:
            values = [float(getattr(bar, key, 0) or 0) for key in ('open', 'high', 'low', 'close')]
        except (TypeError, ValueError):
            return []
        if stamp in seen or any(not math.isfinite(v) or v <= 0 for v in values):
            return []
        if values[2] > min(values[0], values[3]) or values[1] < max(values[0], values[3]):
            return []
        seen.add(stamp)
        result.append(bar)
    return result


def support_snapshot(position, raw, now, previous=None, *, entry_time_override=None):
    """Freeze the pre-entry 40-bar low; arm MA support only while price is above it."""
    entry_time = entry_time_override or getattr(position, 'entry_time', None)
    key = str(entry_time)
    # Scale-in mutates entry_time on the SAME holding object. A new position
    # object is a new lifecycle, even for a same-second re-entry.
    same = (previous or {}).get('position_ref') is position
    state = dict(previous or {}) if same else {'entry_time': key, 'position_ref': position}
    if same:
        from datetime import datetime
        entry_time = datetime.fromisoformat(state['entry_time']) if state['entry_time'] != 'None' else None
    bars = completed_bars(raw, now)
    if not bars or entry_time is None:
        return state, False
    # Stale cache cannot justify deferring a stop.
    fresh = 0 <= (now - bars[-1].timestamp).total_seconds() <= 120
    left = [b for b in bars if b.timestamp + timedelta(minutes=1) <= entry_time][-LOOKBACK:]
    if 'low' not in state and len(left) == LOOKBACK:
        state['low'] = min(float(b.low) for b in left)
    if len(bars) >= max(MA_PERIODS) and fresh:
        closes = [float(b.close) for b in bars]
        mas = [sum(closes[-n:]) / n for n in MA_PERIODS]
        clusters = [pair for pair in combinations(mas, 2) if max(pair) / min(pair) - 1 <= MA_CLUSTER_PCT]
        # MA zone cannot be newly armed by a price already below that zone.
        eligible = [min(pair) for pair in clusters if closes[-1] >= max(pair)]
        if eligible:
            state['ma'] = max(float(state.get('ma', 0)), max(eligible))
    return state, fresh


def stop_reason(position, price, stop_price, commission, tax, support, fresh):
    rate = net_return(position, price, commission, tax)
    if rate <= -MAX_LOSS + 1e-12:
        return '비용포함 최대손절-3.0%'
    if float(price) > float(stop_price):
        return ''
    low, ma = float(support.get('low', 0)), float(support.get('ma', 0))
    if low and price < low:
        return '저점지지 이탈 손절'
    if ma and price < ma:
        return '이평밀집 지지 이탈 손절'
    if SUPPORT_DEFER and fresh and (low > 0 or ma > 0):
        return ''
    return '비용포함 기본손절-1.5%'
