# Company Workspace 단일 EXE — 0.23.20

`Company-Workspace-0.23.20.exe`는 앱 파일을 담은 Windows 실행기입니다. EXE ZIP을 받았다면 먼저 압축을 풀고 실행합니다. **이미 설치된 Python 3.11 이상이 필요하며 Python 실행 환경·설치 프로그램을 포함하거나 다운로드·자동 설치하지 않습니다.** 앱 전용 로그인은 없고 AI 업무는 현재 사용자의 기존 Claude Code와 인증을 사용합니다.

현재 화면 사용법은 [로컬 업무 안내](LOCAL_WORKSPACE.md), 일반 Claude Code 중심으로 정리한 범위는 [0.15.0 안내](WORKSPACE_0.15.0_CLAUDE.md)를 참고하세요. 이 문서는 사용법이며 0.23.20 빌드·현장 실행·공개 배포 완료를 증명하는 기록은 아닙니다.

## 필요한 환경과 실행 순서

Windows 10/11 x64, Windows PowerShell, .NET Framework 4.6.2 이상, WebView2 Evergreen Runtime, 기존 Python 3.11 이상이 필요합니다. AI 요청에는 사용 가능한 기존 Claude Code와 인증이 필요합니다. VBS 방식과 같은 시작·권한 검사 경로를 사용하므로 회사의 EXE·PowerShell 허용 정책에 따라 실행 여부가 달라질 수 있습니다.

1. 새 ZIP을 별도 폴더에 압축 해제하고 새 EXE를 실행합니다.
2. 같은 버전·더 최신 버전이 열려 있으면 기존 창을 사용합니다. 새 버전이면 기존 업무가 끝난 뒤 자동 전환합니다. 0.21.4 이하에서는 초안을 보관한 뒤 안내의 **확인**을 한 번 누릅니다.
3. **새 업무**에서 업무 폴더를 확인한 뒤 질문을 보냅니다.

이전 VBS·EXE·배포 폴더를 자동으로 삭제하지 않습니다. 실행 중인 업무나 승인·질문 대기는 끝날 때까지 기다립니다. 0.21.5 이상에서는 보내지 않은 입력·첨부를 보관해 복원하고, 대기열·예약은 재시작 후 확인한 뒤 이어 실행합니다. 전환 대기 중에는 취소할 수 있습니다. 실행 조건이나 정상 종료를 확인하지 못하면 강제 종료하지 않고 안내합니다.

## 기존 Python과 Claude 확인

실행기는 PATH의 기존 Python 실행 파일, `py`가 보고한 설치 목록, Windows의 Python 등록 정보에서 후보를 확인합니다. 실제 실행 파일·3.11 이상 버전·필수 기본 모듈을 확인한 뒤 사용하며 설치 등록이나 PATH를 변경하지 않습니다. Python 함수·`.cmd`·`.bat` 래퍼는 검사 목적으로 실행하지 않습니다.

필요하면 `--python "<기존 python.exe의 절대 경로>"`를 지정합니다. 이 옵션은 이번 실행에만 적용하고 조건을 통과하지 못한 지정 파일을 다른 설치로 자동 대체하지 않습니다.

Claude 호출 대상은 현재 Windows 사용자의 기존 PowerShell 프로필과 명령 해석을 따릅니다. 기존 함수·별칭·래퍼·명시 경로를 유지하며, 다른 사용자 설치나 인증을 찾지 않습니다. Claude를 확인하지 못해도 앱 화면에서 문제를 안내하고 AI 요청을 보류합니다. 설정을 초기화하거나 다른 CLI로 몰래 전환하지 않습니다.

Company Agent 전용 학습·기억 관리·설치 등록 기능은 이번 앱에서 제외했습니다. 사용자의 기존 설치를 제거하지 않으며 활성화된 Claude 플러그인은 일반 플러그인으로 조회합니다. 기존 인증·`CLAUDE_CONFIG_DIR`·모델·MCP·스킬·메모리·플러그인 설정은 보존합니다. 승인 카드에서 사용자가 **항상 허용**을 명시적으로 선택한 경우에는 그 규칙과 저장 범위만 Claude에 전달합니다.

## 실행 캐시와 업무 데이터

첫 실행은 내장 앱 파일을 `%LOCALAPPDATA%\CompanyAgent\workspace-runtime` 아래 버전과 내용 해시별 폴더에 준비합니다. 추가 다운로드는 하지 않으며 캐시에도 Python은 넣지 않습니다. 같은 EXE를 다시 열면 검증된 같은 앱 파일을 재사용합니다.

업무·대화·대기 요청·예약·알림은 기존 앱 상태 경로 `%LOCALAPPDATA%\CompanyAgent\local-ui`에서 이어집니다. 앱 파일 캐시와 업무 데이터는 별개입니다. **Company-Workspace** 실행 이름과 두 경로는 이전 업무 호환성을 위해 유지하며 Company Agent 설치가 필요하다는 뜻은 아닙니다.

