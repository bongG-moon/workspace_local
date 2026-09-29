# Workspace 0.12.4 검증 기록

검증일: 2026-09-29. 입력창의 모델 · Effort · 승인 모드 인라인 선택 기능을 검증했습니다.

## 구현 범위

- 현재 값을 입력창 아래에 항상 표시하고, 작은 비모달 목록에서 직접 선택합니다.
- 현재 CLI의 모델·모드·모델별 Effort 지원값을 사용합니다. 변경 ACK와 실제 Effort 읽기 결과를 구분합니다.
- Effort는 세션 전용 apply_flag_settings로 변경하고 ultracodeRequested를 보존합니다. 개인 설정을 쓰는 update_settings는 사용하지 않습니다.
- Effort 복원은 연결을 종료하고 기존 설정을 다시 상속합니다. 모델·승인 모드도 함께 복원됨을 선택지에서 안내합니다.
- 초기화에는 AI 질문을 보내지 않습니다. 입력·첨부 보존, 실행 중 중복 변경 방지, 오래된 응답 무시, 거절·재시도와 복원 이벤트의 상태 일치를 확인했습니다.

## 자동 검사

`python -X utf8 scripts/test-lab/test-workspace.py`: **533개 실행, 526개 통과, 7개 건너뜀, 실패·오류 0**.

7개는 샌드박스의 심볼릭 링크 권한, 실제 사용자 토큰 opt-in, Windows Script Host 실행 제약에 따른 제외입니다. 이번 변경으로 생긴 실패를 제외 처리하지 않았습니다. 실제 사용자 환경 실행기 검증은 아래에 별도 기록했습니다.

- 새 인라인 frontend 테스트 16개: 선택·ACK·오류·요청값/실제값 차이·연결 초기화·초안 보존·복원·이벤트·업무 전환.
- Effort backend 테스트 12개: 실제 설정 읽기·지원값 제한·거절·타임아웃·기존 설정 보존·복원 HTTP/SSE 상태 정합성.
- 결과: `build/qa-workspace-0.12.4-tests.json`.

## 화면 및 실제 CLI

격리 fixture의 브라우저에서 모델 변경, high Effort, 계획 모드, 모델 거절 안내와 작성 중인 입력 보존을 확인했습니다.

실제 설치된 Claude Code **2.1.284**를 사용하는 최종 ZIP에서도 초기화 후 모델 `claude-sonnet-5-5` 및 Effort `medium`을 표시했습니다. UI로 **medium → low**를 적용해 실제값을 확인한 다음, 기존 설정으로 복원하고 재연결하여 **medium**으로 돌아온 것을 확인했습니다. AI 사용자 질문은 **0회**입니다. 검증용 연결은 정상 종료했습니다.

개인 설정 `settings.json`, `settings.local.json`, `CLAUDE.md`, `.mcp.json`, `plugins/installed_plugins.json`의 존재/해시 비교에서 **변경 0개**입니다. PC UAC·보안 정책을 바꾸지 않았습니다.

- 직접 CLI 증거: `build/qa-effort-probe/result.json`.
- 배포본 실행/초기화/복원/종료: `build/qa-workspace-0.12.4-live-controls/*-result.json`.
- 실제 화면: `build/qa-inline-controls/live-effort.png`, `live-selected-controls.png`.
- fixture 화면: `build/qa-inline-controls/effort.png`, `selected-controls.png`.

## 실행 ZIP

`dist/company-workspace-preview-0.12.4-20260929-222921.zip`

SHA256: `aa8a15d5a36b9c7c03529176d48cff4c6c517ce0b89cc0fc6d511b98cf3f98fa`

63개 배포 파일이 현재 소스와 같음을 검사했습니다. 런타임 상태와 인증 파일은 포함하지 않았습니다. 압축 해제본의 실행기·API 인증 경계·JS/폰트·업무 생성·정상 종료 검증이 통과했습니다. 현재 사용자 환경에서 같은 버전 재실행 시 동일 PID를 재사용하는 것도 확인했습니다.

이미 압축 해제된 실행 위치:
`dist/company-workspace-preview-0.12.4-20260929-222921/Company-Workspace/Company-Workspace.vbs`

이번 검증은 현재 Windows PC와 CLI 버전에 대한 결과입니다. 다른 PC의 정책 조합이나 모든 CLI 버전에서 동일하게 동작한다고 일반화하지 않습니다. VBS 더블클릭 자동 검사는 이 호스트의 WSH 실행 제약으로 제외되었으며, 실제 배포 PowerShell 실행기 경로는 검증했습니다.
