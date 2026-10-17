"""Tool-free model controls fixture. No network, real model or user settings."""
import json
import os
from pathlib import Path
import sys
import uuid

FLAGS = '--safe-mode --tools --strict-mcp-config --disable-slash-commands --no-chrome --disallowedTools --append-system-prompt --input-format --output-format --permission-prompt-tool'
if '--help' in sys.argv:
    print(FLAGS)
    raise SystemExit(0)
if '--version' in sys.argv:
    print('2.1.296 (deterministic fixture)')
    raise SystemExit(0)

assert all(value in sys.argv for value in ('--safe-mode', '--tools=', '--strict-mcp-config',
                                          '--disable-slash-commands', '--no-chrome', '--disallowedTools=*'))
assert '--model' not in sys.argv
assert os.environ.get('CLAUDE_CODE_SAFE_MODE') == '1'
session = next((arg.split('=', 1)[1] for arg in sys.argv if arg.startswith('--resume=')), str(uuid.uuid4()))
model = os.environ.get('ANTHROPIC_MODEL', 'fixture-default')
behavior = os.environ.get('AX_CHAT_MODEL_FIXTURE', '')
model_rows = [{'value': 'fixture-default', 'displayName': 'Fixture default'},
              {'value': 'fixture-alternative', 'displayName': 'Fixture alternative'}]


def emit(value):
    sys.stdout.buffer.write((json.dumps(value, ensure_ascii=False) + '\n').encode('utf-8'))
    sys.stdout.buffer.flush()


for line in sys.stdin.buffer:
    value = json.loads(line)
    with (Path.cwd() / 'chat-model-wire.jsonl').open('a', encoding='utf-8') as record:
        record.write(json.dumps(value) + '\n')
    if value['type'] == 'control_request':
        request, detail, error = value['request'], {}, ''
        subtype = request['subtype']
        if subtype == 'initialize':
            detail = {'tools': [], 'skills': [], 'mcp_servers': [], 'commands': [],
                      'models': [] if behavior == 'no-model-list' else model_rows}
        elif subtype == 'get_settings':
            if behavior == 'settings-timeout':
                continue
            if behavior == 'settings-auth':
                error = 'authentication_failed'
            elif behavior == 'settings-unavailable':
                error = 'Unknown subtype get_settings'
            else:
                detail = {'applied': {'model': model}}
        elif subtype == 'mcp_status':
            detail = {'mcpServers': []}
        elif subtype == 'set_model':
            requested = request['model']
            if requested == 'fixture-rejected' or behavior == 'reject-alternative' and requested == 'fixture-alternative':
                error = 'Model unavailable by fixture policy'
            elif requested == 'fixture-timeout':
                continue
            else:
                model = requested
        elif subtype == 'interrupt':
            emit({'type': 'control_response', 'response': {'subtype': 'success', 'request_id': value['request_id'], 'response': {}}})
            break
        else:
            raise AssertionError('Unexpected control in model-only general chat: ' + subtype)
        emit({'type': 'control_response', 'response': {'subtype': 'error' if error else 'success',
            'request_id': value['request_id'], **({'error': error} if error else {'response': detail})}})
    elif value['type'] == 'user':
        assert value.get('client_composed') is True
        emit({'type': 'system', 'subtype': 'init', 'session_id': session, 'model': model,
              'tools': [], 'skills': [], 'mcp_servers': []})
        if value['message']['content'] == 'hold':
            continue
        emit({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'fixture model: ' + model}]}})
        emit({'type': 'result', 'session_id': session, 'is_error': False, 'result': ''})
