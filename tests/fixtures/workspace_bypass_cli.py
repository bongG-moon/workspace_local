"""Control-only child; user/model/tool requests are forbidden in this fixture."""
import json
from pathlib import Path
import sys

HELP = '''--input-format --output-format --permission-prompt-tool
  --allow-dangerously-skip-permissions Enable the optional mode, not its default.
  --permission-mode <mode> (choices: "manual", "acceptEdits", "plan", "auto", "bypassPermissions")'''
if '--help' in sys.argv:
    print(HELP)
    raise SystemExit(0)
if '--version' in sys.argv:
    print('bypass-control-fixture 1')
    raise SystemExit(0)
assert '--dangerously-skip-permissions' not in sys.argv
assert '--permission-mode' not in sys.argv
behavior = next((value.partition('=')[2] for value in sys.argv if value.startswith('--fixture=')), '')
enabled = '--allow-dangerously-skip-permissions' in sys.argv
mode = 'bypassPermissions' if behavior == 'inherited' else 'default'
model, effort = 'fixture-model', 'high'


def emit(value):
    sys.stdout.buffer.write((json.dumps(value) + '\n').encode())
    sys.stdout.buffer.flush()


for line in sys.stdin.buffer:
    value = json.loads(line)
    with (Path.cwd() / 'bypass-controls.jsonl').open('a', encoding='utf-8') as record:
        record.write(json.dumps(value) + '\n')
    assert value['type'] == 'control_request', 'This fixture must never execute a user/model/tool request'
    request = value['request']
    response = {'subtype': 'success', 'request_id': value['request_id'], 'response': {}}
    kind = request['subtype']
    if kind == 'initialize':
        response['response'] = {'current_permission_mode': mode, 'models': [
            {'value': model, 'supportedEffortLevels': ['low', 'high'], 'supportsEffort': True}]}
    elif kind == 'get_settings':
        response['response'] = {'applied': {'model': model, 'effort': effort}}
    elif kind == 'set_permission_mode':
        requested = request['mode']
        if requested == 'bypassPermissions' and (behavior == 'policy' or not enabled and behavior != 'inherited'):
            response.update(subtype='error', error='Permission mode rejected by managed policy')
        else:
            mode = 'default' if requested == 'manual' or behavior == 'clamp' else requested
            if behavior != 'empty-ack':
                response['response'] = {'mode': mode}
                emit({'type': 'system', 'subtype': 'status', 'status': None, 'permissionMode': mode})
    else:
        response.update(subtype='error', error='Unsupported control fixture operation')
    emit({'type': 'control_response', 'response': response})
