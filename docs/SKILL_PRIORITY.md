# Skill 목록, 겹침 확인, 프로젝트별 선택

Claude에서 `/company-agent:skills`를 입력하면 현재 프로젝트에서 발견한 Skill과 출처를 확인하고 같은 이름의 Skill 중 사용할 대상을 고를 수 있습니다. 예를 들어 사용자 폴더와 회사 플러그인에 모두 `review`가 있다면, 이 프로젝트에서는 회사 Skill을 사용하고 다른 프로젝트에서는 기본 선택을 따르게 할 수 있습니다.

이 기능은 Company Agent가 검색하고 컨텍스트에 제시한 후보 중 실제로 읽을 `SKILL.md`를 고릅니다. Skill 원본을 지우거나 이름을 바꾸지 않고, Claude의 설정 파일에 임의의 로딩 순서를 쓰지 않습니다.

## 직원이 사용하는 방법

개발본에서는 일반 업무 요청에도 관련 후보를 짧게 전달합니다. 같은 업무를 수행할 후보가
겹치고 명시 선택·저장된 우선순위가 없다면 Claude가 한국어로 선택을 묻습니다. 이름이 달라도
역할이 겹치면 설명을 비교해 질문하고, 단순히 함께 설치돼 있다는 이유로 묻지는 않습니다.
아래 목록 관리 명령을 매번 먼저 실행할 필요는 없습니다.

질문의 답은 이번 요청에만 적용합니다. 기본값·프로젝트 우선순위는 변경하지 않으며,
저장을 명시적으로 요청했을 때만 기존 `prefer` 절차로 저장합니다. 이번 요청의 선택은
`skill choose --session SESSION --turn TURN --candidate ID`로 기록하고 반환된 `readPath`의
본문을 읽습니다. 이 명령은 Claude가 사용자의 답을 받은 후 내부적으로 실행하며 사용자가
ID나 명령어를 입력할 필요는 없습니다. 선택 기록만으로 본문 읽기나 실행 승인을 만들지 않습니다.

1. 작업할 프로젝트에서 Claude Code를 열고 `/company-agent:skills`를 입력합니다.
2. Claude가 보여 주는 목록에서 Skill 이름, 출처, 현재 선택, 겹치는 후보를 확인합니다. 후보는 짧은 번호와 설명으로 안내하므로 JSON이나 내부 ID를 직접 편집할 필요가 없습니다.
3. 바꿀 Skill과 사용할 후보를 고르고 **이 프로젝트만** 또는 **기본값**을 선택합니다. 이미 요청에서 대상을 정했다면 같은 내용을 다시 묻지 않습니다.
4. Claude가 선택을 저장한 뒤 적용 범위와 선택한 출처를 알려 줍니다. 실제 업무에 사용할 때는 선택된 경로의 `SKILL.md` 전체를 읽어 수행합니다.

프로젝트에만 있는 Skill이나 해당 프로젝트에서만 활성인 플러그인 후보는 **이 프로젝트만**으로 저장합니다. **기본값**으로 고를 때는 프로젝트를 제외한 공통 후보 목록을 먼저 확인합니다. 프로젝트 후보를 모든 프로젝트의 기본 대상으로 저장하거나, 기본값으로 쓰기 위해 Skill 파일을 자동 복사·이동하지 않습니다.

다음과 같이 자연어로 요청해도 됩니다.

> 이 프로젝트에서 쓸 Skill 목록과 이름이 겹치는 Skill을 보여줘.

> review는 이 프로젝트에서 회사 플러그인 것을 우선 사용해줘.

> review의 프로젝트 선택을 지우고 기본 선택으로 돌아가게 해줘.

목록·검색만 요청하면 설정을 바꾸지 않습니다. 후보가 삭제되거나 옮겨져 이전 선택을 찾을 수 없으면 오래된 선택으로 표시하고 다시 선택하도록 안내합니다. 다른 후보를 조용히 대신 선택하지 않습니다.

## 목록에 포함되는 범위

