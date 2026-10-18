# AX Workspace 0.23.27 통합 배포 검증

검증일: 2026-10-18. Windows, Python 3.13.5, Node 24.19.0, NSIS 3.12 환경에서 확인했습니다.

사내 마지막 운영 버전인 **0.23.26 이후 변경을 0.23.27 하나로 통합**했습니다. UI 환경 개선, 일반 대화, 간편 화면과 릴리즈 노트 이력 기능을 모두 유지합니다. 별도로 작성했던 0.23.28 변경 내역을 0.23.27에 합치고 앱·실행기·사용 안내서·배포 도구의 현재 버전을 맞췄습니다. 시작 진단은 `ws33-59`, 대상 소스는 `workspace-0.23.27`입니다.

## 이번 수정본의 자동 검사

파일·폴더 목록, 중첩 이동, 첨부 전송, 미리보기·외부 앱 열기·새로고침·자동완성, 일반 대화·예약, 버전·시작 진단·번들·릴리즈 이력·업데이트 패키징을 함께 검사했습니다. 화면 전환 상태 검사 4개를 포함해 최종 **519개 검사 실행: 515개 통과, 4개 건너뜀, 실패·오류 0개**입니다. 이번 재실행에서는 실패가 없었습니다. 별도 실행 허용이 필요한 실제 사용자 토큰 검사와 Windows 심볼릭 링크 권한 검사는 통과 수에 포함하지 않았습니다.

검사 모듈: `test_workspace_motion`, `test_workspace_file_preview`, `test_workspace_external_apps`, `test_workspace_artifact_refresh`, `test_workspace_file_refresh_frontend`, `test_workspace_completions`, `test_workspace_send_attachment_admission`, `test_workspace_drop_upload`, `test_workspace_plain_chat`, `test_workspace_frontend_state`, `test_workspace_work_queue`, `test_workspace_dispatch_routes`, `test_workspace_dispatch_resources`, `test_workspace_bundle_check`, `test_workspace_file_diff`, `test_workspace_picker_native`, `test_workspace_schedule_regressions`, `test_workspace_chat_attachments`, `test_workspace_diagnostic`, `test_local_workspace`, `test_workspace_publisher_release_notes`, `test_workspace_release_history`, `test_workspace_release_history_routes`, `test_workspace_release_history_integration`, `test_workspace_release_history_frontend`, `test_workspace_startup_health_frontend`, `test_workspace_update_packaging`.

## 파일·폴더 탐색과 빈 목록 안내 보완

- 첨부와 동일한 106개 확장자(EXE·ZIP 등)를 대소문자 구분 없이 표시하고, 폴더는 직접 열거나 단일 경로로 첨부합니다. 디렉터리 조회는 한 위치만 읽으며 하위 파일을 미리 펼치거나 복사하지 않습니다. 시간·항목 수 제한, 인증·업무 범위·비공개 파일·연결 경로 제외를 검사했습니다. 실행 파일과 스크립트는 참조할 수 있어도 기본 앱 실행은 차단됩니다.
- 실제 브라우저에서 EXE·ZIP·7z·폴더 표시, 폴더 경로 첨부, 중첩 폴더 진입 → 상위 폴더 → 업무 폴더 복귀와 새로고침 후 위치 유지를 확인했습니다. 삭제된 폴더의 처음 위치 복귀와 늦은 응답 무시는 자동 검사로 확인했습니다. 빈 목록은 제목 13px, 설명 12px·줄 간격 20px이며 라이트·다크 및 260px·292px 패널의 앞선 검증도 유지합니다. 이번 폴더 이동 브라우저 검사에서 오류는 없었습니다.
- 버전은 0.23.27을 유지하고, 아래 배포 파일 해시는 이번 재빌드 결과로 갱신했습니다.

## 화면 전환 효과

- 팝업 140ms, 화면 120ms, 자료·결과 탭 100ms의 진입 효과를 추가했습니다. 닫기·입력·전송을 지연하지 않고 스트리밍 갱신에 반복하지 않으며, OS의 동작 줄이기를 따릅니다.
- WebView2 격리 fixture의 360회 반복에서 95백분위 프레임 간격은 16.8~17.0ms, 50ms 초과 작업은 0회였습니다. 효과 사용 중 프로세스 메모리는 초기 기준보다 약 6~13MiB 높았으나 계속 증가하지 않았고, 종료 후 활성 애니메이션은 0개였습니다. 실제 사내 PC의 장시간 전체 앱 측정은 아닙니다.
- 기능·성능 측정 조건과 한계는 [화면 전환 효과 검증](VALIDATION_UI_MOTION.md)에 기록했습니다.

## 실제 배포 파일 검증

