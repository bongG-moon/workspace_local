"""Integrate persisted follow-ups with the app's existing trusted send path."""
from __future__ import annotations

from datetime import datetime, timedelta
import threading
import time

from .work_queue import WorkQueue


class DispatchController:
    def __init__(self, app):
        self.app = app
        self.queue = WorkQueue(app.state)
        self.stopped = threading.Event()
        self.thread = None
        self.steering = {}
        self.error = None

    @staticmethod
    def context(item):
        # Capture only choices made in this connection. None means inherit the
        # CLI's settings; never snapshot or rewrite a personal settings file.
        choices = item.get('_sessionControls', {})
        return {'workspace': item['workspace'], **{name: choices.get(name) for name in ('model', 'effort', 'permissionMode')}}

    def snapshot(self, sid):
        with self.app.lock:
            item = self.app.get(sid)
            result = self.queue.snapshot(sid)
            for row in result['queue']:
                row['state'] = row.get('state', row['status'])
            for row in result['schedules']:
                row['nextRunAt'] = row.get('nextRunAt', row.get('nextAt'))
                row['lastRun'] = row.get('lastRun', {'dueAt': row.get('lastDueAt'), 'status': row.get('lastStatus')})
            result['steer'] = {'supported': not self.app.demo, 'mode': 'interrupt_resume',
                               'reason': '현재 요청을 중지한 뒤 같은 대화에서 이어갑니다.'}
            result['warning'] = result.get('warning') or self.error
            reasons = {'stopped': '작업을 중지했습니다. 준비되면 이어 실행해 주세요.',
                       'error': '현재 작업을 확인한 뒤 이어 실행해 주세요.',
                       'restart': '앱을 다시 열었습니다. 업무 폴더와 대기·예약 내용을 확인한 뒤 이어 실행해 주세요.',
                       'settings_changed': '모델·Effort·승인 설정이 바뀌었습니다. 이어 실행하면 대기 요청과 활성 예약에 현재 선택한 설정을 사용합니다.',
                       'delivery_unknown': '전송 여부가 확실하지 않은 요청이 있습니다. 대화에서 결과를 확인한 뒤 대기 항목을 정리해 주세요.'}
            result['pauseReason'] = reasons.get(result.get('reason'), result.get('reason'))
            result['policy']['message'] = 'PC가 켜져 있고 앱이 실행 중일 때 동작합니다. 앱 재시작 후에는 내용을 확인하고 이어 실행해 주세요. 놓친 예약은 몰아서 실행하지 않습니다.'
            result['trusted'] = item.get('trusted') is True
            return result

    def start(self):
        if self.thread is None and not self.stopped.is_set():
            self.thread = threading.Thread(target=self._run, daemon=True, name='workspace-dispatch')
            self.thread.start()

    def stop(self):
        self.stopped.set()

    def _run(self):
        while not self.stopped.wait(.5):
            try:
                self.pump()
            except (ValueError, OSError, RuntimeError):
                # Storage errors stop automatic work, not the whole UI. No
                # failed/uncertain send is retried by this loop.
                self.error = '대기·예약 실행을 멈췄습니다. 해당 업무의 상태를 확인해 주세요.'

    def observe(self, sid, kind, data):
        if kind in {'result', 'error'} or kind == 'status' and data.get('state') == 'stopped':
            try:
                self.queue.observe(sid, kind, data)
            except (ValueError, OSError):
                self.error = '대기·예약 기록을 저장하지 못해 자동 실행을 멈췄습니다.'

    def manual_stop(self, sid):
        with self.app.lock:
            self.steering.pop(sid, None)
            # Invalidate a claim before send() can reacquire the session lock.
            # A later ambiguous delivery is retained for review, never replayed.
            self.app.get(sid).pop('_dispatchClaim', None)
            self.queue.pause(sid, 'stopped')

    def pump(self):
        if self.stopped.is_set() or self.error:
            return
        self.queue.tick()
        with self.app.lock:
            session_ids = list(self.app.sessions)
        for sid in session_ids:
            if self.stopped.is_set():
                return
            with self.app.operation():
                with self.app.lock:
                    item = self.app.get(sid)
                    pending = self.steering.get(sid)
                    if pending:
                        bridge = item.get('bridge')
                        if item['state'] == 'stopped' and (bridge is None or getattr(bridge, 'cleanup_complete', False) is True):
                            # Explicit "now" cancels the interrupted queued turn;
                            # it does not silently retry it alongside the new one.
                            for row in self.queue.snapshot(sid)['queue']:
                                if row['id'] in pending['interrupted'] and row['status'] == 'needs_review':
                                    self.queue.cancel(sid, row['id'])
                            self.queue.resume(sid)
                            item['state'] = 'idle'
                            self.steering.pop(sid, None)
                        elif time.monotonic() - pending['created'] > 30 or item['state'] == 'error':
                            self.steering.pop(sid, None)
                            self.queue.pause(sid, 'stopped')
                        else:
                            continue
                    claim = self.queue.claim(sid, item, self.context(item))
                    if claim is not None:
                        # Reserve the normal-send admission while control
                        # restoration waits without holding the app lock.
                        item['_dispatchClaim'] = claim['id']
                if claim is None:
                    continue
                try:
                    self.app.send(sid, claim['text'], claim['attachments'], _dispatch_claim=claim['id'])
                    with self.app.lock:
                        self.queue.dispatched(claim['id'], self.app.get(sid).get('lastRunId'))
                except (ValueError, OSError, RuntimeError):
                    self.queue.failed(claim['id'])
                finally:
                    with self.app.lock:
                        self.app.get(sid).pop('_dispatchClaim', None)

    @staticmethod
    def first_run(schedule):
        if schedule.get('kind') == 'once':
            return schedule.get('runAt')
        wall = schedule.get('time')
        try:
            parsed = datetime.strptime(wall, '%H:%M')
            if parsed.strftime('%H:%M') != wall:
                raise ValueError()
        except (TypeError, ValueError):
            raise ValueError('예약할 시간을 HH:MM 형식으로 선택해 주세요.') from None
        days = schedule.get('weekdays', [])
        if schedule.get('kind') == 'weekly' and (not isinstance(days, list) or not days or
                any(type(day) is not int or day not in range(7) for day in days)):
            raise ValueError('예약할 요일을 선택해 주세요.')
        now = datetime.now()
        for offset in range(8):
            candidate = (now + timedelta(days=offset)).replace(hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0)
            if candidate > now and (schedule.get('kind') != 'weekly' or candidate.weekday() in days):
                return candidate.timestamp()
        raise ValueError('다음 예약 시각을 확인해 주세요.')

    def action(self, sid, data):
        with self.app.lock:
            item = self.app.get(sid)
            action, identifier = data.get('action'), data.get('requestId')
            if action in {'enqueue', 'steer', 'update', 'schedule', 'schedule_update', 'resume', 'schedule_resume'}:
                if not item.get('trusted'):
                    raise ValueError('업무 폴더를 다시 확인한 뒤 대기·예약을 실행해 주세요.')
            if action in {'enqueue', 'steer', 'update', 'schedule', 'schedule_update'}:
                paths = self.app.validate_attachments(data.get('attachments', []))
                text, context = data.get('text'), self.context(item)
            if action in {'enqueue', 'steer'}:
                if action == 'steer' and self.app.demo:
                    raise ValueError('체험 모드에서는 끝나고 이어서 보내기를 사용해 주세요.')
                if item.get('_dispatchClaim') or item.get('_connecting') or item.get('_modelUpdating'):
                    raise ValueError('연결을 준비하고 있습니다. 잠시 뒤 다시 보내 주세요.')
                if action == 'steer' and sid in self.steering:
                    raise ValueError('앞서 보낸 요청으로 전환하고 있습니다. 잠시 기다려 주세요.')
                before = self.queue.snapshot(sid)
                client_id = data.get('clientRequestId')
                if client_id is not None and (not isinstance(client_id, str) or not client_id or len(client_id) > 150):
                    raise ValueError('요청 식별자를 확인해 주세요.')
                row = self.queue.enqueue(sid, text, paths, context, client_id=action + ':' + client_id if client_id else None)
                # ACK retry must not interrupt the next task a second time.
                existing = any(entry['id'] == row['id'] for entry in before['queue'])
                if action == 'steer' and not existing and row['status'] == 'queued':
                    pending = [entry['id'] for entry in self.queue.snapshot(sid)['queue'] if entry['status'] == 'queued']
                    self.queue.reorder(sid, [row['id'], *(key for key in pending if key != row['id'])])
                    if item['state'] in {'starting', 'running', 'approval', 'question'}:
                        self.steering[sid] = {'created': time.monotonic(), 'interrupted': {
                            entry['id'] for entry in before['queue'] if entry['status'] in {'dispatching', 'submitted'}}}
                        bridge = item.get('bridge')
                        if bridge is None:
                            self.steering.pop(sid, None)
                            raise ValueError('현재 연결을 확인할 수 없습니다. 대기 요청은 보존했습니다.')
                        bridge.interrupt()
            elif action == 'update':
                self.queue.edit(sid, identifier, text, paths, context)
            elif action == 'cancel':
                self.queue.cancel(sid, identifier)
            elif action == 'reorder':
                self.queue.reorder(sid, data.get('order'))
            elif action == 'resume':
                # Explicit re-confirmation adopts only app-selected controls.
                current = self.queue.snapshot(sid)
                if current['reason'] == 'settings_changed':
                    self.queue.confirm_context(sid, self.context(item))
                if item['state'] in {'error', 'stopped'}:
                    bridge = item.get('bridge')
                    if bridge and getattr(bridge, 'cleanup_complete', False) is not True:
                        raise ValueError('이전 연결의 종료를 확인한 뒤 이어 실행해 주세요.')
                    item['state'] = 'idle'
                self.queue.resume(sid)
            elif action in {'schedule', 'schedule_update'}:
                schedule = data.get('schedule')
                if not isinstance(schedule, dict):
                    raise ValueError('예약 내용을 확인해 주세요.')
                values = dict(kind=schedule.get('kind'), run_at=self.first_run(schedule), time=schedule.get('time'),
                              weekdays=schedule.get('weekdays'), context=context)
                if action == 'schedule':
                    self.queue.add_schedule(sid, text, paths, **values, client_id=data.get('clientRequestId'))
                else:
                    self.queue.update_schedule(sid, identifier, text, paths, **values, enabled=schedule.get('enabled', True))
            elif action == 'schedule_cancel':
                self.queue.cancel_schedule(sid, identifier)
            elif action in {'schedule_pause', 'schedule_resume'}:
                self.queue.set_schedule_enabled(sid, identifier, action == 'schedule_resume')
            else:
                raise ValueError('대기·예약 작업을 확인해 주세요.')
            return {'ok': True, **self.snapshot(sid)}
