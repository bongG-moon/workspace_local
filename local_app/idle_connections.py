"""On-demand connection admission; no timer, process scan, or extra CLI probe."""
from contextlib import contextmanager
from functools import wraps
import threading
import uuid
import time
import secrets
import hashlib
import json

from .bridge import BridgeError

MAX_CONNECTIONS = 5


class ConnectionCapacityUnavailable(BridgeError):
    """A preflight rejection: no user message or CLI request has been sent."""
    def __init__(self, message=None):
        super().__init__('connection_capacity_wait',
                         message or f'다른 업무가 {MAX_CONNECTIONS}개 연결을 사용하고 있습니다. 연결을 교체하거나 작업이 끝난 뒤 다시 시도해 주세요.',
                         '진행 중인 업무가 끝난 뒤 이어서 실행')


def replacement_candidate(app, target):
    """Exact live connection, oldest first; transitions are never interrupted."""
    candidates = []
    for item in app.sessions.values():
        bridge = item.get('bridge')
        if (item is target or bridge is None or bridge.closed
                or item.get('branch', {}).get('status') == 'pending'
                or any(item.get(key) for key in ('_connecting', '_restarting', '_modelUpdating',
                    '_permissionUpdating', '_dispatchClaim', '_choiceAnswerClaim', '_stopAdmission',
                    '_removingFromList', '_capacityReservation', '_idleReleasing', '_controlRestore'))
                or item['id'] in app.dispatch.steering
                or any(getattr(bridge, key, False) for key in ('stopping', '_control_active',
                    '_control_waiters', 'descendant_cleanup_uncertain'))):
            continue
        try:
            uuid.UUID(str(bridge.session_id))
        except (ValueError, TypeError, AttributeError):
            continue
        candidates.append(item)
    return min(candidates, key=lambda row: (getattr(row['bridge'], '_workspace_connected_at', 0), row['id']), default=None)


