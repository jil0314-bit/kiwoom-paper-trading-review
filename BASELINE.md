# BASE-001 기준본 동결 기록

상태: **완료 - 코드·매매 로직 미수정**  
확정 시각: 2026-08-14 (Asia/Seoul)

## 공식 실행본

- 공식 폴더: `C:\Users\MAIN\Documents\자동매매 코딩\V76_09_15개수정보완_0600연동유지_PAPER`
- 공식 실행파일: `3_START_V76_GUI.vbs`
- 실제 진입파일: `SOURCE\core\bot_v76_00_three_stage_ctrlv_paper.py`
- 기존 실행 체인 manifest: `SOURCE\chain_manifest.json`
- 기준 명세: `SPEC.md`

## 기준 검증 결과

- `SOURCE\chain_manifest.py` 실행 결과: 기존 manifest 대상 99개 파일 일치
- VBS SHA-256: `bc6fa6922834d8978dd764c25b2620bec5628aad3a7d66cd92174608597eb258`
- 실제 진입파일 SHA-256: `3caf68d3260bf0ad779da1100a1d8a804ce79d8e143ea213ac58be55c2d8fafb`
- SPEC v1.1 SHA-256: `532d779afcc4e63033d4188af338bdf7dcfbb7a08efdcaa6a5c7f0f51dd2f85c`

## 동결 범위

- VBS 실행 경로는 변경번호 승인 없이는 변경 금지.
- 0600 연동과 대시보드 디자인은 MAP-001에서 실제 함수 경계를 확정하기 전까지 동작 변경 금지.
- 전략·REST 데이터·주문 코드는 향후 승인된 변경번호 범위에서만 변경 가능.
- `runtime*`, 로그, 체결장부, 토큰 캐시, `__pycache__`는 기준 소스 해시 대상이 아니다.

## 미확인 항목

- Windows 프로세스 조회 권한이 거부되어, 현재 실행 중인 동일 프로젝트 Python 프로세스의 중복 여부는 이 단계에서 확인하지 못했다.
- 실제 키움 PC의 0600 연동·REST 연결·주문 동작은 이 단계에서 검증하지 않았다.

## 다음 단계

`MAP-001`: 실제 실행·상태·주문·표시 경로를 파일과 함수 단위로 지도화한다. 이 작업도 매매 로직을 변경하지 않는다.
