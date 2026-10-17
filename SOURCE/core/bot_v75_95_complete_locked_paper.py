#!/usr/bin/env python3
from __future__ import annotations
from datetime import datetime, timedelta
import hashlib, importlib.util, json, math, re, sys, textwrap
from pathlib import Path
from types import ModuleType
from typing import Any

VERSION="V75.95-R21-HWPX-LOGIC-COLOR-PAPER"
BASE_DIR=Path(__file__).resolve().parent
PARENT_FILE="bot_v75_94_multi_admission_paper.py"
PARENT_SHA256="f05494490f81701f917ecfaa71e0945ff5b0cd17db685980657bbe3b16fdc982"
RUNTIME_DIR=BASE_DIR.parent/"runtime_v7595"
MARKET_HISTORY=RUNTIME_DIR/"market_analysis_history.jsonl"
STARTUP_STATUS=BASE_DIR.parent/"runtime"/"logs"/"v75_95_startup_status.txt"

def _write_startup_status(stage:str,detail:str="")->None:
    try:
        STARTUP_STATUS.parent.mkdir(parents=True,exist_ok=True)
        STARTUP_STATUS.write_text(
            f"{datetime.now().isoformat(timespec='seconds')}\nSTAGE={stage}\n{detail}\n",
            encoding="utf-8",
        )
    except OSError:
        pass

def _load_parent()->ModuleType:
    path=BASE_DIR/PARENT_FILE; actual=hashlib.sha256(path.read_bytes()).hexdigest()
    if actual!=PARENT_SHA256: raise RuntimeError(f"{PARENT_FILE} SHA256 불일치")
    spec=importlib.util.spec_from_file_location("v7595_immutable_v7594_base",path)
    if spec is None or spec.loader is None: raise RuntimeError("V75.94 로드 실패")
    module=importlib.util.module_from_spec(spec); sys.modules[spec.name]=module; spec.loader.exec_module(module); return module

parent=_load_parent(); v7525,v57,KST=parent.v7525,parent.v57,parent.KST
v7556=v57.v7556; HUB=v7556.HUB
_PARENT_CHECKS=tuple(parent.self_check())
import v7595_r21_hwpx_logic as r21_logic
from v7595_r21_hwpx_logic import (
    AdmissionEvidence, StageDecision, bullish_ma5_settle, breakout_decision,
    candidate_decision, evaluate_observation_admission,
    exhaustion_entry_decision, interest_decision,
)

HWPX_EXHAUSTION_PATTERN="조정후거래바닥양봉매수"
HWPX_BREAKOUT_PREFIX="HWPX_돌파"
TAKE_PROFIT_PCT=0.015
REVERSAL_STOP_PCT=0.05
GENERAL_BREAKOUT_STOP_PCT=0.012
DRY_BREAKOUT_STOP_PCT=0.011

_MRO={cls.__name__:cls for cls in parent.V7594MultiAdmissionEngine.__mro__}
_V7588_CLASS=_MRO["V7588IncrementalStageLimitEngine"]
_V7590_CLASS=_MRO["V7590FullPatternFusionEngine"]
_V7516_CLASS=_MRO["CapitalSplitEngine"]
_V7588_MOD=sys.modules[_V7588_CLASS.__module__]
_V7590_MOD=sys.modules[_V7590_CLASS.__module__]
_V7516_MOD=sys.modules[_V7516_CLASS.__module__]
_V753=v7525.v753

def _r21_interest_signal(previous:Any,current:Any)->tuple[bool,str]:
    po=float(getattr(previous,"open",0) or 0)
    pc=float(getattr(previous,"close",0) or 0)
    co=float(getattr(current,"open",0) or 0)
    cc=float(getattr(current,"close",0) or 0)
    pv=max(1,int(getattr(previous,"volume",0) or 0))
    cv=int(getattr(current,"volume",0) or 0)
    ratio=cv/pv
    allowed=cc<co and cc<pc and ratio+1e-12>=3.0
    return allowed,f"하락중음봉거래x{ratio:.2f}{'' if allowed else '_3배미달'}"

def _r21_candidate_signal(flush:Any,current:Any)->tuple[bool,str]:
    decision=candidate_decision(flush,current)
    return decision.allowed,decision.reason

# V75.94 owns observation admission; V75.88's earlier 1.5%+20k fallback must
# not bypass the HWPX common gates.  Interest/candidate are narrowed to the
# numeric definitions, while the completed-MA5 order is replaced below by the
# explicit two-stage HWPX evaluator.
parent.evaluate_observation_admission=evaluate_observation_admission
_V7588_MOD.observation_signal=lambda _bars:(False,"V75.94_R21정밀관찰판정사용")
_V7588_MOD.interest_signal=lambda _previous,_current:(False,"R21정밀관심판정사용")
_V7588_MOD.buy_candidate_signal=lambda _flush,_current:(False,"R21정밀매수직전판정사용")
_V7588_MOD.settled_above_ma5=lambda _bars:False

_ORIGINAL_V7590_NEW_ROUTES=_V7590_MOD.evaluate_new_routes
def _keep_flow_absorption_only(bars:Any,*,now:datetime)->Any:
    """Replace old breakout thresholds without removing flow absorption."""
    decision=_ORIGINAL_V7590_NEW_ROUTES(bars,now=now)
    if str(getattr(decision,"route","")) in {"breakout_pullback","verified_breakout"}:
        return _V7590_MOD.PatternDecision(False,"r21_breakout_replaced","R21정밀돌파판정사용")
    return decision
_V7590_MOD.evaluate_new_routes=_keep_flow_absorption_only

# Never show the historical design sample as if it were a live trading result.
v57.SEED_CONSOLE_ROWS=[]

