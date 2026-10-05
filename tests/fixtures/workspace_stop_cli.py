"""Local protocol-only stop fixture: never invokes Claude, network, or tools."""
import json
import os
from pathlib import Path
import sys
import time
import uuid

session = str(uuid.uuid4())
root = Path.cwd()
identity_sent = False


def emit(value):
    print(json.dumps(value), flush=True)


def emit_init():
    emit({'type': 'system', 'subtype': 'init', 'session_id': session,
          'model': 'fixture-model', 'permissionMode': 'manual', 'mcp_servers': [], 'tools': []})


for raw in sys.stdin.buffer:
    data = json.loads(raw)
    with (root / 'stop-wire.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'pid': os.getpid(), **data}) + '\n')
    if data['type'] == 'user':
        if not identity_sent:
            time.sleep(.1)
            emit_init()
            identity_sent = True
        text = data['message']['content']
        if text == 'wait approval':
            emit({'type': 'control_request', 'request_id': 'approval', 'request': {
                'subtype': 'can_use_tool', 'tool_name': 'Bash', 'input': {'command': 'fixture only'}}})
        elif not text.startswith('wait'):
            emit({'type': 'result', 'session_id': session, 'is_error': False, 'result': 'completed: ' + text})
        continue
    if data['type'] != 'control_request':
        continue
    subtype = data['request']['subtype']
    ack = {'type': 'control_response', 'response': {
        'subtype': 'success', 'request_id': data['request_id'], 'response': {}}}
    if subtype == 'initialize':
        config_file = root / 'stop-config.json'
        config = json.loads(config_file.read_text()) if config_file.exists() else {}
        if not config.get('deferInit'):
            emit_init()
            identity_sent = True
    elif subtype == 'interrupt':
        config_file = root / 'stop-config.json'
        config = json.loads(config_file.read_text()) if config_file.exists() else {}
        terminal = {'type': 'result', 'session_id': session, 'is_error': True,
                    'result': config.get('error', '[ede_diagnostic] result_type=user last_content_type=n/a stop_reason=tool_use')}
        replies = [('ack', ack), ('result', terminal)]
        if config.get('order') == 'result-first':
            replies.reverse()
        for kind, value in replies:
            if config.get('missing') != kind:
                time.sleep(config.get('delay', .01))
                emit(value)
        continue
    emit(ack)
