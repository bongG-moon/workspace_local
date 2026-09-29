# Company Workspace 0.12.5 단일 EXE 검증

검증일: 2026-09-29. 최종 배포 파일은 `dist/Company-Workspace-0.12.5.exe`입니다.

## 결과

EXE 한 파일만 들어 있는 한글·공백 경로에서 실행한 앱으로 기존 Claude 인증을 사용한 실제 응답을 확인했습니다. 별도 Python 설치를 사용하지 않았고, 기존 VBS 배포본과 확인 대상 개인 설정 파일은 보존됐습니다.

| 항목 | 확인 결과 |
| --- | --- |
| 최종 파일 크기 | 16,952,320 bytes, 약 17 MB |
| SHA-256 | `0ddc4115672d39bb732c7214af763d194b4149773b96749828532515a3e14bc8` |
| 파일 구성 | 앱 파일 64개 + 공식 Python 실행 환경 등 총 99개 내장 |
| Python | 3.13.15 x64 embeddable, 내장 `pythonw.exe`에서 실제 서버 실행 |
| Claude | 기존 2.1.284 설치, 기존 사용자 설정 경로, 인증 공유 |
| 초기 연결 | `claude-sonnet-5-5`, Effort `medium`, 명령 140개 수신 |
| 실제 요청 | 파일·도구 사용 없는 검증 요청에 `WORKSPACE_EXE_OK` 응답, 대기 승인 없음 |
| 재실행 | 같은 캐시와 기존 서버 PID 재사용 |
| 개인 설정 | 검사한 5개 파일 변경 0개 |
| 기존 VBS | 0.12.4 ZIP 해시 및 추출된 63개 파일 동일 |

개인 설정 비교 대상은 현재 Claude 설정 폴더의 `settings.json`, `settings.local.json`, `CLAUDE.md`, `.mcp.json`, `plugins/installed_plugins.json`입니다. 없던 파일은 계속 없는지도 비교했습니다. 내용이나 인증 정보를 보고서에 복사하지 않았습니다.

## 연결 방식과 수정 근거

EXE는 앱과 전용 Python을 안정된 사용자 캐시에 준비한 뒤 기존 PowerShell 실행기를 호출합니다. 기존 프로필·회사용 별칭·함수·래퍼·`CLAUDE_CONFIG_DIR`를 이어 사용하며, 앱에 포함한 Python을 시스템 PATH에 등록하지 않습니다. PATH에 `claude`가 없는 경우에는 현재 사용자로 검증된 공식 네이티브 설치 위치를 추가로 확인합니다. 명시된 실행 경로나 기존 래퍼가 실패했을 때 다른 설치로 몰래 전환하지 않습니다.

실제 파일 시험 중 발견한 두 문제를 수정했습니다.

1. Windows `Compress-Archive`가 ZIP 디렉터리 경로를 `\`로 기록하는 경우를 처리했습니다. 경로 구분자 정규화 후 경로 탈출·중복·재분석 지점 검사를 유지합니다. 빌드 중 실제 내장 ZIP을 다시 풀어 검증한 후에만 EXE를 배포 경로로 옮깁니다.
2. 숨김 PowerShell의 CP949 출력 해석으로 한글 Python 경로가 손상되던 문제를 고쳤습니다. Python 실행 경로를 ASCII JSON으로 전달하고 파싱한 후 파일을 확인합니다. 한글·공백·`&` 경로 및 인접 `pythonw.exe` 선택을 회귀검사에 포함했습니다.

실행 실패 안내도 EXE에 포함한 `Check-Workspace.cmd`를 사용할 수 있도록 정리했습니다. [사용·진단 안내](WORKSPACE_STANDALONE_EXE.md)를 참고하세요.

## 자동 검사

- `python -X utf8 scripts/test-lab/test-workspace.py`: 557개 실행, 550개 통과, 7개 제외, 실패·오류 0.
- 최종 오류 안내 수정 후 C# EXE 통합검사 18개 추가 재실행: 모두 통과.
- 실행기 검사 11개에는 기존 프로필/래퍼 우선, 네이티브 경로 보완, 잘못된 기존 래퍼 보존, 한글 CP949 경로 검증이 포함됩니다.
- 내장 앱 ZIP의 64개 파일과 현재 소스 바이트 일치, 개인 상태·인증 파일 미포함, 진단 도구의 실행기 해시 일치 확인.
- `git diff --check`: 통과.

제외된 7개는 심볼릭 링크 생성 권한 3개, 실제 사용자 토큰 전용 opt-in 3개, 이 테스트 환경에서 실행할 수 없는 WSH/VBS 1개입니다. 일반 회귀검사에서 분리된 기존 사용자 세션 전용 fixture 3개도 전체 통과 수에 포함하지 않았습니다. 이와 별도로 현재 로그인한 사용자로 최종 EXE의 실제 실행과 Claude 응답을 검증했습니다.

## 실제 파일 검증 절차

`scripts/test-lab/verify-workspace-standalone.py`를 사용했습니다. 별도 업무·상태·캐시를 만들고 테스트 자식 프로세스의 PATH에서 Python 및 WindowsApps 항목 6개를 제외했습니다. 실제 서버의 실행 파일이 EXE에 포함된 `runtime/pythonw.exe`인지 확인했습니다.

최종 EXE에서 `start → prepare → prompt → status → finish`를 순서대로 실행했습니다. HTTP 정적 자원·한글 폰트·안내서 응답을 확인하고, 브라우저 화면에서도 실제 답변과 모델·Effort·승인 표시를 확인했습니다. 테스트 서버는 앱 종료 API로 정상 종료했습니다.

증거: `build/qa-standalone-0.12.5-release/`의 `startup-result.json`, `prepare-result.json`, `response-result.json`, `finish-result.json`, `package-result.json`, `screen.png`. 전체 검사 결과는 `build/qa-workspace-0.12.5-tests.json`, 빌드 정보는 `build/workspace-standalone-0.12.5-build.json`에 있습니다. 시험용 실행 파일 사본과 캐시·실패 빌드 폴더 20개(625,844,210 bytes)는 검증 후 정리했습니다. 최종 EXE와 기존 VBS는 보존했습니다.

## 검증 범위

Windows 10/11 x64용입니다. 현재 PC의 실제 Claude 연결은 확인했지만, 회사별 실행 제한·UAC 비활성화 PC·ARM64/32비트 환경에서의 실행을 새로 검증한 결과는 아닙니다. 이 시험은 격리된 상태 경로와 `--no-browser`로 서버를 실행한 뒤 검증 브라우저에서 화면을 확인했습니다. 모든 PC 정책 조합이나 모든 기본 브라우저의 앱 창 동작을 보장하지 않습니다.

앱에는 별도 로그인이 없으며 AI 기능은 해당 PC의 기존 Claude 설치·인증을 사용합니다. 기존 VBS 앱이 실행 중이면 **설정 → 앱 종료** 후 새 EXE를 실행합니다. Windows 보안 정책, UAC, 개인 Claude 설정이나 Company Agent 설치 설정을 변경하지 않았습니다.
