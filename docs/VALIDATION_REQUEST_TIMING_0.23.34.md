# 0.23.34 동일 버전 갱신: 요청 소요 시간·화면 아이콘

## 변경 범위

- 요청별 완료 소요 시간을 기존 대화 기록과 마지막 이벤트에 함께 보관합니다. 정상 완료·중지·오류를 구분하며 기록이 없는 과거 요청은 추정하지 않습니다.
- 실제 실행 시작부터 완료 응답까지 monotonic clock으로 측정합니다. 대기·준비·완료 후 파일 관찰은 제외하고 실행 중 승인 대기는 포함합니다. 실행 중 경과 시간의 기존 표시와 다른 업무 실행을 유지합니다.
- bounded 대화 기록에서 첫 메시지가 잘려도 남은 같은 요청의 답변에 시간 기록을 보존합니다. 화면별 표시 항목도 최대 150개로 제한하며 별도 polling을 추가하지 않습니다.
- 웹/안내서/파일/폴더/연결 설정/대화 분기 등의 우측 대각선 화살표를 의미에 맞는 SVG로 교체했습니다. 기존 링크·버튼 동작과 접근성 이름을 유지합니다.
- 새 request-timing.js와 request-timing.css를 소스·EXE·VBS 번들에 포함합니다.

## 검증

2026-10-26 최종 커밋 준비 시 다음 14개 모듈의 **185개 검사**를 통과했습니다.

`test_workspace_request_timing`, `test_workspace_run_elapsed`, `test_workspace_observation_responsiveness`, `test_workspace_history`, `test_workspace_history_cache`, `test_workspace_request_timing_frontend`, `test_workspace_elapsed_frontend`, `test_workspace_multiwork_frontend`, `test_workspace_event_recovery_frontend`, `test_workspace_frontend_state`, `test_workspace_modes_frontend`, `test_workspace_restart_frontend`, `test_workspace_release_history_frontend`, `test_workspace_rich_content_frontend`.

시간 측정·중복 종료·시계 변경·느린 완료 파일 관찰·기록 절단·업무 이동·초안 및 첨부 보존·이벤트 복원·링크 생성과 기존 버튼 동작을 포함합니다. 이전 단계에서는 실제 Edge의 라이트/다크/좁은 화면에서 완료 시간과 아이콘을 확인했습니다. 브라우저 요청과 응답은 격리된 로컬 대역을 사용했으며 개인 Claude 요청·사내 운영 서버·VDI 실측 결과는 아닙니다.

배포는 이 변경과 새 릴리즈 노트·source manifest를 포함한 Git 커밋 스냅샷에서 빌드합니다. 기존 0.23.34의 초안 보존·업데이트 복구 변경을 유지합니다. 관련 빌드·체크섬·정확한 소스 및 번들 대조 결과는 배포 산출물의 검증 보고서에 기록합니다.

P&T AI Square 확장의 설치 진행 화면은 별도 패키지에서 제공하며, 이 기본 앱에 조직용 기능을 내장하지 않습니다. 개발 중 진단 앱 스크립트는 이번 소스 스냅샷에 포함하지 않습니다.
