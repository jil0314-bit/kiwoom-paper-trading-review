# V76 자동매매 — 새 세션 인수인계

작성일: 2026-09-10  
프로젝트: `C:/Users/MAIN/Documents/자동매매 코딩/V76_09_15개수정보완_0600연동유지_PAPER`

## 새 세션에서 먼저 읽을 파일

1. `SPEC.md` — 유일한 확정 명세
2. `SOURCE/core/bot_v76_00_three_stage_ctrlv_paper.py` — 실제 실행 엔진
3. `SOURCE/core/v7595_r21_hwpx_logic.py` — 부모 로직
4. `SOURCE/core/v7600_state_manager.py` — 단계 전이
5. `SOURCE/core/v7609_account_guard.py` — 0311 계좌 보호
6. `SOURCE/core/v7609_volume_prebuy.py` — 거래량 매수직전 규칙
7. `tests/test_link_account_order.py` — 0600 연동 검사
8. `tests/test_early_volume_batch.py` — 조기거래·손절 검사
9. `tests/test_four_route_fixes.py` — 기존 오류 회귀검사
10. `SOURCE/chain_manifest.json` — 파일 해시 기준

## 현재 작업 기준

- 최신 엔진 표식: 2026-09-09 15:31
- 0600 연동: 계좌 ComboBox가 보이지 않아도, REST에서 인증된 0311 계좌와 정확한 0600 종목코드 Edit를 분리해 표시 전송하도록 수정됨.
- 2000 주문창·전역 키보드 우회는 사용하지 않음.
- 손절 기준: 평단 기준 1.5% 근처 유효 호가.
- 익절·매수·수량·VBS·대시보드 디자인은 변경하지 않는 것이 원칙.
- 실제 Kiwoom 화면에서 연동 성공 여부는 아직 사용자 재실행 확인 전. 성공으로 단정하지 말 것.
- PAPER 전용이며 실제 주문 API 전송 여부는 별도 확인 대상.

## 현재 Git 상태

기존 작업이 여러 개 미커밋 상태다. 새 세션에서 전체 초기화·reset·checkout을 하지 말 것.

확인 명령:

```powershell
cd 'C:\Users\MAIN\Documents\자동매매 코딩\V76_09_15개수정보완_0600연동유지_PAPER'
git status --short
git diff -- SOURCE/core/bot_v76_00_three_stage_ctrlv_paper.py SOURCE/core/v7595_r21_hwpx_logic.py SOURCE/core/v7600_state_manager.py SPEC.md
```

최근 커밋은 참고용이며, 현재 작업 기준은 실제 working tree와 `SPEC.md`다.

## 수정 원칙

이번 새 요청도 한 번에 한 변경만 처리한다.

- 매매 조건·주문 조건·대시보드 디자인·VBS·0600 연동을 임의로 섞지 않는다.
- 먼저 실제 호출 경로와 로그를 읽는다.
- 추측으로 기대값을 바꾸지 않는다.
- 실제 PC 검증과 문법/회귀검사를 구분한다.
- 수정 전후 `git diff`를 확인한다.
- 토큰·계좌번호·로그·runtime 장부를 새 파일에 저장하지 않는다.

## 관련 검사 명령

문법검사:

```powershell
$env:PYTHONIOENCODING='utf-8'
& 'C:\Python311_32\python.exe' -B -c "import ast,pathlib; [ast.parse(pathlib.Path(p).read_text(encoding='utf-8-sig')) for p in ['SOURCE/core/bot_v76_00_three_stage_ctrlv_paper.py','tests/test_link_account_order.py','tests/test_early_volume_batch.py','tests/test_four_route_fixes.py']]; print('SYNTAX OK')"
```

0600 연동 검사:

```powershell
& 'C:\Python311_32\python.exe' -B -m unittest tests.test_link_account_order -v
```

관련 PAPER 회귀검사:

```powershell
& 'C:\Python311_32\python.exe' -B -m unittest tests.test_early_volume_batch tests.test_four_route_fixes -v
```

manifest:

```powershell
& 'C:\Python311_32\python.exe' -B SOURCE/chain_manifest.py
```

## 새 세션 첫 요청문

아래 문장을 그대로 붙여 넣으면 된다.

> `SESSION_HANDOFF.md`와 위에 지정된 `SPEC.md` 및 실제 파일을 먼저 읽어줘. 현재 working tree의 변경은 사용자 작업이므로 reset하지 마. 실제 실행 경로와 git diff를 확인한 뒤, 내가 지정하는 한 가지 문제만 최소 수정해줘. 매매 조건·주문 조건·대시보드·VBS·0600 연동은 명시하지 않는 한 건드리지 마. 검사 결과는 통과/실패/실제 PC 미확인으로 나눠 사실대로 보고해줘.

