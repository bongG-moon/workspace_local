"""Local model-denial fixture. No network, credentials, tools, or real Claude."""
import json
import os
from pathlib import Path
import sys
import uuid


FLAGS = ('--safe-mode --tools --strict-mcp-config --disable-slash-commands '
         '--no-chrome --disallowedTools --append-system-prompt --input-format '
         '--output-format --permission-prompt-tool --permission-mode')
if '--help' in sys.argv:
    print(FLAGS)
    raise SystemExit(0)
if '--version' in sys.argv:
    print('2.1.296 (Claude Code)')
    raise SystemExit(0)

assert '--model' not in sys.argv
config = Path(os.environ['CLAUDE_CONFIG_DIR']) / 'settings.json'
model = json.loads(config.read_text(encoding='utf-8'))['model']
denial = os.environ.get('AX_MODEL_RECOVERY_CASE', 'permission')
session = next((arg.split('=', 1)[1] for arg in sys.argv if arg.startswith('--resume=')), str(uuid.uuid4()))
chat = '--safe-mode' in sys.argv
if chat:
    assert all(flag in sys.argv for flag in ('--tools=', '--strict-mcp-config',
        '--disable-slash-commands', '--no-chrome', '--disallowedTools=*'))
    assert os.environ.get('CLAUDE_CODE_SAFE_MODE') == '1'


def emit(frame):
    sys.stdout.buffer.write((json.dumps(frame, ensure_ascii=False) + '\n').encode('utf-8'))
    sys.stdout.buffer.flush()


for raw in sys.stdin.buffer:
    frame = json.loads(raw)
    with (Path.cwd() / 'model-recovery-wire.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'pid': os.getpid(), **frame}) + '\n')
    if frame['type'] == 'control_request':
        request, detail = frame['request'], {}
        kind = request['subtype']
        if kind == 'initialize':
            detail = {'models': [{'value': name, 'supportedEffortLevels': ['medium']}
                                 for name in ('Fixture-Big-Latest', 'Fixture-Small-Latest')],
                      'current_permission_mode': 'default', 'commands': [], 'tools': [],
                      'skills': [], 'mcp_servers': []}
        elif kind == 'get_settings':
            detail = {'applied': {'model': model, 'effort': 'medium'}}
        elif kind == 'mcp_status':
            detail = {'mcpServers': []}
        elif kind == 'set_model':
            model = request['model']
        elif kind == 'set_permission_mode':
            assert not chat
            detail = {'mode': request['mode']}
        elif kind == 'interrupt':
            break
        else:
            raise AssertionError('Unexpected model recovery control: ' + kind)
        emit({'type': 'control_response', 'response': {'subtype': 'success',
              'request_id': frame['request_id'], 'response': detail}})
    elif frame['type'] == 'user':
        if chat:
            assert frame.get('client_composed') is True
        emit({'type': 'system', 'subtype': 'init', 'session_id': session, 'model': model,
              'permissionMode': 'default', 'tools': [], 'skills': [], 'mcp_servers': []})
        if model == 'Fixture-Big-Latest':
            message = ('API Error: 404 model Fixture-Big-Latest not found' if denial == 'missing' else
                       'API Error: 403 authentication_failed: team not allowed to access model Fixture-Big-Latest')
            emit({'type': 'assistant', 'error': 'authentication_failed',
                  'message': {'content': [{'type': 'text', 'text': message}]}})
            emit({'type': 'result', 'session_id': session, 'is_error': True,
                  'result': 'authentication_failed', 'errors': [message]})
        else:
            emit({'type': 'assistant', 'message': {'content': [{'type': 'text',
                  'text': 'Fixture response from ' + model}]}})
            emit({'type': 'result', 'session_id': session, 'is_error': False, 'result': ''})
