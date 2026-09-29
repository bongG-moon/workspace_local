"""Read-only, bounded capability catalog from CLI evidence and installed metadata.

Opening the catalog never initializes Claude, invokes tools, or alters a profile.
The installed inventory is discovery evidence only; runtime availability belongs
to the selected CLI connection and its own permissions.
"""
from __future__ import annotations

import hashlib
import json
import subprocess

MAX_ITEMS = 1000
MAX_DESCRIPTION = 800
GROUP_FIELDS = {'skills': 'skills', 'tools': 'tools', 'commands': 'slashCommands',
                'mcp': 'mcp', 'plugins': 'plugins'}
SOURCES = {'user', 'project', 'personal', 'company', 'plugin', 'corporate'}
TOOLS_REFERENCE = 'https://code.claude.com/docs/en/tools-reference'
# Concise labels checked against Anthropic's tool reference on 2026-09-29.
# This explains only names actually reported by the CLI; it never adds tools.
BUILTIN_TOOLS = {
    'Agent': ('작업 위임', '보조 에이전트에게 작업을 맡깁니다.'),
    'AskUserQuestion': ('선택 질문', '사용자의 선택이나 추가 답변을 받습니다.'),
    'Bash': ('명령 실행', '셸 명령을 실행합니다.'),
    'PowerShell': ('Windows 명령 실행', 'PowerShell 명령을 실행합니다.'),
    'Read': ('파일 읽기', '파일 내용을 읽습니다.'),
    'Write': ('파일 저장', '파일을 만들거나 덮어씁니다.'),
    'Edit': ('파일 수정', '파일의 지정한 부분을 수정합니다.'),
    'Glob': ('파일 찾기', '이름 패턴으로 파일을 찾습니다.'),
    'Grep': ('내용 검색', '파일 내용에서 문자열 패턴을 찾습니다.'),
    'Skill': ('스킬 사용', '대화에서 스킬을 실행합니다.'),
    'WebFetch': ('웹 페이지 읽기', '지정한 웹 주소의 내용을 가져옵니다.'),
    'WebSearch': ('웹 검색', '웹에서 정보를 검색합니다.'),
    'NotebookEdit': ('노트북 수정', 'Jupyter 노트북 셀을 수정합니다.'),
    'CronCreate': ('예약 등록', '현재 세션에 예약 작업을 만듭니다.'),
    'CronDelete': ('예약 취소', '등록된 예약 작업을 취소합니다.'),
    'CronList': ('예약 확인', '현재 세션의 예약 작업을 확인합니다.'),
    'EnterPlanMode': ('계획 시작', '구현 전 계획 모드로 전환합니다.'),
    'ExitPlanMode': ('계획 검토', '계획을 검토받고 계획 모드를 마칩니다.'),
    'EnterWorktree': ('별도 작업 공간', 'Git 작업 공간으로 전환합니다.'),
    'ExitWorktree': ('원래 공간 복귀', '원래 작업 위치로 돌아갑니다.'),
    'ListAgents': ('에이전트 확인', '메시지를 보낼 수 있는 에이전트를 확인합니다.'),
    'SendMessage': ('에이전트 메시지', '다른 에이전트에게 메시지를 보냅니다.'),
    'Monitor': ('변화 관찰', '백그라운드 출력이나 이벤트를 관찰합니다.'),
    'PushNotification': ('알림 보내기', '사용자에게 알림을 보냅니다.'),
    'ScheduleWakeup': ('다음 실행 예약', '반복 작업의 다음 실행을 예약합니다.'),
    'TaskStop': ('작업 중지', '실행 중인 백그라운드 작업을 중지합니다.'),
    'ListMcpResourcesTool': ('연결 자료 목록', '연결 서버의 자료 목록을 확인합니다.'),
    'ReadMcpResourceTool': ('연결 자료 읽기', '지정한 연결 서버 자료를 읽습니다.'),
    'ReportFindings': ('검토 결과 전달', '코드 검토 결과를 구조화해 전달합니다.'),
    'Workflow': ('복합 작업 실행', '여러 에이전트의 작업을 조정합니다.'),
}


def _identity(prefix, *parts):
    raw = json.dumps(parts, ensure_ascii=True, separators=(',', ':')).encode('ascii')
    return prefix + ':' + hashlib.sha256(raw).hexdigest()[:32]


