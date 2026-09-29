# Workspace 단계별 구현·검증 기록

요구사항과 완료 기준: [R01~R14 구현 계획](WORKSPACE_IMPLEMENTATION_PLAN_R01_R14_2026-09-29.md).

현재 상태: **5단계 구현·개발 환경 검증 및 실행 ZIP 제작 완료**. 각 단계 검증을 통과한 뒤 다음 단계로 진행했다. 회사 PC에서만 가능한 수용시험은 별도로 남긴다.

후속 요청의 앱 실행·CLI 승인 경험 개선은 **0.12.1**에 반영했다. 아래 5단계는 0.12.0 당시 기록이며, 최신 추가 변경과 검증은 [0.12.1 검증 기록](VALIDATION_0.12.1.md)을 따른다.

| 단계 | 범위 | 상태 | 검증 |
| --- | --- | --- | --- |
| 1 | R01, R02, R04, R05 | 구현 및 개발 환경 검증 통과 | 통합 104건 중 99 통과, 5 생략. 별도 native 토큰 복사/자식 검증 통과 |
| 2 | R03, R07, R11 | 구현 및 개발 환경 검증 통과 | 통합 112건 모두 통과, 독립 검토 통과 |
| 3 | R10, R12, R13, R14 | 구현 및 개발 환경 검증 통과 | 통합 77건 통과, 마지막 UI 전환 회귀 23건 통과, 코어 125건 통과 |
| 4 | R06, R08, R09 | 구현 및 개발 환경 검증 통과 | 통합 99건 중 98 통과, symlink 생성 권한 1 생략. UI 관련 77건 통과 |
| 5 | 통합 회귀·배포 묶음 | 개발 환경 검증·ZIP 제작 완료 | Workspace 422 통과·7 생략, 별도 Windows 7 통과, 코어 134 통과. 독립 검토·ZIP 정합·압축 해제 실행 통과 |

Windows UAC·보안·계정 설정과 기존 Claude 인증·모델·MCP·개인 스킬·기억은 변경하지 않는다. 실제 회사 PC의 UAC-off 실행과 Office·보안 정책 조합은 이 개발 PC의 fixture 통과와 별도로 기록한다.

## 1단계 결과

- R01: 기존 linked-token 경로를 유지한다. 동일 사용자·세션의 UAC-off unsplit high 토큰에 한해 제한 복사본을 만들고, 관리자 SID·privilege·medium integrity와 suspended child를 확인한 뒤 재실행한다. 제한 실패 시 중단한다. 직접 Python 진입도 같은 사용자 검사를 수행한다.
- R02: stdout 파싱을 요청별 전용 JSON 파일 계약으로 바꿨다. 사용자 LocalAppData에 원자적인 SID/SYSTEM DACL을 적용하고 schema·경로·파일형식·크기와 정리를 검증한다. PowerShell은 OS가 반환한 절대 시스템 경로를 사용한다.
- R04: OS가 반환한 실제 Desktop의 Company Workspace 아래 새 업무를 생성한다. 화면에서 저장 위치를 변경할 수 있다. 기존 업무와 내부 상태는 이동하지 않는다. Cloud reparse 태그는 업무 경로에서 구별하여 허용하되 symlink/junction은 차단한다.
- R05: 전체 현대 한글 11,172자를 포함한 Noto Sans KR 가변 글꼴과 OFL을 앱에 동봉했다. 로컬 HTTP 제공과 새 업무 모달의 한글 표시·경로 줄바꿈을 확인했다.
- 통합 명령: `python -X utf8 -m unittest tests.test_workspace_picker tests.test_workspace_picker_integration tests.test_workspace_picker_native tests.test_workspace_product tests.test_workspace_frontend_state tests.test_workspace_direct_startup tests.test_workspace_startup tests.test_workspace_normal_token tests.test_workspace_diagnostic tests.test_workspace_reopen` → 104건, OK, skipped=5. JS syntax 및 diff whitespace 검사 통과.
- 실제 native 토큰 복사/자식 실행은 별도 opt-in 시험에서 통과. 현재 PC는 UAC-on limited 사용자이므로 UAC-off 원본에서 VBS→Claude 요청까지 성공했다는 뜻이 아니다.
- 남은 현장 확인: 실제 회사 PC UAC-off, 선택창 전경·복수 모니터 DPI, 회사 리디렉션 Desktop 생성, Company Agent 코어의 OneDrive 경로 호환. 테스트와 화면 검증은 임시 루트만 사용했다.

