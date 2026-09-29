# Company Workspace 0.12.9 배포 안내

터미널 명령에 익숙하지 않은 사용자가 기존 Claude Code를 GUI에서 사용하는 Windows 로컬 앱입니다. **앱 전용 로그인 없이 실행하고, AI는 기존 Claude 인증을 사용합니다.**

## 다운로드

| 파일 | 선택 기준 | 바로 받기 |
| --- | --- | --- |
| VBS ZIP | Python 3.11 이상과 Windows Script Host가 설치·허용된 Windows 환경 | [Company-Workspace-0.12.9-vbs.zip](https://github.com/bongG-moon/workspace_local/releases/download/v0.12.9/Company-Workspace-0.12.9-vbs.zip) |
| EXE ZIP | Windows 10/11 x64에서 앱과 Python을 한 실행 파일로 사용 | [Company-Workspace-0.12.9-exe.zip](https://github.com/bongG-moon/workspace_local/releases/download/v0.12.9/Company-Workspace-0.12.9-exe.zip) |

EXE에는 Python 3.13.15 x64와 원본 라이선스가 들어 있어 별도 Python 설치가 필요하지 않습니다. 두 방식 모두 같은 PowerShell 프로필·시작 검사·Claude 호출 환경을 사용합니다. EXE도 PowerShell과 조직 정책의 영향을 받으며, VBS·EXE 허용 여부는 회사마다 다를 수 있습니다. 모든 PC에서 실행된다는 보장은 아닙니다.

## 실행·업데이트 순서

1. 기존 앱에서 **설정 → 앱 종료**를 누릅니다. 창의 X만 닫으면 서버가 남을 수 있습니다.
2. 원하는 ZIP을 새 폴더에 모두 풉니다. 기존 배포 폴더에 덮어쓰지 않습니다.
3. VBS ZIP은 `Company-Workspace.vbs`, EXE ZIP은 `Company-Workspace-0.12.9.exe`를 더블클릭합니다.
4. 새 업무 또는 기존 폴더를 선택해 요청합니다. 같은 시점에는 한 버전만 실행하세요.

기존 프로필·인증·모델·MCP·스킬·기억 설정과 업무 기록을 자동으로 바꾸거나 옮기지 않습니다. 기존 Claude 호출 대상을 확인하지 못하면 연결 안내를 표시하며 다른 계정으로 대신 실행하지 않습니다. 사용자가 지속 허용을 명시적으로 고른 경우에만 CLI가 제공한 해당 규칙과 저장 범위를 전달합니다.

## 0.11.4에서 0.12.9로 바뀐 주요 내용

- **시작·선택·표시:** 실행 진단과 파일·폴더 선택 결과 처리를 보완하고 실제 바탕화면 기본 위치, Noto Sans KR을 제공합니다.
- **스킬·도구:** 공통·업무 폴더별 목록과 설치 발견/현재 CLI 보고를 구분합니다.
- **업무 진행:** 모든 업무의 승인·질문 대기 배지, 지원 환경의 알림, HTML 등 외부 앱 열기를 추가했습니다.
- **입력·실행 설정:** `/`·`@` 추천, 실제 모델·Effort·승인 모드 표시, `/effort`, Shift+Tab 모드 전환을 제공합니다. 설정 변경 자체가 새 업무 요청을 보내지 않습니다.
- **승인 카드:** 요약·범위·버튼을 간결하게 보여주며 Claude의 승인 규칙과 사용자가 선택한 저장 범위는 유지합니다.
- **배포 방식:** 기존 VBS와 함께 Python을 포함한 단일 EXE 방식을 제공합니다.

Workspace와 Company Agent 코어는 별도 배포입니다. 이 묶음은 설치된 코어·후크·스킬을 업데이트하지 않습니다. 일부 HTML 디자인 카드와 Stop 후크 개선에는 그 기능을 제공하는 코어가 필요합니다. 모델·모드·권한 선택도 설치 Claude가 실제 제공하는 범위를 따릅니다.

## 검증과 파일 확인

동일한 0.12.9 소스를 두 방식으로 묶어 검증했습니다.

| 확인 항목 | 결과 |
| --- | --- |
| 공개 소스 점검 | 인증·업무 상태·개인 설정 제외, 앱과 선택적 Company Agent 코어 분리 |
| 공개 저장소 회귀 | 603건 중 576 통과, 27 환경·선택 통합 생략, 실패·오류 0 |
| 별도 Windows 실행기 검사 | 3건 통과 |
| 선택적 Company Agent 통합 | 외부 코어를 명시한 51건 통과 |
| VBS ZIP | 현재 소스 64개·진단 해시 일치, 압축 해제한 서버의 격리 데모 시작·API·종료 통과 |
| EXE ZIP | 내장 앱 소스 64개가 VBS와 동일, ZIP 안의 EXE 해시·CRC 일치 |
| 실제 EXE 시작·연결 | 별도 Python 없이 시작·재열기, Claude 2.1.284 연결 준비, 사용자 메시지 0, 설정 변경 0 |
| 승인 화면 | 7가지 화면 조건과 승인·질문 응답 검증 통과 |

파일 크기와 SHA-256:

- VBS ZIP: 5,876,379 bytes — `032022458d2ee52611eecf203890eb991e6f7c761bb1d5aafdd8ec03135db071`
- EXE ZIP: 16,905,500 bytes — `e0310f59e927a3931182309b6d6c3db1e545acdce6e018e418643ad49e76b9f2`

Release의 [SHA256SUMS.txt](https://github.com/bongG-moon/workspace_local/releases/download/v0.12.9/SHA256SUMS.txt)와 다운로드한 ZIP의 SHA-256을 비교할 수 있습니다. 자동 생성되는 Source code ZIP 대신 위 실행용 ZIP을 사용하세요.

상세한 시험 범위와 미검증 환경은 저장소의 `docs/VALIDATION_0.12.9.md`에 기록합니다. 회사 PC의 UAC-off·그룹 정책·Office·DRM·알림 조합까지 전수 확인한 것은 아닙니다. 시작 오류는 실행한 묶음의 `Check-Workspace.cmd`로 읽기 전용 진단을 확인하세요. EXE에서는 [실행 캐시 안내](WORKSPACE_STANDALONE_EXE.md)를 따릅니다.

[상세 사용법](LOCAL_WORKSPACE.md) · [시작 진단 안내](WORKSPACE_STARTUP_DIAGNOSTIC.md) · [제3자 고지](../THIRD_PARTY_NOTICES.md)
