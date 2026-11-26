from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any


KST = timezone(timedelta(hours=9))
API_BASE = "https://api.kiwoom.com"
TOKEN_ENDPOINT = "/oauth2/token"
CHART_ENDPOINT = "/api/dostk/chart"
CHART_API_ID = "ka10080"
BAR_LIST_KEY = "stk_min_pole_chart_qry"
DAILY_API_ID = "ka10081"
DAILY_LIST_KEY = "stk_dt_pole_chart_qry"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "tests" / "replay_data"
DEFAULT_CREDENTIAL_POINTER = Path.home() / ".v75_last_credentials_path.txt"
OFFICIAL_SPEC = Path.home() / "Downloads" / "kiwoom-rest-api-spec.json"


CONFIRMED_CASES: dict[str, dict[str, Any]] = {
    "STR-01": {
        "symbol": "035720",
        "name": "카카오",
        "trade_date": "20260807",
        "expected": {
            "at": "2026-08-07T10:03:00+09:00",
            "interest_admission": "forbidden",
            "stage": "시장보류",
            "reason": "장대음봉이 전저점과 중간 지지를 동시 이탈하여 회복 전 관심 편입 금지",
        },
        "expectation_sources": [
            "TEST-001.md:30",
            "SOURCE/postclose_review_20260807.json:347-354",
        ],
    }
}


class ReplayDataError(RuntimeError):
    pass


class ReadOnlyPacer:
    """Space read-only REST calls without importing the production engine."""

    def __init__(self, interval: float = 1.05) -> None:
        self.interval = max(0.0, float(interval))
        self.last_call = 0.0

    def wait(self) -> None:
        remaining = self.interval - (time.monotonic() - self.last_call)
        if remaining > 0:
            time.sleep(remaining)
        self.last_call = time.monotonic()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def credential_path(pointer: Path) -> Path:
    if not pointer.is_file():
        raise ReplayDataError(f"인증파일 포인터 없음: {pointer}")
    target = Path(pointer.read_text(encoding="utf-8-sig").strip())
    if not target.is_file():
        raise ReplayDataError(f"인증파일 없음: {target}")
    return target


def load_credentials(pointer: Path) -> tuple[str, str]:
    values = read_env_file(credential_path(pointer))
    app_key = values.get("KIWOOM_APP_KEY", "").strip()
    secret_key = values.get("KIWOOM_SECRET_KEY", "").strip()
    if not app_key or not secret_key:
        raise ReplayDataError("KIWOOM_APP_KEY 또는 KIWOOM_SECRET_KEY 누락")
    return app_key, secret_key


def post_json(
    url: str,
    body: dict[str, str],
    headers: dict[str, str],
    *,
    timeout: float,
) -> tuple[dict[str, Any], dict[str, str], int, bytes]:
    payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json;charset=UTF-8", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(response.status)
            raw = response.read()
            response_headers = {key.lower(): value for key, value in response.headers.items()}
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        raw = exc.read()
        response_headers = {key.lower(): value for key, value in exc.headers.items()}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ReplayDataError(f"REST 전송 실패: {type(exc).__name__}") from exc
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReplayDataError(f"REST 응답 JSON 오류: HTTP {status}") from exc
    if not isinstance(parsed, dict):
        raise ReplayDataError(f"REST 응답 최상위 형식 오류: {type(parsed).__name__}")
    return parsed, response_headers, status, raw


def issue_memory_only_token(app_key: str, secret_key: str, *, timeout: float) -> str:
    response, _headers, status, _raw = post_json(
        API_BASE + TOKEN_ENDPOINT,
        {
            "grant_type": "client_credentials",
            "appkey": app_key,
            "secretkey": secret_key,
        },
        {},
        timeout=timeout,
    )
    token = str(response.get("token", "")).strip()
    code = response.get("return_code", 0)
    if status != 200 or str(code) not in {"0", "None"} or not token:
        message = str(response.get("return_msg", ""))[:160]
        raise ReplayDataError(f"토큰 발급 실패: HTTP {status}, code={code}, msg={message!r}")
    return token


