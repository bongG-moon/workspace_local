# Company Workspace 단일 EXE

`Company-Workspace-0.12.9.exe` 하나를 원하는 폴더에 두고 더블클릭합니다. ZIP 압축 해제나 별도 Python 설치는 필요하지 않습니다. 기존 `Company-Workspace.vbs`와 이전 배포 폴더는 자동 변경하거나 삭제하지 않습니다. 새 기능은 새 0.12.9 실행 파일을 사용해야 적용됩니다.

## 기존 Claude와 연결

앱은 별도 로그인 없이 열립니다. AI 업무에는 이 Windows 계정에 설치된 Claude Code와 기존 인증이 필요합니다. EXE가 새 Claude 계정이나 앱 전용 인증을 만들지는 않습니다.

EXE도 VBS와 동일하게 Windows PowerShell의 기존 프로필을 읽어 `claude` 명령을 찾습니다. 회사에서 만든 함수·별칭·래퍼와 명시된 실행 경로를 우선합니다. PATH에 명령이 없을 때에는 검증된 현재 사용자 폴더의 공식 네이티브 설치 경로 `.local\bin\claude.exe`를 확인합니다. 다른 계정의 설치나 인증을 찾지 않습니다.

기존 `CLAUDE_CONFIG_DIR`, 모델·MCP·스킬·Company Agent 설치 설정을 유지합니다. 앱에 포함한 Python을 시스템 PATH에 등록하지 않으며, 기존 Company Agent 관리 연동의 Python 경로도 임의로 바꾸지 않습니다.

## 모델·Effort·승인 모드

입력창 아래 **모델 / Effort / 승인**에서 실제 연결값을 확인합니다. 선택 목록을 열거나 값을 바꾸는 과정은 AI 업무 요청이 아니며 작성 중인 내용과 첨부를 자동으로 보내지 않습니다.

- **Effort** 클릭 또는 `/effort`만 보내면 선택 목록을 엽니다. `/effort high`처럼 값을 지정하면 현재 모델에서 제공한 low·medium·high·xhigh·max 중 지원하는 값만 적용합니다. 버튼과 명령은 같은 경로를 사용합니다.
- `/effort auto`는 현재 모델에서 변경 전에 확인한 값으로 복원합니다. 개인 설정을 삭제하거나 다른 모델·승인 모드를 초기화하지 않습니다. 원래 값을 모르면 복원 불가를 안내합니다. 성공하면 해당 명령 텍스트만 지우고 첨부는 유지합니다.
- 입력창에 커서가 있을 때 **Shift+Tab**으로 현재 연결이 지원하는 Manual → Accept edits → Plan → Auto를 순환합니다. 요청 진행 중에는 변경하지 않으며 다른 입력 필드의 Shift+Tab은 기존 이동 동작을 유지합니다.
- 같은 앱 실행 중 같은 업무가 재연결되면 선택한 모델·Effort·승인 모드를 다시 확인하여 적용합니다. 메모리에만 유지하므로 앱을 완전히 종료·재시작하면 기존 Claude 설정을 상속합니다. 다른 업무나 개인 기본 설정을 변경하지 않습니다.

승인 모드는 `manual mode on`, `accept edits on`, `plan mode on`, `auto mode on`처럼 실제 연결의 상태를 표시합니다. 대화형 Claude 터미널과 SDK/`-p`의 시작 모드는 다를 수 있습니다. 개발 환경의 CLI 2.1.284 SDK 연결은 `default`를 상속했으며 앱이 이를 임의로 Auto로 바꾸지 않습니다. `ultracode`도 Effort와 별도로 보존합니다. 자세한 복원·실패·지원 범위는 [로컬 업무 화면 사용법](LOCAL_WORKSPACE.md)을 확인하세요.

## 승인 카드

0.12.9는 승인 카드의 요청 요약·범위 선택·버튼을 간결하게 정리합니다. **이번만 허용**이 기본이며, 연결·지속 허용은 현재 Claude가 제공한 규칙과 저장 범위를 확인하고 직접 선택합니다. 실행 원문과 허용 규칙을 계속 확인할 수 있으며, 화면 정리가 자동 승인이나 개인 설정의 일괄 변경을 뜻하지 않습니다.

