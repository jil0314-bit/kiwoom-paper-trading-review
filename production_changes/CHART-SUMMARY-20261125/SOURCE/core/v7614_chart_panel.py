"""V7614 대시보드 차트 패널 — 키움 0600 화면 방식(읽기 전용, 주문·장부 변경 없음).

* 1분·3분·5분·일·주·월봉, 이동평균 5·10·20·30·60·120·200·360 (분봉도 동일)
* 거래량 막대는 그 봉의 가격봉과 같은 색(양봉 빨강·음봉 파랑)
* 일·주·월봉 아래에 외국인 순매수량(주)·기관 순매수금액(백만원) — ka10059 일별 자료
  (분봉에는 키움이 분 단위 수급을 주지 않으므로 수급칸을 숨긴다)
* 종목표 클릭·↑↓ 선택을 그대로 따라간다(0600 연동 경로는 기존 그대로, 이 패널은 화면만 그린다)
* 조회는 백그라운드 1개 작업자 · 마지막 요청만 처리 · 캐시 → 매매 엔진 조회 한도 영향 최소
"""
from __future__ import annotations

import json
import math
import queue
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

try:  # 시험 환경(리눅스)에서도 순수 계산 함수는 쓸 수 있게 한다.
    import tkinter as tk
    from tkinter import ttk
except Exception:  # pragma: no cover
    tk = None
    ttk = None

KST = timezone(timedelta(hours=9))

TIMEFRAMES = (("1분", "m1"), ("3분", "m3"), ("5분", "m5"), ("일", "D"), ("주", "W"), ("월", "M"))
MINUTE_SCOPE = {"m1": "1", "m3": "3", "m5": "5"}
PERIOD_API = {"D": ("ka10081", "stk_dt_pole_chart_qry"),
              "W": ("ka10082", "stk_stk_pole_chart_qry"),
              "M": ("ka10083", "stk_mth_pole_chart_qry")}
DEFAULT_TF = "m3"
MA_PERIODS = (5, 10, 20, 30, 60, 120, 200, 360)
MA_COLORS = {5: "#ff5fd2", 10: "#3fc3ff", 20: "#ffd34d", 30: "#5fe07a",
             60: "#ff8a4c", 120: "#b59cff", 200: "#e8e8e8", 360: "#c49a6c"}

BG = "#06121f"
PANEL_BG = "#081a2d"
GRID = "#12304d"
AXIS_TEXT = "#9fb6cc"
TEXT = "#e7f0f8"
UP = "#ff3b3b"
DOWN = "#2f86ff"
FLAT = "#c8d2dc"
CROSS = "#7f97ad"

CACHE_TTL = {"m1": 8.0, "m3": 12.0, "m5": 15.0, "D": 60.0, "W": 300.0, "M": 600.0}
INVESTOR_TTL = 300.0
AUTO_REFRESH_MS = {"m1": 10_000, "m3": 15_000, "m5": 20_000, "D": 60_000, "W": 300_000, "M": 600_000}
SLOT_MIN, SLOT_MAX, SLOT_DEFAULT = 3, 24, 7
# V7629 (2026-11-16 형 지시): 최근 N봉 구간 버튼. 고르면 화면 폭에 그 봉 수가 꼭 맞게 들어간다.
SPANS = (560, 480, 360, 240, 180, 120, 60)
SPAN_SLOT_MIN = 0.5
VIEW_STATE_FILE = Path(__file__).resolve().parent.parent / "runtime_v7592" / "v7614_chart_view.json"


def span_slot(plot_w: float, span: int) -> float:
    """구간 봉 수가 그림 폭에 꼭 맞는 봉 1개 폭(픽셀)."""
    return max(SPAN_SLOT_MIN, float(plot_w) / max(1, int(span)))


ZOOM_DRAG_MIN_PX = 8      # 이보다 짧은 드래그는 클릭으로 본다
ZOOM_MIN_BARS = 5


