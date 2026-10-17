# 교차검증 요청서 — STAGE-LAG-SCROLL-002 (2026-09-11)

아래 V76 PAPER 실행본의 **2026-09-11 변경**을 교차검증해 주세요.
이 문서는 요청서일 뿐 정답 근거가 아닙니다. 실제 파일·실행 경로·로그를 직접 읽고
추측 없이 `통과 / 실패 / 확인불가`로 판정해 주세요.
**분석만 하고 파일·설정·runtime 장부·ZIP을 수정하지 마세요.**

---

## 1. 대상과 실제 실행 경로

- 실행 폴더: `C:\Users\MAIN\Documents\자동매매 코딩\V76_09_15개수정보완_0600연동유지_PAPER`
- 실행 시작: `3_START_V76_GUI.vbs`
- 실행 체인: `3_START_V76_GUI.vbs` → `SOURCE\launcher.py` → `SOURCE\core\bot_v76_00_three_stage_ctrlv_paper.py`
- 기준 manifest: `SOURCE\chain_manifest.json`
- 확인 명령: `C:\Python311_32\python.exe -B SOURCE\chain_manifest.py`
- 현 표식: `V76.09-FOREGROUND-APP-ACTIVATE-0600-LINK-PAPER`, 125 files, 체인 OK
- 변경 전 비교본: `V7609_SESSION_HANDOFF_FULL_VERIFY.zip`
  (압축 해제하지 말고 읽기 전용 비교. 단, 이 ZIP은 **2026-09-10 변경 이전** 상태입니다.
  이번 변경만 보려면 `git diff`와 아래 변경 파일 목록을 함께 쓰세요.)

## 2. 이번(09-11) 변경 파일

1. `SOURCE\core\bot_v76_00_three_stage_ctrlv_paper.py`
2. `SOURCE\core\v7609_trade_view.py`
3. `tests\test_support_exit.py`
4. `tests\test_trade_view_tk.py`
5. `SPEC.md`
6. `SOURCE\chain_manifest.json`

`3_START_V76_GUI.vbs`, `SOURCE\launcher.py`, `V7600CtrlVHeroLink`(0600 전송),
`V7609OneMinuteKiwoomClient`(REST 시세/계좌), `v7609_exit_policy.py`, `v7609_volume_prebuy.py`,
손절·익절 비율, 주문 수량, 계좌 가드는 **변경 대상이 아닙니다.** 실제 해시/디프로 확인해 주세요.

## 3. 이번에 구현했다고 주장하는 내용

### A. 관찰·관심 편입이 0으로 굳는 문제

- 관측 사실(2026-09-11 로그): 종일 `관심=0 관찰=0`, `매수가능성`만 8 → 144 → 155로 증가.
- 주장하는 원인: `V7600ThreeStageEngine._refresh_actual_bars`의 신선도 관문
  `_v7609_latest_completed_bar_is_fresh`가 **"최신 완성봉 == 직전 1분"** 을 정확히 요구한다.
  200종목 순환 조회(HOT 20/3초, 일반 15초)에서는 대부분 이 조건을 못 맞춰 차단된다
  (당일 `STALE_COMPLETED_BAR` 1,919건).
  5배 거래량 경로 `_v7609_try_volume_prebuy`는 이 관문보다 **먼저** 실행되어 영향을 받지 않는다.
  그래서 매수가능성만 계속 쌓이고 관찰/관심은 굶는다.
- 조치: 관문 자체는 그대로 두고, 차단된 경우에만 새 메서드
  `_v7609_stage_only_admission(symbol, raw, now)`로 **표시 단계(관찰/관심)** 판정만 허용.
  - 허용 지연: `V7609_STAGE_BAR_MAX_LAG_MIN = 3` (완성 1분봉 기준 최대 3분)
  - `_v7609_completed_bar_lag_minutes`가 음수(형성중/미래봉)이면 사용하지 않음
  - 보유 종목·`_v7587_excluded`·`pending_entries`는 즉시 제외
  - 이미 `관심` 이상인 종목의 flush 기준은 지연봉으로 재설정하지 않음
  - 편입 사유에 `완성봉지연N분`을 남김
  - 주문·PREBUY·분할매수·청산은 호출하지 않음
