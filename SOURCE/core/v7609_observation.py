"""Display-only early observation. Never supplies entry/order evidence."""
from datetime import timedelta
import math
from v7609_exit_policy import completed_bars


def early_candidate(raw, now):
    bars = [b for b in completed_bars(raw, now)
            if b.timestamp.date() == now.date() and b.timestamp.hour >= 8]
    if not bars or bars[-1].timestamp != now.replace(second=0, microsecond=0)-timedelta(minutes=1):
        return None
    low, anchor, gap_bar = float('inf'), 0, -1
    for i, b in enumerate(bars):
        volume = float(getattr(b, 'volume', 0))
        if not math.isfinite(volume) or volume < 0 or b.timestamp.second or b.timestamp.microsecond:
            return None
        if i and b.timestamp-bars[i-1].timestamp != timedelta(minutes=1):
            low, anchor, gap_bar = float(b.low), i, i
        if b.low < low:
            low, anchor = float(b.low), i
    count = len(bars)-anchor
    last = bars[-1]
    if (gap_bar == len(bars)-1 or count > 15 or last.volume <= 0
            or not low*104 <= float(last.close)*100 < low*105):
        return None
    return {'data_time': last.timestamp, 'expires_at': min(
        bars[anchor].timestamp+timedelta(minutes=16), last.timestamp+timedelta(minutes=2)),
        'current_price': float(last.close), 'rise': float(last.close)/low-1,
        'reason': f'예비관찰(매수자격 아님) · 저점대비 {float(last.close)/low-1:+.2%} · {count}/15봉 · 5% 확인 대기'}
