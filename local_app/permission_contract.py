"""Bounded projections of Claude's permission control contract.

These helpers neither classify commands as safe nor grant permissions. Choices
come only from the current CLI request, and require an explicit user selection.
"""
from __future__ import annotations

import json
import re
import unicodedata
import uuid


MODE_LABELS = {
    'default': ('Manual', '필요한 작업마다 Claude의 기존 승인 규칙을 따릅니다.'),
    'manual': ('Manual', '필요한 작업마다 Claude의 기존 승인 규칙을 따릅니다.'),
    'plan': ('Plan', '실행 전에 계획을 검토합니다.'),
    'acceptEdits': ('Accept edits', 'Claude의 파일 편집 허용 모드를 이 연결에 적용합니다.'),
    'auto': ('Auto', 'Claude가 작업 위험을 판단하는 자동 모드를 이 연결에 적용합니다.'),
}

# These are SDK PermissionUpdateDestination values. Claude owns the setting
# location and enforcement; this module only offers its current suggestions.
PERMISSION_SCOPES = {
    'session': ('이 연결에서 허용',
                '현재 Claude 연결에서만 아래 규칙을 허용합니다. 새 연결에는 이어지지 않아요.'),
    'localSettings': ('이 프로젝트에서 항상 허용 · 나만',
                      '현재 프로젝트의 개인 설정에 아래 규칙을 저장합니다. 다음 연결에도 적용돼요.'),
    'projectSettings': ('이 프로젝트에서 항상 허용 · 공유 설정',
                        '프로젝트 공유 설정에 아래 규칙을 저장합니다. 이 설정 파일을 공유하면 다른 사용자에게도 적용될 수 있어요.'),
    'userSettings': ('모든 프로젝트에서 항상 허용 · 내 설정',
                     '현재 Claude 사용자 설정에 아래 규칙을 저장합니다. 다른 프로젝트와 다음 연결에도 적용돼요.'),
}


def help_permission_modes(help_text):
    """Use the installed version's explicit choices; never translate aliases."""
    if not isinstance(help_text, str):
        return []
    option = re.search(r'--permission-mode\b', help_text[:256 * 1024])
    if option is None:
        return []
    detail = help_text[option.end():option.end() + 1500]
    detail = re.split(r'\n\s*--[a-z]', detail, maxsplit=1)[0]
    choices = re.search(r'\bchoices:\s*([^)]{1,900})', detail)
    if choices is None:
        return []
    names = set(re.findall(r'[\"\x27]([A-Za-z][A-Za-z0-9]*)[\"\x27]', choices.group(1)))
    if 'default' in names:
        names.discard('manual')
    return [mode for mode in MODE_LABELS if mode in names]


def mode_options(modes):
    modes = list(dict.fromkeys(modes))
    return [{'value': mode, 'displayName': MODE_LABELS[mode][0],
             'description': MODE_LABELS[mode][1]} for mode in modes
            if mode in MODE_LABELS and not (mode == 'manual' and 'default' in modes)]


def mode_label(mode):
    return {'default': 'manual mode on', 'manual': 'manual mode on',
            'acceptEdits': 'accept edits on', 'plan': 'plan mode on',
            'auto': 'auto mode on', 'dontAsk': "don't ask on",
            'bypassPermissions': 'bypass permissions on'}.get(mode, observed_mode(mode))


def mode_cycle(modes):
    """CLI Shift+Tab order, limited to this connection's observed choices."""
    manual = 'default' if 'default' in modes else 'manual'
    return [mode for mode in (manual, 'acceptEdits', 'plan', 'auto') if mode in modes]


def mode_wire_value(mode, available):
    """Resolve the CLI's Manual alias without changing the observed state.

    Some releases advertise `manual` but report the canonical `default` in
    initialize/status/ACK. Only map this documented alias, and only to an
    available (not previously rejected) value from this connection.
    """
    if not isinstance(mode, str):
        return None
    if mode in available:
        return mode
    alias = {'manual': 'default', 'default': 'manual'}.get(mode)
    return alias if alias in available else None


def observed_mode(value):
    # Unknown current modes may be displayed/restored as inherited state, but
    # do not become new selectable modes merely because init reports them.
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9]{0,63}', value) else ''


