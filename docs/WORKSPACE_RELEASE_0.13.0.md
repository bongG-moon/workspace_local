# Company Workspace 0.13.0 배포 안내

창을 닫아도 이어지는 업무, 부드러운 응답 표시, 파일 드래그 첨부, 후속 요청과 실행 예약을 추가했습니다. 일반 Claude Code 설치를 기준으로 동작하며 Company Agent 하네스는 필수가 아닙니다.

## 실행 파일

| 배포 파일 | 실행 방법 |
| --- | --- |
| [EXE ZIP](https://github.com/bongG-moon/workspace_local/releases/download/v0.13.0/Company-Workspace-0.13.0-exe.zip) | ZIP을 풀고 `Company-Workspace-0.13.0.exe` 실행 |
| [VBS ZIP](https://github.com/bongG-moon/workspace_local/releases/download/v0.13.0/Company-Workspace-0.13.0-vbs.zip) | ZIP 전체를 풀고 `Company-Workspace.vbs` 실행 |

두 방식 모두 설치된 Python 3.11 이상과 Windows PowerShell을 사용합니다. EXE는 Windows 10/11 x64 기준이며 Python·설치 파일을 포함하거나 다운로드하지 않습니다. AI 업무에는 같은 계정의 기존 Claude Code와 인증이 필요합니다. 앱 전용 로그인은 없습니다.

기존 버전의 **설정 → 완전히 종료** 또는 이전 버전의 **앱 종료**를 누른 뒤 새 버전을 실행하세요. 창의 X만 누르면 앱이 백그라운드에 남을 수 있습니다. 기존 업무·파일·Claude·Company Agent 개인 설정을 유지합니다.

## 달라진 사용 흐름

- **트레이:** 창의 X는 화면을 닫습니다. 트레이 아이콘에서 다시 열거나 완전히 종료할 수 있습니다. 진행 중·응답 대기 개수를 표시합니다.
- **응답 표시:** 작은 응답 조각을 묶어 갱신합니다. 위로 스크롤해 이전 내용을 읽는 동안 위치를 유지하고 최신 응답 이동 버튼을 제공합니다.
- **드래그:** 자료·결과 파일을 입력창으로 끌면 원본 경로를 첨부합니다. 탐색기에서 끌어 넣은 파일은 앱 관리 공간의 복사본으로 첨부합니다.
- **후속 요청:** 실행 중에도 다음 요청을 작성합니다. **끝나고 이어서**는 정상 완료 후 같은 업무로 전달합니다. **지금 반영**은 현재 요청의 중지·연결 정리 후 같은 대화에서 새 요청을 보냅니다.
- **예약:** 한 번·매일·매주(복수 요일)를 지원하며 목록에서 수정·정지·재개·삭제합니다.

예약은 PC가 깨어 있고 앱이 실행 중인 동안 동작합니다. 앱 재시작 후에는 대기 내용과 업무 폴더를 확인하고 이어 실행합니다. 실행 중 5분 이내의 지연은 처리할 수 있지만 지난 회차를 몰아서 실행하지 않습니다. 예약도 현재 CLI의 질문·승인을 그대로 기다립니다.

전송 여부가 불확실하거나 오류·수동 중지가 발생하면 자동으로 다음 요청을 보내지 않습니다. 설정이 등록 때와 달라지면 확인 후 재개하며 개인 설정 파일을 수정하지 않습니다. “지금 반영”이 이미 수행한 파일 변경·외부 작업을 되돌리거나 실행 중 Claude 내부에 메시지만 끼워 넣는다는 뜻은 아닙니다.

## 사용법과 검증

[기능 사용법·설계 이유·추가 개선 제안](WORKSPACE_0.13.0_DESKTOP.md), [검증 기록](VALIDATION_0.13.0.md), [시작 진단](WORKSPACE_STARTUP_DIAGNOSTIC.md)을 확인하세요. 이전 0.12.12의 운영 PC 실행 성공은 사용자에게 확인받았으며 이번 신규 기능의 모든 회사 환경 검증과는 구분합니다.

공개 배포와 다운로드 검증 상태는 검증 기록에 기록합니다. [Release 페이지](https://github.com/bongG-moon/workspace_local/releases/tag/v0.13.0)와 [ZIP 체크섬](https://github.com/bongG-moon/workspace_local/releases/download/v0.13.0/SHA256SUMS.txt)을 함께 제공합니다.
