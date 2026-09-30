# WS-38: Python 실행 단계의 접근 거부 진단

대상: Company Workspace 0.12.11 / 진단 도구 ws38-1

## 확인된 내용

운영 PC에서는 PowerShell의 Python 3.11.5 실행이 보고되었으나, 앱의 Python 검사에는 `start_failed`, Windows 코드 `5`가 기록됐다. 코드 5는 접근 거부이며 Python 버전 부족이나 파일 경로 오타를 뜻하지 않는다. 전달된 경로 문자열에는 문자 변형 가능성이 있어 원본 JSON을 기준으로 확인해야 한다.

`Process.Start()`는 자식 프로세스를 만드는 것뿐 아니라 표준 출력·오류를 받는 파이프도 준비한다. 따라서 현재 기록만으로 Python 이미지 실행 거부와 출력 파이프 준비 실패를 구분할 수 없다. 이 오류는 Python 검사 코드가 실행됐거나 표준 모듈 검사가 실패했다는 증거도 아니다.

현재 앱은 같은 Windows 사용자·세션·프로필을 확인한다. UAC 연결 토큰이 있으면 일반 권한으로 재실행하고, UAC가 꺼진 관리자 환경에서는 별도로 검증한 제한 토큰으로 재실행한다. 터미널과 앱의 사용자 이름이 같더라도 실행 토큰이 같다는 뜻은 아니다.

## 로컬 확인과 한계

- 현재 개발 PC: UAC 켜짐, medium integrity, 일반 권한의 제한된 UAC 토큰.
- 생산 코드의 제한 복사 토큰으로 PowerShell 자식을 실행하고 그 자식에서 Python을 검사했다. 원본 토큰 요약은 변하지 않았으며, 파이프 생성·자기 프로세스 핸들 접근·출력 리디렉션을 사용한 Python 검사·리디렉션 없는 Python 실행이 통과했다.
- 별도의 일회성 파이프 fixture에서 SYSTEM/Administrators만 허용하는 보안 설명자는 코드 5를 재현했다. 이는 실패 가능 단계의 검증이며, 운영 PC의 실제 기본 DACL이나 원인 확인은 아니다. Windows 설정과 원본 프로세스 토큰은 변경하지 않았다.
- 기존 `exit 73` 자식 검사는 Python 손자 프로세스와 출력 통로를 검사하지 않았다. 기존 `python-check` 기록에도 실패 프로세스의 토큰 상태가 없다.
- 운영 PC의 현재 UAC, 토큰, 기본 DACL, 파일 실행 권한 및 실행 제어 정책은 아직 확인되지 않았다. 앱 실행 문제가 해결되었다고 판단하지 않는다.

## 더블클릭 비교 진단

`Company-Workspace-0.12.11-python-diagnostic-1.zip`을 새 폴더에 풀고 `Check-Python.cmd`를 더블클릭한다. 생성된 `Workspace-Python-Diagnostic-*.json`을 확인한다. 이 파일은 실행 환경을 비교하기 위한 진단 도구이며 앱 업데이트가 아니다.

검사 목적은 현재 실행 조건과 앱의 정상 권한 실행 조건에서 같은 Python 검사 결과를 비교하는 것이다. 앱 서버와 Claude를 실행하지 않고, AI 요청도 보내지 않는다. Python과 의존성을 설치하지 않는다. Windows 정책, UAC, PATH, 파일 ACL, 개인 인증 및 Claude·Company Agent 설정을 변경하지 않는다. 검사에 필요한 일시적 자식 프로세스와 새 진단 결과만 만든다.

보고서에는 Python 실행 경로, 버전, 상태·오류 코드, 권한 조건을 비교하기 위한 제한된 메타데이터가 포함된다. 사용자 SID, 인증값, 환경 변수 전체, PowerShell 프로필 내용, 자식 프로세스의 오류 출력 원문은 수집하지 않는다. 진단 결과에는 경로의 Windows 계정 이름이 포함될 수 있으므로 공개 저장소에 올리지 않는다.

진단에 필요한 기능이 차단되면 그 단계의 실패를 기록한다. 관리자 권한이나 다른 계정으로 대신 실행하지 않는다. 운영 환경을 재현하지 못한 상태에서 UAC 변경, Python 재설치, PATH 변경, 정책 예외를 해결책으로 제시하지 않는다.

## 결과 판정

| 관찰 | 해석과 다음 확인 |
| --- | --- |
| 원래 실행 조건 통과, 앱 조건에서 파이프 실패 | 제한된 실행 조건의 기본 DACL 및 출력 통로 실패 증거를 검토 |
| 파이프는 통과, 앱 조건의 Python만 실패 | Python 파일·디렉터리 접근권과 해당 시각의 실행 제어 기록 확인 |
| 현재 조건과 앱 조건 모두 통과 | 원래 EXE의 부모 프로세스·프로필·실행 시각 차이와 기존 실패 JSON 비교 |
| 계정 확인 또는 토큰 재실행 실패 | 해당 단계를 먼저 확인; Python 검사 성공으로 해석하지 않음 |
| 검사 시간 초과 또는 일부 검사 미실행 | 미확인으로 유지; 앱 정상 실행으로 해석하지 않음 |

## 근거

- [Microsoft: Windows 오류 코드 5](https://learn.microsoft.com/en-us/windows/win32/debug/system-error-codes--0-499-)
- [Microsoft: CreatePipe의 기본 보안 설명자](https://learn.microsoft.com/en-us/windows/win32/api/namedpipeapi/nf-namedpipeapi-createpipe)
- [Microsoft: 제한 토큰과 접근 검사](https://learn.microsoft.com/en-us/windows/win32/secauthz/restricted-tokens)

## 패키징

개발 환경에서 `python scripts/test-lab/package-ws38-diagnostic.py`로 생성한다. 명시한 소스 파일만 ZIP에 넣고 SHA-256 manifest 및 ZIP CRC를 확인한다. 같은 이름의 기존 ZIP을 덮어쓰지 않는다. 실제 진단 결과와 로컬 검증 기록은 배포하지 않는다. 기존 0.12.11 VBS·EXE 배포 파일도 변경하지 않는다.

## 진단 도구 검증

2026-09-30, Windows PowerShell 5.1과 실제 로그인 계정에서 ZIP을 한글·공백 경로에 압축 해제해 검사했다. 소스 해시 검증, 같은 사용자·세션 확인, 생산 Python resolver, 리디렉션 유무를 비교하는 고정 Python 검사와 JSON 생성이 통과했다. 현재 계정은 이미 일반 권한이므로 권한 전환을 수행했다는 표시 대신 `same_context_already_normal`로 기록한다.

회귀 검사 4개가 통과했다: 변경된 소스 거절, 자식 실패·보고서 누락·시간 초과의 미완료 처리, 검사 완료와 앱 성공의 구분, PS5.1에서 생산 resolver와 같은 `List[object]` 결과의 직렬화. 처음 실제 실행에서 발견한 PowerShell 배열 변환 문제는 파이프라인으로 목록을 열거하도록 수정했다. 이 테스트들의 통과는 운영 PC의 WS-38 해결을 의미하지 않는다.
