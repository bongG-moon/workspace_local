# Company Workspace 0.20.2

기존 Claude Code를 창과 버튼으로 사용하는 Windows 로컬 앱입니다. 터미널 경로 이동 없이 업무 폴더를 선택하고 대화와 자료를 다룹니다. 특정 하네스 설치는 필요하지 않습니다.

**[EXE ZIP 다운로드](https://github.com/bongG-moon/workspace_local/releases/download/v0.20.2/Company-Workspace-0.20.2-exe.zip)** · **[VBS ZIP 다운로드](https://github.com/bongG-moon/workspace_local/releases/download/v0.20.2/Company-Workspace-0.20.2-vbs.zip)**

[0.20.2 변경 안내](docs/VALIDATION_0.20.2.md) · [사용법](docs/LOCAL_WORKSPACE.md) · [검증 범위](docs/VALIDATION_0.20.2.md)

## 실행하기

| 배포 파일 | 실행 방법 | 필요한 환경 |
| --- | --- | --- |
| EXE ZIP | 압축을 풀고 `Company-Workspace-0.20.2.exe` 더블클릭 | Windows 10/11 x64, Windows PowerShell, 기존 Python 3.11 이상, WebView2 Runtime, .NET 4.6.2 이상 |
| VBS ZIP | 전체 압축을 풀고 `Company-Workspace.vbs` 더블클릭 | Windows, Windows PowerShell, Windows Script Host, 기존 Python 3.11 이상, WebView2 Runtime, .NET 4.6.2 이상 |

앱 전용 로그인은 없습니다. AI 기능은 같은 Windows 사용자의 **기존 Claude Code 설치와 인증**을 사용합니다. 두 방식 모두 Python을 포함하거나 설치하지 않고 실행 조건만 확인합니다. EXE는 앱 파일을 사용자 캐시에 풀어 실행하는 서명되지 않은 실행기입니다. 회사의 EXE·VBS·PowerShell 실행 정책에 따라 사용이 제한될 수 있습니다.

업데이트할 때는 기존 앱의 **설정 → 완전히 종료**를 누르고 새 ZIP을 별도 폴더에 풀어 실행하세요. 창의 X는 앱을 트레이에 남깁니다. 한 번에 한 버전만 실행합니다.

## 0.20.2 변경

- 새 앱 창은 처음부터 최대화됩니다. 작업 표시줄을 유지하는 일반 최대화이며, 이미 열린 창을 다시 활성화할 때는 사용자가 조절한 창 상태를 유지합니다.
- 상단 상태를 **Claude 실행 준비됨 / 업무 연결됨 / 업무 연결 종료**로 구분합니다. 실행 파일 확인과 실제 업무 연결을 혼동하지 않도록 각 상태에 설명을 추가했습니다.

## 0.20.1에서 개선된 기능

- 알림을 상태·업무명·열기 버튼만 남긴 작은 카드로 정리했습니다. 색감은 유지하고 모서리의 중복된 경계를 제거했습니다.
- Noto Sans KR 400·600을 앱 안에서 불러와 한글을 표시합니다. 글꼴 설치나 시스템 설정 변경은 없습니다.

## 0.20.0에서 개선된 기능

- 완료·승인·질문·오류 알림을 앱 색감에 맞춘 전용 카드로 표시합니다. 해당 업무 열기와 닫기를 제공하며 다른 앱의 입력 포커스를 유지합니다.
- 기존 알림 설정과 알림함은 유지합니다. 카드마다 새 브라우저나 프로세스를 만들지 않습니다. [알림 카드 안내](docs/WORKSPACE_0.20.0_NOTIFICATIONS.md)를 확인하세요.

## 0.19.1에서 개선된 기능

- 이전 대화는 **새 업무 → 기존 세션 활용하기**에서 불러옵니다. 기존 팝업과 세션 이어가기 동작을 유지합니다.
- 불러오기 팝업의 주요 버튼을 앱 공통 보라색 버튼 스타일로 통일했습니다.

## 0.19.0에서 개선된 기능

- 상단과 입력창 주변 높이를 줄이고, 좌측 업무를 제목 중심의 목록으로 정리했습니다. 선택한 업무는 기존 색감의 음영으로 표시합니다.
- 좌측은 아이콘 레일로 접고, 우측 자료·결과도 숨길 수 있습니다. 넓은 창의 접힘 상태를 기억하고 좁은 창에서는 겹쳐 여는 패널을 사용합니다.
- **이어 할 일·실행 예약** 버튼을 입력창 위로 이동하고 중복 안내 문구를 없앴습니다.

## 0.18.1에서 개선된 기능

- 트레이 메뉴를 아이콘 위치에 표시하고 모니터 배율에 맞게 렌더링합니다. Windows 배율 설정을 바꿀 필요가 없습니다.

## 0.18.0에서 개선된 기능

- 실행 아이콘을 다시 누르면 기존 창을 활성화합니다. WebView2를 내장한 전용 Windows 창을 사용합니다. Edge·Chrome 브라우저 창은 열지 않습니다.
- X는 연결된 트레이로 숨기고, 완전 종료는 전용 창과 서버를 함께 정리합니다. 화면 엔진 오류는 AI 요청을 다시 보내지 않고 복구합니다.
- WebView2 Runtime을 새 실행 조건으로 확인하며 자동 설치하지 않습니다. UI 캐시는 앱 전용 폴더에 저장하고 개인 브라우저 프로필을 사용하지 않습니다.

## 0.17.0에서 개선된 기능

- 좌측 탐색 영역을 넓히고 업무 행 간격을 줄였습니다. 업무 이름이 같으면 `(2)`, `(3)`을 붙입니다.
- 이전 Claude 대화를 업무에 추가해 원래 세션과 폴더에서 후속 요청을 보냅니다.
- 도구 화면의 **연결하고 목록 확인**으로 명령·MCP 상태를 확인합니다. CLI 미제공 목록은 0개와 구분합니다.
- `ede_diagnostic` 실패를 완료로 처리하지 않으며, 남은 승인 상태를 정리하고 같은 대화의 후속 요청을 받습니다. CLI 자체의 모든 오류 원인을 해결했다는 뜻은 아닙니다.

## 0.16.0에서 추가된 기능

- **통합 알림함**에서 응답 필요·완료·확인 필요·읽지 않음을 필터링합니다. 읽음 처리와 승인·답변 처리는 별개입니다.
- **빠른 실행 / Ctrl+K**로 업무와 자주 쓰는 기능을 검색합니다. 기존 `/`·`@` 입력과 Shift+Tab은 유지합니다.
- **이번 결과 → 파일 변경 비교**에서 요청 전후의 텍스트를 확인합니다. 최근 8개 요청을 앱 전체에서 보관하며 Office·큰 파일은 변경 정보만 표시합니다. 원본 복구·자동 되돌리기는 수행하지 않습니다.
- **대화 분기**로 마지막 완료 기록을 독립된 Claude 세션에서 이어갑니다. 첫 요청 전에는 분기 준비 상태이며 원본과 같은 업무 폴더를 사용합니다.
- 사용하지 않는 저장 대화의 본문을 필요할 때 읽고, 닫힌 알림함과 팔레트의 화면 요소를 해제합니다. 새 파일 감시기나 반복 조회 타이머를 추가하지 않습니다.

이전 버전의 업무 순서·고정, 공통/폴더별 도구 조회, 자동완성, 모델·Effort·승인, 대기열·예약·첨부 기능도 함께 유지합니다.

## 주요 사용 흐름

업무 폴더를 선택하고 요청을 입력합니다. `/` 명령·스킬, `@` 파일 추천과 모델·Effort·승인 모드 선택을 지원합니다. 입력창에서 Shift+Tab으로 CLI가 지원하는 일반 승인 모드를 순환합니다. **⚠ Bypass permissions** 진입은 별도 위험 확인을 거치며 개인 기본값은 바꾸지 않습니다.

자료와 결과 파일을 입력창으로 끌어 첨부하고 기본 앱에서 열 수 있습니다. 진행 중 후속 요청은 대기열에 넣거나 현재 작업에 반영할 수 있습니다. 예약은 PC가 깨어 있고 앱이 실행 중일 때 동작하며 앱 재시작 후 사용자가 확인하여 재개합니다.

기존 Claude 세션은 목록이나 세션 UUID로 불러오고 원래 업무 폴더를 함께 확인합니다. 완료·승인·질문·오류는 알림 목록에서 확인하며 백그라운드에서는 Windows 트레이 알림을 요청합니다. Windows 알림 설정에 따라 배너가 나타나지 않을 수 있습니다.

CLI의 모든 터미널 기능을 에뮬레이션하지는 않습니다. 제공 명령, 모드, 스킬 실행은 설치된 Claude 버전과 해당 환경의 정책에 따릅니다. 목록에서 발견한 설치 메타데이터와 실제 연결에서 보고받은 항목을 구분합니다.

## 기존 환경 보존

UAC·계정·영구 실행 정책, Claude 인증·모델·MCP·스킬·기억 설정을 자동 변경하지 않습니다. 사용자가 승인 카드에서 CLI가 제안한 지속 허용을 직접 선택하면 해당 규칙 저장만 요청합니다. 모델·Effort·승인 모드 변경은 실제 CLI 응답을 확인하고 업무 연결에 적용합니다.

기존 기록 호환을 위해 실행 파일 이름과 `%LOCALAPPDATA%\CompanyAgent\local-ui`, `%LOCALAPPDATA%\CompanyAgent\workspace-runtime` 경로는 유지합니다. 이 이름 때문에 Company Agent가 필요해지는 것은 아닙니다. 업무 파일은 사용자가 선택한 폴더에 남습니다.

서버는 `127.0.0.1`에만 바인딩하며 실행마다 내부 접근 토큰을 만듭니다. Claude의 외부 요청은 기존 연결 설정을 따릅니다. 배포에는 Python·Claude 실행 파일, 개인 기록이나 인증 정보를 포함하지 않습니다.

[오프라인 안내서](docs/WORKSPACE_USER_GUIDE.html) · [EXE 안내](docs/WORKSPACE_STANDALONE_EXE.md) · [시작 진단](docs/WORKSPACE_STARTUP_DIAGNOSTIC.md)

## 개발과 검증

저장소 루트에서 실행합니다. 앱은 Python 표준 라이브러리를 사용하며 UI 회귀 시험에는 Node.js가 필요합니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File deploy/New-WorkspaceDesktop.ps1
python -X utf8 -m local_app.server --demo
python -X utf8 scripts/test-lab/test-workspace.py
powershell -NoProfile -ExecutionPolicy Bypass -File deploy/New-WorkspaceBundle.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File deploy/New-WorkspaceStandalone.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File deploy/New-WorkspaceRelease.ps1
```

소스 경로가 긴 경우 EXE 빌드에 `-VerificationDirectory 'C:\짧은검증폴더'`를 지정할 수 있습니다. 이는 빌드 시 포함 파일 검사 위치이며 사용자 실행 설정을 변경하지 않습니다.

패키지 검사기는 허용 파일, 소스 일치, 시작 진단 해시, 개인 상태 미포함을 확인합니다. EXE/VBS에는 같은 앱 파일을 담습니다. GitHub 자동 소스 ZIP과 실행용 Release ZIP은 다릅니다. 기존 Company Agent 어댑터와 관련 시험은 이전 구현 기록으로 소스에 남아 있지만 앱 진입점과 배포 파일에는 연결되지 않습니다.

[0.16.0 검증 기록](docs/VALIDATION_0.16.0.md) · [제3자 고지](THIRD_PARTY_NOTICES.md)
