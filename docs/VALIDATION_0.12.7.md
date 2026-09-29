# Company Workspace 0.12.7 — Claude 실행 설정과 업무 상태 분리

검증일: 2026-09-29~30. 이 기록은 앱 실행 설정 변경에 대한 검증이며 Company Agent 코어 배포나 회사 PC의 보안 정책 변경 기록이 아닙니다.

## 원인과 수정

실제 Claude Code 2.1.284는 승인 모드 변경 시 `system/status`의 `status:null`과 `permissionMode`를 보냅니다. 이전 앱은 이를 일반 작업 진행 신호로 취급했습니다. 변경 후에는 승인 모드 정보만 갱신합니다. 사용자가 보낸 실제 업무가 있을 때만 작업 상태를 표시합니다.

초기 연결 응답의 `current_permission_mode`를 읽고, 변경 응답의 `mode`와 후속 상태 알림을 표시합니다. 나중에 도착한 초기 정보나 종료한 이전 연결의 이벤트가 확인된 현재 값을 덮어쓰지 않도록 했습니다. `default`와 `manual` 별칭은 선택 목록에 중복 표시하지 않습니다. 실제 CLI가 도움말에는 `manual`만 제공하면서 적용 응답에는 `default`를 반환하는 경우도 확인했습니다. 이 경우 현재 선택 표시·다음 Shift+Tab·재연결 복원은 같은 모드로 대응시키고, 실제 보고값 자체는 유지합니다.

`/effort`는 입력창 위 선택 목록을 열고, `/effort high` 같은 명령은 클릭과 동일한 연결 제어를 사용합니다. 새 업무 메시지를 만들지 않습니다. `/effort auto`는 현재 모델에서 변경 전 확인한 수준으로 복원하며, 모델·승인 설정이나 연결을 초기화하지 않습니다. 원래 값을 확인하지 못하면 복원했다고 주장하지 않습니다. 기존 ultracode 설정은 보존합니다.

입력창의 Shift+Tab은 현재 연결이 제공하는 Manual → Accept edits → Plan → Auto 순서에서 지원하는 항목만 순환합니다. 일반 Tab 자동완성, 한글 조합 중 키 입력, 다른 입력란의 Shift+Tab 이동을 유지합니다. Claude가 거절한 모드는 적용 완료로 표시하지 않습니다.

같은 앱의 같은 업무에서 선택한 값은 메모리에 보관하고, 연결을 다시 열면 Claude의 적용 응답을 받은 뒤 업무를 전송합니다. 복원에 실패하면 업무를 보내지 않습니다. 앱 종료 후에는 기존 Claude 설정을 다시 상속하며 개인 설정 파일에 앱의 선택을 기록하지 않습니다. 선택 질문 답변 중 재연결도 잠금을 보유한 채 응답을 기다리지 않으며 중복 제출을 차단합니다.

## 실제 CLI 제어 확인

`scripts/test-lab/probe-workspace-controls.py`로 실제 설치된 CLI에 업무 메시지 없이 초기화·제어 요청만 보냈습니다.

- 초기 승인 모드 `default`, 모델 `claude-sonnet-5-5`, Effort `medium`을 확인했습니다.
- Plan, Accept edits, Auto, Default의 적용 응답과 `status:null` 이벤트를 확인했습니다.
- Effort `high` 적용 후 실제 설정 읽기로 같은 값을 확인했습니다.
- 사용자 메시지 전송은 0건이며 확인한 개인 설정 파일 5개는 변경되지 않았습니다.

증거: `build/qa-workspace-control-protocol/report.json`.

## 브라우저 검증

격리된 가상 CLI 연결에서 `/effort high`, 인수 없는 `/effort` 목록 열기, 목록의 low 클릭, Manual 클릭 후 Shift+Tab 전환을 확인했습니다. 마지막 상태는 `idle`, 대화 메시지는 0건, Effort는 low, 승인 모드는 acceptEdits였습니다. 실제 AI 응답 검증과 구분합니다.

증거: `build/qa-workspace-0.12.7-controls-ui/browser-result.json`.

최종 EXE와 실제 CLI에서도 Auto → Manual → Accept edits를 Shift+Tab으로 전환하고 Manual의 현재 선택 체크를 확인했습니다. Auto 클릭, `/effort low` 입력, high 클릭도 확인했습니다. 화면을 새로고침하여 같은 업무를 열었을 때 high와 Auto가 유지됐고, 이 과정의 대화 메시지는 0건이었습니다. 증거: `build/qa-standalone-0.12.7-modes-final/ui-controls-result.json`.

## 최종 회귀검사

`python -X utf8 scripts/test-lab/test-workspace.py`: **591개 중 584개 통과, 7개 환경 제한으로 제외**, 실패·오류 0. 모델·Effort·승인 상태와 선택 질문·연결 종료·입력 자동완성·기존 앱 기능을 포함합니다. 실행 시간 133.697초.

