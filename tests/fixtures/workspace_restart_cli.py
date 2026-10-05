"""Isolated restart fixture. Reads only its cwd; never invokes Claude or tools."""
import json
import os
from pathlib import Path
import sys
import uuid

if '--version' in sys.argv:
    print('restart-fixture'); sys.exit(0)
if '--help' in sys.argv:
    print('--input-format --output-format --permission-prompt-tool\n'
          '--allow-dangerously-skip-permissions Enable optional mode\n'
          '--permission-mode <mode> (choices: "manual", "auto", "bypassPermissions")'); sys.exit(0)

root = Path.cwd()
config = root / 'restart-config.json'
config = json.loads(config.read_text(encoding='utf-8')) if config.exists() else {}
resumed = next((arg.split('=', 1)[1] for arg in sys.argv if arg.startswith('--resume=')), None)
session = resumed or str(uuid.uuid4())
if config.get('wrongIdentity'):
    session = str(uuid.uuid4())
state = {'model': 'baseline-model', 'effort': 'medium', 'permissionMode': 'manual'}

def log(value):
    with (root / 'restart-wire.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'pid': os.getpid(), **value}) + '\n')

def emit(value):
    print(json.dumps(value), flush=True)

log({'type': 'startup', 'argv': sys.argv[1:], 'sessionId': session})
for raw in sys.stdin.buffer:
    data = json.loads(raw)
    log(data)
    if data['type'] == 'user':
        if data['message']['content'] == 'wait for cancellation':
            continue
        if data['message']['content'] == 'wait for approval':
            emit({'type': 'control_request', 'request_id': 'fixture-approval',
                  'request': {'subtype': 'can_use_tool', 'tool_name': 'Bash',
                              'input': {'command': 'fixture action'}}})
            continue
        emit({'type': 'result', 'session_id': session, 'is_error': False, 'result': 'fixture complete'})
        continue
    if data['type'] != 'control_request':
        continue
    request = data['request']; subtype = request['subtype']
    body, error = {}, None
    if subtype == 'initialize':
        if config.get('rejectInit'):
            error = 'Fixture startup rejected'
        else:
            emit({'type': 'system', 'subtype': 'init', 'session_id': session,
                  **state, 'tools': ['Read'], 'skills': config.get('skills', []),
                  'mcp_servers': config.get('mcp', []), 'plugins': []})
            body = {'models': [{'value': name, 'displayName': name, 'supportsEffort': True,
                                'supportedEffortLevels': ['low', 'medium', 'high']}
                               for name in ('baseline-model', 'alternate-model')],
                    'commands': config.get('commands', [{'name': 'before-restart'}]),
                    'current_permission_mode': state['permissionMode']}
    elif subtype == 'get_settings':
        body = {'applied': {'model': state['model'], 'effort': state['effort'], 'ultracodeRequested': False}}
    elif subtype == 'mcp_status':
        body = {'mcpServers': config.get('mcp', [])}
    elif subtype == 'set_model':
        state['model'] = request['model']
    elif subtype == 'apply_flag_settings':
        state['effort'] = request['settings']['effortLevel']
    elif subtype == 'set_permission_mode':
        if config.get('rejectPermission'):
            error = 'policy rejected permission'
        else:
            state['permissionMode'] = request['mode']
            body = {'mode': state['permissionMode']}
    elif subtype == 'interrupt':
        break
    response = {'subtype': 'error' if error else 'success', 'request_id': data['request_id']}
    response.update({'error': error} if error else {'response': body})
    emit({'type': 'control_response', 'response': response})
