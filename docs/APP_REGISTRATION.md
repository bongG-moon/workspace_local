# 설치 현황 집계 배포 설정

웹의 집계 POST 시험이 정상이라면 현재 웹은 그대로 사용합니다. 앱 배포 설정에는 웹 서버와 같은 등록용 인증키를 넣습니다. MongoDB URI, 데이터베이스 이름과 컬렉션 이름은 웹 서버에만 두며 앱에는 넣지 않습니다.

기본 웹 주소는 `http://pntaisquare.skhynix.com`입니다. 이 주소를 계속 사용한다면 배포 담당자는 인증키만 한 번 입력하면 됩니다. 실제 요청 경로 `/api/ax-workspace/registrations`는 앱이 붙입니다.

## 사내 배포 파일 만들기

저장소 루트의 PowerShell에서 다음 명령을 실행합니다. 인증키는 터미널의 숨김 입력으로 받으며 명령행 인수나 로그에 남기지 않습니다.

```powershell
python -X utf8 -B scripts/prepare-workspace-registration.py
```

생성 위치는 Git에서 제외된 `build/private-config/workspace-registration.json`입니다. 이미 파일이 있으면 덮어쓰지 않습니다. 키를 바꿀 때는 다른 `--output` 경로로 새 파일을 만든 후 배포 대상 설정을 확인합니다. 웹 주소가 달라지면 `--base-url http://새주소`를 추가합니다. 비밀번호나 키를 주소에 넣지 않습니다.

사내 EXE와 설치 파일을 만들 때만 이 설정을 명시합니다. 기존 업데이트 서버와 사용량 조회 설정을 사용한다면 기존 `-UpdateConfig`, `-UsageConfig` 인수도 함께 전달합니다.

```powershell
$registrationConfig = 'build/private-config/workspace-registration.json'
. .\deploy\Workspace.Version.ps1
$version = Sync-WorkspaceVersion -RepoRoot (Get-Location).Path -Check
& deploy/New-WorkspaceStandalone.ps1 -OutputDirectory dist/internal-registration -RegistrationConfig $registrationConfig
& deploy/New-WorkspaceRelease.ps1 -StandaloneExe "dist/internal-registration/AX-Workspace-$version.exe" -OutputDirectory dist/internal-registration-release -RegistrationConfig $registrationConfig -IncludeInstaller
```

위 명령은 `local_app/server.py`의 `WORKSPACE_VERSION`을 기준으로 현재 버전과 배포 메타데이터를 읽기 전용으로 확인합니다. 버전을 올리거나 파일을 수정하지 않습니다. NSIS 설치기는 기존 방식으로 새 EXE를 감싸므로 별도 사번 입력 페이지를 설치기에 추가하지 않습니다. 설정은 EXE의 앱 파일에 함께 들어가며 설치 후 첫 실행에서 사번을 입력받습니다.

`-RegistrationConfig`를 생략한 기본 빌드에는 인증키와 설정 파일이 포함되지 않고 집계 기능이 꺼집니다. 선택한 설정이 EXE와 ZIP에서 일치하지 않으면 릴리즈 묶음 검증에 실패합니다. 설정을 넣은 EXE, 설치기, ZIP과 검증용 스테이징 폴더는 사내 배포 전용입니다. 실제 설정 파일과 키가 포함된 배포 파일은 공개 Git 또는 공개 릴리즈에 올리지 않습니다.

## 사용자 화면과 동작

사번이 없을 때만 입력받습니다. 이미 저장되어 있으면 사번을 다시 표시하거나 추가 확인창을 띄우지 않고 기존 업데이트를 진행합니다. 앞뒤 공백을 제거한 1~8자만 허용하며 사번은 현재 Windows 사용자 앱 설정에 저장합니다. 설정 화면에서 변경할 수 있습니다.

안내 문구:

> 설치 또는 업데이트가 완료되면 결과를 한 번 전송합니다.
>
> 이 집계 기능은 평소 앱 사용 중 사용 정보를 수집하지 않습니다.

최초 설치 파일로 설치한 앱의 정상 첫 실행 또는 앱 내부 업데이트를 마치고 새 버전의 실행이 확인된 경우에만 집계합니다. 사번과 버전, 설치 또는 업데이트 구분을 보내며 완료 시각은 웹 서버에서 기록합니다. 같은 버전은 로컬에서 한 번만 시도하고 서버도 사번과 버전으로 중복을 방지합니다. 실패해도 업데이트와 앱 사용을 계속 허용합니다. 주기적인 집계, 일반 사용 감시, 대화와 파일 내용 전송은 추가하지 않습니다. portable 실행이나 파일 수동 교체는 집계 시작 조건이 아닙니다.

## 운영 설정의 보관

앱은 사내 빌드에 포함된 설정을 `%LOCALAPPDATA%\CompanyAgent\local-ui\workspace-registration.json`에 보관해 이후 앱 내부 업데이트에서도 이어서 사용합니다. 새 빌드에 유효한 사내 설정이 있으면 그 설정을 우선 적용하고 사용자 설정에도 보관합니다. 빌드에 설정이 없을 때만 보관된 사용자 설정을 사용합니다. 빌드에 설정이 있으나 올바르지 않으면 이전 주소로 조용히 전송하지 않고 집계를 끕니다. 운영 주소나 인증키를 교체할 때는 웹 서버와 새 사내 배포 설정을 같은 값으로 갱신합니다. 앱 화면이나 API 응답에는 인증키를 노출하지 않습니다.

기존 웹의 로그인과 CSRF 보호는 그대로 두고 집계 경로의 기존 HMAC 인증을 사용합니다. 앱의 등록용 키는 일반 사용자가 입력하지 않습니다.
