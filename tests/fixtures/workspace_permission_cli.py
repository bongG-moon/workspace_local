"""Permission protocol fixture: no tools, profiles, network or model execution."""
import json
import os
from pathlib import Path
import sys
import uuid


HELP = ('--input-format --output-format --permission-prompt-tool\n'
        '  --permission-mode <mode>  Permission mode (choices: "acceptEdits", "auto", '
        '"bypassPermissions", "manual", "dontAsk", "plan")')
if '--version' in sys.argv:
    print('permission-fixture 1.0')
    sys.exit(0)
if '--help' in sys.argv:
    print(HELP)
    sys.exit(0)

session = str(uuid.uuid4())
mode = 'manual'
if os.environ.get('WORKSPACE_PERMISSION_FIXTURE', '').startswith('runtime-alias'):
    mode = 'default'
turn = 0
allowed = False
rules = [{'toolName': 'Bash', 'ruleContent': 'fixture-read-one'},
         {'toolName': 'Bash', 'ruleContent': 'fixture-read-two'}]
destination = os.environ.get('WORKSPACE_PERMISSION_DESTINATION', 'session')
assert destination in ('session', 'localSettings', 'projectSettings', 'userSettings')
update = {'type': 'addRules', 'rules': rules, 'behavior': 'allow', 'destination': destination}


def emit(value):
    sys.stdout.buffer.write((json.dumps(value, ensure_ascii=False) + '\n').encode('utf-8'))
    sys.stdout.buffer.flush()


def result(text, error=False):
    emit({'type': 'result', 'is_error': error, 'session_id': session, 'result': text})


for line in sys.stdin.buffer:
    value = json.loads(line)
    with (Path.cwd() / 'permission-wire.jsonl').open('a', encoding='utf-8') as record:
        record.write(json.dumps(value) + '\n')
    if value['type'] == 'control_request':
        request = value['request']
        behavior = os.environ.get('WORKSPACE_PERMISSION_FIXTURE', '')
        detail = {}
        if request['subtype'] == 'initialize' and behavior.startswith('runtime-'):
            detail['current_permission_mode'] = mode
        if request['subtype'] == 'set_permission_mode':
            if behavior == 'timeout' and request['mode'] == 'auto':
                continue
            error = ('Unknown subtype set_permission_mode' if behavior == 'unsupported' else
                     'Permission mode rejected by policy' if behavior == 'runtime-alias-reject' and request['mode'] == 'manual' else
                     'Permission mode rejected by policy' if behavior in {'reject', 'runtime-reject'} and request['mode'] == 'auto' else '')
            if error:
                if behavior == 'runtime-reject':
                    emit({'type': 'system', 'subtype': 'status', 'status': None, 'permissionMode': request['mode']})
                emit({'type': 'control_response', 'response': {'subtype': 'error', 'request_id': value['request_id'], 'error': error}})
                continue
            mode = request['mode']
            if behavior.startswith('runtime-alias'):
                assert request['mode'] != 'default', 'This release only advertises manual on the wire'
                if mode == 'manual' or behavior == 'runtime-alias-clamp':
                    mode = 'default'
            if behavior.startswith('runtime-'):
                if behavior == 'runtime-clamp':
                    mode = 'manual'
                detail['mode'] = mode
                emit({'type': 'system', 'subtype': 'status', 'status': None, 'permissionMode': mode})
        emit({'type': 'control_response', 'response': {'subtype': 'success', 'request_id': value['request_id'], 'response': detail}})
    elif value['type'] == 'user':
        turn += 1
        init = {'type': 'system', 'subtype': 'init', 'session_id': session, 'model': 'fixture-only'}
        if os.environ.get('WORKSPACE_PERMISSION_OMIT_INIT') != '1':
            init['permissionMode'] = 'manual' if os.environ.get('WORKSPACE_PERMISSION_FIXTURE') == 'runtime-stale-init' else mode
        emit(init)
        if value['message']['content'] != 'permission' or allowed:
            result('기존 세션 허용 적용' if allowed else '연결 준비')
        else:
            emit({'type': 'control_request', 'request_id': 'p' + str(turn), 'request': {
                'subtype': 'can_use_tool', 'tool_name': 'Bash', 'input': {'command': 'fixture-read-one'},
                'permission_suggestions': [update]}})
    elif value['type'] == 'control_response':
        response = value['response']['response']
        if 'updatedPermissions' in response:
            if response['behavior'] != 'allow' or response['updatedPermissions'] != [update]:
                result('Invalid permission update', True)
                continue
            allowed = True
        result(('세션 허용 전달됨' if destination == 'session' else '설정 범위 전달됨: ' + destination)
               if allowed else '이번 요청 ' + response['behavior'])
