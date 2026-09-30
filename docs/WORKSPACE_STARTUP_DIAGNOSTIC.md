# Workspace 시작 오류 확인하기

대상: **Workspace 0.20.0**. `Check-Workspace.cmd` 결과 파일의 `diagnosticVersion`은 `ws33-25`, `targetSource`는 `workspace-0.20.0`입니다. 오래된 진단 파일과 새 실행기를 섞지 마세요.

`WS-33`은 Windows 실행 권한 검사에서 중단됐다는 뜻입니다. 다운로드 폴더에서 실행했다는 이유만으로 표시되는 코드는 아니며, 이 번호만으로 회사 정책 문제라고 단정할 수 없습니다.

VBS의 일반 종료 코드 `1` 역시 원인을 확정하는 코드가 아닙니다. 실행 정책·스크립트 구문·시작 프로필 등 원래 실행 과정 중 어느 단계에서 실패했는지는 별도로 확인해야 합니다. 다른 PC에서 프로세스 한정 Bypass로 해결됐다는 사례만으로 같은 원인이라고 판단하지 않습니다.

EXE와 VBS 실행은 모두 이미 설치된 Python 3.11 이상이 필요합니다. 배포 파일에 Python 실행 환경·설치 프로그램은 없으며 자동 다운로드·설치하지 않습니다. `Check-Workspace.cmd` 진단은 Python이나 Claude를 실행하지 않으므로 진단 통과만으로 해당 준비 조건을 확인했다고 판단하지 마세요.

시작 실행기의 `WS-37`은 설치된 Python을 찾지 못한 경우, `WS-38`은 Python 실행·최소 버전·필수 기본 모듈을 확인하지 못한 경우의 안내입니다. 두 경우 모두 자동 설치나 PATH 변경을 하지 않습니다. 실제 메시지와 함께 기존 설치 상태를 확인하세요.

## Python 시작 실패 기록과 직접 경로 지정

`WS-37`·`WS-38`이 발생하면 실행기는 실제 후보 검사에서 확인한 상태를 오류 안내에 덧붙입니다. 기록 저장에 성공한 경우 기본 `%LOCALAPPDATA%\CompanyAgent\local-ui\diagnostics` 아래 `python-check-<시각>-<식별값>.json`이 생성되고 안내에 파일 위치가 표시됩니다. 별도 `StateRoot`를 지정했다면 그 상태 폴더의 `diagnostics` 아래에 저장됩니다. 저장 실패가 원래 시작 오류를 바꾸지는 않으며, 파일이 없다고 검사 성공으로 해석하지 않습니다.

이 파일은 **`diagnosticVersion: python-1`인 실제 Python 시작 실패 기록**입니다. 아래의 `Check-Workspace.cmd`가 만드는 `ws33-25` 읽기 전용 Windows 진단과 목적이 다릅니다. 기존 파일을 덮어쓰거나 외부로 전송하지 않습니다.

| 기록 | 의미 |
| --- | --- |
| `workspaceVersion`, `createdUtc`, `code` | 실행기 버전·기록 시각·`WS-37` 또는 `WS-38` |
| `attemptId` | 한 번의 시작 시도를 구분하는 식별값. 일반 권한 재실행 전후에 같은 시도의 기록만 연결합니다. |
| `sourceRoot`, `powershellVersion` | 실패한 앱 폴더와 사용한 PowerShell 버전 |
| `checks[].source`, `checks[].path` | 후보 발견 방식과 확인한 Python 경로 |
| `checks[].status` | 실행 파일 없음, 지원하지 않는 명령, 검사 자식 환경 준비 실패(`environment_failed`), 시작 실패, 실행 오류, 시간 초과, 응답 불일치, 구버전, 기본 모듈 확인 실패 등의 판정 |
| `checks[].version`, `nativeCode`, `missingModules` | 확인된 Python 버전, 확인된 Windows/종료 코드, 이름이 확인된 필수 기본 모듈. 확인하지 못한 값은 비어 있을 수 있습니다. |

