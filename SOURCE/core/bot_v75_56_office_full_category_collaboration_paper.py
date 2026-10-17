#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.56 PAPER — 사무실 PC용 전체 카테고리 GUI + 안전한 협업 입력 통합판.

검증된 V75.48 매매 엔진·콘솔·영웅문 차트 연동·한 번/더블클릭·키보드
화살표 이동을 SHA256으로 고정 상속한다. V75.55의 협업 패널과 안전 로직을
그대로 유지하면서, 콘솔의 7개 카테고리를 GUI에서 누락 없이 한 화면에 표시하고
카테고리별 개수·필터·색상 구분을 추가한다.
Claude 교차검증과 실제 반례 실행에서 드러난 6자리 가격 오인, 부정문 오인,
지원하지 않는 시간·손절·투입금 조건의 침묵 누락, 오래된 해석 카드 적용,
중복 가격조건 및 실행 직전 버전 경쟁을 보강한다. PAPER 전용이다.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
import difflib
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import queue
import re
import sys
import threading
import time
from types import ModuleType
from typing import Any, Iterable
from uuid import uuid4

VERSION = "V75.56-OFFICE-FULL-CATEGORY-COLLABORATION-PAPER"
BASE_DIR = Path(__file__).resolve().parent
V7548_FILENAME = "bot_v75_48_holding_totals_console_paper.py"
V7548_SHA256 = "99d9240fb7716dfc077a2197b59f9052cd23bd861dbbc6c3da2328183fbbc17a"
RUNTIME_DIR = BASE_DIR / "v75_56_runtime"
COLLAB_STATE_FILE = RUNTIME_DIR / "collaboration_state.json"
COLLAB_LOG_FILE = RUNTIME_DIR / "collaboration_events.jsonl"


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


v7548 = _load_fixed(V7548_FILENAME, V7548_SHA256, "v7555_immutable_v7548_base")
v7547 = v7548.v7547
v7546 = v7548.v7546
v7544 = v7548.v7544
v7543 = v7548.v7543
v7538 = v7547.v7538
v7530 = v7547.v7530
v7525 = v7548.v7525
KST = v7548.KST
TERMINAL_OVERLAY_STATUSES = frozenset({"CANCELLED", "EXPIRED", "EXECUTED", "BLOCKED"})
OUTLIER_RATIO_HIGH = 2.0
OUTLIER_RATIO_LOW = 0.5
_V7555_NAME_HINT: dict[str, str] = {}
_V7555_PRICE_HINT: dict[str, float] = {}

GUI_STAGE_ORDER = (
    "관심종목", "관찰후보", "매수가능성", "매수직전",
    "시장보류", "매수종목", "매도종목",
)
GUI_STAGE_COLORS: dict[str, tuple[str, str]] = {
    "관심종목": ("#f4f8ff", "#1f4f8a"),
    "관찰후보": ("#fffbe8", "#765c00"),
    "매수가능성": ("#eef9f0", "#176b2f"),
    "매수직전": ("#e5f6ea", "#0b5f24"),
    "시장보류": ("#fff0f0", "#8a1c1c"),
    "매수종목": ("#eef2ff", "#2d3f8f"),
    "매도종목": ("#f7f0ff", "#663399"),
}


def prepare_gui_rows(rows: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """콘솔 카테고리를 GUI용으로 정렬하고 정확한 중복만 제거한다.

    같은 종목이라도 현재 단계와 당일 매도 이력처럼 카테고리가 다르면 모두 보존한다.
    동일 카테고리·종목·시각·사유가 완전히 같은 행만 제거하여 화살표 탐색에서
    중복 노이즈를 줄인다. 알 수 없는 새 단계는 마지막에 보존한다.
    """
    prepared: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for raw in rows:
        row = dict(raw)
        stage = str(row.get("stage", "") or "미분류")
        symbol = str(row.get("symbol", ""))
        stage_time = str(row.get("stage_time", row.get("time", "-")) or "-")
        reason = str(row.get("reason", ""))
        key = (stage, symbol, stage_time, reason)
        if key in seen:
            continue
        seen.add(key)
        row["stage"] = stage
        row.setdefault("stage_time", stage_time)
        prepared.append(row)
    order = {stage: index for index, stage in enumerate(GUI_STAGE_ORDER)}
    prepared.sort(key=lambda row: (
        order.get(str(row.get("stage", "")), len(order)),
        str(row.get("stage_time", row.get("time", "-"))),
        str(row.get("name", "")),
        str(row.get("symbol", "")),
    ))
    counts = {stage: 0 for stage in GUI_STAGE_ORDER}
    for row in prepared:
        stage = str(row.get("stage", ""))
        counts[stage] = counts.get(stage, 0) + 1
    counts["전체"] = len(prepared)
    return prepared, counts


def _next_session_close_iso(now: datetime | None = None) -> str:
    """현재 시각 기준 다음 정규장 종료(평일 15:30 KST)를 반환한다."""
    cursor = now.astimezone(KST) if now is not None else datetime.now(KST)
    candidate = cursor.replace(hour=15, minute=30, second=0, microsecond=0)
    if cursor.weekday() >= 5 or cursor >= candidate:
        candidate = candidate + timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)
    return candidate.isoformat()


def _expired(expires_at: str, now: datetime | None = None) -> bool:
    if not expires_at:
        return False
    try:
        deadline = datetime.fromisoformat(expires_at)
    except (TypeError, ValueError):
        return True
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=KST)
    current = now.astimezone(KST) if now is not None else datetime.now(KST)
    return current >= deadline.astimezone(KST)


def _trigger_label(trigger: str) -> str:
    return "상향 돌파" if trigger == "AT_OR_ABOVE" else "가격 이하 도달"


def _trigger_code(label: str) -> str:
    return "AT_OR_ABOVE" if label in {"상향 돌파", "AT_OR_ABOVE"} else "AT_OR_BELOW"


AutoDiscoverHeroLink = v7546.AutoDiscoverHeroLink
LinkStatus = v7546.LinkStatus
CursorPosition = v7546.CursorPosition
capture_cursor_position = v7546.capture_cursor_position
restore_review_focus = v7547.restore_review_focus
select_arrow_index = v7546.select_arrow_index
find_iid_by_symbol = v7546.find_iid_by_symbol
diag = v7546.diag
_ORIGINAL_SELF_CHECK = v7548.self_check


@dataclass(slots=True)
class CollaborationDraft:
    """GUI에서 사용자가 확인하기 전의 해석 결과.

    errors는 적용을 막는 항목이고 warnings는 사용자가 확인하면 진행 가능한 항목이다.
    source_text와 explicit_symbol은 오래된 해석 카드를 잘못 적용하지 않기 위한 감사값이다.
    """

    action: str
    symbol: str = ""
    name: str = ""
    buy_price: float | None = None
    trigger: str = "AT_OR_BELOW"
    target_price: float | None = None
    note: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    source_text: str = ""
    explicit_symbol: bool = False


@dataclass(slots=True)
class CollaborationOverlay:
    """사용자가 확인한 수동 조건. 기존 자동단계를 덮어쓰지 않는다."""

    overlay_id: str
    version: int
    symbol: str
    name: str
    kind: str
    trigger: str = ""
    buy_price: float | None = None
    target_price: float | None = None
    note: str = ""
    status: str = "ACTIVE"
    created_at: str = ""
    updated_at: str = ""
    expires_at: str = ""
    last_error: str = ""
    cooldown_until: float = 0.0


