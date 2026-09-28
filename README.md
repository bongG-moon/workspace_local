# Company Workspace

터미널 명령이나 `cd`에 익숙하지 않은 사용자가 기존 Claude Code 업무 환경을 창, 버튼, 자연어 요청으로 사용할 수 있도록 만든 Windows 로컬 앱입니다.

현재 버전: **0.11.4**

**[제품·기술 설계 명세서](docs/WORKSPACE_DESIGN_SPEC.md)** — 누구를 위한 앱인지, 왜 이런 구조와 화면을 선택했는지, 구현 범위와 제약을 설명합니다.

## 무엇을 할 수 있나요?

- 새 업무 공간을 만들거나 기존 폴더를 골라 대화를 시작합니다.
- 파일을 첨부하고 요청, 실행 승인, 추가 질문, 중지와 후속 대화를 GUI에서 처리합니다.
- 이전 업무를 검색하고 이름을 바꾸거나 고정합니다.
- 자료와 이번 요청에서 관찰한 파일 변경을 구분하고 지원 파일을 미리 봅니다.
- 스킬·도구·연결 서버·명령 목록과 정보의 출처를 확인합니다.
- 지원되는 Company Agent 설치가 있으면 기억·업무 방식·따라 하기·사용 현황을 같은 화면에서 관리합니다.

일반 터미널 전체를 에뮬레이션하는 프로그램은 아닙니다. GUI가 표현하는 Claude 기능을 사용하고, 필요한 경우 기존 CLI로 이어가는 구조입니다. 스킬과 도구의 실제 실행 가능 범위는 설치된 Claude, 플러그인, 권한 및 회사 환경에 따라 달라집니다.

## 실행하기

필요 환경:

- Windows와 Windows PowerShell, Windows Script Host
- Python 3.11 이상
- 같은 사용자 계정에서 이미 실행 가능한 Claude Code와 기존 로그인·회사 연결 설정
- Microsoft Edge 권장. 없으면 기본 브라우저로 열립니다.

이 저장소를 내려받아 압축을 모두 푼 뒤 **`Company-Workspace.vbs`를 더블클릭**하세요. 관리자 권한으로 실행할 필요가 없습니다. 조직에서 스크립트 실행을 제한한다면 기존 승인 절차를 사용합니다.

새 업무에서 저장 위치를 고르고 해당 폴더의 지침·도구 실행을 신뢰하는지 확인한 뒤 요청합니다. 앱은 다른 사용자 계정의 설정을 찾아 사용하거나 보안 정책을 바꾸지 않습니다.

창의 **X**는 화면만 닫습니다. 연결까지 완전히 종료하려면 **설정 → 앱 종료**를 사용하세요. 기존 앱이 실행 중이면 업데이트 전에 이 방식으로 종료합니다.

자세한 사용법은 [Workspace 사용 안내](docs/LOCAL_WORKSPACE.md), 오프라인 읽기 자료는 [Company Agent 사용자 안내서](docs/Company-Agent-사용자-안내서.html)에 있습니다. HTML은 다운로드 후 로컬 브라우저에서 열 수 있습니다.

## Claude와 Company Agent의 관계

이 저장소는 **Workspace 화면과 로컬 연결 코드**를 제공합니다. Claude 실행 파일, 로그인 정보, 회사 API 키, 개인 Skills·Memory·MCP 설정, Company Agent 전체 설치본은 포함하지 않습니다.

일반 Claude 대화와 별개로 Company Agent의 기억·지침 관리 및 설치 스킬 조회에는 호환되는 활성 설치가 필요합니다. 앱은 등록된 설치 경로와 Python을 확인한 뒤 해당 설치의 서비스에 연결합니다. 설치가 없거나 지원되지 않으면 기능을 사용할 수 없다는 안내를 표시합니다.

