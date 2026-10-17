"""Exercise restart readiness through isolated, authenticated LocalApp HTTP servers.

No Claude, updater download, personal app state, or native process is started.
The native host and browser lifecycle are represented by the same readiness
events the real host/browser send; all routing, draft handoff, readiness state,
runtime capability changes, and coordinator waiting run production code.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
import uuid


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    output = args.output.absolute()
    output.mkdir(parents=True, exist_ok=False)
    os.environ['LOCALAPPDATA'] = str(output / 'local')
    os.environ['CLAUDE_CONFIG_DIR'] = str(output / 'claude')
    (output / 'claude').mkdir()

    from local_app.server import LocalApp, Server, WORKSPACE_VERSION
    from local_app.upgrade_launcher import Client, HandoffError, wait_replacement

    results = []

    def record(name, **details):
        results.append({'check': name, 'passed': True, **details})
        print('PASS ' + name, flush=True)

    @contextmanager
    def live(state, *, desktop=True):
        app = LocalApp(state, demo=True)
        if desktop:
            app._desktop_window = SimpleNamespace(notification_available=False)
        server = Server(app)
        runtime = state / 'runtime.json'
        runtime.write_text(json.dumps({'url': server.origin + '/#token=' + app.token}), encoding='utf-8')
        def serve():
            try:
                server.serve_forever(poll_interval=.02)
            finally:
                server.server_close()
                runtime.unlink(missing_ok=True)
        worker = threading.Thread(target=serve, name='update-qa-http', daemon=True)
        worker.start()
        client = Client(server.origin, app.token)
        try:
            yield SimpleNamespace(app=app, server=server, runtime=runtime, worker=worker, client=client)
        finally:
            server.shutdown()
            worker.join(3)
            app.close()
            if worker.is_alive():
                raise AssertionError('Owned HTTP server did not stop')

    def call(fixture, path, value=None, expected=200):
        status, result = fixture.client.request(path, value)
        assert status == expected, (path, status, result)
        return result

    def health(fixture, document, event='document', status='pending'):
        return call(fixture, '/api/ui-health', {
            'documentId': document, 'event': event, 'module': 'app', 'status': status,
        })

    def wait(fixture, previous_token, **changes):
        options = {'previous_token': previous_token, 'demo': True,
                   'target_version': WORKSPACE_VERSION, 'execution_mode': 'normal',
                   'timeout': 3}
        options.update(changes)
        return wait_replacement(fixture.runtime, **options)

    def rejected(fixture, previous_token, **changes):
        began = time.monotonic()
        try:
            wait(fixture, previous_token, **changes)
        except HandoffError:
            return round(time.monotonic() - began, 3)
        raise AssertionError('Unexpected ready acknowledgement')

    state = output / 'handoff-state'
    workspace = output / 'workspace'
    workspace.mkdir()
    attachment = workspace / 'report.txt'
    attachment.write_text('Synthetic attachment preserved across restart.', encoding='utf-8')
    draft = None
    old_token = None
    with live(state) as old:
        initial = call(old, '/api/bootstrap')
        old_token = old.app.token
        assert initial['window']['startupState'] == 'pending'
        doc = 'old-window-' + uuid.uuid4().hex
        health(old, doc)
        health(old, doc, 'startup', 'ready')
        ready = call(old, '/api/bootstrap')
        assert ready['window']['startupState'] == 'ready'
        old_launch = ready['window']['launchId']

        unauthorized = Client(old.server.origin, 'x' * 43)
        status, _ = unauthorized.request('/api/ui-health', {
            'documentId': 'untrusted-document', 'event': 'startup', 'module': 'app', 'status': 'failed'})
        assert status == 403
        assert call(old, '/api/bootstrap')['window']['startupState'] == 'ready'
        record('Authenticated readiness events only')

        work = call(old, '/api/create', {'workspace': str(workspace), 'trusted': True, 'title': 'Update QA'})
        sid = work['id']
        # Simulate the CLI waiting for an answer; no actual model is invoked.
        old.app.sessions[sid]['state'] = 'question'
        cancelled_id = uuid.uuid4().hex
        request = {'requestId': cancelled_id, 'targetVersion': WORKSPACE_VERSION}
        waiting = call(old, '/api/upgrade', {'action': 'prepare', **request})
        assert waiting['upgrade']['stage'] == 'waiting'
        blocked = call(old, '/api/upgrade', {'action': 'commit', **request}, expected=409)
        assert not blocked['closed'] and old.worker.is_alive()
        cancelled = call(old, '/api/upgrade', {'action': 'cancel', 'requestId': cancelled_id})
        assert cancelled['upgrade']['stage'] == 'cancelled'
        assert not call(old, '/api/bootstrap')['closing']
        record('Waiting for a human answer remains usable and cancellable')

        old.app.sessions[sid]['state'] = 'idle'
        request_id = uuid.uuid4().hex
        request = {'requestId': request_id, 'targetVersion': WORKSPACE_VERSION}
        prepared = call(old, '/api/upgrade', {'action': 'prepare', **request})
        draft = {'sessionId': sid, 'mode': 'work', 'drafts': [
            {'id': sid, 'text': '보내지 않은 요청\n  Unicode draft 😀  ', 'attachments': [str(attachment)]},
            {'id': 'home:chat', 'text': '일반 대화 입력도 보존', 'attachments': []},
        ]}
        captured = call(old, '/api/upgrade', {'action': 'capture', 'requestId': request_id,
            'revision': prepared['upgrade']['revision'], 'snapshot': draft})
        assert captured['upgrade']['stage'] == 'captured'
        closed = call(old, '/api/upgrade', {'action': 'commit', **request})
        assert closed['closed'] is True
        old.worker.join(3)
        assert not old.worker.is_alive() and not old.runtime.exists()
        saved = json.loads((state / 'upgrade-drafts.json').read_text(encoding='utf-8'))
        assert saved['status'] == 'ready' and saved['snapshot'] == draft
        record('Authenticated capture and shutdown preserve exact drafts and attachments')

    with live(state) as new:
        bootstrap = call(new, '/api/bootstrap')
        assert bootstrap['window']['startupState'] == 'pending'
        assert bootstrap['window']['launchId'] != old_launch
        assert bootstrap['upgradeRestore']['snapshot'] == draft
        record('New server cannot inherit stale ready state from persisted diagnostics')
        outcome = {}
        began = time.monotonic()
        def waiter():
            try:
                outcome['ready'] = wait(new, old_token, timeout=4)
            except Exception as error:
                outcome['error'] = type(error).__name__
        observer = threading.Thread(target=waiter, name='update-qa-readiness', daemon=True)
        observer.start()
        time.sleep(.65)
        assert not outcome and observer.is_alive(), 'Runtime existence is not UI readiness'
        new_doc = 'replacement-' + uuid.uuid4().hex
        health(new, new_doc)
        time.sleep(.55)
        assert not outcome and observer.is_alive(), 'Document load alone is not UI readiness'
        # The prepared screen must be available for interactive draft recovery.
        # This only closes the restart waiter; update success is shown by the
        # frontend separately, after the exact restored acknowledgement.
        health(new, new_doc, 'startup', 'ready')
        observer.join(3)
        assert outcome == {'ready': True} and not observer.is_alive(), outcome
        assert call(new, '/api/bootstrap')['upgradeRestore']['snapshot'] == draft
        call(new, '/api/upgrade', {'action': 'restored', 'requestId': request_id})
        assert call(new, '/api/bootstrap')['upgradeRestore'] is None
        assert not (state / 'upgrade-drafts.json').exists()
        assert attachment.read_text(encoding='utf-8') == 'Synthetic attachment preserved across restart.'
        record('Restart waiter spans delayed window bootstrap and exact draft restore', elapsedSeconds=round(time.monotonic() - began, 3))

        elapsed = rejected(new, new.app.token, timeout=.2)
        record('Already running old capability cannot satisfy replacement readiness', elapsedSeconds=elapsed)
        elapsed = rejected(new, old_token, target_version='99.0.0', timeout=.2)
        record('Wrong version cannot satisfy replacement readiness', elapsedSeconds=elapsed)

        # A new document supersedes readiness; late old ready cannot hide failure.
        later_doc = 'new-document-' + uuid.uuid4().hex
        health(new, later_doc)
        health(new, later_doc, 'startup', 'failed')
        health(new, new_doc, 'startup', 'ready')
        assert call(new, '/api/bootstrap')['window']['startupState'] == 'failed'
        elapsed = rejected(new, old_token)
        assert elapsed < 2
        record('Current document failure is explicit and stale completion cannot override it', elapsedSeconds=elapsed)

        health(new, later_doc, 'startup', 'ready')
        new.app.ui_health.native({'type': 'process_failed', 'pid': 987654})
        health(new, later_doc, 'startup', 'ready')
        assert call(new, '/api/bootstrap')['window']['startupState'] == 'failed'
        elapsed = rejected(new, old_token)
        assert elapsed < 2
        record('Native process failure survives a queued frontend completion', elapsedSeconds=elapsed)

    with live(output / 'headless-state', desktop=False) as fixture:
        assert call(fixture, '/api/bootstrap')['window']['startupState'] == 'pending'
        assert wait(fixture, old_token, no_browser=True) is True
        record('Headless update readiness does not depend on a nonexistent browser')
        elapsed = rejected(fixture, old_token, timeout=.2)
        health(fixture, 'browser-only-document', 'startup', 'ready')
        assert wait(fixture, old_token) is True
        record('Browser fallback still requires its own ready event', elapsedSeconds=elapsed)

    report = {'schema': 1, 'version': WORKSPACE_VERSION, 'passed': len(results), 'checks': results,
              'scope': 'Real isolated LocalApp HTTP and coordinator; native host/browser events simulated. No Claude or external update service.'}
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'passed': len(results), 'report': str(output / 'report.json')}, ensure_ascii=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
