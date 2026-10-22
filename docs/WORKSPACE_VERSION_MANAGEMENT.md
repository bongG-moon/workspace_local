# 버전 관리

현재 버전의 원본은 `local_app/server.py`의 `WORKSPACE_VERSION` 하나입니다. 시작 스크립트, Python 실행 진단, 독립 실행 진단과 번들 이름에 필요한 값은 이 원본에서 생성합니다. 앱 실행 중 새 파일을 읽거나 반복 조회하지 않습니다.

## 버전 변경

1. `local_app/server.py`의 `WORKSPACE_VERSION`만 수정합니다.
2. 저장소 최상위에서 `powershell -NoProfile -File scripts/Sync-WorkspaceVersion.ps1`을 실행합니다. 필요한 상수와 시작 진단 해시가 함께 갱신됩니다. 생성된 diff를 검토합니다. 해시 생성은 코드 검토를 대신하지 않습니다.
3. 변경사항 Markdown을 작성하고 기존 `scripts/write-workspace-release-notes.py --version <버전> --notes-file <경로>`로 해당 버전의 배포 이력을 갱신합니다. 과거 버전의 설명은 버전 동기화 도구가 수정하지 않습니다.
4. `powershell -NoProfile -File scripts/Sync-WorkspaceVersion.ps1 -Check`로 일치 여부를 확인합니다. 소스를 stage한 뒤 기존 `scripts/write-workspace-source-manifest.py`로 소스 목록을 만들고 그 목록도 stage합니다.

모든 `deploy/New-Workspace*.ps1` 빌드는 파일 생성이나 다운로드 전에 같은 읽기 전용 검사를 실행합니다. 생성된 값·진단 해시가 오래됐거나 배포 이력의 현재 버전이 다르면 빌드를 중단합니다. 빌드가 소스를 몰래 변경하지 않으며, 잘못된 템플릿은 자동으로 추정하지 않고 수정 위치를 안내합니다.

생성은 UTF-8 BOM과 기존 줄바꿈, 버전과 관련 없는 수정 내용을 유지합니다. 버전 원본은 기존 형식 그대로여서 사내 배포 도구와 소스 ZIP 검증기가 동일하게 읽습니다. 사용자 PC의 Python/Claude 설치 및 개인 설정과 무관한 개발·빌드 단계 기능입니다.