기존 Company Agent 프로젝트: [bongG-moon/claude_base_repo](https://github.com/bongG-moon/claude_base_repo). 이 저장소의 `company-agent-plugin/resources/onboarding-course.json`은 따라 하기 화면에 쓰는 정적 자료입니다.

## 구성

| 위치 | 역할 |
| --- | --- |
| `Company-Workspace.vbs` | 콘솔 창 없이 실행하는 진입점 |
| `deploy/` | 사용자 확인, 실행·재실행·중복 방지, 배포 ZIP 생성 |
| `local_app/server.py` | 로컬 HTTP API, 세션·파일·종료 제어 |
| `local_app/bridge.py` | 기존 CLI의 구조화된 입출력과 승인·질문 처리 |
| `local_app/web/` | HTML/CSS/JavaScript 화면과 아이콘 |
| `local_app/WorkspacePicker.cs` | 고해상도 Windows 파일·폴더 선택 창 |
| `local_app/harness_client.py` | 활성 Company Agent 설치 연결 |
| `docs/` | 설계 명세서, 사용 안내, 검증 범위 |
| `tests/` | 가상 CLI와 회귀 테스트 |

앱 Python 런타임은 표준 라이브러리를 사용합니다. 웹 프레임워크 설치나 프론트엔드 빌드 없이 실행할 수 있습니다. 로컬 서버는 `127.0.0.1`에만 바인딩하며 실행마다 새 접근 토큰을 사용합니다. Claude가 외부 서비스에 보내는 요청은 기존 연결 설정을 따르므로, 앱 전체가 오프라인이라는 뜻은 아닙니다.

업무 기록은 기본적으로 `%LOCALAPPDATA%\CompanyAgent\local-ui`에 저장합니다. 선택한 자료와 생성 결과는 해당 업무 폴더에 남습니다. 저장소에는 실행 상태나 사용자 기록을 커밋하지 않습니다.

## 개발 및 검증

아래 명령은 저장소 루트의 PowerShell에서 실행합니다. 일상 사용자는 실행기를 더블클릭하면 됩니다.

AI를 호출하지 않는 화면 체험:

```powershell
powershell -NoProfile -File deploy/Start-CompanyWorkspace.ps1 -Demo
```

회귀 테스트:

```powershell
python -X utf8 -m unittest discover -s tests -p "test*workspace*.py" -v
node --check local_app/web/app.js
node --check local_app/web/companion.js
node --check local_app/web/capabilities.js
```

Node.js는 프론트엔드 회귀 테스트에 필요합니다. Windows API 및 실제 실행기 테스트는 해당 Windows 환경이 필요하며, 격리된 서비스 계정에서는 사용자 일치 검사가 정상적으로 차단할 수 있습니다. 가상 CLI 테스트 성공은 실제 회사 모델의 업무 성공을 뜻하지 않습니다.

Company Agent core 통합 테스트까지 실행하려면 별도로 검토한 Company Agent 소스의 **플러그인 루트**를 지정합니다. 실행 중인 개인 설치를 수정하지 않습니다.

```powershell
$env:COMPANY_AGENT_SOURCE = 'C:\dev\claude_base_repo\company-agent-plugin'
python -X utf8 -m unittest discover -s tests -p "test*workspace*.py" -v
Remove-Item Env:COMPANY_AGENT_SOURCE
```

외부 core가 없는 경우 해당 통합 항목만 건너뛰며, Workspace 자체 테스트는 실행합니다. 잘못된 경로를 명시하면 구성 오류로 처리합니다.

배포 ZIP 생성 및 검사:

```powershell
powershell -NoProfile -File deploy/New-WorkspaceBundle.ps1
python -X utf8 scripts/test-lab/check-workspace-bundle.py 'dist\<생성된 ZIP 파일명>.zip'
```

빌더는 필요한 42개 실행 파일·문서만 ZIP에 담습니다. 검사기는 허용 목록, 소스 일치, 시작 진단 해시, 개인 상태 미포함을 확인합니다. GitHub의 소스 ZIP에는 개발 문서와 테스트도 포함되며, 실행용 배포 ZIP과 구성이 다릅니다.

아이콘을 변경하는 개발자는 Pillow를 준비한 뒤 `python scripts/build-workspace-icon.py`를 사용합니다. 안내서 생성은 `node scripts/build-manuals.mjs --modules '<승인된 node_modules 경로>'`로 실행하며 해당 경로의 `marked`가 필요합니다. 폰트 재생성에는 fontTools와 승인된 원본 폰트가 필요합니다. 이미 생성된 앱·아이콘·안내서를 사용하는 데에는 이 도구들이 필요하지 않습니다.

## 현재 제약

- Windows 실행기는 동일 사용자·세션·권한 검사를 통과해야 합니다. 실행을 위해 UAC나 계정 설정을 자동 변경하지 않습니다.
- CLI의 보고·지원 여부에 따라 모델 변경과 기능 목록의 확인 범위가 달라집니다.
- 결과 목록은 요청 전후 파일 변경을 관찰한 정보이며, 결과 내용의 정확성을 보증하지 않습니다.
- 대화·기록 복원과 실제 Claude 연결 재개는 구분됩니다. 복원한 폴더의 신뢰는 다시 확인합니다.
- HTML은 스크립트를 실행하지 않는 제한된 미리보기를 제공합니다.
- 자동 업데이트, 설치 마법사, 모든 터미널 기능의 GUI화는 현재 구현 범위에 포함되지 않습니다.

[0.11.4 검증 범위](docs/VALIDATION_0.11.4.md)와 [제3자 고지](THIRD_PARTY_NOTICES.md)를 참고하세요.