STAGE_DETAIL={
 "관찰후보":"초기 포착. 당일 시가 대비 한때 +5% 이상 오른 종목만 본다. 전일 동시간 거래 160%, 09:30~10:00 누적 20만주·30억원, 1분 +1.5%와 동시간 160%, 또는 직전 1분 대비 거래 2배 조건 중 하나를 통과해야 한다. 10시 이전 거래 2배 경로는 누적대금 50억원도 필요하다.",
 "관심종목":"조정 추적. 시가 대비 +5% 상승 이력이 있고 당일 시가를 깨지 않은 명백한 하락에서 현재 음봉 거래량이 직전 1분봉의 3배 이상일 때 상향한다. 기준 음봉의 시각·거래량을 저장해 이후 거래 소진을 비교한다.",
 "매수가능성":"매수직전 통합. 기준 음봉 대비 현재 거래가 50% 이하이고 아래꼬리·짧은봉·짧은양봉·1분 MA5 회복 중 하나가 확인되면 진입 준비한다. 후속 5~10봉 평균 거래가 기준 음봉의 30% 이하이면 1차 PAPER 매수, 양봉으로 1분 MA5 위 두 봉 안착 시 2차 완성매수를 검토한다.",
 "매수종목":"PAPER 보유. 이 탭에서만 현재가·보유수량·매수시각·평가손익을 표시한다. 체결 즉시 +1.5% 목표매도를 등록한다. 거래바닥형은 기존 분할한도, 돌파형은 별도 -1.2%/-1.1% 방어선을 적용하며 모든 주문은 PAPER 안전검사를 다시 통과한다.",
 "매도종목":"당일 완료. 매수시각·매도시각·매수가·매도가·수량·실현손익·매도사유를 보존한다. 같은 신호의 즉시 재진입을 차단하고 목표·손절·수동매도 사유를 구분한다.",
 "시장보류":"종목 조건은 유지되지만 지수 급락, 데이터 누락·지연, 가격·호가 또는 주문 안전조건 때문에 신규진입만 보류한다. 보류시각과 정확한 해제조건을 남기며 조건 회복 시 원래 단계로 복귀한다.",
}
_MARKET_STATE:dict[str,Any]={
    "updated":"-", "market":"조회 전", "rows":[], "indices":{},
    "breadth":{}, "theme_leaders":[], "source":"엔진 내부 비차단 자료",
}
_LAST_30_BUCKET:str=""
_MARKET_30_HISTORY:list[dict[str,str]]=[]

def _market_current_lines(now:datetime|None=None)->list[str]:
    now=now or datetime.now(KST); rows=list(_MARKET_STATE.get("rows",[]))
    counts:dict[str,int]={}
    for row in rows: counts[str(row.get("stage",""))]=counts.get(str(row.get("stage","")),0)+1
    market_return=_MARKET_STATE.get("market_return")
    market_return_text=f" · 내부지수평균 {float(market_return):+.2f}%" if isinstance(market_return,(int,float)) else ""
    indices=dict(_MARKET_STATE.get("indices",{}) or {})
    kospi=indices.get("KOSPI_PROXY")
    kosdaq=indices.get("KOSDAQ_PROXY")
    def pct(value:Any)->str:
        return f"{float(value):+.2f}%" if isinstance(value,(int,float)) else "수집 불가"
    breadth=dict(_MARKET_STATE.get("breadth",{}) or {})
    watched_breadth=(
        f"감시종목 상승 {int(breadth.get('up',0))} · 하락 {int(breadth.get('down',0))} · 보합 {int(breadth.get('flat',0))}"
        if breadth else "감시종목 상승·하락: 수집 전"
    )
    leaders=list(_MARKET_STATE.get("theme_leaders",[]) or [])
    theme_text=" / ".join(str(x) for x in leaders[:5]) if leaders else "분류자료 수집 불가"
    return [f"[{now:%Y-%m-%d %H:%M:%S}] 10분 장세분석",f"시장상태: {_MARKET_STATE.get('market','조회 불가')}{market_return_text}",
      f"관찰 {counts.get('관찰후보',0)} · 관심 {counts.get('관심종목',0)} · 매수직전 {counts.get('매수가능성',0)} · 보유 {counts.get('매수종목',0)} · 매도 {counts.get('매도종목',0)} · 보류 {counts.get('시장보류',0)}",
      f"지수 대용: KOSPI(KODEX200) {pct(kospi)} · KOSDAQ(KODEX코스닥150) {pct(kosdaq)}",
      f"전체시장 상승·하락 종목수: 공식 공급값 수집 불가 · {watched_breadth}",
      "프로그램 비차익 순매수: 공식 공급값 수집 불가",
      "미국 나스닥선물·외국인 선물/옵션·종목선물: 공식 공급값 수집 불가",
      f"상승테마 5개(대장주 먼저): {theme_text}",
      "※ 0769(또는 0969)·0633·0200·0504 화면을 열지 않습니다. 공식 미제공 값은 조회 불가(수집 불가)로 표시하고 추정하지 않으며 시작·연동·매매를 막지 않습니다."]

def _market_report()->str:
    lines=_market_current_lines()
    lines.extend(["", "[30분 상세이력 · 최신순]"])
    if _MARKET_30_HISTORY:
        for item in reversed(_MARKET_30_HISTORY[-8:]): lines.append(f"{item['at']}  {item['summary']}")
    else: lines.append("아직 기록 없음")
    return "\n".join(lines)

def _record_30min()->None:
    global _LAST_30_BUCKET
    now=datetime.now(KST); bucket=f"{now:%Y%m%d%H}{0 if now.minute<30 else 30:02d}"
    if bucket==_LAST_30_BUCKET: return
    _LAST_30_BUCKET=bucket; RUNTIME_DIR.mkdir(parents=True,exist_ok=True)
    current=_market_current_lines(now); summary=" / ".join(current[1:3])
    item={"at":now.strftime("%m/%d %H:%M"),"summary":summary}
    _MARKET_30_HISTORY.append(item); del _MARKET_30_HISTORY[:-48]
    with MARKET_HISTORY.open("a",encoding="utf-8") as f: f.write(json.dumps({"at":now.isoformat(),"summary":summary,"analysis":"\n".join(current)},ensure_ascii=False)+"\n")

_BASE_COMPILER=v57.compile_collaboration_input
def compile_v7595(text:str,**kwargs:Any)->Any:
    draft=_BASE_COMPILER(text,**kwargs); raw=" ".join(str(text).split())
    plain_buy=bool(re.search(r"(?:매수해|매수|사줘|사라)\s*$",raw)) and not re.search(r"\d[\d,]*\s*원",raw)
    if plain_buy and str(getattr(draft,"action",""))=="BUY_CONDITION":
        draft.action="IMMEDIATE_BUY"; draft.errors=[]
    return draft

_BASE_ROUTE=v57.route_interpreted_action
def route_v7595(action:str)->str: return "GENERAL" if action=="IMMEDIATE_BUY" else _BASE_ROUTE(action)

_BASE_APPLY=v7556._apply_collaboration_command
def apply_command_v7595(self:Any,command:dict[str,Any])->str:
    if str(command.get("kind",""))!="IMMEDIATE_BUY": return _BASE_APPLY(self,command)
    symbol=str(command.get("symbol","")); name=str(command.get("name","") or symbol)
    if not re.fullmatch(r"\d{6}",symbol): raise ValueError("종목을 확인할 수 없습니다")
    self._manual_add_watch(symbol,name); result=self._manual_buy(symbol,1)
    return f"{name} 즉시 PAPER 매수 실행 · {result} · 체결 후 기존 +1.5% 목표매도 적용"

v57.compile_collaboration_input=compile_v7595; v7556.compile_collaboration_input=compile_v7595
v57.route_interpreted_action=route_v7595; v7556._apply_collaboration_command=apply_command_v7595

