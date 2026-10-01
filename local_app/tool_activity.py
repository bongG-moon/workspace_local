"""Bounded display-only tool evidence; never retain raw inputs or tool outputs."""
from collections import deque
import math
import re
import threading
import time

from .executions import safe_run_id

MAX_RECORDS = 80
MAX_TOTAL = 48000
MAX_TARGET = 160
ACTIVE = {'requested', 'running'}
STATES = ACTIVE | {'completed', 'error', 'interrupted'}
_ID = re.compile(r'[A-Za-z0-9_-]{1,160}\Z')
_NAME = re.compile(r'[A-Za-z][A-Za-z0-9_.:/-]{0,159}\Z')
_SECRET = re.compile(r'(?:bearer\s+|(?:api[_ -]?key|token|secret|password|authorization)\s*[:=]|\b(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]{8,}|data:[^\s,]*;base64,)', re.I)
_URL = re.compile(r'(?:https?|file)://\S+', re.I)
_ACTIONS = {
    'Skill': '스킬 사용', 'Read': '자료 읽기', 'Write': '파일 작성',
    'Edit': '파일 수정', 'MultiEdit': '파일 수정', 'NotebookEdit': '노트북 수정',
    'Glob': '파일 찾기', 'Grep': '내용 검색', 'Bash': '명령 실행',
    'PowerShell': '명령 실행', 'Agent': '작업 위임', 'Task': '작업 위임',
    'AskUserQuestion': '답변 요청', 'WebSearch': '웹 검색', 'WebFetch': '웹 자료 읽기',
    'EnterPlanMode': '계획 모드 전환', 'ExitPlanMode': '계획 확인',
    'EnterWorktree': '작업 공간 준비', 'ExitWorktree': '작업 공간 정리',
    'TaskCreate': '할 일 추가', 'TaskUpdate': '할 일 갱신', 'TaskList': '할 일 확인',
    'TaskGet': '할 일 확인', 'TaskStop': '작업 중지', 'TaskOutput': '작업 결과 확인',
    'TodoWrite': '진행 계획 갱신', 'CronCreate': '예약 추가', 'CronDelete': '예약 삭제',
    'CronList': '예약 확인', 'ListMcpResourcesTool': '연결 자료 확인',
    'ReadMcpResourceTool': '연결 자료 읽기',
}


def _identifier(value):
    return value if isinstance(value, str) and _ID.fullmatch(value) else None


def _stamp(value):
    return type(value) in (int, float) and 0 <= value <= 1e12 and math.isfinite(value)


def action_for(tool):
    return _ACTIONS.get(tool, '연결 도구 호출' if tool.startswith('mcp__') else '도구 호출')


def _text(value):
    if not isinstance(value, str) or _SECRET.search(value[:4096]):
        return ''
    value = _URL.sub('[주소]', value[:4096])
    value = ''.join(c for c in value if ord(c) >= 32 and ord(c) != 127)
    return ' '.join(value.encode('utf-8', errors='replace').decode('utf-8').split())[:MAX_TARGET]


def target_for(tool, inputs):
    """Only explicit small display fields: never commands, contents or prompts."""
    if tool == 'Skill':
        skill = inputs.get('skill')
        return skill if isinstance(skill, str) and _NAME.fullmatch(skill) else ''
    if tool in {'Read', 'Write', 'Edit', 'MultiEdit', 'NotebookEdit', 'Glob', 'Grep'}:
        path = inputs.get('file_path') or inputs.get('notebook_path') or inputs.get('path')
        if isinstance(path, str) and not _URL.search(path):
            return _text(path.replace('\\', '/').rstrip('/').rsplit('/', 1)[-1])
    if tool in {'Bash', 'PowerShell', 'Agent', 'Task'}:
        return _text(inputs.get('description'))
    return ''


def normalize_activity(value, *, run_id=None):
    if (not isinstance(value, dict) or not _identifier(value.get('id'))
            or not isinstance(value.get('tool'), str) or not _NAME.fullmatch(value['tool'])
            or not isinstance(value.get('state'), str) or value['state'] not in STATES
            or not _stamp(value.get('startedAt'))):
        return None
    result = {'id': value['id'], 'tool': value['tool'], 'state': value['state'],
              'action': action_for(value['tool']), 'target': _text(value.get('target')),
              'startedAt': value['startedAt']}
    if value['state'] not in ACTIVE and _stamp(value.get('finishedAt')):
        result['finishedAt'] = max(value['startedAt'], value['finishedAt'])
    parent = value.get('parentToolUseId')
    if parent is not None:
        if not _identifier(parent) or parent == value['id']:
            return None
        result['parentToolUseId'] = parent
    selected = safe_run_id(value.get('runId') if run_id is None else run_id)
    if selected is not None:
        result['runId'] = selected
    return result


def _key(value):
    return value.get('runId'), value['id']


