# Company Workspace 0.15.0

기존 Claude Code를 창과 버튼으로 사용하는 Windows 로컬 앱입니다. 터미널 경로 이동 없이 업무 폴더를 선택하고 대화와 자료를 다룹니다. 특정 하네스 설치는 필요하지 않습니다.

**[EXE ZIP 다운로드](https://github.com/bongG-moon/workspace_local/releases/download/v0.15.0/Company-Workspace-0.15.0-exe.zip)** · **[VBS ZIP 다운로드](https://github.com/bongG-moon/workspace_local/releases/download/v0.15.0/Company-Workspace-0.15.0-vbs.zip)**

[0.15.0 변경 안내](docs/WORKSPACE_0.15.0_CLAUDE.md) · [사용법](docs/LOCAL_WORKSPACE.md) · [검증 범위](docs/VALIDATION_0.15.0.md)

## 실행하기

| 배포 파일 | 실행 방법 | 필요한 환경 |
| --- | --- | --- |
| EXE ZIP | 압축을 풀고 `Company-Workspace-0.15.0.exe` 더블클릭 | Windows 10/11 x64, Windows PowerShell, 기존 Python 3.11 이상 |
| VBS ZIP | 전체 압축을 풀고 `Company-Workspace.vbs` 더블클릭 | Windows, Windows PowerShell, Windows Script Host, 기존 Python 3.11 이상 |

앱 전용 로그인은 없습니다. AI 기능은 같은 Windows 사용자의 **기존 Claude Code 설치와 인증**을 사용합니다. 두 방식 모두 Python을 포함하거나 설치하지 않고 실행 조건만 확인합니다. EXE는 앱 파일을 사용자 캐시에 풀어 실행하는 서명되지 않은 실행기입니다. 회사의 EXE·VBS·PowerShell 실행 정책에 따라 사용이 제한될 수 있습니다.

업데이트할 때는 기존 앱의 **설정 → 완전히 종료**를 누르고 새 ZIP을 별도 폴더에 풀어 실행하세요. 창의 X는 앱을 트레이에 남깁니다. 한 번에 한 버전만 실행합니다.

## 0.15.0 변경

- 최근 업무를 손잡이로 끌어 순서를 바꾸고 핀 아이콘으로 고정합니다. 고정 업무와 일반 업무는 각각 같은 그룹 안에서 정렬하며 순서는 재시작 후에도 유지됩니다. 손잡이에 초점을 놓고 Alt+↑/↓도 사용할 수 있습니다.
- 공통 스킬의 설치 범위와 **연결 기준 업무**를 구분했습니다. 공통 화면에서도 선택한 업무의 Claude가 보고한 도구·서버·명령을 확인합니다. 여러 업무의 서로 다른 연결 목록을 합치지 않습니다.
- Company Agent 전용 기억·학습·설치 등록 연동을 앱에서 제거했습니다. 사용자가 Claude에 설치한 스킬·플러그인은 일반 목록에 그대로 나타납니다. 개인 설치를 삭제하거나 설정을 바꾸지 않습니다.
- 자동완성과 Shift+Tab이 동시에 연결을 준비하면서 모드 변경이 실패하던 문제를 수정했습니다. Esc 취소, 한글 조합 입력, Tab·방향키·Ctrl+Enter 처리도 보완했습니다.

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
python -X utf8 -m local_app.server --demo
python -X utf8 scripts/test-lab/test-workspace.py
powershell -NoProfile -ExecutionPolicy Bypass -File deploy/New-WorkspaceBundle.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File deploy/New-WorkspaceStandalone.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File deploy/New-WorkspaceRelease.ps1
```

소스 경로가 긴 경우 EXE 빌드에 `-VerificationDirectory 'C:\짧은검증폴더'`를 지정할 수 있습니다. 이는 빌드 시 포함 파일 검사 위치이며 사용자 실행 설정을 변경하지 않습니다.

패키지 검사기는 허용 파일, 소스 일치, 시작 진단 해시, 개인 상태 미포함을 확인합니다. EXE/VBS에는 같은 앱 파일을 담습니다. GitHub 자동 소스 ZIP과 실행용 Release ZIP은 다릅니다. 기존 Company Agent 어댑터와 관련 시험은 이전 구현 기록으로 소스에 남아 있지만 앱 진입점과 배포 파일에는 연결되지 않습니다.

[0.15.0 검증 기록](docs/VALIDATION_0.15.0.md) · [제3자 고지](THIRD_PARTY_NOTICES.md)