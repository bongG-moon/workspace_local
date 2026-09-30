# 0.18.0 — 전용 Windows 앱 창

## 무엇이 달라졌나요

Workspace가 자신의 Windows 창 안에 WebView2 화면을 표시합니다. 처음 실행하거나 실행 아이콘을 다시 눌러도 Edge·Chrome 브라우저 창으로 앱을 열지 않습니다. EXE와 VBS 배포판은 같은 전용 창을 사용합니다. HTML 결과물을 사용자가 외부 앱으로 열거나 외부 문서 링크를 누를 때는 기존 연결 프로그램을 사용합니다.

X는 트레이가 연결된 경우 창을 숨깁니다. 실행 아이콘·트레이·업무 알림을 누르면 같은 창을 복원합니다. 완전히 종료는 서버·Claude 연결·전용 창을 정리합니다. 트레이를 사용할 수 없는 환경의 X는 완전 종료를 요청합니다. 제목 표시줄은 Windows의 이동·크기 조절·최소화·최대화·접근성을 유지하고 지원되는 Windows에서 앱 색상을 적용합니다. Windows 10이나 고대비 모드에서는 운영체제 기본 장식이 유지될 수 있습니다.

## 필요한 실행 환경

- Windows 10/11 x64, Windows PowerShell, .NET Framework 4.6.2 이상.
- 이미 설치된 Python 3.11 이상과 Microsoft Edge WebView2 Evergreen Runtime.
- AI 업무에는 현재 사용자의 기존 Claude Code와 인증. 앱 전용 로그인은 없습니다.
- VBS 방식은 Windows Script Host도 필요합니다.

WebView2는 앱 안에 웹 화면을 표시하는 Microsoft 구성요소입니다. Edge 브라우저의 설치 여부와 별도입니다. Windows 11 및 많은 Windows 10 PC에 있지만 모든 회사 PC에 있다고 보장할 수는 없습니다. [Microsoft 배포 안내](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution)를 참고하세요. 배포 파일에는 작은 SDK 연결 DLL만 포함하며 WebView2 Runtime, Python, Claude 또는 설치 프로그램은 포함·다운로드·자동 설치하지 않습니다.

| 오류 | 의미와 확인할 사항 |
|---|---|
| WS-46 | WebView2 Runtime을 찾지 못했습니다. 회사에서 허용한 Runtime 설치 여부를 확인하세요. |
| WS-47 | 전용 창 초기화 또는 창 소유자 검증에 실패했습니다. 배포 파일 누락, Runtime 동작 및 EXE 실행 정책을 확인하세요. |
| WS-37 / WS-38 | 기존 Python 탐색 또는 실행 검사에 실패했습니다. WebView2와 별개입니다. |

UAC, 영구 실행 정책, 기존 파일 ACL, 로그인 계정 및 Claude 인증·모델·MCP·스킬·플러그인 설정은 바꾸지 않습니다. Windows의 실행 차단을 우회하지 않습니다. 기존 WS-38의 제한 토큰/DACL 수정과 같은 사용자·세션 확인은 유지합니다.

## 연결 및 데이터

기존 Python 서버와 Claude CLI 연결 코드를 그대로 사용합니다. `/`·`@` 자동완성, 모델·effort, Shift+Tab, 승인·대기열·예약·이전 대화·파일 비교·분기는 같은 API와 상태를 사용합니다. 파일 끌어 놓기와 복사/붙여넣기는 WebView2의 일반 웹 입력 기능을 사용합니다. 다운로드에는 앱 창을 소유자로 지정한 저장 창을 표시합니다.

WebView2의 화면 캐시는 앱 상태 폴더의 `webview2`에 저장합니다. Edge·Chrome의 개인 프로필이나 로그인 정보를 복사하지 않습니다. 사용자 지정 `--state`는 별도의 화면 프로필을 가집니다. 작업 관리자에 `msedgewebview2.exe` 하위 프로세스가 보이는 것은 정상이며 브라우저 앱 창을 연다는 의미는 아닙니다. 화면 엔진은 별도 메모리를 사용하므로 브라우저 방식보다 항상 적은 메모리를 사용한다고 보장하지 않습니다.

앱의 화면 연결 토큰은 명령행에 넣지 않고 부모·자식 전용 파이프로 전달합니다. 네이티브 호스트는 정해진 창 제어 명령만 받으며 웹 페이지에 파일/프로세스 실행 객체를 공개하지 않습니다. 화면은 자신의 localhost 주소만 탐색하고 외부 페이지·파일 탐색·카메라·마이크 권한을 차단합니다. 업무 기능의 외부 파일 열기는 기존 인증 API를 따릅니다.

화면 엔진이 중단되면 **화면 다시 열기**로 화면을 복구할 수 있습니다. 이 동작은 새 AI 요청이나 실패한 도구 실행을 재전송하지 않습니다. 서버가 종료되면 전용 파이프가 닫혀 화면 호스트도 종료됩니다.

## 빌드와 확인

개발 PC에서 `deploy/New-WorkspaceDesktop.ps1`을 먼저 실행합니다. 공식 NuGet SDK의 버전·SHA256은 `deploy/WebView2.lock.json`에 고정합니다. SDK를 내려받는 동작은 개발 빌드에서만 수행합니다. `-SdkPackage`로 미리 받은 동일 패키지를 지정할 수 있습니다. .NET Framework C# 컴파일러가 필요하며 컴파일한 호스트·DLL·라이선스·출처 해시는 `build/desktop-host`에 생성합니다.

`New-WorkspaceBundle.ps1`, `New-WorkspaceStandalone.ps1`, `New-WorkspaceRelease.ps1`이 같은 호스트 결과를 배포합니다. 소스·SDK·산출물 해시가 같으면 이전 빌드를 재사용합니다. 모든 바이너리가 컴퓨터 간 바이트 단위로 재현된다는 주장은 하지 않습니다.

`scripts/test-lab/verify-workspace-desktop.py --state <새 테스트 폴더>`는 별도의 가짜 업무로 실제 창 열기, 소유 PID/HWND 확인, 반복 열기, X 숨기기, 복원, 종료를 검사합니다. 일반 회귀 시험은 실제 Claude나 개인 창을 조작하지 않습니다. 배포 실행 및 회사 PC 조건별 확인 결과는 이 문서와 구분해 검증 기록에 남깁니다.
