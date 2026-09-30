# Company Workspace 0.13.0

터미널 명령이나 `cd`에 익숙하지 않은 사용자가 기존 Claude Code 업무 환경을 창, 버튼, 자연어 요청으로 사용하는 Windows 로컬 앱입니다.

**[EXE ZIP 다운로드](https://github.com/bongG-moon/workspace_local/releases/download/v0.13.0/Company-Workspace-0.13.0-exe.zip)** · **[VBS ZIP 다운로드](https://github.com/bongG-moon/workspace_local/releases/download/v0.13.0/Company-Workspace-0.13.0-vbs.zip)**

[0.13.0 배포 안내](docs/WORKSPACE_RELEASE_0.13.0.md) · [사용법](docs/LOCAL_WORKSPACE.md) · [검증 범위](docs/VALIDATION_0.13.0.md)

## 어떤 파일을 받으면 되나요?

| 배포 파일 | 실행 방법 | 필요한 환경 |
| --- | --- | --- |
| `Company-Workspace-0.13.0-exe.zip` | ZIP을 풀고 `Company-Workspace-0.13.0.exe` 더블클릭 | Windows 10/11 x64, Windows PowerShell, 이미 설치된 Python 3.11 이상 |
| `Company-Workspace-0.13.0-vbs.zip` | ZIP 전체를 풀고 `Company-Workspace.vbs` 더블클릭 | Windows, Windows PowerShell, Windows Script Host, 설치된 Python 3.11 이상 |

**앱 전용 로그인·API 키는 없습니다. AI 업무에는 같은 Windows 사용자의 기존 Claude Code와 인증이 필요합니다.** 두 방식 모두 기존 PowerShell 프로필과 Claude 호출 환경을 사용합니다. EXE는 앱 파일만 묶은 실행기이며 VBS와 같은 시작 검사·PowerShell 경로를 사용합니다. 두 배포본 모두 Python 실행 환경이나 설치 프로그램을 포함하지 않고, 다운로드·자동 설치도 하지 않습니다. 기존 Python을 확인하지 못하면 안내 후 중단합니다. 회사의 VBS·EXE·PowerShell 허용 정책이 서로 다를 수 있으므로 모든 PC에서의 실행을 보장하지 않습니다.

EXE는 서명되지 않은 자체 제작 실행 파일입니다. 앱은 UAC·계정·영구 실행 정책을 자동 변경하지 않으며, 회사가 정한 배포 정책을 따릅니다. 상세 조건은 [단일 EXE 안내](docs/WORKSPACE_STANDALONE_EXE.md)와 [시작 진단](docs/WORKSPACE_STARTUP_DIAGNOSTIC.md)을 확인하세요.

## 0.13.0 앱 사용 흐름 개선

- 창의 X로 화면을 닫아도 앱은 백그라운드에 남습니다. Windows 트레이에서 다시 열거나 완전히 종료합니다.
- 응답 조각을 묶어 표시하고, 이전 내용을 읽을 때 스크롤 위치를 유지합니다.
- 자료·결과 파일을 입력창으로 끌어 붙입니다. 탐색기에서 끌어 온 파일은 앱 관리 공간의 복사본으로 첨부합니다.
- 실행 중 **끝나고 이어서**로 요청을 대기시키거나 **지금 반영**으로 현재 요청을 중지한 뒤 같은 대화를 이어갑니다.
- 한 번·매일·매주 예약을 지원합니다. PC가 깨어 있고 앱이 실행 중이어야 하며, 앱 재시작 후에는 내용을 확인하고 재개합니다.

**Company Agent 하네스는 필수가 아닙니다.** 후속 요청과 예약은 일반 Claude Code 연결과 기존 승인 경로를 사용합니다. [사용법·설계 이유·추가 제안](docs/WORKSPACE_0.13.0_DESKTOP.md)을 참고하세요.

0.12.12의 WS-38 수정 이후 운영 PC 실행 성공을 사용자가 확인했습니다. 그 권한·Python 탐색 코드는 유지합니다. 이번 0.13.0의 새 기능은 [별도 검증 기록](docs/VALIDATION_0.13.0.md)으로 범위를 구분합니다.

## 시작과 업데이트

1. 기존 앱이 열려 있으면 **설정 → 완전히 종료**를 누릅니다. 창의 X는 화면만 닫을 수 있습니다.
2. 선택한 ZIP을 새 폴더에 완전히 풉니다. 압축 파일 미리보기 안에서 실행하지 않습니다.
3. 해당 실행 파일을 더블클릭합니다. **한 번에 한 버전만 실행**하세요. 기존 업무를 강제 종료하거나 다른 버전을 조용히 재사용하지 않습니다.
4. 새 업무 공간 또는 기존 폴더를 선택하고, 폴더의 지침·도구 실행 범위를 확인합니다.
5. 자료를 추가하고 요청한 뒤 필요한 질문·승인에 답하고 결과를 확인합니다.

기존 업무·대화와 개인 Claude 인증·모델·MCP·스킬·기억 설정을 자동 이동하거나 덮어쓰지 않습니다. 승인 카드에서 사용자가 지속 허용을 직접 선택한 경우에만 CLI가 제공한 해당 규칙·범위의 저장을 요청합니다. Workspace와 Company Agent 코어는 별도 배포이며 이 파일들은 설치된 코어를 업데이트하지 않습니다.

## 0.11.4 이후 달라진 점

- 실제 바탕화면 아래 업무별 폴더를 만들고 저장 위치를 선택합니다. 기존 업무 폴더는 유지합니다.
- 파일·폴더 선택 응답을 별도 채널로 받아 안내 문구가 섞이는 오류를 줄이고, Noto Sans KR을 앱에 동봉합니다.
- 공통·업무 폴더별 스킬을 조회하고 설치에서 발견한 후보와 현재 CLI 보고를 구분합니다.
- 모든 업무의 승인·질문 대기를 배지로 확인합니다. 확인된 Edge 앱 창에 작업표시줄 강조를 요청하며 브라우저 알림은 직접 켠 경우에만 사용합니다.
- `/` 명령·`@` 파일 추천, 모델·Effort·승인 모드의 현재 값, `/effort`와 입력창의 Shift+Tab 모드 전환을 제공합니다.
- 승인 카드를 간결하게 정리하고 **이번만 허용 / CLI가 제안한 연결·지속 허용 범위**를 구분합니다. 기존 Claude 승인 규칙을 임의로 넓히지 않습니다.
- HTML을 포함한 지원 파일을 기본 앱에서 열거나 탐색기로 확인하고, 지원 텍스트를 메모장으로 엽니다.
- 기존 Python을 확인하여 사용하는 EXE 배포 방식을 제공합니다. Python 설치나 다운로드 없이 준비 조건만 확인합니다.

일반 터미널 전체를 에뮬레이션하지 않습니다. 제공 목록·모드·스킬의 실제 동작은 설치된 CLI, 모델, 회사 정책과 플러그인에 따릅니다. HTML 디자인 카드와 일부 Stop 후크 개선은 해당 기능을 제공하는 Company Agent 코어가 별도로 필요합니다.

## 기존 환경을 사용하는 구조

이 저장소는 **Workspace 화면과 로컬 연결 코드**를 제공합니다. Python 실행 환경·설치 프로그램, Claude 실행 파일, 로그인 정보, 회사 API 키, 개인 설정이나 Company Agent 전체 설치본은 포함하지 않습니다. 호환되는 Company Agent 설치가 있으면 기억·업무 방식·따라 하기 등을 연결하며, 등록을 확인하지 못하면 가능한 메타데이터 조회와 진단을 구분해서 표시합니다.

모델·Effort·승인 모드는 실제 CLI 응답으로 확인합니다. 같은 앱 실행 중 같은 업무의 재연결에는 선택을 메모리에서 다시 적용하지만, 앱을 완전히 재시작하면 개인 기본 설정을 다시 상속합니다. `/effort auto`는 관찰한 변경 전 값을 복원하며 개인 설정을 지우지 않습니다.

로컬 서버는 `127.0.0.1`에만 바인딩하고 실행마다 내부 접근 토큰을 만듭니다. 앱 로그인과 Claude 인증은 별개입니다. Claude가 외부 서비스에 보내는 요청은 기존 연결 설정을 따르므로 전체 오프라인 AI 앱이라는 뜻은 아닙니다.

업무 기록은 기본 `%LOCALAPPDATA%\CompanyAgent\local-ui`, EXE 실행 캐시는 `%LOCALAPPDATA%\CompanyAgent\workspace-runtime`에 둡니다. 자료·결과는 선택한 업무 폴더에 남습니다. 저장소에 개인 실행 상태·기록·인증 파일을 포함하지 않습니다.

[제품·기술 설계 기록](docs/WORKSPACE_DESIGN_SPEC.md) · [오프라인 사용자 안내서](docs/Company-Agent-사용자-안내서.html) · [Company Agent 프로젝트](https://github.com/bongG-moon/claude_base_repo)

## 개발 및 검증

개발 명령은 저장소 루트에서 실행합니다. 일반 사용자는 위 실행 파일을 사용하면 됩니다.

```powershell
# 실제 AI 요청 없는 화면 체험
python -X utf8 -m local_app.server --demo
# 격리 회귀 시험
python -X utf8 scripts/test-lab/test-workspace.py
# VBS 묶음 제작·검사
powershell -NoProfile -ExecutionPolicy Bypass -File deploy/New-WorkspaceBundle.ps1
python -X utf8 scripts/test-lab/check-workspace-bundle.py 'dist\<생성된 ZIP 파일명>.zip'
# 앱 파일만 포함한 EXE 제작 (실행 PC에 기존 Python 필요)
powershell -NoProfile -ExecutionPolicy Bypass -File deploy/New-WorkspaceStandalone.ps1
# 같은 버전의 VBS/EXE ZIP과 체크섬 생성 (기존 EXE와 소스 동일성 확인)
powershell -NoProfile -ExecutionPolicy Bypass -File deploy/New-WorkspaceRelease.ps1
```

앱은 Python 표준 라이브러리를 사용하며 프론트엔드 빌드 없이 실행합니다. Node.js는 UI 회귀 시험에 필요합니다. EXE 제작은 VBS와 같은 앱 파일과 Windows .NET Framework C# 컴파일러를 사용하며 Python 배포본을 다운로드·포함하지 않습니다. Windows 사용자·토큰 시험은 실제 로그인 환경과 별도 실행 조건을 구분합니다.

Company Agent 코어 통합 시험에는 별도로 검토한 소스의 플러그인 루트를 `COMPANY_AGENT_SOURCE`로 지정할 수 있습니다. 외부 코어가 없으면 해당 시험만 생략하며 잘못된 경로를 지정하면 구성 오류로 처리합니다. 시험 때문에 실행 중인 개인 설치를 수정하지 않습니다.

패키지 검사기는 허용 파일, 현재 소스 일치, 시작 진단 해시와 개인 상태 미포함을 확인합니다. GitHub의 자동 소스 ZIP과 위의 실행용 Release ZIP은 구성이 다릅니다. 가상 CLI·현재 개발 PC 시험은 모든 회사 PC의 실행 정책, 실제 문서 품질·Office·DRM 호환성을 대신하지 않습니다.

검증 결과와 남은 현장 확인은 [0.13.0 검증 기록](docs/VALIDATION_0.13.0.md), 라이선스는 [제3자 고지](THIRD_PARTY_NOTICES.md)를 확인하세요.
