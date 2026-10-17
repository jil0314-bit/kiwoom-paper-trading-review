"""V7610 눌림목 그림자 신호 성적표 — 조회 전용(ka10080). 주문 API·장부 쓰기 없음.

엔진이 runtime_v7592/pullback_shadow/*.jsonl 에 남긴 신호를 실제 1분봉으로 채점한다.

체결 원칙 (2026-08-27 체결불가 가격 오류 재발 방지)
  · 진입가 = 신호봉 다음 1분봉 시가 × 1.001 (매도호가 근사). 신호봉 종가로 사지 않는다.
  · 같은 봉에서 손절과 목표가 모두 닿으면 손절로 본다.
  · 손절가 아래로 갭이 나면 그 봉 시가로 청산한다.
  · 최대 60분, 당일 안에서만 본다. 왕복비용 0.21% (paper_fills.csv 389건 실측 중앙값).

판정: 허용 시간대 신호가 30건 이상이고 95% 신뢰구간 하한이 0보다 클 때만
      "PAPER 매수 전환 검토 가능". 그 전에는 그림자 유지.

실행:  C:\\Python311_32\\python.exe tools\\v7610_pullback_shadow_report.py
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
import urllib.request

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parents[1]
SHADOW_DIR = ROOT / "SOURCE" / "runtime_v7592" / "pullback_shadow"
CREDENTIAL_POINTER = Path.home() / ".v75_last_credentials_path.txt"
COST_PCT = 0.21
ENTRY_SLIPPAGE = 1.001
MAX_BARS = 60
MIN_SIGNALS = 30
PAGES = 6


def _env_file(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def credentials() -> tuple[str, str, str]:
    """Read only; nothing is written or printed."""
    values = dict(os.environ)
    if not (values.get("KIWOOM_APP_KEY") and values.get("KIWOOM_SECRET_KEY")):
        try:
            values.update(_env_file(Path(CREDENTIAL_POINTER.read_text(encoding="utf-8").strip())))
        except (OSError, UnicodeError):
            pass
    app = (values.get("KIWOOM_APP_KEY") or values.get("APP_KEY") or "").strip()
    secret = (values.get("KIWOOM_SECRET_KEY") or values.get("KIWOOM_APP_SECRET")
              or values.get("APP_SECRET") or "").strip()
    base = (values.get("KIWOOM_BASE_URL") or values.get("BASE_URL") or "https://api.kiwoom.com").strip()
    return app, secret, base.rstrip("/")


def _post_json(url: str, body: dict, headers: dict) -> tuple[dict, dict]:
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json;charset=UTF-8", **headers})
    with urllib.request.urlopen(request, timeout=15) as reply:
        return json.loads(reply.read().decode("utf-8")), {k.lower(): v for k, v in reply.headers.items()}


class MinuteBars:
    def __init__(self, app: str, secret: str, base: str) -> None:
        self.base = base
        data, _headers = _post_json(base + "/oauth2/token", {
            "grant_type": "client_credentials", "appkey": app, "secretkey": secret}, {})
        self.token = str(data.get("token") or "").strip()
        if not self.token:
            raise RuntimeError(f"토큰 발급 실패: {data.get('return_msg')}")
        self.last = 0.0

    def _post(self, body: dict, key: str) -> tuple[dict, str, str]:
        wait = 1.05 - (time.monotonic() - self.last)
        if wait > 0:
            time.sleep(wait)
        self.last = time.monotonic()
        headers = {"authorization": f"Bearer {self.token}", "api-id": "ka10080"}
        if key:
            headers.update({"cont-yn": "Y", "next-key": key})
        data, reply_headers = _post_json(self.base + "/api/dostk/chart", body, headers)
        return data, reply_headers.get("cont-yn", "N"), reply_headers.get("next-key", "")

    def fetch(self, symbol: str, oldest_day: str) -> dict[str, list[dict]]:
        rows, key = {}, ""
        for _ in range(PAGES):
            data, more, key = self._post(
                {"stk_cd": f"{symbol}_AL", "tic_scope": "1", "upd_stkpc_tp": "1"}, key)
            chunk = data.get("stk_min_pole_chart_qry") or []
            for item in chunk:
                stamp = str(item.get("cntr_tm", "")).strip()
                if len(stamp) < 12:
                    continue
                values = [abs(float(str(item.get(k, "0")).replace(",", "") or 0))
                          for k in ("open_pric", "high_pric", "low_pric", "cur_prc", "trde_qty")]
                if min(values[:4]) <= 0:
                    continue
                moment = datetime.strptime(stamp[:12], "%Y%m%d%H%M").replace(tzinfo=KST)
                rows[moment] = dict(timestamp=moment, open=values[0], high=values[1],
                                    low=values[2], close=values[3], volume=values[4])
            if not chunk or more != "Y" or not key or min(rows, default=datetime.max.replace(
                    tzinfo=KST)).date().isoformat() < oldest_day:
                break
        days = defaultdict(list)
        for moment in sorted(rows):
            days[moment.date().isoformat()].append(rows[moment])
        return days


def score(signal: dict, day_bars: list[dict], target: str) -> float | None:
    trigger = datetime.fromisoformat(signal["data_time"]).astimezone(KST)
    after = [b for b in day_bars if b["timestamp"] > trigger][:MAX_BARS]
    if not after or after[0]["timestamp"] != trigger + timedelta(minutes=1):
        return None
    entry = after[0]["open"] * ENTRY_SLIPPAGE
    stop = float(signal["stop"])
    if entry <= stop:
        return None
    goal = entry * (1 + (2.0 + COST_PCT) / 100) if target == "NET2" else float(signal["peak_high"])
    if goal <= entry:
        return None
    for bar in after:
        if bar["low"] <= stop:
            return (min(bar["open"], stop) / entry - 1) * 100 - COST_PCT
        if bar["high"] >= goal:
            return (goal / entry - 1) * 100 - COST_PCT
    return (after[-1]["close"] / entry - 1) * 100 - COST_PCT


def summary(label: str, values: list[float]) -> tuple[int, float, float]:
    if len(values) < 2:
        print(f"  {label:<28} {len(values):>4}건  표본부족")
        return len(values), 0.0, -math.inf
    mean = statistics.mean(values)
    se = statistics.stdev(values) / math.sqrt(len(values))
    wins = sum(1 for v in values if v > 0)
    low, high = mean - 1.96 * se, mean + 1.96 * se
    print(f"  {label:<28} {len(values):>4}건  승률 {wins / len(values) * 100:5.1f}%  "
          f"거래당 {mean:+.3f}%  95%구간 [{low:+.2f}, {high:+.2f}]")
    return len(values), mean, low


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    signals, seen = [], set()
    for path in sorted(SHADOW_DIR.glob("pullback_shadow_*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            key = (item.get("symbol"), item.get("peak_time"))
            if key not in seen:
                seen.add(key)
                signals.append(item)
    print(f"그림자 신호 {len(signals)}건  ({SHADOW_DIR})")
    if not signals:
        print("아직 기록된 신호가 없습니다. 장중 실행 후 다시 확인하세요.")
        return 0
    app, secret, base = credentials()
    if not (app and secret):
        print("키움 REST 키를 찾지 못했습니다. 대시보드를 한 번 실행한 뒤 다시 시도하세요.")
        return 1
    client = MinuteBars(app, secret, base)
    by_symbol = defaultdict(list)
    for item in signals:
        by_symbol[item["symbol"]].append(item)
    results = defaultdict(list)
    for index, (symbol, items) in enumerate(sorted(by_symbol.items()), 1):
        oldest = min(datetime.fromisoformat(i["data_time"]).date().isoformat() for i in items)
        try:
            days = client.fetch(symbol, oldest)
        except Exception as exc:  # one symbol must not stop the report
            print(f"  {symbol} 분봉 조회 실패: {type(exc).__name__}")
            continue
        for item in items:
            day = datetime.fromisoformat(item["data_time"]).date().isoformat()
            window = "blocked" if item.get("blocked_window") else "allowed"
            for target in ("NET2", "PEAK"):
                value = score(item, days.get(day, []), target)
                if value is not None:
                    results[(target, window)].append(value)
                    results[(target, "all")].append(value)
        print(f"  {index}/{len(by_symbol)} {symbol} 채점", flush=True)
    print()
    verdict = []
    for target, title in (("NET2", "목표 +2%(비용후) · 손절 눌림저점"), ("PEAK", "목표 파동고점 · 손절 눌림저점")):
        print(f"[{title}]")
        summary("전체", results[(target, "all")])
        count, _mean, low = summary("허용 시간대만", results[(target, "allowed")])
        summary("차단 시간대(참고)", results[(target, "blocked")])
        verdict.append(count >= MIN_SIGNALS and low > 0)
        print()
    if any(verdict):
        print("판정: 허용 시간대 신뢰구간 하한 > 0 → PAPER 매수 전환 검토 가능")
    else:
        print(f"판정: 그림자 유지 (허용 시간대 {MIN_SIGNALS}건 이상 + 신뢰구간 하한 > 0 필요)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
