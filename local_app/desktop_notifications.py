"""App-owned notification preferences and a small durable activity inbox.

Only task titles and fixed status messages leave the app. Native delivery is a
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


DEFAULTS = {'enabled': True, 'completed': True, 'attention': True, 'errors': True}
MESSAGES = {
    'completed': '작업이 완료됐어요. 클릭하면 결과를 볼 수 있어요.',
    'attention': '승인 또는 답변이 필요해요. 클릭하면 해당 업무를 열어요.',
    'error': '작업 상태를 확인해 주세요. 클릭하면 해당 업무를 열어요.',
}
DELIVERY = {'disabled', 'foreground', 'cooldown', 'unavailable', 'requested'}
MAX_INBOX, MAX_SEEN = 50, 256


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
                 clock=time.time, monotonic=time.monotonic, cooldown=3.0):
        self.path = Path(state) / 'desktop-notifications.json'
        self.notify = notify
        self.is_foreground = is_foreground
        self.on_open = on_open
        self.clock, self.monotonic = clock, monotonic
        self.cooldown = max(0.0, float(cooldown))
        self._lock = threading.RLock()
        self._closed = False
        self._last_request = None
        self.warning = None
        self.data = {'schemaVersion': 1, 'preferences': dict(DEFAULTS), 'inbox': [], 'seen': []}
        try:
            if safe(self.path).exists():
                value = read(self.path, 256 * 1024)
                self._validate(value)
                self.data = value
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
            if (not isinstance(row, dict) or set(row) != {'id', 'sessionId', 'title', 'kind', 'message', 'createdAt', 'read', 'delivery'}
                    or not _identifier(row['id']) or row['id'] in ids
                    or str(uuid.UUID(row['sessionId'])) != row['sessionId']
                    or row['kind'] not in MESSAGES or row['message'] != MESSAGES[row['kind']]
                    or not isinstance(row['title'], str) or row['title'] != _title(row['title'])
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
                    'deliveryNote': 'PC 알림은 Windows 알림 설정에 따라 표시되지 않을 수 있어요. 기록은 이 목록에서 확인할 수 있어요.'}

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
            return self.snapshot()

    def publish(self, session_id, title, kind, event_id):
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
            self.data['seen'] = [*self.data['seen'], key][-MAX_SEEN:]
            self.data['inbox'] = [*self.data['inbox'], row][-MAX_INBOX:]
            self._save()  # Record before asking Windows; restart never replays it.
            attempt = delivery == 'unavailable' and callable(self.notify) and not self.warning
            if attempt:
                self._last_request = now
        requested = False
        if attempt:
            try:
                requested = self.notify(deepcopy(row), lambda: self.open(key)) is True
            except Exception:
                pass
        with self._lock:
            if requested:
                row['delivery'] = 'requested'
                self._save()
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