def fetch_chart_pages(
    token: str,
    *,
    symbol: str,
    trade_date: str,
    timeout: float,
    max_pages: int,
    pacer: ReadOnlyPacer | None = None,
) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    cont_yn = "N"
    next_key = ""
    for page_number in range(1, max_pages + 1):
        body = {
            "stk_cd": symbol,
            "tic_scope": "1",
            "upd_stkpc_tp": "1",
            "base_dt": trade_date,
        }
        if pacer is not None:
            pacer.wait()
        response, response_headers, status, raw_body = post_json(
            API_BASE + CHART_ENDPOINT,
            body,
            {
                "authorization": f"Bearer {token}",
                "api-id": CHART_API_ID,
                "cont-yn": cont_yn,
                "next-key": next_key,
            },
            timeout=timeout,
        )
        code = response.get("return_code", 0)
        if status != 200 or str(code) not in {"0", "None"}:
            message = str(response.get("return_msg", ""))[:160]
            raise ReplayDataError(
                f"ka10080 실패: page={page_number}, HTTP {status}, code={code}, msg={message!r}"
            )
        bars = response.get(BAR_LIST_KEY)
        if not isinstance(bars, list):
            raise ReplayDataError(f"ka10080 분봉 배열 누락: page={page_number}")
        pages.append(
            {
                "page_number": page_number,
                "request": body,
                "response": response,
                "raw_body": raw_body,
                "continuation": {
                    "cont_yn": str(response_headers.get("cont-yn", "")).upper(),
                    "next_key_sha256": sha256_bytes(
                        str(response_headers.get("next-key", "")).encode("utf-8")
                    ),
                },
            }
        )

        timestamps = [
            str(row.get("cntr_tm", ""))
            for row in bars
            if isinstance(row, dict) and str(row.get("cntr_tm", ""))
        ]
        dates = {stamp[:8] for stamp in timestamps if len(stamp) >= 8}
        has_target = trade_date in dates
        oldest = min(dates) if dates else ""
        new_cont_yn = str(response_headers.get("cont-yn", "")).upper()
        new_next_key = str(response_headers.get("next-key", ""))
        if has_target and oldest and oldest < trade_date:
            break
        if new_cont_yn != "Y" or not new_next_key:
            break
        cont_yn = "Y"
        next_key = new_next_key
    return pages


def fetch_daily_pages(
    token: str,
    *,
    symbol: str,
    base_date: str,
    timeout: float,
    max_pages: int,
    minimum_rows: int = 10,
    pacer: ReadOnlyPacer | None = None,
) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    cont_yn = "N"
    next_key = ""
    seen_dates: set[str] = set()
    for page_number in range(1, max_pages + 1):
        body = {
            "stk_cd": symbol,
            "base_dt": base_date,
            "upd_stkpc_tp": "1",
        }
        if pacer is not None:
            pacer.wait()
        response, response_headers, status, raw_body = post_json(
            API_BASE + CHART_ENDPOINT,
            body,
            {
                "authorization": f"Bearer {token}",
                "api-id": DAILY_API_ID,
                "cont-yn": cont_yn,
                "next-key": next_key,
            },
            timeout=timeout,
        )
        code = response.get("return_code", 0)
        if status != 200 or str(code) not in {"0", "None"}:
            message = str(response.get("return_msg", ""))[:160]
            raise ReplayDataError(
                f"ka10081 실패: page={page_number}, HTTP {status}, code={code}, msg={message!r}"
            )
        rows = response.get(DAILY_LIST_KEY)
        if not isinstance(rows, list):
            raise ReplayDataError(f"ka10081 일봉 배열 누락: page={page_number}")
        for row in rows:
            if isinstance(row, dict):
                stamp = str(row.get("dt", "")).strip()[:8]
                if stamp and stamp <= base_date:
                    seen_dates.add(stamp)
        pages.append(
            {
                "page_number": page_number,
                "request": body,
                "response": response,
                "raw_body": raw_body,
                "continuation": {
                    "cont_yn": str(response_headers.get("cont-yn", "")).upper(),
                    "next_key_sha256": sha256_bytes(
                        str(response_headers.get("next-key", "")).encode("utf-8")
                    ),
                },
            }
        )
        new_cont_yn = str(response_headers.get("cont-yn", "")).upper()
        new_next_key = str(response_headers.get("next-key", ""))
        if len(seen_dates) >= max(1, minimum_rows):
            break
        if new_cont_yn != "Y" or not new_next_key:
            break
        cont_yn = "Y"
        next_key = new_next_key
    return pages


