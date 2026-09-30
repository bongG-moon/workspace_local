# 0.21.0 대화 이미지·표·코드·실행 기록 검증

검증일: 2026-09-30. Windows 개발 PC에서 수행했습니다. 설계와 참고 서비스는 [대화 결과 설계 문서](WORKSPACE_0.21.0_RICH_CHAT.md)에 정리했습니다.

## 자동 검사

- 실행 수집·복원, 이미지·표·코드 미리보기, HTML 경계, 파일 열기, 연결 초기화, 자동완성, effort, 대화 분기, 종료와 EXE 시작 경로를 포함해 **316개 실행, 310개 통과, 조건부 제외 6개**, 실패·오류 0개입니다.
- 제외 항목: 선택 설치하는 Company Agent 연동 자료 2개, 별도 옵션으로 실행하는 실제 사용자 토큰 진단 1개, 현재 계정에서 만들 수 없는 심볼릭 링크 검사 3개입니다. Company Agent 설치는 앱 실행 조건이 아닙니다.
- 마지막 스트리밍 응답의 요청 ID 전달 보정 후, 실행 카드 배치·스트림·기존 프런트엔드 검사 **42개를 다시 실행해 모두 통과**했습니다. 위 검사와 겹치는 수를 총합으로 합산하지 않았습니다.
- JavaScript 구문 검사와 `git diff --check`를 통과했습니다.

새 기능의 재현 가능한 검사 모듈은 `test_workspace_executions`, `test_workspace_execution_view`, `test_workspace_rich_content_frontend`, `test_workspace_file_preview`입니다. 예:

```powershell
python -B -X utf8 -m unittest discover -s tests -p test_workspace_execution_view.py
```

샌드박스 계정에서 Windows 실행기 검사 3개가 WS-31로 중단된 최초 결과는 통과로 취급하지 않았습니다. 실제 로그인 사용자 컨텍스트에서 위 전체 검사를 다시 실행한 결과가 316개 기록입니다.

## 화면 확인

`scripts/test-lab/preview-rich-chat.py`의 합성 대화·파일로 브라우저에서 확인했습니다. 실제 AI 실행 여부와 구별하도록 화면 체험 배너를 유지했습니다.

- 대화 내 표와 Python 코드의 문법 색상, 표 복사·코드 복사·접기
- 로컬 이미지 표시와 확대, CSV 표 미리보기와 소스 파일 읽기 전용 미리보기
- 실행 카드의 명령·출력·상태, 접힌 상태에서 내용 DOM을 만들지 않는 동작
- 페이지 재로드 후 대화·실행 카드·결과 파일 복원
- 900×760과 1440×1000 크기에서 배치 확인, 넓은 화면에서 가로 넘침 없음
- 확인한 브라우저 콘솔 오류 0개

이미지 수·화소·파일 크기, 표 행·열·문자 수, Markdown과 코드 DOM 복잡도, 실행 기록 수·총문자 수 제한도 검사했습니다. 이는 제한 정책과 동작 검증이며 모든 운영 PC의 최대 메모리 사용량을 측정한 성능 벤치마크는 아닙니다.

## 최종 EXE·실제 Claude 실행

0.21.0 최종 EXE를 다른 한글 경로의 EXE만 있는 폴더에 복사하고, 격리된 앱 상태·캐시로 검증했습니다.

- WebView2 전용 창으로 시작, 반복 실행에서 같은 서버 PID와 캐시 재사용
- 설치된 Python 3.13.5 사용, Python 설치 파일·다운로드 없음
- 기존 Claude Code 2.1.285 자동 탐지, 기존 인증으로 초기화 성공
- 연결에서 모델 `claude-sonnet-5-5`, effort `medium`, 명령 141개 확인
- 별도 테스트 업무에 합성 숫자 120·156·192를 합산하는 Python 스크립트 준비
- Claude가 실제 PowerShell 도구로 스크립트 1회 실행: `WORKSPACE_RICH_OK total=468`, 종료 코드 0
- 해당 명령만 이번 한 번 승인, 지속적인 허용 규칙·승인 모드 변경 없음
- 생성된 `metrics.csv` 내용과 3행 표 미리보기 확인
- 실행 기록·응답·생성 파일의 동일 요청 ID 연결 확인, 업무 완료와 승인 요청 없음 확인
- 테스트 앱 정상 종료, 검사한 개인 설정 파일 변경 0개

개인 설정 비교 대상은 기존 Claude 구성 루트의 `settings.json`, `settings.local.json`, `CLAUDE.md`, `.mcp.json`, `plugins/installed_plugins.json`입니다. Claude가 정상 실행 중 만드는 자신의 대화 기록까지 변경 없음으로 주장하지 않습니다.

## 배포 파일

- VBS ZIP을 새 격리 폴더에 추출하여 실제 시작 경로, 인증 API 경계, 정적 화면·글꼴·사용법, 업무·파일·카탈로그 API 및 정상 종료 확인
- EXE와 VBS ZIP의 동일 배포 내용 100개 확인: 소스 93개, 고정 네이티브 빌드 파일 7개
- 앱 상태·개인 인증 정보·Python 실행 파일 미포함 확인

| 파일 | SHA-256 |
| --- | --- |
| Company-Workspace-0.21.0.exe | a500123cdfb3d4053bcca3d72304bd4669540d5e331e1960b04e559e2df8420f |
| Company-Workspace-0.21.0-exe.zip | fa7c43ac45a6918f799ccc05c932f49de6b89fdd48ec8bc851777f0540c15816 |
| Company-Workspace-0.21.0-vbs.zip | be641e3265ec53ab16f48552773c8ada7985b8c372612e93d1dd01ed2dec10e6 |

로컬 상세 기록은 `build/qa-rich-chat-tests.json`, `build/qa-rich-chat-visual/`, `build/qa-standalone-0.21.0-rich-chat/`에 보관합니다. 인증 토큰을 포함하는 런타임 URL과 개인 환경의 전체 기록은 배포하지 않습니다.

## 검증 한계와 기존 예약 결함

실행 카드는 앱이 이번 버전부터 관찰한 최상위 Bash·PowerShell 호출을 표시합니다. 기존 Claude 과거 대화나 하위 에이전트의 모든 실행을 재구성하지 않습니다. 코드 블록 자체는 실행되지 않으며 실제 실행은 기존 Claude CLI와 승인 경로를 사용합니다.

모든 Windows·Claude 버전·관리 정책 조합의 운영 검증은 아닙니다. 이전에 확인한 예약 기능의 재시작 후 폴더 확인 연결, 완료된 일회성 예약 수정 후 비활성 상태, CLI 한도에서 확인 필요 상태 고정 문제 3개는 이번 변경 범위에 포함되지 않았습니다. [기존 예약 검증 기록](VALIDATION_0.20.1_SCHEDULING.md)을 유지하며, 이를 이번 통과 수에 포함하지 않았습니다.