## 2단계 결과

- `/api/capabilities?scope=common` 또는 `scope=folder&workspace=...`를 추가했다. 기존 task-bound API는 호환 유지한다. 명시한 폴더와 runtime 업무가 다르면 거절하며, 목록 조회는 신뢰·업무 폴더·모델을 바꾸지 않는다.
- `HarnessClient.discovery_inventory`는 제한된 JSON/frontmatter만 직접 읽는다. 설치된 코어나 Python을 실행하지 않는다. Company Agent 등록 진단과 일반 Claude 스킬 발견을 분리한다.
- 현재 사용자 실제 메타데이터 읽기: common/folder 각각 52개 항목, readFailures=0, 등록 ready. 이는 실제 CLI 로드·호출 성공 검증이 아니다. configRootSource=default, actualCliContextVerified=false를 보존한다.
- 격리 브라우저: 업무 생성 없이 공통 일반 스킬 13개 표시, 폴더 미선택 상태, 범위 선택 컨트롤과 한글 레이아웃 확인. 실제 계정/모델 호출 없음.
- 중간 회귀: backend 84건 통과, frontend 12건 통과. frontend의 기존 상대 테스트 import로 최초 module invocation은 로더 오류였고 `unittest discover -s tests -p test_workspace_capabilities_frontend.py`로 재실행해 통과했다. 추가 회귀와 독립 검토가 끝난 뒤 단계 완료로 갱신한다.
- 최종 통합: capabilities / capabilities_frontend / discovery / skill_inventory / catalog_scope_routes / companion 6개 모듈, 112건 모두 통과. JS 구문 및 diff 검사 통과. 독립 검토에서도 남은 핵심 결함 없음.
- 조회 후보와 실제 연결 보고를 분리한다. 손상 목록과 조회 실패는 0개로 바꾸지 않으며, 같은 호출명의 출처가 다른 후보를 합치지 않는다. 프로젝트 설정의 disabled overlay와 명시적 scope도 검증했다.

## 3단계 결과

- R10/R14: 설치 CLI help에서 확인한 모드 이름과 실제 제어 응답을 사용한다. 현재 PC의 manual을 default로 바꾸지 않는다. 변경은 현재 연결에만 적용하며 거절·시간 초과·기존 설정 복원을 처리한다. 세션 허용은 CLI가 제공한 session 범위 규칙 중 사용자가 선택한 ID만 반환한다.
- R12: 종료 후크의 정상 보정과 실행 오류를 구분하고, 단순 프로세스 성공을 결과물 검증 성공으로 표시하지 않는다. 코어의 정확한 디자인 선택 대기만 Stop 보정 대상에서 일시 구별하며 기존 검증 의무와 보정 한도는 보존한다.
- R13: 등록된 helper의 구조화 응답만 10개 카드로 표시한다. CLI 도구 결과에서 임의 명령·문서의 JSON을 카드로 승격하지 않는다. 스타일 이름은 앱의 고정 ID 매핑으로 생성한다. 오래된 선택·중복 클릭·다른 업무 전환·stream replay를 검증했다.
- Workspace 통합: choices_hooks/control_routes/permissions/transport_product/controls_frontend/frontend_state/dialogs 77건 통과. 마지막 A 요청 대기 중 B 전환 회귀 포함 UI 23건 통과. 독립 리뷰에서 발견한 출처 검증·surrogate·전환 잠금 문제를 수정 후 통과했다.
- 코어 관련 7개 suite 125건 통과, 추가·교차 28건 통과. 격리 child가 실제 소스 helper의 선택 JSON을 생성하고 HTTP 선택 응답이 같은 child의 다음 사용자 턴으로 1회 전달되는 통합 시험 통과.
- 격리 브라우저에서 카드 10개가 한 번 표시되고 뉴모피즘 클릭 후 다음 요청/응답이 하나씩 표시되는 것을 확인했다. 가상 CLI의 승인 모드 변경 응답과 화면 반영도 확인했다. 실제 모델 호출과 개인 설치 업데이트는 수행하지 않았다.
- 기존 통합 113건에서는 sandbox 계정 검사로 실행기 3건이 WS-31이었다. 같은 사용자의 sandbox 밖 임시 fixture로 해당 3건을 다시 실행해 모두 통과했다. 나머지 108건 통과, 2건 생략. 사용자 검사 코드를 완화하지 않았다.
- 코어 수정은 Company Agent 소스에만 있다. 기존 설치에 이 변경이 배포됐다는 뜻이 아니며 Workspace ZIP은 설치된 코어를 자동 덮어쓰지 않는다.