목록은 구현이 지원하는 로컬 `SKILL.md` 위치와 현재 범위에서 활성인 로컬 설치 플러그인을 확인한 결과입니다. Claude 사용자 폴더, 현재 프로젝트와 Git 루트까지의 지원 경로, Company Agent 개인 상태·회사 플러그인·회사 Knowledge의 Skill 경로, 지정한 신규 후보를 조회합니다. Git을 사용하지 않는 업무 폴더에서는 가까운 상위 `.claude` 프로젝트 폴더까지 확인하되 사용자 홈·전체 설정 폴더·드라이브 루트를 프로젝트로 취급하지 않습니다. 모든 Skill 본문이나 프로젝트 전체를 재귀 탐색하지 않습니다.

이 목록은 Claude의 전체 네이티브 인벤토리와 같지 않습니다. 조직의 managed Skill, 내장 Skill, 원격 Skill, `--add-dir` 또는 세션 중 동적으로 발견된 경로 전체, `.claude/commands`의 기존 command까지 모두 열거하지 않습니다. 플러그인 등록 정보와 로컬 파일로 확인할 수 있는 범위에서 겹침을 알려 줍니다.

조회 결과의 `complete: false`는 지원 범위 안에서도 제한이나 읽기 오류로 확인하지 못한 항목이 있다는 뜻입니다. 이때 목록에 겹침이 없다는 이유로 전체 충돌 없음으로 판단하지 않습니다. `complete: true`도 위 지원 경로를 기준으로 한 값이며 모든 Claude 기능·Skill을 조사했다는 뜻은 아닙니다.

## 본문 로드와 실제 적용의 구분

**목록 발견 → 후보 선택 → 본문 로드 → 업무 실행·결과 확인**은 서로 다른 단계입니다. 목록이 전달되거나 선택이 저장됐다는 사실만으로 스킬 본문을 읽었다고 처리하지 않습니다. 현재 대화 안에 그대로 남아 있는 변경 없는 본문만 재사용하며, 관련 스킬이 없으면 없는 이름을 강제로 호출하지 않고 일반 실행합니다.

부모 대화가 작업자에게 위임할 때는 부모가 관련 본문을 먼저 읽거나 실제로 보유한 본문을 재사용해야 합니다. 별도 작업자가 같은 `session_id`로 보고해도 `agent_id`가 있으면 다른 대화로 구분하고, 그 작업자의 읽기 성공이나 준비 보정 횟수를 부모 기록에 합치지 않습니다. `agent_type`만 있다는 이유로 메인 대화를 작업자로 간주하지는 않습니다.

이 구분은 준비 기록의 정확성을 위한 것이며 작업자에게 권한을 추가하거나 회사 정책 검사를 생략하는 기능이 아닙니다. 본문 로드 기록도 지침을 실제로 따랐거나 결과가 정확하다는 증거는 아니므로 도구 실행 경로와 최종 결과를 함께 확인합니다. 사용자가 이를 위해 목록 명령이나 추가 승인 단계를 매번 실행할 필요는 없습니다.

## 설치 전에 겹치는 Skill 확인

설치기는 기존 Skill과 이번 ZIP에 들어오는 Skill을 비교합니다. 같은 Skill 이름을 대소문자 구분 없이 비교하며, 이름은 `SKILL.md`의 frontmatter `name`, 없으면 Skill 폴더 이름을 사용합니다. 설명이 비슷한 다른 이름까지 의미적으로 중복 판정하거나 MCP·Tool 전체의 역할 중복을 검사하는 기능은 아닙니다.

새로 들어오는 Skill과 겹치는 이름이 없으면 추가 질문 없이 진행합니다. 겹치면 출처와 후보를 보여 주고 다음 중 하나를 선택합니다.

| 설치 선택 | 결과 |
| --- | --- |
| 기존 선택 유지 / `KeepCurrent` | 이미 저장한 우선 선택을 유지하고 새 Skill을 함께 설치합니다. 선택하지 않은 겹침은 별도 선택이 필요할 수 있습니다. |
| 새 Company Agent Skill 우선 / `PreferIncoming` | 백업 후 해당 이름에 대해 새 Company Agent 후보를 우선 선택으로 저장합니다. |