def positive_int(value: Any, field: str) -> int:
    text = str(value).strip().replace(",", "")
    try:
        return abs(int(float(text)))
    except ValueError as exc:
        raise ReplayDataError(f"분봉 숫자 변환 실패: {field}={text!r}") from exc


def normalize_target_bars(pages: list[dict[str, Any]], trade_date: str) -> list[dict[str, Any]]:
    by_timestamp: dict[str, dict[str, Any]] = {}
    for page in pages:
        bars = page["response"].get(BAR_LIST_KEY, [])
        for row in bars:
            if not isinstance(row, dict):
                continue
            timestamp = str(row.get("cntr_tm", "")).strip()
            if not timestamp.startswith(trade_date) or len(timestamp) < 12:
                continue
            normalized = {
                "timestamp": timestamp,
                "open": positive_int(row.get("open_pric"), "open_pric"),
                "high": positive_int(row.get("high_pric"), "high_pric"),
                "low": positive_int(row.get("low_pric"), "low_pric"),
                "close": positive_int(row.get("cur_prc"), "cur_prc"),
                "volume": positive_int(row.get("trde_qty"), "trde_qty"),
            }
            if str(row.get("acc_trde_qty", "")).strip():
                normalized["acc_volume"] = positive_int(row.get("acc_trde_qty"), "acc_trde_qty")
            if normalized["low"] > min(normalized["open"], normalized["close"]):
                raise ReplayDataError(f"저가 불변식 오류: {timestamp}")
            if normalized["high"] < max(normalized["open"], normalized["close"]):
                raise ReplayDataError(f"고가 불변식 오류: {timestamp}")
            previous = by_timestamp.get(timestamp)
            if previous is not None and previous != normalized:
                raise ReplayDataError(f"동일 시각 분봉 충돌: {timestamp}")
            by_timestamp[timestamp] = normalized
    rows = [by_timestamp[key] for key in sorted(by_timestamp)]
    if not rows:
        raise ReplayDataError(f"대상 거래일 1분봉 없음: {trade_date}")
    return rows


def normalize_daily_bars(
    pages: list[dict[str, Any]], base_date: str, *, minimum_rows: int = 10
) -> list[dict[str, Any]]:
    by_date: dict[str, dict[str, Any]] = {}
    for page in pages:
        rows = page["response"].get(DAILY_LIST_KEY, [])
        for row in rows:
            if not isinstance(row, dict):
                continue
            stamp = str(row.get("dt", "")).strip()[:8]
            if len(stamp) != 8 or stamp > base_date:
                continue
            normalized = {
                "date": stamp,
                "open": positive_int(row.get("open_pric"), "open_pric"),
                "high": positive_int(row.get("high_pric"), "high_pric"),
                "low": positive_int(row.get("low_pric"), "low_pric"),
                "close": positive_int(row.get("cur_prc"), "cur_prc"),
                "volume": positive_int(row.get("trde_qty"), "trde_qty"),
            }
            if str(row.get("trde_prica", "")).strip():
                normalized["traded_value_million_krw"] = positive_int(
                    row.get("trde_prica"), "trde_prica"
                )
            if normalized["low"] > min(normalized["open"], normalized["close"]):
                raise ReplayDataError(f"일봉 저가 불변식 오류: {stamp}")
            if normalized["high"] < max(normalized["open"], normalized["close"]):
                raise ReplayDataError(f"일봉 고가 불변식 오류: {stamp}")
            previous = by_date.get(stamp)
            if previous is not None and previous != normalized:
                raise ReplayDataError(f"동일 날짜 일봉 충돌: {stamp}")
            by_date[stamp] = normalized
    ordered = [by_date[key] for key in sorted(by_date, reverse=True)]
    if len(ordered) < minimum_rows:
        raise ReplayDataError(
            f"ka10081 일봉 부족: base_date={base_date}, rows={len(ordered)}"
        )
    return ordered