def _text(value, limit=200):
    if not isinstance(value, str):
        return ''
    return ' '.join(''.join(c if ord(c) >= 32 else ' ' for c in value[:limit * 4]).split())[:limit]


def _row(value, group):
    if isinstance(value, str):
        value = {'name': value}
    if not isinstance(value, dict):
        return None
    name = _text(value.get('name'))
    if not name:
        return None
    row = {'name': name, 'description': _text(value.get('description'), MAX_DESCRIPTION)}
    provided_source = value.get('source') if isinstance(value.get('source'), str) else None
    for key in ('candidateId', 'installationId', 'originLabel', 'scopeLabel', 'pluginNamespace'):
        if _text(value.get(key)):
            row[key] = _text(value[key])
    if provided_source in SOURCES | {'builtin', 'harness', 'mcp'}:
        row['source'] = provided_source
    if isinstance(value.get('scope'), str) and value['scope'] in {'personal', 'project', 'company', 'unknown'}:
        row['scope'] = value['scope']
    if group in {'skills', 'commands'}:
        row['invocation'] = _text(value.get('invocation')) or '/' + name.lstrip('/')
    if group == 'tools':
        provided_kind = value.get('kind') if isinstance(value.get('kind'), str) else None
        parts = name.split('__', 2)
        if provided_kind in {'builtin', 'harness', 'mcp', 'unknown'}:
            row['kind'], row['classificationSource'] = provided_kind, 'runtime'
        elif provided_source in {'company', 'harness'}:
            row['kind'], row['classificationSource'] = 'harness', 'runtime'
        elif len(parts) == 3 and parts[0] == 'mcp' and all(parts[1:]):
            row['kind'], row['classificationSource'] = 'mcp', 'qualified-name'
        elif name in BUILTIN_TOOLS and provided_source in {None, 'builtin'}:
            row['kind'], row['classificationSource'] = 'builtin', 'reference'
        else:
            row['kind'], row['classificationSource'] = 'unknown', 'unknown'
        if row['kind'] == 'mcp':
            row['server'] = _text(value.get('server')) or (parts[1] if len(parts) == 3 and parts[0] == 'mcp' else '')
        row['descriptionSource'] = 'runtime' if row['description'] else 'unknown'
        if row['kind'] == 'builtin' and name in BUILTIN_TOOLS:
            row['displayName'] = BUILTIN_TOOLS[name][0]
            if not row['description']:
                row['description'] = BUILTIN_TOOLS[name][1]
                row['descriptionSource'] = 'reference'
            row['referenceUrl'] = TOOLS_REFERENCE
        row['id'] = _identity('runtime-tool', name, row['kind'], row.get('server'), row.get('source'),
                              row.get('candidateId'), row.get('installationId'), row.get('originLabel'),
                              row.get('pluginNamespace'))
    if group == 'mcp':
        row['status'] = _text(value.get('status'), 80) or 'unknown'
    if group == 'plugins':
        version = _text(value.get('version'), 80)
        if version:
            row['version'] = version
    return row


def _group(connection, group):
    key = GROUP_FIELDS[group]
    raw = connection.get(key)
    flags = connection.get('reported')
    # New bridges keep explicit presence flags. For older connections only a
    # nonempty list is evidence: their default [] cannot prove reported-empty.
    claimed = flags.get(group) is True if isinstance(flags, dict) else isinstance(raw, list) and bool(raw)
    reported = claimed and isinstance(raw, list)
    rows, seen, invalid = [], set(), 0
    if reported and isinstance(raw, list):
        for value in raw[:MAX_ITEMS]:
            row = _row(value, group)
            if row is None:
                invalid += 1
                continue
            key = row.get('id') or _identity('runtime', group, row.get('name'), row.get('source'),
                                              row.get('scope'), row.get('candidateId'), row.get('installationId'))
            if key not in seen:
                seen.add(key)
                rows.append(row)
    return {'reported': reported and not (invalid and not rows), 'items': rows,
            'limited': bool(invalid or (claimed and not isinstance(raw, list))
                            or (isinstance(raw, list) and len(raw) > MAX_ITEMS))}