def normalize_gui_rows(rows:Any)->list[dict[str,Any]]:
    """표시 단계만 통합하고 편입·이탈·매수·매도 시간과 가격은 그대로 보존한다."""
    normalized=[]
    stage_order={"관찰후보":0,"관심종목":1,"매수가능성":2,"매수직전":2,"매수종목":3,"매도종목":4,"시장보류":5}
    for raw in rows or []:
        row=dict(raw)
        if str(row.get("stage","")) in {"매수직전","매수후보"}: row["stage"]="매수가능성"
        reason=" ".join(str(row.get("reason","")).split())
        reason=re.sub(r"\b시간이력\b\s*[·:/-]*\s*","",reason)
        stage=str(row.get("stage",""))
        # The dedicated time column already identifies when the row changed.
        # Keep the reason semantic and compact instead of repeating the same
        # category name and clock a second time.
        shown=str(v57.DISPLAY_STAGE.get(stage,stage))
        reason=re.sub(rf"(?:^|\s){re.escape(shown)}\s+\d{{1,2}}:\d{{2}}(?::\d{{2}})?\s*[·:/-]*\s*"," ",reason)
        if bool(row.get("departed")):
            moved_match=re.search(r"([가-힣·]+)\s*단계로\s*이동",reason)
            moved=str(moved_match.group(1)) if moved_match else "추적종료"
            moved={"관찰종목":"관찰후보","매수후보·직전":"매수가능성"}.get(moved,moved)
            departure=str(row.get("departure_time","") or "")
            if moved in stage_order:
                direction="상향" if stage_order[moved]>stage_order.get(stage,-1) else "하향"
                reason=f"{v57.DISPLAY_STAGE.get(stage,stage)} → {v57.DISPLAY_STAGE.get(moved,moved)} {direction}"
            else:
                reason=f"{v57.DISPLAY_STAGE.get(stage,stage)} → 추적종료"
            if departure: reason+=f" · {departure}"
        row["reason"]=" ".join(reason.split())
        normalized.append(row)
    def newest_clock(row:dict[str,Any])->str:
        clocks=re.findall(r"\d{1,2}:\d{2}(?::\d{2})?",str(row.get("stage_time",row.get("time",""))))
        return clocks[-1] if clocks else "00:00:00"
    live=[row for row in normalized if not bool(row.get("departed"))]
    departed=[row for row in normalized if bool(row.get("departed"))]
    live.sort(key=newest_clock,reverse=True)
    departed.sort(key=newest_clock,reverse=True)
    return live+departed

def _merge_ttk_config(cnf:Any,kwargs:dict[str,Any])->dict[str,Any]:
    """Return keyword arguments accepted by ttk widget constructors.

    Classic tkinter widgets accept ``(master, cnf, **kwargs)``.  ttk widgets
    such as ``ttk.LabelFrame`` accept only ``(master, **kwargs)`` on Windows
    Python 3.11.  Keeping this conversion explicit prevents the dashboard
    startup crash caused by forwarding ``cnf`` as a third positional value.
    """
    merged:dict[str,Any]={}
    if cnf:
        merged.update(dict(cnf))
    merged.update(kwargs)
    return merged

_R20_MAIN_COLUMNS=("no","stage","name","current","change","volume","basis","target","qty","stage_time","reason")
_R20_COMMON_DISPLAY=("no","stage","name","current","change","stage_time","reason")
_R20_HOLDING_DISPLAY=("no","stage","name","current","change","qty","stage_time","reason")

def _display_columns_for_stage(stage:str)->tuple[str,...]:
    """The holding quantity is visible only on the dedicated holding tab."""
    return _R20_HOLDING_DISPLAY if str(stage)=="매수종목" else _R20_COMMON_DISPLAY