**Python 실패 기록에는 로컬 설치·앱 경로가 포함될 수 있습니다.** 공유 전에 경로에 개인 이름이나 사내 정보가 있는지 확인하세요. 인증 값·Claude 설정·프로필 본문·환경변수 값·표준 오류 원문은 저장하지 않습니다. `missing_module`은 가져오기 검사 실패를 뜻하며, 설치 손상이나 회사 정책 등 근본 원인을 단정하지 않습니다. `invalid_response` 역시 Python 부재나 구버전으로 단정하는 상태가 아닙니다.

자동 탐색은 PATH에 있는 여러 Python 후보와 `py`의 기존 설치 목록, 현재 사용자·컴퓨터 범위의 PEP 514 등록 경로를 읽어 확인합니다. 후보 수와 시간에 제한이 있어 PC의 모든 설치를 빠짐없이 확인했다는 뜻은 아닙니다. 등록 정보나 PATH를 수정하지 않고 다른 사용자 프로필을 탐색하지 않습니다. 실제 실행 파일을 가리키는 별칭은 확인하며, 함수·`.cmd`·`.bat` 래퍼처럼 직접 확인할 수 없는 형식은 `unsupported_command`로 구분합니다. 표식이 있는 ASCII JSON 응답을 확인하므로 일반 안내 문구가 앞뒤에 섞여도 정상 응답을 구분합니다. 표식·응답 형식을 확인하지 못하면 중단합니다.

담당자가 정상 동작하는 기존 Python 경로를 확인했다면 EXE에 `--python`으로 직접 지정할 수 있습니다. 다음은 PowerShell 예시이며, 두 경로는 실제 EXE와 기존 Python 경로로 바꾸세요.

```powershell
& ".\Company-Workspace-0.20.0.exe" --python "C:\Tools\Python311\python.exe"
```

터미널 대신 EXE의 바로 가기를 만들어 **속성 → 대상**의 실행 파일 경로 뒤에 `--python "기존 python.exe의 절대 경로"`를 붙일 수도 있습니다. 파일이 존재하는 절대 `.exe` 경로만 허용하며, 지정한 설치가 검사를 통과하지 못하면 다른 Python으로 자동 대체하지 않습니다. 경로 지정은 그 실행기에만 적용되고 Claude 개인 설정·시스템 PATH에 저장되지 않습니다. VBS의 평소 더블클릭은 자동 탐색을 사용하며, 개발자 PowerShell 실행기는 기존 `-PythonCommand` 인수를 사용합니다.

운영 PC에서 별도 비교 진단을 받은 결과, 원래 실행 조건의 Python 3.11.5·기본 모듈 검사는 통과했고 앱이 만든 제한 권한 자식에서 파이프·자기 프로세스 접근이 코드 5로 실패했습니다. 기본 접근 목록에 현재 사용자 직접 허용이 없는 원본 설정을 제한 복사 토큰이 이어받은 경로를 확인했습니다. 이는 새로 받은 운영 진단에 근거한 원인 확인이며, `WS-38`이나 코드 5만으로 모든 PC의 원인을 동일하게 판단하는 것은 아닙니다. 2026-09-30 사용자가 수정본 0.12.12의 운영 PC 실행 성공을 명시적으로 확인했습니다. 추가 실행 로그를 수집한 것은 아니며, 이 사용자 보고는 0.20.0 검증과 구분합니다.

## 실행 방법

