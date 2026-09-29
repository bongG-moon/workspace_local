# Company Workspace 0.12.10 배포 안내

터미널 명령에 익숙하지 않은 사용자가 기존 Claude Code를 GUI에서 사용하는 Windows 로컬 앱입니다. **앱 전용 로그인 없이 실행하고, AI는 기존 Claude 인증을 사용합니다.**

## 다운로드와 준비 조건

| 파일 | 필요한 환경 | 바로 받기 |
| --- | --- | --- |
| VBS ZIP | Windows, Windows PowerShell, Windows Script Host, 이미 설치된 Python 3.11 이상 | [Company-Workspace-0.12.10-vbs.zip](https://github.com/bongG-moon/workspace_local/releases/download/v0.12.10/Company-Workspace-0.12.10-vbs.zip) |
| EXE ZIP | Windows 10/11 x64, Windows PowerShell, 이미 설치된 Python 3.11 이상 | [Company-Workspace-0.12.10-exe.zip](https://github.com/bongG-moon/workspace_local/releases/download/v0.12.10/Company-Workspace-0.12.10-exe.zip) |

**두 배포본 모두 Python 실행 환경·설치 프로그램을 포함하지 않으며 다운로드·자동 설치하지 않습니다.** EXE는 앱 파일만 담은 실행기입니다. 기존 Python이 준비 조건에 맞는지 확인하고, 확인하지 못하면 안내 후 중단합니다. 기존 Python 설치·PATH·Claude 인증과 개인 설정을 자동으로 바꾸지 않습니다.

두 방식은 같은 PowerShell 프로필·시작 검사·Claude 호출 환경을 사용합니다. 회사의 EXE·VBS·PowerShell 허용 정책은 서로 다를 수 있으며 모든 PC의 실행을 보장하지 않습니다. EXE는 서명되지 않은 자체 제작 실행 파일입니다. AI 업무에는 같은 Windows 사용자의 기존 Claude Code와 정상 인증이 필요합니다.

## 실행·업데이트 순서

1. 기존 앱에서 **설정 → 앱 종료**를 누릅니다. 창의 X만 닫으면 서버가 남을 수 있습니다.
2. 선택한 ZIP을 새 폴더에 모두 풉니다. 압축 파일 미리보기에서 실행하거나 기존 배포 폴더에 덮어쓰지 않습니다.
3. VBS ZIP은 `Company-Workspace.vbs`, EXE ZIP은 `Company-Workspace-0.12.10.exe`를 더블클릭합니다.
4. 필요한 기존 실행 환경을 확인한 뒤 앱이 열립니다. 준비 조건 안내가 나오면 그 내용을 확인하세요. 설치 프로그램이 자동 실행되지 않습니다.
5. 새 업무 또는 기존 폴더를 선택해 요청합니다. **한 번에 한 버전만 실행**하세요.

기존 업무·대화와 Claude 인증·모델·MCP·스킬·기억 설정은 유지합니다. 앱이 확인하지 못한 Claude를 다른 계정이나 CLI로 자동 대체하지 않습니다. Claude 호출 대상을 찾지 못한 경우에도 Python 등 앱 실행 조건이 충족되면 화면에서 연결 안내를 확인할 수 있습니다. 업데이트가 기존 Company Agent 코어를 설치·변경하지는 않습니다.

## 변경 범위

0.12.10은 EXE에서 Python 런타임을 제외하고 **이미 설치된 Python을 확인하는 방식**으로 정리합니다. Python 설치·다운로드 기능을 추가하지 않습니다. 기존 0.12.9 파일과 검증 기록은 변경하지 않습니다.

0.11.4 이후 추가된 주요 기능은 다음과 같습니다.

- 업무별 저장 폴더와 개선된 파일·폴더 선택, Noto Sans KR 표시.
- 공통·폴더별 스킬 목록, 설치에서 발견한 정보와 현재 CLI 보고의 구분.
- 전체 업무의 승인·질문 대기 알림, `/`·`@` 추천, 외부 앱으로 결과 열기.
- 실제 CLI의 모델·Effort·승인 모드 표시, `/effort` 및 입력창의 Shift+Tab 모드 전환.
- 간결한 승인 카드와 **이번만 허용** 기본값. 현재 CLI가 제안한 연결·지속 허용 범위만 표시하며, 사용자가 선택한 정확한 규칙·범위만 전달.

승인 기본값과 Claude의 권한 의미는 0.12.10에서 바꾸지 않습니다. 회사 정책이나 개인 기본 설정을 임의로 완화하지 않으며, 설정 변경을 AI 업무 요청으로 보내지 않습니다. 지원 기능은 연결한 CLI·모델·플러그인에 따릅니다. HTML 디자인 카드와 일부 Stop 후크 개선은 해당 기능을 제공하는 Company Agent 코어가 별도로 필요합니다.

## 검증 기록

0.12.10의 새 실행 파일과 ZIP을 별도 폴더에서 검사했습니다. 상세 범위는 [검증 기록](VALIDATION_0.12.10.md)을 참고하세요.

| 항목 | 결과 |
| --- | --- |
| 현재 소스·버전·시작 진단 핀 검사 | 4개 통과, 진단 ws33-13 |
| 기존 Python 탐색·부재·미지원 버전 처리와 무설치 검사 | 준비 조건 회귀 7개 통과 |
| VBS ZIP 소스 일치·별도 압축 해제 실행 | 64개 일치, 격리 데모 시작·API·종료 확인 |
| EXE 앱 전용 페이로드·Python 미포함·기존 Python 실행 | 앱 파일 64개만 포함, 기존 Python 3.13.5로 실행·재열기 확인 |
| 실제 Claude 연결·기존 개인 설정 유지 | CLI 2.1.285 연결 초기화, 명령 141개, 확인한 설정 변경 0개 |
| VBS ZIP SHA-256 | `fe8d60abe978d1aa0c961cec82c058b729f3baed71e1028ebad2bf25ca0e0793` |
| EXE ZIP SHA-256 | `0f3a4879e1c53ed3bad422fb078b52fc96cfc46cc1be279cdeb6f0143b7e8f89` |
| 다운로드 체크섬 | Release의 `SHA256SUMS.txt`와 ZIP별 해시 제공; 공개 게시 후 다운로드 대조 |

회사 PC의 보안 정책 전체, 실제 Office·DRM 처리와 모든 모델·플러그인 동작은 별도 현장 확인이 필요합니다. 기존 승인 화면 검증은 `docs/VALIDATION_0.12.9.md`, 이번 실행 준비 검사와 배포 검증은 `docs/VALIDATION_0.12.10.md`에 구분하여 기록합니다.

사용 방법은 [로컬 업무 화면](LOCAL_WORKSPACE.md), [EXE 안내](WORKSPACE_STANDALONE_EXE.md), [시작 진단](WORKSPACE_STARTUP_DIAGNOSTIC.md)을 참고하세요.