- 주문 경로의 신선도 기준은 **바꾸지 않았다고 주장**합니다.

### B. 통합표 매도 칸 스크롤

- 증상: `오늘 매수·매도` 통합표의 매도 칸이 10종목만 보이고 나머지를 볼 수 없었음.
- 조치: 페이지 버튼 방식 → **세로 스크롤 방식**.
  표시 높이 10행 유지, 모든 행을 Treeview에 넣고 세로/가로 스크롤바 + 휠 + PageUp/PageDown.
  순번은 전체 기준. `page_rows()` 함수는 기존 검사 호환을 위해 남겨둠.

### C. 부수 수정 2건

- 같은 종목을 당일 2회 매도하면 통합표에서 한 줄만 남던 문제.
  행 식별자를 `row_key(stage, row, index)`로 바꿔 매도일·매도시각·매도회차를 포함.
  보유 행은 기존대로 종목코드 기준(시세 갱신 시 행 재사용).
- Enter 명령이 **종목 없는** 즉시매수/매도를 큐에 넣을 수 있던 문제.
  기존 버튼(`submit_simple`)과 동일하게 거부.

---

## 4. 반드시 검증할 질문

1. `_v7609_stage_only_admission`이 정말로 주문 경로에 도달하지 못하는지 **호출 그래프 전체**로 확인해 주세요.
   `_v7588_publish("INTEREST", ...)`가 내부적으로 진입/주문/PREBUY를 유발하지 않는지,
   `self._v7588_flush[symbol] = flush` 설정이 이후 **정상 경로의 진입 판정을 바꾸는지** 판단해 주세요.
   바꾼다면 그것이 허용 가능한 범위인지, 아니면 실패로 볼지 명시해 주세요.
2. 지연 3분 허용이 "형성중 봉·미래 봉·중복 봉" 사용으로 새는 곳이 없는지 확인해 주세요.
   `_v7609_completed_bar_lag_minutes`와 `pattern_bars`의 조합으로 충분한지,
   `v7609_exit_policy.completed_bars` 수준의 중복/이상치 검증이 필요한지 판단해 주세요.
3. 지연봉으로 편입된 `관심`이 이후 `매수가능성`·실제 매수로 이어질 때,
   기존 90초 재검증(V75.44)·`_v7609_current_prebuy_approved`·`_v7609_early_order_allowed`가
   여전히 최신 데이터를 요구하는지 확인해 주세요. 요구하지 않는 경로가 있으면 실패로 처리해 주세요.
4. `V7609_STAGE_BAR_MAX_LAG_MIN = 3`이 적절한지 실제 로그의 지연 분포로 검증해 주세요.
   (`SOURCE\v75_25_daily_logs\V75_25_2026-09-11.txt`의 `STALE_COMPLETED_BAR actual=/expected=` 쌍)
   3분으로 회복되는 비율과, 값을 키울 때 늘어나는 위험을 숫자로 제시해 주세요.
5. `TradeView`의 스크롤 전환이 원본 Treeview·카테고리 필터와 충돌하지 않는지,
   반복 갱신(300ms)·선택 유지·Enter·화살표에서 **중복 0600 전송/예외/Tk 스레드 문제**가 없는지 확인해 주세요.
6. `row_key`가 실제 `_gui_snapshot` 매도 행의 키(`sell_date`·`sell_time`·`sell_cycle`)와 일치하는지,
   그 키들이 항상 채워지는지 실제 코드로 확인해 주세요. 비는 경우의 동작을 판단해 주세요.
7. Enter 명령의 종목 식별이 안전한지 재확인해 주세요.
   특히 `known_names`가 **종목명 → 코드 dict**라서 동일 표시명이 겹치면 마지막 값만 남는 문제,
   그리고 `'내일 매수해'`가 여전히 `IMMEDIATE_BUY`로 해석되는 문제(아래 8번)를 평가해 주세요.
8. 아래 "이미 확인된 미해결 항목"이 실제로 미해결인지, 위험도 순서가 맞는지 판정해 주세요.

---

## 5. 이미 수행된 로컬 검사 — 재검증 필요