1. VBS 방식은 실행한 0.20.0 Workspace ZIP 전체를 압축 해제합니다. 단일 EXE 방식은 `%LOCALAPPDATA%\CompanyAgent\workspace-runtime`에서 실행한 0.20.0 폴더 아래 `Company-Workspace`를 엽니다. [EXE 실행 캐시 안내](WORKSPACE_STANDALONE_EXE.md)를 참고하세요.
2. 해당 묶음의 **`Check-Workspace.cmd`를 일반 더블클릭**합니다. 관리자 권한으로 실행하지 마세요.
3. 같은 폴더에 생긴 **`Workspace-Diagnostic-….json`**을 확인한 뒤 담당자에게 전달합니다. 결과가 자동 전송되지는 않습니다.

별도로 받은 진단 ZIP을 쓰는 경우에는 **같은 Workspace 버전을 위한** `Check-Workspace.cmd`와 `Check-Workspace.ps1` 두 파일을 오류가 난 `Company-Workspace.vbs` 옆에 복사해 실행합니다. 실행기 도우미 내용이 기준과 다르면 `DIAGNOSTIC_SOURCE_MISMATCH`로 멈춥니다. 두 진단 파일을 서로 다른 폴더에 두거나 압축 파일 미리보기 안에서 실행하지 마세요.

CMD나 PowerShell 실행이 차단되면 표시된 화면을 담당자에게 전달하세요. 0.20.0의 CMD는 `-ExecutionPolicy Bypass`를 전달하며 이번 실행의 PowerShell 프로세스와 그 자식 프로세스 계열에만 적용합니다. 영구 실행 정책·다운로드 표시·UAC를 변경하거나 관리자 실행·보안 프로그램 예외 등록을 요구하는 도구가 아닙니다. 조직의 그룹 정책이 우선하며 AppLocker·WDAC·WSH 차단까지 이 옵션으로 해제하지 않습니다. 결과를 저장하지 못하면 화면에 출력된 내용을 전달하면 됩니다.

## 확인하는 것과 바꾸지 않는 것

- 현재 실행 프로세스의 Windows 권한 상태와 사용자·세션 일치 여부를 확인합니다.
- 진단 프로세스의 유효 실행 정책과 5개 정책 범위, 묶음 내 대상 스크립트의 존재·다운로드 표시 유무·PowerShell 구문 상태를 읽습니다. 다운로드 표시 원문이나 URL은 읽어 보고하지 않습니다.
- 필요한 경우 연결된 일반 권한 토큰과 UAC 활성 상태를 조회합니다. 제한 토큰의 원본 후보 조건도 읽기만 합니다.
- `CreateRestrictedToken` 호출, 토큰 복제·권한 변경·다른 토큰으로의 전환·자식 프로세스 실행은 하지 않습니다. 제한 토큰 생성 경로를 시험하는 도구가 아닙니다.
- 회사의 영구 정책·로그인·모델·MCP·스킬·개인 설정을 변경하지 않습니다. Claude와 Workspace 업무 서버도 실행하지 않습니다.
- 계정 이름, SID, 실제 폴더 경로, 인증키, 예외 원문은 결과 파일에 넣지 않습니다. 확인 결과만 새 JSON 파일로 저장합니다.
- 기존 Windows PowerShell 시작 프로필은 평소처럼 적용됩니다. 원래 실패한 VBS의 프로필 실행을 재현하거나 프로필 파일을 읽어 저장하지 않으며, 프로필 자체의 동작까지 이 진단 도구가 통제하는 것은 아닙니다.
- 진단 결과는 Git에서 제외합니다. 개인 PC의 결과 파일을 저장소에 강제로 추가하지 마세요.

## 결과 읽는 법