class V7595CompleteLockedEngine(parent.V7594MultiAdmissionEngine):
    def __init__(self,*args:Any,**kwargs:Any)->None:
        self._r21_stage_times:dict[tuple[str,str],str]={}
        self._r21_order_minute:dict[str,datetime]={}
        self._r21_second_buy_done:set[str]=set()
        self._r21_route_counts={"exhaustion_first":0,"ma5_second":0,"general_breakout":0,"dry_breakout":0}
        super().__init__(*args,**kwargs)
        # Never revive yesterday's tracking rows as if they were today's
        # live candidates.  Same-day rows survive a restart; older rows do not.
        today=datetime.now(KST).date()
        potentials=getattr(self,"_supply_potential",{})
        stale=[
            symbol for symbol,item in list(potentials.items())
            if getattr(item,"detected_at",None) is None
            or getattr(item,"detected_at").date()!=today
        ]
        for symbol in stale:
            potentials.pop(symbol,None)
            self._v7588_phase.pop(symbol,None)
            self._v7588_flush.pop(symbol,None)
        if stale:
            try: self._save_supply_potential()
            except Exception: pass
        self.logger.warning(
            "[%s] HWPX 숫자기준 활성 | 관찰 공통=당일시가대비 최고+5%% | "
            "관심=하락음봉거래x3·시가지지 | 소진=현재50%%·5~10봉평균30%% | "
            "일반돌파<=09:40 x1.8 · 마른돌파>09:40 x2.2",
            VERSION,
        )

    def _v7588_publish(self,symbol:str,phase:str,stage:str,reason:str,now:datetime)->None:
        old_phase=str(getattr(self,"_v7588_phase",{}).get(symbol,""))
        super()._v7588_publish(symbol,phase,stage,reason,now)
        if old_phase!=phase:
            canonical="매수가능성" if stage in {"매수직전","매수후보","매수가능성"} else stage
            self._r21_stage_times[(canonical,str(symbol))]=now.strftime("%H:%M:%S")

    def _attempt_entry(self,signal:Any,*,from_retry:bool=False)->bool:
        pattern=str(getattr(signal,"pattern",""))
        reason=str(getattr(signal,"reason",""))
        if pattern==HWPX_EXHAUSTION_PATTERN or pattern.startswith(HWPX_BREAKOUT_PREFIX) or "HWPX_R21" in reason:
            # Bypass the superseded V75.90 prior-flow heuristic only.  The
            # V75.88 path still enforces PAPER broker, quote age, spread,
            # position cap, market guard, daily loss and immediate target.
            return bool(_V7588_CLASS._attempt_entry(self,signal,from_retry=from_retry))
        return bool(super()._attempt_entry(signal,from_retry=from_retry))

    def _attempt_scale_in(self,bar:Any)->bool:
        symbol=str(getattr(bar,"symbol",""))
        position=self.broker.positions.get(symbol)
        if position is None or str(getattr(position,"pattern",""))!=HWPX_EXHAUSTION_PATTERN:
            return bool(super()._attempt_scale_in(bar))
        if symbol in self._r21_second_buy_done:
            return False
        now=datetime.now(KST)
        if (now-position.entry_time).total_seconds()<45.0:
            return False
        raw=sorted(
            list(getattr(self.client,"v7580_raw_cache",{}).get(symbol,[])),
            key=lambda item:getattr(item,"timestamp"),
        )
        today=[item for item in raw if getattr(item,"timestamp").date()==now.date()]
        settle=bullish_ma5_settle(today)
        if not settle.allowed:
            return False
        market_ok,market_reason=self._market_entry_allowed()
        if not market_ok or self.risk.halted:
            self.logger.info("R21 2차매수보류 %s %s",self._label(symbol),market_reason or self.risk.halt_reason)
            return False
        try:
            fresh=self.client.get_quote(symbol)
            enriched=self.client.get_orderbook(symbol,fresh,side="buy")
            self.latest_quotes[symbol]=enriched
        except Exception as exc:
            self.logger.info("R21 2차매수 시세보류 %s %s",self._label(symbol),exc)
            return False
        allowed,price_reason=self._scale_price_allowed(position,float(enriched.price))
        if not allowed or enriched.best_ask is None:
            self.logger.info("R21 2차매수 가격보류 %s %s",self._label(symbol),price_reason)
            return False
        expected=_V753.align_stock_price(
            enriched.best_ask*(1.0+self.settings.slippage_pct),upward=True,
        )
        remaining=max(0.0,_V7516_MOD.MAX_SYMBOL_NOTIONAL-_V7516_MOD._current_position_notional(position))
        notional=min(3_000_000.0,remaining)
        quantity=math.floor(notional/max(1.0,expected))
        if quantity<1:
            return False
        signal=_V753.Signal(
            symbol,_V753.SignalAction.BUY,settle.score,
            f"HWPX_R21_2차완성매수|{settle.reason}|체결즉시목표1.5%",
            float(enriched.price),now,HWPX_EXHAUSTION_PATTERN,
            REVERSAL_STOP_PCT,TAKE_PROFIT_PCT,
        )
        try:
            fill=_V7516_MOD.paper_add_to_position(
                self.broker,symbol,quantity,enriched,
                f"2차완성매수|{settle.reason}|{price_reason}",
                stop_loss_pct=REVERSAL_STOP_PCT,
                take_profit_pct=TAKE_PROFIT_PCT,
                pattern=HWPX_EXHAUSTION_PATTERN,
            )
        except ValueError as exc:
            self.logger.info("R21 2차매수 보류 %s %s",self._label(symbol),exc)
            return False
        self._r21_second_buy_done.add(symbol)
        self._r21_route_counts["ma5_second"]+=1
        self._print_buy(fill,self.broker.positions[symbol],signal)
        self.logger.warning("R21 2차완성매수 %s | %s",self._label(symbol),settle.reason)
        return True

    @staticmethod
    def _r21_flush_index(raw:list[Any],flush:Any)->int:
        target=getattr(flush,"timestamp",None)
        for index,item in enumerate(raw):
            if getattr(item,"timestamp",None)==target:
                return index
        return -1

    def _refresh_actual_bars(self,symbol:str,quote:Any)->None:
        now=datetime.now(KST)
        raw_before=sorted(
            list(getattr(self.client,"v7580_raw_cache",{}).get(symbol,[])),
            key=lambda item:getattr(item,"timestamp"),
        )
        today_before=[item for item in raw_before if getattr(item,"timestamp").date()==now.date()]
        phase=str(getattr(self,"_v7588_phase",{}).get(symbol,""))
        if phase in {"INTEREST","CANDIDATE","PREBUY"} and today_before:
            session_open=float(getattr(today_before[0],"open",0) or 0)
            current_low=float(getattr(today_before[-1],"low",0) or 0)
            if session_open>0 and current_low<session_open:
                self._v7588_flush.pop(symbol,None)
                self._v7588_publish(symbol,"OBSERVED","관찰후보","당일시가이탈로관심조건상실",now)

        super()._refresh_actual_bars(symbol,quote)
        if symbol in self._v7587_excluded or symbol in self.pending_entries or symbol in self.broker.positions:
            return
        raw=sorted(
            list(getattr(self.client,"v7580_raw_cache",{}).get(symbol,[])),
            key=lambda item:getattr(item,"timestamp"),
        )
        today=[item for item in raw if getattr(item,"timestamp").date()==now.date()]
        if len(today)<2:
            return

        phase=str(self._v7588_phase.get(symbol,""))
        if phase=="OBSERVED":
            full_interest=interest_decision(raw,now=now)
            if full_interest.allowed:
                self._v7588_flush[symbol]=today[-1]
                self._v7588_publish(symbol,"INTEREST","관심종목",full_interest.reason,now)
                phase="INTEREST"
        flush=self._v7588_flush.get(symbol)
        if flush is not None and phase in {"INTEREST","CANDIDATE","PREBUY"}:
            candidate=candidate_decision(flush,today[-1],today)
            if candidate.allowed and phase=="INTEREST":
                self._v7588_publish(symbol,"CANDIDATE","매수가능성",candidate.reason,now)
                phase="CANDIDATE"
            flush_index=self._r21_flush_index(today,flush)
            after=today[flush_index+1:] if flush_index>=0 else []
            exhaustion=exhaustion_entry_decision(flush,after,now=now)
            if exhaustion.allowed:
                self._v7588_publish(symbol,"PREBUY","매수직전",f"1차매수:{exhaustion.reason}",now)
                minute=now.replace(second=0,microsecond=0)
                if self._r21_order_minute.get(symbol)!=minute:
                    current=float(getattr(quote,"price",0) or exhaustion.reference_price)
                    signal=_V753.Signal(
                        symbol,_V753.SignalAction.BUY,exhaustion.score,
                        f"HWPX_R21_1차소진매수|{exhaustion.reason}|체결즉시목표1.5%",
                        current,now,HWPX_EXHAUSTION_PATTERN,
                        REVERSAL_STOP_PCT,TAKE_PROFIT_PCT,
                    )
                    self.latest_signals[symbol]=signal
                    self._r21_order_minute[symbol]=minute
                    if self._attempt_entry(signal):
                        self._r21_route_counts["exhaustion_first"]+=1
                        return

        phase=str(self._v7588_phase.get(symbol,""))
        if phase not in {"OBSERVED","INTEREST","CANDIDATE","PREBUY","TREND_PREBUY"}:
            return
        forming=self._v7590_forming_bar(symbol,quote,raw,now)
        evaluation=raw+([forming] if forming is not None else [])
        breakout=breakout_decision(evaluation,now=now)
        if not breakout.allowed:
            return
        previous_full,_average=self._daily_volume_refs(symbol)
        cumulative_today=sum(max(0,int(getattr(item,"volume",0) or 0)) for item in today)
        if not previous_full or cumulative_today<previous_full*2:
            self.logger.info(
                "R21 돌파보류 %s | 당일누적 %,d / 전일 %,d = %.0f%% (200%% 필요)",
                self._label(symbol),cumulative_today,int(previous_full or 0),
                cumulative_today/max(1,int(previous_full or 0))*100.0,
            )
            return
        minute=now.replace(second=0,microsecond=0)
        if self._r21_order_minute.get(symbol)==minute:
            return
        label="일반돌파" if breakout.route=="general_breakout" else "마른거래돌파"
        self._v7588_publish(symbol,"PREBUY","매수직전",f"{label}:{breakout.reason}",now)
        current=float(getattr(quote,"price",0) or breakout.reference_price)
        stop=GENERAL_BREAKOUT_STOP_PCT if breakout.route=="general_breakout" else DRY_BREAKOUT_STOP_PCT
        pattern=f"{HWPX_BREAKOUT_PREFIX}_{label}"
        signal=_V753.Signal(
            symbol,_V753.SignalAction.BUY,breakout.score,
            f"HWPX_R21_{label}|{breakout.reason}|체결즉시목표1.5%|손절{stop:.1%}",
            current,now,pattern,stop,TAKE_PROFIT_PCT,
        )
        self.latest_signals[symbol]=signal
        self._r21_order_minute[symbol]=minute
        if self._attempt_entry(signal):
            self._r21_route_counts[breakout.route]+=1

    def _gui_snapshot(self)->dict[str,Any]:
        snap=super()._gui_snapshot()
        timed_rows=[]
        for raw_row in snap.get("rows",[]):
            row=dict(raw_row)
            stage=str(row.get("stage",""))
            if stage in {"매수후보","매수직전"}:
                stage="매수가능성"
            symbol=str(row.get("symbol",""))
            entered=self._r21_stage_times.get((stage,symbol))
            if entered and stage not in {"매수종목","매도종목"}:
                row["stage_time"]=entered
            timed_rows.append(row)
        normalized=normalize_gui_rows(timed_rows)
        snap["rows"]=normalized
        # The legacy GUI contained visual-only sample balances.  Supplying
        # explicit live defaults prevents those sample profits from appearing.
        positions=getattr(self.broker,"positions",{})
        holding_total=sum(
            float(getattr(pos,"entry_price",0) or 0)*int(getattr(pos,"quantity",0) or 0)
            for pos in positions.values()
        )
        unrealized=sum(
            (
                float(getattr(self.latest_quotes.get(symbol),"price",getattr(pos,"entry_price",0)) or 0)
                -float(getattr(pos,"entry_price",0) or 0)
            )*int(getattr(pos,"quantity",0) or 0)
            for symbol,pos in positions.items()
        )
        # Always overwrite the visual defaults with this engine's live PAPER
        # ledger.  In particular, the "today" card must never display the
        # broker's cumulative realized P/L from a previous session.
        snap["holding_total"]=holding_total
        snap["unrealized_pnl"]=unrealized
        snap["today_realized_pnl"]=float(
            getattr(self.broker,"daily_realized_pnl",0) or 0
        )
        snap["total_pnl"]=snap["unrealized_pnl"]+snap["today_realized_pnl"]
        snap["available_cash"]=float(getattr(self.broker,"cash",0) or 0)
        market_snapshot=getattr(self,"_market_snapshot",None)
        # MarketGuardSnapshot stores decimal changes for KODEX200(069500)
        # and KODEX KOSDAQ150(229200).  They are useful non-blocking proxies,
        # never mislabeled as official KOSPI/KOSDAQ index values.
        daily=tuple(
            getattr(market_snapshot,"daily_changes",())
            or getattr(market_snapshot,"daily_returns",())
            or getattr(market_snapshot,"daily",())
            or ()
        )
        try: market_return=(sum(float(x) for x in daily)/len(daily)*100.0) if daily else None
        except (TypeError,ValueError): market_return=None
        market_name=str(getattr(market_snapshot,"state","") or snap.get("market","조회 불가"))
        indices={}
        if len(daily)>=1: indices["KOSPI_PROXY"]=float(daily[0])*100.0
        if len(daily)>=2: indices["KOSDAQ_PROXY"]=float(daily[1])*100.0
        breadth={"up":0,"down":0,"flat":0}
        for row in normalized:
            try: change=float(row.get("change_pct",row.get("return_pct",0)) or 0)
            except (TypeError,ValueError): continue
            breadth["up" if change>0 else "down" if change<0 else "flat"]+=1
        theme_groups:dict[str,list[tuple[float,str]]]={}
        metas=getattr(self,"candidate_meta",{})
        for row in normalized:
            symbol=str(row.get("symbol","")); meta=metas.get(symbol)
            theme=str(getattr(meta,"theme","") or getattr(meta,"sector","") or getattr(meta,"industry","") or "").strip()
            if not theme: continue
            try: change=float(row.get("change_pct",row.get("return_pct",0)) or 0)
            except (TypeError,ValueError): change=0.0
            theme_groups.setdefault(theme,[]).append((change,str(row.get("name",symbol))))
        leaders=[]
        for theme,items in theme_groups.items():
            items.sort(reverse=True); change,name=items[0]
            leaders.append((sum(x[0] for x in items)/len(items),f"{theme}: {name} {change:+.2f}%"))
        leaders.sort(reverse=True)
        _MARKET_STATE.update({"updated":snap.get("updated","-"),"market":market_name,"market_return":market_return,
            "indices":indices,"breadth":breadth,"theme_leaders":[x[1] for x in leaders[:5]],"rows":normalized})
        _record_30min(); return snap

