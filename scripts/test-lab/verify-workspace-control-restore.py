"""Exercise model reset -> cancelled turn -> resume on an owned test app.

Use an isolated EXE run created by verify-workspace-standalone.py. Model names
come only from that CLI connection; the test never edits personal settings.
Each action is explicit so a long CLI response can be inspected between steps.
"""
import argparse
import json
from pathlib import Path
import time
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

parser = argparse.ArgumentParser()
parser.add_argument('action', choices=['probe', 'alternate', 'reset-cancel', 'follow-up', 'status'])
parser.add_argument('--qa-dir', type=Path, required=True)
args = parser.parse_args()
out = args.qa_dir.resolve(strict=True)
state = out / 'app-state'
sid = json.loads((out / 'task.json').read_text(encoding='utf-8'))['id']


def request(route, data=None):
    runtime = json.loads((state / 'runtime.json').read_text(encoding='utf-8'))
    url = urlsplit(runtime['url'])
    headers = {'Authorization': 'Bearer ' + parse_qs(url.fragment)['token'][0]}
    if data is not None:
        headers['Content-Type'] = 'application/json'
    query = Request(url.scheme + '://' + url.netloc + route,
                    data=json.dumps(data).encode() if data is not None else None, headers=headers)
    with urlopen(query, timeout=75) as response:
        return json.load(response)


def session():
    return request('/api/session?id=' + sid)


def controls(item):
    return {key: item['connection'].get(key) for key in
            ('model', 'modelOverride', 'effort', 'effortOverride', 'permissionMode',
             'permissionModeOverride', 'controlRestore')}


def save(name, value):
    (out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def wait_for(predicate, seconds=45):
    deadline = time.monotonic() + seconds
    while True:
        item = session()
        if predicate(item):
            return item
        assert time.monotonic() < deadline, 'Owned test app did not reach expected state'
        time.sleep(.2)


def send(text):
    return request('/api/send', {'id': sid, 'trusted': True, 'attachments': [], 'text': text})


if args.action == 'probe':
    prepared = request('/api/connect', {'id': sid})
    item = session()
    connection = prepared['connection']
    assert prepared['ok'] and not item['messages']
    levels = {row['value'] for row in connection.get('availableEfforts', [])}
    assert levels, 'Current CLI model does not provide Effort levels for this test'
    effort = 'low' if 'low' in levels else sorted(levels)[0]
    models = connection.get('availableModels', [])
    candidates = [row for row in models if row.get('value') and
                  row['value'] not in {connection['model'], 'default'} and
                  row.get('resolvedModel') != connection['model'] and
                  (row.get('supportsEffort') is False or
                   isinstance(row.get('supportedEffortLevels', []), list) and
                   effort not in row.get('supportedEffortLevels', []))]
    assert candidates, 'CLI did not provide a different model with incompatible Effort support'
    plan = {'baseline': controls(item), 'alternate': candidates[0]['value'], 'effort': effort,
            'reportedModelOptions': models, 'scope': 'existing_local_cli_only'}
    save('control-restore-plan.json', plan)
    print(json.dumps({'modelCount': len(models), 'baseline': plan['baseline'],
                      'alternate': plan['alternate'], 'effort': effort}, ensure_ascii=False))
elif args.action == 'alternate':
    plan = json.loads((out / 'control-restore-plan.json').read_text(encoding='utf-8'))
    before = session()
    request('/api/model', {'id': sid, 'model': plan['alternate']})
    changed = session()
    assert len(changed['messages']) == len(before['messages'])
    assert plan['effort'] not in {row['value'] for row in changed['connection'].get('availableEfforts', [])}
    save('control-restore-alternate.json', controls(changed))
    send('앱의 모델 재연결 검증입니다. 도구, 파일 접근, 외부 검색 없이 RESTORE_ALTERNATE_OK 한 줄만 답해 주세요.')
    print(json.dumps({'requested': 'alternate marker', 'controls': controls(changed)}, ensure_ascii=False))
elif args.action == 'reset-cancel':
    plan = json.loads((out / 'control-restore-plan.json').read_text(encoding='utf-8'))
    before = wait_for(lambda item: item['state'] in {'done', 'error', 'approval', 'question'})
    assert before['state'] == 'done', 'Alternate test turn did not finish normally'
    assert any('RESTORE_ALTERNATE_OK' in row.get('text', '') for row in before['messages'] if row.get('role') == 'assistant')
    request('/api/model', {'id': sid, 'model': None})
    request('/api/effort', {'id': sid, 'effort': plan['effort']})
    reset = session()
    assert reset['connection']['model'] == plan['baseline']['model']
    assert reset['connection']['modelOverride'] is None
    assert len(reset['messages']) == len(before['messages'])
    send('중지 기능을 검증하는 합성 요청입니다. 도구, 파일 접근, 외부 검색 없이 1부터 2000까지 숫자를 한 줄씩 출력해 주세요.')
    wait_for(lambda item: item['state'] in {'starting', 'running'})
    request('/api/stop', {'id': sid})
    wait_for(lambda item: item.get('connectionStopped') is True, 30)
    resumed = request('/api/connect', {'id': sid})
    actual = session()
    assert resumed['ok'] and actual['connection']['model'] == plan['baseline']['model']
    assert actual['connection']['effort'] == plan['effort']
    assert actual['connection']['permissionMode'] == plan['baseline']['permissionMode']
    assert not actual['connection'].get('controlRestore')
    users = [row for row in actual['messages'] if row.get('role') == 'user']
    assert len(users) == 2, 'Control changes or cancellation created an extra prompt'
    result = {'resetBeforeCancel': controls(reset), 'afterResume': controls(actual),
              'userMessages': len(users), 'resumedOriginalModel': True}
    save('control-restore-resume.json', result)
    print(json.dumps(result, ensure_ascii=False))
elif args.action == 'follow-up':
    send('중지 후 후속 전송 검증입니다. 도구, 파일 접근, 외부 검색 없이 RESTORE_FOLLOWUP_OK 한 줄만 답해 주세요.')
    print(json.dumps({'requested': 'follow-up marker'}))
elif args.action == 'status':
    item = session()
    result = {'state': item['state'], 'controls': controls(item),
              'userMessages': sum(row.get('role') == 'user' for row in item['messages']),
              'followupReceived': any('RESTORE_FOLLOWUP_OK' in row.get('text', '') for row in item['messages'] if row.get('role') == 'assistant'),
              'pendingTools': [row.get('tool') for row in item.get('requests', [])],
              'verification': item.get('verification')}
    save('control-restore-status.json', result)
    print(json.dumps(result, ensure_ascii=False))