class CollaborationHub:
    """GUI와 엔진 사이의 스레드 안전한 협업 명령·상태 저장소."""

    def __init__(self, state_file: Path, log_file: Path) -> None:
        self.state_file = state_file
        self.log_file = log_file
        self._lock = threading.RLock()
        self._commands: queue.Queue[dict[str, Any]] = queue.Queue()
        self._results: queue.Queue[dict[str, Any]] = queue.Queue()
        self._overlays: dict[str, CollaborationOverlay] = {}
        self._memos: list[dict[str, str]] = []
        self._load()

    def _load(self) -> None:
        with self._lock:
            if not self.state_file.exists():
                return
            try:
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                self._overlays = {
                    str(item["overlay_id"]): CollaborationOverlay(**item)
                    for item in data.get("overlays", [])
                    if isinstance(item, dict) and item.get("overlay_id")
                }
                self._memos = [dict(item) for item in data.get("memos", [])][-100:]
                recovered = False
                now_iso = datetime.now(KST).isoformat()
                for overlay in self._overlays.values():
                    if overlay.status == "EXECUTING":
                        # 주문 송신 직후 프로세스가 종료됐을 수 있으므로 자동 재실행하지 않는다.
                        overlay.version += 1
                        overlay.status = "BLOCKED"
                        overlay.updated_at = now_iso
                        overlay.last_error = "이전 실행 중 비정상 종료—보유·주문내역 확인 필요"
                        recovered = True
                        self._log_locked("EXECUTING_RECOVERED_BLOCKED", asdict(overlay))
                if self._expire_locked() or recovered:
                    self._save_locked()
            except Exception as exc:
                # 손상 파일을 조용히 덮어쓰지 않는다. 원본을 보존한 뒤 안전하게 빈 상태로 시작한다.
                self._overlays = {}
                self._memos = []
                try:
                    stamp = datetime.now(KST).strftime("%Y%m%d_%H%M%S")
                    backup = self.state_file.with_name(f"{self.state_file.stem}.corrupt_{stamp}{self.state_file.suffix}")
                    self.state_file.replace(backup)
                    self._log_locked("STATE_CORRUPT_BACKUP", {"backup": str(backup), "error": f"{type(exc).__name__}: {exc}"})
                except Exception:
                    pass

    def _expire_locked(self) -> bool:
        changed = False
        now_iso = datetime.now(KST).isoformat()
        for overlay in self._overlays.values():
            if overlay.status == "ACTIVE" and _expired(overlay.expires_at):
                overlay.version += 1
                overlay.status = "EXPIRED"
                overlay.updated_at = now_iso
                overlay.last_error = "유효기간 종료"
                changed = True
                self._log_locked("OVERLAY_EXPIRED", asdict(overlay))
        return changed

    def _save_locked(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": VERSION,
            "saved_at": datetime.now(KST).isoformat(),
            "overlays": [asdict(item) for item in self._overlays.values()],
            "memos": self._memos[-100:],
        }
        temp = self.state_file.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.state_file)

    def _log_locked(self, event: str, payload: dict[str, Any]) -> None:
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        row = {"at": datetime.now(KST).isoformat(), "event": event, **payload}
        with self.log_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def submit(self, command: dict[str, Any]) -> str:
        """엔진 처리 대기열에 명령을 넣고 추적 가능한 command_id를 반환한다."""
        command = dict(command)
        command.setdefault("command_id", uuid4().hex)
        command.setdefault("submitted_at", datetime.now(KST).isoformat())
        self._commands.put(command)
        return str(command["command_id"])

    def report_result(self, command_id: str, *, ok: bool, message: str, kind: str = "") -> None:
        """엔진의 실제 적용 결과를 GUI에 전달한다."""
        self._results.put({
            "command_id": str(command_id),
            "ok": bool(ok),
            "message": str(message),
            "kind": str(kind),
            "at": datetime.now(KST).isoformat(),
        })

    def drain_results(self, limit: int = 50) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for _ in range(limit):
            try:
                result.append(self._results.get_nowait())
            except queue.Empty:
                break
        return result

    def drain(self, limit: int = 50) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for _ in range(limit):
            try:
                result.append(self._commands.get_nowait())
            except queue.Empty:
                break
        return result

    def activate(self, command: dict[str, Any]) -> CollaborationOverlay:
        now = datetime.now(KST).isoformat()
        overlay = CollaborationOverlay(
            overlay_id=uuid4().hex,
            version=1,
            symbol=str(command.get("symbol", "")),
            name=str(command.get("name", "")),
            kind=str(command.get("kind", "BUY_CONDITION")),
            trigger=str(command.get("trigger", "")),
            buy_price=_finite_or_none(command.get("buy_price")),
            target_price=_finite_or_none(command.get("target_price")),
            note=str(command.get("note", "")),
            created_at=now,
            updated_at=now,
            expires_at=str(command.get("expires_at") or _next_session_close_iso()),
        )
        with self._lock:
            self._overlays[overlay.overlay_id] = overlay
            self._save_locked()
            self._log_locked("OVERLAY_ACTIVATED", asdict(overlay))
        return overlay

    def add_memo(self, note: str) -> None:
        with self._lock:
            item = {"at": datetime.now(KST).isoformat(), "note": str(note)}
            self._memos.append(item)
            self._save_locked()
            self._log_locked("MEMO_ADDED", item)

    def cancel_symbol(self, symbol: str) -> int:
        changed = 0
        now = datetime.now(KST).isoformat()
        with self._lock:
            for overlay in self._overlays.values():
                if overlay.symbol == symbol and overlay.status == "ACTIVE":
                    overlay.version += 1
                    overlay.status = "CANCELLED"
                    overlay.updated_at = now
                    overlay.last_error = "사용자 해제"
                    changed += 1
                    self._log_locked("OVERLAY_CANCELLED", asdict(overlay))
            if changed:
                self._save_locked()
        return changed

    def update(self, overlay_id: str, *, expected_version: int, **changes: Any) -> bool:
        with self._lock:
            overlay = self._overlays.get(overlay_id)
            if overlay is None or overlay.version != expected_version:
                return False
            requested_status = str(changes.get("status", ""))
            if overlay.status in TERMINAL_OVERLAY_STATUSES and requested_status == "ACTIVE":
                return False
            for key, value in changes.items():
                if hasattr(overlay, key):
                    setattr(overlay, key, value)
            overlay.version += 1
            overlay.updated_at = datetime.now(KST).isoformat()
            self._save_locked()
            self._log_locked("OVERLAY_UPDATED", asdict(overlay))
            return True

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            if self._expire_locked():
                self._save_locked()
            overlays = [asdict(item) for item in self._overlays.values()]
            active = [item for item in overlays if item["status"] == "ACTIVE"]
            return {"active": active, "all": overlays, "memos": list(self._memos[-20:])}

    def active_overlays(self) -> list[CollaborationOverlay]:
        with self._lock:
            if self._expire_locked():
                self._save_locked()
            return [CollaborationOverlay(**asdict(item)) for item in self._overlays.values() if item.status == "ACTIVE"]

    def active_for_symbol(self, symbol: str, *, kind: str = "") -> list[CollaborationOverlay]:
        """해당 종목의 활성 지시 사본을 반환한다."""
        return [
            item for item in self.active_overlays()
            if item.symbol == symbol and (not kind or item.kind == kind)
        ]

    def get(self, overlay_id: str) -> CollaborationOverlay | None:
        with self._lock:
            item = self._overlays.get(overlay_id)
            return CollaborationOverlay(**asdict(item)) if item is not None else None


HUB = CollaborationHub(COLLAB_STATE_FILE, COLLAB_LOG_FILE)


def _finite_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def parse_korean_number(raw: str) -> float | None:
    """7만, 7만2천, 2.35만, 23,500을 원 단위 숫자로 변환한다."""
    text = str(raw).replace(",", "").replace("원", "").strip()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)만(?:\s*(\d+)천)?", text)
    if match:
        return float(match.group(1)) * 10000 + float(match.group(2) or 0) * 1000
    match = re.fullmatch(r"(\d+(?:\.\d+)?)천", text)
    if match:
        return float(match.group(1)) * 1000
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


_NUMBER_PATTERN = re.compile(r"\d+(?:\.\d+)?\s*만(?:\s*\d+천)?|\d[\d,]*(?:\.\d+)?\s*천?|\d[\d,]*(?:\.\d+)?")


def _numbers_with_positions(text: str) -> list[tuple[int, int, float, str]]:
    result: list[tuple[int, int, float, str]] = []
    for match in _NUMBER_PATTERN.finditer(text):
        value = parse_korean_number(match.group(0))
        if value is not None and value >= 100:
            result.append((match.start(), match.end(), value, match.group(0)))
    return result


def _nearest_number(text: str, keywords: Iterable[str], numbers: list[tuple[int, int, float, str]]) -> float | None:
    """행동 단어와 숫자 구간 사이의 실제 간격으로 가장 가까운 숫자를 고른다.

    숫자의 시작점만 비교하면 "23,500 ... 사고 목표 24,200"에서 뒤 숫자가
    잘못 선택될 수 있으므로 숫자 구간의 끝점도 함께 사용한다.
    """
    positions: list[int] = []
    for keyword in keywords:
        positions.extend(match.start() for match in re.finditer(re.escape(keyword), text))
    if not positions or not numbers:
        return None

    def distance(item: tuple[int, int, float, str], position: int) -> int:
        start, end = item[0], item[1]
        if position < start:
            return start - position
        if position > end:
            return position - end
        return 0

    best = min(numbers, key=lambda item: min(distance(item, pos) for pos in positions))
    return best[2]


_CODE6_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
_UNSUPPORTED_TIME_RE = re.compile(
    r"(내일|모레|다음\s*주|다음장|장\s*시작|장\s*마감|오전\s*\d|오후\s*\d|\d{1,2}\s*시\s*(?:이후|부터|전|까지))"
)
_UNSUPPORTED_STOP_RE = re.compile(r"(손절|손절가|스탑|stop\s*loss)", re.IGNORECASE)
_UNSUPPORTED_SIZE_RE = re.compile(
    r"((?:투입|예산|자금|금액|한도).{0,12}\d[\d,]*(?:만|천)?\s*원|\d+\s*주(?:만|를|씩|매수)?|반만|절반|분할\s*매수|[123]\s*차\s*매수)"
)
_APPROXIMATE_RE = re.compile(
    r"(근처|부근|쯤|정도|대략|반토막|\d[\d,.]*\s*(?:만\s*원|원|만)?대(?:에서|쯤|로)?|[~∼]|\d[\d,]*\s*부터\s*\d[\d,]*\s*까지|\d[\d,]*\s*사이)"
)
_IMMEDIATE_RE = re.compile(r"(?:지금|즉시|바로).{0,20}(?:사자|매수|사고|살래)")


def _merged_known_names(known_names: dict[str, str] | None) -> dict[str, str]:
    merged = dict(_V7555_NAME_HINT)
    merged.update({str(k): str(v) for k, v in (known_names or {}).items() if k and v})
    return merged


def _named_mentions(text: str, known_names: dict[str, str]) -> list[tuple[int, int, str, str]]:
    """긴 종목명을 우선해 겹치지 않는 명시 종목명을 찾는다."""
    candidates: list[tuple[int, int, str, str]] = []
    for name in sorted(known_names, key=len, reverse=True):
        if not name:
            continue
        for match in re.finditer(re.escape(name), text):
            candidates.append((match.start(), match.end(), name, known_names[name]))
    candidates.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    chosen: list[tuple[int, int, str, str]] = []
    for item in candidates:
        if any(not (item[1] <= old[0] or item[0] >= old[1]) for old in chosen):
            continue
        chosen.append(item)
    return sorted(chosen)


