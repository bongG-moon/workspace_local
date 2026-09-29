# Company Workspace 0.12.10 — 기존 실행 환경 확인

검증일: 2026-09-30.

EXE에서 Python 런타임과 빌드 중 Python 다운로드를 제거했습니다. VBS와 같은 앱 파일만 포함하고 공통 PowerShell 실행기가 기존 Python 3.11 이상과 필수 기본 모듈을 확인합니다. Python 설치 프로그램을 포함하거나 자동 실행하지 않습니다.

## 확인 방식

- 기본 실행은 기존 `python`을 먼저 확인하고, 사용할 수 없으면 `py -0p`가 제공하는 설치된 실행 파일 경로만 확인합니다. `py`에 버전 실행·설치를 요청하지 않습니다.
- 명시한 `-PythonCommand`가 실패하면 다른 Python으로 대신 실행하지 않습니다. 검사 시간과 출력 대기를 제한하고 한글 경로를 보존합니다.
- 자동 설치 억제 환경변수는 검사 자식 프로세스에만 적용합니다. 부모·실제 서버·Claude의 기존 환경, PATH, 개인 설정과 PC 정책을 변경하지 않습니다.
- 준비 조건 누락은 WS-37, 실행·버전·필수 모듈 확인 실패는 WS-38로 안내합니다. Claude가 없는 경우의 앱 실행 및 연결 안내는 기존 동작을 유지합니다.

## 검증 결과

- Python 미설치, `py`만 있는 설치, 구버전·불완전 Python, 명시 경로 실패, 설치 억제 환경과 시간 제한을 포함한 실행기·시작 검사: 31개 통과, Windows Script Host 환경 검사 1개 생략. 마지막 보정 후 Python 준비 조건 검사 7개 재실행 통과.
- 실제 C# 컴파일·실행 기반 EXE 부트스트랩 19개 통과. 캐시 무결성·안전한 압축 해제·오류 전달을 포함합니다.
- 버전·진단 해시 계약 4개 통과. 진단 버전 `ws33-13`, 대상 `workspace-0.12.10`.
- 현재 Windows 사용자로 실행한 기존 Claude 별칭 보존·다른 빌드 분리·숨김 실행과 재열기 검사 3개 통과.
- VBS ZIP의 앱 파일 64개가 현재 소스와 바이트 단위로 일치하고 EXE 내부에도 동일한 64개만 포함됩니다. Python 실행 파일·런타임 디렉터리·개인 실행 상태·인증 파일이 없습니다.
- VBS ZIP을 새 폴더에 풀어 포함된 서버를 격리된 데모 상태로 실행했습니다. API 인증 경계, 화면·한글 폰트·안내서, 업무 생성·파일 목록·스킬 목록, 정상 종료를 확인했습니다. 이 검사는 VBS 더블클릭 자체를 대신하지 않으며 실행기 검사는 위 3건으로 구분합니다.
- EXE ZIP에서 추출한 EXE 하나를 한글·공백 폴더에 복사하여 실행했습니다. 기존 Python **3.13.5**가 캐시 밖에서 서버를 실행했고 재실행은 같은 서버 PID를 사용했습니다.
- 기존 Claude Code **2.1.285** 연결 초기화 성공: 모델 `claude-sonnet-5-5`, Effort `medium`, 명령 141개. 사용자 AI 업무 요청은 0건이며 답변 생성이나 모든 도구의 실제 업무 실행을 검증한 결과는 아닙니다.
- 검증 서버 정상 종료, 확인 대상 개인 설정 파일 5개 중 변경 0개. 비교는 `settings.json`, `settings.local.json`, `CLAUDE.md`, `.mcp.json`, `plugins/installed_plugins.json`의 존재 여부와 해시를 사용했습니다. 인증 정보는 출력하거나 배포하지 않았습니다.

공개 저장소 전체 회귀는 **611건 중 584개 통과, 27개 생략, 실패·오류 0건**입니다(156.860초). 생략은 별도 Company Agent 코어 미제공 20개, Windows 선택 검사 3개, 심볼릭 링크 권한 3개, WSH 환경 1개입니다. 현재 사용자 실행기 3건은 이 회귀와 별도로 위에서 통과했습니다. 보고서는 공개 clone의 `build/qa-workspace-0.12.10-tests.json`에 보관합니다.

로컬 검증 기록: `build/workspace-standalone-0.12.10-build.json`, `build/workspace-release-0.12.10.json`, `build/qa-workspace-0.12.10-bundle.json`, `build/qa-standalone-0.12.10-release/*-result.json`. 개인 경로를 포함할 수 있는 원시 기록은 공개 배포에 넣지 않습니다.

## 배포 파일

| 파일 | 크기(bytes) | SHA-256 |
| --- | ---: | --- |
| `Company-Workspace-0.12.10.exe` | 5,947,392 | `441a1aecb7bbd022afbc46cd5cc67309d6b5c6699c6e9e7c2518819ad6938aef` |
| `Company-Workspace-0.12.10-vbs.zip` | 5,878,241 | `fe8d60abe978d1aa0c961cec82c058b729f3baed71e1028ebad2bf25ca0e0793` |
| `Company-Workspace-0.12.10-exe.zip` | 5,914,031 | `0f3a4879e1c53ed3bad422fb078b52fc96cfc46cc1be279cdeb6f0143b7e8f89` |

두 ZIP과 `SHA256SUMS.txt`는 v0.12.10 Release에 별도 등록합니다. Git의 자동 소스 ZIP은 실행용 ZIP과 다릅니다. 공개 게시 후 실제 다운로드의 SHA-256을 이 값과 대조하며 게시 결과는 Release 전달 기록으로 구분합니다.

개발 PC에서의 결과이며 모든 회사 PC 정책 조합·모든 Python 배포판·Office·DRM·모델·플러그인 동작을 보장하지 않습니다. 앱을 실행할 Python이 이미 설치되어 있어야 합니다. 기존 Windows 보안 정책과 Claude·Company Agent 개인 설정을 바꾸는 방식으로 해결하지 않습니다.
