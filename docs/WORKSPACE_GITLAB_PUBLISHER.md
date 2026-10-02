# 사내 GitLab에 그대로 옮겨 배포하기

외부 개발 저장소에서 완성한 소스를 사내로 가져온 뒤 Python 배포 도구를 실행하는 방식입니다. 소스의 주소나 버전 문자열을 사내에서 직접 고칠 필요가 없습니다. 배포 도구는 별도 작업 폴더에서 실행 파일을 만들고 사내 업데이트 설정을 넣습니다. 원본 커밋과 개인 Claude·SSH 설정은 유지합니다.

## 역할

- 개발자는 외부 저장소에서 구현과 검증을 마치고 버전을 올립니다.
- 사내 게시자는 새 소스를 반입하고 Python 배포 도구에서 저장소 반영, 빌드, 게시를 진행합니다.
- 앱 사용자는 처음 받은 사내 배포본을 실행합니다. 이후에는 앱의 설정에서 변경 내용을 읽고 업데이트합니다. GitLab 로그인이나 SSH 키는 필요하지 않습니다.

## 처음 준비하기

사내 GitLab 프로젝트에서 Package Registry와 **Allow anyone to pull from package registry**를 켭니다. 소스 저장소는 Private로 유지할 수 있습니다. 이 설정은 패키지 파일 다운로드에 적용되므로 비공개 Releases API를 앱에서 호출하지 않습니다.

사내 소스 폴더에서 다음을 실행하면 배포 창이 열립니다.

```powershell
python -X utf8 Publish-Workspace.py
```

GitLab HTTPS 주소, 숫자 Project ID, 소스를 반영할 SSH 주소를 입력합니다. 설정은 Git에서 제외된 `build/publisher/` 아래에 저장됩니다. 게시용 토큰은 파일에 저장하지 않으며 게시할 때만 입력합니다. 사용자 앱에 포함되는 설정에는 서버 주소와 프로젝트 정보만 들어갑니다.

게시용 Deploy Token에는 해당 프로젝트의 `write_package_registry` 권한을 부여합니다. Git 저장소 푸시는 사내 PC에 이미 설정된 본인의 Git 인증을 사용합니다. Git에 사용하는 SSH 키와 패키지 게시용 토큰은 서로 다른 용도입니다. 사내 정책이 Deploy Token을 금지하면 지원하는 CI Job Token 방식이나 관리자가 허용한 게시 환경을 사용합니다.

## 매번 배포하기

1. 새 소스와 태그를 사내로 가져옵니다. 버전이 이전 배포보다 높은지 확인합니다.
2. 배포 도구에서 대상 주소와 커밋을 확인하고 소스를 사내 저장소에 반영합니다. 다른 브랜치를 강제로 덮어쓰지 않습니다.
3. 빌드를 실행합니다. 원본 저장소가 아닌 별도 작업 폴더에 현재 커밋을 펼치고 사내 설정을 포함한 EXE/VBS ZIP을 만듭니다.
4. 이번 버전의 변경 내용을 입력하고 게시를 실행합니다. ZIP 두 개와 체크섬 파일을 먼저 올립니다.
5. 도구가 인증 없는 다운로드와 해시 일치를 확인한 뒤 최신 버전 정보 파일을 마지막으로 게시합니다. 마지막 확인까지 성공해야 완료로 표시됩니다.
6. 첫 사내 버전은 사용자에게 ZIP 링크로 전달해 한 번 직접 실행하게 합니다. 이후 버전은 앱 업데이트로 시험합니다.

게시가 중간에 실패하면 같은 빌드로 재시도합니다. 배포 창을 다시 열 때 직전 빌드의 파일과 배포 대상이 맞는지 확인해 복원합니다. 새 빌드가 필요하지 않은 네트워크 오류 때문에 같은 버전을 다시 만들지 않아도 됩니다. 대상 서버를 바꾸거나 파일이 변조되었다면 복원하지 않고 다시 빌드하도록 안내합니다.

현재 실행 중인 공개 배포본과 같은 버전 번호로 사내 전환을 시도하면 기존 앱을 재사용할 수 있습니다. 최초 사내 전환은 실제 사용 중인 버전보다 높은 소스로 진행하세요. 게시된 버전의 파일 내용을 바꾸지 말고 새 버전으로 배포합니다.

