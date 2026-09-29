# Company Workspace 0.12.11 배포 안내

기존 Python을 확인하는 시작 과정을 개선한 버전입니다. **앱 전용 로그인은 없으며 AI 업무에는 기존 Claude Code 인증을 사용합니다.**

## 다운로드와 실행 조건

| 파일 | 필요한 환경 | 다운로드 |
| --- | --- | --- |
| VBS ZIP | Windows, Windows PowerShell, Windows Script Host, 기존 Python 3.11 이상 | [Company-Workspace-0.12.11-vbs.zip](https://github.com/bongG-moon/workspace_local/releases/download/v0.12.11/Company-Workspace-0.12.11-vbs.zip) |
| EXE ZIP | Windows 10/11 x64, Windows PowerShell, 기존 Python 3.11 이상 | [Company-Workspace-0.12.11-exe.zip](https://github.com/bongG-moon/workspace_local/releases/download/v0.12.11/Company-Workspace-0.12.11-exe.zip) |

두 배포본에 Python 런타임·설치 프로그램은 없습니다. 자동 다운로드나 설치, PATH·등록 정보 변경도 하지 않습니다. EXE는 앱 파일만 준비한 뒤 VBS와 같은 시작 검사 경로를 사용합니다. 회사의 실행 허용 정책에 따라 실행 여부가 달라질 수 있습니다.

1. 기존 앱에서 **설정 → 앱 종료**를 누릅니다. 창의 X만 닫으면 서버가 남을 수 있습니다.
2. 새 ZIP을 새 폴더에 모두 풉니다. 이전 배포 폴더에 덮어쓰지 않습니다.
3. VBS ZIP은 `Company-Workspace.vbs`, EXE ZIP은 `Company-Workspace-0.12.11.exe`를 더블클릭합니다.
4. 준비 조건 안내가 나오면 실제 메시지와 진단 기록을 확인합니다. 한 번에 한 버전만 실행하세요.

## 이번 변경

- PATH에 Python이 여러 개 있으면 후보를 확인하고, `py`의 기존 설치 목록과 Windows의 Python 등록 경로(PEP 514)도 읽어 확인합니다.
- ASCII JSON 표식이 있는 Python 검사 응답을 분리하여 시작 안내 문구 등 다른 출력이 섞인 경우를 처리합니다. 정상 응답이 확인되지 않으면 중단합니다.
- Python 탐색·실행·버전·기본 모듈·응답 확인 과정의 실제 실패 이유를 구분하여 안내·기록합니다.
- EXE에 `--python "<기존 python.exe의 절대 경로>"` 옵션을 제공합니다. 명시한 설치가 조건을 통과하지 못하면 다른 Python으로 자동 대체하지 않습니다.
- 현재 진단 대상은 Workspace 0.12.11, 진단 버전은 `ws33-14`입니다.

Python 3.11.5가 설치된 PC에서 `WS-38`이 보고되었지만, 버전 정보만으로 그 PC의 정확한 원인이 확인된 것은 아닙니다. 새 검사와 개발 환경 검증이 실제 운영 PC 재검증을 대신하지 않습니다. 시작 실패 기록의 내용·위치와 경로 지정 방법은 [진단 안내](WORKSPACE_STARTUP_DIAGNOSTIC.md)를 참고하세요.

기존 Claude 인증·모델·MCP·스킬·개인 상태와 Company Agent 코어 설치는 유지합니다. 승인 화면이나 승인 기본값을 바꾸지 않으며, 사용자가 선택한 Claude의 정확한 규칙·범위만 전달하는 기존 동작을 유지합니다. 0.12.10의 배포 파일·검증 기록은 변경하지 않습니다.

## 검증 기록

새 실행 파일과 ZIP을 별도 폴더에서 검사했습니다. 아래 개발 환경 결과와 실제 운영 PC 재실행 여부를 구분합니다.

| 항목 | 결과 |
| --- | --- |
| 버전·시작 진단 핀 계약 | 4개 통과, ws33-14 |
| 복수 PATH·등록 경로·출력 잡음·명시 Python 경로 회귀 | 실행기·시작 검사 42개 통과, 환경 조건 1개 생략; EXE 검사 21개 통과 |
| Python 실패 기록·민감 원문 제외·로컬 경로 포함 확인 | 4개 행동 검사 통과 |
| VBS ZIP 소스 일치·별도 압축 해제 실행 | 64개 소스 일치, 격리 데모 시작·API·종료 확인 |
| EXE 앱 전용 페이로드·Python 미포함·기존 설치 실행 | 기존 Python 3.13.5로 자동/명시 경로 실행·재열기·종료 확인 |
| 실제 Claude 연결·기존 개인 설정 보존 | CLI 2.1.285 연결 초기화, 명령 141개, 확인한 설정 변경 0개 |
| VBS ZIP SHA-256 | `cf023909fe6f6a7a575b36054996a6105aa78a57d269372d3151d03aec4d5290` |
| EXE ZIP SHA-256 | `7b58be9433e9c7fb1280f42542b1ac1db9fc1786d9e3fee63b2c580f6e2774e5` |
| 다운로드 체크섬 | Release에 SHA256SUMS.txt 제공; 공개 게시 후 다운로드 대조 |
| WS-38이 보고된 운영 PC 재실행 | 미확인 |

상세 검증은 저장소의 `docs/VALIDATION_0.12.11.md`에 기록합니다. 회사의 보안 정책 전체, Office·DRM 및 모든 모델·플러그인 동작은 별도 확인이 필요합니다.

[사용법](LOCAL_WORKSPACE.md) · [EXE 안내](WORKSPACE_STANDALONE_EXE.md) · [시작 진단](WORKSPACE_STARTUP_DIAGNOSTIC.md)
