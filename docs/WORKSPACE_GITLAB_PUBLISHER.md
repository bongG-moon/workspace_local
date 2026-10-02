# 소스 ZIP으로 사내 GitLab에 배포하기

0.23.1부터 GitHub **Code → Download ZIP**을 그대로 가져와 사내용 배포 파일을 만들 수 있습니다. `.git` 폴더 복원이나 소스 파일 수정은 필요하지 않습니다. 소스 ZIP에는 완성된 EXE·DLL·WebView2 SDK 패키지가 들어 있지 않으며, 실행 파일은 사내 PC에서 만듭니다.

## 처음 시작하는 순서

1. GitHub 저장소에서 **Code → Download ZIP**을 선택합니다. 회사가 허용하는 경로로 ZIP을 옮기고 짧은 경로에 압축을 풉니다.
2. `Publish-Workspace.py`가 있는 폴더에서 아래 명령으로 배포 창을 엽니다.
3. 화면에 **소스 ZIP**, 버전, 소스 확인값이 표시되는지 확인합니다.
4. 사내 GitLab의 HTTPS 시작 주소와 숫자 Project ID를 입력하고 저장합니다.
5. **빌드 SDK 준비**에서 SDK를 다운로드하거나 승인된 SDK 파일을 선택합니다.
6. EXE/VBS 빌드를 실행하고, 변경 내용을 입력한 뒤 게시합니다. 소스에 사내 주소를 직접 적을 필요가 없습니다.

```powershell
python -X utf8 Publish-Workspace.py
```

소스 ZIP 방식의 빌드·게시에는 Git 설치가 필요하지 않습니다. 빌드 PC에는 Python 3.11 이상, Windows PowerShell, .NET Framework x64 C# 컴파일러가 필요합니다. 전체 개발용 프런트엔드 회귀검사에는 Node.js도 사용하지만 배포 빌드의 필수 조건은 아닙니다. Python에 tkinter가 없으면 `python -X utf8 Publish-Workspace.py --cli --help`로 명령형 기능을 이용할 수 있습니다.

메일에서 스크립트나 ZIP 자체도 차단할 수 있습니다. 소스 ZIP으로 바꿨다고 반입이 보장되는 것은 아닙니다. 확장자를 바꾸거나 실행 파일을 숨겨 넣지 말고 회사에서 승인한 소스 반입 경로를 사용합니다.

## SDK는 사내에서 한 번 준비합니다

SDK는 앱 창을 만드는 데 필요한 **빌드용 파일**입니다. 사용자 PC에서 앱 화면을 표시하는 WebView2 Runtime과는 다릅니다.

- 사내 PC에서 NuGet에 접속할 수 있으면 **SDK 다운로드**를 누릅니다. 지정된 버전만 내려받습니다.
- 사내에 승인된 HTTPS 파일 저장소가 있으면 해당 SDK 파일 주소를 입력하고 다운로드합니다. 주소에 계정·암호·토큰을 넣지 않습니다.
- 이미 사내에 승인된 `.nupkg` 파일이 있으면 **SDK 파일 선택**으로 가져옵니다.

배포 도구는 시작이나 빌드 시 자동으로 다운로드하지 않습니다. `deploy/WebView2.lock.json`에 지정한 버전과 SHA256이 맞아야 사용하며, 실패하면 기존의 정상 SDK를 보존합니다. 다운로드 주소는 설정에 저장하지 않습니다. SDK 파일이 없고 내려받을 수도 없다면 사내 담당자가 승인된 파일을 준비해야 빌드할 수 있습니다.

준비된 SDK는 이 소스 폴더의 `build/desktop-sdk/`에 저장됩니다. 다음 버전의 소스 ZIP을 새 폴더에 풀었을 때는 기존 폴더의 SDK 파일을 다시 선택해 사용할 수 있습니다. SDK를 메일로 함께 반입할 필요는 없습니다.

명령형 기능도 제공합니다.