## 첫 실행과 재실행

첫 실행은 EXE에 들어 있는 앱과 Python을 현재 사용자의 `%LOCALAPPDATA%\CompanyAgent\workspace-runtime` 아래 전용 폴더에 준비합니다. 추가 다운로드는 하지 않습니다. 준비가 끝난 같은 EXE는 그 폴더를 다시 사용합니다. 캐시 이름은 버전과 포함된 파일의 내용으로 정하므로 EXE를 다른 폴더로 옮겨도 동일한 실행 환경을 사용합니다.

업무·대화는 기존 `%LOCALAPPDATA%\CompanyAgent\local-ui`에서 이어집니다. 실행 캐시와 업무 데이터는 별도입니다. 파일 변경이나 누락을 발견하면 그대로 실행하지 않고 안내합니다.

기존 VBS 앱이나 다른 버전이 이미 연결되어 있다면 먼저 그 앱에서 **설정 → 앱 종료**를 선택한 뒤 EXE를 실행하세요. 창의 X 버튼은 연결을 종료하지 않을 수 있습니다. 진행 중인 업무를 강제로 끊거나 다른 버전으로 몰래 전환하지 않습니다.

Windows 10/11 x64용입니다. 회사의 실행 제한이나 보안 정책을 변경하지 않습니다. 이 EXE는 자체 제작한 서명되지 않은 실행 파일이며, 회사에서 서명·배포 승인이 필요한 경우 해당 배포 절차를 따릅니다.

## 실행 진단

시작 오류가 계속되면 파일 탐색기 주소창에 `%LOCALAPPDATA%\CompanyAgent\workspace-runtime`을 입력하세요. 실행한 버전의 폴더 아래 `Company-Workspace\Check-Workspace.cmd`를 열면 읽기 전용 진단을 실행할 수 있습니다. 0.12.9의 진단 버전은 `ws33-12`, 대상 소스는 `workspace-0.12.9`입니다. EXE에 포함된 도구이므로 별도 VBS 배포본은 필요하지 않습니다. 이 위치가 아직 만들어지지 않았다면 표시된 `EXE-` 오류 코드를 전달하세요. 진단은 PC의 영구 보안 정책이나 Claude 개인 설정을 변경하지 않습니다. [진단 결과 읽는 법](WORKSPACE_STARTUP_DIAGNOSTIC.md)을 참고하세요.

## 개발 및 검증

빌드: `powershell -NoProfile -ExecutionPolicy Bypass -File deploy/New-WorkspaceStandalone.ps1`

빌드에 사용하는 Python 3.13.15 x64 임베디드 ZIP은 공식 SHA-256을 확인합니다. `.NET Framework` C# 컴파일러로 Windows GUI 실행 파일을 만들며, 기존 파일을 덮어쓰지 않습니다. Python 배포본의 라이선스는 내장 런타임과 함께 포함합니다.

검증용 옵션: `--no-browser`, `--state <절대 경로>`, `--cache-root <절대 경로>`, `--demo`, `--verify-only`. 보통 사용할 때에는 옵션이 필요하지 않습니다. `--verify-only`는 내장 파일 준비와 검증만 하고 앱·Claude를 시작하지 않습니다.

0.12.9의 실제 검증 결과와 남은 현장 확인은 저장소의 `docs/VALIDATION_0.12.9.md`에 기록합니다. 이 사용법은 새 버전의 모든 시험이 완료됐다는 기록이 아닙니다.

공식 근거: [Python 임베디드 배포](https://docs.python.org/3.13/using/windows.html#the-embeddable-package), [포함한 Python 배포본과 체크섬](https://www.python.org/downloads/release/python-31315/), [Windows 실행 매니페스트](https://learn.microsoft.com/en-us/windows/win32/sbscs/application-manifests).
