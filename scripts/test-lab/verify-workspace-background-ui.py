"""Isolated real-window notification QA with synthetic events, never Claude.

Run as the signed-in Windows user with --state <new isolated directory>.
Close the actual window with X, then write uniquely named commands/*.json:
  {"action":"questions"}   two questions close together (card busy/cooldown)
  {"action":"choice"}      a business choice (first task)
  {"action":"completed"}   a normal final answer (first task)
  {"action":"approval"}    a permission request (first task)
  {"action":"clear"}       resolve only synthetic pending requests
  {"action":"status"}      refresh the technical-only status snapshot
  {"action":"tool-start"}  show a synthetic skill and concurrent file tools
  {"action":"tool-approval"} hold the same synthetic run for approval
  {"action":"tool-finish"} finish synthetic activity, keeping its history
  {"action":"quit"}        complete shutdown of this owned fixture

No test route is added to the production server. status.json and results.json
contain only fixture identifiers/statuses, never URLs, bearer tokens, user text,
personal configuration, or document contents. ui-runtime.json holds this test's
private local connection for optional HTTP probes and must not be published.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from local_app.attention import AttentionNotifier
from local_app.native_window import DesktopHost
from local_app.server import ASSETS, LocalApp, Server
from local_app.tray import WorkspaceTray


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', required=True, type=Path)
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('The native fixture requires Windows.')
    state = args.state.absolute()
    state.mkdir(parents=True, exist_ok=True)
    if state.is_symlink() or (state / 'ui-runtime.json').exists():
        parser.error('Choose a new isolated state directory for each run.')
    commands = state / 'commands'
    commands.mkdir()
    app = LocalApp(state / 'app-state', demo=True)
    app.notifier = AttentionNotifier(enabled=True)
    tasks = [app.create(str(app.state), True, title='백그라운드 알림 확인 ' + label)['id']
             for label in ('A', 'B')]
    server = Server(app)
    stopped = threading.Event()
    events, native_events, notification_attempts = [], [], []
    records_lock = threading.Lock()

    def save(name, value):
        target = state / name
        temporary = target.with_suffix('.new')
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(target)

    def native_event(event):
        app.ui_health.native(event)
        with records_lock:
            native_events.append({key: event[key] for key in ('type', 'pid', 'hwnd', 'code') if key in event})
            del native_events[:-100]

    def quit_fixture():
        if not app.close():
            return False
        stopped.set()
        server.shutdown()
        return True

    window = DesktopHost(app.notifier, server.origin + '/#token=' + app.token, app.state,
                         on_close=quit_fixture, on_event=native_event)
    app._desktop_window = window
    app._open_window_callback = window.open
    tray = app.tray = WorkspaceTray('background-qa-' + uuid.uuid4().hex,
                                    window.open, quit_fixture, ASSETS / 'app-icon.ico')

    def notification_state():
        # Read-only Windows status used by the existing native card gate. No
        # Focus Assist, notification preference, registry or policy is changed.
        value = ctypes.c_int()
        try:
            query = ctypes.windll.shell32.SHQueryUserNotificationState
            query.argtypes = [ctypes.POINTER(ctypes.c_int)]
            query.restype = ctypes.c_long
            result = int(query(ctypes.byref(value)))
            return {'hresult': result, 'stateCode': value.value if result == 0 else None,
                    'matchesNativeAllowedState': result == 0 and value.value == 5}
        except (OSError, AttributeError):
            return {'hresult': None, 'stateCode': None, 'matchesNativeAllowedState': False}

    def record_attempt(value):
        with records_lock:
            notification_attempts.append(value)
            del notification_attempts[:-100]

    original_command = window._command
    def observed_command(command, **payload):
        if command != 'notify':
            return original_command(command, **payload)
        before = notification_state()
        started = time.monotonic()
        try:
            reply = original_command(command, **payload)
        except Exception as exc:
            record_attempt({'stage': 'nativeAck', 'ok': False,
                'errorType': type(exc).__name__, 'windowsBefore': before,
                'windowsAfter': notification_state(), 'elapsedMs': int((time.monotonic() - started) * 1000)})
            raise
        reason = reply.get('notificationReason')
        record_attempt({'stage': 'nativeAck', 'ok': reply.get('ok') is True,
            'accepted': reply.get('notificationAccepted') is True,
            'reason': reason if reason in {'invalid', 'unavailable', 'busy', 'suppressed', None} else 'other',
            'windowsBefore': before, 'windowsAfter': notification_state(),
            'elapsedMs': int((time.monotonic() - started) * 1000)})
        return reply
    window._command = observed_command

    original_notify = window.notify
    def observed_notify(**payload):
        value = original_notify(**payload)
        record_attempt({'stage': 'hostOutcome',
            'outcome': 'accepted' if value is True else 'declined' if value is False
                       else 'fallback' if value is None else 'busy' if value == 'busy' else 'other',
            'notificationCapable': window.notification_available,
            'windowsState': notification_state()})
        return value
    window.notify = observed_notify

    def snapshot():
        handle = window.window_handle
        user = ctypes.windll.user32
        user.IsWindowVisible.argtypes = [ctypes.c_void_p]
        user.IsIconic.argtypes = [ctypes.c_void_p]
        with app.lock:
            notices = app.desktop.snapshot()
            rows = [{'id': item['id'], 'kind': item['kind'], 'delivery': item['delivery'],
                     'read': item['read'], 'sessionId': item['sessionId']} for item in notices['inbox']]
            value = {'fixture': True, 'claudeCalled': False, 'serverPid': os.getpid(),
                     'desktopPid': window.process.pid if window.process else None,
                     'serverAlive': server_thread.is_alive(), 'trayAvailable': tray.available,
                     'visible': bool(handle and user.IsWindowVisible(handle)),
                     'minimized': bool(handle and user.IsIconic(handle)),
                     'window': app.window_state(), 'lifecycle': app.shutdown_status(),
                     'windowsNotificationState': notification_state(),
                     'pendingCount': app.attention()['total'], 'notifications': rows,
                     'taskIds': tasks, 'states': [app.get(sid)['state'] for sid in tasks]}
        with records_lock:
            value['nativeEvents'] = list(native_events)
            value['notificationAttempts'] = list(notification_attempts)
        save('status.json', value)
        return value

    def clear(sid):
        with app.lock:
            for rid in list(app.get(sid)['requests']):
                app.emit(sid, 'request_closed', {'id': rid})
            if app.get(sid).get('choice'):
                app.emit(sid, 'choice_closed', {})
            app.emit(sid, 'status', {'state': 'idle'})

    activity_started = 0

    def inject(action):
        nonlocal activity_started
        if action.startswith('tool-'):
            sid = tasks[0]
            if action == 'tool-start':
                clear(sid)
                with app.lock:
                    app.get(sid)['lastRunId'] = uuid.uuid4().hex
                activity_started = time.time()
                app.emit(sid, 'status', {'state': 'running'})
                rows = [
                    {'id': 'fixture-skill', 'tool': 'Skill', 'action': '스킬 호출',
                     'target': 'report-summary', 'state': 'completed', 'finishedAt': activity_started + .1},
                    {'id': 'fixture-read', 'tool': 'Read', 'action': '자료 읽기',
                     'target': '분기 실적.csv', 'state': 'running'},
                    {'id': 'fixture-glob', 'tool': 'Glob', 'action': '파일 찾기',
                     'target': '참고 자료', 'state': 'requested'}]
                for row in rows:
                    app.emit(sid, 'tool_activity', {**row, 'startedAt': activity_started,
                        'runId': app.get(sid)['lastRunId']})
            elif action == 'tool-approval':
                app.emit(sid, 'request', {'id': 'fixture-tool-approval', 'tool': 'Read',
                    'input': {'file_path': '분기 실적.csv'}})
            else:
                for rid in list(app.get(sid)['requests']):
                    app.emit(sid, 'request_closed', {'id': rid})
                app.emit(sid, 'status', {'state': 'running'})
                for row in list(app.get(sid).get('toolActivity', [])):
                    if row['state'] in {'requested', 'running'}:
                        app.emit(sid, 'tool_activity', {**row, 'state': 'completed', 'finishedAt': time.time()})
                app.emit(sid, 'assistant', {'text': '활동 표시 검증을 마쳤습니다. 가상 기록이며 실제 Claude는 호출하지 않았습니다.'})
                app.emit(sid, 'result', {})
            return
        if action == 'clear':
            for sid in tasks:
                clear(sid)
            return
        for sid in tasks if action == 'questions' else tasks[:1]:
            clear(sid)
            with app.lock:
                app.get(sid)['lastRunId'] = uuid.uuid4().hex
            app.emit(sid, 'status', {'state': 'running'})
            if action in {'questions', 'question', 'approval'}:
                question = action != 'approval'
                app.emit(sid, 'request', {'id': 'fixture-' + uuid.uuid4().hex,
                    'tool': 'AskUserQuestion' if question else '가상 승인 확인',
                    'input': {'questions': [{'question': '어떤 형태로 정리할까요?', 'multiSelect': False,
                        'options': [{'label': '핵심 요약', 'description': '가상 선택 A'},
                                    {'label': '항목별 정리', 'description': '가상 선택 B'}]}]} if question else
                             {'내용': '실제 파일 변경이나 외부 연결이 없는 알림 검증입니다.'}})
            elif action == 'choice':
                app.emit(sid, 'choice', {'schemaVersion': 1, 'id': 'fixture-' + uuid.uuid4().hex,
                    'kind': 'html-report-style', 'responseMode': 'next-user-message',
                    'question': '보고서 형태를 선택해 주세요.', 'allowCustom': True,
                    'options': [{'id': 'minimalism', 'label': '미니멀리즘'},
                                {'id': 'editorial', 'label': '에디토리얼'}]})
                app.emit(sid, 'result', {})
            else:
                app.emit(sid, 'assistant', {'text': '가상 알림 검증을 완료했습니다. 실제 Claude는 호출하지 않았습니다.'})
                app.emit(sid, 'result', {})

    def control_loop():
        processed = set()
        while not stopped.wait(.25):
            for source in sorted(commands.glob('*.json'))[:200]:
                if source.name in processed:
                    continue
                if time.time() - source.stat().st_mtime < .1:
                    continue  # Let a small file write finish before consuming it.
                processed.add(source.name)
                try:
                    if source.is_symlink() or source.stat().st_size > 4096:
                        raise ValueError('Invalid fixture command.')
                    command = json.loads(source.read_text(encoding='utf-8-sig'))
                    action = command['action']
                    if set(command) != {'action'} or action not in {
                            'questions', 'question', 'approval', 'choice', 'completed', 'clear', 'status', 'quit',
                            'tool-start', 'tool-approval', 'tool-finish'}:
                        raise ValueError('Invalid fixture command.')
                    if action == 'quit':
                        quit_fixture()
                    elif action != 'status':
                        inject(action)
                    events.append({'commandFile': source.name, 'action': action, 'ok': True})
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    events.append({'commandFile': source.name, 'ok': False, 'error': type(exc).__name__})
                save('results.json', {'fixture': True, 'claudeCalled': False, 'events': events[-200:]})
            snapshot()

    server_thread = threading.Thread(target=server.serve_forever, daemon=True, name='qa-http')
    control_thread = None
    try:
        tray.start()
        if not tray.available:
            raise RuntimeError('The test tray could not start; X-to-tray is not testable.')
        window.background = True
        server_thread.start()
        window.open()
        app.dispatch.start()
        save('ui-runtime.json', {'url': server.origin + '/#token=' + app.token, 'pid': os.getpid()})
        snapshot()
        control_thread = threading.Thread(target=control_loop, daemon=True, name='qa-file-commands')
        control_thread.start()
        print(json.dumps({'ready': True, 'fixture': True, 'state': str(state)}, ensure_ascii=False), flush=True)
        while not stopped.wait(.5):
            if not server_thread.is_alive() or app.shutdown_status()['closed']:
                stopped.set()
    except KeyboardInterrupt:
        quit_fixture()
    finally:
        app.close()
        stopped.set()
        if control_thread:
            control_thread.join(3)
        if server_thread.is_alive():
            server.shutdown()
            server_thread.join(3)
        window.close()
        tray.stop()
        server.server_close()
        snapshot()
        save('results.json', {'fixture': True, 'claudeCalled': False, 'closed': True, 'events': events[-200:]})


if __name__ == '__main__':
    main()