| 검사 | 명령 | 결과 |
|---|---|---|
| manifest | `C:\Python311_32\python.exe -B SOURCE\chain_manifest.py` | `체인 OK(125개)` |
| 문법 | `ast.parse` (SOURCE+tests) | 통과 |
| 전체 | `python -B -m unittest tests.test_*` | **222건 중 1건 실패** |
| 숨김 Tk | `V7609_TEST_TK=1 python -B -m unittest tests.test_trade_view_tk` | 6건 통과 |
| self-check | `python -B SOURCE/core/bot_v76_00_three_stage_ctrlv_paper.py --self-check` | **1건 실패** |

- 전체 검사 실패 1건은 `tests.test_replay_002.Replay002InputAudit.test_actual_execution_path_declares_the_missing_inputs`입니다.
  **변경 전에도 실패**했음을 ZIP 원본을 별도 폴더에 풀어 실행해 직접 확인했습니다. 재확인해 주세요.
- self-check 실패 1건은 `단계경로실제시각순`입니다. 변경 전·후 모두 실패함을 확인했습니다. 재확인해 주세요.
- 새로 추가된 self-check 행 `V7609지연봉표시단계만`은 통과합니다.
- 실제 키움 0600 화면, 실시간 시세, 실계좌 주문, 수익성은 **검증하지 않았습니다.**

## 6. 운영상 반드시 확인할 사항 (이번에 발견)

`SOURCE\chain_manifest.json`이 실제 파일 해시와 불일치하면
**대시보드가 축소 경로로 열려 통합표와 설명 라벨이 아예 나타나지 않습니다.**
(파일 1개만 바꿔도 재현됩니다. `gui.tree`조차 생성되지 않습니다.)
파일 수정 후 매니페스트를 재생성하지 않으면 "수정이 반영되지 않은 것처럼" 보입니다.
이 동작이 의도된 fail-closed인지, 아니면 조용한 기능 상실인지 판정해 주세요.

## 7. 이미 확인된 미해결 항목 (이번 변경 범위 밖, 판정 요청)

1. **실제 체결 손실이 -3%를 넘음.** 2026-09-11 기준 한켐 -3.66%, 아바텍 -3.48%.
   `-3.0%`는 폴링된 시세에 대한 **판정 임계값**이고 체결은 시장가(호가)입니다.
   시세가 임계값을 이미 지나친 뒤 관측되면 그만큼 더 손실이 납니다.
   "최대손실 3%" 요구와 충돌하는지, 충족하려면 무엇이 필요한지 판정해 주세요.
2. **분할매수가 지지 기준선을 낮춤.**
   `v7516.paper_add_to_position`이 `position.entry_time = now`로 갱신하고,
   `v7609_exit_policy.support_snapshot`은 `entry_time`을 상태 키로 씁니다.
   따라서 추가매수 때 40봉 저점이 **더 낮은 값으로 다시 계산**됩니다.
   SPEC의 "여러 번 매수해도 같은 보유 사이클의 기준은 낮추지 않는다"와 충돌하는지 판정해 주세요.
3. **`'내일 매수해'`가 즉시매수로 해석됨.**
   조건문·부정문 차단 정규식이 `하면/되면/일때/충족/조건`만 보고 미래시제를 보지 않습니다.
4. **`ma_broken` 플래그가 읽히기만 하고 쓰이지 않음** (`v7609_exit_policy.support_snapshot`).
5. **정적 `_v7609_exit_prices(entry_price)`가 비용을 무시**합니다(기본값 commission=0, tax=0).
   현재 실행 경로는 `_v7609_position_exit_prices`만 쓰지만, 검사에서는 정적 쪽을 참조합니다.
6. **`_enforce_gem_orders`의 밴드 검사가 넓어짐**: 기존 `±0.1%p` → 현재 `0 < loss_pct < 3`.
   저가 종목에서 순손절이 -1.07%까지 얕아져도 경고가 나오지 않습니다(예: 매수가 116원).

## 8. 결과 형식

각 질문마다 아래 표로 답해 주세요.

| 항목 | 통과/실패/확인불가 | 실제 파일·함수·줄 | 근거 | 최소 수정 제안 |
|---|---|---|---|---|

마지막에는 아래만 짧게 정리해 주세요.

1. PAPER에서 즉시 막아야 할 위험 0~3개
2. 이번 변경이 아닌 기존 실패로 확인된 항목
3. 실제 키움 PC에서만 확인 가능한 항목
