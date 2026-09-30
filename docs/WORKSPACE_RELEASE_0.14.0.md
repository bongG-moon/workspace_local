# Company Workspace 0.14.0 배포 안내

기존 Claude 대화를 원래 폴더와 함께 불러오고, 완료·결정 대기를 알림함과 PC 알림으로 확인할 수 있습니다. 승인 모드에는 위험 확인을 거치는 Bypass 선택을 추가하고 제목 표시줄의 색상을 앱과 맞추도록 요청합니다.

| 배포 파일 | 실행 방법 |
| --- | --- |
| [EXE ZIP](https://github.com/bongG-moon/workspace_local/releases/download/v0.14.0/Company-Workspace-0.14.0-exe.zip) | 압축을 풀고 `Company-Workspace-0.14.0.exe` 실행 |
| [VBS ZIP](https://github.com/bongG-moon/workspace_local/releases/download/v0.14.0/Company-Workspace-0.14.0-vbs.zip) | ZIP 전체를 풀고 `Company-Workspace.vbs` 실행 |

기존 앱의 **설정 → 완전히 종료** 또는 **트레이 → 완전 종료**를 먼저 선택하세요. 창의 X만 누르면 백그라운드에 남을 수 있습니다. 기존 업무·개인 Claude·Company Agent 설정을 유지합니다.

두 방식 모두 이미 설치된 Python 3.11 이상과 Windows PowerShell이 필요합니다. EXE는 Windows 10/11 x64 기준이며 Python을 포함·설치·다운로드하지 않습니다. 앱 전용 로그인은 없고 AI 업무는 기존 Claude Code 인증을 사용합니다. Company Agent 하네스는 필수가 아닙니다.

- **이전 대화:** 왼쪽의 불러오기에서 최근 목록 또는 세션 UUID로 찾습니다. 원래 폴더와 대화를 확인한 후 불러오며 새 요청 전에 폴더 확인을 거칩니다.
- **알림:** 상단 알림함에서 해당 업무로 이동합니다. 설정에서 완료·승인·오류의 PC 알림을 선택할 수 있습니다. Windows 설정에 따라 배너가 표시되지 않을 수 있습니다.
- **Bypass:** 승인 목록에서 위험 설명을 확인한 뒤 적용합니다. 선택한 업무의 연결에만 적용하고 실제 CLI 응답을 확인합니다. Shift+Tab으로 실수로 켜지지 않으며, 켜진 상태에서는 일반 모드로 돌아갑니다.
- **제목 표시줄:** 지원되는 Windows 창에 앱 색상과 어울리는 색을 요청합니다. Windows 10·Edge 자체 제목 표시줄은 기존 색을 유지할 수 있습니다. 기본 창 버튼은 유지합니다.

[사용법과 구현 범위](WORKSPACE_0.14.0_CONTINUITY.md) · [검증 결과와 한계](VALIDATION_0.14.0.md) · [Release 페이지](https://github.com/bongG-moon/workspace_local/releases/tag/v0.14.0) · [체크섬](https://github.com/bongG-moon/workspace_local/releases/download/v0.14.0/SHA256SUMS.txt)

[v0.14.0 공개 사전 배포](https://github.com/bongG-moon/workspace_local/releases/tag/v0.14.0)에 등록한 EXE ZIP·VBS ZIP·SHA256SUMS.txt를 인증 없이 다시 다운로드하여 3개 모두 SHA-256 일치를 확인했습니다. Release는 초안이 아니며 소스·태그 커밋은 `26eb478d22093ad676253421d7a776c01439fe09`입니다. 후속 검증 문서 커밋과 관계없이 ZIP은 이 소스 기준입니다.
