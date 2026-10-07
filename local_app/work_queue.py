"""App-owned follow-ups and wall-clock schedules, independent of Claude tools.

The server owns the single scheduler loop and calls its existing send() only
after claim(). Claims are persisted before delivery; uncertain delivery is
never retried. This module never starts processes or answers permissions.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import hashlib
import math
from pathlib import Path
import threading
import time
import uuid

from .history import HistoryStore, read, safe
from .schedule_time import KINDS, timing, next_after

POLICY = {
    'runsWhileAppOpen': True, 'requiresAwakePc': True, 'missedRuns': 'skip',
    'restart': 'paused_until_confirmed', 'uncertainDelivery': 'manual_review_no_retry',
    'permissions': 'existing_session_policy', 'timeZone': 'pc_local',
    'settingsChanged': 'pause', 'maxOutstandingPerSchedule': 1,
}
ACTIVE = {'queued', 'dispatching', 'submitted', 'needs_review'}
CONTEXT_KEYS = ('workspace', 'model', 'effort', 'permissionMode')


def _sid(value):
    if not isinstance(value, str) or not value or len(value) > 160:
        raise ValueError('업무 식별자를 확인해 주세요.')
    return value


def _prompt(text, attachments):
    if not isinstance(text, str) or not text.strip() or len(text) > 32000:
        raise ValueError('요청은 1~32,000자로 입력해 주세요.')
    if not isinstance(attachments, list) or len(attachments) > 12 or any(
            not isinstance(value, str) or not value or len(value) > 8192 for value in attachments):
        raise ValueError('첨부 파일 목록을 확인해 주세요.')
    # Existence/type/trust are checked again by the server's normal send path.
    return text.strip(), list(dict.fromkeys(attachments))


def _context(value):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError('업무 실행 설정을 확인해 주세요.')
    result = {}
    for key in CONTEXT_KEYS:
        if key in value:
            item = value[key]
            if item is not None and (not isinstance(item, str) or len(item) > 8192):
                raise ValueError('업무 실행 설정을 확인해 주세요.')
            result[key] = item
    return result


def _stamp(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('예약 시각을 확인해 주세요.')
    try:
        datetime.fromtimestamp(value)
    except (ValueError, OverflowError, OSError):
        raise ValueError('예약 시각을 확인해 주세요.') from None
    return float(value)


def _rule(kind, run_at, wall_time, weekdays, now, *, day_of_month=None,
          interval_minutes=None, start_time=None, end_time=None):
    first = _stamp(run_at)
    if first <= now:
        raise ValueError('앞으로 실행할 시각을 선택해 주세요.')
    local = datetime.fromtimestamp(first)
    if wall_time is None and kind != 'interval':
        wall_time = local.strftime('%H:%M')
    if kind == 'weekly' and weekdays is None:
        weekdays = [local.weekday()]
    rule = timing(kind, wall_time, weekdays, day_of_month=day_of_month,
                  interval_minutes=interval_minutes, start_time=start_time, end_time=end_time)
    if kind != 'once' and kind != 'interval' and (
            local.strftime('%H:%M') != rule['time']
            or kind in {'weekly', 'weekdays'} and local.weekday() not in rule['weekdays']
            or kind == 'monthly' and local.day != rule['dayOfMonth']):
        raise ValueError('첫 예약 시각과 반복 시간·날짜를 맞춰 주세요.')
    if kind == 'interval':
        expected = next_after(rule, first - .001)
        if not math.isclose(expected, first, rel_tol=0, abs_tol=.0001):
            raise ValueError('첫 예약 시각과 반복 간격·실행 구간을 맞춰 주세요.')
    return {**rule, 'runAt': first, 'nextAt': first}


def _next_after(schedule, now):
    rule = timing(schedule['kind'], schedule['time'], schedule.get('weekdays'),
                  day_of_month=schedule.get('dayOfMonth'), interval_minutes=schedule.get('intervalMinutes'),
                  start_time=schedule.get('startTime'), end_time=schedule.get('endTime'))
    return next_after(rule, now)


def _paused_by_user(schedule):
    if 'pausedByUser' in schedule:
        return schedule['pausedByUser']
    # Legacy stores conflated one-shot exhaustion and explicit pause. Retain
    # recognizable pauses; a consumed occurrence can be scheduled again by edit.
    consumed = (schedule['kind'] == 'once' and schedule['nextAt'] is None and
                schedule.get('lastStatus') in {'queued', 'submitted', 'done', 'missed',
                                               'queue_full', 'needs_review', 'previous_pending'})
    return not schedule['enabled'] and not consumed


class WorkQueue:
    def __init__(self, root: Path, *, clock=time.time, grace_seconds=300):
        self.root, self.clock = Path(root), clock
        self.path = self.root / 'work-queue.json'
        self.lock = threading.RLock()
        self.grace = grace_seconds
        self.warning = None
        self.data = {'schemaVersion': 1, 'revision': 0, 'queue': [], 'schedules': [], 'holds': {}, 'receipts': {}}
        self._early_results = {}
        try:
            if safe(self.path).exists():
                value = read(self.path, 32 * 1024 * 1024)
                self._validate_loaded(value)
                value.setdefault('receipts', {})
                self.data = value
                sessions = set()
                for row in value['queue']:
                    if row['status'] in ACTIVE:
                        sessions.add(row['sessionId'])
                    if row['status'] in {'dispatching', 'submitted'}:
                        row['status'], row['reason'] = 'needs_review', 'delivery_unknown_after_restart'
                for schedule in value['schedules']:
                    schedule.setdefault('pausedByUser', _paused_by_user(schedule))
                    if schedule['enabled']:
                        sessions.add(schedule['sessionId'])
                for sid in sessions:
                    value['holds'][sid] = 'restart'
                if sessions:
                    self._save()
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            # Preserve damaged/unsupported data; do not start a clean scheduler
            # which could repeat previously delivered work.
            self.warning = '대기·예약 기록을 확인하지 못해 자동 실행을 중지했습니다. 원본 기록은 보존했습니다.'

    @staticmethod
    def _validate_loaded(value):
        if (not isinstance(value, dict) or value.get('schemaVersion') != 1 or
                type(value.get('revision')) is not int or not isinstance(value.get('holds'), dict) or
                not isinstance(value.get('queue'), list) or len(value['queue']) > 1000 or
                not isinstance(value.get('schedules'), list) or len(value['schedules']) > 100 or
                not isinstance(value.get('receipts', {}), dict) or len(value.get('receipts', {})) > 10000):
            raise ValueError('Invalid queue store')
        seen = set()
        for row in value['queue'] + value['schedules']:
            if not isinstance(row, dict) or not isinstance(row.get('id'), str) or len(row['id']) != 32 or row['id'] in seen:
                raise ValueError('Invalid queue identity')
            int(row['id'], 16)
            seen.add(row['id'])
            _sid(row['sessionId'])
            _prompt(row['text'], row['attachments'])
            _context(row['context'])
        for row in value['queue']:
            if row['status'] not in ACTIVE | {'done', 'cancelled'}:
                raise ValueError('Invalid queue status')
            if type(row.get('editRevision', 0)) is not int or row.get('editRevision', 0) < 0:
                raise ValueError('Invalid queue edit revision')
            for field in ('createdAt', 'updatedAt'):
                _stamp(row[field])
            if row.get('runId') is not None and (not isinstance(row['runId'], str) or len(row['runId']) > 160):
                raise ValueError('Invalid run identity')
        for schedule in value['schedules']:
            if type(schedule['enabled']) is not bool or schedule['kind'] not in KINDS:
                raise ValueError('Invalid schedule')
            if 'pausedByUser' in schedule and type(schedule['pausedByUser']) is not bool:
                raise ValueError('Invalid schedule pause intent')
            if schedule['nextAt'] is not None:
                _stamp(schedule['nextAt'])
            _stamp(schedule['runAt'])
            if datetime.strptime(schedule['time'], '%H:%M').strftime('%H:%M') != schedule['time']:
                raise ValueError('Invalid wall time')
            if not isinstance(schedule['weekdays'], list) or any(type(day) is not int or day not in range(7) for day in schedule['weekdays']):
                raise ValueError('Invalid weekdays')
            if schedule['kind'] == 'weekly' and not schedule['weekdays']:
                raise ValueError('Invalid weekdays')
            timing(schedule['kind'], schedule['time'], schedule['weekdays'],
                   day_of_month=schedule.get('dayOfMonth'), interval_minutes=schedule.get('intervalMinutes'),
                   start_time=schedule.get('startTime'), end_time=schedule.get('endTime'))
        for sid, reason in value['holds'].items():
            _sid(sid)
            if reason not in {'user', 'error', 'stopped', 'restart', 'settings_changed', 'delivery_unknown', 'control_restore_required'}:
                raise ValueError('Invalid hold')
        for key, receipt in value.get('receipts', {}).items():
            if not isinstance(key, str) or len(key) != 64 or not isinstance(receipt, dict):
                raise ValueError('Invalid receipt')
            for field, length in (('id', 32), ('digest', 64)):
                if not isinstance(receipt.get(field), str) or len(receipt[field]) != length:
                    raise ValueError('Invalid receipt')
                int(receipt[field], 16)
            int(key, 16)

    def _check(self):
        if self.warning:
            raise ValueError(self.warning)

    def _save(self):
        self._check()
        terminal = [row for row in self.data['queue'] if row['status'] not in ACTIVE][-100:]
        keep = {row['id'] for row in terminal}
        self.data['queue'] = [row for row in self.data['queue'] if row['status'] in ACTIVE or row['id'] in keep]
        self.data['revision'] += 1
        try:
            payload = json.dumps(self.data, ensure_ascii=False, allow_nan=False).encode('utf-8')
            if len(payload) > 32 * 1024 * 1024:
                raise ValueError('대기·예약 기록의 확인 범위를 초과했습니다.')
            HistoryStore(self.root)._write(self.path, payload)
        except (OSError, ValueError):
            self.warning = '대기·예약 기록을 저장하지 못해 자동 실행을 중지했습니다.'
            raise

    def snapshot(self, sid=None):
        with self.lock:
            rows = [row for row in self.data['queue'] if row['status'] in ACTIVE and (sid is None or row['sessionId'] == sid)]
            schedules = [row for row in self.data['schedules'] if sid is None or row['sessionId'] == sid]
            reason = self.data['holds'].get(sid) if sid is not None else None
            rows = [{**row, 'state': row['status']} for row in rows]
            schedules = [{**row, 'nextRunAt': row['nextAt'], 'pausedByUser': _paused_by_user(row),
                          'lastRun': {'dueAt': row['lastDueAt'], 'status': row['lastStatus']}} for row in schedules]
            return deepcopy({'revision': self.data['revision'], 'queue': rows, 'schedules': schedules,
                             'paused': bool(reason), 'reason': reason, 'warning': self.warning, 'policy': POLICY})

    def dispatch_candidates(self):
        """Read only task IDs that can have an unsent request claimed.

        Do not copy prompt/attachment payloads or scan the application's whole
        conversation history. Claim still rechecks all mutable admission state
        under its lock immediately before a request is sent.
        """
        with self.lock:
            self._check()
            blocked = {row['sessionId'] for row in self.data['queue']
                       if row['status'] in {'dispatching', 'submitted', 'needs_review'}}
            return tuple(dict.fromkeys(row['sessionId'] for row in self.data['queue']
                         if row['status'] == 'queued' and row['sessionId'] not in blocked
                         and not self.data['holds'].get(row['sessionId'])))

    def _new_row(self, sid, text, attachments, context, *, schedule_id=None, due=None):
        active = [row for row in self.data['queue'] if row['status'] in ACTIVE]
        if len(active) >= 500 or sum(row['sessionId'] == sid for row in active) >= 64:
            raise ValueError('대기 요청이 많습니다. 기존 요청을 정리한 뒤 추가해 주세요.')
        return {'id': uuid.uuid4().hex, 'sessionId': sid, 'text': text, 'attachments': attachments,
                'context': context, 'status': 'queued', 'createdAt': self.clock(), 'updatedAt': self.clock(),
                'scheduleId': schedule_id, 'dueAt': due, 'runId': None, 'reason': None, 'editRevision': 0}

    def enqueue(self, sid, text, attachments=None, context=None, *, client_id=None, clear_inactive_hold=False):
        sid = _sid(sid)
        text, attachments = _prompt(text, [] if attachments is None else attachments)
        context = _context(context)
        if client_id is not None and (not isinstance(client_id, str) or not client_id or len(client_id) > 160):
            raise ValueError('전송 식별자를 확인해 주세요.')
        with self.lock:
            self._check()
            receipt_key = hashlib.sha256((sid + '\0' + client_id).encode()).hexdigest() if client_id is not None else None
            digest = hashlib.sha256(json.dumps([text, attachments, context], sort_keys=True).encode()).hexdigest()
            if receipt_key in self.data['receipts']:
                receipt = self.data['receipts'][receipt_key]
                if receipt['digest'] != digest:
                    raise ValueError('같은 전송 식별자로 다른 요청을 보낼 수 없습니다.')
                previous = next((row for row in self.data['queue'] if row['id'] == receipt['id']), None)
                return deepcopy(previous or {'id': receipt['id'], 'sessionId': sid, 'status': 'done', 'state': 'done'})
            if receipt_key is not None and len(self.data['receipts']) >= 10000:
                raise ValueError('중복 전송 방지 기록의 보관 한도에 도달했습니다.')
            row = self._new_row(sid, text, attachments, context)
            # A failed/stopped turn can leave a hold even when it had no queued
            # work. An explicit new follow-up in a healthy conversation must not
            # inherit that obsolete pause. Do not resume older pending work,
            # schedules, uncertain deliveries, or user/settings confirmations.
            # Run this only after the idempotency check: retrying an old enqueue
            # must never undo a later stop.
            if (clear_inactive_hold and self.data['holds'].get(sid) in {'error', 'stopped', 'restart', 'delivery_unknown'}
                    and not any(item['sessionId'] == sid and item['status'] in ACTIVE for item in self.data['queue'])
                    and not any(item['sessionId'] == sid and item['enabled'] for item in self.data['schedules'])):
                self.data['holds'].pop(sid, None)
            if receipt_key is not None:
                row['clientRequestId'] = client_id
                self.data['receipts'][receipt_key] = {'id': row['id'], 'digest': digest}
            self.data['queue'].append(row)
            self._save()
            return deepcopy(row)

    def _row(self, sid, identifier, collection='queue'):
        self._check()
        for row in self.data[collection]:
            if row['id'] == identifier and row['sessionId'] == sid:
                return row
        raise ValueError('대기 요청 또는 예약을 찾을 수 없습니다.')

    def edit(self, sid, identifier, text, attachments=None, context=None):
        text, attachments = _prompt(text, [] if attachments is None else attachments)
        context = _context(context) if context is not None else None
        with self.lock:
            row = self._row(sid, identifier)
            if row['status'] != 'queued':
                raise ValueError('전송 전 대기 요청만 수정할 수 있습니다.')
            row.update(text=text, attachments=attachments, updatedAt=self.clock(),
                       editRevision=row.get('editRevision', 0) + 1)
            if context is not None:
                row['context'] = context
            self._save()
            return deepcopy(row)

    def apply_now(self, sid, identifier, revision, client_id, *, commit=False):
        """Prioritize the existing row, never copy it into another request.

        The controller holds the app admission lock across preflight and commit.
        A durable receipt makes retries harmless even after delivery/restart or
        terminal-row pruning. A revision rejects an outdated editor/list click.
        """
        sid = _sid(sid)
        if (not isinstance(identifier, str) or type(revision) is not int or revision < 0
                or not isinstance(client_id, str) or not client_id or len(client_id) > 150):
            raise ValueError('대기 요청과 전송 식별자를 확인해 주세요.')
        key = hashlib.sha256((sid + '\0apply_now:' + client_id).encode()).hexdigest()
        digest = hashlib.sha256(json.dumps([identifier, revision]).encode()).hexdigest()
        with self.lock:
            self._check()
            receipt = self.data['receipts'].get(key)
            if receipt is not None:
                if receipt['digest'] != digest:
                    raise ValueError('같은 전송 식별자로 다른 요청을 보낼 수 없습니다.')
                return None, True
            row = self._row(sid, identifier)
            if row.get('editRevision', 0) != revision:
                raise ValueError('대기 요청 내용이 바뀌었습니다. 목록을 다시 확인한 뒤 실행해 주세요.')
            if row['status'] in {'dispatching', 'submitted', 'done'}:
                return deepcopy(row), True  # Automatic dispatch won the race.
            if row['status'] != 'queued':
                raise ValueError('현재 대기 중인 요청만 바로 실행할 수 있습니다.')
            if commit:
                if len(self.data['receipts']) >= 10000:
                    raise ValueError('중복 전송 방지 기록의 보관 한도에 도달했습니다.')
                slots = [i for i, value in enumerate(self.data['queue'])
                         if value['sessionId'] == sid and value['status'] == 'queued']
                ordered = [row] + [self.data['queue'][i] for i in slots if self.data['queue'][i]['id'] != identifier]
                for index, value in zip(slots, ordered):
                    self.data['queue'][index] = value
                self.data['receipts'][key] = {'id': identifier, 'digest': digest}
                self._save()
            return deepcopy(row), False

    def cancel(self, sid, identifier):
        with self.lock:
            row = self._row(sid, identifier)
            if row['status'] not in {'queued', 'needs_review'}:
                raise ValueError('이미 전송된 요청은 현재 작업의 중지 기능을 사용해 주세요.')
            row.update(status='cancelled', updatedAt=self.clock())
            self._save()

    def reorder(self, sid, identifiers):
        with self.lock:
            self._check()
            slots = [i for i, row in enumerate(self.data['queue']) if row['sessionId'] == sid and row['status'] == 'queued']
            rows = {self.data['queue'][i]['id']: self.data['queue'][i] for i in slots}
            if (not isinstance(identifiers, list) or len(identifiers) != len(rows) or
                    any(not isinstance(value, str) for value in identifiers) or set(identifiers) != set(rows)):
                raise ValueError('현재 대기 중인 요청을 한 번씩 포함해 순서를 정해 주세요.')
            for index, identifier in zip(slots, identifiers):
                self.data['queue'][index] = rows[identifier]
            self._save()

    def pause(self, sid, reason='user'):
        with self.lock:
            self._check()
            self.data['holds'][_sid(sid)] = reason if reason in {'user', 'error', 'stopped', 'restart', 'settings_changed', 'delivery_unknown', 'control_restore_required'} else 'user'
            self._save()

    def resume(self, sid):
        with self.lock:
            self._check()
            # Restart never catches up missed occurrences, even inside grace.
            if self.data['holds'].get(sid) == 'restart':
                for schedule in self.data['schedules']:
                    if schedule['sessionId'] == sid and schedule['nextAt'] is not None and schedule['nextAt'] <= self.clock():
                        self._advance(schedule, self.clock(), 'missed')
            self.data['holds'].pop(sid, None)
            self._save()

    def confirm_context(self, sid, context):
        """Apply an explicit user's confirmation to future work only.

        This changes app queue metadata, never a CLI/personal settings file.
        Delivered or uncertain work retains its original execution context.
        """
        sid, context = _sid(sid), _context(context)
        with self.lock:
            self._check()
            for row in self.data['queue']:
                if row['sessionId'] == sid and row['status'] == 'queued':
                    if row['context'] != context:
                        row['editRevision'] = row.get('editRevision', 0) + 1
                    row['context'] = dict(context)
            for schedule in self.data['schedules']:
                if schedule['sessionId'] == sid and schedule['enabled']:
                    schedule['context'] = dict(context)
            self._save()

    def add_schedule(self, sid, text, attachments=None, *, kind, run_at, context=None, time=None, weekdays=None, client_id=None,
                     day_of_month=None, interval_minutes=None, start_time=None, end_time=None):
        text, attachments = _prompt(text, [] if attachments is None else attachments)
        sid, context = _sid(sid), _context(context)
        if client_id is not None and (not isinstance(client_id, str) or not client_id or len(client_id) > 160):
            raise ValueError('전송 식별자를 확인해 주세요.')
        receipt_key = hashlib.sha256(('schedule\0' + sid + '\0' + client_id).encode()).hexdigest() if client_id is not None else None
        # A retried daily/weekly registration can compute a different first
        # timestamp at midnight. Its wall-time rule still identifies the intent.
        intent = [text, attachments, context, kind, run_at if kind == 'once' else None, time, weekdays]
        if kind in {'monthly', 'interval'}:
            intent.extend([day_of_month, interval_minutes, start_time, end_time])
        digest = hashlib.sha256(json.dumps(intent, sort_keys=True).encode()).hexdigest()
        with self.lock:
            self._check()
            if receipt_key in self.data['receipts']:
                receipt = self.data['receipts'][receipt_key]
                if receipt['digest'] != digest:
                    raise ValueError('같은 전송 식별자로 다른 예약을 보낼 수 없습니다.')
                previous = next((row for row in self.data['schedules'] if row['id'] == receipt['id']), None)
                return deepcopy(previous or {'id': receipt['id'], 'sessionId': sid, 'enabled': False})
        rule = _rule(kind, run_at, time, weekdays, self.clock(), day_of_month=day_of_month,
                     interval_minutes=interval_minutes, start_time=start_time, end_time=end_time)
        with self.lock:
            self._check()
            # Recheck after validation in case a concurrent HTTP retry won.
            if receipt_key in self.data['receipts']:
                return self.add_schedule(sid, text, attachments, kind=kind, run_at=run_at,
                                         context=context, time=time, weekdays=weekdays, client_id=client_id,
                                         day_of_month=day_of_month, interval_minutes=interval_minutes,
                                         start_time=start_time, end_time=end_time)
            if receipt_key is not None and len(self.data['receipts']) >= 10000:
                raise ValueError('중복 전송 방지 기록의 보관 한도에 도달했습니다.')
            if len(self.data['schedules']) >= 100:
                raise ValueError('예약은 최대 100개까지 저장할 수 있습니다.')
            row = {'id': uuid.uuid4().hex, 'sessionId': sid, 'text': text, 'attachments': attachments,
                   'context': context, **rule, 'enabled': True, 'pausedByUser': False,
                   'lastDueAt': None, 'lastStatus': None}
            self.data['schedules'].append(row)
            if receipt_key is not None:
                self.data['receipts'][receipt_key] = {'id': row['id'], 'digest': digest}
            self._save()
            return deepcopy(row)

    def update_schedule(self, sid, identifier, text, attachments=None, *, kind, run_at, context=None, time=None, weekdays=None, enabled=None,
                        day_of_month=None, interval_minutes=None, start_time=None, end_time=None):
        text, attachments = _prompt(text, [] if attachments is None else attachments)
        rule = _rule(kind, run_at, time, weekdays, self.clock(), day_of_month=day_of_month,
                     interval_minutes=interval_minutes, start_time=start_time, end_time=end_time)
        context = _context(context) if context is not None else None
        if enabled is not None and type(enabled) is not bool:
            raise ValueError('예약 사용 여부를 확인해 주세요.')
        with self.lock:
            row = self._row(sid, identifier, 'schedules')
            selected_enabled = not _paused_by_user(row) if enabled is None else enabled
            row.update(text=text, attachments=attachments, **rule, enabled=selected_enabled,
                       pausedByUser=not selected_enabled)
            if context is not None:
                row['context'] = context
            # Editing/rescheduling withdraws only not-yet-sent occurrences.
            for pending in self.data['queue']:
                if pending.get('scheduleId') == identifier and pending['status'] == 'queued':
                    pending['status'] = 'cancelled'
            self._save()
            return deepcopy(row)

    def cancel_schedule(self, sid, identifier):
        with self.lock:
            self._row(sid, identifier, 'schedules')
            self.data['schedules'] = [row for row in self.data['schedules'] if row['id'] != identifier]
            for row in self.data['queue']:
                if row.get('scheduleId') == identifier and row['status'] == 'queued':
                    row['status'] = 'cancelled'
            self._save()

    def set_schedule_enabled(self, sid, identifier, enabled):
        if type(enabled) is not bool:
            raise ValueError('예약 사용 여부를 확인해 주세요.')
        with self.lock:
            schedule = self._row(sid, identifier, 'schedules')
            schedule['enabled'] = enabled
            schedule['pausedByUser'] = not enabled
            if not enabled:
                for row in self.data['queue']:
                    if row.get('scheduleId') == identifier and row['status'] == 'queued':
                        row['status'] = 'cancelled'
                schedule['lastStatus'] = 'paused'
            elif schedule['nextAt'] is None:
                schedule['enabled'] = False
            elif schedule['nextAt'] <= self.clock():
                self._advance(schedule, self.clock(), 'missed')
            self._save()
            return deepcopy(schedule)

    def _schedule_status(self, row, status):
        for schedule in self.data['schedules']:
            if schedule['id'] == row.get('scheduleId') and schedule['lastDueAt'] == row.get('dueAt'):
                schedule['lastStatus'] = status

    @staticmethod
    def _advance(schedule, now, status):
        schedule['lastDueAt'], schedule['lastStatus'] = schedule['nextAt'], status
        schedule['nextAt'] = _next_after(schedule, now)
        if schedule['nextAt'] is None:
            schedule['enabled'] = False

    def tick(self, now=None):
        now = self.clock() if now is None else _stamp(now)
        with self.lock:
            self._check()
            changed = False
            for schedule in self.data['schedules']:
                due = schedule['nextAt']
                if not schedule['enabled'] or due is None or due > now or self.data['holds'].get(schedule['sessionId']) == 'restart':
                    continue
                changed = True
                outstanding = any(row.get('scheduleId') == schedule['id'] and row['status'] in ACTIVE for row in self.data['queue'])
                if now - due > self.grace or outstanding:
                    self._advance(schedule, now, 'missed' if not outstanding else 'previous_pending')
                    continue
                try:
                    row = self._new_row(schedule['sessionId'], schedule['text'], list(schedule['attachments']),
                                        dict(schedule['context']), schedule_id=schedule['id'], due=due)
                except ValueError:
                    self._advance(schedule, now, 'queue_full')
                    continue
                self.data['queue'].append(row)
                self._advance(schedule, now, 'queued')
            if changed:
                self._save()
            return changed

    def claim(self, sid, session_state, context=None):
        with self.lock:
            self._check()
            if (self.data['holds'].get(sid) or not isinstance(session_state, dict) or
                    session_state.get('state') not in {'idle', 'done'} or session_state.get('trusted') is not True or
                    session_state.get('requests') or session_state.get('choice') or
                    (session_state.get('verification') or {}).get('state') == 'needs-review' or
                    session_state.get('_connecting') or session_state.get('_modelUpdating') or
                    session_state.get('_permissionUpdating')):
                return None
            if any(row['sessionId'] == sid and row['status'] in {'dispatching', 'submitted', 'needs_review'} for row in self.data['queue']):
                return None
            row = next((row for row in self.data['queue'] if row['sessionId'] == sid and row['status'] == 'queued'), None)
            if row is None:
                return None
            actual = _context(context)
            if any(actual.get(key) != value for key, value in row['context'].items()):
                self.data['holds'][sid] = 'settings_changed'
                self._save()
                return None
            row.update(status='dispatching', reason=None, updatedAt=self.clock())
            self._save()  # Must complete before any user request is sent.
            return deepcopy(row)

    def dispatched(self, identifier, run_id=None):
        with self.lock:
            self._check()
            row = next((item for item in self.data['queue'] if item['id'] == identifier), None)
            if row is None or row['status'] != 'dispatching':
                return
            early = self._early_results.pop(row['sessionId'], None)
            row.update(status='submitted', runId=run_id, updatedAt=self.clock())
            if early is not None and run_id is not None and early == run_id:
                row['status'] = 'done'
            self._schedule_status(row, row['status'])
            self._save()

    def defer_unsubmitted(self, identifier):
        """Preserve a request rejected by the typed control-restoration preflight.

        Only the caller with proof of no delivery may use this path. Sent,
        interrupted or uncertain rows are never made eligible for replay.
        A separate explicit resume is still required after controls are fixed.
        """
        with self.lock:
            self._check()
            row = next((item for item in self.data['queue'] if item['id'] == identifier), None)
            if row is None or row['status'] != 'dispatching' or row.get('runId') is not None:
                return
            row.update(status='queued', reason='control_restore_required', updatedAt=self.clock())
            self._early_results.pop(row['sessionId'], None)
            self._schedule_status(row, 'queued')
            self.data['holds'].setdefault(row['sessionId'], 'control_restore_required')
            self._save()

    def failed(self, identifier):
        with self.lock:
            self._check()
            row = next((item for item in self.data['queue'] if item['id'] == identifier), None)
            if row is None or row['status'] not in {'dispatching', 'submitted'}:
                return
            row.update(status='needs_review', reason='delivery_unknown', updatedAt=self.clock())
            self._schedule_status(row, 'needs_review')
            self.data['holds'][row['sessionId']] = 'delivery_unknown'
            self._save()

    def observe(self, sid, kind, data):
        with self.lock:
            if self.warning:
                return
            if kind == 'error' or kind == 'status' and data.get('state') == 'stopped':
                self.data['holds'][sid] = 'error' if kind == 'error' else 'stopped'
                for row in self.data['queue']:
                    if row['sessionId'] == sid and row['status'] in {'dispatching', 'submitted'}:
                        row.update(status='needs_review', reason='interrupted', updatedAt=self.clock())
                        self._schedule_status(row, 'needs_review')
                self._save()
            elif kind == 'result':
                if (data.get('verification') or {}).get('state') == 'needs-review':
                    self.data['holds'][sid] = 'error'
                    self._save()
                active = next((row for row in self.data['queue'] if row['sessionId'] == sid and row['status'] in {'dispatching', 'submitted'}), None)
                if active is None:
                    return
                run_id = data.get('lastRunId')
                if active['status'] == 'dispatching':
                    self._early_results[sid] = run_id
                elif active['runId'] is None or active['runId'] == run_id:
                    active.update(status='done', updatedAt=self.clock())
                    self._schedule_status(active, 'done')
                    self._save()
