"""2026-09-15 requested PREBUY signals; no broker, GUI or network access."""
from datetime import datetime, time, timedelta
from math import isfinite
from statistics import mean, median
from types import SimpleNamespace

import v7609_volume_prebuy as volume5
import v7600_three_stage_logic as structure

RULE_ID = "V7609_PREBUY_20260915"


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
        if (not rising and (result or before_nine) and i-anchor+1 <= 15
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
                or totals[j] < 2 * previous_volume):
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
            spans = [r['high']-r['low'] for r in rows[max(0,j-5):j] if r['high'] > r['low']]
            reference = median(spans) if spans else f['high']-f['low']
            reversal = False
            for r in rows[k+1:]:
                span, body = r['high']-r['low'], abs(r['close']-r['open'])
                wick = min(r['close'],r['open'])-r['low']
                reversal |= (r['close'] > r['open'] or
                    (span > 0 and wick >= body*1.5 and wick >= span*.35) or
                    (span > 0 and body <= span*.35 and span <= reference*.65))
            buy = reversal and _above_ma(rows, len(rows)-1, (5,))
            return dict(route='BOTTOM_MA5' if buy else 'FLOOR', buy=buy,
                        episode=rows[k]['timestamp'], flush_index=j, floor_index=k,
                        reason='대량하락음봉3배·전일전체거래2배·거래바닥35%/55%' +
                               ('·바닥이후반전·MA5회복' if buy else '·매수직전'))
    return None


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


def evaluate(raw, *, now, previous_day=None, previous_volume=None):
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
    if result is None and events and events[-1]['index'] == len(rows)-1:
        e = events[-1]
        result = dict(route='WAVE', buy=False, episode=rows[e['index']]['timestamp'],
                      reason=f"{len(events)}차상승·15봉이내+{e['threshold']}%·매수직전")
    if result is None:
        return None
    result.update(rule_id=RULE_ID, source=volume5.SOURCE, bars=rows,
                  data_time=rows[-1]['timestamp'], previous_day=str(previous_day) if previous_day else None,
                  previous_volume=previous_volume)
    return result


def validate_evidence(proof, data_time, decision_time):
    try:
        result = evaluate(proof['bars'], now=volume5._stamp(decision_time),
                          previous_day=proof.get('previous_day'), previous_volume=proof.get('previous_volume'))
        return bool(result and data_time == result['data_time'] and
                    all(proof.get(k) == v for k,v in result.items()))
    except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
        return False