제외된 항목은 심볼릭 링크 권한 3개, 현재 로그인 사용자 또는 제한 토큰을 요구하는 선택 실행 항목 3개, 이 환경의 WSH 실행 제한 1개입니다. 별도 현재 사용자 실행기 사례 3개는 이 회귀 묶음에서 제외했으며 이번에 같은 테스트를 재실행한 것은 아닙니다. 아래 최종 EXE의 실제 계정 실행·재실행·연결 검증과 구분합니다.

증거: `build/qa-workspace-0.12.7-tests.json`.

## 최종 EXE

| 항목 | 결과 |
| --- | --- |
| 파일 | `dist/Company-Workspace-0.12.7.exe` |
| 크기 | 16,958,976 bytes |
| SHA-256 | `823692fcfe5f830340fc1dc89a1b1706c31f33d1a7f1946a466efb733bbc9c0b` |
| 내장 파일 | 99개, 실제 내장 ZIP 추출·해시 검증 통과 |
| 앱 소스 일치 | 배포 대상 64개 파일 바이트 일치, 진단 시작 파일 해시 확인 |
| 독립 실행 | EXE만 있는 한글 폴더, 시험 PATH에서 Python 관련 항목 6개 제외 |
| 실제 런타임 | 내장 Python 3.13.15 `pythonw.exe`, 재실행 시 기존 캐시·서버 PID 재사용 |
| 기존 Claude 연결 | 2.1.284, 기존 PowerShell 진입점·인증 사용, 명령 140개 초기화 |
| 설정 제어 | Manual 별칭과 초기값 확인, Plan·Accept edits·Auto, high·원래 medium 복원 |
| 재연결 | Manual의 실제 `default`와 Auto/high 모두 확인 응답 후 복원 |
| 실제 응답 | 도구 없는 한 줄 요청에 `WORKSPACE_EXE_OK` 수신, `done`, 보류 도구 없음 |
| 응답 후 설정 | 모델·Effort high·Auto 모두 요청 전 값 유지 |
| 개인 설정 | 확인한 설정 파일 5개 변경 0, 검증 앱 정상 종료 |

빌드 정보는 `build/workspace-standalone-0.12.7-build.json`, 실제 실행 결과는 `build/qa-standalone-0.12.7-modes-final/`에 있습니다. 기존 0.12.4 VBS ZIP은 유지하며 기존 ZIP의 SHA-256은 `aa8a15d5a36b9c7c03529176d48cff4c6c517ce0b89cc0fc6d511b98cf3f98fa`입니다.

설정 제어 시험을 마친 뒤 명시적으로 실제 AI 요청 1건을 전송했습니다. 결과는 `response-result.json`, 설정 보존과 종료 결과는 `finish-result.json`에 있습니다. 이 시험은 파일 결과물을 만들지 않았으므로 앱의 일반 결과물 확인 상태 `unverified`와 프로토콜 응답·설정 유지 검증을 구별합니다. 응답 후 실제 실행 화면 (로컬 검증 기록: `build/qa-standalone-0.12.7-modes-final/screen.png`; 공개 저장소에는 포함하지 않음)에서 완료 상태와 high·Auto를 확인할 수 있습니다. 1280px 창의 가로 넘침은 없었습니다.

검증 종료 후 이번 작업에서 만든 임시 빌드·추출 캐시·검증용 EXE 복사본을 정리했습니다. 보고서와 화면은 남겼습니다. `dist`에는 최신 0.12.7 EXE만 남기고 기존 VBS 배포본을 보존했습니다. 실제 사용 중인 앱의 업무 기록과 실행 캐시는 정리 대상으로 삼지 않았습니다.

## 공식 동작과 적용 범위

[Claude Code 승인 모드](https://code.claude.com/docs/en/permission-modes), [모델과 Effort 설정](https://code.claude.com/docs/en/model-config), [SDK 승인 제어](https://code.claude.com/docs/en/agent-sdk/permissions)를 확인했습니다. 대화형 터미널과 SDK의 시작 기본값은 다를 수 있으므로 앱이 임의로 Auto를 선택하지 않고 실제 연결 값을 표시합니다. `/effort auto`의 개인 기본값 삭제 기능은 이 앱에서 수행하지 않으며 현재 연결에서 관찰한 이전 수준만 복원합니다.

UAC 비활성화 회사 PC, 조직 정책으로 Auto가 제한된 실제 계정, 모든 모델의 모든 수준을 현장 검증했다는 의미는 아닙니다. 미지원·거절·지연 응답·모델별 수준 제한은 별도의 가상 연결 회귀검사로 확인합니다.