def _code_is_entity(
    text: str,
    start: int,
    end: int,
    code: str,
    *,
    named_codes: set[str],
    valid_codes: set[str],
    selected_symbol: str,
) -> bool:
    before = text[max(0, start - 12):start]
    if re.search(r"(?:종목\s*코드|코드)\s*$", before):
        return True
    if code in named_codes:
        return True
    # 종목명이 이미 적혀 있으면 다른 6자리 숫자는 가격으로 본다.
    if named_codes:
        return False
    stripped_start = len(text) - len(text.lstrip())
    if start == stripped_start and (code in valid_codes or code == selected_symbol):
        return True
    return False


def _append_unique(items: list[str], message: str) -> None:
    if message and message not in items:
        items.append(message)


def _unknown_entity_tokens(text: str, known_names: dict[str, str]) -> list[str]:
    """선택 문맥에 잘못 붙을 수 있는 미확정 종목명 같은 토큰을 찾는다."""
    scrub = text
    for name in sorted(known_names, key=len, reverse=True):
        scrub = scrub.replace(name, " ")
    scrub = _NUMBER_PATTERN.sub(" ", scrub)
    scrub = re.sub(
        r"(가격|현재가|목표가?|매도가?|매수기준가|이하|이상|아래|위로|밑|돌파|넘으면|"
        r"내려오면|올라가면|빠지면|떨어지면|오면|사자|사고|사면|살래|매수|팔자|팔고|"
        r"팔면|매도|익절|관심|지켜|눈여겨|감시|보기만|관찰만|하지|말고|마|금지|해제|"
        r"허용|취소|지정|조건|변경|바꿔|오늘|내일|모레|다음|오전|오후|이후|부터|전|까지|시|장기|대기|원|주)",
        " ",
        scrub,
    )
    tokens = re.findall(r"[가-힣A-Za-z]{2,20}", scrub)
    allowed = {"PAPER", "paper"}
    return [token for token in tokens if token not in allowed]


def compile_collaboration_input(
    text: str,
    *,
    selected_symbol: str = "",
    selected_name: str = "",
    known_names: dict[str, str] | None = None,
) -> CollaborationDraft:
    """자유문장을 안전한 한 장의 협업 카드로 해석한다.

    한 번에 한 종목·한 행동만 허용한다. 지원하지 않는 시간·손절·투입금·범위
    표현은 조용히 버리지 않고 명시적으로 적용을 막는다. 6자리 가격은 종목코드로
    오인하지 않으며, 현재가와 큰 차이는 경고 후 GUI에서 재확인한다.
    """
    source = str(text or "").strip()
    raw = " ".join(source.split())
    draft = CollaborationDraft(action="MEMO", note=raw, source_text=raw)
    if not raw:
        draft.errors.append("의견이나 명령을 입력해주세요.")
        return draft

    nonempty_lines = [line.strip() for line in source.splitlines() if line.strip()]
    if len(nonempty_lines) > 1:
        draft.errors.append("한 번에 한 줄만 적용합니다. 종목이나 명령별로 한 줄씩 입력해주세요.")
        return draft

    known = _merged_known_names(known_names)
    valid_codes = {code for code in known.values() if re.fullmatch(r"\d{6}", code)}
    if re.fullmatch(r"\d{6}", selected_symbol):
        valid_codes.add(selected_symbol)

    named = _named_mentions(raw, known)
    named_codes = {code for _, _, _, code in named if code}
    explicit: list[tuple[int, int, str, str]] = list(named)
    code_spans: list[tuple[int, int]] = []
    for match in _CODE6_RE.finditer(raw):
        code = match.group(1)
        if _code_is_entity(
            raw,
            match.start(),
            match.end(),
            code,
            named_codes=named_codes,
            valid_codes=valid_codes,
            selected_symbol=selected_symbol,
        ):
            name = next((n for n, sym in known.items() if sym == code), code)
            explicit.append((match.start(), match.end(), name, code))
            code_spans.append((match.start(), match.end()))

    unique_entities: list[tuple[str, str]] = []
    for _, _, name, code in sorted(explicit):
        if code and (name, code) not in unique_entities:
            unique_entities.append((name, code))
    unique_codes = {code for _, code in unique_entities if code}
    if len(unique_codes) > 1:
        draft.errors.append("두 종목 이상의 지시가 섞여 있습니다. 종목별로 한 줄씩 나눠주세요.")
        return draft

    draft.explicit_symbol = bool(unique_entities)
    if unique_entities:
        draft.name, draft.symbol = unique_entities[0]
    else:
        draft.name, draft.symbol = selected_name, selected_symbol

    negated_watch = bool(
        re.search(r"(?:매수|사지|사지는|사지는\s*것을).{0,12}(?:하지\s*말|하지마|말고|않고)", raw)
        and re.search(r"(보기만|관찰만|지켜|감시만|관심만)", raw)
    )
    if negated_watch:
        draft.action = "WATCH"
    elif re.search(r"(취소|지정\s*해제|조건\s*해제)", raw):
        draft.action = "CANCEL"
    elif re.search(r"(금지\s*해제|매수\s*허용)", raw):
        draft.action = "UNBLOCK_BUY"
    elif re.search(r"(사지\s*마|매수\s*금지|신규매수\s*금지)", raw):
        draft.action = "BLOCK_BUY"
    elif re.search(r"(관심|지켜|눈여겨|감시|보기만|관찰만)", raw) and not re.search(r"(사자|매수|팔자|목표)", raw):
        draft.action = "WATCH"
    elif re.search(r"(목표|매도가).*(변경|바꿔)|목표\s*가격", raw):
        draft.action = "SET_TARGET"
    elif re.search(r"(사자|매수|사고|살래|사면)", raw):
        draft.action = "BUY_CONDITION"
    else:
        draft.action = "MEMO"

    if draft.action == "MEMO" and not draft.explicit_symbol:
        draft.symbol = ""
        draft.name = ""
    if draft.action != "MEMO" and not re.fullmatch(r"\d{6}", draft.symbol):
        draft.errors.append("종목을 선택하거나 정확한 종목명·코드 6자리를 입력해주세요.")

    if draft.action != "MEMO" and not draft.explicit_symbol:
        unknown_tokens = _unknown_entity_tokens(raw, known)
        if unknown_tokens:
            token = unknown_tokens[0]
            candidates = difflib.get_close_matches(token, list(known), n=3, cutoff=0.45)
            hint = f" 후보: {', '.join(candidates)}" if candidates else ""
            draft.errors.append(
                f"'{token}' 항목이 다른 종목명처럼 보입니다. 선택 종목에 자동으로 붙이지 않았습니다.{hint}"
            )

    numbers = [
        item for item in _numbers_with_positions(raw)
        if not any(item[0] == start and item[1] == end for start, end in code_spans)
    ]

    if draft.action == "BUY_CONDITION":
        if _IMMEDIATE_RE.search(raw):
            _append_unique(draft.errors, "즉시매수 표현은 자동 적용하지 않습니다. 가격조건으로 다시 입력해주세요.")
        if _UNSUPPORTED_TIME_RE.search(raw):
            _append_unique(draft.errors, "시간·날짜 예약조건은 아직 적용하지 않습니다. 해당 시점에 다시 입력해주세요.")
        if _UNSUPPORTED_STOP_RE.search(raw):
            _append_unique(draft.errors, "손절 조건은 현재 협업 입력에서 지원하지 않습니다. 기존 엔진 손절 규칙을 사용합니다.")
        if _UNSUPPORTED_SIZE_RE.search(raw):
            _append_unique(draft.errors, "투입금·수량·분할매수 지시는 현재 지원하지 않습니다. 기존 엔진 한도를 사용합니다.")
        if _APPROXIMATE_RE.search(raw):
            _append_unique(draft.errors, "범위·근처·대략 가격은 적용하지 않습니다. 정확한 한 가격을 입력해주세요.")

        draft.buy_price = _nearest_number(raw, ("사자", "매수", "사고", "살래", "사면", "오면"), numbers)
        draft.target_price = _nearest_number(raw, ("목표", "팔자", "팔고", "팔면", "매도", "익절"), numbers)
        if draft.buy_price is None and numbers:
            draft.buy_price = numbers[0][2]
        if draft.target_price == draft.buy_price and len(numbers) >= 2:
            draft.target_price = numbers[-1][2]

        upward = bool(re.search(r"(돌파|이상|위로|넘으면|올라가면)", raw))
        downward = bool(re.search(r"(아래|이하|밑|내려오면|빠지면|떨어지면)", raw))
        if upward and downward:
            _append_unique(draft.errors, "상향 돌파와 이하 도달이 함께 적혀 있습니다. 방향을 하나만 선택해주세요.")
        elif upward:
            draft.trigger = "AT_OR_ABOVE"
        elif downward:
            draft.trigger = "AT_OR_BELOW"
        else:
            draft.errors.append("가격 이하 도달인지 상향 돌파인지 정해주세요.")
        if draft.buy_price is None:
            draft.errors.append("매수기준가를 읽지 못했습니다.")
        if draft.buy_price and draft.target_price and draft.target_price <= draft.buy_price:
            draft.errors.append("목표가는 매수기준가보다 높아야 합니다.")

        current = _finite_or_none(_V7555_PRICE_HINT.get(draft.symbol))
        if current is not None and draft.buy_price is not None:
            ratio = draft.buy_price / current
            if ratio >= OUTLIER_RATIO_HIGH or ratio <= OUTLIER_RATIO_LOW:
                draft.warnings.append(
                    f"입력가 {draft.buy_price:,.0f}원은 현재가 {current:,.0f}원의 {ratio:.1f}배입니다. 등록 전에 다시 확인합니다."
                )
        if "아니" in raw and len(numbers) >= 2:
            draft.warnings.append("정정 표현이 있어 행동 단어에 가장 가까운 최신 가격을 사용했습니다.")

    elif draft.action == "SET_TARGET":
        if _APPROXIMATE_RE.search(raw):
            _append_unique(draft.errors, "목표가는 정확한 한 가격으로 입력해주세요.")
        draft.target_price = _nearest_number(raw, ("목표", "매도가", "바꿔", "변경"), numbers)
        if draft.target_price is None and numbers:
            draft.target_price = numbers[-1][2]
        if draft.target_price is None:
            draft.errors.append("목표가격을 읽지 못했습니다.")

    draft.errors = list(dict.fromkeys(draft.errors))
    draft.warnings = list(dict.fromkeys(draft.warnings))
    return draft


