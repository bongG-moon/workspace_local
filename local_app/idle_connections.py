"""On-demand connection admission; no timer, process scan, or extra CLI probe."""
from contextlib import contextmanager
from functools import wraps
import threading
import uuid

from .bridge import BridgeError


class ConnectionCapacityUnavailable(BridgeError):
    """A preflight rejection: no user message or CLI request has been sent."""
    def __init__(self, message=None):
        super().__init__('connection_capacity_wait',
                         message or '다른 업무가 3개 연결을 사용하고 있습니다. 작업이 끝난 뒤 다시 시도해 주세요.',
                         '진행 중인 업무가 끝난 뒤 이어서 실행')


def occupies_slot(item):
    bridge = item.get('bridge')
    return bool(item.get('_capacityReservation') or item.get('_dispatchClaim')
                or item.get('_restarting') or item.get('_idleReleasing')
                or bridge is not None and (not bridge.closed
                    or getattr(bridge, 'cleanup_complete', False) is not True))


def idle_candidate(app, target):
    """Call under app.lock. Inspect only metadata; never hydrate stored bodies."""
    selected = None
    for item in app.sessions.values():
        bridge = item.get('bridge')
        if (item is target or bridge is None or bridge.closed
                or item.get('state') not in {'idle', 'done'}
                or item.get('state') == 'idle' and not getattr(bridge, 'resume_id', None)
                or item.get('branch', {}).get('status') == 'pending'
                or not getattr(bridge, '_initialized', False)
                or any(item.get(key) for key in ('requests', 'choice', '_connecting', '_restarting',
                    '_modelUpdating', '_permissionUpdating', '_dispatchClaim', '_choiceAnswerClaim',
                    '_stopAdmission', '_removingFromList', '_capacityReservation', '_idleReleasing',
                    '_controlRestore', '_needsControlRestore', '_idleReleaseFailed'))
                or item['id'] in app.dispatch.steering
                or any(getattr(bridge, key, False) for key in ('busy', 'stopping', 'pending', 'tasks',
                    '_control_active', '_control_waiters', '_permission_pending', 'descendant_cleanup_uncertain'))):
            continue
        try:
            uuid.UUID(str(getattr(bridge, 'session_id', None)))
        except (ValueError, TypeError):
            continue  # Never retire a conversation that cannot be resumed exactly.
        queue = app.dispatch.queue.snapshot(item['id'])
        if (app.dispatch.error or queue.get('warning') or any(row['status'] in
                {'queued', 'dispatching', 'submitted', 'needs_review'} for row in queue['queue'])):
            continue
        # Keep the task currently being viewed when another idle task is available.
        rank = (item['id'] == getattr(app, '_viewed_session', None), item.get('updated', 0))
        if selected is None or rank < selected[0]:
            selected = (rank, item)
    return selected[1] if selected else None


def _preflight(app, item, mode, args, kwargs):
    """Reject invalid admission before retiring another task's idle process."""
    app._require_running()
    app._check_stopping(item)
    if mode == 'dispatch':
        queued = app.dispatch.queue.snapshot(item['id'])
        if (item.get('state') not in {'idle', 'done'} or item.get('trusted') is not True
                or item.get('requests') or item.get('choice') or item.get('_controlRestore')
                or any(item.get(key) for key in ('_restarting', '_connecting', '_modelUpdating', '_permissionUpdating'))
                or (item.get('verification') or {}).get('state') == 'needs-review'
                or queued.get('paused') or queued.get('warning')):
            raise ConnectionCapacityUnavailable()
        return  # The durable queue claim rechecks trust, state and its context.
    if item.get('_dispatchClaim') != kwargs.get('_dispatch_claim') and mode != 'dispatch':
        raise ValueError('대기 요청을 전송하고 있습니다. 잠시 후 다시 시도해 주세요.')
    if any(item.get(key) for key in ('_restarting', '_connecting', '_modelUpdating', '_permissionUpdating')):
        raise ValueError('연결이나 설정을 변경하고 있습니다. 잠시 후 다시 시도해 주세요.')
    if item.get('state') in {'starting', 'running', 'question', 'approval'}:
        raise ValueError('현재 진행 중인 작업을 먼저 마치거나 중지해 주세요.')
    if mode == 'send':
        text = args[0] if args else kwargs.get('text')
        attachments = args[1] if len(args) > 1 else kwargs.get('attachments')
        if not isinstance(text, str) or not text.strip() or len(text) > 32000:
            raise ValueError('요청은 1~32,000자로 입력해 주세요.')
        if not isinstance(attachments, list) or len(attachments) > 12:
            raise ValueError('파일은 한 번에 12개까지 선택할 수 있습니다.')
        app.validate_attachments(attachments)
        app._validate_choice_claim(item, kwargs.get('_choice_claim'))
    trusted = (mode == 'send' and (kwargs.get('trusted') is True or len(args) > 2 and args[2] is True))
    if not item.get('trusted') and not trusted:
        raise ValueError('업무 폴더의 설정 실행에 동의한 뒤 다시 연결해 주세요.')
    app._check_import_context(item)
    from .server import workspace_folder
    workspace_folder(item)
    if app.error:
        raise ValueError(app.error)