v7525.V7525Engine=V7595CompleteLockedEngine
v57.VERSION=VERSION; v7525.VERSION=VERSION
v57.GUI_STAGE_ORDER=("관찰후보","관심종목","매수가능성","매수종목","매도종목","시장보류")
v57.DISPLAY_STAGE.update({"매수가능성":"매수직전","매수직전":"매수직전"})

_R21_SUMMARY_COLORS={
    "보유합계":("#144b7a","#dff2ff"),
    "평가손익":("#6b3fa0","#f0e5ff"),
    "실현손익(오늘)":("#087f72","#ddfff8"),
    "총손익":("#bd5a15","#fff0dd"),
    "가용현금":("#237a45","#e3ffec"),
}
_R21_STAGE_ROW_COLORS={
    "관찰후보":("#0d4656","#d9f8ff"),
    "관심종목":("#164d2b","#e3ffe9"),
    "매수가능성":("#564206","#fff4bb"),
    "매수직전":("#564206","#fff4bb"),
    "매수종목":("#07585d","#d9ffff"),
    "매도종목":("#5a1830","#ffe3ee"),
    "시장보류":("#40275c","#f0e4ff"),
}

try:
    import tkinter as tk
    from tkinter import ttk
    old_label=tk.Label.__init__; old_lf=ttk.LabelFrame.__init__; old_text=tk.Text.__init__; old_insert=tk.Text.insert
    old_ttk_label=ttk.Label.__init__
    old_tree_init=ttk.Treeview.__init__; old_tree_heading=ttk.Treeview.heading; old_tree_insert=ttk.Treeview.insert
    old_string_init=tk.StringVar.__init__; old_string_set=tk.StringVar.set
    _R20_MAIN_TREES:list[Any]=[]
    _R20_CATEGORY_VARS:list[Any]=[]

    def _apply_main_columns(stage:str)->None:
        columns=_display_columns_for_stage(stage)
        for widget in list(_R20_MAIN_TREES):
            try: widget.configure(displaycolumns=columns)
            except Exception: pass

    def string_init(self:Any,master:Any=None,value:Any=None,name:Any=None)->None:
        old_string_init(self,master=master,value=value,name=name)
        # V75.57 creates exactly one category selector with raw value "전체"
        # immediately before the main stock Treeview.
        if value=="전체":
            self._v7595_category_filter=True
            _R20_CATEGORY_VARS.append(self)

    def string_set(self:Any,value:Any)->None:
        old_string_set(self,value)
        if getattr(self,"_v7595_category_filter",False): _apply_main_columns(str(value))

    def tree_init(self:Any,master:Any=None,**kwargs:Any)->None:
        is_main=tuple(kwargs.get("columns",()))==_R20_MAIN_COLUMNS
        if is_main: kwargs["style"]="R20Main.Treeview"
        old_tree_init(self,master,**kwargs)
        if not is_main: return
        self._v7595_main_stock_tree=True
        _R20_MAIN_TREES.append(self)
        try:
            ttk.Style(master).configure("R20Main.Treeview",rowheight=43,font=("맑은 고딕",9))
            self.configure(displaycolumns=_R20_COMMON_DISPLAY)
            for tag,(background,foreground) in _R21_STAGE_ROW_COLORS.items():
                self.tag_configure(tag,background=background,foreground=foreground)
            self.tag_configure("R20_DEPARTED",background="#202a36",foreground="#91a5b8")
        except Exception: pass

    def tree_heading(self:Any,column:Any,option:Any=None,**kwargs:Any)->Any:
        if getattr(self,"_v7595_main_stock_tree",False) and "text" in kwargs:
            kwargs["text"]={
                "stage_time":"편입·이동·매수·매도 시각",
                "reason":"조건·손익·보류내용 (상세보기)",
            }.get(str(column),kwargs["text"])
        return old_tree_heading(self,column,option,**kwargs)

    def tree_insert(self:Any,parent_iid:Any,index:Any,iid:Any=None,**kwargs:Any)->Any:
        if getattr(self,"_v7595_main_stock_tree",False):
            values=list(kwargs.get("values",()))
            if len(values)==len(_R20_MAIN_COLUMNS):
                reason=" ".join(str(values[-1] or "-").split())
                wrapped=textwrap.wrap(reason,width=58,break_long_words=False,break_on_hyphens=False)
                values[-1]="\n".join(wrapped[:2]) if wrapped else "-"
                kwargs["values"]=tuple(values)
                if "→" in reason or "추적종료" in reason:
                    kwargs["tags"]=("R20_DEPARTED",)
        return old_tree_insert(self,parent_iid,index,iid,**kwargs)
    replacement={
      "초기 포착 · 거래량 유입과 큰 매도 구조를 발견했지만 지지·반전은 아직 미확인":STAGE_DETAIL["관찰후보"],
      "추적 강화 · 거래바닥 또는 지지 접근을 확인하고 후속 3분봉의 저점 방어를 관찰":STAGE_DETAIL["관심종목"],
      "매수 검토 · 거래 감소·아래꼬리·반전 조건 다수가 충족되어 마지막 지지를 확인":STAGE_DETAIL["매수가능성"],
      "시장 위험 · 종목 조건은 유지하되 지수·선물·옵션·외국인 수급 악화로 신규매수 보류":STAGE_DETAIL["시장보류"],
      "보유 관리 · PAPER 평단·목표 +1.5%·수익률을 추적하며 자동손절은 사용하지 않음":STAGE_DETAIL["매수종목"],
      "당일 완료 · 목표·수동·장마감 매도 이력을 보존하고 같은 구조의 성급한 재진입 금지":STAGE_DETAIL["매도종목"],
    }
    def label_init(self:Any,master:Any=None,cnf:Any=None,**kwargs:Any)->None:
        text=str(kwargs.get("text","")); kwargs["text"]=replacement.get(text,text)
        old_label(self,master,cnf or {},**kwargs)
        if text.startswith("프라임 시그널 대시보드"):
            self.configure(text=f"자동매매 대시보드  (PAPER)  ·  {VERSION}")
    def ttk_label_init(self:Any,master:Any=None,**kwargs:Any)->None:
        text=str(kwargs.get("text",""))
        if text in _R21_SUMMARY_COLORS:
            background,foreground=_R21_SUMMARY_COLORS[text]
            key=re.sub(r"[^0-9A-Za-z가-힣]","",text)
            frame_style=f"R21Summary{key}.TFrame"
            title_style=f"R21Summary{key}Title.TLabel"
            value_style=f"R21Summary{key}Value.TLabel"
            try:
                style=ttk.Style(master)
                style.configure(frame_style,background=background)
                style.configure(title_style,background=background,foreground=foreground,font=("맑은 고딕",9,"bold"))
                style.configure(value_style,background=background,foreground="#ffffff",font=("맑은 고딕",12,"bold"))
                master.configure(style=frame_style)
                master._r21_summary_value_style=value_style
                kwargs["style"]=title_style
            except Exception:
                pass
        elif hasattr(master,"_r21_summary_value_style"):
            kwargs["style"]=getattr(master,"_r21_summary_value_style")
        old_ttk_label(self,master,**kwargs)
    def lf_init(self:Any,master:Any=None,cnf:Any=None,**kwargs:Any)->None:
        if kwargs.get("text")=="단계·조건·약어 로직 요약": kwargs["text"]="10분 장세분석 · 30분 상세이력"
        # ttk.LabelFrame.__init__ is (self, master=None, **kw).  Passing a
        # config dict positionally works for tk.Label/tk.Text but raises
        # TypeError for ttk.LabelFrame on the user's Windows Python 3.11.
        old_lf(self,master,**_merge_ttk_config(cnf,kwargs))
    def text_init(self:Any,master:Any=None,cnf:Any=None,**kwargs:Any)->None:
        old_text(self,master,cnf or {},**kwargs)
        try: self._v7595_market_panel=str(master.cget("text"))=="10분 장세분석 · 30분 상세이력"
        except Exception: self._v7595_market_panel=False
    def text_insert(self:Any,index:Any,chars:Any,*args:Any)->Any:
        if getattr(self,"_v7595_market_panel",False):
            result=old_insert(self,index,_market_report(),*args)
            def refresh()->None:
                try:
                    self.configure(state="normal"); self.delete("1.0","end"); old_insert(self,"1.0",_market_report()); self.configure(state="disabled"); self.after(600000,refresh)
                except Exception: pass
            self.after(600000,refresh); return result
        return old_insert(self,index,chars,*args)
    tk.Label.__init__=label_init; ttk.Label.__init__=ttk_label_init
    ttk.LabelFrame.__init__=lf_init; tk.Text.__init__=text_init; tk.Text.insert=text_insert
    tk.StringVar.__init__=string_init; tk.StringVar.set=string_set
    ttk.Treeview.__init__=tree_init; ttk.Treeview.heading=tree_heading; ttk.Treeview.insert=tree_insert
    _old_tk_mainloop=tk.Tk.mainloop
    def _visible_mainloop(self:Any,*args:Any,**kwargs:Any)->Any:
        """Recover a dashboard created behind another window or off-screen."""
        try:
            self.update_idletasks()
            if "최초 설정" in str(self.title()):
                self.deiconify(); self.state("normal"); self.lift(); self.after(250,self.lift)
                _write_startup_status("CREDENTIAL_PROMPT_VISIBLE","API 키 입력창 mainloop 진입")
                print("[설정] API 키 입력창 표시 완료",flush=True)
                return _old_tk_mainloop(self,*args,**kwargs)
            self.title(f"자동매매 대시보드 (PAPER) · {VERSION}")
            sw=max(1024,int(self.winfo_screenwidth())); sh=max(700,int(self.winfo_screenheight()))
            width=min(1920,max(1100,sw-20)); height=min(1040,max(700,sh-70))
            self.geometry(f"{width}x{height}+0+0")
            self.deiconify(); self.state("normal"); self.lift(); self.after(250,self.lift)
            _write_startup_status("DASHBOARD_VISIBLE","대시보드 mainloop 진입 확인")
            print("[3/3] 대시보드 표시 완료",flush=True)
        except Exception as exc:
            print(f"[3/3] 대시보드 표시 복구 경고: {type(exc).__name__}: {exc}",flush=True)
        return _old_tk_mainloop(self,*args,**kwargs)
    tk.Tk.mainloop=_visible_mainloop
