"""Bounded local startup diagnostics containing only fixed technical fields."""
from collections import deque
import json
import math
import os
from pathlib import Path
import re
import threading
import time
import uuid

from .history import safe, read

MODULES = frozenset(('app', 'stream', 'attachments', 'drafts', 'attachment-storage', 'archived-tasks', 'workflow', 'composer',
    'inline-controls', 'input-keys', 'chat-shortcuts', 'attention', 'desktop', 'session-import', 'capabilities',
    'productivity', 'palette', 'layout', 'rich-content', 'execution-view', 'tool-activity', 'progress-view', 'path-picker', 'startup-health', 'upgrade-handoff', 'app-updates'))
EVENTS = frozenset(('document', 'startup', 'module', 'resource', 'error', 'recovery'))
STATES = frozenset(('pending', 'ready', 'failed', 'restored'))
REASONS = frozenset(('', 'resource-load', 'resource-error', 'runtime-error',
    'unhandled-rejection', 'missing-global', 'missing-handler', 'bootstrap-failed',
    'timeout', 'storage-unavailable', 'reload-requested', 'restore-failed'))
NATIVE_EVENTS = frozenset(('ready', 'loaded', 'hidden', 'activated', 'close_requested',
    'error', 'external_link_failed', 'manual_link_failed', 'process_failed', 'navigation_failed', 'api_proxy_failed', 'exited'))
MAX_EVENTS = 240


def frontend_row(value):
    if not isinstance(value, dict):
        raise ValueError('화면 진단 형식을 확인해 주세요.')
    row = {}
    ident = value.get('documentId')
    if not isinstance(ident, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{8,64}', ident):
        raise ValueError('화면 진단 식별자를 확인해 주세요.')
    row['documentId'] = ident
    for key, allowed in (('event', EVENTS), ('module', MODULES), ('status', STATES), ('reason', REASONS)):
        val = value.get(key, '' if key == 'reason' else None)
        if not isinstance(val, str) or val not in allowed:
            raise ValueError('화면 진단 상태를 확인해 주세요.')
        row[key] = val
    for key, maximum in (('line', 1000000), ('column', 1000000), ('elapsedMs', 86400000)):
        val = value.get(key)
        if val is not None:
            if type(val) not in (int, float) or not 0 <= val <= maximum:
                raise ValueError('화면 진단 수치를 확인해 주세요.')
            row[key] = int(val)
    missing = value.get('missing', [])
    if not isinstance(missing, list) or len(missing) > len(MODULES):
        raise ValueError('화면 진단 목록을 확인해 주세요.')
    if any(not isinstance(name, str) or name not in MODULES for name in missing):
        raise ValueError('화면 진단 모듈을 확인해 주세요.')
    if missing:
        row['missing'] = sorted(set(missing))
    # Unknown keys, including URL, stack, message, token and file paths, are
    # deliberately not copied. Input is never used as a log format or filename.
    return row


class UiHealthLog:
    def __init__(self, state, version, *, clock=time.time, monotonic=time.monotonic):
        self.path = Path(state) / 'ui-diagnostics.json'
        self.version, self.clock, self.monotonic = version, clock, monotonic
        self.launch_id = uuid.uuid4().hex
        self.lock = threading.Lock()
        self.rows = deque(maxlen=MAX_EVENTS)
        self.writes = deque()
        self.native_context = {}
        try:
            previous = read(self.path, 256 * 1024)
            # Prior records are local diagnostics, but do not carry unknown data
            # forward from a malformed or externally modified file.
            previous_rows = previous.get('events', []) if isinstance(previous, dict) else []
            if not isinstance(previous_rows, list):
                previous_rows = []
            for row in previous_rows[-MAX_EVENTS:]:
                try:
                    if row.get('module') == 'native':
                        if row.get('event') not in NATIVE_EVENTS or row.get('status') not in STATES:
                            continue
                        clean = {key: row[key] for key in ('event', 'module', 'status')}
                    else:
                        clean = frontend_row(row)
                    if not re.fullmatch(r'[0-9a-f]{32}', row.get('launchId', '')):
                        continue
                    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', row.get('version', '')):
                        continue
                    if type(row.get('at')) not in (int, float) or not math.isfinite(row['at']):
                        continue
                    clean.update({key: row[key] for key in ('at', 'launchId', 'version')})
                    for key in ('serverPid', 'hostPid', 'hwnd', 'code'):
                        if type(row.get(key)) is int and 0 < row[key] < 2**63:
                            clean[key] = row[key]
                    if isinstance(row.get('runtime'), str) and re.fullmatch(r'[0-9][0-9A-Za-z. -]{0,79}', row['runtime']):
                        clean['runtime'] = row['runtime']
                    self.rows.append(clean)
                except (ValueError, TypeError, AttributeError, OverflowError, RecursionError):
                    continue
        except (OSError, ValueError, TypeError, AttributeError, OverflowError, RecursionError):
            pass

    def frontend(self, value):
        return self._append(frontend_row(value))

    def native(self, value):
        if (not isinstance(value, dict) or not isinstance(value.get('type'), str)
                or value['type'] not in NATIVE_EVENTS):
            return False
        row = {'event': value['type'], 'module': 'native', 'status':
               'failed' if value['type'] in {'error', 'external_link_failed', 'manual_link_failed',
                                           'process_failed', 'navigation_failed', 'api_proxy_failed'} else 'ready'}
        for key, source in (('hostPid', 'pid'), ('hwnd', 'hwnd'), ('code', 'code')):
            val = value.get(source)
            if type(val) is int and 0 < val < 2**63:
                row[key] = val
        runtime = value.get('runtime')
        if isinstance(runtime, str) and re.fullmatch(r'[0-9][0-9A-Za-z. -]{0,79}', runtime):
            row['runtime'] = runtime
        return self._append(row, native=True)

    def _append(self, row, native=False):
        with self.lock:
            if native:
                if row.get('hostPid') != self.native_context.get('hostPid'):
                    self.native_context.clear()
                self.native_context.update({key: row[key] for key in ('hostPid', 'hwnd', 'runtime') if key in row})
            row = {**self.native_context, **row, 'at': self.clock(), 'launchId': self.launch_id,
                   'version': self.version, 'serverPid': os.getpid()}
            if native and row['event'] == 'exited':
                self.native_context.clear()
            now = self.monotonic()
            while self.writes and now - self.writes[0] >= 60:
                self.writes.popleft()
            if len(self.writes) >= 300:
                return False
            self.writes.append(now)
            self.rows.append(row)
            temp = self.path.with_suffix('.tmp')
            try:
                safe(self.path); safe(temp)
                temp.write_text(json.dumps({'schema': 1, 'events': list(self.rows)}, ensure_ascii=True), encoding='utf-8')
                os.replace(temp, self.path)
                return True
            except (OSError, ValueError):
                # Diagnostics must never make an otherwise usable app fail.
                return False