## 4단계 결과

- R06: 현재 열린 업무와 관계없이 실제 승인·질문·디자인 선택 대기를 집계한다. 요청 수명별 식별자로 중복 알림을 막고, 배지에서 해당 업무로 이동한다. 브라우저 알림은 사용자가 켰을 때만 권한을 요청한다.
- Windows 작업표시줄 알림은 인증된 화면의 별도 무작위 제목과 Edge 실행파일·SID·로그인 세션·프로세스 생성시각을 확인한 창에만 적용한다. 다른 앱으로 초점을 옮기지 않는다. 창을 확인할 수 없는 환경은 배지·사용자가 선택한 브라우저 알림을 사용한다.
- R08: HTML을 포함한 지원 문서는 명시한 버튼으로 기존 기본 앱에서 열 수 있다. 탐색기에서 위치 보기와 텍스트/HTML의 메모장 열기를 추가했다. 업무 폴더 또는 직접 선택한 파일 범위와 reparse 경로를 실행 직전 다시 확인한다. 앱 내부 HTML은 기존의 정적 격리를 유지한다.
- 텍스트는 UTF-8·BOM이 있는 UTF-16·CP949를 손실 치환 없이 읽는다. 확인하지 못하는 인코딩은 외부 앱 안내를 표시하며 원본 파일은 바꾸지 않는다.
- R09: 실제 살아 있는 업무 연결이 보고한 슬래시 명령을 추천한다. `/스킬 인자`는 그대로 전달하고, `@` 선택은 현재 업무/직접 선택한 첨부를 기존 JSON 파일 참조로 한 번 전달한다. 폴더 조회는 깊이·개수·시간 제한이 있으며 부분 조회를 빈 목록으로 단정하지 않는다.
- IME 조합 Enter, Shift+Enter, 중간 커서, 취소, 다른 업무 전환, 늦은 picker·preview 응답과 초안 보존을 검증했다. 발견된 경합은 회귀를 추가해 수정했다.
- 통합 99건: 98 통과, symlink 생성 권한 1 생략. 합성 reparse/junction 차단 검증은 통과. UI 관련 새 시험과 기존 회귀 77건 모두 통과. 독립 검토 44건 중 43 통과, 같은 권한 조건 1 생략.
- 격리 브라우저에서 대기 배지 → 다른 업무 질문 → 답변 거절 → 배지 해제, `/test-skill` 추천, `@확인` 한글 파일 선택/첨부, HTML의 외부 열기 버튼 3개를 확인했다. 실제 child로 slash와 파일 JSON이 원문대로 전달되는 HTTP 왕복 시험도 통과했다.
- Windows 프로세스 신원 조회와 메모장/탐색기 절대 경로는 실제 API로 읽기 확인했다. 실제 회사 PC의 Edge 작업표시줄 점멸, 브라우저 알림 전달, Office/DRM 문서 창 표시는 별도 수용시험이다. 검증을 위해 사용자 문서·개인 설정을 열거나 바꾸지 않았다.

## 5단계 결과