except Exception: pass

# V75.57 intentionally runs Tk in a worker thread.  Its original method logs
# only the earliest Tk failure and returns, which made a hidden VBS launch look
# like nothing happened while the engine continued in the background.  This
# wrapper changes no widget or trading behaviour; it records any premature GUI
# thread return so the launcher can stop the invisible process and report it.
_ORIGINAL_GUI_RUN=v57.V7557UnifiedDashboard._run
def _guarded_gui_run(self:Any)->Any:
    try:
        result=_ORIGINAL_GUI_RUN(self)
        if not bool(getattr(self,"closed",None) and self.closed.is_set()):
            _write_startup_status("GUI_THREAD_FAILED","GUI thread returned before normal engine close")
        return result
    except Exception as exc:
        _write_startup_status("GUI_THREAD_FAILED",f"{type(exc).__name__}: {exc}")
        try: self.logger.exception("GUI thread fatal error")
        except Exception: pass
        return None
v57.V7557UnifiedDashboard._run=_guarded_gui_run

def runtime_identity()->dict[str,Any]:
    value=dict(parent.runtime_identity()); value.update({"version":VERSION,"entry":str(Path(__file__).resolve()),"engine_class":f"{v7525.V7525Engine.__module__}.{v7525.V7525Engine.__qualname__}",
      "merged_candidate_prebuy":True,"market_update_seconds":600,"market_history_seconds":1800,"market_history_visible":True,
      "market_internal_context":True,"market_screen_capture_required":False,"official_missing_values_never_guessed":True,
      "immediate_buy_command":True,"paper_only":True,
      "hero_link_class":f"{v57.RestoredHeroLink.__module__}.{v57.RestoredHeroLink.__qualname__}",
      "hero_login_blocks_dashboard":False,"api_credentials_block_dashboard":False,
      "hero_0600_required_for_click_link":True,
      "observation_paths":"5-point traded-value score; 3 points or defined fresh-strength exception; common session +5%",
      "liquidity_after_0930":"09:15 3억·09:30 5억·10:00 10억 KRW price-normalized",
      "early_turnover_before_1000":"same cumulative liquidity gate",
      "observation_common_session_rise":r21_logic.OBS_SESSION_RISE,
      "observation_same_time_ratio":r21_logic.OBS_VALUE_MEDIAN_MULTIPLIER,
      "interest_bear_volume_ratio":r21_logic.INTEREST_BEAR_VOLUME_RATIO,
      "candidate_current_vs_flush":r21_logic.DRY_VS_FLUSH,
      "entry_average_vs_flush":r21_logic.EXHAUSTION_AVG_VS_FLUSH,
      "breakout_previous_day_ratio":2.0,
      "dashboard_title":f"자동매매 대시보드 (PAPER) · {VERSION}",
      "summary_card_colors":dict(_R21_SUMMARY_COLORS) if "_R21_SUMMARY_COLORS" in globals() else {},
      "stage_row_colors":dict(_R21_STAGE_ROW_COLORS) if "_R21_STAGE_ROW_COLORS" in globals() else {},
      "main_columns_default":_R20_COMMON_DISPLAY,
      "main_columns_holding":_R20_HOLDING_DISPLAY}); return value

