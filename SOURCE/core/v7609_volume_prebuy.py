"""VOL5-20260909-001: pure completed-bar admission; never places orders."""
from datetime import date, datetime, time, timedelta, timezone
from math import isfinite

RULE_ID = "V7609_VOLUME5_PREBUY"
EARLY_RULE_ID = "V7609_VOLUME5_EARLY_PAPER"
EARLY_REASON = "5배예상·양봉 조기매수(PAPER)"
SOURCE = "KIWOOM_REST_KA10080_RAW_1M"
KST = timezone(timedelta(hours=9))


def _stamp(value):
    result = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(result, datetime) or result.tzinfo is None:
        raise ValueError("timezone missing")
    return result.astimezone(KST)


def _bar(value):
    get = value.get if isinstance(value, dict) else lambda k: getattr(value, k)
    stamp = _stamp(get("timestamp"))
    if stamp.second or stamp.microsecond:
        raise ValueError("not a one-minute bar")
    row = {k: float(get(k)) for k in ("open", "high", "low", "close", "volume")}
    if not all(isfinite(v) for v in row.values()):
        raise ValueError("nonfinite OHLCV")
    if (min(row[k] for k in ("open", "high", "low", "close")) <= 0
            or row["volume"] < 0 or not row["volume"].is_integer()
            or not row["low"] <= min(row["open"], row["close"]) <= max(row["open"], row["close"]) <= row["high"]):
        raise ValueError("invalid OHLCV")
    row["volume"] = int(row["volume"])
    row["timestamp"] = stamp.isoformat()
    return row


def volume_inputs(bars, now, previous_day=None):
    """Return only a fivefold candidate. Missing minutes are not fabricated."""
    cutoff = _stamp(now).replace(second=0, microsecond=0)
    rows = [_bar(b) for b in bars if _stamp(b.get("timestamp") if isinstance(b, dict) else b.timestamp) < cutoff]
    rows.sort(key=lambda r: r["timestamp"])
    if not rows or len({r["timestamp"] for r in rows}) != len(rows):
        return None
    current = rows[-1]
    stamp = _stamp(current["timestamp"])
    if stamp != cutoff - timedelta(minutes=1):
        return None
    clock = stamp.time()
    if not (time(8) <= clock < time(20)) or not (time(8) <= _stamp(now).time() < time(20)):
        return None
    opening = time(8) <= clock < time(8, 15) or time(9) <= clock < time(9, 15)
    if opening:
        days = [_stamp(r["timestamp"]).date() for r in rows[:-1] if _stamp(r["timestamp"]).date() < stamp.date()]
        prior = previous_day or (max(days) if days else None)
        refs = [r for r in rows[:-1] if _stamp(r["timestamp"]).date() == prior][-40:]
    else:
        refs = rows[:-1][-40:]
    if len(refs) != 40:
        return None
    total = sum(r["volume"] for r in refs)
    if total <= 0 or current["volume"] * 40 < total * 5:
        return None
    return {"source": SOURCE, "trigger_bar": current, "reference_bars": refs,
            "reference_kind": "PREVIOUS_SESSION_LAST40" if opening else "PREVIOUS40",
            "reference_average_volume": total / 40,
            "volume_multiple": current["volume"] * 40 / total}


def evaluate(bars, *, now, previous_day, previous_close):
    prior = date.fromisoformat(previous_day) if isinstance(previous_day, str) else previous_day
    close = float(previous_close)
    if not isinstance(prior, date) or prior >= _stamp(now).date() or not isfinite(close) or close <= 0:
        return None
    evidence = volume_inputs(bars, now, prior)
    if evidence is None or evidence["trigger_bar"]["close"] <= close:
        return None
    evidence.update(previous_day=prior.isoformat(), previous_close=close,
                    previous_close_source="KIWOOM_REST_KA10081_COMPLETED_DAILY",
                    data_time=evidence["trigger_bar"]["timestamp"],
                    rule_id=RULE_ID, reason="5배급등거래")
    return evidence


def validate_evidence(evidence, data_time, decision_time):
    """Recompute this one exception on transitions and restart restoration."""
    try:
        if evidence.get("rule_id") == EARLY_RULE_ID:
            computed = evaluate_early(
                [*evidence["reference_bars"], evidence["trigger_bar"]],
                now=_stamp(decision_time), previous_day=evidence["previous_day"],
                previous_close=evidence["previous_close"],
            )
            return bool(computed and computed["data_time"] == data_time
                        and all(evidence.get(k) == v for k, v in computed.items()))
        computed = evaluate([*evidence["reference_bars"], evidence["trigger_bar"]],
                            now=_stamp(decision_time), previous_day=evidence["previous_day"],
                            previous_close=evidence["previous_close"])
        return bool(computed and computed["data_time"] == data_time
                    and all(evidence.get(k) == v for k, v in computed.items()))
    except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
        return False


def early_inputs(bars, now, previous_day=None):
    """A measured partial candle, never extrapolated prices or completed OHLCV."""
    instant = _stamp(now)
    start = instant.replace(second=0, microsecond=0)
    elapsed = (instant - start).total_seconds()
    if not 25 <= elapsed < 60:
        return None
    rows = sorted((_bar(b) for b in bars), key=lambda r: r["timestamp"])
    if not rows or len({r["timestamp"] for r in rows}) != len(rows):
        return None
    current = rows[-1]
    if _stamp(current["timestamp"]) != start or current["close"] <= current["open"]:
        return None
    clock = start.time()
    if not time(8) <= clock < time(20):
        return None
    opening = time(8) <= clock < time(8, 15) or time(9) <= clock < time(9, 15)
    if opening:
        days = [_stamp(r["timestamp"]).date() for r in rows[:-1]
                if _stamp(r["timestamp"]).date() < start.date()]
        prior = previous_day or (max(days) if days else None)
        refs = [r for r in rows[:-1] if _stamp(r["timestamp"]).date() == prior][-40:]
    else:
        refs = rows[:-1][-40:]
    if len(refs) != 40:
        return None
    average = sum(r["volume"] for r in refs) / 40
    projected = current["volume"] * 60 / elapsed
    if average <= 0 or projected < average * 5:
        return None
    return {"source": SOURCE, "completed": False, "trigger_bar": current,
            "reference_bars": refs, "reference_kind": "PREVIOUS_SESSION_LAST40" if opening else "PREVIOUS40",
            "reference_average_volume": average, "elapsed_seconds": elapsed,
            "actual_volume_multiple": current["volume"] / average,
            "projected_volume": projected, "volume_multiple": projected / average}


def evaluate_early(bars, *, now, previous_day, previous_close):
    prior = date.fromisoformat(previous_day) if isinstance(previous_day, str) else previous_day
    close = float(previous_close)
    if not isinstance(prior, date) or prior >= _stamp(now).date() or not isfinite(close) or close <= 0:
        return None
    result = early_inputs(bars, now, prior)
    if result is None or result["trigger_bar"]["close"] <= close:
        return None
    result.update(previous_day=prior.isoformat(), previous_close=close,
                  previous_close_source="KIWOOM_REST_KA10081_COMPLETED_DAILY",
                  data_time=result["trigger_bar"]["timestamp"],
                  rule_id=EARLY_RULE_ID, reason=EARLY_REASON)
    return result