def trigger_reached(trigger: str, current_price: float, reference_price: float) -> bool:
    if trigger == "AT_OR_ABOVE":
        return current_price >= reference_price
    return current_price <= reference_price


# -------- 기존 엔진에 협업 Overlay를 얇게 연결 --------
_ORIGINAL_ENGINE_INIT = v7544.V7544Engine.__init__
_ORIGINAL_PROCESS_COMMAND_FILE = v7544.V7544Engine._process_command_file
_ORIGINAL_GUI_SNAPSHOT = v7544.V7544Engine._gui_snapshot
_ORIGINAL_FRESH_ENRICHED_QUOTE = v7544.V7544Engine._fresh_enriched_quote


def _validate_execution_capsule_quote(self: Any, symbol: str, quote: Any) -> None:
    """실제 주문 함수가 사용할 마지막 호가에 수동 조건을 다시 적용한다.

    _manual_buy 내부에서 시세를 한 번 더 조회하므로, 그 최종 호가가 조건에서
    벗어났거나 지시가 취소·만료되었으면 broker.buy 전에 예외로 중단한다.
    """
    capsule = getattr(self, "_v7555_execution_capsule", None)
    if not isinstance(capsule, dict) or str(capsule.get("symbol", "")) != symbol:
        return
    overlay_id = str(capsule.get("overlay_id", ""))
    expected_version = int(capsule.get("version", 0) or 0)
    current_overlay = HUB.get(overlay_id)
    if (
        current_overlay is None
        or current_overlay.status != "EXECUTING"
        or current_overlay.version != expected_version
    ):
        raise ValueError("주문 직전 협업조건이 변경·취소되었습니다")
    if _expired(current_overlay.expires_at):
        raise ValueError("주문 직전 협업조건 유효기간이 끝났습니다")
    exact_price = _finite_or_none(getattr(quote, "price", None))
    if exact_price is None or not trigger_reached(
        current_overlay.trigger, exact_price, float(current_overlay.buy_price or 0.0)
    ):
        raise ValueError("주문에 사용할 최종 시세가 지정 가격조건에서 벗어났습니다")


def _fresh_enriched_quote_with_capsule(self: Any, symbol: str, *, side: str) -> Any:
    quote = _ORIGINAL_FRESH_ENRICHED_QUOTE(self, symbol, side=side)
    if side == "buy":
        _validate_execution_capsule_quote(self, symbol, quote)
    return quote


def _engine_init(self: Any, *args: Any, **kwargs: Any) -> None:
    _ORIGINAL_ENGINE_INIT(self, *args, **kwargs)
    self._v7552_processed_command_ids = set()
    for overlay in HUB.active_overlays():
        if overlay.symbol:
            try:
                self._manual_add_watch(overlay.symbol, overlay.name)
            except Exception as exc:
                self.logger.warning("협업조건 관심복원 실패 %s: %s", overlay.symbol, exc)


def _apply_collaboration_command(self: Any, command: dict[str, Any]) -> str:
    command_id = str(command.get("command_id", ""))
    if command_id and command_id in self._v7552_processed_command_ids:
        return "이미 처리한 중복 명령을 무시했습니다."
    if command_id:
        self._v7552_processed_command_ids.add(command_id)
    kind = str(command.get("kind", "MEMO"))
    symbol = str(command.get("symbol", ""))
    name = str(command.get("name", ""))
    if kind == "MEMO":
        HUB.add_memo(str(command.get("note", "")))
        return "의견을 저장했습니다."
    if not re.fullmatch(r"\d{6}", symbol):
        raise ValueError("종목코드 6자리가 필요합니다")
    if kind == "WATCH":
        self._manual_add_watch(symbol, name)
        return f"{name or symbol} 관심등록을 적용했습니다."
    elif kind == "BLOCK_BUY":
        self.buy_blocked.add(symbol)
        self._save_command_state()
        return f"{name or symbol} 신규매수를 금지했습니다."
    elif kind == "UNBLOCK_BUY":
        self.buy_blocked.discard(symbol)
        self._save_command_state()
        return f"{name or symbol} 매수금지를 해제했습니다."
    elif kind == "CANCEL":
        count = HUB.cancel_symbol(symbol)
        return f"{name or symbol} 활성 가격조건 {count}건을 해제했습니다."
    elif kind == "SET_TARGET":
        target = _finite_or_none(command.get("target_price"))
        if target is None:
            raise ValueError("목표가격 오류")
        self._set_target_price(symbol, target)
        return f"{name or symbol} 목표가를 {target:,.0f}원으로 적용했습니다."
    elif kind == "BUY_CONDITION":
        buy_price = _finite_or_none(command.get("buy_price"))
        target_price = _finite_or_none(command.get("target_price"))
        trigger = str(command.get("trigger", ""))
        expires_at = str(command.get("expires_at", ""))
        if buy_price is None:
            raise ValueError("매수기준가 오류")
        if trigger not in {"AT_OR_BELOW", "AT_OR_ABOVE"}:
            raise ValueError("가격조건 방향 오류")
        if target_price is not None and target_price <= buy_price:
            raise ValueError("목표가는 매수기준가보다 높아야 합니다")
        if not expires_at or _expired(expires_at):
            raise ValueError("유효기간이 없거나 이미 지났습니다")

        quote = getattr(self, "latest_quotes", {}).get(symbol)
        current = _finite_or_none(getattr(quote, "price", None)) if quote is not None else None
        if current is None:
            raise ValueError("현재가를 확인할 수 없어 가격조건을 등록하지 않았습니다")
        ratio = buy_price / current
        if ratio >= OUTLIER_RATIO_HIGH or ratio <= OUTLIER_RATIO_LOW:
            if not bool(command.get("price_confirmed")):
                raise ValueError("현재가와 차이가 큰 가격은 사용자 재확인이 필요합니다")
            confirmed_market = _finite_or_none(command.get("confirmed_market_price"))
            if confirmed_market is None or abs(current / confirmed_market - 1.0) > 0.10:
                raise ValueError("가격 확인 뒤 현재가가 크게 변했습니다. 다시 확인해주세요")

        active = HUB.active_for_symbol(symbol, kind="BUY_CONDITION")
        if active:
            if not bool(command.get("replace_existing")):
                raise ValueError("이미 활성 가격조건이 있습니다. 교체 여부를 먼저 확인해주세요")
            HUB.cancel_symbol(symbol)
        self._manual_add_watch(symbol, name)
        HUB.activate(command)
        return f"{name or symbol} PAPER 가격조건을 활성화했습니다."
    else:
        raise ValueError(f"지원하지 않는 협업명령: {kind}")


def _evaluate_collaboration_overlays(self: Any) -> None:
    now_epoch = time.time()
    for overlay in HUB.active_overlays():
        if overlay.kind != "BUY_CONDITION" or overlay.buy_price is None:
            continue
        if overlay.cooldown_until > now_epoch:
            continue
        if overlay.symbol in self.broker.positions:
            HUB.update(
                overlay.overlay_id,
                expected_version=overlay.version,
                status="BLOCKED",
                last_error="이미 보유 중이어서 조건 종료",
            )
            continue
        quote = self.latest_quotes.get(overlay.symbol)
        if quote is None:
            continue
        current = float(getattr(quote, "price", 0.0) or 0.0)
        if current <= 0 or not trigger_reached(overlay.trigger, current, float(overlay.buy_price)):
            continue
        latest = HUB.get(overlay.overlay_id)
        if latest is None or latest.status != "ACTIVE" or latest.version != overlay.version:
            continue
        try:
            fresh_quote = self._fresh_enriched_quote(overlay.symbol, side="buy") if hasattr(self, "_fresh_enriched_quote") else quote
            fresh_price = float(getattr(fresh_quote, "price", 0.0) or 0.0)
            if fresh_price <= 0 or not trigger_reached(overlay.trigger, fresh_price, float(overlay.buy_price)):
                HUB.update(
                    overlay.overlay_id,
                    expected_version=overlay.version,
                    last_error="최신 시세에서 가격조건 이탈—계속 대기",
                    cooldown_until=now_epoch + 1.0,
                )
                continue
            refreshed = HUB.get(overlay.overlay_id)
            if (
                refreshed is None
                or refreshed.status != "ACTIVE"
                or refreshed.version != overlay.version
            ):
                continue
            if _expired(refreshed.expires_at):
                HUB.update(
                    refreshed.overlay_id,
                    expected_version=refreshed.version,
                    status="EXPIRED",
                    last_error="주문 직전 유효기간 종료",
                )
                continue
            # ACTIVE→EXECUTING을 원자적으로 선점한다. 이 순간 이후의 취소 이벤트가
            # 이미 송신 중인 주문을 CANCELLED로 덮어쓰는 경쟁조건을 막는다.
            claimed_ok = HUB.update(
                refreshed.overlay_id,
                expected_version=refreshed.version,
                status="EXECUTING",
                last_error="주문 직전 실행 잠금",
            )
            if not claimed_ok:
                continue
            claimed = HUB.get(refreshed.overlay_id)
            if claimed is None or claimed.status != "EXECUTING":
                continue

            self._v7555_execution_capsule = {
                "overlay_id": claimed.overlay_id,
                "version": claimed.version,
                "symbol": claimed.symbol,
            }
            try:
                result = self._manual_buy(claimed.symbol, 1)
            finally:
                self._v7555_execution_capsule = None

            final_message = str(result)
            if claimed.target_price is not None and claimed.symbol in self.broker.positions:
                try:
                    self._set_target_price(claimed.symbol, float(claimed.target_price))
                except Exception as target_exc:
                    final_message += f" | 목표가 적용 실패: {type(target_exc).__name__}: {target_exc}"
                    self.logger.warning("협업조건 매수 후 목표가 적용 실패 %s | %s", self._label(claimed.symbol), target_exc)
            HUB.update(
                claimed.overlay_id,
                expected_version=claimed.version,
                status="EXECUTED",
                last_error=final_message,
            )
            self.logger.warning("협업조건 실행 %s | %s", self._label(claimed.symbol), final_message)
        except Exception as exc:
            current_overlay = HUB.get(overlay.overlay_id)
            if current_overlay is not None and current_overlay.status in {"ACTIVE", "EXECUTING"}:
                if _expired(current_overlay.expires_at):
                    HUB.update(
                        current_overlay.overlay_id,
                        expected_version=current_overlay.version,
                        status="EXPIRED",
                        last_error=f"주문 실패 후 유효기간 종료: {type(exc).__name__}: {exc}",
                    )
                else:
                    HUB.update(
                        current_overlay.overlay_id,
                        expected_version=current_overlay.version,
                        status="ACTIVE",
                        last_error=f"{type(exc).__name__}: {exc}",
                        cooldown_until=now_epoch + 5.0,
                    )
            self.logger.warning("협업조건 대기 %s | %s", self._label(overlay.symbol), exc)


