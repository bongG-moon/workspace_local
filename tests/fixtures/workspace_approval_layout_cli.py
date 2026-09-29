"""UI specimens only: records approval responses, never executes any tool.

Messages: permission / edit / question / suppressed. All profiles and records
are owned by serve-workspace-permission-fixture.py's isolated temporary root.
"""
import json
from pathlib import Path
import sys
import uuid

if '--version' in sys.argv:
    print('approval-layout-fixture 1.0')
    sys.exit(0)
if '--help' in sys.argv:
    print('--input-format --output-format --permission-prompt-tool\n'
          '  --permission-mode <mode>  Permission mode (choices: "default", "acceptEdits", "plan", "auto")')
    sys.exit(0)

session = str(uuid.uuid4())
turn = 0


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def specimen(name):
    command = 'python -c "print(\'승인 화면 검증: 실제 실행되지 않는 합성 명령\')"\n' + '\n'.join(
        '# 확인할 자료 %02d: 업무 결과 보고서와 관련 파일의 경로 및 설명' % n for n in range(1, 60))
    tool_input = {'command': command, 'description': '긴 실행 내용과 승인 버튼의 동시 표시 확인'}
    tool = 'Bash'
    if name == 'edit':
        tool = 'Edit'
        tool_input = {'file_path': str(Path.cwd() / '보고서.md'),
                      'old_string': '\n'.join('수정 전 내용 %s' % i for i in range(80)),
                      'new_string': '\n'.join('수정 후 내용 %s' % i for i in range(80))}
    if name == 'question':
        return {'subtype': 'can_use_tool', 'tool_name': 'AskUserQuestion', 'input': {'questions': [
            {'header': '확인 %s' % i, 'question': '보고서의 %s번째 항목을 선택해 주세요.' % i,
             'multiSelect': i == 2, 'options': [
                 {'label': '요약', 'description': '중요한 결과를 간결하게 정리합니다.'},
                 {'label': '상세', 'description': '근거와 세부 내용을 포함합니다.'}]} for i in range(1, 5)]}}
    request = {'subtype': 'can_use_tool', 'tool_name': tool, 'input': tool_input,
               'decision_reason': '이 연결의 승인 규칙에 따라 실행 전 확인이 필요합니다. (검증용 예시)',
               'matched_ask_rule': {'source': 'userSettings', 'tool_name': 'Bash', 'rule_content': 'python *'},
               'permission_suggestions': [
                   {'type': 'addRules', 'destination': destination, 'behavior': 'allow',
                    'rules': [{'toolName': tool, 'ruleContent': 'fixture-review-%s' % n} for n in range(8)]}
                   for destination in ('session', 'localSettings', 'projectSettings', 'userSettings')]}
    if name == 'suppressed':
        request['suppress_always_allow_rule'] = True
    return request


for line in sys.stdin:
    value = json.loads(line)
    with (Path.cwd() / 'approval-layout-wire.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + '\n')
    if value['type'] == 'control_request':
        emit({'type': 'control_response', 'response': {'subtype': 'success',
              'request_id': value['request_id'], 'response': {'current_permission_mode': 'default'}}})
    elif value['type'] == 'user':
        turn += 1
        emit({'type': 'system', 'subtype': 'init', 'session_id': session,
              'model': 'fixture-only', 'permissionMode': 'default'})
        frame = {'type': 'control_request', 'request_id': 'layout-%s' % turn,
                 'request': specimen(value['message']['content'])}
        emit(frame)
        emit(frame)  # Same pending ID must stay one visible card.
        emit({'type': 'system', 'subtype': 'status', 'status': None, 'permissionMode': 'default'})
    elif value['type'] == 'control_response':
        response = value['response']['response']
        emit({'type': 'result', 'session_id': session, 'is_error': False,
              'result': '검증 응답 수신: ' + response['behavior'] + '. 실제 도구와 개인 설정은 변경하지 않았습니다.'})
