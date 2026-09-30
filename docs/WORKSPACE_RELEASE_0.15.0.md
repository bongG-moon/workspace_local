# Company Workspace 0.15.0 배포 안내

기존 Claude Code를 화면에서 사용하는 기능에 집중했습니다. Company Agent 전용 학습·기억 관리·설치 등록 기능은 앱에서 제외하고, 공통 설치 스킬과 선택한 업무의 실제 연결 목록을 구분합니다. 사용자의 기존 플러그인·인증·개인 설정은 유지합니다.

**v0.15.0 사전 배포를 공개했습니다.** 코드·화면·EXE 실행과 기존 Claude 연결을 확인하고, 아래 ZIP과 체크섬을 인증 없이 다시 다운로드해 해시 일치를 검증했습니다.

| 배포 파일 | 실행 방법 |
| --- | --- |
| [EXE ZIP](https://github.com/bongG-moon/workspace_local/releases/download/v0.15.0/Company-Workspace-0.15.0-exe.zip) | 압축을 풀고 `Company-Workspace-0.15.0.exe` 실행 |
| [VBS ZIP](https://github.com/bongG-moon/workspace_local/releases/download/v0.15.0/Company-Workspace-0.15.0-vbs.zip) | ZIP 전체를 풀고 `Company-Workspace.vbs` 실행 |

업데이트 전 기존 앱의 **설정 → 완전히 종료** 또는 **트레이 → 완전 종료**를 선택하세요. 창의 X만 누르면 백그라운드에 남을 수 있습니다. 새 묶음은 별도 폴더에 풀어 사용하며 기존 업무 데이터나 개인 Claude 설정을 자동 삭제하지 않습니다.

두 방식 모두 **이미 설치된 Python 3.11 이상**과 Windows PowerShell이 필요합니다. EXE는 Windows 10/11 x64 기준이고 VBS는 Windows Script Host도 필요합니다. Python 실행 환경·설치 프로그램을 포함하거나 다운로드·자동 설치하지 않습니다. 앱 전용 로그인은 없으며 AI 업무에는 기존 Claude Code 인증을 사용합니다.

- **일반 Claude 앱:** 전용 하네스 관리 화면·등록 연동을 제거했습니다. 기존 Claude 설정에서 활성화된 Company Agent가 있으면 다른 플러그인과 같이 조회하며 사용자 설치를 제거하지 않습니다.
- **공통 목록 복구:** 공통 범위는 설치 스킬에 적용합니다. 도구·연결 서버·명령은 **연결 기준 업무**를 선택해 그 업무의 보고 목록을 확인합니다. 서로 다른 폴더나 세션의 목록을 합치지 않습니다.
- **최근 업무 정렬:** 같은 고정 그룹 안에서 이동 손잡이 또는 Alt+↑/↓로 순서를 바꿉니다. 핀으로 고정 여부를 바꾸며 업무 내용과 실행 폴더는 유지합니다. 좌측·전체 업무의 포인터 드래그와 새로고침 후 순서·핀 유지를 확인했습니다.
- **연결 준비 경합 수정:** `/` 자동완성과 Shift+Tab이 같은 업무의 연결 준비를 공유합니다. 설정 선택을 AI 요청으로 보내거나 모드를 임의 기본값으로 바꾸지 않습니다.

Company-Workspace 실행 이름과 기존 앱 상태·EXE 캐시 경로는 기록 호환성을 위해 유지합니다. 이름이나 경로에 남은 CompanyAgent가 전용 하네스 설치를 요구한다는 뜻은 아닙니다. 기존 대화 가져오기·알림·승인·후속 요청·예약 기능은 일반 Claude 업무 흐름으로 이어집니다.

확인한 전체 회귀는 **847개 중 816 통과, 31 조건부 생략, 실패·오류 0**이며 별도 현재 사용자 실행기 **9개**가 통과했습니다. 포인터 변경 뒤 영향 범위 **143개**, 별도 확대 검사 **69개**도 통과했으며 중복되는 검사 수는 합산하지 않습니다. 1280×720 브라우저에서 가상 CLI의 공통 목록 숫자, 핀·드래그·키보드 정렬, Shift+Tab, Tab 자동완성을 확인했고 콘솔 오류는 0건이었습니다.

EXE·VBS의 앱 파일 **69개**가 소스와 일치하고 진단 핀 검사를 통과했습니다. 한글·공백 경로의 EXE 한 파일 폴더에서 기존 Python 3.13.5를 자동 발견했으며 재실행 시 같은 캐시·PID를 사용했습니다. 기존 Claude Code 2.1.285 연결 초기화에서 모델·Effort·명령 141개를 확인했고 **업무 메시지는 보내지 않았습니다**. 비교 대상 설정 파일 5개 해시는 유지됐고 검증 서버는 정상 종료했습니다. VBS ZIP 추출 체험 서버 검사도 통과했지만 실제 VBS 더블클릭과 회사 운영 PC의 이번 버전 실행은 미확인입니다.

| 배포 파일 | 크기(bytes) | SHA-256 |
| --- | ---: | --- |
| `Company-Workspace-0.15.0-exe.zip` | 5,719,661 | `aacf91344df69d36ea7dbe2495e53e13d8abdffb456a2cde3cd9c24ab965de59` |
| `Company-Workspace-0.15.0-vbs.zip` | 5,684,452 | `b9cbb1cfda6824b8d3be450f9f89dec06e479c67582ccc0cb8197cafc0c08f8e` |

[현재 사용법](LOCAL_WORKSPACE.md) · [0.15.0 변경 범위](WORKSPACE_0.15.0_CLAUDE.md) · [검증 결과와 남은 확인](VALIDATION_0.15.0.md)

공개 위치: [v0.15.0 Release](https://github.com/bongG-moon/workspace_local/releases/tag/v0.15.0), [SHA256SUMS.txt](https://github.com/bongG-moon/workspace_local/releases/download/v0.15.0/SHA256SUMS.txt). 위 해시는 공개 파일을 다시 다운로드해 확인한 값입니다. 소스와 배포 태그의 커밋은 `a048f7d60b30c25f558b73352896f6f3d8222257`이며 이후 검증 문서 커밋과 구분합니다.
