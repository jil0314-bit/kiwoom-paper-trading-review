#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.60 PAPER — 표본충분도 인식형 진입품질 최종판.

V75.59를 SHA256으로 고정 상속하며 매매·주문·손절·목표가·대시보드·영웅문
연동 로직은 변경하지 않는다. Claude가 제안한 '표본 부족이면 결론 유보' 원칙을
보고서에 명시하고, 전체 건수뿐 아니라 완전경로·거래일·종목수·구간별 분모와
95% Wilson 구간까지 표시한다.

PAPER 전용. 관찰 보고서는 주문에 개입하지 않는다.
"""
from __future__ import annotations

import csv
from datetime import datetime
import hashlib
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import ModuleType
from typing import Any

VERSION = "V75.60-SAMPLE-AWARE-FINAL-PAPER"
BASE_DIR = Path(__file__).resolve().parent
V7559_FILENAME = "bot_v75_59_entry_quality_observer_hardened_paper.py"
V7559_SHA256 = "9b88082b417fe6353cf9c2eb589b254428d88696942882e68a2e52a66084e219"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_fixed(filename: str, expected_sha: str, module_name: str) -> ModuleType:
    path = BASE_DIR / filename
    actual = sha256_file(path)
    if actual.lower() != expected_sha.lower():
        raise RuntimeError(f"{filename} SHA256 불일치\n기대: {expected_sha}\n실제: {actual}")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"{filename} 로드 실패")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


v7559 = _load_fixed(V7559_FILENAME, V7559_SHA256, "v7560_immutable_v7559_base")
KST = v7559.KST

_report_path = BASE_DIR / "entry_quality_report_v3.py"
_report_spec = importlib.util.spec_from_file_location("v7560_entry_quality_report", _report_path)
if _report_spec is None or _report_spec.loader is None:
    raise RuntimeError("entry_quality_report_v3.py 로드 실패")
_report_module = importlib.util.module_from_spec(_report_spec)
sys.modules[_report_spec.name] = _report_module
_report_spec.loader.exec_module(_report_module)

analyze_quality_sample_aware = _report_module.analyze_quality_sample_aware
wilson_interval = _report_module.wilson_interval
sample_grade = _report_module.sample_grade

_SAMPLE_TERMS = """

[표본충분도]
결론 유보: 표본이 너무 적어 숫자는 보이지만 손절선·전략 변경 판단에는 사용하지 않는 단계
완전경로: 매수부터 청산까지 재시작 누락 없이 MAE·MFE가 온전히 관찰된 거래
95% 구간: 같은 방식으로 자료를 다시 모을 때 실제 비율이 있을 가능성이 높은 범위. 넓을수록 불확실
거래일 분산: 거래 수가 많아도 하루나 한 장세에 몰리면 표본이 충분하지 않을 수 있다는 점검
종목 집중도: 같은 종목 반복 거래가 전체 표본을 부풀리지 않았는지 확인하는 값
표본 관문: 완전경로 100건·20거래일·10종목·해당 하락구간 30건을 기본 확인 기준으로 사용
"""
if _SAMPLE_TERMS.strip() not in v7559.v7557.DASHBOARD_TERMS:
    v7559.v7557.DASHBOARD_TERMS += _SAMPLE_TERMS

# 콘솔/대시보드에 표시되는 버전만 갱신한다.
v7559.v7557.v7556.v7525.VERSION = VERSION


def quality_report() -> str:
    v7559.TRACKER.flush()
    return analyze_quality_sample_aware(
        v7559.TRADES_V2_CSV,
        v7559.ACTIVE_STATE_JSON,
        v7559.PORTFOLIO_SNAPSHOTS_CSV,
        read_trade_rows=v7559._tracker_module.read_trade_rows,
        csv_module=csv,
        legacy_csv=v7559.LEGACY_V58_CSV,
    )


def _write_trade_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    columns = list(v7559._tracker_module.CSV_COLUMNS)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            normalized = {key: row.get(key, "") for key in columns}
            writer.writerow(normalized)


def self_check() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []
    results.append((
        "V75.59기준SHA",
        sha256_file(BASE_DIR / V7559_FILENAME) == V7559_SHA256,
        V7559_SHA256[:16],
    ))
    base_rows = v7559.self_check()
    results.append((
        "V75.59회귀",
        all(ok for _, ok, _ in base_rows),
        f"{sum(ok for _, ok, _ in base_rows)}/{len(base_rows)}",
    ))
    low, high = wilson_interval(5, 10)
    results.append((
        "Wilson구간",
        23.0 < low < 24.0 and 76.0 < high < 77.0,
        f"{low:.2f}~{high:.2f}",
    ))
    results.append((
        "표본등급",
        sample_grade(5) == "결론 유보" and sample_grade(30) == "초기 경향" and sample_grade(100) == "표본 충분",
        f"5={sample_grade(5)},30={sample_grade(30)},100={sample_grade(100)}",
    ))

    with tempfile.TemporaryDirectory(prefix="v7560_selfcheck_") as tmp:
        root = Path(tmp)
        trades = root / "trades.csv"
        active = root / "active.json"
        snapshots = root / "snapshots.csv"
        rows: list[dict[str, Any]] = []
        for index in range(12):
            complete = index != 11
            rows.append({
                "schema_version": "2",
                "trade_id": f"T-{index}",
                "symbol": "005930" if index < 8 else f"{100000 + index:06d}",
                "name": "삼성전자" if index < 8 else f"종목{index}",
                "entry_time": datetime(2026, 7, 1 + index % 3, 9, 10, tzinfo=KST).isoformat(),
                "entry_price": "10000",
                "target_price": "10150",
                "pattern": "테스트패턴",
                "entry_reason": "테스트",
                "mfe_pct": "1.5" if index % 2 == 0 else "0.5",
                "mae_pct": "-3.0" if index < 6 else "-1.0",
                "target_touched": "Y" if index % 2 == 0 else "N",
                "max_adverse_before_target_pct": "-3.0" if index % 2 == 0 else "0",
                "net_pnl_pct": "1.2" if index % 2 == 0 else "-1.0",
                "history_complete": "Y" if complete else "N",
            })
        _write_trade_rows(trades, rows)
        active.write_text('{"active": []}', encoding="utf-8")
        report = analyze_quality_sample_aware(
            trades,
            active,
            snapshots,
            read_trade_rows=v7559._tracker_module.read_trade_rows,
            csv_module=csv,
        )
        results.append((
            "표본부족결론유보",
            "판정: 결론 유보" in report and "/100건" in report,
            "전역 표본 관문",
        ))
        results.append((
            "불완전경로추론제외",
            "완전한 시세경로 11건" in report and "불완전한 1건" in report,
            "경로 품질 분리",
        ))
        results.append((
            "구간별분모신뢰구간",
            "95%" in report and "관련표본" in report and "결론 유보" in report,
            "손절구간별 판정",
        ))
        results.append((
            "집중도경고",
            "반복 거래가 독립 표본처럼 부풀려질 수 있습니다" in report,
            "종목 반복 편향",
        ))
    return results


def main() -> int:
    return int(v7559.main())


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        rows = self_check()
        for name, ok, detail in rows:
            print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
        raise SystemExit(0 if all(ok for _, ok, _ in rows) else 1)
    if "--quality-report" in sys.argv:
        print(quality_report())
        raise SystemExit(0)
    raise SystemExit(main())
