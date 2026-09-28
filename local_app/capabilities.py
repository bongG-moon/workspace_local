"""Read-only, bounded capability catalog from CLI evidence and installed metadata.

Opening the catalog never initializes Claude, invokes tools, or alters a profile.
The installed inventory is discovery evidence only; runtime availability belongs
to the selected CLI connection and its own permissions.
"""
from __future__ import annotations

import subprocess

MAX_ITEMS = 1000
MAX_DESCRIPTION = 800
GROUP_FIELDS = {'skills': 'skills', 'tools': 'tools', 'commands': 'slashCommands',
                'mcp': 'mcp', 'plugins': 'plugins'}
SOURCES = {'user', 'project', 'personal', 'company', 'plugin', 'corporate'}


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
    if group in {'skills', 'commands'}:
        row['invocation'] = _text(value.get('invocation')) or '/' + name.lstrip('/')
    if group == 'tools':
        row['kind'] = 'mcp' if name.startswith('mcp__') else 'builtin'
        if row['kind'] == 'mcp':
            parts = name.split('__', 2)
            if len(parts) == 3:
                row['server'] = parts[1]
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
    reported = (flags.get(group) is True if isinstance(flags, dict)
                else isinstance(raw, list) and bool(raw))
    rows, seen = [], set()
    if reported and isinstance(raw, list):
        for value in raw[:MAX_ITEMS]:
            row = _row(value, group)
            if row and row['name'] not in seen:
                seen.add(row['name'])
                rows.append(row)
    return {'reported': reported, 'items': rows,
            'limited': isinstance(raw, list) and len(raw) > MAX_ITEMS}


def _installed(item, client, demo, validate_workspace):
    base = {'state': 'not-selected', 'skills': [], 'limited': False, 'readFailures': 0,
            'counts': {'skills': 0, 'internal': 0, 'reference': 0, 'total': 0},
            'notice': '업무를 선택하면 해당 폴더의 설치된 스킬을 확인할 수 있습니다.'}
    if item is None:
        return base, []
    if demo:
        return {**base, 'state': 'demo', 'notice': '체험 모드에서는 실제 설치 정보와 설정을 읽지 않습니다.'}, []
    if item.get('trusted') is not True:
        return {**base, 'state': 'needs-trust', 'notice': '업무 폴더를 다시 확인하면 설치된 스킬을 조회할 수 있습니다.'}, []
    try:
        validate_workspace(item)
        snapshot = client.skill_inventory(item['workspace'])
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
            source = value.get('source') if value.get('source') in SOURCES else 'unknown'
            scope = value.get('storageScope')
            if scope not in {'personal', 'project', 'company'}:
                scope = 'project' if source == 'project' else 'company' if source in {'company', 'corporate'} else 'personal' if source in {'user', 'personal'} else 'unknown'
            key = (row['name'], source, scope, row['invocation'])
            if key in seen:
                continue
            seen.add(key)
            visible = value['userInvocable']
            rows.append({**row, 'source': source, 'scope': scope,
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
        warnings = ['설치된 스킬의 일부 메타데이터는 확인하지 못했습니다.'] if partial else []
        if type(snapshot.get('skillConflicts')) is int and snapshot['skillConflicts'] > 0:
            warnings.append('이름이 겹치는 설치 스킬이 있습니다. 실제 연결의 목록을 우선 확인해 주세요.')
        counts = {kind: sum(row['kind'] == kind for row in rows) for kind in ('skill', 'internal', 'reference')}
        return {'state': 'discovered', 'skills': rows, 'limited': partial, 'readFailures': read_failures,
                'counts': {'skills': counts['skill'], 'internal': counts['internal'],
                           'reference': counts['reference'], 'total': len(rows)},
                'notice': '설치 메타데이터에서 발견한 항목입니다. 현재 Claude 연결에서의 로드 여부나 실제 실행 성공을 보장하지는 않습니다.'}, warnings
    except (ValueError, OSError, UnicodeError, TypeError, KeyError, subprocess.SubprocessError):
        # Never expose stderr, settings values, paths from discovery errors, or
        # arbitrary harness dictionaries as catalog text.
        return {**base, 'state': 'unavailable',
                'notice': '이 폴더의 설치된 스킬 목록을 확인하지 못했습니다. Claude 연결에서 보고한 목록은 계속 볼 수 있습니다.'}, []


def catalog(item, *, client, demo=False, validate_workspace=lambda item: None):
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
    installed, warnings = _installed(item, client, demo, validate_workspace)
    groups = {group: _group(connection, group) for group in GROUP_FIELDS}
    if any(group['limited'] for group in groups.values()):
        warnings.append('연결 목록이 많아 각 분류의 앞 1,000개까지만 표시합니다.')
    return {'schemaVersion': 1, 'sessionId': item.get('id') if item else None,
            'workspace': item.get('workspace') if item else None,
            'status': status, 'notice': notices[status],
            'runtime': {'state': state, 'model': _text(connection.get('model')), 'groups': groups},
            'installed': installed, 'warnings': warnings}
