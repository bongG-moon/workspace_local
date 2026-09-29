# Company Workspace 0.12.9 — 승인 카드와 요청 설명

검증일: 2026-09-30.

승인 카드가 대화 영역보다 길어지고 요청 수신 시 전체 화면을 맨 아래로 이동시켜 설명이 잘리는 문제를 수정했습니다. 카드 높이는 실제 대화 영역 안으로 제한하고 제목·승인/거절 버튼을 유지하며 본문만 스크롤합니다. 전체 명령, 입력 원문, 파일 변경 전후와 허용 규칙은 확인할 수 있습니다. 승인 범위는 펼쳐서 선택하며 현재 선택은 버튼 옆에도 표시합니다.

CLI가 제공하는 승인 사유와 해당 ask 규칙을 표시합니다. 설명은 길이와 제어문자를 제한하며, CLI가 반복 허용을 금지한 요청은 추가 규칙 선택지를 제공하지 않습니다. 서버도 해당 요청에 과거·임의 선택 ID가 제출되면 거부합니다.

## 승인 빈도 조사

일반 tool_use 활동은 승인 카드를 만들지 않으며, CLI의 can_use_tool 요청만 표시합니다. 동일한 대기 요청 ID는 backend와 화면에서 중복 제거됩니다. 현재 모드와 기존 allow/deny/ask 규칙은 CLI가 판단하고 앱이 임의의 자동 허용 규칙을 추가하지 않습니다. ‘이번만 허용’은 규칙을 저장하지 않으므로 이후 유사 요청도 승인이 필요할 수 있습니다.

기존 실제 CLI 2.1.284의 연결 검사 기록은 초기 모드 default를 보고했습니다. 대화형 CLI가 Auto이고 앱 연결이 Manual인 경우 승인 빈도가 달라질 수 있습니다. 이번 검증에서 사용자의 실제 업무 대화나 승인 횟수를 수집하지 않았으므로 모든 반복 요청의 원인을 단정하지 않습니다. addDirectories 제안은 기존 미지원 상태로, 실제 관련 요청이 관찰되면 별도 구현 후보입니다.

공식 근거: [Claude 권한](https://code.claude.com/docs/en/permissions), [SDK 권한 평가 순서](https://code.claude.com/docs/en/agent-sdk/permissions). 개인 PC 정책·Claude 설정·승인 모드는 변경하지 않았습니다.

## 검증 결과

- 승인 backend/protocol 관련 68개 통과: `tests.test_workspace_permissions`, `tests.test_workspace_runtime_controls`, `tests.test_local_workspace.BridgeTests`, `tests.test_workspace_transport_product`.
- frontend 관련 107개 통과: 신규 승인 카드 행동 검사 6개를 포함해 controls/state/dialogs/inline/composer/native handoff 검사.
- 버전·시작 파일 진단 해시 검사 4개 통과.
- 격리된 가상 CLI 브라우저 검증 7가지 배치 조건 통과. 1280×720, 900×600, 390×700에서 60줄 명령, 4개 승인 범위, 긴 Edit diff, 4개 질문과 직접 답변을 확인했습니다. 제목과 버튼이 대화 영역에 들어오고 가로 넘침이 없습니다.
- 동일 요청 ID 재전송과 뒤따르는 상태 이벤트에도 카드가 하나만 나타납니다. 선택한 연결 범위 규칙만 전달되며, 거절과 반복 허용 금지 요청에는 updatedPermissions가 없습니다. 필수 질문 미응답은 제출되지 않고, 다중 선택·직접 입력이 보존됩니다.
- 브라우저 오류 로그 없음. 가상 CLI는 실제 도구·모델을 실행하지 않으며, 개인 설정과 분리된 검증 디렉터리를 사용했습니다. 이번 변경에서 실제 AI 업무 실행은 반복하지 않았습니다.

화면·검증 기록: 수정 화면 (로컬 검증 기록: `build/qa-workspace-0.12.9-approval/desktop.png`; 공개 저장소에는 포함하지 않음), 좁은 화면 (로컬 검증 기록: `build/qa-workspace-0.12.9-approval/narrow.png`; 공개 저장소에는 포함하지 않음), `build/qa-workspace-0.12.9-approval/bounds.json`, `verification.json`. 검증 서버를 정상 종료하고 임시 runtime과 빌드 작업 디렉터리를 정리했습니다.

## 배포

`dist/Company-Workspace-0.12.9.exe` — 16,961,024 bytes.

SHA-256: `e2d45a791b41d264efe9f81f9a8582462352127297f2fdbf2cd729b1c6a10df0`.

실제 EXE에 내장된 99개 파일 추출·해시 검증과 64개 앱 소스 바이트 일치 검사를 통과했습니다. 실행 상태나 인증정보는 포함하지 않았습니다. 빌드 기록: `build/workspace-standalone-0.12.9-build.json`.

구버전 0.12.8 EXE를 정리하고 기존 0.12.4 VBS 배포본은 유지했습니다. 실행 중인 앱이 있으면 **설정 → 앱 종료** 후 새 EXE를 실행하세요.