```powershell
python -X utf8 Publish-Workspace.py --cli sdk-status
python -X utf8 Publish-Workspace.py --cli sdk-download
python -X utf8 Publish-Workspace.py --cli sdk-import --file "C:\approved-sdk\1.0.4258.31.nupkg"
```

사내 HTTPS 저장소를 사용한다면 `sdk-download --url "https://사내서버/SDK파일.nupkg"`을 사용합니다. 사내 인증서가 필요하면 승인된 인증서 환경을 준비하며 인증서 검증을 해제하지 않습니다.

## 사내 GitLab 준비

프로젝트의 Package Registry와 **Allow anyone to pull from package registry**를 켭니다. 소스 저장소는 Private로 유지할 수 있습니다. 앱은 프로젝트의 패키지 다운로드 경로를 사용합니다. [GitLab 공식 안내](https://docs.gitlab.com/user/packages/package_registry/#allow-anyone-to-pull-from-package-registry)

서버 주소에는 `https://gitlab.company.example`처럼 GitLab 시작 주소를 넣습니다. 프로젝트 페이지의 `/그룹/저장소` 부분은 제외합니다. GitLab 자체가 `/gitlab` 하위 경로에 설치되어 있다면 그 부분은 포함합니다. Project ID는 해당 프로젝트 화면에 표시되는 숫자입니다.

게시할 때에는 해당 프로젝트의 `write_package_registry` 권한을 가진 Deploy Token을 입력합니다. 토큰은 파일에 저장하거나 사용자 앱에 넣지 않습니다. 사내 정책에 맞는 CI Job Token 방식도 지원합니다. 사용자 앱에는 서버 주소와 프로젝트 정보만 포함되며, SSH 개인키나 GitLab 로그인은 필요하지 않습니다.

두 번째 배포부터 최신 정보를 갱신하려면 `company-workspace-channel`의 같은 파일 이름 게시가 허용되어야 합니다. 그룹의 **Settings → Packages and registries → Generic / Duplicate packages**에서 해당 채널의 중복 게시 정책을 담당자와 확인합니다. 다른 패키지의 정책까지 바꿀 필요는 없습니다. [중복 패키지 공식 안내](https://docs.gitlab.com/user/packages/generic_packages/#disable-publishing-duplicate-package-names)

## 매번 새 버전을 배포할 때

1. 최신 소스 ZIP을 별도 폴더에 풉니다. 버전은 이전 사내 배포보다 높아야 합니다.
2. 사내 GitLab 주소와 Project ID를 저장하고 SDK를 준비합니다. 이전에 검증한 SDK 파일을 다시 선택해도 됩니다.
3. 빌드를 실행합니다. 도구가 소스를 확인한 뒤 별도 작업 폴더에 복사하고 사내 업데이트 설정을 넣어 EXE/VBS ZIP을 만듭니다. 반입한 원본은 고치지 않습니다.
4. 이번 버전의 변경 내용을 입력하고 게시를 실행합니다. ZIP 두 개와 체크섬 파일을 먼저 올립니다.
5. 도구가 인증 없는 다운로드와 파일 해시를 확인한 뒤 최신 버전 정보를 마지막으로 게시합니다. 마지막 확인까지 성공해야 완료로 표시됩니다.
6. 첫 사내 버전은 사용자에게 ZIP 링크로 전달해 한 번 직접 실행하게 합니다. 다음부터는 앱의 **설정 → 앱 업데이트**에서 변경 내용을 확인하고 업데이트합니다.

공개 GitHub Release의 EXE/VBS는 공개 업데이트를 사용하는 배포본입니다. 사내 서버를 사용하는 첫 배포본은 위 순서로 사내에서 만들어야 합니다. 처음 사내로 전환할 때도 현재 실행 중인 앱보다 높은 버전을 사용하세요. 같은 버전은 이미 실행 중인 앱을 재사용할 수 있습니다.

게시가 중간에 실패하면 같은 빌드로 재시도합니다. 배포 창을 다시 열면 직전 빌드와 게시 대상이 맞는지 확인해 복원합니다. 네트워크 오류만으로 같은 버전을 다시 만들지 않아도 됩니다. 이미 게시한 버전의 파일이 달라지면 새 버전이 필요합니다.

## 소스 ZIP과 Git 저장소의 차이

ZIP에는 Git 이력이 없으므로 **Git 소스 반영** 기능만 사용하지 않습니다. 실행 파일 빌드, GitLab 패키지 게시, 앱 업데이트는 사용할 수 있습니다. GitLab에 소스 코드와 커밋 이력도 보관해야 한다면 기존 Git clone/bundle 방식이나 회사에서 정한 소스 등록 절차를 사용합니다. 도구가 임의로 Git 이력을 만들지는 않습니다.

소스 ZIP은 함께 들어 있는 `workspace-source-manifest.json`의 파일 목록과 SHA256으로 검사합니다. 누락·변경·예상 밖의 소스 파일이 있으면 빌드를 중단하며, 복사 후와 빌드 완료 시에도 다시 확인합니다. 이 목록은 전송 과정의 일치 여부를 확인하는 자료이지 서명이나 Git 커밋 인증이 아닙니다. 공식 저장소에서 소스를 받으세요.

기존 Git clone 방식은 깨끗한 커밋을 Git archive로 펼쳐 빌드하고, 확인한 소스를 사내 SSH 원격에 반영하는 기능을 유지합니다. `origin`과 개인 Git·SSH·Claude 설정은 바꾸지 않습니다.

개발자가 새 소스를 커밋할 때는 변경 파일을 먼저 stage한 뒤 다음 명령으로 확인 목록을 갱신하고 목록도 함께 stage합니다. 사내 사용자가 이 작업을 할 필요는 없습니다.

```powershell
python -X utf8 scripts/write-workspace-source-manifest.py
python -X utf8 scripts/write-workspace-source-manifest.py --check
```

## 배포 파일과 최신 버전 정보

- 버전별 파일: `company-workspace/<버전>/Company-Workspace-<버전>-exe.zip`, VBS ZIP, `SHA256SUMS.txt`.
- 고정 최신 정보: `company-workspace-channel/0.0.0/latest.json`.
- 앱은 GitLab Generic Package의 프로젝트 다운로드 경로를 사용합니다. 최신 정보 안의 임의 주소로 이동하지 않습니다.
- 설정과 빌드 기록은 Git에서 제외된 `build/publisher/` 아래에 보관합니다. 빌드 오류 안내에 표시되는 로그에서 실패한 단계를 확인할 수 있습니다.

채널의 중복 업로드가 거절되거나 오래된 정보가 내려오면 도구는 실패로 표시합니다. 관리자와 함께 해당 채널의 `latest.json`만 확인한 뒤 재시도합니다. 도구가 프로젝트나 기존 버전 패키지를 광범위하게 삭제하지 않습니다.

사내 HTTPS 인증서와 별도 파일 저장소가 사용될 수 있습니다. 인증서 검증을 해제하지 않고 사내 인증서 체인을 준비합니다. 별도 저장소로 이동하는 환경은 확인한 HTTPS 출처만 배포 설정에서 허용합니다.

## 첫 사내 배포에서 확인할 사항

- 일반 사용자 PC에서 로그인·토큰 없이 버전 정보와 ZIP이 내려오는지 확인합니다.
- 사내 배포본에서 새 버전과 한글 변경 내용을 표시하는지 확인합니다.
- 업무·초안·첨부가 업데이트 뒤 복원되는지 확인합니다.
- 앱을 정상 종료한 뒤 원래 EXE로 최신 설치 버전이 열리는지 확인합니다.
- 다운로드 오류, 잘못된 해시, 로그인 HTML을 성공으로 처리하지 않는지 확인합니다.

외부 개발 환경의 자동검사와 모의 서버 시험은 사내 GitLab의 정책·인증서·네트워크 실측을 대신하지 않습니다. 기존 PC 설정, Claude 인증·개인 설정은 그대로 유지합니다.