def _process_command_file(self: Any) -> None:
    _ORIGINAL_PROCESS_COMMAND_FILE(self)
    for command in HUB.drain():
        command_id = str(command.get("command_id", ""))
        kind = str(command.get("kind", ""))
        try:
            message = _apply_collaboration_command(self, command)
            HUB.report_result(command_id, ok=True, message=message, kind=kind)
            self.logger.warning("협업명령 적용 | %s | %s", kind, message)
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            HUB.report_result(command_id, ok=False, message=message, kind=kind)
            self.logger.warning("협업명령 오류 | %s | %s", command, exc)
    _evaluate_collaboration_overlays(self)


def _gui_snapshot(self: Any) -> dict[str, Any]:
    snapshot = _ORIGINAL_GUI_SNAPSHOT(self)

    # 엔진이 이미 알고 있는 전체 종목명·현재가를 파서의 보조 사전으로 전달한다.
    names = getattr(self, "names", {})
    if isinstance(names, dict):
        for symbol, name in names.items():
            if re.fullmatch(r"\d{6}", str(symbol)) and str(name):
                _V7555_NAME_HINT[str(name)] = str(symbol)
    quotes = getattr(self, "latest_quotes", {})
    if isinstance(quotes, dict):
        for symbol, quote in quotes.items():
            price = _finite_or_none(getattr(quote, "price", None))
            if re.fullmatch(r"\d{6}", str(symbol)) and price is not None:
                _V7555_PRICE_HINT[str(symbol)] = price

    collab = HUB.snapshot()
    active_by_symbol: dict[str, list[dict[str, Any]]] = {}
    for item in collab["active"]:
        active_by_symbol.setdefault(str(item.get("symbol", "")), []).append(item)
    rows = list(snapshot.get("rows", []))
    for row in rows:
        symbol = str(row.get("symbol", ""))
        name = str(row.get("name", ""))
        if name and re.fullmatch(r"\d{6}", symbol):
            _V7555_NAME_HINT[name] = symbol
        overlays = active_by_symbol.get(symbol, [])
        if not overlays:
            continue
        texts: list[str] = []
        for item in overlays:
            if item.get("kind") == "BUY_CONDITION":
                direction = "이하" if item.get("trigger") == "AT_OR_BELOW" else "돌파"
                expiry_text = ""
                try:
                    expiry_text = datetime.fromisoformat(str(item.get("expires_at", ""))).astimezone(KST).strftime(" %m/%d %H:%M까지")
                except (TypeError, ValueError):
                    pass
                texts.append(f"형지정 {float(item.get('buy_price') or 0):,.0f}원 {direction}{expiry_text}")
        if texts:
            row["reason"] = f"{' · '.join(texts)} · {row.get('reason', '')}"
    snapshot["rows"] = rows
    snapshot["collaboration"] = collab
    return snapshot


v7544.V7544Engine.__init__ = _engine_init
v7544.V7544Engine._process_command_file = _process_command_file
v7544.V7544Engine._gui_snapshot = _gui_snapshot
v7544.V7544Engine._fresh_enriched_quote = _fresh_enriched_quote_with_capsule