def official_api_audit() -> dict[str, Any]:
    if not OFFICIAL_SPEC.is_file():
        raise ReplayDataError(f"공식 JSON 명세 없음: {OFFICIAL_SPEC}")
    try:
        spec = json.loads(OFFICIAL_SPEC.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReplayDataError(f"공식 JSON 명세 읽기 실패: {type(exc).__name__}") from exc
    if not isinstance(spec, dict):
        raise ReplayDataError("공식 JSON 명세 최상위 형식 오류")

    required = {
        "ka10080": {
            "url": "/api/dostk/chart",
            "request": {"stk_cd", "tic_scope", "upd_stkpc_tp", "base_dt"},
            "response": {"stk_min_pole_chart_qry"},
        },
        "ka10081": {
            "url": "/api/dostk/chart",
            "request": {"stk_cd", "base_dt", "upd_stkpc_tp"},
            "response": {"stk_dt_pole_chart_qry"},
        },
        "ka10023": {
            "url": "/api/dostk/rkinfo",
            "request": {"mrkt_tp", "sort_tp", "tm_tp", "trde_qty_tp", "tm", "stk_cnd", "pric_tp", "stex_tp"},
            "response": {"trde_qty_sdnin"},
        },
        "ka10027": {
            "url": "/api/dostk/rkinfo",
            "request": {"mrkt_tp", "sort_tp", "trde_qty_cnd", "stk_cnd", "crd_cnd", "updown_incls", "pric_cnd", "trde_prica_cnd", "stex_tp"},
            "response": {"pred_pre_flu_rt_upper"},
        },
        "ka90009": {
            "url": "/api/dostk/rkinfo",
            "request": {"mrkt_tp", "amt_qty_tp", "qry_dt_tp", "date", "stex_tp"},
            "response": {"frgnr_orgn_trde_upper"},
        },
        "ka10001": {
            "url": "/api/dostk/stkinfo",
            "request": {"stk_cd"},
            "response": {"cur_prc"},
        },
    }
    audit: dict[str, Any] = {}
    for api_id, expectation in required.items():
        entry = spec.get(api_id)
        if not isinstance(entry, dict):
            raise ReplayDataError(f"공식 JSON API 누락: {api_id}")
        request_fields = {
            str(item.get("itemId", ""))
            for item in entry.get("requestIo", [])
            if isinstance(item, dict)
        }
        response_fields = {
            str(item.get("itemId", "")).removeprefix("- ")
            for item in entry.get("responseIo", [])
            if isinstance(item, dict)
        }
        if str(entry.get("url", "")) != expectation["url"]:
            raise ReplayDataError(f"공식 JSON URL 불일치: {api_id}")
        if not expectation["request"].issubset(request_fields):
            raise ReplayDataError(f"공식 JSON 요청필드 불일치: {api_id}")
        if not expectation["response"].issubset(response_fields):
            raise ReplayDataError(f"공식 JSON 응답필드 불일치: {api_id}")
        audit[api_id] = {
            "url": entry.get("url"),
            "request_fields": sorted(
                field
                for field in request_fields
                if field not in {"api-id", "authorization", "cont-yn", "next-key"}
            ),
            "response_list_or_value": sorted(expectation["response"]),
        }
    return audit


def persist_raw_pages(
    root: Path, dataset_id: str, api_id: str, pages: list[dict[str, Any]], retrieved_at: str
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for page in pages:
        number = int(page["page_number"])
        raw_path = root / "raw" / f"{dataset_id}_page_{number:03d}.response.json"
        meta_path = root / "raw" / f"{dataset_id}_page_{number:03d}.meta.json"
        write_bytes(raw_path, page["raw_body"])
        write_json(
            meta_path,
            {
                "dataset_id": dataset_id,
                "api_id": api_id,
                "page_number": number,
                "request": page["request"],
                "retrieved_at": retrieved_at,
                "continuation": page["continuation"],
                "raw_response_path": raw_path.name,
                "raw_response_sha256": sha256_file(raw_path),
                "secrets_saved": False,
            },
        )
        records.append(
            {
                "raw_path": str(raw_path.relative_to(root)).replace(os.sep, "/"),
                "raw_sha256": sha256_file(raw_path),
                "meta_path": str(meta_path.relative_to(root)).replace(os.sep, "/"),
                "meta_sha256": sha256_file(meta_path),
            }
        )
    return records


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    path.write_bytes(data)


def write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def acquire_case(
    case_id: str,
    *,
    output_root: Path,
    credential_pointer: Path,
    timeout: float,
    max_pages: int,
) -> Path:
    case = CONFIRMED_CASES.get(case_id)
    if case is None:
        raise ReplayDataError(f"확정 사례 아님: {case_id}")
    app_key, secret_key = load_credentials(credential_pointer)
    token = issue_memory_only_token(app_key, secret_key, timeout=timeout)
    pages = fetch_chart_pages(
        token,
        symbol=case["symbol"],
        trade_date=case["trade_date"],
        timeout=timeout,
        max_pages=max_pages,
    )
    bars = normalize_target_bars(pages, case["trade_date"])
    retrieved_at = datetime.now(KST).isoformat(timespec="seconds")

    destination = output_root / case_id
    with tempfile.TemporaryDirectory(prefix="replay-data-") as temp_name:
        temp_dir = Path(temp_name) / case_id
        raw_paths: list[Path] = []
        meta_paths: list[Path] = []
        for page in pages:
            page_number = int(page["page_number"])
            raw_path = temp_dir / "raw" / f"page_{page_number:03d}.response.json"
            meta_path = temp_dir / "raw" / f"page_{page_number:03d}.meta.json"
            write_bytes(raw_path, page["raw_body"])
            write_json(
                meta_path,
                {
                    "page_number": page_number,
                    "request": page["request"],
                    "continuation": page["continuation"],
                    "raw_response_path": raw_path.name,
                    "raw_response_sha256": sha256_file(raw_path),
                },
            )
            raw_paths.append(raw_path)
            meta_paths.append(meta_path)
        bars_path = temp_dir / "completed_1m_chronological.json"
        write_json(bars_path, bars)
        manifest = {
            "schema": "REPLAY-DATA-001/v1",
            "case_id": case_id,
            "symbol": case["symbol"],
            "name": case["name"],
            "trade_date": case["trade_date"],
            "expected": case["expected"],
            "expectation_sources": case["expectation_sources"],
            "market_data_source": {
                "provider": "Kiwoom REST API",
                "api_id": CHART_API_ID,
                "endpoint": API_BASE + CHART_ENDPOINT,
                "request": {
                    "stk_cd": case["symbol"],
                    "tic_scope": "1",
                    "upd_stkpc_tp": "1",
                    "base_dt": case["trade_date"],
                },
                "retrieved_at": retrieved_at,
                "official_spec_path": str(OFFICIAL_SPEC),
                "official_spec_sha256": sha256_file(OFFICIAL_SPEC) if OFFICIAL_SPEC.is_file() else None,
            },
            "integrity": {
                "page_count": len(raw_paths),
                "completed_1m_count": len(bars),
                "first_timestamp": bars[0]["timestamp"],
                "last_timestamp": bars[-1]["timestamp"],
                "raw_pages": [
                    {
                        "path": str(path.relative_to(temp_dir)).replace(os.sep, "/"),
                        "sha256": sha256_file(path),
                        "metadata_path": str(meta_paths[index].relative_to(temp_dir)).replace(os.sep, "/"),
                        "metadata_sha256": sha256_file(meta_paths[index]),
                    }
                    for index, path in enumerate(raw_paths)
                ],
                "completed_1m_path": str(bars_path.relative_to(temp_dir)).replace(os.sep, "/"),
                "completed_1m_sha256": sha256_file(bars_path),
            },
            "secrets_saved": False,
            "runtime_files_used": False,
            "release_zip_include": False,
        }
        write_json(temp_dir / "manifest.json", manifest)
        if destination.exists():
            shutil.rmtree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(temp_dir, destination)
    return destination


def acquire_str01_context(
    *,
    output_root: Path,
    credential_pointer: Path,
    timeout: float,
    max_pages: int,
) -> Path:
    """Collect only officially reproducible REPLAY-DATA-002 inputs."""

    spec_audit = official_api_audit()
    app_key, secret_key = load_credentials(credential_pointer)
    token = issue_memory_only_token(app_key, secret_key, timeout=timeout)
    retrieved_at = datetime.now(KST).isoformat(timespec="seconds")
    pacer = ReadOnlyPacer()

    previous_pages = fetch_chart_pages(
        token,
        symbol="035720",
        trade_date="20260806",
        timeout=timeout,
        max_pages=max_pages,
        pacer=pacer,
    )
    previous_bars = normalize_target_bars(previous_pages, "20260806")

    kakao_daily_pages = fetch_daily_pages(
        token,
        symbol="035720",
        base_date="20260806",
        timeout=timeout,
        max_pages=max_pages,
        minimum_rows=10,
        pacer=pacer,
    )
    kakao_daily = normalize_daily_bars(kakao_daily_pages, "20260806")

    proxy_payloads: dict[str, dict[str, Any]] = {}
    for symbol, name in (("069500", "KODEX200"), ("229200", "KODEX코스닥150")):
        minute_pages = fetch_chart_pages(
            token,
            symbol=symbol,
            trade_date="20260807",
            timeout=timeout,
            max_pages=max_pages,
            pacer=pacer,
        )
        minutes = normalize_target_bars(minute_pages, "20260807")
        daily_pages = fetch_daily_pages(
            token,
            symbol=symbol,
            base_date="20260806",
            timeout=timeout,
            max_pages=max_pages,
            minimum_rows=1,
            pacer=pacer,
        )
        daily = normalize_daily_bars(daily_pages, "20260806", minimum_rows=1)
        proxy_payloads[symbol] = {
            "name": name,
            "minute_pages": minute_pages,
            "minutes": minutes,
            "daily_pages": daily_pages,
            "reference_daily": daily[0],
        }

    case_root = output_root / "STR-01"
    destination = case_root / "context"
    with tempfile.TemporaryDirectory(prefix="replay-context-") as temp_name:
        temp_root = Path(temp_name) / "context"
        raw_records: dict[str, list[dict[str, Any]]] = {}

        raw_records["previous_session_1m"] = persist_raw_pages(
            temp_root, "previous_session_1m", CHART_API_ID, previous_pages, retrieved_at
        )
        previous_path = temp_root / "previous_session_1m.json"
        write_json(previous_path, previous_bars)

        raw_records["daily_volume_refs"] = persist_raw_pages(
            temp_root, "daily_volume_refs", DAILY_API_ID, kakao_daily_pages, retrieved_at
        )
        recent_10 = kakao_daily[:10]
        daily_refs = {
            "api_id": DAILY_API_ID,
            "symbol": "035720",
            "cutoff_date": "20260806",
            "previous_session": recent_10[0],
            "recent_10_sessions": recent_10,
            "recent_10_average_volume": sum(row["volume"] for row in recent_10) / len(recent_10),
            "source_is_official_rest": True,
        }
        daily_path = temp_root / "daily_volume_refs.json"
        write_json(daily_path, daily_refs)

        proxy_paths: dict[str, Path] = {}
        for symbol, payload in proxy_payloads.items():
            input_id = f"market_proxy_{symbol}"
            raw_records[input_id] = []
            raw_records[input_id].extend(
                persist_raw_pages(
                    temp_root,
                    f"{input_id}_1m",
                    CHART_API_ID,
                    payload["minute_pages"],
                    retrieved_at,
                )
            )
            raw_records[input_id].extend(
                persist_raw_pages(
                    temp_root,
                    f"{input_id}_reference_daily",
                    DAILY_API_ID,
                    payload["daily_pages"],
                    retrieved_at,
                )
            )
            proxy_path = temp_root / f"{input_id}.json"
            write_json(
                proxy_path,
                {
                    "minute_api_id": CHART_API_ID,
                    "reference_api_id": DAILY_API_ID,
                    "symbol": symbol,
                    "name": payload["name"],
                    "trade_date": "20260807",
                    "reference_date": payload["reference_daily"]["date"],
                    "reference_close": payload["reference_daily"]["close"],
                    "completed_1m_count": len(payload["minutes"]),
                    "completed_1m": payload["minutes"],
                    "source_is_official_rest": True,
                },
            )
            proxy_paths[input_id] = proxy_path

        unavailable = {
            "candidate_admission": {
                "status": "official_historical_data_unavailable",
                "required": "2026-08-07 당시 카카오가 실제 감시 후보가 된 REST 원본과 편입 가능 시각",
                "reason": (
                    "ka10023·ka10027 요청에는 과거 거래일과 당시 스냅샷 시각 필드가 없다. "
                    "ka90009는 date가 있으나 일별 외국인·기관 순위이며 당시 후보 스냅샷 시각을 반환하지 않는다."
                ),
                "synthetic_or_engine_result_used": False,
            },
            "quote_timeline_035720": {
                "status": "official_historical_data_unavailable",
                "required": "2026-08-07 실제 엔진 호출 시각별 카카오 시세 흐름",
                "reason": (
                    "ka10001은 종목코드만 받아 현재 시세를 반환하고 과거 조회 시각 필드가 없다. "
                    "ka10080 완성 1분봉은 이미 확보했지만 실제 호출 순간의 호가·현재가 이력과 동일하지 않다."
                ),
                "available_but_not_substituted": "STR-01/completed_1m_chronological.json",
                "synthetic_or_engine_result_used": False,
            },
        }
        unavailable_path = temp_root / "official_unavailable.json"
        write_json(unavailable_path, unavailable)

        dataset_paths = {
            "previous_session_1m": previous_path,
            "daily_volume_refs": daily_path,
            **proxy_paths,
        }
        status_by_input: dict[str, Any] = {}
        for input_id, path in dataset_paths.items():
            completed_count = 0
            if input_id == "previous_session_1m":
                completed_count = len(previous_bars)
            elif input_id.startswith("market_proxy_"):
                completed_count = len(proxy_payloads[input_id.rsplit("_", 1)[-1]]["minutes"])
            status_by_input[input_id] = {
                "status": "acquired",
                "path": str(path.relative_to(temp_root)).replace(os.sep, "/"),
                "sha256": sha256_file(path),
                "completed_1m_count": completed_count,
                "raw_pages": raw_records[input_id],
            }
        for input_id, detail in unavailable.items():
            status_by_input[input_id] = {
                **detail,
                "evidence_path": str(unavailable_path.relative_to(temp_root)).replace(os.sep, "/"),
            }

        manifest = {
            "schema": "REPLAY-DATA-002/v1",
            "case_id": "STR-01",
            "symbol": "035720",
            "name": "카카오",
            "trade_date": "20260807",
            "retrieved_at": retrieved_at,
            "official_spec": {
                "path": str(OFFICIAL_SPEC),
                "sha256": sha256_file(OFFICIAL_SPEC),
                "api_audit": spec_audit,
            },
            "status_by_required_input": status_by_input,
            "acquired_count": sum(
                1 for item in status_by_input.values() if item.get("status") == "acquired"
            ),
            "official_unavailable_count": sum(
                1
                for item in status_by_input.values()
                if item.get("status") == "official_historical_data_unavailable"
            ),
            "secrets_saved": False,
            "account_data_saved": False,
            "order_api_called": False,
            "runtime_files_used": False,
            "release_zip_include": False,
        }
        write_json(temp_root / "manifest.json", manifest)
        if destination.exists():
            shutil.rmtree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(temp_root, destination)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description="REPLAY-DATA-001/002 키움 REST 읽기 전용 원본 수집")
    parser.add_argument("case_id", choices=sorted(CONFIRMED_CASES))
    parser.add_argument(
        "--context",
        action="store_true",
        help="REPLAY-DATA-002에 필요한 STR-01 과거 입력만 수집",
    )
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--credential-pointer", type=Path, default=DEFAULT_CREDENTIAL_POINTER)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--max-pages", type=int, default=10)
    args = parser.parse_args()
    try:
        if args.context:
            if args.case_id != "STR-01":
                raise ReplayDataError("REPLAY-DATA-002 context는 STR-01만 허용")
            destination = acquire_str01_context(
                output_root=args.output_root.resolve(),
                credential_pointer=args.credential_pointer.resolve(),
                timeout=max(3.0, args.timeout),
                max_pages=max(1, min(args.max_pages, 30)),
            )
        else:
            destination = acquire_case(
                args.case_id,
                output_root=args.output_root.resolve(),
                credential_pointer=args.credential_pointer.resolve(),
                timeout=max(3.0, args.timeout),
                max_pages=max(1, min(args.max_pages, 30)),
            )
    except ReplayDataError as exc:
        print(f"REPLAY_DATA_FAIL: {exc}", file=sys.stderr)
        return 1
    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    if args.context:
        print(
            "REPLAY_CONTEXT_OK "
            f"case={manifest['case_id']} acquired={manifest['acquired_count']} "
            f"official_unavailable={manifest['official_unavailable_count']}"
        )
    else:
        integrity = manifest["integrity"]
        print(
            "REPLAY_DATA_OK "
            f"case={manifest['case_id']} symbol={manifest['symbol']} date={manifest['trade_date']} "
            f"bars={integrity['completed_1m_count']} pages={integrity['page_count']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
