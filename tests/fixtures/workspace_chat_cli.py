"""Deterministic general-chat transport fixture; never calls a model or network."""
import json
import sys
import uuid

FLAGS = '--safe-mode --tools --strict-mcp-config --disable-slash-commands --no-chrome --disallowedTools --append-system-prompt --input-format --output-format --permission-prompt-tool'
if '--help' in sys.argv:
    print(FLAGS)
    raise SystemExit(0)
if '--version' in sys.argv:
    print('2.1.296 (deterministic fixture)')
    raise SystemExit(0)

assert all(value in sys.argv for value in ('--safe-mode', '--tools=', '--strict-mcp-config', '--disallowedTools=*'))
session = next((arg.split('=', 1)[1] for arg in sys.argv if arg.startswith('--resume=')), str(uuid.uuid4()))


def emit(value):
    sys.stdout.buffer.write((json.dumps(value, ensure_ascii=False) + '\n').encode('utf-8'))
    sys.stdout.buffer.flush()


for line in sys.stdin.buffer:
    value = json.loads(line)
    if value['type'] == 'control_request':
        subtype = value['request']['subtype']
        detail = {'tools': [], 'skills': [], 'mcp_servers': [], 'commands': [], 'models': []} if subtype == 'initialize' else {'mcpServers': []}
        emit({'type': 'control_response', 'response': {'subtype': 'success', 'request_id': value['request_id'], 'response': detail}})
        if subtype == 'interrupt':
            break
    elif value['type'] == 'user':
        assert value.get('client_composed') is True
        emit({'type': 'system', 'subtype': 'init', 'session_id': session, 'model': 'fixture-only', 'tools': [], 'skills': [], 'mcp_servers': []})
        prompt = value['message']['content']
        if prompt == 'permission-leak':
            emit({'type': 'control_request', 'request_id': 'leaked-tool', 'request': {'subtype': 'can_use_tool', 'tool_name': 'Bash', 'input': {'command': 'must-never-run'}}})
        else:
            emit({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': json.dumps(prompt, ensure_ascii=False)}]}})
            emit({'type': 'result', 'session_id': session, 'is_error': False, 'result': ''})
    elif value['type'] == 'control_response':
        assert value['response']['response']['behavior'] == 'deny'
        emit({'type': 'result', 'session_id': session, 'is_error': False, 'result': 'tool denied'})