## 외부 인터넷이 차단된 사내 PC

Git bundle로 커밋과 태그를 반입할 수 있습니다. 회사에서 허용하는 반입 경로를 이용합니다. GitHub의 일반 **Download ZIP / Source code ZIP**에는 Git 이력이 없으므로 게시 도구의 원본 커밋 검증에 사용할 수 없습니다. Git clone한 전체 저장소나 아래 bundle 방식을 사용합니다.

```powershell
# 외부 PC의 깨끗한 소스 저장소
git bundle create workspace-local.bundle --all
git bundle verify workspace-local.bundle

# 사내 PC의 반입 파일 폴더: 최초 한 번
git clone .\workspace-local.bundle workspace_local
```

이후 새 bundle을 가져왔을 때는 사내 작업 폴더의 수정 사항을 먼저 정리하고 해당 bundle에서 대상 커밋을 가져옵니다. 사내 저장소가 외부 GitHub에 접근 가능하다면 일반 Git fetch/clone 경로를 사용할 수도 있습니다.

빌드 PC에는 Git, Python 3.11 이상, .NET Framework x64 C# 컴파일러가 필요합니다. 전체 프런트엔드 회귀검사에는 Node.js가 필요합니다. 외부 NuGet이 막혀 있으면 `deploy/WebView2.lock.json`에 지정한 SDK 파일을 `build/desktop-sdk/<version>.nupkg`로 반입합니다. 해시가 다르면 빌드가 중단됩니다. 이 SDK는 빌드용이며 사용자 PC의 WebView2 Runtime과 다릅니다.

## 배포 파일과 최신 버전 정보

- 버전별 파일: `company-workspace/<버전>/Company-Workspace-<버전>-exe.zip`, VBS ZIP, `SHA256SUMS.txt`.
- 고정 최신 정보: `company-workspace-channel/0.0.0/latest.json`.
- 앱은 GitLab Generic Package의 프로젝트 다운로드 경로를 사용합니다. 최신 정보 안의 임의 주소로 이동하지 않습니다.
- 버전 정보와 변경 내용도 파일로 제공하므로 사용자에게 GitLab API 토큰을 줄 필요가 없습니다.
- 이미 있는 버전 파일이 정확히 같으면 재사용할 수 있습니다. 다르면 새 버전이 필요합니다.

GitLab은 중복 패키지 파일 정책이 설치별로 다를 수 있습니다. 채널 파일을 올린 뒤 익명 다운로드 결과가 방금 올린 내용과 같은지 반드시 확인합니다. 중복 업로드 거절이나 오래된 내용이 확인되면 도구는 실패로 표시합니다. 이때 관리자와 함께 채널의 `latest.json`만 정리하고 게시를 재시도합니다. 도구가 프로젝트나 기존 버전 패키지를 광범위하게 삭제하지 않습니다.

사내 HTTPS 인증서와 실제 파일 저장소 리디렉션이 사용될 수 있습니다. 인증서 검증을 해제하지 말고 사내 인증서 체인을 준비합니다. 별도 파일 저장소로 이동하는 환경은 확인된 HTTPS 원본 주소만 배포 설정에서 허용합니다.

## 현장 확인

- 일반 사용자 PC에서 로그인·토큰 없이 버전 파일과 ZIP이 내려오는지 확인합니다.
- 수정한 배포본에서 새 버전과 한글 변경 내용을 표시하는지 확인합니다.
- 진행 중인 업무, 초안, 첨부가 업데이트 뒤 복원되는지 확인합니다.
- 앱을 정상 종료한 뒤 원래 EXE로 최신 설치 버전이 열리는지 확인합니다.
- 다운로드 오류, 잘못된 해시, 로그인 HTML은 성공으로 처리하지 않아야 합니다.

외부 개발 환경의 자동검사와 모의 서버 시험은 사내 GitLab의 정책·인증서·연결 실측을 대신하지 않습니다.

공식 문서: [익명 패키지 다운로드](https://docs.gitlab.com/user/packages/package_registry/#allow-anyone-to-pull-from-package-registry), [Generic Packages](https://docs.gitlab.com/user/packages/generic_packages/), [Deploy Token](https://docs.gitlab.com/user/project/deploy_tokens/).
