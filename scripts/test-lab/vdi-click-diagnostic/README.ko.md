# AX Workspace 클릭 위치 진단 1.0.0

VDI 안에서 AX Workspace의 보이는 버튼 위치와 실제 클릭 위치가 어긋날 때 사용하는 별도 진단 도구입니다. **문제를 수정하는 앱이나 AX Workspace 설치 파일은 아닙니다.**

## 실행과 저장

1. 문제가 발생하는 **VDI 내부**로 `AX-Workspace-VDI-Click-Diagnostic-1.0.0.exe`를 가져가 더블클릭합니다. 기존 AX Workspace를 삭제할 필요는 없습니다.
2. 처음 열린 최대화 창에서 **새 검사**를 누릅니다. 보라색으로 표시되는 십자 **중심**을 1번부터 5번까지 한 번씩 클릭합니다.
3. 아무 반응이 없으면 **F8**로 해당 위치를 건너뜁니다. 상단 버튼에도 같은 기능이 있습니다.
4. 첫 검사를 마친 뒤 창을 일반 크기로 바꾸거나 다른 모니터로 이동하고 **새 검사**로 다시 비교합니다. 검사 도중 크기가 바뀌면 해당 회차를 중단하고 새 검사를 요청합니다. 이전 기록은 유지됩니다.
5. **결과 저장** 또는 **Ctrl+S**로 JSON 파일을 저장합니다. **결과 폴더 열기**로 확인할 수 있습니다.

단축키: **F6** 새 검사, **F8** 현재 위치 건너뛰기, **Ctrl+S** 결과 저장. 페이지 입력에 문제가 있어도 상단의 Windows 기본 버튼과 키보드로 조작할 수 있습니다.

저장 위치:

```text
%LOCALAPPDATA%\CompanyAgent\vdi-click-diagnostic\results\
```

저장 파일을 담당자에게 전달할 때 VDI 제품/버전, 모니터 수, 로컬 PC와 VDI의 화면 배율, 어느 검사 회차에서 문제가 보였는지를 함께 알려 주세요. 도구가 자동으로 전송하지 않습니다.

## 기록 범위와 해석

- 기록: 테스트 표적 중심, 문서가 받은 `pointerdown`/`pointerup`/`pointercancel`/`click` 좌표, 배율, 창 크기, Windows 및 WebView2 버전, 검사 시각.
- 업무 대화, 인증키, 개인 파일, 화면 이미지, 다른 앱의 입력은 수집하지 않습니다. 입력 기록은 테스트 화면 안에서만 하며 최근 2,000건으로 제한됩니다. 초과 건수는 JSON에 표시됩니다.
- 오차는 **사용자가 보이는 십자 중심을 정확히 눌렀다는 전제**에서 계산한 CSS 픽셀 차이입니다. VDI를 사용하는 물리 PC의 실제 마우스 좌표를 측정하는 것은 아닙니다.
- 큰 오차가 관찰되어도 DPI, VDI, WebView2 중 무엇이 원인인지 자동으로 확정하지 않습니다. 창 크기/배율과 이벤트 순서를 함께 분석해야 합니다. 표적 밖 클릭도 기록하므로, 잘못 누른 회차는 새 검사로 다시 측정합니다.
- 앱의 실제 업무 화면을 재현하지 않으므로, 이 도구에서 정상이라고 모든 앱 입력 문제가 배제되지는 않습니다. 클릭이 전혀 오지 않는 경우 F8로 건너뛴 기록과 담당자의 관찰이 필요합니다.

## 실행 환경

Windows x64, .NET Framework 4.6.2 이상, 설치된 Microsoft Edge WebView2 Runtime이 필요합니다. Python이나 Node.js는 실행 PC에 필요하지 않습니다. 런타임을 자동 설치하거나 회사 보안 정책을 변경하지 않습니다.

정식 앱과 같은 `System DPI aware` 매니페스트, WinForms `AutoScaleMode.Dpi`, WebView2 `DockStyle.Fill`, SDK 1.0.4258.31을 사용합니다. 최초 창은 최대화입니다. 기존 WS-47 해결 설정을 변경하거나 시험용 DPI 설정을 강제로 적용하지 않습니다.

EXE는 포함된 WebView2 연결 라이브러리와 호스트를 위 전용 폴더의 `runtime` 아래에 SHA-256으로 검증한 뒤 사용합니다. 별도 `webview` 폴더는 이 도구의 테스트 화면 프로필입니다. AX Workspace의 설치 폴더/업무 데이터/Claude 설정은 읽거나 바꾸지 않습니다. WebView2 구성요소의 Microsoft LICENSE/NOTICE도 실행 자료에 포함됩니다. Windows가 제공한 LocalAppData 위치를 사용하므로 VDI의 사용자 프로필 리디렉션을 바꾸지 않습니다. 그 아래 도구가 관리하는 실행 자료 경로에 별도로 만들어진 정션/심볼릭 링크는 경로 검증에서 거부합니다.

도구 페이지의 네트워크 요청은 차단됩니다. WebView2 Runtime 자체의 운영 동작이나 회사 VDI의 통신까지 통제하는 도구는 아닙니다. 백신이 차단하면 백신을 끄거나 예외 처리하지 말고 담당자에게 파일과 탐지 정보를 전달해 주세요.

## 개발자 빌드와 검사

저장소 루트에서 실행합니다. `build/desktop-sdk/1.0.4258.31.nupkg`가 이미 있으면 첫 번째 명령은 생략합니다. SDK가 없는 전체 저장소에서는 첫 번째 명령으로 확보할 수 있습니다. 진단 도구의 소스 ZIP만 받은 경우에는 `deploy/WebView2.lock.json`의 공식 패키지를 준비하고 `Build.ps1 -SdkPackage <패키지 경로>`로 지정할 수도 있습니다. 빌드 스크립트는 항상 패키지의 고정 SHA-256을 검증합니다.

```powershell
powershell -NoProfile -File deploy\New-WorkspaceDesktop.ps1
powershell -NoProfile -File scripts\test-lab\vdi-click-diagnostic\Build.ps1
node scripts\test-lab\vdi-click-diagnostic\test-page.mjs
$result = Start-Process -FilePath build\vdi-click-diagnostic\AX-Workspace-VDI-Click-Diagnostic-1.0.0.exe -ArgumentList '--self-test' -Wait -PassThru -WindowStyle Hidden
$result.ExitCode
```

`--self-test`는 알려진 숫자 필드만 저장되는지와 포함된 HTML 리소스를 검사합니다. 실제 VDI 입력 호환성 검사가 아닙니다. 배포 전 실제 WebView2 창에서 클릭, 건너뛰기, 여러 회차 및 결과 저장을 별도로 확인해야 합니다. 중복 클릭이나 대응하는 down/up이 없는 click은 기록하되 정상 측정으로 세지 않습니다.
