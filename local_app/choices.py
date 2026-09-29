"""Bounded, explicit next-turn choices; never infer buttons from prose."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re

MAX_CHOICE_BYTES = 96 * 1024
STYLE_LABELS = {'minimalism': '미니멀리즘', 'bento-grid': '벤토그리드', 'editorial': '에디토리얼',
                'glassmorphism': '글래스모피즘', 'neumorphism': '뉴모피즘', 'brutalism': '브루탈리즘',
                'gradient-mesh': '그라디언트 메시', 'freeform': '자유양식',
                'immersive-3d': '3D·이머시브', 'retro-y2k': '레트로·Y2K'}
STYLE_IDS = frozenset(STYLE_LABELS)


def command_is_helper(command, origin):
    """Match literal registered entry points; never execute/import the helper."""
    if not isinstance(command, str) or len(command) > 16384 or not isinstance(origin, dict):
        return False
    command = command.strip()
    if command.startswith('& '):
        command = command[2:].lstrip()
    if any(char in command for char in '\r\n\0$`%!^;|<>&{}*?'):
        return False
    tokens, position = [], 0
    pattern = re.compile(r'''(?:"([^"]*)"|'([^']*)'|([^\s"']+))(?:\s+|$)''')
    while position < len(command):
        match = pattern.match(command, position)
        if match is None:
            return False
        value = next(value for value in match.groups() if value is not None)
        if match.group(3) is not None and any(char in value for char in '()[]'):
            return False
        tokens.append(value)
        position = match.end()
    if not tokens:
        return False
    def native_path(value):
        # Claude's Windows Bash presents /c/... paths. This mapping is only
        # lexical identity comparison; no translated command is ever executed.
        if os.name == 'nt' and re.match(r'^/[a-zA-Z]/', value):
            value = value[1] + ':/' + value[3:]
        return Path(value)
    def same(value, expected):
        try:
            path = native_path(value)
            return (isinstance(expected, str) and path.is_absolute() and '..' not in path.parts
                    and os.path.normcase(str(path)) == os.path.normcase(str(Path(expected))))
        except (ValueError, TypeError):
            return False
    program, *args = tokens
    if same(program, origin.get('python')):
        while args:
            if args[0] in {'-B', '-I', '-u', '-Xutf8'}:
                args.pop(0)
            elif args[:2] == ['-X', 'utf8']:
                args = args[2:]
            else:
                break
        if not args or not same(args[0], origin.get('script')):
            return False
        args = args[1:]
    elif same(program, origin.get('powershell')):
        if (len(args) < 8 or [arg.casefold() for arg in args[:5]] !=
                ['-nologo', '-noprofile', '-executionpolicy', 'bypass', '-file']
                or not same(args[5], origin.get('wrapper'))
                or [arg.casefold() for arg in args[6:8]] != ['-mode', 'cli']):
            return False
        args = args[8:]
    else:
        return False
    if args[:2] != ['business', 'html-choices'] or len(args[2:]) not in {2, 4}:
        return False
    fields = {}
    for index in range(2, len(args), 2):
        key, value = args[index:index + 2]
        if key not in {'--spec', '--state-root'} or key in fields or not value:
            return False
        fields[key] = value
    spec = native_path(fields.get('--spec', ''))
    return (spec.is_absolute() and '..' not in spec.parts and len(str(spec)) <= 4096
            and spec.suffix.casefold() == '.json'
            and ('--state-root' not in fields or same(fields['--state-root'], origin.get('stateRoot'))))


def text(value, maximum):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise ValueError('선택 항목의 형식을 확인하지 못했습니다.')
    try:
        value.encode('utf-8')
    except UnicodeError as exc:
        raise ValueError('선택 항목의 문자를 확인하지 못했습니다.') from exc
    return value.strip()


def normalize(value):
    if not isinstance(value, dict) or type(value.get('schemaVersion')) is not int or value['schemaVersion'] != 1:
        raise ValueError('지원하지 않는 선택 형식입니다.')
    if value.get('kind') != 'html-report-style' or value.get('responseMode') != 'next-user-message':
        raise ValueError('지원하지 않는 선택 종류입니다.')
    identifier = text(value.get('id'), 160)
    if not re.fullmatch(r'[a-zA-Z0-9:_-]+', identifier):
        raise ValueError('선택 식별자가 올바르지 않습니다.')
    options = value.get('options')
    if not isinstance(options, list) or not 1 <= len(options) <= 20:
        raise ValueError('선택 항목 수가 올바르지 않습니다.')
    rows, seen = [], set()
    for option in options:
        if not isinstance(option, dict) or not isinstance(option.get('id'), str) or option['id'] not in STYLE_IDS or option['id'] in seen:
            raise ValueError('선택 항목이 중복되거나 올바르지 않습니다.')
        seen.add(option['id'])
        text(option.get('label'), 120)
        rows.append({'id': option['id'], 'label': STYLE_LABELS[option['id']],
                     'description': text(option['description'], 400) if option.get('description') else ''})
    if type(value.get('allowCustom')) is not bool:
        raise ValueError('선택 응답 방식이 올바르지 않습니다.')
    return {'schemaVersion': 1, 'id': identifier, 'kind': 'html-report-style',
            'question': text(value.get('question'), 400), 'options': rows,
            'allowCustom': value['allowCustom'], 'responseMode': 'next-user-message'}


def from_tool_output(content):
    """Accept one complete JSON object, optionally a shell stdout envelope.

    No prefix stripping, first-object extraction, Markdown or fuzzy matching.
    Metadata can propose a question only: a click sends an ordinary user turn.
    """
    if isinstance(content, list):
        if len(content) != 1 or not isinstance(content[0], dict) or content[0].get('type') != 'text':
            return None
        content = content[0].get('text')
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError('Duplicate choice metadata')
            out[key] = value
        return out
    try:
        if not isinstance(content, str) or len(content.encode('utf-8')) > MAX_CHOICE_BYTES:
            return None
        value = json.loads(content, object_pairs_hook=unique)
        if isinstance(value, dict) and isinstance(value.get('stdout'), str):
            if value.get('interrupted') or any(key in value and (type(value[key]) is not int or value[key] != 0)
                                               for key in ('exit_code', 'exitCode')):
                return None
            value = json.loads(value['stdout'], object_pairs_hook=unique)
        if (not isinstance(value, dict) or value.get('ok') is not False or value.get('status') != 'input_required'
                or value.get('presentation') != 'workspace' or value.get('code') != 'report_choices_required'
                or value.get('stage') != 'design_detail' or value.get('missing') != ['style']
                or value.get('waitForUser') is not True):
            return None
        return normalize(value.get('workspaceChoice'))
    except (ValueError, TypeError, RecursionError, UnicodeError):
        return None


def answer(choice, *, option_id=None, custom=None):
    if option_id is not None and custom is not None:
        raise ValueError('선택지 또는 직접 입력 중 하나로 답변해 주세요.')
    if option_id is not None:
        if not isinstance(option_id, str):
            raise ValueError('선택 항목을 확인해 주세요.')
        option = next((row for row in choice['options'] if row['id'] == option_id), None)
        if option is None:
            raise ValueError('현재 질문에 있는 선택지를 골라 주세요.')
        # Explicit user text only. Neither an executable path nor a tool call.
        return f"{STYLE_LABELS[option['id']]} ({option['id']}) 디자인으로 진행해 주세요."
    if choice.get('allowCustom') is not True:
        raise ValueError('현재 질문의 선택지를 골라 주세요.')
    if not isinstance(custom, str) or not custom.strip() or len(custom) > 2000 or any(ord(c) < 32 and c not in '\n\r\t' for c in custom):
        raise ValueError('직접 입력한 답변을 확인해 주세요. 최대 2,000자까지 입력할 수 있습니다.')
    try:
        custom.encode('utf-8')
    except UnicodeError as exc:
        raise ValueError('직접 입력한 답변의 문자를 확인해 주세요.') from exc
    return custom.strip()
