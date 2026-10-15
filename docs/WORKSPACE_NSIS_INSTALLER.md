# AX Workspace 설치 파일 안내

`AX-Workspace-0.23.25-Setup.exe`는 AX Workspace를 설치하고 시작 메뉴에서 다시 열 수 있게 하는 NSIS 설치 프로그램입니다. 앱 버전은 **0.23.25**로 유지합니다. [설치 파일 다운로드](https://github.com/bongG-moon/workspace_local/releases/latest/download/AX-Workspace-Setup.exe)에서 받을 수 있습니다. GitHub 0.23.25 Release의 직접 EXE 다운로드도 같은 설치 프로그램으로 제공합니다. EXE·VBS ZIP은 기존 실행 및 자동 업데이트 용도로 유지합니다.

## 사용자가 설치하고 실행하기

1. 최신 Claude Code를 설치하고 인증을 완료합니다. 기존 Python **3.11 이상**, Windows 10/11 x64, Windows PowerShell, WebView2 Runtime, .NET Framework 4.6.2 이상도 필요합니다.
2. AX Workspace가 열려 있다면 트레이 아이콘을 우클릭해 **완전 종료**합니다. 창의 **X**는 트레이로 숨기는 기능입니다.
3. `AX-Workspace-0.23.25-Setup.exe`를 실행합니다. 바탕화면 바로가기 여부를 선택하고 설치합니다.
4. 시작 메뉴 또는 바탕화면의 **AX Workspace**를 엽니다. 기존 업무와 Claude 인증·설정을 그대로 사용합니다.

현재 Windows 사용자 계정에 설치하며 기본 위치는 다음과 같습니다. 별도 앱 로그인은 없습니다.

```text
%LOCALAPPDATA%\Programs\AX Workspace\AX-Workspace.exe
```

설치 프로그램은 관리자 권한을 요구하지 않습니다. 앱과 Claude의 실제 실행 권한은 앱을 연 Windows 환경을 따릅니다. 별도의 관리자 권한이 필요할 때만 기존 앱을 완전 종료한 뒤 앱 바로가기를 우클릭해 **관리자 권한으로 실행**하세요.

설치 파일은 Python·Claude Code·WebView2 등의 의존성을 자동 설치하거나 내려받지 않습니다. PC 시작 시 자동 실행, Windows 서비스, 새 상주 프로세스도 추가하지 않습니다. 기존 앱을 실행하면 원래의 트레이·예약 동작을 그대로 사용합니다.

## 새 버전과 제거

앱 안의 **업데이트하기**와 기존 사내 업데이트 채널은 계속 사용할 수 있습니다. 설치 프로그램은 처음 설치와 직접 재설치에 사용하고, 앱 안의 업데이트는 기존 검증된 ZIP을 사용합니다. 내부 업데이트 이후에도 같은 시작 메뉴 바로가기로 앱을 엽니다.

새 Setup 파일로 직접 설치하거나 앱을 제거할 때는 먼저 트레이에서 **완전 종료**하세요. 설치된 실행 파일이 사용 중이면 강제로 작업을 끊거나 재부팅을 예약하지 않고 다시 종료하도록 안내합니다.

제거는 Windows **설정 → 앱 → AX Workspace → 제거**에서 진행합니다.

| 제거하는 항목 | 유지하는 항목 |
| --- | --- |
| 설치한 실행기, 제거 프로그램, 앱의 시작 메뉴·바탕화면 바로가기, 설치 등록 정보 | 기존 업무·대화·예약·설정, 실제 업무 폴더·결과물, Claude 설치·인증·개인 설정 |

`%LOCALAPPDATA%\CompanyAgent\local-ui`와 `%LOCALAPPDATA%\CompanyAgent\workspace-runtime`도 유지합니다. 같은 Windows 사용자로 다시 설치하면 기존 내용을 사용할 수 있습니다. 앱을 제거하는 것만으로 개인 데이터까지 삭제되지는 않습니다.

기존 ZIP 배포본도 계속 사용할 수 있지만, 설치 후에는 시작 메뉴의 **AX Workspace**를 사용하면 실행 파일 위치를 다시 찾지 않아도 됩니다. 예전 작업표시줄 고정 아이콘이 중복된다면 고정을 해제하고 새 바로가기로 연 창을 다시 고정하세요.

## 기존 EXE 링크와 설치형 전환

- GitHub 0.23.25 Release에서 `AX-Workspace-0.23.25.exe`, `Company-Workspace-0.23.25.exe`, `AX-Workspace-0.23.25-Setup.exe`는 같은 NSIS 설치 파일입니다. 이전에 공유한 해당 EXE 링크로 새로 받는 사람도 설치 화면을 봅니다.
- 앞으로 공유할 주소는 [설치 파일 다운로드](https://github.com/bongG-moon/workspace_local/releases/latest/download/AX-Workspace-Setup.exe)입니다. 다음 Release에도 `AX-Workspace-Setup.exe`를 첨부해야 이 주소가 계속 작동합니다.
- 이미 PC에 저장되거나 메일에 첨부된 예전 EXE의 내용은 바뀌지 않습니다. 설치 파일을 한 번 새로 받아 실행하세요. 업무·설정과 Claude 환경은 유지됩니다.
- 기존 앱의 자동 업데이트는 ZIP으로 계속 동작하며, 이것만으로 Windows에 설치 등록되지는 않습니다. 같은 0.23.25이므로 이번 설치 방식 변경만으로 새 버전 알림이 뜨지는 않습니다.
- 이 전환은 GitHub 0.23.25 Release에 적용합니다. 과거 버전 링크와 별도 사내 GitLab 배포 파일은 자동으로 교체되지 않습니다.

## 배포 담당자가 설치 파일 만들기

사용자 PC에는 NSIS가 필요하지 않습니다. **빌드 PC에만 NSIS 3 Unicode 컴파일러**를 준비합니다. 회사에서 승인한 설치본이나 공식 포터블 ZIP을 사용하고, `makensis.exe`와 함께 제공되는 폴더 전체를 유지하세요. 컴파일러와 부속 파일은 사용자 설치 파일에 포함하지 않습니다.

빌드는 다음 순서로 컴파일러를 찾습니다.

1. `-NsisCompiler`에 지정한 `makensis.exe`
2. `AX_WORKSPACE_NSIS` 환경 변수의 경로
3. PATH에서 찾은 `makensis.exe`
4. 일반 NSIS 설치 폴더와 소스 폴더의 `build/nsis/` 아래

컴파일러가 없거나 NSIS 3가 아니면 중단합니다. 자동 다운로드는 하지 않습니다. 기존 앱 빌드용 WebView2 SDK도 먼저 준비해야 합니다. 아래 명령은 저장소 루트에서 실행하며, 출력 폴더에는 기존 파일이 없어야 합니다.

```powershell
.\deploy\New-WorkspaceStandalone.ps1 -OutputDirectory .\dist\installer-input-0.23.25
.\deploy\New-WorkspaceRelease.ps1 -StandaloneExe .\dist\installer-input-0.23.25\AX-Workspace-0.23.25.exe -OutputDirectory .\dist\installer-release-0.23.25 -IncludeInstaller -NsisCompiler "C:\approved-tools\nsis\makensis.exe"
```

두 번째 명령은 현재 소스와 실행기에 포함된 앱 파일이 같은지 확인하고, Setup 파일·기존 EXE/VBS ZIP·호환 파일·체크섬을 함께 만듭니다. 기존 업데이트를 위해 ZIP 파일도 계속 배포하세요.

이미 검증한 같은 버전의 독립 실행 EXE를 설치 파일로 감싸기만 하려면 아래 명령을 사용합니다. 이 명령은 앱 자체를 다시 빌드하지 않으므로 입력 EXE가 원하는 수정본인지 먼저 확인해야 합니다.

```powershell
.\deploy\New-WorkspaceInstaller.ps1 -StandaloneExe .\dist\AX-Workspace-0.23.25.exe -OutputDirectory .\dist\workspace-installer-0.23.25 -NsisCompiler "C:\approved-tools\nsis\makensis.exe"
```

Setup과 `.sha256` 파일이 출력 폴더에 생성되며, 빌드 기록에는 원본 EXE의 SHA256과 NSIS 버전도 남습니다. 설치 파일은 서명되지 않았습니다. 회사가 허용한 배포 경로와 실행 정책을 사용하세요.

## GitHub 다운로드 파일 구성

GitHub에서 공유하는 직접 EXE는 설치 프로그램으로, 앱의 자동 업데이트용 ZIP 안 EXE는 기존 독립 실행기로 유지합니다. 둘은 파일 이름이 비슷해도 교체 대상이 다릅니다.

설치 파일을 빌드한 뒤, 검증한 ZIP 폴더와 소스 ZIP을 사용해 아래처럼 게시용 폴더를 준비합니다. 출력 폴더는 새 경로로 지정하세요.

```powershell
python -X utf8 scripts/prepare-workspace-github-downloads.py --release-directory dist/workspace-release-0.23.25 --installer-report build/workspace-installer-0.23.25.json --source-zip dist/source-0.23.25/workspace-local-0.23.25-source.zip --output-directory dist/github-installer-0.23.25
```

이 도구는 기존 ZIP의 체크섬을 확인해 바이트 그대로 복사하고, 설치 파일을 버전별 이름·기존 직접 EXE 이름·고정 다운로드 이름으로 함께 준비합니다. `SHA256SUMS.txt`도 전체 파일에 맞춰 만듭니다. 네트워크 게시나 사용자 PC 설치는 수행하지 않습니다. 다음 버전에도 이 게시용 파일 구성을 유지하세요.

이미 공개한 버전을 전환할 때는 **4개 업데이트 ZIP을 다시 업로드하지 않고** 새 설치 EXE·직접 EXE·소스 ZIP을 올린 뒤 체크섬과 다운로드 안내를 갱신합니다. 게시 전 파일명·버전·해시를 확인하고 게시 후 로그인 없이 다시 내려받아 검증하세요. 이전 업데이트 정보를 보관한 앱에서 전환 중 해시 확인 오류가 나면 **새 버전 확인**으로 다운로드 정보를 갱신합니다.

원래 `v0.23.25` 태그는 보존합니다. GitHub가 태그로 자동 생성하는 **Source code (zip/tar.gz)**는 최초 0.23.25 소스입니다. 설치 배포 코드는 **main의 Code → Download ZIP** 또는 별도 **workspace-local-0.23.25-source.zip**에 포함됩니다.

## 사내 GitLab 배포 도구에서 만들기

`python -X utf8 Publish-Workspace.py`로 배포 창을 엽니다. **고급 설정 → NSIS 설치 파일 만들기 (권장)**는 기본으로 켜져 있습니다. 필요하면 **NSIS 컴파일러 (선택)**에서 승인된 `makensis.exe`를 고릅니다. 컴파일러를 준비할 수 없다면 선택을 해제하고 기존 ZIP 배포를 계속할 수 있습니다.

NSIS 설치 파일이 포함된 빌드에서는 Setup과 업데이트용 ZIP을 함께 게시합니다. 사용자에게 처음 전달하는 파일은 Setup이고, 앱이 읽는 채널 정보는 계속 ZIP을 가리킵니다. 사내 서버 주소는 기존 방식대로 빌드할 때 주입하며, 사용자에게 토큰이나 SSH 키를 배포하지 않습니다.

이미 GitLab에 게시한 같은 버전의 파일·체크섬은 다른 내용으로 바꾸지 않습니다. **사내에 0.23.25를 이미 게시했다면 설치 파일을 추가한 다음 배포에는 더 높은 버전이 필요합니다.** GitHub의 직접 EXE 교체와 사내 GitLab의 게시 정책은 별개입니다. 사내 같은 버전 패키지는 덮어쓰지 말고 다음 버전 소스를 받아 게시하세요. 단순 게시 실패를 재시도할 때는 같은 검증된 파일을 재사용할 수 있습니다.

자세한 서버·토큰 설정은 [소스 ZIP으로 사내 GitLab에 배포하기](WORKSPACE_GITLAB_PUBLISHER.md)를 참고하세요.