def zoom_window(left: int | None, right: int | None, total: int) -> tuple[int, int] | None:
    """드래그한 두 봉 위치 → (보여줄 봉 수, 오른쪽 끝에서 밀린 봉 수). 잘못된 입력이면 None."""
    if left is None or right is None or total <= 0:
        return None
    left, right = max(0, min(left, right)), min(total - 1, max(left, right))
    count = right - left + 1
    if count < ZOOM_MIN_BARS:       # 너무 좁게 잡으면 가운데를 기준으로 최소 봉 수만큼
        left = max(0, min(total - ZOOM_MIN_BARS, left - (ZOOM_MIN_BARS - count) // 2))
        right = min(total - 1, left + ZOOM_MIN_BARS - 1)
        count = right - left + 1
    return count, total - 1 - right


def load_span(path: Any = None) -> int | None:
    try:
        value = json.loads(Path(path or VIEW_STATE_FILE).read_text(encoding="utf-8")).get("span")
        return int(value) if value in SPANS else None
    except Exception:
        return None


def save_span(span: int | None, path: Any = None) -> None:
    try:
        target = Path(path or VIEW_STATE_FILE)
        if target.parent.is_dir():
            target.write_text(json.dumps({"span": span}), encoding="utf-8")
    except Exception:
        pass


# ------------------------------------------------------------------ 숫자·자료 변환(순수 함수)
def _num(value: Any, *, absolute: bool = False) -> float:
    text = str(value if value is not None else "").replace(",", "").strip()
    if text in {"", "+", "-"}:
        return 0.0
    number = float(text)
    return abs(number) if absolute else number


def parse_minute_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bars: dict[datetime, dict[str, Any]] = {}
    for row in rows or []:
        try:
            stamp = str(row.get("cntr_tm", "")).strip()
            moment = datetime.strptime(stamp[:12], "%Y%m%d%H%M").replace(tzinfo=KST)
            o, h, l, c = (_num(row.get(k), absolute=True) for k in ("open_pric", "high_pric", "low_pric", "cur_prc"))
            if min(o, h, l, c) <= 0:
                continue
            bars[moment] = {"t": moment, "o": o, "h": max(h, o, c), "l": min(l, o, c), "c": c,
                            "v": _num(row.get("trde_qty"), absolute=True)}
        except (TypeError, ValueError):
            continue
    return [bars[k] for k in sorted(bars)]


def parse_period_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bars: dict[date, dict[str, Any]] = {}
    for row in rows or []:
        try:
            day = datetime.strptime(str(row.get("dt", "")).strip()[:8], "%Y%m%d").date()
            o, h, l, c = (_num(row.get(k), absolute=True) for k in ("open_pric", "high_pric", "low_pric", "cur_prc"))
            if min(o, h, l, c) <= 0:
                continue
            bars[day] = {"t": datetime.combine(day, datetime.min.time(), tzinfo=KST), "o": o,
                         "h": max(h, o, c), "l": min(l, o, c), "c": c,
                         "v": _num(row.get("trde_qty"), absolute=True)}
        except (TypeError, ValueError):
            continue
    return [bars[k] for k in sorted(bars)]


def parse_investor_rows(rows: list[dict[str, Any]], key: str) -> dict[date, float]:
    result: dict[date, float] = {}
    for row in rows or []:
        try:
            day = datetime.strptime(str(row.get("dt", "")).strip()[:8], "%Y%m%d").date()
            result[day] = _num(row.get(key))
        except (TypeError, ValueError):
            continue
    return result


def moving_averages(closes: list[float], periods=MA_PERIODS) -> dict[int, list[float | None]]:
    output: dict[int, list[float | None]] = {}
    for period in periods:
        values: list[float | None] = []
        running = 0.0
        for index, close in enumerate(closes):
            running += close
            if index >= period:
                running -= closes[index - period]
            values.append(running / period if index >= period - 1 else None)
        output[period] = values
    return output


def period_key(moment: datetime, tf: str) -> Any:
    day = moment.date()
    if tf == "W":
        iso = day.isocalendar()
        return (iso[0], iso[1])
    if tf == "M":
        return (day.year, day.month)
    return day


def align_investor(bars: list[dict[str, Any]], daily: dict[date, float], tf: str) -> list[float | None]:
    """봉마다 해당 기간의 일별 순매수 합계. 자료가 없는 기간은 None(빈칸)."""
    if tf not in PERIOD_API or not daily:
        return [None] * len(bars)
    sums: dict[Any, float] = {}
    for day, value in daily.items():
        key = period_key(datetime.combine(day, datetime.min.time(), tzinfo=KST), tf)
        sums[key] = sums.get(key, 0.0) + float(value)
    first = min(daily)
    out: list[float | None] = []
    for bar in bars:
        key = period_key(bar["t"], tf)
        if tf == "D":
            out.append(daily.get(bar["t"].date()))
        elif key in sums and key != period_key(datetime.combine(first, datetime.min.time(), tzinfo=KST), tf):
            out.append(sums[key])   # 자료 시작 기간(일부 날짜만 있음)은 왜곡되므로 비운다
        else:
            out.append(None)
    return out


def won(value: float) -> str:
    return f"{value:,.0f}"


def signed(value: float, digits: int = 0) -> str:
    """부호를 크게 보이게: ▲ +1,234 / ▼ -1,234."""
    text = f"{abs(value):,.{digits}f}"
    if value > 0:
        return f"▲+{text}"
    if value < 0:
        return f"▼-{text}"
    return text


def format_market_summary(values: dict[str, Any], now: datetime) -> tuple[str, str]:
    """Today's cumulative volume versus the entire previous trading day, not same-time volume."""
    def number(key: str, minimum: float | None = None) -> float | None:
        try:
            value = float(values[key])
            return value if math.isfinite(value) and (minimum is None or value >= minimum) else None
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
    stamp = values.get("timestamp")
    if (not isinstance(stamp, datetime) or stamp.tzinfo is None
            or stamp.astimezone(KST).date() != now.astimezone(KST).date()
            or (now - stamp).total_seconds() < -5):
        return "전일比 미확인 · 거래량 미확인 · 전일전체 대비 미확인", AXIS_TEXT
    change, volume, previous = number("change_pct"), number("volume", 0), number("previous_volume", 0)
    change_text = f"{signed(change, 2)}%" if change is not None else "미확인"
    volume_text = f"{volume:,.0f}주" if volume is not None else "미확인"
    ratio = volume / previous * 100 if volume is not None and previous else None
    ratio_text = f"{ratio:,.1f}%" if ratio is not None else "미확인"
    delay = " · 시세지연" if (now - stamp).total_seconds() > 30 else ""
    color = UP if change is not None and change > 0 else DOWN if change is not None and change < 0 else FLAT
    return (f"전일比 {change_text} · 거래량 {volume_text} · 전일전체 대비 {ratio_text}"
            f" · {stamp.astimezone(KST):%H:%M:%S}{delay}"), color


def nice_step(span: float, count: int) -> float:
    if span <= 0 or count <= 0:
        return 1.0
    raw = span / count
    power = 10 ** math.floor(math.log10(raw))
    for factor in (1, 2, 2.5, 5, 10):
        if raw <= factor * power:
            return factor * power
    return 10 * power


# ------------------------------------------------------------------ 조회(백그라운드)
class ChartFetcher:
    """엔진의 키움 클라이언트(조회 한도 공유)를 빌려 차트 자료만 조회한다."""

    def __init__(self, client_getter: Callable[[], Any], symbol_mapper: Callable[[str, Any], str] | None = None):
        self.client_getter = client_getter
        self.symbol_mapper = symbol_mapper
        self.cache: dict[tuple[str, str], tuple[float, Any]] = {}

    def _client(self) -> Any:
        client = self.client_getter()
        if client is None:
            raise RuntimeError("엔진 연결 대기 중")
        return client

    def _request(self, client: Any, api_id: str, endpoint: str, body: dict[str, Any], required: str,
                 cont_key: str = "") -> tuple[Any, str]:
        lock = client._v7628_auth_lock() if hasattr(client, "_v7628_auth_lock") else threading.RLock()
        with lock:
            headers = {"cont-yn": "Y", "next-key": cont_key} if cont_key else None
            if hasattr(client, "_v7628_query"):
                data = client._v7628_query(api_id=api_id, endpoint=endpoint, body=body,
                                           required_any=(required,), extra_headers=headers)
            else:
                data = client.post(api_id=api_id, endpoint=endpoint, body=body, required_any=(required,))
            response = dict(getattr(client, "_last_response_headers", {}) or {})
        next_key = str(response.get("next-key", "") or "") if str(response.get("cont-yn", "")).upper() == "Y" else ""
        return data, next_key

    def _code(self, client: Any, symbol: str) -> str:
        if self.symbol_mapper is not None:
            try:
                return self.symbol_mapper(symbol, client)
            except Exception:
                pass
        return symbol

    def bars(self, symbol: str, tf: str, *, force: bool = False) -> list[dict[str, Any]]:
        key = (symbol, tf)
        hit = self.cache.get(key)
        if hit and not force and time.monotonic() - hit[0] < CACHE_TTL.get(tf, 30.0):
            return hit[1]
        client = self._client()
        code = self._code(client, symbol)
        if tf in MINUTE_SCOPE:
            data, _ = self._request(client, "ka10080", "/api/dostk/chart",
                                    {"stk_cd": code, "tic_scope": MINUTE_SCOPE[tf], "upd_stkpc_tp": "1"},
                                    "stk_min_pole_chart_qry")
            result = parse_minute_rows(list(data.get("stk_min_pole_chart_qry") or []))
        else:
            api_id, list_key = PERIOD_API[tf]
            data, _ = self._request(client, api_id, "/api/dostk/chart",
                                    {"stk_cd": code, "base_dt": datetime.now(KST).strftime("%Y%m%d"),
                                     "upd_stkpc_tp": "1"}, list_key)
            result = parse_period_rows(list(data.get(list_key) or []))
        self.cache[key] = (time.monotonic(), result)
        return result

    def investors(self, symbol: str, *, pages: int = 2) -> tuple[dict[date, float], dict[date, float]]:
        key = (symbol, "INVESTOR")
        hit = self.cache.get(key)
        if hit and time.monotonic() - hit[0] < INVESTOR_TTL:
            return hit[1]
        client = self._client()
        code = self._code(client, symbol)
        merged: list[dict[date, float]] = []
        for amt_qty, field in (("2", "frgnr_invsr"), ("1", "orgn")):
            values: dict[date, float] = {}
            cont = ""
            for _page in range(max(1, pages)):
                body = {"dt": datetime.now(KST).strftime("%Y%m%d"), "stk_cd": code,
                        "amt_qty_tp": amt_qty, "trde_tp": "0", "unit_tp": "1"}
                data, cont = self._request(client, "ka10059", "/api/dostk/stkinfo", body, "stk_invsr_orgn", cont)
                values.update(parse_investor_rows(list(data.get("stk_invsr_orgn") or []), field))
                if not cont:
                    break
            merged.append(values)
        result = (merged[0], merged[1])
        self.cache[key] = (time.monotonic(), result)
        return result


# ------------------------------------------------------------------ 화면
class ChartPanel(tk.Frame if tk is not None else object):
    def __init__(self, master: Any, *, fetcher: ChartFetcher,
                 overlay_getter: Callable[[str], dict[str, float]] | None = None,
                 summary_getter: Callable[[str], dict[str, Any]] | None = None,
                 focus_return: Callable[[], None] | None = None,
                 font_family: str = "맑은 고딕") -> None:
        super().__init__(master, bg=BG, highlightthickness=0, takefocus=0)
        self.fetcher = fetcher
        self.overlay_getter = overlay_getter
        self.summary_getter = summary_getter
        self._summary_updated = 0.0
        self.focus_return = focus_return
        self.font = font_family
        self.symbol = ""
        self.name = ""
        self.tf = DEFAULT_TF
        self.bars: list[dict[str, Any]] = []
        self.mas: dict[int, list[float | None]] = {}
        self.foreign: list[float | None] = []
        self.inst: list[float | None] = []
        self.slot = SLOT_DEFAULT
        self.span: int | None = load_span()   # 최근 N봉 구간(없으면 기존 확대/축소 방식)
        self.offset = 0          # 오른쪽 끝에서 과거로 밀어본 봉 수
        self.error = ""
        self.loading = False
        self._layout: dict[str, Any] = {}
        self._request_token = 0
        self._debounce: Any = None
        self._refresh_job: Any = None
        self._drag_x: int | None = None
        self._results: "queue.Queue[tuple]" = queue.Queue()
        self._pending: tuple | None = None
        self._pending_lock = threading.Lock()
        self._wake = threading.Event()
        self._closed = False
        self._build()
        threading.Thread(target=self._worker, name="v7614-chart", daemon=True).start()
        self.after(80, self._poll_results)
        self.bind("<Destroy>", self._on_destroy, add="+")

    # -------------------------------------------------------------- 구성
    def _build(self) -> None:
        top = tk.Frame(self, bg=BG, takefocus=0)
        top.pack(fill="x", padx=6, pady=(4, 2))
        self.title_var = tk.StringVar(value="종목을 선택하면 차트가 표시됩니다")
        tk.Label(top, textvariable=self.title_var, bg=BG, fg=TEXT, font=(self.font, 13, "bold"),
                 anchor="w", takefocus=0).pack(side="left")
        self.summary_var = tk.StringVar(value="")
        self.summary_label = tk.Label(top, textvariable=self.summary_var, bg=BG, fg=TEXT,
                                     font=(self.font, 10, "bold"), anchor="w", justify="left", takefocus=0)
        self.summary_label.pack(side="left", fill="x", expand=True, padx=(10, 0))
        self.summary_label.bind("<Configure>", lambda e: self.summary_label.configure(wraplength=max(100, e.width)))
        self.tf_buttons: dict[str, Any] = {}
        span_row = tk.Frame(self, bg=BG, takefocus=0)
        span_row.pack(fill="x", padx=6, pady=(0, 2))
        box = tk.Frame(span_row, bg=BG, takefocus=0)
        box.pack(side="left")
        for label, tf in TIMEFRAMES:
            button = tk.Label(box, text=label, bg="#12304d", fg=TEXT, font=(self.font, 11, "bold"),
                              padx=9, pady=2, cursor="hand2", takefocus=0, relief="flat", bd=0)
            button.pack(side="left", padx=2)
            button.bind("<Button-1>", lambda _e, chosen=tf: self.set_timeframe(chosen))
            self.tf_buttons[tf] = button
        span_box = tk.Frame(span_row, bg=BG, takefocus=0)
        span_box.pack(side="right")
        tk.Label(span_box, text="최근", bg=BG, fg=AXIS_TEXT, font=(self.font, 9), takefocus=0).pack(side="left", padx=(0, 2))
        self.span_buttons: dict[int, Any] = {}
        for span in SPANS:
            button = tk.Label(span_box, text=f"{span}봉", bg="#12304d", fg=TEXT, font=(self.font, 10, "bold"),
                              padx=6, pady=1, cursor="hand2", takefocus=0, relief="flat", bd=0)
            button.pack(side="left", padx=1)
            button.bind("<Button-1>", lambda _e, chosen=span: self.set_span(chosen))
            self.span_buttons[span] = button
        legend = tk.Frame(self, bg=BG, takefocus=0)
        legend.pack(fill="x", padx=6)
        self.ma_labels: dict[int, Any] = {}
        tk.Label(legend, text="이평", bg=BG, fg=AXIS_TEXT, font=(self.font, 9), takefocus=0).pack(side="left")
        for period in MA_PERIODS:
            label = tk.Label(legend, text=f"{period}", bg=BG, fg=MA_COLORS[period],
                             font=(self.font, 9, "bold"), takefocus=0)
            label.pack(side="left", padx=(4, 0))
            self.ma_labels[period] = label
        self.status_var = tk.StringVar(value="")
        tk.Label(legend, textvariable=self.status_var, bg=BG, fg=AXIS_TEXT, font=(self.font, 9),
                 anchor="e", takefocus=0).pack(side="right")
        self.info_var = tk.StringVar(value="")
        tk.Label(self, textvariable=self.info_var, bg=BG, fg=TEXT, font=(self.font, 10, "bold"),
                 anchor="w", takefocus=0).pack(fill="x", padx=6)
        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0, takefocus=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True, padx=(4, 2), pady=(0, 4))
        self.canvas.bind("<Configure>", lambda _e: self.redraw())
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", lambda _e: self._clear_cross())
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Button-4>", lambda e: self._zoom(+1))
        self.canvas.bind("<Button-5>", lambda e: self._zoom(-1))
        # V7630: 왼쪽 드래그 = 구간 선택 확대(수집기 차트와 같은 방식), 오른쪽 드래그 = 과거/최근으로 밀기
        self.canvas.bind("<ButtonPress-1>", self._on_select_press)
        self.canvas.bind("<B1-Motion>", self._on_select_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_select_release)
        self.canvas.bind("<ButtonPress-3>", self._on_press)
        self.canvas.bind("<B3-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-3>", self._on_release)
        self.canvas.bind("<Double-Button-1>", lambda _e: self._reset_view())
        self._paint_tf_buttons()

    def _paint_tf_buttons(self) -> None:
        for tf, button in self.tf_buttons.items():
            active = tf == self.tf
            button.configure(bg="#2f7bd6" if active else "#12304d", fg="white" if active else TEXT)
        for span, button in getattr(self, "span_buttons", {}).items():
            active = span == self.span
            button.configure(bg="#2f7bd6" if active else "#12304d", fg="white" if active else TEXT)

    def set_span(self, span: int | None) -> None:
        """최근 N봉 구간 선택. 같은 버튼을 다시 누르면 해제(기존 확대 방식으로)."""
        self.span = None if span == self.span or span not in SPANS else span
        if self.span is None:
            self.slot = SLOT_DEFAULT
        self.offset = 0
        save_span(self.span)
        self._paint_tf_buttons()
        self.redraw()
        self._give_focus_back()

    def _give_focus_back(self) -> None:
        if self.focus_return is not None:
            try:
                self.focus_return()
            except Exception:
                pass

    # -------------------------------------------------------------- 외부 호출
    def show_symbol(self, symbol: str, name: str = "") -> None:
        symbol = str(symbol or "").strip().upper()
        if not symbol:
            return
        changed = symbol != self.symbol
        if not changed and (self.bars or self.loading or self.error):
            return   # 같은 종목 재선택(화면 갱신마다 발생)은 무시
        self.symbol, self.name = symbol, str(name or "").strip()
        if changed:
            self.offset = 0
            if self.span is not None and self.span not in SPANS:   # 드래그 확대는 그 종목에서만
                self.span = load_span()
                self._paint_tf_buttons()
            self.bars, self.mas, self.foreign, self.inst = [], {}, [], []
            self.error = ""
            self._set_title()
            self.redraw()
        if self._debounce is not None:
            try:
                self.after_cancel(self._debounce)
            except Exception:
                pass
        # ↑↓로 빠르게 넘길 때 중간 종목은 조회하지 않는다(조회 한도 보호).
        self._debounce = self.after(320 if changed else 10, self._request_load)

    def set_timeframe(self, tf: str) -> None:
        if tf not in dict((v, k) for k, v in TIMEFRAMES):
            return
        if tf != self.tf:
            self.tf = tf
            self.offset = 0
            if self.span is not None and self.span not in SPANS:
                self.span = load_span()
            if self.span is None:
                self.slot = SLOT_DEFAULT
            self.bars, self.mas, self.foreign, self.inst = [], {}, [], []
            self._paint_tf_buttons()
            self._set_title()
            self.redraw()
            self._request_load()
        self._give_focus_back()

    # -------------------------------------------------------------- 조회 흐름
    def _set_title(self) -> None:
        label = dict((v, k) for k, v in TIMEFRAMES).get(self.tf, self.tf)
        if self.symbol:
            self.title_var.set(f"{self.name} {self.symbol} · {label}봉")
            self._update_summary()

    def _update_summary(self) -> None:
        values = {}
        if self.summary_getter is not None and self.symbol:
            try:
                values = self.summary_getter(self.symbol) or {}
            except Exception:
                pass  # Display failure must never stop chart or trading updates.
        text, color = format_market_summary(values, datetime.now(KST))
        self.summary_var.set(text if self.symbol else "")
        self.summary_label.configure(fg=color)
        self._summary_updated = time.monotonic()

    def _request_load(self, *, force: bool = False) -> None:
        self._debounce = None
        if not self.symbol:
            return
        self._request_token += 1
        self.loading = True
        self.status_var.set("불러오는 중…")
        with self._pending_lock:
            self._pending = (self._request_token, self.symbol, self.tf, force)
        self._wake.set()
        self._schedule_refresh()

    def _schedule_refresh(self) -> None:
        if self._refresh_job is not None:
            try:
                self.after_cancel(self._refresh_job)
            except Exception:
                pass
        self._refresh_job = self.after(AUTO_REFRESH_MS.get(self.tf, 30_000), self._auto_refresh)

    def _auto_refresh(self) -> None:
        self._refresh_job = None
        now = datetime.now(KST)
        visible = bool(self.winfo_ismapped())
        if visible and self.symbol and 8 <= now.hour < 20 and now.weekday() < 5:
            self._request_load(force=True)
        else:
            self._schedule_refresh()

    def _worker(self) -> None:
        while not self._closed:
            self._wake.wait(1.0)
            self._wake.clear()
            with self._pending_lock:
                job, self._pending = self._pending, None
            if job is None:
                continue
            token, symbol, tf, force = job
            try:
                bars = self.fetcher.bars(symbol, tf, force=force)
                foreign: dict[date, float] = {}
                inst: dict[date, float] = {}
                note = ""
                if tf in PERIOD_API:
                    try:
                        foreign, inst = self.fetcher.investors(symbol)
                    except Exception as exc:  # 수급만 실패해도 가격 차트는 보여준다
                        note = f"수급 조회 실패: {type(exc).__name__}"
                self._results.put((token, symbol, tf, bars, foreign, inst, "", note))
            except Exception as exc:
                self._results.put((token, symbol, tf, [], {}, {}, f"{type(exc).__name__}: {exc}"[:120], ""))

    def _poll_results(self) -> None:
        if self._closed:
            return
        if time.monotonic() - self._summary_updated >= 1.0:
            self._update_summary()
        try:
            while True:
                token, symbol, tf, bars, foreign, inst, error, note = self._results.get_nowait()
                if token != self._request_token or symbol != self.symbol or tf != self.tf:
                    continue
                self.loading = False
                self.error = error
                if not error:
                    self.bars = bars
                    closes = [b["c"] for b in bars]
                    self.mas = moving_averages(closes)
                    self.foreign = align_investor(bars, foreign, tf)
                    self.inst = align_investor(bars, inst, tf)
                stamp = datetime.now(KST).strftime("%H:%M:%S")
                self.status_var.set(f"오류 · {error}" if error else (f"{note} · " if note else "") + f"갱신 {stamp}")
                self.redraw()
        except queue.Empty:
            pass
        self.after(80, self._poll_results)

    def _on_destroy(self, event: Any) -> None:
        if event.widget is self:
            self._closed = True
            self._wake.set()

    # -------------------------------------------------------------- 조작
    def _on_wheel(self, event: Any) -> None:
        if event.state & 0x0001:   # Shift: 과거/최근으로 이동
            self._scroll(int(-event.delta / 120) * 5)
        else:
            self._zoom(1 if event.delta > 0 else -1)

    def _zoom(self, direction: int) -> None:
        if self.span is not None:      # 휠로 확대/축소하면 구간 고정을 풀고 지금 폭에서 이어간다
            if self.span in SPANS:
                save_span(None)
            self.span = None
            self._paint_tf_buttons()
            self.slot = int(round(self.slot))
        self.slot = max(SLOT_MIN, min(SLOT_MAX, self.slot + (1 if direction > 0 else -1)))
        self.redraw()

    def _scroll(self, bars: int) -> None:
        self.offset = max(0, min(max(0, len(self.bars) - 10), self.offset + bars))
        self.redraw()

    def _on_press(self, event: Any) -> None:
        self._drag_x = event.x

    def _on_drag(self, event: Any) -> None:
        if self._drag_x is None:
            return
        moved = int((event.x - self._drag_x) / max(SPAN_SLOT_MIN, self.slot))
        if moved:
            self._drag_x = event.x
            self._scroll(moved)

    def _on_release(self, _event: Any) -> None:
        self._drag_x = None
        self._give_focus_back()

    # ---- V7630 드래그 구간 확대
    def _index_at(self, x: float) -> int | None:
        layout = self._layout
        if not layout or not layout.get("view"):
            return None
        index = layout["end"] - 1 - int((layout["plot_w"] - min(x, layout["plot_w"] - 1)) // max(SPAN_SLOT_MIN, self.slot))
        view = layout["view"]
        return max(view[0], min(view[-1], index))

    def _on_select_press(self, event: Any) -> None:
        self._select_x = event.x if self._layout and event.x <= self._layout.get("plot_w", 0) else None

    def _on_select_drag(self, event: Any) -> None:
        start = getattr(self, "_select_x", None)
        if start is None or not self._layout:
            return
        layout = self._layout
        x = max(0, min(layout["plot_w"], event.x))
        self.canvas.delete("zoomsel")
        top, bottom = layout["panels"]["price"][0], layout["height"] - layout["bottom_h"]
        self.canvas.create_rectangle(min(start, x), top, max(start, x), bottom, fill="#3d8bfd", stipple="gray25",
                                     outline="#9cc7ff", width=1, tags="zoomsel")

    def _on_select_release(self, event: Any) -> None:
        start, self._select_x = getattr(self, "_select_x", None), None
        self.canvas.delete("zoomsel")
        if start is not None and self._layout and abs(event.x - start) >= ZOOM_DRAG_MIN_PX:
            picked = zoom_window(self._index_at(min(start, event.x)), self._index_at(max(start, event.x)), len(self.bars))
            if picked is not None:
                self.span, self.offset = picked
                self._paint_tf_buttons()
                self.redraw()
        self._give_focus_back()

    def _reset_view(self) -> None:
        self.offset = 0
        self.span = load_span()        # 드래그 확대를 풀고, 골라 둔 구간 버튼이 있으면 그 구간으로
        self._paint_tf_buttons()
        if self.span is None:          # 구간을 골라 둔 상태면 구간은 유지하고 최근 위치로만 돌아간다
            self.slot = SLOT_DEFAULT
        self.redraw()
        self._give_focus_back()

    # -------------------------------------------------------------- 그리기
    def redraw(self) -> None:
        canvas = self.canvas
        canvas.delete("all")
        width, height = int(canvas.winfo_width()), int(canvas.winfo_height())
        if width < 120 or height < 120:
            return
        axis_w, bottom_h = 74, 22
        plot_w = width - axis_w - 4
        if self.span is not None:
            self.slot = span_slot(plot_w, self.span)
        if not self.bars:
            message = (self.error and f"차트 조회 실패\n{self.error}") or (
                "불러오는 중…" if self.loading else "종목표에서 종목을 클릭하거나 ↑↓로 고르세요")
            canvas.create_text(width / 2, height / 2, text=message, fill=AXIS_TEXT,
                               font=(self.font, 12), justify="center")
            self._layout = {}
            return
        count = self.span if self.span is not None else max(5, int(plot_w // self.slot))
        end = len(self.bars) - self.offset
        start = max(0, end - count)
        view = list(range(start, end))
        show_flow = self.tf in PERIOD_API
        usable = height - bottom_h
        ratios = (0.56, 0.16, 0.14, 0.14) if show_flow else (0.72, 0.28)
        names = ("price", "volume", "foreign", "inst") if show_flow else ("price", "volume")
        panels: dict[str, tuple[int, int]] = {}
        y = 2
        for name, ratio in zip(names, ratios):
            panel_h = int(usable * ratio) - 6
            panels[name] = (y, y + panel_h)
            y += panel_h + 6
        for top, bottom in panels.values():
            canvas.create_rectangle(0, top, plot_w, bottom, fill=PANEL_BG, outline=GRID)
        x_of = lambda i: plot_w - (end - i) * self.slot + self.slot / 2
        body_w = max(1, self.slot * 0.68)

        # 가격 범위(캔들 기준, 이평은 범위를 넘으면 잘라서 그림)
        lows = [self.bars[i]["l"] for i in view]
        highs = [self.bars[i]["h"] for i in view]
        lo, hi = min(lows), max(highs)
        overlay = {}
        if self.overlay_getter is not None and self.symbol:
            try:
                overlay = {k: v for k, v in (self.overlay_getter(self.symbol) or {}).items() if v and v > 0}
            except Exception:
                overlay = {}
        pad = max((hi - lo) * 0.06, hi * 0.002)
        lo, hi = lo - pad, hi + pad
        p_top, p_bot = panels["price"]
        y_of = lambda price: p_bot - (price - lo) / (hi - lo) * (p_bot - p_top)

        # 가격 눈금
        step = nice_step(hi - lo, 6)
        tick = math.ceil(lo / step) * step
        while tick < hi:
            yy = y_of(tick)
            canvas.create_line(0, yy, plot_w, yy, fill=GRID, dash=(2, 4))
            canvas.create_text(plot_w + 6, yy, text=won(tick), anchor="w", fill=AXIS_TEXT, font=(self.font, 9))
            tick += step

        # 캔들 + 거래량(같은 색)
        v_top, v_bot = panels["volume"]
        vmax = max([self.bars[i]["v"] for i in view] + [1])
        for i in view:
            bar = self.bars[i]
            prev_close = self.bars[i - 1]["c"] if i > 0 else bar["o"]
            if bar["c"] > bar["o"]:
                color = UP
            elif bar["c"] < bar["o"]:
                color = DOWN
            else:
                color = UP if bar["c"] >= prev_close else DOWN
            x = x_of(i)
            canvas.create_line(x, y_of(bar["h"]), x, y_of(bar["l"]), fill=color)
            y1, y2 = y_of(max(bar["o"], bar["c"])), y_of(min(bar["o"], bar["c"]))
            if y2 - y1 < 1:
                y2 = y1 + 1
            canvas.create_rectangle(x - body_w / 2, y1, x + body_w / 2, y2, fill=color, outline=color)
            vh = (bar["v"] / vmax) * (v_bot - v_top - 4)
            canvas.create_rectangle(x - body_w / 2, v_bot - vh, x + body_w / 2, v_bot, fill=color, outline=color)
        canvas.create_text(4, v_top + 3, text="거래량", anchor="nw", fill=AXIS_TEXT, font=(self.font, 9, "bold"))
        canvas.create_text(plot_w + 6, v_top + 8, text=self._short(vmax), anchor="w", fill=AXIS_TEXT, font=(self.font, 9))

        # 이동평균선
        for period in MA_PERIODS:
            series = self.mas.get(period, [])
            segment: list[float] = []
            for i in view:
                value = series[i] if i < len(series) else None
                if value is None or not (lo <= value <= hi):
                    if len(segment) >= 4:
                        canvas.create_line(*segment, fill=MA_COLORS[period], width=1.4)
                    segment = []
                    continue
                segment += [x_of(i), y_of(value)]
            if len(segment) >= 4:
                canvas.create_line(*segment, fill=MA_COLORS[period], width=1.4)

        # 보유 평단·목표·손절선
        for key, color, label in (("avg", "#ffd34d", "평단"), ("target", UP, "목표"), ("stop", DOWN, "손절")):
            value = overlay.get(key)
            if value and lo <= value <= hi:
                yy = y_of(value)
                canvas.create_line(0, yy, plot_w, yy, fill=color, dash=(6, 3), width=1.2)
                canvas.create_text(4, yy - 2, text=f"{label} {won(value)}", anchor="sw", fill=color,
                                   font=(self.font, 9, "bold"))

        # 현재가 표시
        last = self.bars[end - 1]
        yy = y_of(last["c"])
        last_color = UP if last["c"] >= last["o"] else DOWN
        canvas.create_rectangle(plot_w + 1, yy - 9, width - 1, yy + 9, fill=last_color, outline=last_color)
        canvas.create_text(plot_w + 6, yy, text=won(last["c"]), anchor="w", fill="white", font=(self.font, 9, "bold"))

        # 수급(외국인 수량·기관 금액)
        if show_flow:
            for key, series, title, unit in (("foreign", self.foreign, "외국인 순매수량", "주"),
                                             ("inst", self.inst, "기관 순매수금액", "백만원")):
                top, bottom = panels[key]
                values = [series[i] for i in view if i < len(series) and series[i] is not None]
                mid = (top + bottom) / 2
                canvas.create_line(0, mid, plot_w, mid, fill=GRID)
                canvas.create_text(4, top + 3, text=f"{title}({unit})", anchor="nw", fill=AXIS_TEXT,
                                   font=(self.font, 9, "bold"))
                if not values:
                    canvas.create_text(plot_w / 2, mid, text="수급 자료 없음", fill=AXIS_TEXT, font=(self.font, 9))
                    continue
                peak = max(abs(v) for v in values) or 1.0
                half = (bottom - top) / 2 - 3
                for i in view:
                    value = series[i] if i < len(series) else None
                    if value is None:
                        continue
                    x = x_of(i)
                    h = value / peak * half
                    color = UP if value > 0 else DOWN
                    canvas.create_rectangle(x - body_w / 2, mid - max(h, 0), x + body_w / 2, mid - min(h, 0),
                                            fill=color, outline=color)
                canvas.create_text(plot_w + 6, top + 8, text=self._short(peak), anchor="w", fill=AXIS_TEXT,
                                   font=(self.font, 9))
                canvas.create_text(plot_w + 6, bottom - 8, text="-" + self._short(peak), anchor="w",
                                   fill=AXIS_TEXT, font=(self.font, 9))

        # 시간 눈금
        last_label_x = -999.0
        last_day = None
        for i in view:
            moment = self.bars[i]["t"]
            x = x_of(i)
            label = ""
            if self.tf in MINUTE_SCOPE:
                if moment.date() != last_day:
                    label = moment.strftime("%m/%d")
                    last_day = moment.date()
                elif moment.minute % (30 if self.tf == "m1" else 60) == 0:
                    label = moment.strftime("%H:%M")
            elif self.tf == "D":
                if last_day is None or moment.month != last_day.month:
                    label = moment.strftime("%y/%m")
                last_day = moment.date()
            else:
                if last_day is None or moment.year != last_day.year:
                    label = moment.strftime("%Y")
                last_day = moment.date()
            if label and x - last_label_x > 56 and x > 24:
                canvas.create_line(x, panels["price"][0], x, height - bottom_h, fill=GRID, dash=(1, 6))
                canvas.create_text(x, height - bottom_h + 4, text=label, anchor="n", fill=AXIS_TEXT,
                                   font=(self.font, 9))
                last_label_x = x
        self._layout = {"x_of": x_of, "y_of": y_of, "view": view, "end": end, "plot_w": plot_w,
                        "panels": panels, "lo": lo, "hi": hi, "height": height, "bottom_h": bottom_h}
        self._update_info(end - 1)

    @staticmethod
    def _short(value: float) -> str:
        value = abs(float(value))
        if value >= 1e8:
            return f"{value / 1e8:.1f}억"
        if value >= 1e4:
            return f"{value / 1e4:.0f}만"
        return f"{value:,.0f}"

    def _update_info(self, index: int) -> None:
        if not self.bars or not (0 <= index < len(self.bars)):
            self.info_var.set("")
            return
        bar = self.bars[index]
        prev = self.bars[index - 1]["c"] if index > 0 else bar["o"]
        change = (bar["c"] / prev - 1) * 100 if prev else 0.0
        when = bar["t"].strftime("%m/%d %H:%M" if self.tf in MINUTE_SCOPE else "%Y/%m/%d")
        text = (f"{when}  시 {won(bar['o'])}  고 {won(bar['h'])}  저 {won(bar['l'])}  종 {won(bar['c'])} "
                f"({signed(change, 2)}%)  거래량 {won(bar['v'])}")
        if self.tf in PERIOD_API:
            f = self.foreign[index] if index < len(self.foreign) else None
            g = self.inst[index] if index < len(self.inst) else None
            if f is not None:
                text += f"  외국인 {signed(f)}주"
            if g is not None:
                text += f"  기관 {signed(g)}백만"
        self.info_var.set(text)
        for period, label in self.ma_labels.items():
            series = self.mas.get(period, [])
            value = series[index] if index < len(series) else None
            label.configure(text=f"{period}:{won(value)}" if value else f"{period}")

    def _clear_cross(self) -> None:
        self.canvas.delete("cross")
        if self._layout:
            self._update_info(self._layout["end"] - 1)

    def _on_motion(self, event: Any) -> None:
        layout = self._layout
        if not layout:
            return
        self.canvas.delete("cross")
        plot_w = layout["plot_w"]
        if event.x > plot_w:
            return
        end = layout["end"]
        index = end - 1 - int((plot_w - event.x) // max(SPAN_SLOT_MIN, self.slot))
        if index not in layout["view"]:
            return
        x = layout["x_of"](index)
        self.canvas.create_line(x, layout["panels"]["price"][0], x, layout["height"] - layout["bottom_h"],
                                fill=CROSS, dash=(3, 3), tags="cross")
        p_top, p_bot = layout["panels"]["price"]
        if p_top <= event.y <= p_bot:
            self.canvas.create_line(0, event.y, plot_w, event.y, fill=CROSS, dash=(3, 3), tags="cross")
            price = layout["lo"] + (p_bot - event.y) / (p_bot - p_top) * (layout["hi"] - layout["lo"])
            self.canvas.create_rectangle(plot_w + 1, event.y - 9, plot_w + 73, event.y + 9, fill="#2b4560",
                                         outline="#2b4560", tags="cross")
            self.canvas.create_text(plot_w + 6, event.y, text=won(price), anchor="w", fill="white",
                                    font=(self.font, 9, "bold"), tags="cross")
        self._update_info(index)