@contextmanager
def connection_slot(app, sid, *, mode='dispatch', args=(), kwargs=None):
    """Reserve admission under the app lock, close idle CLI outside that lock."""
    kwargs = kwargs or {}
    owner = threading.get_ident()
    reserved = False
    candidate = None
    with app.lock:
        item = app.sessions.get(sid)
        if item is None:
            app.get(sid)  # Keep the normal not-found validation and HTTP error.
        if item.get('_idleReleasing'):
            raise ConnectionCapacityUnavailable()
        nested = item.get('_capacityReservation') == owner
        if item.get('_capacityReservation') and not nested:
            raise ConnectionCapacityUnavailable('대기 요청이나 업무 연결을 준비하고 있습니다. 잠시 후 다시 시도해 주세요.')
        bridge = item.get('bridge')
        needs_slot = not app.demo and (bridge is None or bridge.closed) and not nested
        if needs_slot:
            if bridge is not None and getattr(bridge, 'cleanup_complete', False) is not True:
                if mode == 'dispatch':
                    raise ConnectionCapacityUnavailable()
                raise BridgeError('stop_cleanup_unverified', '이전 연결의 종료를 확인한 뒤 다시 연결해 주세요.', '이전 연결 종료 상태 확인')
            if not app.connection_capacity_available(item):
                _preflight(app, item, mode, args, kwargs)
                candidate = idle_candidate(app, item)
                if candidate is None:
                    raise ConnectionCapacityUnavailable()
                old = candidate['bridge']
                app._capture_control_baselines(candidate, old)
                actual = old.model_state()
                candidate['_idleRestoreControls'] = {name: actual[name] for name in
                    ('model', 'effort', 'permissionMode') if isinstance(actual.get(name), str) and actual[name]}
                candidate['_idleReleasing'] = sid
                candidate['_connecting'] = True
            item['_capacityReservation'] = owner
            reserved = True
    try:
        if candidate is not None:
            # Do not call app.stop(): that is a user cancellation and pauses work.
            try:
                cleaned = (old.close() is True and getattr(old, 'cleanup_complete', False) is True
                           and getattr(old, 'descendant_cleanup_uncertain', False) is not True)
            except (OSError, ValueError, RuntimeError):
                cleaned = False
            with app.lock:
                if not cleaned:
                    candidate.pop('_idleRestoreControls', None)
                    candidate['_idleReleaseFailed'] = True
                    raise ConnectionCapacityUnavailable('유휴 연결의 종료를 확인하지 못해 새 연결을 시작하지 않았습니다. 현재 연결 상태를 확인해 주세요.')
                candidate['sessionId'] = old.session_id
                candidate['_idleReleased'] = True
                candidate['_requireResumeIdentity'] = True
                candidate.pop('_idleReleasing', None)
                candidate['_connecting'] = False
                # Preserve completed state, transcript and queue. This event is
                # connection metadata, not a stopped or completed user request.
                app.emit(candidate['id'], 'connection_idle', {
                    'state': candidate['state'], 'connection': app.public(candidate)['connection']})
        with app.lock:
            app._require_running()
            if reserved and item.get('_capacityCancelled'):
                raise ConnectionCapacityUnavailable('연결 준비를 중지했습니다. 요청은 보내지 않았습니다.')
        yield
    finally:
        with app.lock:
            if candidate is not None and candidate.get('_idleReleasing') == sid:
                candidate.pop('_idleReleasing', None)
                candidate['_connecting'] = False
            if reserved and item.get('_capacityReservation') == owner:
                item.pop('_capacityReservation', None)
                item.pop('_capacityCancelled', None)


def with_connection_slot(method):
    @wraps(method)
    def admitted(app, sid, *args, **kwargs):
        with connection_slot(app, sid, mode=method.__name__, args=args, kwargs=kwargs):
            return method(app, sid, *args, **kwargs)
    return admitted