def replacement_offer(app, target, fingerprint):
    error = ConnectionCapacityUnavailable()
    candidate = replacement_candidate(app, target)
    if candidate is not None:
        bridge = candidate['bridge']
        if not hasattr(bridge, '_workspace_generation'):
            bridge._workspace_generation = secrets.token_hex(16)
        token = secrets.token_urlsafe(24)
        # One bounded, ephemeral proposal per task; no persisted process handles.
        target['_replacementOffer'] = (token, time.monotonic() + 120, candidate['id'], bridge._workspace_generation,
                                       candidate.get('lastRunId'), candidate.get('state'), fingerprint)
        error.replacement = {'token': token, 'title': str(candidate.get('title') or '이름 없는 업무')[:180],
                             'state': candidate.get('state'), 'targetTitle': str(target.get('title') or '새 업무')[:180],
                             'connectedAt': getattr(bridge, '_workspace_connected_at', None), 'limit': MAX_CONNECTIONS}
    return error


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
    if mode == 'restart_connection' and type(kwargs.get('stop_running', False)) is not bool:
        raise ValueError('진행 중인 업무의 중지 여부를 확인해 주세요.')
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
def connection_slot(app, sid, *, mode='dispatch', args=(), kwargs=None, offer_replacement=False, replacement_token=None):
    """Reserve admission under the app lock, close idle CLI outside that lock."""
    kwargs = kwargs or {}
    owner = threading.get_ident()
    reserved = False
    candidate = None
    explicit = False
    fingerprint = (hashlib.sha256(json.dumps([mode, args, kwargs], sort_keys=True, default=str).encode()).hexdigest()
                   if offer_replacement or replacement_token is not None else None)
    with app.lock:
        item = app.sessions.get(sid)
        if item is None:
            app.get(sid)  # Keep the normal not-found validation and HTTP error.
        approved = None
        if replacement_token is not None:
            proposal = item.pop('_replacementOffer', None)
            if (not offer_replacement or not isinstance(replacement_token, str) or not proposal
                    or not secrets.compare_digest(proposal[0], replacement_token)
                    or proposal[1] < time.monotonic() or proposal[6] != fingerprint):
                raise ConnectionCapacityUnavailable('연결 교체 확인이 만료되었어요. 요청을 다시 보내 종료할 업무를 확인해 주세요.')
            approved = app.sessions.get(proposal[2])
            if (approved is None or getattr(approved.get('bridge'), '_workspace_generation', None) != proposal[3]
                    or approved.get('lastRunId') != proposal[4] or approved.get('state') != proposal[5]
                    or replacement_candidate(app, item) is not approved):
                raise ConnectionCapacityUnavailable('종료할 업무의 상태가 바뀌었어요. 요청을 다시 보내 확인해 주세요.')
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
                    if approved is not None:
                        candidate, explicit = approved, True
                    else:
                        raise replacement_offer(app, item, fingerprint) if offer_replacement else ConnectionCapacityUnavailable()
                old = candidate['bridge']
                resumable = bool(getattr(old, 'resume_id', None) or candidate.get('lastRunId')
                                 or candidate.get('messages') or candidate.get('state') != 'idle')
                app._capture_control_baselines(candidate, old)
                actual = old.model_state()
                candidate['_idleRestoreControls'] = {name: actual[name] for name in
                    ('model', 'effort', 'permissionMode') if isinstance(actual.get(name), str) and actual[name]}
                if explicit:
                    app.dispatch.manual_stop(candidate['id'])
                    # Ignore late callbacks from the retired child while closing.
                    candidate['bridge'] = None
                candidate['_idleReleasing'] = sid
                candidate['_connecting'] = True
            item['_capacityReservation'] = owner
            reserved = True
    try:
        if candidate is not None:
            # Idle reclamation preserves queues; explicit replacement already
            # paused them under the admission lock before closing the child.
            try:
                cleaned = (old.close() is True and getattr(old, 'cleanup_complete', False) is True
                           and getattr(old, 'descendant_cleanup_uncertain', False) is not True)
            except (OSError, ValueError, RuntimeError):
                cleaned = False
            with app.lock:
                if not cleaned:
                    if explicit:
                        candidate['bridge'] = old
                        app.emit(candidate['id'], 'error', {'code': 'connection_replacement_cleanup_failed',
                            'message': '연결 종료를 확인하지 못했어요. 이 업무의 연결 상태를 확인해 주세요. 이어 할 일과 예약은 일시정지됩니다.',
                            'resumeSessionId': old.session_id if resumable else None})
                    else:
                        candidate.pop('_idleRestoreControls', None)
                    candidate['_idleReleaseFailed'] = True
                    raise ConnectionCapacityUnavailable('기존 연결의 종료를 확인하지 못해 새 연결을 시작하지 않았습니다. 현재 연결 상태를 확인해 주세요.')
                if explicit:
                    candidate['bridge'] = old
                    for request_id in list(candidate.get('requests', {})):
                        app.emit(candidate['id'], 'request_closed', {'id': request_id})
                    if candidate.get('choice'):
                        app.emit(candidate['id'], 'choice_closed', {'id': candidate['choice']['id']})
                    app.emit(candidate['id'], 'status', {'state': 'stopped', 'label': '새 업무 연결을 위해 중지했어요 · 이어 할 일과 예약은 일시정지됩니다'})
                candidate['sessionId'] = old.session_id if resumable else None
                candidate['_idleReleased'] = True
                candidate['_requireResumeIdentity'] = resumable
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
        offer = kwargs.pop('_offer_replacement', False)
        token = kwargs.pop('_replacement_token', None)
        with connection_slot(app, sid, mode=method.__name__, args=args, kwargs=kwargs,
                             offer_replacement=offer, replacement_token=token):
            try:
                return method(app, sid, *args, **kwargs)
            finally:
                with app.lock:
                    bridge = app.sessions.get(sid, {}).get('bridge')
                    if bridge is not None and not hasattr(bridge, '_workspace_connected_at'):
                        bridge._workspace_connected_at = time.time()
    return admitted