- 앱 버전을 0.12.0으로 갱신하고 실행기·진단 버전·진단 helper 고정 해시·ZIP 파일 목록을 일치시켰다. Company Agent 코어 버전은 임의로 올리거나 사용자 설치에 적용하지 않았다.
- `python -X utf8 scripts/test-lab/test-workspace.py` → 429건 중 422 통과, 7 생략, 실패·오류 0. 보고서는 `build/qa-workspace-0.12.0-tests.json`이다.
- 생략 중 현재 사용자 진단·제한 토큰 복사·검증 후 자식 실행·VBS 경고 중복 방지 4건을 실제 로그인 사용자 환경에서 따로 실행해 모두 통과했다. 나머지 3건은 실제 symbolic link 생성 권한 조건이며, 합성 reparse/junction 차단 회귀는 통과했다.
- 별도 실행기 3건도 같은 사용자 환경에서 통과했다. 기존 CLI alias/config 사용, 다른 버전 서버 보존, 공백 있는 상태 경로에서 숨김 실행·창 재열기를 검증했다. 위 4건과 회귀 총수를 단순 합산하지 않는다.
- Company Agent의 report_styles/native_runtime/execution_contract/html_choice_bookkeeping/completion_feedback/completion_efficiency/background_completion/state_compatibility 8개 suite 134건 모두 통과했다.
- ZIP `dist/company-workspace-preview-0.12.0-20260929-195240.zip`: 59개 파일이 현재 소스와 동일하고 4개 시작 helper 해시가 일치한다. 상태·인증 정보 미포함을 확인했다. SHA-256은 같은 위치의 `.zip.sha256`에 저장했다.
- 새 별도 폴더에 압축을 풀고 실제 로그인 사용자로 번들의 `python -m local_app.server --demo`를 실행했다. 0.12.0·추출된 앱 경로, 인증 없는 API의 403, HTML/JS/한글 폰트/안내서 8개 자산, 임시 업무와 파일, attention/catalog API, 정상 종료와 runtime 삭제를 확인했다. 보고서는 `build/qa-workspace-0.12.0-bundle.json`이다.
- 검증용 브라우저 탭과 가상 CLI 서버를 정상 종료했다. 기존 사용자 앱을 종료하거나 개인 설치에 덮어쓰지 않았다.
- [검증 기록](VALIDATION_0.12.0.md), [0.12.0 호환성 안내](WORKSPACE_0.12.0_COMPATIBILITY.md)를 작성했다. 최종 독립 검토에서 사용자/세션·토큰·picker 접근권한/정리·서버 파일/인증/종료 경계를 확인했고, 새 배포 차단 결함은 발견하지 못했다.
- 공개 업로드·커밋·푸시·메일 발송과 기존 사용자 설치 교체는 이번 단계에서 수행하지 않았다. 실제 회사 PC 수용시험과 Company Agent 코어 별도 배포가 남는다.

## 후속 개선: 0.12.1

- 사용자가 확인한 목표는 앱 전용 로그인 없이 실행하고 AI 업무는 기존 Claude 인증을 사용하는 것이다. CLI 탐색 실패와 앱 화면 시작을 분리했으며 기존 터미널 명령을 다른 CLI로 자동 대체하지 않는다.
- 실행·진단·재실행·권한 확인·선택창·CLI 연결의 PowerShell 호출에 이번 프로세스 계열 한정 Bypass를 적용했다. 영구 실행 정책·UAC·다운로드 표시는 변경하지 않으며 그룹 정책이 우선한다. 종료 코드 1은 원인을 단정하지 않고 ws33-4 읽기 전용 진단으로 이어진다.
- CLI가 실제 제공한 후보만 이번만/연결 동안/프로젝트 개인/프로젝트 공유/사용자 공통 범위로 보여 준다. 사용자가 명시적으로 고른 규칙만 CLI에 전달하며 앱이 개인 설정 파일을 직접 수정하지 않는다.
- 전체 446건 중 439 통과·7 생략, 실패 0. 생략 중 4건을 포함한 별도 Windows 7건 모두 통과. 가상 CLI 승인 카드를 브라우저에서 직접 선택해 정확한 범위가 한 번 전달되고 대기 배지가 해제되는 것을 확인했다.
- 처음 생성한 0.12.1 ZIP은 HTML 안내서 누락으로 검사에 실패했다. 빌더의 UTF-8 BOM과 복사 전후 파일 존재 검사를 보완하고 해당 ZIP을 배포 폴더에서 격리했다. 최종 `company-workspace-preview-0.12.1-20260929-205708.zip`은 59개 파일·소스·고정 해시 일치와 압축 해제 실행 검사를 통과했다.
- 최종 ZIP에서 실제 생산 실행기를 별도 상태로 시작하고 기존 Claude 2.1.283 인증으로 한 줄 시험 요청의 응답을 받았다. 재실행 시 같은 서버를 사용했고 정상 종료했다. 비교한 기존 설정 5개 파일의 전후 해시는 동일했다. 실제 지속 허용 규칙 저장·UAC-off 회사 PC·Office·DPI 수용시험은 대신하지 않는다.