def _display_text(value, maximum=2000):
    """Bound CLI display metadata and remove terminal/control presentation."""
    if not isinstance(value, str):
        return ''
    text = value[:16000]
    text = re.sub(r'(?:\x1b\]|\x9d)[^\x07\x1b\x9c]*(?:\x07|\x1b\\|\x9c|$)', '', text)
    text = re.sub(r'(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]', '', text)
    text = re.sub(r'\x1b[@-_]', '', text).replace('\r\n', '\n').replace('\r', '\n')
    text = ''.join(char for char in text if char in '\n\t'
                   or unicodedata.category(char) not in {'Cc', 'Cf', 'Cs'}).strip()
    return text[:maximum - 1] + '…' if len(text) > maximum or len(value) > 16000 else text


def request_context(request):
    """Display only the current ask's reason; never expose arbitrary settings."""
    if not isinstance(request, dict):
        return {}
    result = {}
    reason = _display_text(request.get('decision_reason'))
    if reason:
        result['decisionReason'] = reason
    reason_type = request.get('decision_reason_type')
    if isinstance(reason_type, str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9]{0,63}', reason_type):
        result['decisionReasonType'] = reason_type
    if request.get('suppress_always_allow_rule') is True:
        result['suppressAlwaysAllowRule'] = True
    rule = request.get('matched_ask_rule')
    if isinstance(rule, dict):
        source, tool = rule.get('source'), rule.get('tool_name')
        if (isinstance(source, str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_.:-]{0,119}', source)
                and isinstance(tool, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}', tool)
                and ('rule_content' not in rule or isinstance(rule['rule_content'], str))):
            projected = {'source': source, 'toolName': tool}
            if 'rule_content' in rule:
                projected['ruleContent'] = _display_text(rule['rule_content'])
            result['matchedAskRule'] = projected
    return result


def session_choices(request):
    """Return explicit choices and private updates from this CLI request only.

    The historical name is retained for bridge compatibility. Nothing is
    persisted here: only selecting an offered ID sends its update to Claude.
    """
    if not isinstance(request, dict) or request.get('suppress_always_allow_rule') is True:
        return [], {}
    tool = request.get('tool_name')
    raw = request.get('permission_suggestions')
    if tool == 'AskUserQuestion' or not isinstance(tool, str) or not isinstance(raw, list):
        return [], {}
    choices, updates, seen = [], {}, set()
    for suggestion in raw[:8]:
        if (not isinstance(suggestion, dict) or set(suggestion) - {'type', 'destination', 'behavior', 'rules'}
                or suggestion.get('type') != 'addRules'
                or not isinstance(suggestion.get('destination'), str)
                or suggestion['destination'] not in PERMISSION_SCOPES
                or suggestion.get('behavior') != 'allow'):
            continue
        raw_rules = suggestion.get('rules')
        if not isinstance(raw_rules, list) or not 1 <= len(raw_rules) <= 8:
            continue
        rules = []
        for rule in raw_rules:
            if (not isinstance(rule, dict) or set(rule) - {'toolName', 'ruleContent'}
                    or rule.get('toolName') != tool
                    or re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}', tool) is None):
                break
            projected = {'toolName': tool}
            if 'ruleContent' in rule:
                content = rule['ruleContent']
                # The SDK serializes an unrestricted tool rule as null. Keep
                # null/omitted exactly as supplied, and label both as whole-tool.
                if content is not None and (not isinstance(content, str) or not content or len(content) > 2000
                        or any(ord(char) < 32 or ord(char) == 127
                               or 0xD800 <= ord(char) <= 0xDFFF for char in content)):
                    break
                projected['ruleContent'] = content
            rules.append(projected)
        if len(rules) != len(raw_rules):
            continue
        destination = suggestion['destination']
        update = {'type': 'addRules', 'rules': rules, 'behavior': 'allow', 'destination': destination}
        key = json.dumps(update, ensure_ascii=True, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        choice_id = 'permission-' + uuid.uuid4().hex
        updates[choice_id] = update
        label, description = PERMISSION_SCOPES[destination]
        choices.append({'id': choice_id, 'label': label, 'description': description,
                        'destination': destination, 'rules': [dict(rule) for rule in rules]})
    return choices, updates