- 독립 실행 EXE를 다시 빌드하고 `--verify-only`로 내장 파일 **171개**를 검증했습니다. EXE 내용과 자동 업데이트용 VBS ZIP의 171개 파일이 바이트 단위로 일치합니다.
- ZIP 허용 목록 검사에서 소스 **164개**와 고정된 WebView2 빌드 자산 **7개**를 확인했습니다. 시작 진단의 네 파일 해시도 UTF-8 BOM 제거·LF 정규화 기준으로 동기화했습니다.
- 새 VBS ZIP을 임시 폴더에 풀어 빈 상태의 `demo/no-browser` 서버로 실행했습니다. **9개 검증 그룹·21개 HTTP 요청**이 통과했습니다.
- 실제 서버의 버전은 0.23.27이며, 이력 API와 내장 JSON의 현재 설명이 `RELEASE_NOTES_0.23.27.md`와 일치합니다. UI 개선·일반 대화·간편 화면·업데이트 이력·사내 0.23.26 업데이트 설명을 포함하고, 별도의 0.23.28 이력 항목은 없습니다.
- 인증 없는 API 요청은 403, 잘못된 이력 요청은 400으로 거부합니다. 이력 조회·재확인, 정적 HTML·JS·CSS·사용 안내서 일치와 정상 종료를 확인했습니다. 종료 코드 0, 오류 로그 0바이트이며 runtime과 임시 상태·추출 폴더를 정리했습니다.
- NSIS 설치본은 정식 제품 ID `AX.Workspace.Desktop`, 사용자별 설치, 파일 버전 `0.23.27.0`으로 컴파일했습니다. 테스트용 설치 ID를 사용하지 않았습니다.

| 파일 | SHA-256 |
| --- | --- |
| ZIP 안의 독립 실행 EXE | `d6dfeda58e6165eb09a663752461db5c33e8b48199bb0c809c4bb062f0ebf7dd` |
| AX-Workspace-0.23.27-vbs.zip | `5ebf5757ff5417770b83b280449f75676820300ce98a81aee333eb926ed64399` |
| AX-Workspace-0.23.27-Setup.exe | `dac9d25cda9bfd71045a34c5c11fd4e980f4aaf0d18093f8e833a00fb140b088` |

공개 다운로드의 전체 해시는 [SHA256SUMS.txt](https://github.com/bongG-moon/workspace_local/releases/download/v0.23.27/SHA256SUMS.txt)를 기준으로 확인하세요. 직접 EXE 이름은 NSIS 설치본의 호환 이름이며, EXE ZIP에는 기존 독립 실행기를 제공합니다. Python·Claude Code·개인 인증·대화 상태는 포함하지 않습니다.

## 포함된 기능의 앞선 검증

아래는 통합 전 기능 구현 단계의 기록이며 이번 버전 정리에서 전체를 다시 실행한 것으로 계산하지 않습니다. 통합 시 버전·설명·진단 해시·패키징을 정리했으며 아래 기능 구현을 유지합니다. 파일·폴더 탐색 보완의 별도 검증은 위에 기록했습니다.

- 간편 화면 23개와 우측 패널 12개, 총 **35개 브라우저 시나리오** 통과, 페이지 오류 0개. 화면 전환 시 업무·입력·첨부·승인·진행 중 응답 유지, 설정 복원, 중복 패널 버튼 제거, 드래그·키보드 너비 조절과 라이트·다크·좁은 화면을 확인했습니다.
- 릴리즈 이력·GitLab 이전 배포 기록 구현: 중복을 제외한 **493개 검사 통과, 심볼릭 링크 환경 검사 3개 건너뜀**. 별도 **11개 브라우저 시나리오**에서 지연 조회·캐시·오프라인·서버 설명 우선 반영·출처 구분·키보드·1440/640/360px 화면을 확인했습니다.
- 기존 전체 검사 기록은 **2843개 실행, 2792개 통과·49개 건너뜀·2개 실패, 오류 0개**입니다. 당시 진단 해시와 종료 연결 테스트 기대값을 보완한 뒤 관련 12개 검사 및 최종 번들 검사를 통과했습니다. 최종 소스로 전체 2843개가 다시 통과했다고 표현하지 않습니다.
- 일반 대화·첨부의 앞선 실제 CLI 검증과 성능 측정은 [2026-10-16 기록](VALIDATION_CHAT_MODES_LOCAL.md)에 범위를 구분해 보존했습니다.

## 배포 적용 범위

사내 운영 환경의 **0.23.26 → 0.23.27** 업데이트를 위한 통합본입니다. 같은 GitLab 프로젝트에 소스 ZIP으로 새 패키지와 채널을 게시해야 하며, 이 환경에서 실제 사내 GitLab에 접속하거나 게시하지 않았습니다. 기존 서버 설명 반영은 모의 서버로 검증했습니다.

이미 이전 공개 0.23.27을 설치했다면 같은 버전이라 자동 업데이트 알림이 뜨지 않습니다. 트레이에서 앱을 완전히 종료한 뒤 새 설치본을 적용해야 하며, 기존 관리 실행기 바로가기가 같은 버전의 이전 파일을 가리키는지도 확인해야 합니다. 공개 0.23.28을 설치한 PC는 버전 하향 보호와 보존된 업데이트 경로가 있어 별도 전환 확인이 필요합니다. 단순 재설치만으로 0.23.27로 바뀐다고 보장하지 않으며, 개인 설정·업무를 일괄 삭제하는 방법을 사용하지 않습니다.

이번 검증에서 사용 중인 앱에 실제 설치·제거하거나 실제 Claude·회사 인증·MCP·DRM·Office를 실행하지 않았습니다. 새 패키지 검증은 격리한 상태로 수행했습니다.