새 버전은 기존 데이터를 자동으로 삭제·이동하거나 개인 Claude 설정을 다시 작성하지 않습니다. 파일 변경이나 누락을 발견하면 검증되지 않은 캐시를 그대로 실행하지 않고 안내합니다.

## 화면과 종료

Edge·Chrome 브라우저 창 대신 자체 Windows 창에 WebView2를 표시합니다. Runtime과 Python을 자동 설치하지 않습니다. [0.18.0 전용 창 안내](WORKSPACE_0.18.0_NATIVE_WINDOW.md)를 참고하세요.

최근 업무는 같은 고정 그룹 안에서 손잡이를 끌거나 **Alt+↑ / Alt+↓**로 정렬합니다. **스킬·도구**의 공통 범위는 설치 스킬에 적용하고, 도구·연결 서버·명령은 **연결 기준 업무**가 보고한 목록을 표시합니다.

입력창 아래 모델·Effort·승인 선택은 AI 질문 전송과 구분합니다. `/` 자동완성과 Shift+Tab이 같은 업무의 연결을 준비할 때 중복 연결을 만들지 않도록 공유합니다. Bypass는 명시적인 위험 확인 뒤 CLI의 적용 응답을 확인하며 일반 Shift+Tab 순환으로 들어가지 않습니다.

X로 창을 닫거나 지원되는 창에서 **트레이로 보내기**를 선택해도 앱은 계속 실행됩니다. **트레이 → 앱 열기** 또는 같은 실행기를 다시 열어 돌아옵니다. **완전 종료**는 앱과 업무 연결을 종료합니다. Windows 로그인 자동 시작은 등록하지 않습니다.

예약은 PC가 깨어 있고 앱이 실행 중일 때만 처리합니다. 완전히 종료했다가 다시 열면 폴더와 대기·예약 내용을 확인하고 **이어 실행**해야 합니다. 앱을 실행하지 않아도 예약이 계속 작동하는 Windows 작업 스케줄러 등록 기능은 아닙니다.

## 실행 진단

`WS-37`·`WS-38`은 확인한 Python 후보와 실패 이유를 구분합니다. 기록 저장에 성공하면 `%LOCALAPPDATA%\CompanyAgent\local-ui\diagnostics\python-check-….json` 위치를 안내합니다. 기록에는 로컬 설치 경로가 포함될 수 있지만 인증 값이나 표준 오류 원문은 저장하지 않습니다.

시작 오류가 계속되면 `%LOCALAPPDATA%\CompanyAgent\workspace-runtime`의 해당 버전 폴더 아래 `Company-Workspace\Check-Workspace.cmd`를 실행합니다. 0.23.20 진단 표기는 **`ws33-55`**, 대상 소스는 **`workspace-0.23.20`**입니다. 캐시 준비 전이라면 표시된 `EXE-` 오류 코드부터 확인합니다. [시작 진단 안내](WORKSPACE_STARTUP_DIAGNOSTIC.md)를 참고하세요.

진단과 실행은 Windows의 UAC·영구 실행 정책·기존 파일 ACL을 바꾸지 않습니다. PowerShell의 이번 실행에 적용한 옵션으로 모든 그룹 정책·AppLocker·WDAC·Script Host 제한을 해결할 수 있다고 보장하지 않습니다.

## 개발용 옵션

일반 사용에는 아래 옵션이 필요하지 않습니다.

| 옵션 | 범위 |
| --- | --- |
| `--python <절대 경로>` | 이미 설치된 Python 실행 파일 지정 |
| `--execution-mode normal` / `--execution-mode administrator` | 이번 실행의 앱·Claude 권한 지정. 관리자는 Windows 승인과 같은 계정 검증 필요 |
| `--state <절대 경로>` | 별도 앱 상태 폴더 |
| `--cache-root <절대 경로>` | 별도 앱 파일 캐시 폴더 |
| `--no-browser` | 자동 화면 열기 없이 시작 |
| `--demo` | 실제 Claude 요청을 보내지 않는 체험 모드 |
| `--verify-only` | 내장 앱 파일 준비·해시만 확인하고 앱·Claude를 시작하지 않음 |

`--verify-only` 성공은 Python·Claude·회사 PC 실행 조건까지 검증했다는 뜻이 아닙니다. 빌드는 `deploy/New-WorkspaceStandalone.ps1`, 실행기 구조는 `deploy/CompanyWorkspace.Standalone.cs`에서 확인할 수 있습니다. 배포 파일 제작·공개 게시·직원 PC 적용은 별도 단계입니다.


권한 선택 보완본은 설정에서 일반·관리자 실행을 선택합니다. 시작 진단의 `executionMode`와 `predictedGuard`를 함께 확인하세요. Windows 승인이나 권한 변경 실행을 진단 도구가 수행하는 것은 아닙니다. [실행 권한 안내](WORKSPACE_EXECUTION_MODE.md).
