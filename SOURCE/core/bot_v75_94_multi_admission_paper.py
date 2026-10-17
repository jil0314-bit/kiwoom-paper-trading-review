#!/usr/bin/env python3
from __future__ import annotations
from datetime import datetime
import hashlib, importlib.util, json, sys, time
from pathlib import Path
from types import ModuleType
from typing import Any

VERSION="V75.94-MULTI-ADMISSION-PAPER"
BASE_DIR=Path(__file__).resolve().parent
PARENT_FILE="bot_v75_93_semantic_arbiter_paper.py"
PARENT_SHA256="3b76570ad8c69d6d018df19c83e0e23456400ea63c32a91e57654b74d589a972"

def _load_parent()->ModuleType:
    path=BASE_DIR/PARENT_FILE; actual=hashlib.sha256(path.read_bytes()).hexdigest()
    if actual!=PARENT_SHA256: raise RuntimeError(f"{PARENT_FILE} SHA256 불일치")
    spec=importlib.util.spec_from_file_location("v7594_immutable_v7593_base",path)
    if spec is None or spec.loader is None: raise RuntimeError("V75.93 로드 실패")
    module=importlib.util.module_from_spec(spec); sys.modules[spec.name]=module; spec.loader.exec_module(module); return module

parent=_load_parent()
from v7594_observation_admission_logic import evaluate_observation_admission
v7525,v57,KST=parent.v7525,parent.v57,parent.KST
_PARENT_CHECKS=tuple(parent.self_check())

class V7594MultiAdmissionEngine(parent.V7593SemanticArbiterEngine):
    def __init__(self,*args:Any,**kwargs:Any)->None:
        self._v7594_daily_volume_cache:dict[str,tuple[float,int|None,float|None]]={}
        super().__init__(*args,**kwargs)

    def _daily_volume_refs(self,symbol:str)->tuple[int|None,float|None]:
        cached=self._v7594_daily_volume_cache.get(symbol)
        if cached and time.monotonic()-cached[0]<6*3600: return cached[1],cached[2]
        try:
            rows=self.client.get_daily_bars(symbol,limit=12)
            completed=[x for x in rows if getattr(x,"trading_date",None)<datetime.now(KST).date()]
            volumes=[int(getattr(x,"volume",0) or 0) for x in completed if int(getattr(x,"volume",0) or 0)>0]
            previous=volumes[-1] if volumes else None
            average=sum(volumes[-10:])/len(volumes[-10:]) if volumes else None
        except Exception:
            previous=average=None
        self._v7594_daily_volume_cache[symbol]=(time.monotonic(),previous,average)
        return previous,average

    def _refresh_actual_bars(self,symbol:str,quote:Any)->None:
        now=datetime.now(KST)
        raw=sorted(list(getattr(self.client,"v7580_raw_cache",{}).get(symbol,[])),key=parent.stamp)
        if not self._v7588_phase.get(symbol) and len(raw)>=2:
            previous,average=self._daily_volume_refs(symbol)
            evidence=evaluate_observation_admission(raw,now=now,previous_full_volume=previous,average_10d_volume=average)
            if evidence.allowed:
                self._v7588_publish(symbol,"OBSERVED","관찰후보",f"V7594:{evidence.reason}",now)
        super()._refresh_actual_bars(symbol,quote)

v7525.V7525Engine=V7594MultiAdmissionEngine
v57.VERSION=VERSION; v7525.VERSION=VERSION
try:
    import tkinter as tk
    _inherited_label_init=tk.Label.__init__
    def _v7594_label_init(self:Any,master:Any=None,cnf:Any=None,**kwargs:Any)->None:
        is_title=str(kwargs.get("text","")).startswith("프라임 시그널 대시보드")
        _inherited_label_init(self,master,cnf or {},**kwargs)
        if is_title: self.configure(text=f"프라임 시그널 대시보드  (PAPER)  ·  {VERSION}")
    tk.Label.__init__=_v7594_label_init
except Exception:
    pass

def runtime_identity()->dict[str,Any]:
    value=dict(parent.runtime_identity()); value.update({"version":VERSION,"entry":str(Path(__file__).resolve()),
      "engine_class":f"{v7525.V7525Engine.__module__}.{v7525.V7525Engine.__qualname__}",
      "observation_paths":"6 independent OR","liquidity_after_0930":"100k shares AND 1bn KRW","paper_only":True}); return value

def self_check():
    rows=list(_PARENT_CHECKS); ident=runtime_identity(); rows.extend([
      ("V75.94실행엔진",v7525.V7525Engine is V7594MultiAdmissionEngine,ident["engine_class"]),
      ("관찰6경로OR",ident["observation_paths"]=="6 independent OR",ident["observation_paths"]),
      ("0930공통AND",ident["liquidity_after_0930"]=="100k shares AND 1bn KRW",ident["liquidity_after_0930"]),
      ("PAPER보존",ident["paper_only"],"실주문 금지")]); return rows

def main()->None:
    if "--self-check" in sys.argv:
        rows=self_check()
        for name,ok,detail in rows: print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
        raise SystemExit(0 if all(x[1] for x in rows) else 1)
    if "--runtime-info" in sys.argv: print("RUNTIME_IDENTITY "+json.dumps(runtime_identity(),ensure_ascii=False,default=str)); raise SystemExit(0)
    parent.parent.parent.parent.main()

if __name__=="__main__": main()