def _bounded(records):
    records = records[-MAX_RECORDS:]
    total = sum(len(value) for row in records for value in row.values() if isinstance(value, str))
    while records and total > MAX_TOTAL:
        total -= sum(len(value) for value in records.pop(0).values() if isinstance(value, str))
    return records


def normalize_activities(values, *, interrupted=False):
    records = {}
    for value in values[-MAX_RECORDS:] if isinstance(values, list) else []:
        clean = normalize_activity(value)
        if clean is None:
            continue
        key = _key(clean)
        old = records.get(key)
        if old is None:
            records[key] = clean
        elif (old['tool'] == clean['tool'] and old.get('parentToolUseId') == clean.get('parentToolUseId')
              and old['state'] in ACTIVE and not (old['state'] == 'running' and clean['state'] == 'requested')):
            records[key] = {**clean, 'tool': old['tool'], 'action': old['action'],
                            'target': old['target'], 'startedAt': old['startedAt']}
            if old.get('parentToolUseId'):
                records[key]['parentToolUseId'] = old['parentToolUseId']
    result = _bounded(list(records.values()))
    if interrupted:
        for value in result:
            if value['state'] in ACTIVE:
                value['state'] = 'interrupted'
                value.pop('finishedAt', None)
    return result


def merge_activity(records, value, *, run_id=None):
    clean = normalize_activity(value, run_id=run_id)
    current = normalize_activities(records)
    if clean is None:
        return current
    # Normalize before limiting so an update to the oldest retained row cannot
    # evict that row and lose its original identity/terminal state.
    for index, old in enumerate(current):
        if _key(old) == _key(clean):
            current[index] = normalize_activities([old, clean])[0]
            return _bounded(current)
    return _bounded([*current, clean])


class ToolActivityCapture:
    """One turn of correlated evidence. A result is never inferred from text."""
    def __init__(self, emit, *, clock=time.time, run_id=None, tombstones=None):
        self.emit, self.clock, self.run_id = emit, clock, safe_run_id(run_id)
        self.lock = threading.RLock()
        self.records = []
        self.turn_ids = set()
        self.turn_order = deque()
        self.seen, self.order = tombstones if tombstones is not None else (set(), deque())
        self.closed = False

    def request(self, block, *, parent=None):
        if not isinstance(block, dict) or not isinstance(block.get('input'), dict):
            return
        tool = block.get('name')
        if not isinstance(tool, str) or not _NAME.fullmatch(tool):
            return
        value = normalize_activity({'id': block.get('id'), 'tool': tool,
            'target': target_for(tool, block['input']), 'state': 'requested',
            'startedAt': self.clock(), 'parentToolUseId': parent, 'runId': self.run_id})
        if value is None:
            return
        with self.lock:
            if self.closed or value['id'] in self.seen:
                return
            # An orphan child can be a late frame from a preceding turn.
            if parent and parent not in self.turn_ids:
                return
            self.seen.add(value['id'])
            self.order.append(value['id'])
            self.turn_ids.add(value['id'])
            self.turn_order.append(value['id'])
            if len(self.turn_order) > 4096:
                self.turn_ids.discard(self.turn_order.popleft())
            if len(self.order) > 4096:
                self.seen.discard(self.order.popleft())
            self.records = merge_activity(self.records, value)
        self.emit(dict(value))

    def _matching(self, identifier, parent):
        return next((row for row in self.records if row['id'] == identifier
                     and row.get('parentToolUseId') == parent and row['state'] in ACTIVE), None)

    def progress(self, data):
        identifier = _identifier(data.get('tool_use_id')) if isinstance(data, dict) else None
        if identifier is None or not _stamp(data.get('elapsed_time_seconds')):
            return
        with self.lock:
            old = self._matching(identifier, data.get('parent_tool_use_id'))
            if self.closed or old is None or old['state'] == 'running' or data.get('tool_name') != old['tool']:
                return
            value = {**old, 'state': 'running'}
            self.records = merge_activity(self.records, value)
        self.emit(dict(value))

    def result(self, block, *, parent=None):
        identifier = _identifier(block.get('tool_use_id')) if isinstance(block, dict) else None
        if identifier is None or type(block.get('is_error', False)) is not bool:
            return
        with self.lock:
            old = self._matching(identifier, parent)
            if self.closed or old is None:
                return
            value = {**old, 'state': 'error' if block.get('is_error') else 'completed', 'finishedAt': self.clock()}
            self.records = merge_activity(self.records, value)
        self.emit(dict(value))

    def interrupt(self, *, closed=True):
        with self.lock:
            self.closed = self.closed or closed
            values = [{**row, 'state': 'interrupted', 'finishedAt': self.clock()}
                      for row in self.records if row['state'] in ACTIVE]
            for value in values:
                self.records = merge_activity(self.records, value)
        for value in values:
            self.emit(dict(value))
