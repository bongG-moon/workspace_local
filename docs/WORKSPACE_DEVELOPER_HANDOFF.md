# AX Workspace 앱 개발 소스 안내

이 자료는 앱의 구현 구조를 이해하고 개조하기 위한 개발 원본입니다. 설치 파일이나 Git 게시·서버 배포 절차 안내가 아닙니다.

## 어떤 기술로 만들었나요?

| 영역 | 사용 기술 | 핵심 소스 |
| --- | --- | --- |
| 화면 | HTML, CSS, JavaScript | `local_app/web/` |
| 기능·요청 처리 | Python | `local_app/server.py`, `app_dispatch.py`, `work_queue.py` |
| Claude Code 연결 | Python | `local_app/bridge.py` |
| Windows 앱 창 | C#, WebView2 | `deploy/Workspace.Desktop.cs`, `local_app/app_window.py` |
| Windows 실행기 | C#, PowerShell | `deploy/CompanyWorkspace.Standalone.cs`, `Start-CompanyWorkspace.ps1` |
| 기능 검증 | Python, JavaScript | `tests/`, `scripts/test-lab/` |

별도 프런트엔드 프레임워크 없이 브라우저 기본 기능으로 화면을 만들었고, Python 표준 라이브러리로 로컬 서버를 구성했습니다. Windows에서는 WebView2가 그 화면을 앱 창 안에 표시합니다. AI 응답은 사용자 PC에 설치된 Claude Code와 연결해서 받습니다.

## 다른 조직에 전달할 소스

**릴리즈의 `workspace-local-0.23.30-source.zip` 전체**를 전달하면 됩니다. 사내 반입용과 동일한 검증된 개발 원본이며, EXE·개인 설정·대화 기록·`.git` 이력은 포함하지 않습니다. 이 문서부터 읽도록 안내하면 사용 기술과 수정 위치를 바로 확인할 수 있습니다. 개발자는 아래 부분부터 보면 됩니다.

- `local_app/`: 화면, 채팅, 업무, 파일, 예약, Claude 연결 등 앱의 실제 구현과 화면 자산
- `deploy/`: Windows 창·실행기 소스와 이를 만드는 빌드 스크립트
- `tests/`, `scripts/`: 수정 후 기능을 확인하는 테스트와 개발 보조 도구
- `docs/`, `README.md`: 앱 구조와 개발 안내
- `THIRD_PARTY_NOTICES.md` 및 글꼴 `OFL.txt`: 소스와 함께 보존할 제3자 고지

화면만 수정하려면 `local_app/web/`부터, 기능을 수정하려면 `local_app/`의 Python 코드부터 확인하세요. 창 모양이나 Windows 동작은 `deploy/Workspace.Desktop.cs`가 출발점입니다.

EXE는 이미 빌드된 실행 파일이라 개조용 원본을 대신하지 않습니다. `workspace_publisher/`와 `Publish-Workspace.py`는 사내 서버 게시 도구이므로 앱 구현을 살펴보거나 수정하는 데 사용하지 않아도 됩니다. 개인 로그인·대화 기록·첨부·설정 폴더는 전달 대상이 아닙니다.
