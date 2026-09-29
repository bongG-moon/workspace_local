"""No-network effort control fixture; never interprets business prompts."""
import json
import os
from pathlib import Path
import sys

if '--version' in sys.argv:
    print('effort-fixture'); sys.exit(0)
if '--help' in sys.argv:
    print('--input-format --output-format --permission-prompt-tool --permission-mode <mode> (choices: "manual", "auto")'); sys.exit(0)

model, effort, ultra = 'reported-model', 'medium', True
mode = os.environ.get('WORKSPACE_EFFORT_CASE', '')
changed = False

for raw in sys.stdin.buffer:
    data = json.loads(raw)
    with (Path.cwd() / 'effort-wire.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(data) + '\n')
    if data['type'] != 'control_request':
        raise AssertionError('Effort verification must not send prompts')
    request = data['request']; subtype = request['subtype']
    body, error = {}, None
    if subtype == 'initialize':
        body = {'models': [
            {'value': 'reported-model', 'resolvedModel': 'reported-model', 'supportsEffort': True,
             'supportedEffortLevels': ['low', 'medium', 'high', 'max']},
            {'value': 'unsupported-model', 'supportsEffort': False},
            {'value': 'restricted-model', 'supportsEffort': True, 'supportedEffortLevels': ['low']}], 'commands': []}
    elif subtype == 'get_settings':
        if mode == 'legacy' or changed and mode == 'unconfirmed':
            error = 'unknown request get_settings'
        else:
            body = {'effective': {'secret': 'DO_NOT_EXPOSE'},
                    'applied': {'model': model, 'effort': effort, 'ultracode': ultra,
                                'ultracodeRequested': ultra}}
    elif subtype == 'apply_flag_settings':
        if mode == 'timeout':
            continue
        if mode in {'rejected', 'unsupported'}:
            error = 'unknown request' if mode == 'unsupported' else 'policy rejected'
        else:
            assert set(request['settings']) == {'effortLevel', 'ultracode'}
            assert request['settings']['ultracode'] is True
            effort = request['settings']['effortLevel']
            if mode == 'clamp':
                effort = 'low'
            changed = True
    elif subtype == 'set_model':
        model = request['model']
        effort = None if model == 'unsupported-model' else 'low' if model == 'restricted-model' else effort
    elif subtype == 'interrupt':
        break
    response = {'subtype': 'error' if error else 'success', 'request_id': data['request_id'],
                'error': error} if error else {'subtype': 'success', 'request_id': data['request_id'], 'response': body}
    print(json.dumps({'type': 'control_response', 'response': response}), flush=True)