class V7556OfficeFullCategoryGui(v7546.V7546Gui):
    """기존 영웅문 연동 목록 아래에 협업 입력판만 추가한 GUI."""

    def _run(self) -> None:  # noqa: C901 - 기존 검증 GUI 흐름을 의도적으로 보존
        try:
            import tkinter as tk
            from tkinter import messagebox, ttk
            root = tk.Tk()
        except Exception as exc:
            self.logger.warning("GUI 시작 실패: %s", exc)
            return
        import queue as queue_module

        click_mode = {"single": v7538._single_click_enabled()}

        def mode_label() -> str:
            return "한 번 클릭" if click_mode["single"] else "더블클릭"

        root.title(f"V75.56 전체 카테고리 검토·협업 — {mode_label()} 영웅문 연동")
        root.geometry("1680x1020")
        root.minsize(1180, 720)
        if sys.platform == "win32":
            try:
                root.state("zoomed")
            except Exception:
                pass
        hero = AutoDiscoverHeroLink(v7525.HERO_LINK_FILE)
        self.root = root
        self.hero = hero

        toolbar = ttk.Frame(root, padding=8)
        toolbar.pack(fill="x")
        status_var = tk.StringVar(value="기존 차트연동·화살표 검토 유지 · 하단에서 형의 의견과 가격조건을 입력합니다.")
        time_var = tk.StringVar(value="갱신 전")
        ttk.Button(toolbar, text="자동연동 확인", command=lambda: check_auto_link()).pack(side="left")
        ttk.Button(toolbar, text="수동 위치 저장(예비)", command=lambda: begin_capture()).pack(side="left", padx=(6, 0))
        ttk.Button(toolbar, text="예비 위치 초기화", command=lambda: do_reset()).pack(side="left", padx=(6, 0))
        single_var = tk.BooleanVar(value=click_mode["single"])
        ttk.Checkbutton(toolbar, text="한 번 클릭 사용", variable=single_var, command=lambda: change_click_mode()).pack(side="left", padx=(10, 0))
        mode_help_var = tk.StringVar(value=f"  {mode_label()} 또는 ↑/↓ → 차트 변경 후 목록 복귀")
        ttk.Label(toolbar, textvariable=mode_help_var).pack(side="left")
        active_var = tk.StringVar(value="형의 활성 지정 0건")
        ttk.Label(toolbar, textvariable=active_var).pack(side="right", padx=(12, 0))
        ttk.Label(toolbar, textvariable=time_var).pack(side="right")

        glossary = ttk.LabelFrame(root, text="고정 용어 안내", padding=(10, 5))
        glossary.pack(fill="x", padx=8, pady=(0, 4))
        glossary_text = (
            "관심종목=처음 감시  |  관찰후보=큰 매도 뒤 회복·거래감소 확인 중  |  매수가능성=주요 조건 상당수 충족  |  "
            "매수직전=마지막 가격·봉·시장 확인만 남음  |  시장보류=종목은 좋지만 시장 위험으로 대기\n"
            "매수종목=현재 보유 중  |  매도종목=오늘 청산된 종목  |  형지정=형이 직접 등록한 감시가격  |  "
            "매수기준가=진입 판단가격  |  평단=수수료를 포함한 실제 평균 매수가\n"
            "지지1·2·3=큰 매도 뒤 가격을 1·2·3번 지켜낸 상태  |  반전봉대기=작은 양봉·아래꼬리 기다림  |  "
            "지지확인봉대기=버티는 봉을 한 번 더 확인  |  큰매도배수=평소 대비 매도 거래량  |  앞선매수배수=직전 매수 거래량 강도  |  "
            "신규매수중지=새 매수만 멈추고 보유관리는 계속"
        )
        ttk.Label(glossary, text=glossary_text, justify="left", wraplength=1630).pack(fill="x")

        category_bar = ttk.LabelFrame(root, text="GUI 전체 카테고리 · 기본은 전체 연속검토", padding=(8, 5))
        category_bar.pack(fill="x", padx=8, pady=(0, 4))
        category_filter_var = tk.StringVar(value="전체")
        category_count_vars: dict[str, Any] = {}
        for stage in ("전체", *GUI_STAGE_ORDER):
            label_var = tk.StringVar(value=f"{stage} 0")
            category_count_vars[stage] = label_var
            ttk.Radiobutton(
                category_bar,
                textvariable=label_var,
                value=stage,
                variable=category_filter_var,
                command=lambda chosen=stage: choose_category(chosen),
            ).pack(side="left", padx=(0, 8))
        ttk.Label(category_bar, text="전체에서 ↑/↓를 누르면 7개 카테고리를 순서대로 계속 탐색합니다.", foreground="#53627a").pack(side="right")
        self.category_filter_var = category_filter_var
        self.category_count_vars = category_count_vars

        tree_frame = ttk.Frame(root)
        tree_frame.pack(fill="both", expand=True, padx=8, pady=4)
        columns = ("no", "stage", "stage_time", "name", "symbol", "price", "reason")
        tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="browse")
        self.tree = tree
        headings = {
            "no": "순번", "stage": "단계", "stage_time": "편입/매수시각", "name": "종목명",
            "symbol": "코드", "price": "현재가 / 매수기준가·평단", "reason": "조건·손익·보류이유",
        }
        widths = {"no": 45, "stage": 90, "stage_time": 90, "name": 135, "symbol": 68, "price": 230, "reason": 780}
        for column in columns:
            tree.heading(column, text=headings[column])
            anchor = "w" if column in ("name", "reason") else ("e" if column == "price" else "center")
            tree.column(column, width=widths[column], anchor=anchor, stretch=column == "reason")
        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        tree.pack(side="left", fill="both", expand=True)
        for stage, (background, foreground) in GUI_STAGE_COLORS.items():
            tree.tag_configure(stage, background=background, foreground=foreground)

        collab = ttk.LabelFrame(root, text="형의 의견·명령 입력", padding=8)
        collab.pack(fill="x", padx=8, pady=(2, 4))
        selected_var = tk.StringVar(value="선택종목: 없음")
        ttk.Label(collab, textvariable=selected_var, font=("맑은 고딕", 10, "bold")).grid(row=0, column=0, columnspan=4, sticky="w")
        input_text = tk.Text(collab, height=3, wrap="word")
        self.collab_input = input_text
        input_text.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(5, 5))
        ttk.Label(collab, text="예: 23,500원 이하 오면 사고 목표 24,200원 / 매수하지 말고 보기만 / 오늘 로봇 테마 수급이 강해 보여", foreground="#66738c").grid(row=8, column=0, columnspan=4, sticky="w", pady=(4, 0))

        action_var = tk.StringVar(value="해석 전")
        symbol_var = tk.StringVar(value="-")
        buy_var = tk.StringVar(value="")
        target_var = tk.StringVar(value="")
        trigger_var = tk.StringVar(value="가격 이하 도달")
        error_var = tk.StringVar(value="")
        warning_var = tk.StringVar(value="")
        ttk.Label(collab, text="해석").grid(row=2, column=0, sticky="w")
        ttk.Label(collab, textvariable=action_var).grid(row=2, column=1, sticky="w")
        ttk.Label(collab, text="종목").grid(row=2, column=2, sticky="e")
        ttk.Label(collab, textvariable=symbol_var).grid(row=2, column=3, sticky="w")
        ttk.Label(collab, text="매수기준가").grid(row=3, column=0, sticky="w")
        ttk.Entry(collab, textvariable=buy_var, width=16).grid(row=3, column=1, sticky="w")
        trigger_box = ttk.Combobox(collab, textvariable=trigger_var, values=("가격 이하 도달", "상향 돌파"), state="readonly", width=15)
        trigger_box.grid(row=3, column=2, sticky="e")
        ttk.Label(collab, text="원하는 가격조건").grid(row=3, column=3, sticky="w")
        ttk.Label(collab, text="목표가격").grid(row=4, column=0, sticky="w")
        ttk.Entry(collab, textvariable=target_var, width=16).grid(row=4, column=1, sticky="w")
        ttk.Label(collab, textvariable=error_var, foreground="#b00020").grid(row=4, column=2, columnspan=2, sticky="w")
        ttk.Label(collab, text="재확인").grid(row=5, column=0, sticky="w")
        ttk.Label(collab, textvariable=warning_var, foreground="#9a6700").grid(row=5, column=1, columnspan=3, sticky="w")

        ttk.Label(collab, text="유효기간").grid(row=6, column=0, sticky="w")
        ttk.Label(collab, text="오늘 장 종료까지 · 장 종료 후 등록 시 다음 장 종료까지", foreground="#66738c").grid(row=6, column=1, columnspan=3, sticky="w")

        button_row = ttk.Frame(collab)
        button_row.grid(row=7, column=0, columnspan=4, sticky="ew", pady=(6, 0))
        ttk.Button(button_row, text="뜻 확인", command=lambda: parse_input()).pack(side="left")
        ttk.Button(button_row, text="해석대로 적용", command=lambda: apply_interpreted()).pack(side="left", padx=(6, 0))
        ttk.Button(button_row, text="관심등록", command=lambda: submit_simple("WATCH")).pack(side="left", padx=(6, 0))
        ttk.Button(button_row, text="의견 저장", command=lambda: submit_simple("MEMO")).pack(side="left", padx=(6, 0))
        ttk.Button(button_row, text="PAPER 가격조건 등록", command=lambda: submit_buy()).pack(side="left", padx=(6, 0))
        ttk.Button(button_row, text="목표가 적용", command=lambda: submit_target()).pack(side="left", padx=(6, 0))
        ttk.Button(button_row, text="지정 해제", command=lambda: submit_simple("CANCEL")).pack(side="left", padx=(6, 0))
        ttk.Button(button_row, text="입력 지우기", command=lambda: clear_input()).pack(side="right")
        collab.columnconfigure(1, weight=1)
        collab.columnconfigure(3, weight=1)

        ttk.Label(root, textvariable=status_var, anchor="w", padding=8).pack(fill="x")

        state: dict[str, Any] = {
            "last_symbol": "", "last_ts": 0.0, "selected_symbol": "", "selected_name": "",
            "last_action_ts": 0.0, "focus_token": 0, "focus_restored_token": 0,
            "known_names": {}, "current_prices": {}, "current_draft": None,
            "draft_fingerprint": None, "trigger_touched": False,
            "all_rows": [], "last_snapshot": {},
        }

        def input_value() -> str:
            return input_text.get("1.0", "end").strip()

        def current_fingerprint() -> tuple[str, str]:
            return (" ".join(input_value().split()), str(state.get("selected_symbol", "")))

        def invalidate_draft(reason: str = "") -> None:
            state["current_draft"] = None
            state["draft_fingerprint"] = None
            if reason:
                status_var.set(f"{reason} — 뜻 확인을 다시 눌러주세요.")

        def ensure_current_draft() -> CollaborationDraft:
            draft = state.get("current_draft")
            if draft is None or state.get("draft_fingerprint") != current_fingerprint():
                return parse_input()
            return draft

        def on_input_modified(_event: Any = None) -> None:
            if not input_text.edit_modified():
                return
            input_text.edit_modified(False)
            invalidate_draft("입력 내용이 변경되었습니다")

        def tk_copy(symbol: str) -> bool:
            try:
                root.clipboard_clear(); root.clipboard_append(symbol); root.update()
                return str(root.clipboard_get()) == symbol
            except Exception as exc:
                diag(f"TK_CLIPBOARD_FAIL symbol={symbol} error={type(exc).__name__}")
                return False

        def restore_selected_row(symbol: str) -> None:
            iid = find_iid_by_symbol(tree, symbol)
            if iid:
                tree.selection_set(iid); tree.focus(iid); tree.see(iid)

        def schedule_review_restore(symbol: str, cursor: CursorPosition | None, token: int) -> None:
            def attempt(delay_ms: int) -> None:
                if token != int(state["focus_token"]) or int(state["focus_restored_token"]) == token:
                    return
                restore_selected_row(symbol)
                ok = restore_review_focus(root, tree, cursor)
                if ok:
                    state["focus_restored_token"] = token
            for delay_ms in v7547.FOCUS_RESTORE_DELAYS_MS:
                root.after(delay_ms, lambda delay=delay_ms: attempt(delay))

        def update_selected(symbol: str, name: str = "") -> None:
            changed = symbol != str(state.get("selected_symbol", ""))
            state["selected_symbol"] = symbol
            state["selected_name"] = name or next((n for n, c in state["known_names"].items() if c == symbol), symbol)
            selected_var.set(f"선택종목: {state['selected_name']} {symbol}" if symbol else "선택종목: 없음")
            if changed:
                invalidate_draft("선택 종목이 변경되었습니다")

        def switch_symbol(symbol: str, *, source: str) -> None:
            if not symbol.isdigit() or len(symbol) != 6:
                status_var.set(f"종목코드를 읽지 못했습니다: {symbol!r}"); return
            now_mono = time.monotonic()
            if click_mode["single"] and symbol == state["last_symbol"] and now_mono - float(state["last_ts"]) < self.DEBOUNCE_SEC:
                return
            state["focus_token"] = int(state["focus_token"]) + 1
            token = int(state["focus_token"]); cursor = capture_cursor_position()
            state["last_symbol"] = symbol; state["last_ts"] = now_mono; state["last_action_ts"] = now_mono
            update_selected(symbol)
            copied = tk_copy(symbol)
            status_var.set(f"{symbol} 영웅문 차트 전환 중…")
            root.update_idletasks()
            try:
                status, message = hero.send_symbol(symbol)
            except Exception as exc:
                status, message = LinkStatus.FAILED, f"오류: {type(exc).__name__}"
            finally:
                schedule_review_restore(symbol, cursor, token)
            prefix = {"VERIFIED": "✅ ", "SENT_UNVERIFIED": "⚠ ", "FAILED": "❌ "}.get(status.value, "")
            if status == LinkStatus.FAILED and copied:
                message += " (코드는 복사됨)"
            status_var.set(f"{prefix}{message} — 목록 포커스 복귀 중")

        def symbol_from_iid(iid: str) -> str:
            values = tree.item(iid, "values")
            return str(values[4]).strip() if len(values) >= 5 else ""

        def name_from_iid(iid: str) -> str:
            values = tree.item(iid, "values")
            return str(values[3]).strip() if len(values) >= 4 else ""

        def selected_symbol_event(event: Any) -> str | None:
            row_id = tree.identify_row(event.y)
            if not row_id or tree.identify_region(event.x, event.y) not in ("cell", "tree"):
                return None
            tree.selection_set(row_id); tree.focus(row_id); tree.see(row_id); tree.focus_set()
            update_selected(symbol_from_iid(row_id), name_from_iid(row_id))
            switch_symbol(symbol_from_iid(row_id), source=mode_label())
            return "break"

        def move_selection(delta: int) -> str:
            items = list(tree.get_children(""))
            if not items:
                return "break"
            current_iid = tree.focus() or (tree.selection()[0] if tree.selection() else "")
            current_index = items.index(current_iid) if current_iid in items else -1
            next_index = select_arrow_index(current_index, len(items), delta)
            if next_index < 0:
                return "break"
            next_iid = items[next_index]
            tree.selection_set(next_iid); tree.focus(next_iid); tree.see(next_iid); tree.focus_set()
            update_selected(symbol_from_iid(next_iid), name_from_iid(next_iid))
            switch_symbol(symbol_from_iid(next_iid), source="아래화살표" if delta > 0 else "위화살표" if delta < 0 else "Enter")
            return "break"

        def root_navigation(event: Any, delta: int) -> str | None:
            if event.widget is tree:
                return None
            widget_class = str(event.widget.winfo_class())
            if widget_class in {"Text", "Entry", "TEntry", "TCombobox", "Spinbox"}:
                return None
            return move_selection(delta)

        def apply_click_binding() -> None:
            tree.unbind("<ButtonRelease-1>"); tree.unbind("<Double-1>")
            tree.bind("<ButtonRelease-1>" if click_mode["single"] else "<Double-1>", selected_symbol_event)
            mode_help_var.set(f"  {mode_label()} 또는 ↑/↓ → 차트 변경 후 목록 복귀")

        def change_click_mode() -> None:
            click_mode["single"] = bool(single_var.get()); state["last_symbol"] = ""; state["last_ts"] = 0.0
            apply_click_binding(); tree.focus_set(); status_var.set(f"클릭 방식을 {mode_label()}으로 변경했습니다.")

        def check_auto_link() -> None:
            ok, message = hero.probe_auto_target(); state["last_action_ts"] = time.monotonic()
            status_var.set(("✅ " if ok else "❌ ") + message); tree.focus_set()

        def do_reset() -> None:
            hero.reset_config(); status_var.set("예비 수동 위치를 초기화했습니다."); tree.focus_set()

        def finish_capture() -> None:
            ok, message = hero.capture_cursor_target(); status_var.set(f"예비 위치 저장: {message}" if ok else f"예비 위치 저장 실패: {message}")

        def countdown(remaining: int) -> None:
            if remaining <= 0: finish_capture(); return
            status_var.set(f"{remaining}초 안에 영웅문 종목코드 입력칸을 클릭하세요.")
            root.after(1000, lambda: countdown(remaining - 1))

        def begin_capture() -> None:
            messagebox.showinfo("예비 수동 위치 저장", "자동탐색이 실패하는 PC에서만 사용합니다. 확인 후 5초 안에 영웅문 입력칸을 클릭하세요.")
            countdown(5)

        def parse_input() -> CollaborationDraft:
            text = input_value()
            draft = compile_collaboration_input(
                text,
                selected_symbol=str(state["selected_symbol"]),
                selected_name=str(state["selected_name"]),
                known_names=dict(state["known_names"]),
            )
            state["current_draft"] = draft
            state["draft_fingerprint"] = current_fingerprint()
            state["trigger_touched"] = False
            labels = {
                "MEMO": "시장·테마 의견", "WATCH": "관심등록", "BUY_CONDITION": "가격조건 매수",
                "SET_TARGET": "목표가 변경", "BLOCK_BUY": "매수금지",
                "UNBLOCK_BUY": "금지해제", "CANCEL": "지정해제",
            }
            action_var.set(labels.get(draft.action, draft.action))
            symbol_var.set(f"{draft.name} {draft.symbol}" if draft.symbol else "종목 없음")
            buy_var.set(f"{draft.buy_price:.0f}" if draft.buy_price else "")
            target_var.set(f"{draft.target_price:.0f}" if draft.target_price else "")
            trigger_var.set(_trigger_label(draft.trigger))
            error_var.set(" · ".join(draft.errors))
            warning_var.set(" · ".join(draft.warnings))
            if draft.errors:
                status_var.set("자동 적용하지 않습니다. 빨간 안내를 먼저 고쳐주세요.")
            elif draft.warnings:
                status_var.set("뜻은 읽었습니다. 노란 재확인 항목은 등록할 때 다시 묻습니다.")
            else:
                status_var.set("뜻을 확인했습니다.")
            return draft

        def _base_command(kind: str) -> dict[str, Any]:
            draft = ensure_current_draft()
            symbol = str(getattr(draft, "symbol", "") or state["selected_symbol"])
            name = str(getattr(draft, "name", "") or state["selected_name"])
            return {"kind": kind, "symbol": symbol, "name": name, "note": input_value()}

        def submit_simple(kind: str) -> None:
            command = _base_command(kind)
            if kind != "MEMO" and not re.fullmatch(r"\d{6}", command["symbol"]):
                error_var.set("종목을 먼저 선택해주세요.")
                return
            HUB.submit(command)
            status_var.set(f"협업명령 접수: {kind}")
            tree.focus_set()

        def apply_interpreted() -> None:
            draft = ensure_current_draft()
            if draft.errors and draft.action not in {"BUY_CONDITION", "SET_TARGET"}:
                error_var.set(" · ".join(draft.errors))
                return
            if draft.action == "BUY_CONDITION":
                submit_buy()
            elif draft.action == "SET_TARGET":
                submit_target()
            else:
                submit_simple(draft.action)

        def submit_buy() -> None:
            draft = ensure_current_draft()
            symbol = str(getattr(draft, "symbol", "") or state["selected_symbol"])
            name = str(getattr(draft, "name", "") or state["selected_name"])
            buy = _finite_or_none(buy_var.get().replace(",", ""))
            target = _finite_or_none(target_var.get().replace(",", ""))
            errors = list(getattr(draft, "errors", []))
            if buy is not None:
                errors = [e for e in errors if "매수기준가를 읽지 못했습니다" not in e]
            if bool(state.get("trigger_touched")):
                errors = [e for e in errors if "가격 이하 도달인지 상향 돌파인지" not in e]
            if not re.fullmatch(r"\d{6}", symbol):
                errors.append("종목을 선택해주세요.")
            if buy is None:
                errors.append("매수기준가를 입력해주세요.")
            if target is not None and buy is not None and target <= buy:
                errors.append("목표가는 매수가보다 높아야 합니다.")
            if errors:
                error_var.set(" · ".join(dict.fromkeys(errors)))
                return

            current = _finite_or_none(state.get("current_prices", {}).get(symbol))
            if current is None:
                current = _finite_or_none(_V7555_PRICE_HINT.get(symbol))
            if current is None:
                error_var.set("현재가를 확인할 수 없어 가격조건을 등록하지 않았습니다.")
                return

            price_confirmed = False
            ratio = buy / current
            if ratio >= OUTLIER_RATIO_HIGH or ratio <= OUTLIER_RATIO_LOW:
                price_confirmed = messagebox.askyesno(
                    "가격 재확인",
                    f"현재가 {current:,.0f}원과 매수기준가 {buy:,.0f}원의 차이가 큽니다.\n"
                    f"입력 가격은 현재가의 {ratio:.1f}배입니다. 오타가 아니고 이 가격이 맞습니까?",
                )
                if not price_confirmed:
                    error_var.set("매수기준가를 다시 확인해주세요.")
                    return

            active = HUB.active_for_symbol(symbol, kind="BUY_CONDITION")
            replace_existing = False
            if active:
                replace_existing = messagebox.askyesno(
                    "기존 가격조건 교체",
                    f"{name or symbol}에 이미 활성 가격조건 {len(active)}건이 있습니다.\n"
                    "기존 조건을 취소하고 새 조건으로 교체할까요?",
                )
                if not replace_existing:
                    error_var.set("기존 조건을 유지했습니다. 새 조건은 등록하지 않았습니다.")
                    return

            HUB.submit({
                "kind": "BUY_CONDITION",
                "symbol": symbol,
                "name": name,
                "trigger": _trigger_code(trigger_var.get()),
                "buy_price": buy,
                "target_price": target,
                "expires_at": _next_session_close_iso(),
                "note": input_value(),
                "price_confirmed": price_confirmed,
                "confirmed_market_price": current if price_confirmed else None,
                "replace_existing": replace_existing,
            })
            warning_var.set("")
            error_var.set("")
            status_var.set(f"{name or symbol} PAPER 가격조건을 접수했습니다. 주문 전 기존 엔진 안전검사를 다시 통과합니다.")
            tree.focus_set()

        def submit_target() -> None:
            draft = ensure_current_draft()
            command = _base_command("SET_TARGET")
            target = _finite_or_none(target_var.get().replace(",", ""))
            errors = [e for e in draft.errors if "목표가격을 읽지 못했습니다" not in e]
            if errors:
                error_var.set(" · ".join(errors))
                return
            if target is None:
                error_var.set("목표가격을 입력해주세요.")
                return
            command["target_price"] = target
            HUB.submit(command)
            status_var.set("목표가 변경을 접수했습니다.")
            tree.focus_set()

        def clear_input() -> None:
            input_text.delete("1.0", "end")
            action_var.set("해석 전")
            symbol_var.set("-")
            buy_var.set("")
            target_var.set("")
            error_var.set("")
            warning_var.set("")
            state["current_draft"] = None
            state["draft_fingerprint"] = None
            state["trigger_touched"] = False
            input_text.focus_set()

        def on_tree_selection(_event: Any = None) -> None:
            selected = tree.selection()
            if not selected:
                return
            iid = selected[0]
            update_selected(symbol_from_iid(iid), name_from_iid(iid))

        def render_rows() -> None:
            preserve_symbol = str(state.get("selected_symbol", ""))
            for iid in tree.get_children():
                tree.delete(iid)
            all_rows = list(state.get("all_rows", []))
            chosen = str(category_filter_var.get() or "전체")
            rows = all_rows if chosen == "전체" else [row for row in all_rows if str(row.get("stage", "")) == chosen]
            counters: dict[str, int] = {}
            symbol_to_iid: dict[str, str] = {}
            for row in rows:
                stage = str(row.get("stage", ""))
                counters[stage] = counters.get(stage, 0) + 1
                symbol = str(row.get("symbol", ""))
                iid = tree.insert(
                    "", "end",
                    values=(
                        counters[stage], stage, row.get("stage_time", row.get("time", "-")),
                        row.get("name", ""), symbol,
                        row.get("display_price", row.get("price", "")), row.get("reason", ""),
                    ),
                    tags=(stage,),
                )
                if symbol:
                    symbol_to_iid.setdefault(symbol, iid)
            if preserve_symbol in symbol_to_iid:
                iid = symbol_to_iid[preserve_symbol]
                tree.selection_set(iid); tree.focus(iid); tree.see(iid)
                update_selected(preserve_symbol, name_from_iid(iid))
            elif tree.get_children():
                first = tree.get_children()[0]
                tree.selection_set(first); tree.focus(first); tree.see(first)
                update_selected(symbol_from_iid(first), name_from_iid(first))
            status_snapshot = dict(state.get("last_snapshot", {}))
            if time.monotonic() - float(state["last_action_ts"]) > 3.0:
                status_var.set(
                    f"시장 {status_snapshot.get('market', '-')} | {chosen} {len(rows)}종목 | "
                    "클릭·더블클릭·↑↓ 영웅문 연동 | 협업입력 정상"
                )

        def choose_category(stage: str) -> None:
            category_filter_var.set(stage)
            render_rows()
            tree.focus_set()

        self.choose_category = choose_category

        def apply_snapshot(snapshot: dict[str, Any]) -> None:
            raw_rows = list(snapshot.get("rows", []))
            rows, counts = prepare_gui_rows(raw_rows)
            known: dict[str, str] = {}
            prices: dict[str, float] = {}
            for row in rows:
                symbol = str(row.get("symbol", "")); name = str(row.get("name", ""))
                if name and re.fullmatch(r"\d{6}", symbol):
                    known[name] = symbol
                price_match = re.search(r"[\d,]+", str(row.get("price", "")))
                if price_match:
                    parsed_price = _finite_or_none(price_match.group(0).replace(",", ""))
                    if parsed_price is not None:
                        prices[symbol] = parsed_price
            state["known_names"] = {**_V7555_NAME_HINT, **known}
            state["current_prices"] = {**_V7555_PRICE_HINT, **prices}
            state["all_rows"] = rows
            state["last_snapshot"] = dict(snapshot)
            self.last_gui_rows = rows
            for stage in ("전체", *GUI_STAGE_ORDER):
                category_count_vars[stage].set(f"{stage} {counts.get(stage, 0)}")
            time_var.set(str(snapshot.get("updated", "")))
            collab_snapshot = snapshot.get("collaboration", HUB.snapshot())
            active_var.set(f"형의 활성 지정 {len(collab_snapshot.get('active', []))}건")
            render_rows()

        def poll() -> None:
            try:
                while True:
                    kind, payload = self.events.get_nowait()
                    if kind == "snapshot": apply_snapshot(payload)
                    elif kind == "close": root.destroy(); return
            except queue_module.Empty:
                pass
            for result in HUB.drain_results():
                if bool(result.get("ok")):
                    error_var.set("")
                    status_var.set(f"✅ {result.get('message', '협업명령을 적용했습니다.')}")
                else:
                    error_var.set(str(result.get("message", "협업명령 적용 실패")))
                    status_var.set("❌ 협업명령이 실제 엔진에 적용되지 않았습니다.")
            active_var.set(f"형의 활성 지정 {len(HUB.snapshot()['active'])}건")
            if not self.closed.is_set(): root.after(300, poll)

        apply_click_binding()
        input_text.edit_modified(False)
        input_text.bind("<<Modified>>", on_input_modified, add="+")
        trigger_box.bind("<<ComboboxSelected>>", lambda _event: state.__setitem__("trigger_touched", True), add="+")
        tree.bind("<Up>", lambda _event: move_selection(-1)); tree.bind("<Down>", lambda _event: move_selection(1)); tree.bind("<Return>", lambda _event: move_selection(0))
        tree.bind("<Button-1>", lambda _event: tree.focus_set(), add="+")
        tree.bind("<<TreeviewSelect>>", on_tree_selection, add="+")
        root.bind("<Up>", lambda event: root_navigation(event, -1), add="+"); root.bind("<Down>", lambda event: root_navigation(event, 1), add="+"); root.bind("<Return>", lambda event: root_navigation(event, 0), add="+")
        root.protocol("WM_DELETE_WINDOW", lambda: root.withdraw())
        root.after(200, poll); root.mainloop()


