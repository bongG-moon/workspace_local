"""Integrate persisted follow-ups with the app's existing trusted send path."""
from __future__ import annotations

from datetime import datetime, timedelta
import ntpath
import threading
import time

from .work_queue import WorkQueue
from .bridge import ControlRestoreRequired


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
                       'control_restore_required': '모델·Effort·승인 설정을 확인해 주세요. 요청은 아직 보내지 않았습니다. 설정을 고친 뒤 이어 실행하면 현재 선택한 설정을 사용합니다.',
                       'delivery_unknown': '전송 여부가 확실하지 않은 요청이 있습니다. 대화에서 결과를 확인한 뒤 대기 항목을 정리해 주세요.'}
            result['pauseReason'] = reasons.get(result.get('reason'), result.get('reason'))
            result['policy']['message'] = 'PC가 켜져 있고 앱이 실행 중일 때 동작합니다. 앱 재시작 후에는 내용을 확인하고 이어 실행해 주세요. 놓친 예약은 몰아서 실행하지 않습니다.'
            result['trusted'] = item.get('trusted') is True
            return result

    def start(self):
        if self.thread is None and not self.stopped.is_set():
            self.thread = threading.Thread(target=self._run, daemon=True, name='workspace-dispatch')
            self.thread.start()

    def overview(self):
        """Read bounded schedule/navigation metadata without hydrating conversations.

        This view never ticks, claims, reconnects, trusts, or resumes a task.
        WorkQueue already caps persisted schedules at 100 and active requests at
        500; take one consistent snapshot instead of copying it for each task.
        """
        with self.app.lock:
            with self.queue.lock:
                state = self.queue.snapshot()
                holds = dict(self.queue.data['holds'])
            rows = []
            queued_by_schedule, queued_by_session = {}, {}
            for request in state['queue']:
                queued_by_session.setdefault(request['sessionId'], []).append(request)
                if request.get('scheduleId'):
                    queued_by_schedule[request['scheduleId']] = request
            counts = dict(total=0, active=0, paused=0, attention=0, completed=0)
            warning = state.get('warning') or self.error
            for schedule in state['schedules']:
                sid = schedule['sessionId']
                item = self.app.sessions.get(sid)
                if (item is None or item.get('_removingFromList') or
                        self.app.session_visibility.contains(sid)):
                    continue
                pending = queued_by_schedule.get(schedule['id'])
                session_queue = queued_by_session.get(sid, [])
                last = schedule.get('lastStatus')
                next_at = schedule.get('nextRunAt')
                paused = schedule['pausedByUser']
                consumed = not pending and next_at is None and not schedule['enabled']
                reason = ''
                if not paused and not consumed:
                    reason = self._overview_wait_reason(item, holds.get(sid), session_queue, warning)
                if pending and pending['status'] == 'needs_review':
                    category, label = 'attention', '전송 확인 필요'
                elif paused:
                    category, label = 'paused', '일시 정지'
                elif reason and (
                        warning or holds.get(sid) or not item.get('trusted') or item.get('_controlRestore') or
                        item.get('state') in {'error', 'stopped', 'approval', 'question'} or
                        item.get('requests') or item.get('choice') or
                        (item.get('verification') or {}).get('state') == 'needs-review' or
                        any(row['status'] == 'needs_review' for row in session_queue)):
                    category, label = 'attention', '확인 필요'
                elif last in {'missed', 'queue_full', 'needs_review', 'failed', 'error', 'stopped'} and not pending:
                    category, label = 'attention', '놓친 실행' if last == 'missed' else '확인 필요'
                elif consumed:
                    category, label = 'completed', '완료' if last == 'done' else '실행 종료'
                else:
                    category, label = 'active', {'queued': '실행 대기', 'dispatching': '전송 중',
                                                 'submitted': '실행 중'}.get(pending['status'] if pending else '', '예약 중')
                if pending and pending['status'] == 'needs_review':
                    reason = '전송 여부를 대화에서 확인해 주세요. 자동으로 다시 보내지 않습니다.'
                elif paused and pending and pending['status'] in {'dispatching', 'submitted'}:
                    reason = '다음 예약을 멈췄습니다. 이미 시작한 요청은 계속 진행됩니다.'
                elif pending and pending['status'] in {'dispatching', 'submitted'} and category == 'active':
                    reason = ''
                elif not reason and last == 'missed':
                    reason = '예약 시각을 놓쳤습니다. 지난 실행은 자동으로 다시 보내지 않습니다.'
                elif not reason and last == 'queue_full':
                    reason = '이어 할 일이 가득 차 실행하지 못했습니다. 대기 목록을 확인해 주세요.'
                text = ' '.join(str(schedule.get('text') or '').split())
                rows.append({
                    'id': schedule['id'], 'sessionId': sid,
                    'title': str(item.get('title') or '이름 없는 업무')[:180],
                    'workspaceLabel': ntpath.basename(str(item.get('workspace') or '').rstrip('/\\'))[:100],
                    'requestSummary': text[:119] + '…' if len(text) > 120 else text,
                    'attachmentCount': len(schedule.get('attachments') or []),
                    'kind': schedule['kind'], 'time': schedule.get('time'),
                    'weekdays': list(schedule.get('weekdays') or []), 'runAt': schedule['runAt'],
                    'nextRunAt': next_at, 'enabled': schedule['enabled'], 'pausedByUser': paused,
                    'category': category, 'statusLabel': label, 'waitReason': reason,
                    'lastRun': {'dueAt': schedule.get('lastDueAt'), 'status': last},
                })
                counts['total'] += 1
                counts[category] += 1
            rows.sort(key=lambda row: (row['nextRunAt'] is None, row['nextRunAt'] or 0,
                                       row['title'].casefold(), row['id']))
            return {'revision': state['revision'], 'schedules': rows, 'counts': counts,
                    'warning': warning, 'policy': state['policy']}

    def _overview_wait_reason(self, item, hold, requests, warning):
        if warning:
            return warning
        if hold:
            return {'restart': '앱을 다시 열었습니다. 예약 관리에서 내용을 확인하고 이어 실행해 주세요.',
                    'stopped': '중지한 업무입니다. 예약 관리에서 이어 실행해 주세요.',
                    'error': '업무 상태를 확인한 뒤 이어 실행해 주세요.',
                    'settings_changed': '변경한 모델·Effort·승인 설정을 확인한 뒤 이어 실행해 주세요.',
                    'control_restore_required': '모델·Effort·승인 설정을 확인한 뒤 이어 실행해 주세요.',
                    'delivery_unknown': '전송 여부가 확실하지 않은 요청을 확인해 주세요.'}.get(hold, '예약 관리에서 업무 상태를 확인해 주세요.')
        if not item.get('trusted'):
            return '업무 폴더를 확인한 뒤 이어 실행해 주세요.'
        if item.get('_controlRestore'):
            return '모델·Effort·승인 설정을 확인해 주세요.'
        if any(row['status'] == 'needs_review' for row in requests):
            return '전송 여부가 확실하지 않은 요청을 확인해 주세요.'
        if item.get('state') in {'approval', 'question'} or item.get('requests') or item.get('choice'):
            return '현재 업무의 승인 또는 답변을 기다립니다.'
        if (item.get('verification') or {}).get('state') == 'needs-review':
            return '현재 업무의 결과 확인을 기다립니다.'
        if item.get('_connecting') or item.get('_modelUpdating'):
            return '연결 또는 설정 변경이 끝나기를 기다립니다.'
        if item.get('state') in {'starting', 'running'}:
            return '현재 업무가 끝난 뒤 순서대로 실행합니다.'
        if item.get('state') in {'error', 'stopped'}:
            return '업무 상태를 확인한 뒤 이어 실행해 주세요.'
        if not self.app.connection_capacity_available(item):
            return '다른 업무가 사용 중인 Claude 연결이 비기를 기다립니다.'
        if requests:
            return '앞선 이어 할 일이 끝난 뒤 순서대로 실행합니다.'
        return ''

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
                    # Queue eligibility needs only navigation/runtime metadata.
                    # Hydrate the bounded UI mirror only when send actually runs.
                    item = self.app.sessions.get(sid)
                    if (item is None or item.get('_removingFromList') or
                            self.app.session_visibility.contains(sid)):
                        continue
                    pending = self.steering.get(sid)
                    if pending:
                        bridge = item.get('bridge')
                        if item['state'] == 'stopped' and (bridge is None or getattr(bridge, 'cleanup_complete', False) is True
                                or (getattr(bridge, 'stop_state', None) == 'stopped'
                                    and not bridge.closed and not getattr(bridge, 'stopping', False))):
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
                    # A claim also reserves a connection slot. Keep admission,
                    # durable claim and reservation under the same app lock used
                    # by ordinary connect/send, so other tasks cannot take it.
                    if item.get('_stopAdmission') or self.app.stop_state(item) in {'stopping', 'failed'}:
                        continue  # Do not claim or consume work during an unconfirmed stop.
                    if not self.app.connection_capacity_available(item):
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
                except ControlRestoreRequired:
                    # This typed preflight error guarantees send() has not
                    # appended or delivered any part of the queued request.
                    self.queue.defer_unsubmitted(claim['id'])
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
            if item.get('_restarting'):
                raise ValueError('Claude 연결을 재시작하고 있습니다. 끝난 뒤 대기·예약 내용을 변경해 주세요.')
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
                self.app._check_stopping(item)
                # Explicit re-confirmation adopts only app-selected controls.
                current = self.queue.snapshot(sid)
                if item.get('_controlRestore'):
                    raise ValueError('모델·Effort·승인 설정을 먼저 확인해 주세요. 대기 요청은 아직 보내지 않았습니다.')
                if current['reason'] in {'settings_changed', 'control_restore_required'}:
                    self.queue.confirm_context(sid, self.context(item))
                if item['state'] in {'error', 'stopped'}:
                    bridge = item.get('bridge')
                    restored = (current['reason'] == 'control_restore_required' and bridge is not None
                                and not bridge.closed and not getattr(bridge, 'stopping', False))
                    soft_stopped = (bridge is not None and getattr(bridge, 'stop_state', None) == 'stopped'
                                    and not bridge.closed and not getattr(bridge, 'stopping', False))
                    if bridge and getattr(bridge, 'cleanup_complete', False) is not True and not (restored or soft_stopped):
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
                    self.queue.update_schedule(sid, identifier, text, paths, **values, enabled=schedule.get('enabled'))
            elif action == 'schedule_cancel':
                self.queue.cancel_schedule(sid, identifier)
            elif action in {'schedule_pause', 'schedule_resume'}:
                self.queue.set_schedule_enabled(sid, identifier, action == 'schedule_resume')
            else:
                raise ValueError('대기·예약 작업을 확인해 주세요.')
            return {'ok': True, **self.snapshot(sid)}