| 결과 | 의미 |
| --- | --- |
| `normal_process_accepted` | 이 진단 프로세스는 일반 권한 조건을 통과했습니다. 원래 실패한 VBS 실행까지 정상이라는 뜻은 아닙니다. |
| `WS33_current_token_rejected` | 일반 실행 경로의 권한 조건을 통과하지 못했습니다. `current`의 판정값을 비교합니다. |
| `WS33_source_not_same_user_split_token` | 현재 권한은 자동 재실행에 필요한 원본 토큰 조건과 다릅니다. |
| `restricted_candidate_required_launch_not_tested` | UAC 비활성화와 제한 토큰 원본 후보 조건이 확인됐습니다. 실제 실행기는 제한 복사본 생성·Medium 무결성·자식 프로세스 검증이 더 필요합니다. 이 진단은 이를 실행하지 않았습니다. |
| `WS33_linked_token_not_normal` | 연결된 토큰이 현재 실행기의 허용 조건과 다릅니다. |
| `linked_candidate_accepted_launch_not_tested` | 연결된 토큰 조회·조건 확인까지만 통과했습니다. 실제 재실행은 시험하지 않았습니다. |
| `DIAGNOSTIC_SOURCE_MISMATCH` | 필요한 파일이 없거나 지원하는 실행기 버전과 다릅니다. `files`에서 구분합니다. |
| `DIAGNOSTIC_ALREADY_LOADED_TYPE` | 시작 프로필 등에 동명 형식이 이미 로드돼 정확한 구현을 확인할 수 없습니다. 임의로 계속 진행하지 않습니다. |
| 그 밖의 `incomplete` | 일부 상태를 확인하지 못했습니다. `stage`, `reason`, `nativeCode`를 담당자에게 전달합니다. |

### 실행 정책·스크립트 증거

| 항목 | 의미와 한계 |
| --- | --- |
| `executionPolicy.effective` | 지금 진단 PowerShell에 적용된 정책입니다. 원래 VBS 실행 시점의 정책을 관찰한 값은 아닙니다. |
| `MachinePolicy`, `UserPolicy` | 조직 그룹 정책 범위입니다. 값이 있으면 Process 옵션보다 우선하지만, 값이 있다는 사실만으로 차단 원인이라고 확정하지 않습니다. |
| `Process`, `CurrentUser`, `LocalMachine` | 진단 프로세스·사용자·컴퓨터 범위의 조회값입니다. `Process=Bypass`는 이번 진단 옵션일 수 있으며 영구 설정 변경을 뜻하지 않습니다. |
| 정책값 `null` | 조회하지 못했습니다. `Undefined`나 차단 없음으로 바꾸어 읽지 않습니다. |
| `scriptEvidence.<상대 파일명>.exists` | 해당 묶음 파일의 존재 여부입니다. 절대 사용자 경로는 보고서에 넣지 않습니다. |
| `downloadMarkPresent` | `Zone.Identifier` 스트림의 존재 여부만 나타냅니다. `true`는 차단 확정이 아니며 `null`은 미확인입니다. 표시 내용·다운로드 URL은 보고하지 않습니다. |
| `parseStatus` | `.ps1`은 실행하지 않고 구문만 확인합니다. `valid`는 구문 통과, `invalid`는 구문 오류, `unavailable`은 확인 불가입니다. `.vbs` 등 검사 대상이 아닌 형식은 `not_checked`일 수 있습니다. |
| `parseErrorCount` | 오류 개수만 표시하며 최대 1,000으로 제한합니다. 코드 본문·오류 원문은 보고하지 않습니다. 구문 통과는 실행 성공이 아닙니다. |
| `originalLaunchObserved=false` | 원래 실패한 VBS 프로세스를 직접 관찰하지 않았다는 뜻입니다. |
| `notTested` | 원래 VBS·프로필·실패 재현, 토큰 복제·제한 토큰 생성·자식 실행·Claude/Python 등 수행하지 않은 검사를 표시합니다. |

`nextStep`은 다음 확인 항목이며 실패 원인의 확정 판정이 아닙니다.