`KeepCurrent`는 “모든 기존 Skill이 새 Skill보다 무조건 우선한다”는 뜻이 아닙니다. 기존 선호 설정과 기본 선택 규칙을 유지한다는 뜻이며, 플러그인 Skill은 네임스페이스로 함께 존재할 수 있습니다. 이 선택은 기존 하네스의 **유지/교체**와 별개입니다. 기존 하네스에서 `Keep`을 고르면 Company Agent 설치 자체를 하지 않습니다.

운영자가 사용하는 인자는 `-SkillConflictAction Ask|KeepCurrent|PreferIncoming`이며 기본값은 `Ask`입니다. 충돌이 있는 무인 실행의 `Ask`는 `status: input-required`, `input: SkillConflictAction`을 반환합니다. 실제 선택을 받은 후 해당 인자로 다시 실행합니다. Dry Run은 목록과 선택만 확인하며 설정을 저장하지 않습니다. 설치 실패 시 설치기가 바꾼 우선 선택도 복구합니다. 설치 후 이름별·프로젝트별로 조정하려면 `/company-agent:skills`를 사용합니다.

## 적용 범위와 보존

설정은 현재 세션의 `company_agent_runtime.stateRoot` 아래 `config/skill-preferences.json`에 저장됩니다. 파일에는 `defaults`와 `projects`가 있고, 현재 경로에 가장 가까운 프로젝트 설정이 같은 State의 기본값보다 우선합니다. 따라서 프로젝트 폴더에서 저장한 선택은 해당 하위 폴더에도 적용합니다. 이름별 후보 선택과 출처별 순서를 저장할 수 있습니다. 설정은 업데이트에서 보존되고 개인 설정 백업에 포함됩니다.

**기본값은 현재 State의 기본값**입니다. User 설치 하나로 여러 프로젝트를 사용하면 같은 State 안에서 기본값과 프로젝트별 예외를 공유합니다. Project 설치를 별도로 한 프로젝트는 자신의 State를 사용하므로 User 설치나 다른 Project 설치의 선호 설정이 자동으로 합쳐지지 않습니다. 프로젝트 선택을 초기화하면 남아 있는 가장 가까운 상위 프로젝트 설정 또는 같은 State의 기본값을 사용합니다.

이름별 기본 후보는 `inventory --no-project`에서 발견되는 후보만 저장할 수 있습니다. `--scope default` 저장은 프로젝트 후보를 제외해 검증합니다. 저장 결과도 `resolve <이름> --no-project`로 확인하고, 현재 프로젝트에 별도 선택이 남아 있으면 해당 프로젝트의 실제 선택을 함께 안내합니다. 기본값을 바꿔도 프로젝트의 명시 선택을 지우지 않습니다. 출처 순서를 기본값으로 저장할 때 `project`를 포함하여 각 프로젝트의 후보를 우선하도록 하는 것은 가능합니다.

이름별 명시 선택이 출처 순서보다 우선하며, 출처 순서는 사용자가 저장한 경우에 적용합니다. 같은 우선 출처에 후보가 여러 개이거나 겹침을 결정할 설정이 없으면 선택 필요로 표시합니다. 오래된 명시 선택은 출처 순서로 대신 해결하지 않습니다.

이 설정이 모델, MCP, permissions, 회사 관리 정책을 변경하지는 않습니다. 사용자가 명시적으로 고른 대상과 조직의 관리 정책은 계속 우선합니다.

## Claude의 기본 이름 처리와 차이