def _installed(item, client, demo, validate_workspace, *, scope=None, workspace=None):
    base = {'state': 'not-selected', 'skills': [], 'limited': False, 'readFailures': 0,
            'counts': {'skills': 0, 'internal': 0, 'reference': 0, 'total': 0},
            'notice': '업무를 선택하면 해당 폴더의 설치된 스킬을 확인할 수 있습니다.'}
    if item is None and scope is None:
        return base, []
    if demo:
        return {**base, 'state': 'demo', 'notice': '체험 모드에서는 실제 설치 정보와 설정을 읽지 않습니다.'}, []
    if scope is None and item.get('trusted') is not True:
        return {**base, 'state': 'needs-trust', 'notice': '업무 폴더를 다시 확인하면 설치된 스킬을 조회할 수 있습니다.'}, []
    try:
        if scope is None:
            validate_workspace(item)
            snapshot = client.skill_inventory(item['workspace'])
        else:
            snapshot = client.discovery_inventory(workspace if scope == 'folder' else None)
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get('skills'), list):
            raise ValueError('Unsupported installed inventory')
        raw = snapshot['skills']
        rows, seen, invalid = [], set(), 0
        for value in raw[:MAX_ITEMS]:
            row = _row(value, 'skills')
            if row is None or not isinstance(value, dict) or type(value.get('userInvocable')) is not bool:
                invalid += 1
                continue
            # A discovered library can intentionally have no slash invocation.
            # Its presence is not evidence that /<name> is a callable command.
            row['invocation'] = _text(value.get('invocation'))
            source = value.get('source') if isinstance(value.get('source'), str) and value['source'] in SOURCES else 'unknown'
            row_scope = value.get('storageScope')
            if not isinstance(row_scope, str) or row_scope not in {'personal', 'project', 'company'}:
                row_scope = 'project' if source == 'project' else 'company' if source in {'company', 'corporate'} else 'personal' if source in {'user', 'personal'} else 'unknown'
            if scope == 'common' and row_scope not in {'personal', 'company'}:
                continue
            candidate_id = _text(value.get('candidateId')) or _identity('installed', row['name'], source, row_scope,
                                row['invocation'], row.get('installationId'), row.get('originLabel'))
            key = candidate_id if scope is not None else (row['name'], source, row_scope, row['invocation'])
            if key in seen:
                continue
            seen.add(key)
            visible = value['userInvocable']
            rows.append({**row, 'source': source, 'scope': row_scope,
                         'candidateId': candidate_id,
                         'explicitOnly': value.get('explicitOnly') is True,
                         'userInvocable': visible,
                         'kind': 'internal' if not visible else 'skill' if row['invocation'] else 'reference',
                         'pluginNamespace': _text(value.get('pluginNamespace'))})
        read_failures = snapshot.get('readFailures')
        read_failures = min(read_failures, MAX_ITEMS) if type(read_failures) is int and read_failures >= 0 else 0
        read_failures += invalid
        partial = snapshot.get('skillsLimited') is True or len(raw) > MAX_ITEMS or read_failures > 0
        source_warnings = snapshot.get('skillWarnings')
        if isinstance(source_warnings, list) and source_warnings:
            partial = True
        snapshot_state = snapshot.get('state') if isinstance(snapshot.get('state'), str) else None
        if snapshot_state in {'partial', 'unavailable'}:
            partial = True
        warnings = ['설치된 스킬의 일부 메타데이터는 확인하지 못했습니다.'] if partial else []
        if type(snapshot.get('skillConflicts')) is int and snapshot['skillConflicts'] > 0:
            warnings.append('이름이 겹치는 설치 스킬이 있습니다. 실제 연결의 목록을 우선 확인해 주세요.')
        counts = {kind: sum(row['kind'] == kind for row in rows) for kind in ('skill', 'internal', 'reference')}
        diagnostics = snapshot.get('diagnostics')
        projected_diagnostics = None
        if isinstance(diagnostics, dict):
            projected_diagnostics = {'code': _text(diagnostics.get('code'), 80),
                'summary': _text(diagnostics.get('summary'), 400),
                'checks': [{'code': _text(check.get('code'), 80), 'status': _text(check.get('status'), 30),
                            'message': _text(check.get('message'), 300)}
                           for check in diagnostics.get('checks', [])[:20] if isinstance(check, dict)]
                                   if isinstance(diagnostics.get('checks'), list) else []}
        context = snapshot.get('context')
        projected_context = None
        if isinstance(context, dict):
            source = context.get('configRootSource')
            projected_context = {
                'configRootSource': source if isinstance(source, str) and source in {'explicit', 'environment', 'default'} else 'unknown',
                'actualCliContextVerified': context.get('actualCliContextVerified') is True,
                'scope': scope or 'legacy',
            }
        state = ('partial' if rows else 'unavailable') if snapshot_state == 'unavailable' else (
            'partial' if partial and scope is not None else 'discovered')
        return {'state': state,
                'skills': rows, 'limited': partial, 'readFailures': read_failures,
                'diagnostics': projected_diagnostics,
                'context': projected_context,
                'counts': {'skills': counts['skill'], 'internal': counts['internal'],
                           'reference': counts['reference'], 'total': len(rows)},
                'notice': ('설치된 스킬 목록을 확인하지 못했습니다.' if state == 'unavailable' else
                           '설치 메타데이터에서 발견한 항목입니다. 현재 Claude 연결에서의 로드 여부나 실제 실행 성공을 보장하지는 않습니다.')}, warnings
    except (ValueError, OSError, UnicodeError, TypeError, KeyError, subprocess.SubprocessError):
        # Never expose stderr, settings values, paths from discovery errors, or
        # arbitrary harness dictionaries as catalog text.
        return {**base, 'state': 'unavailable',
                'notice': '이 폴더의 설치된 스킬 목록을 확인하지 못했습니다. Claude 연결에서 보고한 목록은 계속 볼 수 있습니다.'}, []


