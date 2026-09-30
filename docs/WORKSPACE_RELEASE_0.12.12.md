# Company Workspace 0.12.12 배포 안내

운영 진단에서 확인한 **제한 권한 재실행 중 WS-38 접근 거부**를 수정하는 버전입니다. 운영 PC의 기존 Python 3.11.5는 원래 실행 조건에서 정상이었고, 앱의 제한 권한 자식에서 파이프와 자기 프로세스 접근이 실패했습니다. 수정본의 운영 PC 실제 재실행 결과는 아직 미확인입니다.

## 다운로드와 실행 조건

| 파일 | 필요한 환경 | 다운로드 |
| --- | --- | --- |
| VBS ZIP | Windows, Windows PowerShell, Windows Script Host, 기존 Python 3.11 이상 | [Company-Workspace-0.12.12-vbs.zip](https://github.com/bongG-moon/workspace_local/releases/download/v0.12.12/Company-Workspace-0.12.12-vbs.zip) |
| EXE ZIP | Windows 10/11 x64, Windows PowerShell, 기존 Python 3.11 이상 | [Company-Workspace-0.12.12-exe.zip](https://github.com/bongG-moon/workspace_local/releases/download/v0.12.12/Company-Workspace-0.12.12-exe.zip) |

두 배포본은 앱 파일만 포함합니다. Python 런타임·설치 프로그램·자동 다운로드·설치는 없으며 기존 설치를 확인하여 사용합니다. 앱 전용 로그인 없이 열고 AI 업무에는 같은 Windows 사용자의 기존 Claude Code 인증을 사용합니다.

1. 기존 앱에서 **설정 → 앱 종료**를 선택합니다. 창의 X만 닫으면 서버가 남을 수 있습니다.
2. 새 ZIP을 새 폴더에 모두 풉니다. 이전 배포 폴더를 덮어쓰지 않습니다.
3. VBS는 `Company-Workspace.vbs`, EXE는 `Company-Workspace-0.12.12.exe`를 더블클릭합니다.
4. 한 번에 한 버전만 실행합니다. 준비 조건 안내가 나오면 해당 코드와 진단 결과를 확인합니다.

## 변경 내용과 범위

앱이 관리자 원본 토큰에서 제한 복사본을 만들 때, 원본의 기본 접근 목록만 이어받으면 같은 사용자의 새 프로세스·출력 통로가 막히는 환경을 확인했습니다. 0.12.12는 앱용 제한 복사본의 기본 DACL 준비를 수정합니다. 관리자 권한을 그대로 허용하거나 Windows 설정을 바꾸는 방식은 아닙니다.

원본 토큰, UAC·영구 실행 정책, 기존 파일·폴더 ACL, 계정, Claude 인증·모델·MCP·스킬·개인 설정과 Company Agent 코어 설치를 유지합니다. 기존 사용자·세션·일반 권한·실제 자식 프로세스 검증을 유지하고 확인에 실패하면 중단합니다. Python 재설치나 PATH 변경을 요구하지 않습니다.

기존 Python 자동 탐색·`--python` 명시 경로 기능과 승인 카드의 의미는 유지합니다. 현재 읽기 전용 시작 진단은 `ws33-15`, 대상은 Workspace 0.12.12입니다. 기존 0.12.11 비교 진단 ZIP과 검증 기록은 그 버전의 역사 자료로 보존합니다.

## 검증 기록

아래는 완료한 개발 환경·로컬 산출물 검증 결과입니다. 공개 게시와 다운로드는 아직 검증하지 않았습니다. 운영 진단의 원인 확인과 수정본의 운영 PC 실행 성공은 별개입니다.

| 항목 | 결과 |
| --- | --- |
| 운영 PC 0.12.11 실패 원인 | 비교 JSON에서 앱 제한 자식의 기본 DACL·접근 거부 확인 |
| 개발 PC 인공 재현·수정 회귀 | 수정 전 전체 소스로 파이프·자식 접근 코드 5 재현, 수정 후 네이티브 14개 및 순수 ACL 3개 통과 |
| 원본 토큰·개인 설정·기존 ACL 보존 | 시험에서 원본 토큰·DACL 불변 확인, 확인 대상 개인 설정 5개 중 변경 0개; 기존 파일 ACL 변경 없음 |
| 버전·시작 파일 해시 계약 | 4개 통과, ws33-15 |
| 공개 소스 전체 회귀 | 636개 중 608개 통과, 28개 생략, 실패·오류 0개 |
| 별도 현재 사용자 실행기 | 3개 전부 통과: 기존 호출 환경·다른 빌드 분리·숨김 실행 및 재열기 |
| VBS·EXE 앱 파일 일치·기존 Python 실행 | 앱 파일 64개 일치·Python 미포함, VBS 격리 데모와 실제 EXE의 기존 Python 3.13.5 실행·재열기 확인 |
| 실제 Claude 연결·앱 정상 종료 | Claude 2.1.285 초기화 성공, 명령 141개, 사용자 AI 요청 0건, 정상 종료 |
| VBS ZIP SHA-256 | `1a7a4e3157e4741872c3aaccbaffc562209879a623b11102ae9e5e1070d5d74d` |
| EXE ZIP SHA-256 | `5b6c21a51ca8cbe5c1826870e5c55ff97188662a2d620a5be65f7779b11fb2a0` |
| 공개 Release 다운로드·체크섬 대조 | 확인 대기 |
| 수정본의 운영 PC 재실행 | 미확인 |

수정 전 인공 시험은 파이프·자식 접근의 코드 5를 재현했으며 운영 JSON의 모든 종료 코드와 실패 경로를 그대로 재현한 것은 아닙니다. 로컬 산출물 해시와 공개 다운로드 검증도 구분합니다.

세부 증거와 남은 범위는 [0.12.12 검증 기록](VALIDATION_0.12.12.md)에 기록합니다. 다른 회사 PC의 모든 보안 정책·Office·DRM·모델·플러그인 호환성까지 보장하는 것은 아닙니다.

[사용법](LOCAL_WORKSPACE.md) · [EXE 안내](WORKSPACE_STANDALONE_EXE.md) · [시작 진단](WORKSPACE_STARTUP_DIAGNOSTIC.md)
