"""Cooperative restart: drain work, preserve unsent drafts, then close normally.

This protocol never terminates an external process or writes Claude settings.
All state transitions are protected by the application's session lock. The
commit barrier takes that lock before the short-lived lifecycle condition.
"""
from __future__ import annotations

import json
import re
import time
import uuid

from .history import HistoryStore, MAX_SESSIONS, read, safe

MAX_SNAPSHOT = 256 * 1024
LEASE_SECONDS = 2 * 60 * 60
CAPTURE_SECONDS = 45
_NONCE = re.compile(r'[0-9a-f]{32}\Z')
_VERSION = re.compile(r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-[A-Za-z0-9.-]+)?\Z')


def nonce(value):
    if not isinstance(value, str) or not _NONCE.fullmatch(value):
        raise ValueError('버전 전환 요청을 확인하지 못했습니다.')
    return value


def version(value):
    if not isinstance(value, str) or len(value) > 80 or not _VERSION.fullmatch(value):
        raise ValueError('실행할 앱 버전을 확인하지 못했습니다.')
    return value


def snapshot(value):
    """Validate without silently filtering/truncating any unsent user input."""
    if not isinstance(value, dict) or set(value) not in ({'sessionId', 'drafts'}, {'sessionId', 'drafts', 'stashes'}):
        raise ValueError('작성 중인 내용의 저장 형식을 확인해 주세요.')
    def identifier(sid, home=False):
        if home and sid == 'home':
            return sid
        if not isinstance(sid, str) or str(uuid.UUID(sid)) != sid:
            raise ValueError('작성 중인 업무의 식별자를 확인하지 못했습니다.')
        return sid
    selected = value['sessionId']
    if selected is not None:
        identifier(selected)
    drafts = value['drafts']
    if not isinstance(drafts, list) or len(drafts) > MAX_SESSIONS + 1:
        raise ValueError('작성 중인 업무의 개수가 저장 범위를 넘었습니다.')
    def entries(rows, *, stashes=False):
        result, seen = [], set()
        keys = {'id', 'text', 'attachments'} | ({'selectionStart', 'selectionEnd'} if stashes else set())
        for row in rows:
            if not isinstance(row, dict) or set(row) != keys:
                raise ValueError('작성 중인 내용의 저장 형식을 확인해 주세요.')
            sid = identifier(row['id'], home=True)
            text, paths = row['text'], row['attachments']
            if sid in seen or not isinstance(text, str) or len(text) > 100000:
                raise ValueError('작성 중인 내용이 중복되거나 저장 범위를 넘었습니다.')
            if (not isinstance(paths, list) or len(paths) > 12 or any(
                    not isinstance(path, str) or not path or len(path) > 8192 or '\x00' in path for path in paths)):
                raise ValueError('작성 중인 첨부 자료의 저장 형식을 확인해 주세요.')
            seen.add(sid)
            entry = {'id': sid, 'text': text, 'attachments': list(paths)}
            if stashes:
                start, end = row['selectionStart'], row['selectionEnd']
                try:
                    units = len(text.encode('utf-16-le')) // 2
                except UnicodeEncodeError as exc:
                    raise ValueError('보관한 입력의 문자를 확인하지 못했습니다.') from exc
                if (type(start) is not int or type(end) is not int or not 0 <= start <= end <= units):
                    raise ValueError('보관한 입력의 선택 범위를 확인하지 못했습니다.')
                entry.update(selectionStart=start, selectionEnd=end)
            result.append(entry)
        return result
    clean = {'sessionId': selected, 'drafts': entries(drafts)}
    if 'stashes' in value:
        stashes = value['stashes']
        if not isinstance(stashes, list) or len(stashes) > 50:
            raise ValueError('보관한 입력의 개수가 저장 범위를 넘었습니다.')
        clean['stashes'] = entries(stashes, stashes=True)
    try:
        size = len(json.dumps(clean, ensure_ascii=False).encode('utf-8'))
    except UnicodeEncodeError as exc:
        raise ValueError('작성 중인 내용의 문자를 확인하지 못했습니다.') from exc
    if size > MAX_SNAPSHOT:
        raise ValueError('작성 중인 내용이 저장 범위를 넘었습니다. 내용을 보관한 뒤 다시 실행해 주세요.')
    return clean