v7525.SimpleDoubleClickGui = V7556OfficeFullCategoryGui
v7525.VERSION = VERSION


def self_check() -> list[tuple[str, bool, str]]:
    """V75.56 변경부만 빠르게 검사한다. 전체 회귀는 별도 테스트 파일에서 실행한다."""
    old_names = dict(_V7555_NAME_HINT)
    old_prices = dict(_V7555_PRICE_HINT)
    try:
        _V7555_NAME_HINT.update({"에스피지": "058610", "삼성전자": "005930", "삼성SDI": "006400"})
        _V7555_PRICE_HINT.update({"058610": 24050.0, "005930": 71200.0, "006400": 377000.0})
        basic = compile_collaboration_input("23,500 아래 오면 사고 목표 24,200", selected_symbol="058610", selected_name="에스피지")
        six_price = compile_collaboration_input("삼성전자 235000 아래 사자")
        negated = compile_collaboration_input("매수하지 말고 보기만", selected_symbol="058610", selected_name="에스피지")
        timed = compile_collaboration_input("내일 23,500 이하 사자", selected_symbol="058610", selected_name="에스피지")
        typo = compile_collaboration_input("에스비지 23,500 이하 사자", selected_symbol="058610", selected_name="에스피지")
    finally:
        _V7555_NAME_HINT.clear(); _V7555_NAME_HINT.update(old_names)
        _V7555_PRICE_HINT.clear(); _V7555_PRICE_HINT.update(old_prices)

    source = Path(__file__).read_text(encoding="utf-8")
    return [
        ("V75.48기준SHA", sha256_file(BASE_DIR / V7548_FILENAME) == V7548_SHA256, V7548_SHA256[:16]),
        ("기존GUI상속", issubclass(V7556OfficeFullCategoryGui, v7546.V7546Gui), "영웅문·한번/더블클릭·↑↓·포커스복귀 유지"),
        ("GUI7개카테고리", GUI_STAGE_ORDER == ("관심종목", "관찰후보", "매수가능성", "매수직전", "시장보류", "매수종목", "매도종목"), str(GUI_STAGE_ORDER)),
        ("GUI전체기본", 'category_filter_var = tk.StringVar(value="전체")' in source and "prepare_gui_rows" in source, "전체 연속검토"),
        ("협업파서기본", basic.buy_price == 23500 and basic.target_price == 24200 and not basic.errors, str(basic)),
        ("6자리가격코드오인방지", six_price.action == "BUY_CONDITION" and six_price.symbol == "005930" and six_price.buy_price == 235000, str(six_price)),
        ("이상가격확인경고", bool(six_price.warnings) and not six_price.errors, str(six_price.warnings)),
        ("부정문보기만", negated.action == "WATCH" and not negated.errors, str(negated)),
        ("미지원시간차단", any("시간·날짜" in item for item in timed.errors), str(timed.errors)),
        ("문맥오타차단", any("자동으로 붙이지" in item for item in typo.errors), str(typo.errors)),
        ("오래된해석무효화코드", "draft_fingerprint" in source and "ensure_current_draft" in source and "<<Modified>>" in source, "입력·선택 변경 시 재해석"),
        ("기존연동바인딩유지", 'tree.bind("<Up>"' in source and 'tree.bind("<Down>"' in source and "hero.send_symbol(symbol)" in source, "한번/더블클릭·↑↓"),
        ("엔진직접호출분리", v7544.V7544Engine._process_command_file is _process_command_file, "Hub→엔진 안전검사"),
        ("PAPER전용", "PAPER" in VERSION, VERSION),
    ]


v7525.self_check = self_check


def main() -> int:
    return int(v7548.main())


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        rows = self_check()
        for name, ok, detail in rows:
            print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
        raise SystemExit(0 if all(ok for _, ok, _ in rows) else 1)
    raise SystemExit(main())