Claude Code 공식 문서는 같은 일반 Skill 이름에 대해 enterprise → personal → project 순으로 우선한다고 설명합니다. 플러그인 Skill은 `/플러그인명:Skill명` 형태라 다른 출처의 같은 이름과 함께 존재합니다. 따라서 프로젝트 Skill을 골랐더라도 사용자가 직접 입력한 일반 `/review`가 자동으로 그 프로젝트 Skill로 바뀐다고 보장할 수 없습니다. [Claude Code 공식 Skill 문서](https://code.claude.com/docs/en/skills#where-skills-live)

Company Agent의 우선 선택은 자체 Skill 검색·컨텍스트 안내·선택된 파일 읽기에 적용합니다. 선택한 흐름을 확실히 쓰려면 `/company-agent:skills`에서 후보를 확인하고 이어서 업무를 요청하세요. Claude의 기본 Skill 자동 선택이나 모든 슬래시 명령을 일괄 재정의하는 기능은 아닙니다. 파일을 읽는 흐름과 Claude가 Skill을 네이티브 호출하는 흐름도 같지 않으므로, 이름이 모호한 상태에서 `Skill(name)` 호출만으로 선택이 적용되었다고 판단하지 않습니다.

## 운영자용 CLI

아래 `company-agent`는 명령 형식 설명용입니다. 설치된 Claude 세션에서는 이를 `company_agent_runtime.cliCommand`의 정확한 실행 접두사로 바꾸어 호출합니다. Python이나 `company-agent`가 PATH에 있다고 가정하지 않습니다. 실제 경로와 후보 ID는 현재 세션과 조회 결과에서 가져옵니다.

```text
company-agent skill inventory
company-agent skill inventory --no-project
company-agent skill conflicts
company-agent skill search "보고서 검토"
company-agent skill resolve review
company-agent skill inventory --incoming-plugin "C:\Approved\new-plugin"
company-agent skill conflicts --incoming-skill "C:\Approved\review"
company-agent skill prefer --name review --candidate "<조회한 후보 ID>" --scope project --project-root "C:\Work\Report"
company-agent skill prefer --name review --candidate "<조회한 후보 ID>" --scope default
company-agent skill resolve review --no-project
company-agent skill order --sources project personal user company plugin corporate --scope project --project-root "C:\Work\Report"
company-agent skill reset --name review --scope project --project-root "C:\Work\Report"
company-agent skill reset --scope default
```

`inventory`는 발견한 목록, `conflicts`는 같은 이름의 후보, `search`는 선택되었거나 단독으로 사용 가능한 관련 후보, `resolve`는 해당 이름의 현재 선택을 보여 줍니다. 선택이 필요한 후보는 충돌 정보에서 확인합니다. 들어올 플러그인은 오프라인 플러그인 루트, 들어올 단일 Skill은 `SKILL.md`가 있는 폴더를 지정해 설치 전에 비교할 수 있습니다. 목록을 읽는 작업은 Skill 설치를 대신하지 않습니다.

조회 명령에는 `--project-root "절대경로"` 또는 `--no-project`, `--state-root "절대경로"`, `--claude-root "절대경로"`, `--plugin-root "설치된 Company Agent 플러그인 절대경로"`, `--incoming-plugin "플러그인 절대경로"`, `--incoming-skill "Skill 폴더 절대경로"`, `--base "회사 Knowledge 루트"`를 지정할 수 있습니다. `--project-root`와 `--no-project`는 함께 사용하지 않습니다. 다른 State를 지정하면 그 State의 선택을 읽으므로 세션의 실제 State를 확인해야 합니다.

`prefer`는 조회한 후보 ID를 이름별로 저장합니다. `order`는 출처를 앞에서부터 우선하도록 설정하며 `project`, `personal`, `user`, `company`, `plugin`, `corporate`에서 중복 없는 순서 또는 일부만 지정할 수 있습니다. `reset --name`은 해당 범위의 이름별 선택을 지우고, 이름 없는 `reset`은 해당 범위의 선호 설정을 초기화합니다. Skill 파일은 그대로 남습니다.

`resolve` 결과의 `selected`는 저장한 설정에 따른 선택, `available`은 단일 후보, `unresolved`는 추가 선택 필요, `stale-choice`는 저장한 후보를 찾지 못함, `not-found`는 해당 이름 없음입니다. 회사 플러그인 후보 ID는 플러그인 이름과 내부 상대 경로를 기준으로 하므로 버전별 설치·캐시 루트가 바뀌어도 같은 후보를 식별할 수 있습니다.

인벤토리와 자동 컨텍스트는 제한된 메타데이터와 경로를 사용합니다. 모든 Skill 본문을 매 요청에 넣지 않습니다. 후보의 설명·본문은 참조 자료로 취급하고, 실제 사용하기로 한 파일만 전체를 읽으며 사용자의 권한·보안 정책을 그대로 따릅니다.
