"""Real authenticated HTTP and Edge stop-before-update checks, using owned fake CLI children.

No personal app state, actual Claude, installer download, or native install is used.
The LocalApp, queue, history, HTTP routing, UI, and draft restore run production code.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--node', default='node')
    args = parser.parse_args()
    output = args.output.absolute()
    output.mkdir(parents=True, exist_ok=False)
    os.environ['LOCALAPPDATA'] = str(output / 'local')
    os.environ['CLAUDE_CONFIG_DIR'] = str(output / 'claude')
    (output / 'claude').mkdir()
    from local_app.server import LocalApp, Server, WORKSPACE_VERSION
    from local_app.upgrade_launcher import Client
    from local_app.work_queue import WorkQueue
    checks = []

    def record(name):
        checks.append(name)
        print('PASS ' + name, flush=True)

    @contextmanager
    def live(state):
        app = LocalApp(state, demo=True)
        server = Server(app)
        def serve():
            try:
                server.serve_forever(poll_interval=.02)
            finally:
                server.server_close()
        worker = threading.Thread(target=serve, daemon=True)
        worker.start()
        try:
            yield app, server, worker, Client(server.origin, app.token)
        finally:
            server.shutdown()
            worker.join(3)
            app.close()
            assert not worker.is_alive()

    def call(client, route, data=None, expected=200):
        status, value = client.request(route, data)
        assert status == expected, (route, status, value)
        return value

    state = output / 'state'
    workspace = output / 'workspace'
    workspace.mkdir()
    attachment = workspace / 'original.txt'
    attachment.write_text('Unchanged original material', encoding='utf8')
    request_id = uuid.uuid4().hex
    draft_text = '업데이트 전 작성 중인 내용\n  줄바꿈과 공백 보존 😀  '
    closed_children = []
    with live(state) as (app, server, worker, client):
        sid = call(client, '/api/create', {'workspace': str(workspace), 'trusted': True, 'title': 'Stop update QA'})['id']
        other = call(client, '/api/create', {'workspace': str(workspace), 'trusted': True, 'title': 'Approval QA'})['id']

        class OwnedBridge:
            closed = False
            busy = True
            pending = {}
            cleanup_complete = False
            stopping = False
            capabilities = {}
            def __init__(self, item_id):
                self.item_id = item_id
            def close(self):
                if not self.closed:
                    saved = json.loads((state / 'upgrade-drafts.json').read_text(encoding='utf8'))
                    assert saved['status'] == 'ready', 'Children stopped before draft persistence'
                    assert any(row['text'] == draft_text for row in saved['snapshot']['drafts'])
                    closed_children.append(self.item_id)
                    self.closed, self.busy, self.cleanup_complete = True, False, True
                    app.emit(self.item_id, 'status', {'state': 'stopped'})
                return True

        app.sessions[sid].update(state='running', bridge=OwnedBridge(sid))
        app.sessions[other].update(state='approval', bridge=OwnedBridge(other))
        context = app.dispatch.context(app.sessions[sid])
        queued = app.dispatch.queue.enqueue(sid, '나중에 할 일', [str(attachment)], context)
        scheduled = app.dispatch.queue.add_schedule(sid, '내일 예약', [], kind='once', run_at=time.time()+86400, context=context)
        app.save(sid)
        app.save(other)
        fixture = output / 'browser-fixture.json'
        fixture.write_text(json.dumps({'url': server.origin + '/#token=' + app.token,
            'sid': sid, 'requestId': request_id, 'target': WORKSPACE_VERSION,
            'draft': draft_text, 'output': str(output)}, ensure_ascii=False), encoding='utf8')
        ui = subprocess.run([args.node, str(Path(__file__).with_name('verify-workspace-stop-update-ui.cjs')), str(fixture)],
                            cwd=ROOT, capture_output=True, text=True, encoding='utf8', timeout=100)
        (output / 'browser.log').write_text(ui.stdout + ui.stderr, encoding='utf8')
        assert ui.returncode == 0, ui.stdout + ui.stderr
        record('Real Edge confirmation, cancel, stop button, and exact typed input')
        assert closed_children == [], 'Prepare/confirmation must not prematurely close a child'
        snapshot = json.loads((state / 'upgrade-drafts.json').read_text(encoding='utf8'))['snapshot']
        assert snapshot['sessionId'] == sid
        assert next(row for row in snapshot['drafts'] if row['id'] == sid)['text'] == draft_text
        call(client, '/api/create', {'workspace': str(workspace), 'trusted': True}, expected=409)
        record('Captured stop snapshot blocks new work without deleting pending requests')
        closed = call(client, '/api/upgrade', {'action': 'commit', 'requestId': request_id, 'targetVersion': WORKSPACE_VERSION})
        assert closed['closed'] is True
        worker.join(4)
        assert not worker.is_alive()
        assert set(closed_children) == {sid, other}
        record('Commit closes owned running and approval children only after durable snapshot')
        try:
            client.request('/api/bootstrap')
        except Exception:
            pass
        else:
            raise AssertionError('Old endpoint remained open')
        record('Old authenticated endpoint closes after successful shutdown')

    with live(state) as (new, server, worker, client):
        bootstrap = call(client, '/api/bootstrap')
        assert bootstrap['upgradeRestore']['snapshot'] == snapshot
        stored = WorkQueue(state).snapshot(sid)
        assert stored['paused'] is True
        assert stored['queue'][0]['id'] == queued['id']
        assert stored['queue'][0]['text'] == '나중에 할 일'
        assert stored['schedules'][0]['id'] == scheduled['id']
        assert attachment.read_text(encoding='utf8') == 'Unchanged original material'
        assert call(client, '/api/session?id=' + sid)['messages'] == []
        record('Restart restores exact drafts, queued request and future schedule without replay')
        call(client, '/api/upgrade', {'action': 'restored', 'requestId': request_id})
        assert not (state / 'upgrade-drafts.json').exists()
        record('Acknowledged recovery removes only its handoff record')
    fixture.unlink(missing_ok=True)
    (output / 'report.json').write_text(json.dumps({'passed': True, 'checks': checks,
        'scope': 'Production HTTP and Edge UI; synthetic owned CLI children, no actual model or installer.'}, ensure_ascii=False, indent=2), encoding='utf8')


if __name__ == '__main__':
    main()
