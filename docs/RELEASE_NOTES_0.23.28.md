# AX Workspace 0.23.28 — 지난 업데이트 내역 보기

2026-10-18

- **업데이트 내역:** 설정 → 앱 업데이트 → 업데이트 내역에서 버전별 변경 내용을 확인합니다. 최신 버전을 사용 중이어도 열 수 있으며, 이력을 보는 것만으로 설치하거나 Claude에 연결하지 않습니다.
- **사내 기존 이력:** 사내 배포 도구가 같은 GitLab 프로젝트에 남아 있는 이전 배포 채널·릴리스·패키지 기록을 가져옵니다. 사내 배포 때 수정한 제목과 설명을 우선 반영하고 출처를 구분합니다.
- **설명을 확인할 수 없는 버전:** 파일만 확인되면 패키지 등록일과 함께 ‘당시 배포 설명을 확인할 수 없습니다’라고 표시합니다. 개발자가 작성한 기본 설명으로 대신 채우지 않습니다.
- **가벼운 조회:** 사용자 앱은 정리된 이력 파일만 읽고 최근 조회 결과를 재사용합니다. 과거 패키지 조회는 배포 도구에서 시간·개수·용량을 제한해 처리합니다. 연결이 안 되어도 저장된 이력과 앱에 포함된 변경 내용을 볼 수 있습니다.
- **배포 호환:** 기존 최신 버전 채널과 ZIP 업데이트 방식을 유지합니다. 이력 수집 일부가 실패하면 정상 배포와 구분해 안내하며, 예전 날짜 형식도 읽을 수 있도록 보완했습니다. 사용 안내서와 사내 배포 가이드도 갱신했습니다.

### 다운로드와 업데이트

[설치 파일](https://github.com/bongG-moon/workspace_local/releases/download/v0.23.28/AX-Workspace-0.23.28-Setup.exe) · [사내 반입용 소스 ZIP](https://github.com/bongG-moon/workspace_local/releases/download/v0.23.28/workspace-local-0.23.28-source.zip)

GitHub의 Setup·직접 EXE는 NSIS 설치 파일입니다. EXE/VBS ZIP은 기존 실행기와 앱 자동 업데이트용으로 함께 제공합니다. 기존 공개 배포 사용자는 앱의 새 버전 확인으로 업데이트할 수 있습니다.

앱 실행에는 Python 3.11 이상과 최신 Claude Code 설치·인증, 기존 Windows 실행 환경이 필요합니다. Python이나 Claude Code를 함께 설치하지 않습니다. 설치 파일로 갱신할 때는 트레이에서 기존 앱을 완전히 종료한 뒤 설치하세요.

사내 GitLab은 이 소스 ZIP으로 사내에서 별도로 배포해야 합니다. 새 배포 도구가 서버에 남아 있는 기록을 조회하며, 삭제된 설명을 복원하지는 못합니다. 서버 버전·보존 정책·토큰 권한에 따라 가져올 수 있는 범위가 다릅니다. 같은 서버·프로젝트에 새 패키지와 채널 정보를 게시하면 기존 사내 최신 설치용 `AX-Workspace-Install.exe`도 새 버전을 확인합니다. 공개 GitHub 갱신만으로 사내 배포가 바뀌지는 않습니다.

[검증 범위와 결과](https://github.com/bongG-moon/workspace_local/blob/v0.23.28/docs/VALIDATION_0.23.28.md) · [사용 안내서](https://github.com/bongG-moon/workspace_local/blob/v0.23.28/docs/WORKSPACE_USER_GUIDE.html)

실제 사내 GitLab에는 접속하지 않았으며 모의 GitLab로 검증했습니다. 파일 무결성은 첨부한 `SHA256SUMS.txt`로 확인할 수 있습니다.