| 값 | 다음 확인 |
| --- | --- |
| `use_matching_workspace_bundle` | 같은 0.20.0 묶음의 실행기와 진단 파일인지 확인합니다. |
| `replace_invalid_workspace_scripts` | 구문 오류가 표시된 파일을 확인하고 정상 묶음과 대조합니다. 진단이 파일을 자동 교체하지는 않습니다. |
| `review_current_user_and_token` | 사용자·세션·토큰 판정과 실제 경고를 함께 확인합니다. |
| `review_effective_group_policy` | 조회된 조직 정책과 유효 정책을 확인합니다. 정책 변경을 지시하는 값이 아닙니다. |
| `compare_original_launch_context` | 진단과 원래 실행의 환경 차이를 비교합니다. 원래 실패가 재현됐다는 뜻은 아닙니다. |

## 진단 범위

이 진단은 `Check-Workspace.ps1`에 기록한 **Workspace 0.20.0 시작 파일 네 개의 고정 SHA-256**과 내용이 일치할 때만 해당 코드를 읽어 사용합니다. 대상은 `Start-CompanyWorkspace.ps1`, `CompanyWorkspace.Startup.ps1`, `CompanyWorkspace.NormalToken.cs`, `CompanyAgent.UserContext.ps1`입니다. 줄바꿈 방식과 UTF-8 BOM 차이는 허용하지만 다른 내용은 실행하지 않습니다. 스크립트 존재·다운로드 표시·구문은 실행 전에 읽기만 하므로, 그 증거가 있더라도 해시 불일치 코드를 실행한 것은 아닙니다. 이후 실행기가 변경되면 진단 버전·고정 해시·시험도 함께 갱신해야 합니다. 과거 `ws33-9` 등 이전 버전의 진단과 섞지 않습니다.

CMD로 실행한 진단과 처음 더블클릭한 VBS는 서로 다른 프로세스입니다. `normal_process_accepted`, `linked_candidate_accepted_launch_not_tested`, `restricted_candidate_required_launch_not_tested` 중 어느 값도 원래 VBS → Python → 실제 Claude 업무의 성공을 뜻하지 않습니다. 결과의 `notTested`에도 토큰 복제·제한 토큰 생성·자식 프로세스 실행 등을 표시합니다. 회사 보안 제품 전체 동작이나 `EnableLUA`만으로 정확한 실패 원인을 단정하지 않습니다.

0.20.0 실행기는 기존 일반 권한 연결 토큰을 우선 사용합니다. UAC 비활성화가 확인된 허용 원본에서는 제한 복사본을 만들고 관리자 SID·남은 권한·Medium 무결성·실제 자식 프로세스를 검증합니다. 실패하면 중단하며 관리자 원본 권한을 그대로 허용하지 않습니다. 기존 UAC·영구 보안 정책·계정·Claude 및 Company Agent 개인 설정은 진단으로 변경하지 않습니다. [Microsoft의 제한 토큰 API 설명](https://learn.microsoft.com/en-us/windows/win32/api/securitybaseapi/nf-securitybaseapi-createrestrictedtoken)은 토큰을 제한하는 방법의 근거이며, 특정 회사 PC에서 이 실행기가 성공했다는 증거는 아닙니다.

이 도구는 시작 환경을 확인합니다. 0.20.0의 모델·Effort·승인 모드 적용, `/effort`·Shift+Tab 동작이나 실제 AI 응답을 시험하지 않습니다. 실행 설정은 앱이 연결한 Claude의 실제 보고와 적용 응답으로 확인합니다.

0.12.12 실행 성공의 사용자 확인과 별개로, 0.20.0의 해당 운영 PC 전체 실행 경로를 추가 로그로 확인한 것은 아닙니다. 진단 파일의 `stage`, `predictedGuard`, `reason`, `nativeCode`, `sourceMatches`, `executionPolicy`, `scriptEvidence`, `nextStep`과 실제 경고 코드를 함께 확인하세요. 확인 없이 UAC나 회사 보안 설정을 변경할 필요는 없습니다.

소스에 진단 파일을 추가해도 기존 GitHub Release의 설치 ZIP이나 이미 다운로드한 Workspace ZIP은 자동 변경되지 않습니다.