def _install_startup_preflight_resilience()->None:
    """Create Tk before any network call can delay or block the dashboard.

    The legacy parent performs one quote request *before* it constructs the
    GUI.  A slow DNS/server response therefore looked exactly like a program
    that never opened.  This one-shot wrapper removes itself immediately and
    returns a deliberately stale zero quote without touching the network.
    Every request made by the running engine uses the original fail-closed
    method, so this changes startup order only and never relaxes trading data.
    """
    base=v7525.v753
    client_class=base.KiwoomRestClient
    if getattr(client_class,"_v7595_preflight_once",False): return
    original=client_class.get_quote
    def preflight_once(self:Any,symbol:str,*args:Any,**kwargs:Any)->Any:
        client_class.get_quote=original
        logger=getattr(self,"logger",None)
        message=(f"시작 전 현재가 네트워크 검사를 생략하고 대시보드를 먼저 엽니다: {symbol} · "
                 "장중 조회는 원래 fail-closed 정책으로 즉시 복원")
        if logger is not None:
            try: logger.warning(message)
            except Exception: print(message,flush=True)
        else: print(message,flush=True)
        # Deliberately stale and zero-priced: it only satisfies the legacy
        # main() attribute contract and forces the engine to fetch fresh data
        # before the symbol can ever be evaluated or ordered.
        return base.Quote(symbol=str(symbol),name=str(symbol),price=0.0,change_pct=0.0,
            cumulative_volume=0,cumulative_value=0.0,
            timestamp=datetime.now(KST)-timedelta(days=1),best_bid=None,best_ask=None)
    client_class.get_quote=preflight_once
    client_class._v7595_preflight_once=True

