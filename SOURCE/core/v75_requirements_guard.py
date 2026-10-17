from __future__ import annotations

import json
from pathlib import Path


LOCK_PATH = Path(__file__).with_name("v75_master_requirements_lock.json")
REQUIRED = {
    "CORE-PAPER", "CORE-LINK", "CORE-TP", "CORE-SL", "CORE-NO-DOUBLE",
    "PATTERN-REVERSAL-FLOOR", "PATTERN-RISE-PULLBACK", "PATTERN-BREAKOUT",
    "PATTERN-INDEPENDENT", "TIMEFRAME-1M-3M", "STAGE-MERGE",
    "STAGE-DEPARTURE", "STAGE-DETAIL", "GUI-LATEST-FIRST", "GUI-SCROLL",
    "COMMAND-FREE-TEXT", "COMMAND-STOCK", "COMMAND-RESULT", "MARKET-ANALYST",
    "MARKET-CADENCE", "DATA-NO-FABRICATION", "REFRESH-FAST",
    "OBS-MULTI-ADMISSION", "RELEASE-GATE",
}


def validate_requirements_lock() -> tuple[bool, list[str]]:
    problems: list[str] = []
    try:
        data = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, [f"요구사항 잠금 파일을 읽을 수 없음: {exc}"]

    rows = data.get("requirements", [])
    ids = [str(row.get("id", "")) for row in rows]
    if len(ids) != len(set(ids)):
        problems.append("요구사항 ID가 중복됨")
    active = {str(row.get("id")) for row in rows if row.get("status") == "active"}
    missing = sorted(REQUIRED - active)
    if missing:
        problems.append("필수 요구사항 누락/비활성: " + ", ".join(missing))
    if data.get("policy", {}).get("mode") != "append_or_supersede_only":
        problems.append("요구사항 이력 정책이 훼손됨")
    return not problems, problems


def require_valid_lock() -> None:
    ok, problems = validate_requirements_lock()
    if not ok:
        raise RuntimeError("요구사항 잠금 검증 실패: " + " / ".join(problems))