class UpgradeHandoff:
    def __init__(self, app, current_version, *, clock=time.monotonic):
        self.app, self.version, self.clock = app, current_version, clock
        self.path = app.state / 'upgrade-drafts.json'
        self.pending = None
        self.restore = None
        self.warning = None
        try:
            if safe(self.path).exists():
                value = read(self.path, MAX_SNAPSHOT + 4096)
                if (not isinstance(value, dict) or value.get('schemaVersion') != 1
                        or value.get('status') not in {'captured', 'ready'}):
                    raise ValueError('Invalid handoff record')
                nonce(value.get('requestId'))
                version(value.get('targetVersion'))
                saved = snapshot(value.get('snapshot'))
                if value['status'] == 'ready' and value['targetVersion'] == self.version:
                    self.restore = {'requestId': value['requestId'], 'snapshot': saved}
                elif value['status'] == 'ready':
                    self.warning = ('작성 중이던 내용을 ' + value['targetVersion']
                                    + ' 버전에 보관했습니다. 해당 버전을 실행해 내용을 먼저 복원해 주세요.')
        except (OSError, ValueError, TypeError, AttributeError, RecursionError):
            self.warning = '이전 창의 작성 내용을 확인하지 못했습니다. 저장 기록은 보존했습니다.'

    @staticmethod
    def _stage(pending, stage, **values):
        if pending['stage'] != stage:
            pending['revision'] += 1
        pending.update(stage=stage, **values)

    def _write(self, pending, status):
        value = {'schemaVersion': 1, 'requestId': pending['requestId'],
                 'targetVersion': pending['targetVersion'], 'status': status,
                 'snapshot': pending['snapshot']}
        HistoryStore(self.path.parent)._write(self.path, json.dumps(value, ensure_ascii=False).encode('utf-8'))

    def busy(self):
        app = self.app
        if app.dialog_lock.locked() or app.reconnect_lock.locked():
            return True
        if app.dispatch.steering or app.dispatch.error:
            return True
        for item in app.sessions.values():
            if (item.get('state') in {'starting', 'running', 'approval', 'question'}
                    or any(item.get(key) for key in ('_connecting', '_modelUpdating', '_dispatchClaim', '_choiceAnswerClaim', '_removingFromList'))
                    or item.get('requests') or item.get('choice')
                    or (item.get('verification') or {}).get('state') in {'checking', 'needs-review'}):
                return True
            bridge = item.get('bridge')
            if bridge and (getattr(bridge, 'busy', False) or getattr(bridge, 'pending', None)
                           or getattr(bridge, 'stopping', False) and getattr(bridge, 'cleanup_complete', False) is not True):
                return True
            queue = app.dispatch.queue.snapshot(item['id'])
            if queue.get('warning') or any(row['status'] in {'dispatching', 'submitted'} for row in queue['queue']):
                return True
        return False

    def invalidate(self):
        """An admitted user action/work event wins over a prepared restart."""
        pending = self.pending
        if pending and pending['stage'] in {'capture', 'captured'}:
            self._stage(pending, 'waiting', snapshot=None, captureDeadline=None)

    def _refresh(self):
        pending = self.pending
        if not pending or pending['stage'] in {'committing', 'closed', 'failed', 'cancelled', 'expired'}:
            return
        now = self.clock()
        if now >= pending['deadline']:
            self._stage(pending, 'expired', snapshot=None)
            return
        with self.app._lifecycle:
            admitted = self.app._active_operations > 0
            closing = self.app._shutdown_state != 'running'
        if closing or self.busy():
            self.invalidate()
            return
        # A brief read-only heartbeat or empty dispatch scan must not discard
        # an acknowledged draft snapshot. Actual mutations invalidate it when
        # admitted, and commit still checks/drains all accepted operations.
        if admitted:
            return
        if pending['stage'] == 'waiting':
            self._stage(pending, 'capture', captureDeadline=now + CAPTURE_SECONDS)
        if pending['stage'] == 'capture' and self.app._upgrade_headless and self.app._desktop_window is None:
            pending['snapshot'] = {'sessionId': None, 'drafts': []}
            self._write(pending, 'captured')
            pending['captureRevision'] = pending['revision']
            self._stage(pending, 'captured')
        elif pending['stage'] == 'capture' and now >= pending['captureDeadline']:
            self._stage(pending, 'expired', snapshot=None)

    def status(self, request_id=None):
        with self.app.lock:
            self._refresh()
            pending = self.pending
            if request_id is not None and (not pending or nonce(request_id) != pending['requestId']):
                raise ValueError('이 버전 전환 요청은 더 이상 유효하지 않습니다.')
            public = None if not pending else {key: pending[key] for key in ('requestId', 'targetVersion', 'stage', 'revision')}
            if pending and pending.get('executionMode'):
                public['executionMode'] = pending['executionMode']
            if pending and pending.get('error'):
                public['error'] = pending['error']
            return {'upgrade': public, 'upgradeRestore': self.restore,
                    'state': 'ready' if pending and pending['stage'] == 'captured' else pending['stage'] if pending else None,
                    'warning': self.warning}

    def action(self, data):
        action, request_id = data.get('action'), nonce(data.get('requestId'))
        with self.app.lock:
            self._refresh()
            if action == 'restored':
                if self.restore and self.restore['requestId'] == request_id:
                    safe(self.path).unlink(missing_ok=True)
                    self.restore = None
                elif self.restore:
                    raise ValueError('복원할 작성 내용의 요청을 확인해 주세요.')
                return {'ok': True, **self.status()}
            if action == 'prepare':
                target = version(data.get('targetVersion'))
                mode = data.get('executionMode')
                if mode is not None:
                    from .execution_mode import checked_mode
                    checked_mode(mode)
                if self.warning or self.restore:
                    raise ValueError(self.warning or '이전 창의 작성 내용을 먼저 복원해 주세요.')
                if self.pending and self.pending['stage'] not in {'cancelled', 'expired', 'failed'}:
                    if self.pending['requestId'] != request_id or self.pending['targetVersion'] != target or self.pending.get('executionMode') != mode:
                        raise ValueError('다른 버전 전환을 준비하고 있습니다. 기존 요청을 먼저 마쳐 주세요.')
                else:
                    self.pending = {'requestId': request_id, 'targetVersion': target, 'stage': 'waiting', 'executionMode': mode,
                                    'revision': 1, 'deadline': self.clock() + LEASE_SECONDS, 'captureDeadline': None, 'snapshot': None}
                return {'ok': True, **self.status()}
            pending = self.pending
            if not pending or pending['requestId'] != request_id:
                raise ValueError('이 버전 전환 요청은 더 이상 유효하지 않습니다.')
            if action == 'cancel':
                if pending['stage'] in {'committing', 'closed'}:
                    raise ValueError('종료를 마무리하고 있어 버전 전환을 취소할 수 없습니다.')
                self._stage(pending, 'cancelled', snapshot=None)
                return {'ok': True, **self.status()}
            if action == 'capture':
                if pending['stage'] == 'captured':
                    # A replay may acknowledge only the exact captured draft.
                    # Another window's different draft must never receive a
                    # false ACK or be silently replaced before this app closes.
                    try:
                        repeated = snapshot(data.get('snapshot'))
                    except (ValueError, TypeError, AttributeError):
                        repeated = None
                    if (repeated != pending['snapshot']
                            or data.get('revision') != pending.get('captureRevision')):
                        self._stage(pending, 'cancelled', snapshot=None,
                                    error='다른 창의 작성 내용이 확인되어 자동 전환을 취소했습니다. 각 창의 내용을 보관해 주세요.')
                        return {'ok': False, 'closed': False, **self.status()}
                    return {'ok': True, **self.status()}
                if pending['stage'] != 'capture':
                    return {'ok': False, **self.status()}
                if data.get('revision') != pending['revision']:
                    return {'ok': False, **self.status()}
                clean = snapshot(data.get('snapshot'))
                pending['snapshot'] = clean
                try:
                    self._write(pending, 'captured')
                except (OSError, ValueError):
                    pending['snapshot'] = None
                    raise
                pending['captureRevision'] = pending['revision']
                self._stage(pending, 'captured')
                return {'ok': True, **self.status()}
            if action != 'commit':
                raise ValueError('버전 전환 동작을 확인해 주세요.')
            if data.get('targetVersion', pending['targetVersion']) != pending['targetVersion']:
                raise ValueError('실행할 앱 버전이 전환 요청과 다릅니다.')
            if data.get('executionMode', pending.get('executionMode')) != pending.get('executionMode'):
                raise ValueError('전환할 실행 권한이 기존 요청과 다릅니다.')
            if pending['stage'] == 'closed':
                return {'ok': True, 'closed': True, **self.status()}
            # Capture and work completion can race a second launch. Admission
            # remains open during prepare; the winner of this barrier owns it.
            with self.app._lifecycle:
                if (pending['stage'] != 'captured' or self.app._active_operations
                        or self.app._shutdown_state != 'running' or self.busy()):
                    self.invalidate()
                    return {'ok': False, 'closed': False, **self.status()}
                # Make the exact snapshot recoverable BEFORE closing admission
                # or stopping a process. A disk failure leaves the old app open.
                # If cleanup later fails this record is retained; a deliberate
                # retry/new app can still recover it without executing a prompt.
                try:
                    self._write(pending, 'ready')
                except (OSError, ValueError):
                    self._stage(pending, 'failed', error='작성 내용을 확정 저장하지 못했습니다. 기존 창에서 계속 사용할 수 있습니다.')
                    return {'ok': False, 'closed': False, **self.status()}
                self.app._shutdown_state = 'closing'
                self._stage(pending, 'committing')
        # Reader callbacks need app.lock while normal CLI cleanup is draining.
        try:
            closed = self.app.close() is True
        except Exception:
            closed = False
        with self.app.lock:
            if closed:
                self._stage(pending, 'closed')
                return {'ok': True, 'closed': True, **self.status()}
            self._stage(pending, 'failed', error='기존 연결의 종료를 확인하지 못했습니다. 작업은 강제 종료하지 않았습니다.')
            return {'ok': False, 'closed': False, **self.status()}
