"""Isolated SDK fixture: completed turns alone persist resumed model metadata."""
import json
import os
from pathlib import Path
import sys
import uuid

if '--version' in sys.argv:
    print('restore-fixture'); sys.exit(0)
if '--help' in sys.argv:
    print('--input-format --output-format --permission-prompt-tool --permission-mode <mode> (choices: "manual", "auto")'); sys.exit(0)

root = Path.cwd()
resumed = next((arg.split('=', 1)[1] for arg in sys.argv if arg.startswith('--resume=')), None)
session_id = resumed or str(uuid.uuid4())
saved = root / ('restore-' + session_id + '.json')
state = json.loads(saved.read_text()) if resumed and saved.exists() else {
    'model': 'baseline-model', 'effort': 'medium', 'permissionMode': 'manual'}
case = os.environ.get('WORKSPACE_RESTORE_CASE', '')
model_changed = False


def emit(value):
    print(json.dumps(value), flush=True)


def models():
    return [
        {'value': 'baseline-model', 'displayName': 'Baseline fixture', 'supportsEffort': True,
         'supportedEffortLevels': ['low'] if case == 'narrow-effort' else ['low', 'medium', 'high']},
        {'value': 'alternate-model', 'displayName': 'Alternate fixture', 'supportsEffort': True,
         'supportedEffortLevels': ['medium', 'high']},
    ]


def init():
    emit({'type': 'system', 'subtype': 'init', 'session_id': session_id,
          'model': state['model'], 'effort': state['effort'], 'permissionMode': state['permissionMode']})


for raw in sys.stdin.buffer:
    data = json.loads(raw)
    with (root / 'restore-wire.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'pid': os.getpid(), **data}) + '\n')
    if data['type'] == 'user':
        init()
        if data['message']['content'] == 'wait for cancellation':
            continue
        saved.write_text(json.dumps(state), encoding='utf-8')
        emit({'type': 'result', 'session_id': session_id, 'is_error': False, 'result': 'fixture complete'})
        continue
    assert data['type'] == 'control_request'
    request = data['request']; subtype = request['subtype']
    body, error = {}, None
    if subtype == 'initialize':
        body = {'models': models(), 'commands': [], 'current_permission_mode': state['permissionMode']}
    elif subtype == 'get_settings':
        if case == 'settings-timeout' or (case == 'model-readback-timeout' and model_changed):
            continue
        if case == 'settings-auth' or (case == 'model-readback-auth' and model_changed):
            emit({'type': 'control_response', 'response': {'subtype': 'error', 'request_id': data['request_id'], 'error': 'not logged in'}})
            continue
        body = {'applied': {'model': state['model'], 'effort': state['effort'], 'ultracodeRequested': False}}
    elif subtype == 'set_model':
        if case in {'model-auth', 'model-unsupported'}:
            error = 'not logged in' if case == 'model-auth' else 'unsupported control'
        elif case == 'reject-model' and request['model'] == 'baseline-model':
            error = 'policy rejected model'
        else:
            state['model'] = request['model']
            model_changed = True
            if state['effort'] not in next(row['supportedEffortLevels'] for row in models() if row['value'] == state['model']):
                state['effort'] = 'medium' if state['model'] == 'alternate-model' else 'low'
    elif subtype == 'apply_flag_settings':
        if case == 'effort-auth':
            error = 'not logged in'
        elif case == 'reject-effort':
            error = 'unsupported control'
        else:
            state['effort'] = request['settings']['effortLevel']
    elif subtype == 'set_permission_mode':
        if case in {'permission-auth', 'permission-unsupported'}:
            error = 'not logged in' if case == 'permission-auth' else 'unsupported control'
        elif case == 'reject-permission' and request['mode'] == 'auto':
            error = 'policy rejected permission'
        else:
            state['permissionMode'] = request['mode']
            body = {'mode': state['permissionMode']}
    elif subtype == 'interrupt':
        break
    response = {'subtype': 'error' if error else 'success', 'request_id': data['request_id']}
    response.update({'error': error} if error else {'response': body})
    emit({'type': 'control_response', 'response': response})