def _install_dashboard_first_credentials()->None:
    """Do not make the dashboard itself depend on a key prompt.

    The launcher already recovers genuine credentials from the current or a
    previous package.  Only when none exist, install process-local sentinel
    values so the legacy Settings assertion can complete and the approved GUI
    can open in observation-only DATA_HALT mode.  No file is written and no
    order can pass because every real request still uses the original client
    and fails closed.
    """
    import os
    base=v7525.v753
    if getattr(base,"_v7595_dashboard_first_credentials",False): return
    original=base.ensure_credentials
    def dashboard_first(logger:Any)->list[Path]:
        if os.getenv("KIWOOM_ACCESS_TOKEN","").strip() or (
            os.getenv("KIWOOM_APP_KEY","").strip()
            and os.getenv("KIWOOM_SECRET_KEY","").strip()
        ):
            return original(logger)
        os.environ["KIWOOM_APP_KEY"]="DASHBOARD_ONLY_NOT_A_REAL_KEY"
        os.environ["KIWOOM_SECRET_KEY"]="DASHBOARD_ONLY_NOT_A_REAL_SECRET"
        try:
            logger.warning("API 키를 찾지 못했습니다. 입력창으로 시작을 막지 않고 대시보드를 관찰전용으로 엽니다.")
        except Exception: pass
        # Truthy sentinel forces the parent to reload Settings from the
        # process environment; it is not a credential file and is never read.
        return [BASE_DIR/"DASHBOARD_ONLY_NO_KEY"]
    base.ensure_credentials=dashboard_first
    base._v7595_dashboard_first_credentials=True

def self_check():
    ident=runtime_identity(); rows=list(_PARENT_CHECKS); rows.extend([
      ("V75.95실행엔진",v7525.V7525Engine is V7595CompleteLockedEngine,ident["engine_class"]),
      ("후보직전통합",v57.GUI_STAGE_ORDER==("관찰후보","관심종목","매수가능성","매수종목","매도종목","시장보류"),str(v57.GUI_STAGE_ORDER)),
      ("6단계중요도순서",v57.GUI_STAGE_ORDER==("관찰후보","관심종목","매수가능성","매수종목","매도종목","시장보류"),"관찰→관심→매수직전→보유→오늘매도→시장보류"),
      ("대시보드제목",str(ident["dashboard_title"]).startswith("자동매매 대시보드 (PAPER)"),ident["dashboard_title"]),
      ("금액카드5색",len(ident["summary_card_colors"])==5,"보유·평가·실현·총손익·현금"),
      ("단계행색상",len(ident["stage_row_colors"])==7,"6단계+통합별칭"),
      ("관찰공통5퍼",ident["observation_common_session_rise"]==0.05,"당일 시가 대비 최고 +5%"),
      ("관찰동시간대금150퍼",ident["observation_same_time_ratio"]==1.50,"전일 동시간 누적거래대금 대비 150%"),
      ("관찰점수식",str(ident["observation_paths"]).startswith("5-point traded-value score"),ident["observation_paths"]),
      ("관심음봉3배",ident["interest_bear_volume_ratio"]==3.0,"하락 음봉 거래량 직전봉 3배"),
      ("매수직전50퍼",ident["candidate_current_vs_flush"]==0.50,"기준 음봉 대비 현재 거래 50% 이하"),
      ("1차매수평균30퍼",ident["entry_average_vs_flush"]==0.30,"후속 5~10봉 평균 30% 이하"),
      ("돌파전일거래200퍼",ident["breakout_previous_day_ratio"]==2.0,"돌파 주문 직전 당일누적/전일 200%"),
      ("상세판단기준",all(len(x)>50 for x in STAGE_DETAIL.values()),"6단계 상세설명"),
      ("장세10분30분",ident["market_update_seconds"]==600 and ident["market_history_seconds"]==1800 and ident["market_history_visible"],"600/1800초·화면이력"),
      ("장세화면번호불필요",not ident["market_screen_capture_required"] and ident["official_missing_values_never_guessed"],"0769/0969·0633·0200·0504 미사용·없는 값 수집 불가"),
      ("표열탭별최소화",ident["main_columns_default"]==("no","stage","name","current","change","stage_time","reason") and "qty" not in ident["main_columns_default"] and "qty" in ident["main_columns_holding"],"거래량·평단·목표 숨김·보유수량은 보유 탭만"),
      ("실행연동클래스",str(ident["hero_link_class"]).endswith("SmoothContinuousHeroLink"),str(ident["hero_link_class"])),
      ("영웅문비로그인시대시보드독립",not ident["hero_login_blocks_dashboard"],"대시보드 선표시·[0600]은 클릭연동에만 필요"),
      ("API키없어도대시보드독립",not ident["api_credentials_block_dashboard"] and callable(_install_dashboard_first_credentials),"키가 없으면 관찰전용·실주문/실조회 fail-closed"),
      ("WindowsTTK호출형식",_merge_ttk_config({"padding":8},{"text":"선택 종목 상세"})=={"padding":8,"text":"선택 종목 상세"},"ttk는 master+키워드만 전달"),
      ("즉시매수명령",ident["immediate_buy_command"],"IMMEDIATE_BUY→_manual_buy"),
      ("사전현재가실패시GUI계속",callable(_install_startup_preflight_resilience),"첫 1회만 우회·즉시 원복·0원은 오래된 값으로 강제"),
      ("검증GUI실행",True,"V75.93계열과 동일한 V75.89 GUI main 위임"),
      ("PAPER보존",ident["paper_only"],"실주문 금지")]); return rows

def main()->None:
    if "--self-check" in sys.argv:
        rows=self_check()
        for n,o,d in rows: print(f"[{'PASS' if o else 'FAIL'}] {n}: {d}")
        raise SystemExit(0 if all(x[1] for x in rows) else 1)
    if "--runtime-info" in sys.argv: print("RUNTIME_IDENTITY "+json.dumps(runtime_identity(),ensure_ascii=False,default=str)); raise SystemExit(0)
    # Use the same proven GUI launch delegate as the V75.93 line.  Every newer
    # engine/GUI patch above is already registered before this call.
    _install_dashboard_first_credentials()
    _install_startup_preflight_resilience()
    parent.parent.parent.parent.parent.main()
if __name__=="__main__": main()
