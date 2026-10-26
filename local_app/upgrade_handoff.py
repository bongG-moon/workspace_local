"""Cooperative restart: preserve drafts, then wait or explicitly stop owned work.

This protocol never terminates an unrelated process or writes Claude settings.
All state transitions are protected by the application's session lock. The
commit barrier takes that lock before the short-lived lifecycle condition.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid

from .history import HistoryStore, MAX_SESSIONS, read, safe

MAX_SNAPSHOT = 256 * 1024
LEASE_SECONDS = 2 * 60 * 60
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
    if (not isinstance(value, dict) or not {'sessionId', 'drafts'}.issubset(value)
            or set(value) - {'sessionId', 'drafts', 'stashes', 'mode'}):
        raise ValueError('작성 중인 내용의 저장 형식을 확인해 주세요.')
    def identifier(sid, home=False):
        if home and sid in {'home', 'home:chat', 'home:work'}:
            return sid
        if not isinstance(sid, str) or str(uuid.UUID(sid)) != sid:
            raise ValueError('작성 중인 업무의 식별자를 확인하지 못했습니다.')
        return sid
    selected = value['sessionId']
    if selected is not None:
        identifier(selected)
    drafts = value['drafts']
    if not isinstance(drafts, list) or len(drafts) > MAX_SESSIONS + 3:
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
    if 'mode' in value:
        if value['mode'] not in ('chat', 'work'):
            raise ValueError('복원할 화면 모드를 확인해 주세요.')
        clean['mode'] = value['mode']
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


def _restore_record(value, current_version):
    if (not isinstance(value, dict) or value.get('schemaVersion') != 1
            or value.get('status') not in {'captured', 'ready'}):
        raise ValueError('Invalid handoff record')
    request_id = nonce(value.get('requestId'))
    target = version(value.get('targetVersion'))
    saved = snapshot(value.get('snapshot'))
    if value['status'] != 'ready':
        return None
    restored = {'requestId': request_id, 'snapshot': saved, 'savedTargetVersion': target}
    try:
        source = value.get('sourceVersion')
        source_parts = tuple(map(int, version(source).split('-')[0].split('.')))
        target_parts = tuple(map(int, version(current_version).split('-')[0].split('.')))
        if target == current_version and source_parts < target_parts:
            restored.update(sourceVersion=source, targetVersion=current_version)
    except (ValueError, TypeError):
        pass
    return restored


def preserve_recovery(state, restore):
    """Durably retain one exact previous snapshot before permitting replacement.

    Shared with the new coordinator when an old server requires a restored ACK.
    Never merges text/attachments or overwrites another revision of the same ID.
    Archives are loaded one at a time and never increase the capture size limit.
    """
    if not isinstance(restore, dict):
        raise ValueError('보관할 이전 초안을 확인하지 못했습니다.')
    rid = nonce(restore.get('requestId'))
    target = version(restore.get('savedTargetVersion'))
    clean = snapshot(restore.get('snapshot'))
    value = {'schemaVersion': 1, 'status': 'ready', 'requestId': rid,
             'targetVersion': target, 'snapshot': clean}
    path = safe(state / 'upgrade-recovery' / (rid + '.json'))
    def matches():
        retained = _restore_record(read(path, MAX_SNAPSHOT + 4096), target)
        return (retained is not None and retained['requestId'] == rid
                and retained['savedTargetVersion'] == target and retained['snapshot'] == clean)
    if path.exists():
        if not matches():
            raise ValueError('같은 식별자의 이전 초안이 달라 원본을 유지했습니다.')
        return path
    HistoryStore(state)._write(path, json.dumps(value, ensure_ascii=False).encode('utf-8'))
    if not matches():
        raise ValueError('이전 초안의 별도 보관을 확인하지 못했습니다.')
    return path


class UpgradeHandoff:
    def __init__(self, app, current_version, *, clock=time.monotonic):
        self.app, self.version, self.clock = app, current_version, clock
        self.path = app.state / 'upgrade-drafts.json'
        self.pending = None
        self.restore = None
        self.warning = None
        self._restore_path = None
        self._write_blocked = False
        self._load_restore()

    def _load_restore(self):
        self.restore = self._restore_path = None
        self.warning = None
        self._write_blocked = False
        def load(path, *, retained=False):
            restored = _restore_record(read(path, MAX_SNAPSHOT + 4096), self.version)
            if retained and (restored is None or path.stem != restored['requestId']):
                raise ValueError('Invalid retained draft record')
            if restored:
                if retained:
                    restored['retained'] = True
                self.restore, self._restore_path = restored, path
                return True
            return False
        try:
            if safe(self.path).exists() and load(self.path):
                return
        except (OSError, ValueError, TypeError, AttributeError, RecursionError):
            # An unreadable live record cannot safely be replaced. An unreadable
            # archive is separate and can stay on disk without blocking updates.
            self._write_blocked = True
            self.warning = '이전 창의 작성 내용을 확인하지 못했습니다. 저장 기록은 보존했습니다.'
        try:
            directory = safe(self.app.state / 'upgrade-recovery')
            if directory.exists():
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if not re.fullmatch(r'[0-9a-f]{32}\.json', entry.name):
                            continue
                        try:
                            if load(safe(directory / entry.name), retained=True):
                                return
                        except (OSError, ValueError, TypeError, AttributeError, RecursionError):
                            self.warning = '일부 이전 초안을 확인하지 못했습니다. 별도 보관 기록은 유지됩니다.'
        except (OSError, ValueError):
            self.warning = '이전 초안 보관 위치를 확인하지 못했습니다. 저장 기록은 유지됩니다.'

    def _checked_restore(self, path):
        stored = _restore_record(read(safe(path), MAX_SNAPSHOT + 4096), self.version)
        if (stored is None or stored['requestId'] != self.restore['requestId']
                or stored['savedTargetVersion'] != self.restore['savedTargetVersion']
                or stored['snapshot'] != self.restore['snapshot']):
            raise ValueError('보관된 초안 기록이 바뀌었습니다. 앱을 다시 열어 최신 기록을 확인해 주세요.')

    def _retain_previous(self):
        if self.restore and self._restore_path == self.path:
            self._checked_restore(self.path)
            archive = preserve_recovery(self.app.state, self.restore)
            # Recheck after the durable copy; do not remove a replaced record.
            self._checked_restore(self.path)
            safe(self.path).unlink()
            self._restore_path = archive
            self.restore['retained'] = True

    @staticmethod
    def _stage(pending, stage, **values):
        if pending['stage'] != stage:
            pending['revision'] += 1
        pending.update(stage=stage, **values)

    def _write(self, pending, status):
        value = {'schemaVersion': 1, 'requestId': pending['requestId'],
                 'sourceVersion': self.version, 'targetVersion': pending['targetVersion'], 'status': status,
                 'snapshot': pending['snapshot']}
        HistoryStore(self.path.parent)._write(self.path, json.dumps(value, ensure_ascii=False).encode('utf-8'))

    def busy(self):
        app = self.app
        if app.dialog_lock.locked() or app.reconnect_lock.locked():
            return True
        if app.dispatch.steering or app.dispatch.error:
            return True
        for item in app.sessions.values():
            if (item.get('state') in {'starting', 'running', 'stopping', 'approval', 'question'}
                    or any(item.get(key) for key in ('_connecting', '_modelUpdating', '_permissionUpdating', '_restarting',
                                                     '_stopAdmission', '_dispatchClaim', '_choiceAnswerClaim', '_removingFromList'))
                    or item.get('requests') or item.get('choice')
                    or (item.get('verification') or {}).get('state') == 'checking'):
                return True
            bridge = item.get('bridge')
            if bridge and (getattr(bridge, 'busy', False) or getattr(bridge, 'pending', None)
                           or getattr(bridge, 'stopping', False) and app.stop_state(item) == 'stopping'):
                return True
            # A completed but failed stop is not running work. Let normal
            # close retry its retained owned handles after drafts are saved;
            # commit still fails unless process/descendant cleanup succeeds.
            queue = app.dispatch.queue.snapshot(item['id'])
            if queue.get('warning') or any(row['status'] in {'dispatching', 'submitted'} for row in queue['queue']):
                return True
        return False

    def blocks_work(self):
        """An explicit stop choice reserves the captured snapshot until commit/cancel."""
        pending = self.pending
        return bool(pending and pending.get('stopRequested')
                    and pending['stage'] in {'captured', 'committing', 'closed'})

    def invalidate(self):
        """An admitted user action/work event wins over a prepared restart."""
        pending = self.pending
        if pending and not pending.get('stopRequested') and pending['stage'] in {'capture', 'captured'}:
            self._stage(pending, 'waiting', snapshot=None)

    def _refresh(self):
        pending = self.pending
        if not pending or pending['stage'] in {'committing', 'closed', 'failed', 'cancelled', 'expired'}:
            return
        now = self.clock()
        if now >= pending['deadline']:
            self._stage(pending, 'expired', snapshot=None)
            return
        # The explicit stop action already persisted its snapshot. Running
        # work is expected here; commit closes admission and drains it safely.
        if self.blocks_work():
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
            # The frontend may still own a settings/preview dialog or file
            # selection. Keep the same bounded handoff lease while it waits;
            # a short second deadline must not expire otherwise healthy work.
            # Nothing closes until the exact screen snapshot is acknowledged.
            self._stage(pending, 'capture')
        if pending['stage'] == 'capture' and self.app._upgrade_headless and self.app._desktop_window is None:
            pending['snapshot'] = {'sessionId': None, 'drafts': []}
            self._write(pending, 'captured')
            pending['captureRevision'] = pending['revision']
            self._stage(pending, 'captured')

    def status(self, request_id=None):
        with self.app.lock:
            self._refresh()
            pending = self.pending
            if request_id is not None and (not pending or nonce(request_id) != pending['requestId']):
                raise ValueError('이 버전 전환 요청은 더 이상 유효하지 않습니다.')
            public = None if not pending else {key: pending[key] for key in ('requestId', 'targetVersion', 'stage', 'revision')}
            if pending:
                public['sourceVersion'] = self.version
                public['canStop'] = pending['stage'] in {'waiting', 'capture'}
                public['stopRequested'] = pending.get('stopRequested') is True
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
                    self._checked_restore(self._restore_path)
                    duplicate = safe(self.app.state / 'upgrade-recovery' / (request_id + '.json'))
                    if self._restore_path == self.path and duplicate.exists():
                        # A coordinator may have copied before losing the old
                        # server's ACK. Consume that identical copy only.
                        self._checked_restore(duplicate)
                        duplicate.unlink()
                    safe(self._restore_path).unlink()
                    self._load_restore()
                elif self.restore:
                    raise ValueError('복원할 작성 내용의 요청을 확인해 주세요.')
                return {'ok': True, **self.status()}
            if action == 'prepare':
                target = version(data.get('targetVersion'))
                mode = data.get('executionMode')
                if mode is not None:
                    from .execution_mode import checked_mode
                    checked_mode(mode)
                    if mode != self.app.execution_mode:
                        raise ValueError('다른 실행 권한으로 변경하려면 앱을 완전 종료한 뒤 Windows에서 다시 실행해 주세요.')
                if self._write_blocked:
                    raise ValueError(self.warning)
                if self.pending and self.pending['stage'] not in {'cancelled', 'expired', 'failed'}:
                    if self.pending['requestId'] != request_id or self.pending['targetVersion'] != target or self.pending.get('executionMode') != mode:
                        raise ValueError('다른 버전 전환을 준비하고 있습니다. 기존 요청을 먼저 마쳐 주세요.')
                else:
                    self._retain_previous()
                    self.pending = {'requestId': request_id, 'targetVersion': target, 'stage': 'waiting', 'executionMode': mode,
                                    'revision': 1, 'deadline': self.clock() + LEASE_SECONDS, 'snapshot': None}
                return {'ok': True, **self.status()}
            pending = self.pending
            if not pending or pending['requestId'] != request_id:
                raise ValueError('이 버전 전환 요청은 더 이상 유효하지 않습니다.')
            if action == 'cancel':
                if pending['stage'] in {'committing', 'closed'}:
                    raise ValueError('종료를 마무리하고 있어 버전 전환을 취소할 수 없습니다.')
                self._stage(pending, 'cancelled', snapshot=None)
                return {'ok': True, **self.status()}
            if action == 'stop':
                if data.get('confirmed') is not True:
                    raise ValueError('작업 중지 후 업데이트를 먼저 선택해 주세요.')
                clean = snapshot(data.get('snapshot'))
                if pending.get('stopRequested') and pending['stage'] == 'captured':
                    if clean != pending['snapshot'] or data.get('revision') != pending.get('captureRevision'):
                        raise ValueError('보관할 작성 내용이 바뀌었습니다. 전환을 취소하고 다시 선택해 주세요.')
                    return {'ok': True, **self.status()}
                if pending['stage'] not in {'waiting', 'capture'} or data.get('revision') != pending['revision']:
                    return {'ok': False, 'closed': False, **self.status()}
                # No child is stopped and no admission is closed until this
                # exact screen snapshot is durably saved and acknowledged.
                pending['snapshot'] = clean
                try:
                    self._write(pending, 'captured')
                except (OSError, ValueError):
                    pending['snapshot'] = None
                    raise
                pending['captureRevision'] = pending['revision']
                self._stage(pending, 'captured', stopRequested=True)
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
                stop_requested = pending.get('stopRequested') is True
                if (pending['stage'] != 'captured' or self.app._shutdown_state != 'running'
                        or not stop_requested and (self.app._active_operations or self.busy())):
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
            closed = (self.app.close(force=True) if stop_requested else self.app.close()) is True
        except Exception:
            closed = False
        with self.app.lock:
            if closed:
                self._stage(pending, 'closed')
                return {'ok': True, 'closed': True, **self.status()}
            self._stage(pending, 'failed', error='기존 연결의 종료를 확인하지 못해 업데이트를 진행하지 않았습니다. 보관된 초안은 유지됩니다. 앱 종료 상태를 확인해 주세요.')
            return {'ok': False, 'closed': False, **self.status()}