def catalog(item=None, *, client, demo=False, validate_workspace=lambda item: None, scope=None, workspace=None):
    if scope not in {None, 'common', 'folder'}:
        raise ValueError('확인할 목록 범위를 선택해 주세요.')
    if scope == 'folder' and (not isinstance(workspace, str) or not workspace):
        raise ValueError('목록을 확인할 폴더를 선택해 주세요.')
    if scope == 'common':
        item = None
    connection = item.get('connection') if item else None
    connection = connection if isinstance(connection, dict) else {}
    state = 'live' if connection.get('connected') is True else 'last-seen' if connection else 'unavailable'
    status = 'no-session' if item is None else 'demo' if demo else 'awaiting-runtime' if state == 'unavailable' else state
    notices = {
        'no-session': '먼저 업무를 선택해 주세요. 업무 폴더에 따라 사용할 수 있는 스킬과 도구가 달라집니다.',
        'demo': '체험 화면입니다. 실제 Claude의 현재 기능 목록이 아닙니다.',
        'awaiting-runtime': '아직 이 업무의 Claude 연결 목록이 없습니다. 첫 요청을 보내 연결이 준비되면 확인할 수 있습니다.',
        'live': '현재 Claude 연결이 시작될 때 보고한 목록입니다. 도구 사용에는 별도의 권한 확인이 필요할 수 있습니다.',
        'last-seen': '연결이 종료되어 마지막 연결의 목록을 표시합니다. 다음 업무 요청에서 목록이 달라질 수 있습니다.',
    }
    installed, warnings = _installed(item, client, demo, validate_workspace, scope=scope, workspace=workspace)
    groups = {group: _group(connection, group) for group in GROUP_FIELDS}
    if any(group['limited'] for group in groups.values()):
        warnings.append('연결 목록이 많거나 일부 항목 형식을 확인하지 못해 확인된 항목만 표시합니다. 각 분류는 최대 1,000개입니다.')
    if scope is not None:
        if scope == 'common':
            status = 'demo' if demo else 'installed-only'
            notices['installed-only'] = '회사·개인 공통 설치 목록입니다. 실제 업무 연결의 로드 여부와 실행 권한은 별도로 확인합니다.'
        elif not item:
            status = 'demo' if demo else 'installed-only'
            notices['installed-only'] = '선택한 폴더의 설치 목록입니다. 업무를 시작하거나 실행 권한을 부여하지 않습니다.'
    return {'schemaVersion': 1 if scope is None else 2, 'sessionId': item.get('id') if item else None,
            'context': {'scope': scope or 'legacy', 'workspace': workspace if scope == 'folder' else item.get('workspace') if item else None},
            'workspace': workspace if scope == 'folder' else item.get('workspace') if item else None,
            'status': status, 'notice': notices[status],
            'runtime': {'state': state, 'model': _text(connection.get('model')), 'groups': groups},
            'installed': installed, 'warnings': warnings}
