"""Bounded, display-only evidence of shell calls observed on the CLI wire."""
from collections import deque
import math
import re
import threading
import time

MAX_RECORDS = 40
MAX_TOTAL = 300000
MAX_OUTPUT = 12000
MAX_COMMAND = 8000
MAX_DESCRIPTION = 240
STATES = {'requested', 'completed', 'error', 'interrupted'}
TOOLS = {'Bash', 'PowerShell'}
_ID = re.compile(r'[A-Za-z0-9_-]{1,160}\Z')
_MEDIA = re.compile(r'data:[^\s,;]{1,100};base64,[A-Za-z0-9+/=\r\n]*', re.I)
_IMAGE = re.compile(r'(?:iVBORw0KGgo|/9j/|R0lGOD[lh]|UklGR)[A-Za-z0-9+/=]{100,}')


def safe_run_id(value):
    return value if isinstance(value, str) and len(value) <= 64 and _ID.fullmatch(value) else None


def _identifier(value):
    return value if isinstance(value, str) and _ID.fullmatch(value) else None


def _text(value, maximum):
    if not isinstance(value, str):
        return '', False
    truncated = len(value) > maximum
    value = value[:maximum]
    # No structured image blocks are projected; redact inline image payloads too.
    value, media = _MEDIA.subn('[image data omitted]', value)
    value, image = _IMAGE.subn('[image data omitted]', value)
    value = value.encode('utf-8', errors='replace').decode('utf-8')
    value = ''.join(c for c in value if c in '\n\r\t' or ord(c) >= 32 and ord(c) != 127)
    return value[:maximum], truncated or bool(media or image)


def _stamp(value):
    return type(value) in (int, float) and 0 <= value <= 1e12 and math.isfinite(value)


def normalize_execution(value, *, run_id=None):
    if (not isinstance(value, dict) or not _identifier(value.get('id'))
            or not isinstance(value.get('tool'), str) or value['tool'] not in TOOLS
            or not isinstance(value.get('state'), str) or value['state'] not in STATES
            or not isinstance(value.get('command'), str) or not _stamp(value.get('startedAt'))):
        return None
    command, command_cut = _text(value['command'], MAX_COMMAND)
    description, description_cut = _text(value.get('description'), MAX_DESCRIPTION)
    output, output_cut = _text(value.get('output'), MAX_OUTPUT)
    result = {'id': value['id'], 'tool': value['tool'], 'command': command,
              'description': description, 'state': value['state'], 'output': output,
              'truncated': value.get('truncated') is True or command_cut or description_cut or output_cut,
              'startedAt': value['startedAt']}
    if value['state'] != 'requested' and _stamp(value.get('finishedAt')):
        result['finishedAt'] = max(value['startedAt'], value['finishedAt'])
    selected_run = safe_run_id(value.get('runId') if run_id is None else run_id)
    if selected_run is not None:
        result['runId'] = selected_run
    return result


def _key(value):
    return value.get('runId'), value['id']


def _bounded(records):
    records = records[-MAX_RECORDS:]
    total = sum(len(value) for row in records for value in row.values() if isinstance(value, str))
    while records and total > MAX_TOTAL:
        total -= sum(len(value) for value in records.pop(0).values() if isinstance(value, str))
    return records


def normalize_executions(values, *, interrupted=False):
    if not isinstance(values, list):
        return []
    records = {}
    for value in values[-MAX_RECORDS:]:
        clean = normalize_execution(value)
        if clean is None:
            continue
        key = _key(clean)
        old = records.get(key)
        if old is None or old['state'] == 'requested':
            records[key] = clean
    result = _bounded(list(records.values()))
    if interrupted:
        for value in result:
            if value['state'] == 'requested':
                value['state'] = 'interrupted'
                # No finish timestamp is invented for a disconnected history.
                value.pop('finishedAt', None)
    return result


def merge_execution(records, value, *, run_id=None):
    result = normalize_executions(records)
    clean = normalize_execution(value, run_id=run_id)
    if clean is None:
        return result
    for index, old in enumerate(result):
        if _key(old) == _key(clean):
            if old['state'] == 'requested':
                result[index] = {**clean, 'command': old['command'], 'description': old['description'],
                                 'startedAt': old['startedAt'], 'truncated': old['truncated'] or clean['truncated']}
            return _bounded(result)
    return _bounded([*result, clean])


def result_text(content):
    """Project only text blocks, never stringify image/resource/binary objects."""
    if isinstance(content, str):
        return _text(content, MAX_OUTPUT)
    if not isinstance(content, list):
        return '', content is not None
    chunks, remaining, truncated = [], MAX_OUTPUT, False
    for block in content:
        if not isinstance(block, dict) or block.get('type') != 'text' or not isinstance(block.get('text'), str):
            truncated = True
            continue
        separator = '\n' if chunks else ''
        if remaining <= len(separator):
            truncated = True
            continue
        text, cut = _text(block['text'], remaining - len(separator))
        chunks.append(separator + text)
        remaining -= len(separator) + len(text)
        truncated = truncated or cut
    return ''.join(chunks), truncated


class ExecutionCapture:
    """Observe protocol evidence without executing commands or answering requests."""
    def __init__(self, emit, *, clock=time.time):
        self.emit, self.clock = emit, clock
        self.lock = threading.RLock()
        self.records = []
        self.seen = set()
        self.order = deque()
        self.closed = False

    def request(self, block):
        if not isinstance(block, dict) or not isinstance(block.get('name'), str) or block['name'] not in TOOLS:
            return
        inputs = block.get('input')
        if not isinstance(inputs, dict):
            return
        value = normalize_execution({'id': block.get('id'), 'tool': block['name'],
            'command': inputs.get('command'), 'description': inputs.get('description'),
            'state': 'requested', 'output': '', 'startedAt': self.clock()})
        if value is None:
            return
        with self.lock:
            if self.closed or value['id'] in self.seen:
                return
            self.seen.add(value['id'])
            self.order.append(value['id'])
            if len(self.order) > 4096:
                self.seen.discard(self.order.popleft())
            self.records = merge_execution(self.records, value)
        self.emit(dict(value))

    def result(self, block):
        identifier = _identifier(block.get('tool_use_id')) if isinstance(block, dict) else None
        if identifier is None or type(block.get('is_error', False)) is not bool:
            return
        with self.lock:
            old = next((row for row in self.records if row['id'] == identifier and row['state'] == 'requested'), None)
            if old is None:
                return
            output, truncated = result_text(block.get('content'))
            value = {**old, 'state': 'error' if block.get('is_error') is True else 'completed',
                     'output': output, 'truncated': old['truncated'] or truncated, 'finishedAt': self.clock()}
            self.records = merge_execution(self.records, value)
        self.emit(dict(value))

    def interrupt(self, *, closed=False):
        with self.lock:
            self.closed = self.closed or closed
            values = [{**row, 'state': 'interrupted', 'finishedAt': self.clock()}
                      for row in self.records if row['state'] == 'requested']
            for value in values:
                self.records = merge_execution(self.records, value)
        for value in values:
            self.emit(dict(value))
