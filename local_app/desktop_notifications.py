"""App-owned notification preferences and a small durable activity inbox.

Only task titles, sanitized request summaries and fixed status messages leave the app. Native delivery is a
request, never proof that Windows displayed a banner. No Windows/Claude setting
is read or changed here. The server supplies foreground and navigation hooks.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import threading
import time
import unicodedata
import uuid

from .history import HistoryStore, read, safe
from .attention import DEFAULT_REQUEST_SUMMARY, clean_summary


DEFAULTS = {'enabled': True, 'completed': True, 'attention': True, 'errors': True}
MESSAGES = {
    'completed': '작업이 완료됐어요. 클릭하면 결과를 볼 수 있어요.',
    'attention': '승인 또는 답변이 필요해요. 클릭하면 해당 업무를 열어요.',
    'error': '작업 상태를 확인해 주세요. 클릭하면 해당 업무를 열어요.',
}
DELIVERY = {'disabled', 'foreground', 'cooldown', 'queued', 'unavailable', 'requested'}
MAX_INBOX, MAX_SEEN = 50, 256
MAX_DEFER_SECONDS = 60.0


def _title(value):
    if not isinstance(value, str):
        return '새 업무'
    value = ''.join(' ' if unicodedata.category(char) in {'Cc', 'Cf', 'Cs'} else char for char in value)
    return ' '.join(value.split())[:100] or '새 업무'


def _identifier(value, length=64):
    return (isinstance(value, str) and len(value) == length
            and all(char in '0123456789abcdef' for char in value))


def notification_id(session_id, kind, event_id):
    """Stable public receipt identity without exposing the original event key."""
    if (not isinstance(session_id, str) or str(uuid.UUID(session_id)) != session_id
            or not isinstance(kind, str) or kind not in MESSAGES
            or not isinstance(event_id, str) or not event_id or len(event_id) > 512):
        raise ValueError('알림의 업무와 이벤트 식별자를 확인해 주세요.')
    return hashlib.sha256(json.dumps([session_id, kind, event_id], ensure_ascii=True).encode('ascii')).hexdigest()


class DesktopNotifications:
    def __init__(self, state, *, notify=None, is_foreground=None, on_open=None,
                 clock=time.time, monotonic=time.monotonic, cooldown=3.0, automatic_retry=True):
        self.path = Path(state) / 'desktop-notifications.json'
        self.notify = notify
        self.is_foreground = is_foreground
        self.on_open = on_open
        self.clock, self.monotonic = clock, monotonic
        self.cooldown = max(0.0, float(cooldown))
        self._lock = threading.RLock()
        self._closed = False
        self._last_request = None
        self._pending = {}
        self._inflight = {}
        self._retry_timer = None
        self._automatic_retry = automatic_retry
        self.warning = None
        self.data = {'schemaVersion': 1, 'preferences': dict(DEFAULTS), 'inbox': [], 'seen': []}
        try:
            if safe(self.path).exists():
                value = read(self.path, 256 * 1024)
                self._validate(value)
                self.data = value
                # Older receipts omitted this display-only field. Keep their
                # identity/read/delivery state: loading must never resend them.
                for row in self.data['inbox']:
                    if row['kind'] == 'attention':
                        row['summary'] = clean_summary(row.get('summary'), DEFAULT_REQUEST_SUMMARY)
        except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
            # Preserve the original file, including a previous disabled setting.
            # Damaged data must never silently opt the user back into OS banners.
            self.warning = '알림 기록을 읽지 못했습니다. 원본은 보존했으며 PC 알림을 보내지 않습니다.'
            self.data['preferences']['enabled'] = False

    @staticmethod
    def _validate(value):
        if (not isinstance(value, dict) or set(value) != {'schemaVersion', 'preferences', 'inbox', 'seen'}
                or value.get('schemaVersion') != 1
                or not isinstance(value.get('preferences'), dict)
                or set(value['preferences']) != set(DEFAULTS)
                or any(type(flag) is not bool for flag in value['preferences'].values())
                or not isinstance(value.get('inbox'), list) or len(value['inbox']) > MAX_INBOX
                or not isinstance(value.get('seen'), list) or len(value['seen']) > MAX_SEEN
                or any(not _identifier(key) for key in value['seen'])
                or len(set(value['seen'])) != len(value['seen'])):
            raise ValueError('Invalid notification state')
        ids = set()
        for row in value['inbox']:
            required = {'id', 'sessionId', 'title', 'kind', 'message', 'createdAt', 'read', 'delivery'}
            if (not isinstance(row, dict) or not required.issubset(row) or set(row) - required - {'summary'}
                    or not _identifier(row['id']) or row['id'] in ids
                    or str(uuid.UUID(row['sessionId'])) != row['sessionId']
                    or row['kind'] not in MESSAGES or row['message'] != MESSAGES[row['kind']]
                    or not isinstance(row['title'], str) or row['title'] != _title(row['title'])
                    or ('summary' in row and (not isinstance(row['summary'], str)
                                            or row['summary'] != clean_summary(row['summary'])))
                    or type(row['createdAt']) not in (int, float) or not math.isfinite(row['createdAt'])
                    or type(row['read']) is not bool or row['delivery'] not in DELIVERY):
                raise ValueError('Invalid notification row')
            ids.add(row['id'])
        if not ids.issubset(set(value['seen'])):
            raise ValueError('Missing notification receipt')

    def _save(self):
        if self.warning:
            return False
        try:
            payload = json.dumps(self.data, ensure_ascii=False, allow_nan=False).encode('utf-8')
            HistoryStore(self.path.parent)._write(self.path, payload)
            return True
        except (OSError, ValueError, TypeError):
            self.warning = '알림 기록을 저장하지 못했습니다. 현재 알림은 앱을 종료하기 전까지 확인할 수 있어요.'
            return False

    def snapshot(self):
        with self._lock:
            return {'preferences': dict(self.data['preferences']),
                    'inbox': deepcopy(list(reversed(self.data['inbox']))),
                    'unreadCount': sum(not row['read'] for row in self.data['inbox']),
                    'warning': self.warning,
                    'deliveryNote': '다른 작업 중에도 완료·응답 대기를 알려드려요. 겹친 알림은 잠시 뒤 표시하며 기록은 이 목록에 남아요.'}

    def configure(self, preferences):
        if (not isinstance(preferences, dict) or not preferences or set(preferences) - set(DEFAULTS)
                or any(type(value) is not bool for value in preferences.values())):
            raise ValueError('알림 설정을 확인해 주세요.')
        with self._lock:
            if self.warning or self._closed:
                raise ValueError('알림 설정을 저장할 수 없습니다. 앱의 알림 상태를 확인해 주세요.')
            previous = dict(self.data['preferences'])
            self.data['preferences'].update(preferences)
            if not self._save():
                self.data['preferences'] = previous
                raise ValueError('알림 설정을 저장하지 못했습니다. 기존 설정을 유지합니다.')
            changed = False
            for key in list(self._pending):
                row = self._retained(key)
                if row and not self._enabled(row):
                    row['delivery'] = 'disabled'
                    self._pending.pop(key, None)
                    changed = True
            if changed:
                self._save()
            return self.snapshot()

    def _retained(self, key):
        return next((row for row in self.data['inbox'] if row['id'] == key), None)

    def _enabled(self, row):
        prefs = self.data['preferences']
        return prefs['enabled'] and prefs['errors' if row['kind'] == 'error' else row['kind']]

    def _schedule_retry(self):
        # One short-lived timer for the whole app, independent of a WebView or
        # its visibility. Nothing is replayed after an app restart.
        if self._closed or not self._pending or self._retry_timer or not self._automatic_retry:
            return
        timer = threading.Timer(max(1.0, self.cooldown), self._retry)
        timer.daemon = True
        self._retry_timer = timer
        timer.start()

    def _defer(self, row, deadline=None):
        retained = {item['id'] for item in self.data['inbox']}
        self._pending = {key: deadline for key, deadline in self._pending.items() if key in retained}
        self._pending.setdefault(row['id'], deadline if deadline is not None else self.monotonic() + MAX_DEFER_SECONDS)
        if deadline is not None:
            self._pending = {row['id']: deadline, **self._pending}
        row['delivery'] = 'queued'
        self._schedule_retry()

    def retain_attention(self, items):
        """Retire delayed popups after their question is answered; keep receipts."""
        current = {notification_id(item['sessionId'], 'attention', item['id']) for item in items}
        with self._lock:
            changed = False
            for key in list(self._pending):
                row = self._retained(key)
                if row and row['kind'] == 'attention' and key not in current:
                    row['delivery'] = 'unavailable'
                    self._pending.pop(key, None)
                    changed = True
            # No write on ordinary repeated polls/events with no pending change.
            if changed:
                self._save()

    def cancel_pending_session(self, session_id):
        """Removing a task from the list must not surface its delayed popup."""
        with self._lock:
            changed = False
            for key in list(self._pending):
                row = self._retained(key)
                if row and row['sessionId'] == session_id:
                    row['delivery'] = 'unavailable'
                    self._pending.pop(key, None)
                    changed = True
            if changed:
                self._save()

    def _retry(self):
        with self._lock:
            self._retry_timer = None
        self.flush_pending()

    def _current_attempt(self, row, token):
        key = row['id']
        return (not self._closed and not self.warning and not row['read']
                and self._enabled(row) and self._inflight.get(key) is token
                and self._retained(key) is row and key in self._pending
                and self.monotonic() < self._pending[key])

    def _finish_attempt(self, row, token, delivery=None):
        key = row['id']
        if self._inflight.get(key) is not token:
            return
        self._inflight.pop(key, None)
        self._pending.pop(key, None)
        if delivery is not None:
            row['delivery'] = delivery
        elif row['delivery'] == 'queued':
            row['delivery'] = 'unavailable'

    def flush_pending(self):
        """Try one deferred card; only known busy/cooldown outcomes are retried."""
        with self._lock:
            if self._closed:
                return
            now, row, token = self.monotonic(), None, None
            for key, deadline in list(self._pending.items()):
                candidate = self._retained(key)
                if candidate is None or candidate['read'] or now >= deadline or self.warning:
                    self._pending.pop(key, None)
                    if candidate and candidate['delivery'] == 'queued':
                        candidate['delivery'] = 'unavailable'
                    continue
                if not self._enabled(candidate):
                    candidate['delivery'] = 'disabled'
                    self._pending.pop(key, None)
                    continue
                if row is None and key not in self._inflight:
                    row = candidate
            if row and (self._last_request is None or now - self._last_request >= self.cooldown):
                self._last_request = now
                token = object()
                self._inflight[row['id']] = token
                # Keep the receipt cancellable while a foreground check or the
                # private native IPC is in progress. Cancellation removes it
                # from _pending; the in-flight token prevents parallel retry.
            else:
                row = None
            self._save()
        if row:
            foreground = False
            if callable(self.is_foreground):
                try:
                    foreground = self.is_foreground(row['sessionId']) is True
                except Exception:
                    pass
            if foreground:
                with self._lock:
                    delivery = 'foreground' if self._current_attempt(row, token) else None
                    self._finish_attempt(row, token, delivery)
                    self._save()
            else:
                self._deliver(row, token)
        with self._lock:
            self._schedule_retry()

    def _deliver(self, row, token):
        with self._lock:
            if not self._current_attempt(row, token):
                self._finish_attempt(row, token)
                self._save()
                return
        # Do not hold the inbox lock across native IPC or callback code, which
        # can need the server's task lock. Once notify has begun, cancellation
        # cannot retract a card already accepted by Windows/the native host.
        outcome = False
        try:
            outcome = self.notify(deepcopy(row), lambda: self.open(row['id']))
        except Exception:
            pass
        with self._lock:
            if not self._current_attempt(row, token):
                self._finish_attempt(row, token)
            elif outcome is True:
                self._finish_attempt(row, token, 'requested')
            elif outcome == 'busy':
                self._inflight.pop(row['id'], None)
                self._defer(row, deadline=self._pending[row['id']])
            else:
                # Suppression or a lost acknowledgement is not safe to replay.
                self._finish_attempt(row, token, 'unavailable')
            self._save()

    def publish(self, session_id, title, kind, event_id, *, summary=''):
        key = notification_id(session_id, kind, event_id)
        # A server hook may acquire the app lock. Never hold our lock across it.
        foreground = False
        if callable(self.is_foreground):
            try:
                foreground = self.is_foreground(session_id) is True
            except Exception:
                pass
        with self._lock:
            if self._closed:
                return {'id': key, 'closed': True}
            if key in self.data['seen']:
                previous = next((row for row in self.data['inbox'] if row['id'] == key), None)
                return {**deepcopy(previous or {'id': key}), 'duplicate': True}
            prefs = self.data['preferences']
            now = self.monotonic()
            if not prefs['enabled'] or not prefs['errors' if kind == 'error' else kind]:
                delivery = 'disabled'
            elif foreground:
                delivery = 'foreground'
            elif self._last_request is not None and now - self._last_request < self.cooldown:
                delivery = 'cooldown'
            else:
                delivery = 'unavailable'
            row = {'id': key, 'sessionId': session_id, 'title': _title(title), 'kind': kind,
                   'message': MESSAGES[kind], 'createdAt': self.clock(), 'read': False, 'delivery': delivery}
            if kind == 'attention':
                row['summary'] = clean_summary(summary, DEFAULT_REQUEST_SUMMARY)
            self.data['seen'] = [*self.data['seen'], key][-MAX_SEEN:]
            self.data['inbox'] = [*self.data['inbox'], row][-MAX_INBOX:]
            self._save()  # Record before asking Windows; restart never replays it.
            if delivery == 'cooldown' and callable(self.notify) and not self.warning:
                self._defer(row)
                self._save()
            attempt = delivery == 'unavailable' and callable(self.notify) and not self.warning
            if attempt:
                self._last_request = now
                self._pending[key] = now + MAX_DEFER_SECONDS
                token = object()
                self._inflight[key] = token
        if attempt:
            self._deliver(row, token)
        with self._lock:
            return deepcopy(row)

    def mark_read(self, identifier=None):
        if identifier is not None and not _identifier(identifier):
            raise ValueError('알림 식별자를 확인해 주세요.')
        with self._lock:
            if identifier is not None and not any(row['id'] == identifier for row in self.data['inbox']):
                raise ValueError('알림을 찾을 수 없습니다.')
            for row in self.data['inbox']:
                if identifier is None or row['id'] == identifier:
                    row['read'] = True
            self._save()
            return self.snapshot()

    def mark_read_many(self, identifiers):
        """Mark exact retained receipts together; stale/invalid batches change none."""
        if (not isinstance(identifiers, list) or not 1 <= len(identifiers) <= MAX_INBOX
                or any(not _identifier(value) for value in identifiers)
                or len(set(identifiers)) != len(identifiers)):
            raise ValueError('읽음으로 표시할 알림 목록을 확인해 주세요.')
        requested = set(identifiers)
        with self._lock:
            retained = {row['id'] for row in self.data['inbox']}
            if not requested.issubset(retained):
                raise ValueError('일부 알림을 찾을 수 없습니다. 목록을 갱신한 뒤 다시 선택해 주세요.')
            for row in self.data['inbox']:
                if row['id'] in requested:
                    row['read'] = True
            self._save()
            return self.snapshot()

    def open(self, identifier):
        with self._lock:
            row = next((row for row in self.data['inbox'] if row['id'] == identifier), None)
            if self._closed or row is None or not callable(self.on_open):
                return False
            session_id = row['sessionId']
        try:
            if self.on_open(session_id) is False:
                return False
        except Exception:
            return False
        with self._lock:
            row['read'] = True
            self._save()
        return True

    def close(self):
        with self._lock:
            self._closed = True
            self._pending.clear()
            self._inflight.clear()
            if self._retry_timer:
                self._retry_timer.cancel()
                self._retry_timer = None
