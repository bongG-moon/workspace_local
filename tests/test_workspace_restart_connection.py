"""Explicit owned-CLI restart preserves identity and never submits a prompt."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from local_app.bridge import BridgeError, ClaudeSession, ControlRestoreRequired, probe_cli
from local_app.server import LocalApp, Server


FIXTURE = Path(__file__).parent / 'fixtures/workspace_restart_cli.py'


def eventually(predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('Fixture did not settle')


class RestartConnectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='workspace-restart-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        command = [sys.executable, '-B', str(FIXTURE.resolve())]
        self.app = LocalApp(self.root / 'state', command=command, info=probe_cli(command),
                            managed_workspace_root=self.root / 'managed')
        self.addCleanup(self.app.close)
        self.sid = self.app.create(str(self.root), True)['id']
        self.item = self.app.get(self.sid)
        self.app.connect(self.sid)

    def frames(self):
        return [json.loads(line) for line in (self.root / 'restart-wire.jsonl').read_text().splitlines()]

    def config(self, **value):
        (self.root / 'restart-config.json').write_text(json.dumps(value), encoding='utf-8')

    def test_idle_replaces_pid_resumes_identity_refreshes_inventory_without_turn(self):
        old = self.item['bridge']
        original_id = self.item['sessionId']
        self.config(commands=[{'name': 'installed-command'}], mcp=[{'name': 'new-server', 'status': 'connected'}])
        result = self.app.restart_connection(self.sid)
        new = self.item['bridge']
        self.assertNotEqual(old.process.pid, new.process.pid)
        self.assertIsNotNone(old.process.poll())
        self.assertTrue(old.cleanup_complete)
        self.assertEqual(original_id, self.item['sessionId'])
        self.assertEqual([{'name': 'installed-command'}], result['connection']['slashCommands'])
        self.assertEqual('new-server', result['connection']['mcp'][0]['name'])
        self.assertEqual('idle', result['session']['state'])
        self.assertFalse(result['connection']['restarting'])
        final = self.item['events'][-1]
        self.assertEqual('connection_restart_finished', final['type'])
        self.assertFalse(final['data']['connection']['restarting'])
        self.assertEqual(final['seq'], result['session']['seq'])
        self.assertFalse(result['workflowPaused'])
        self.assertFalse(self.app.dispatch.snapshot(self.sid)['paused'])
        self.assertEqual([], self.item['messages'])
        self.assertIsNone(self.item['lastRunId'])
        self.assertFalse(any(row['type'] == 'user' for row in self.frames()))
        self.assertIn('--resume=' + original_id,
                      next(row for row in reversed(self.frames()) if row['type'] == 'startup')['argv'])

    def test_completed_transcript_and_selections_survive_without_new_user_frame(self):
        self.app.send(self.sid, 'original business request', [])
        eventually(lambda: self.item['state'] == 'done')
        messages = list(self.item['messages']); run_id = self.item['lastRunId']
        self.app.set_model(self.sid, 'alternate-model')
        self.app.set_effort(self.sid, 'high')
        self.app.set_permission_mode(self.sid, 'auto')
        result = self.app.restart_connection(self.sid)
        self.assertEqual(('alternate-model', 'high', 'auto'), tuple(result['connection'][key] for key in ('model', 'effort', 'permissionMode')))
        self.assertEqual(messages, self.item['messages'])
        self.assertEqual(run_id, self.item['lastRunId'])
        self.assertEqual('done', result['session']['state'])
        self.assertEqual(1, sum(row['type'] == 'user' for row in self.frames()))

    def test_busy_requires_explicit_stop_and_does_not_replay_original_prompt(self):
        self.app.send(self.sid, 'wait for cancellation', [])
        eventually(lambda: any(row['type'] == 'user' for row in self.frames()))
        old = self.item['bridge']; before = len(self.frames())
        with self.assertRaises(BridgeError) as caught:
            self.app.restart_connection(self.sid)
        self.assertEqual('restart_requires_stop', caught.exception.code)
        self.assertFalse(old.closed)
        self.assertEqual(before, len(self.frames()))
        result = self.app.restart_connection(self.sid, stop_running=True)
        self.assertEqual('stopped', result['session']['state'])
        self.assertFalse(result['workflowPaused'])
        self.assertFalse(self.app.dispatch.snapshot(self.sid)['paused'])
        self.assertEqual(1, sum(row['type'] == 'user' for row in self.frames()))
        self.app.send(self.sid, 'explicit next request', [])
        eventually(lambda: self.item['state'] == 'done')

    def test_approval_is_closed_only_after_confirmed_stop(self):
        self.app.send(self.sid, 'wait for approval', [])
        eventually(lambda: self.item['state'] == 'approval')
        with self.assertRaises(BridgeError) as caught:
            self.app.restart_connection(self.sid)
        self.assertEqual('restart_requires_stop', caught.exception.code)
        self.assertTrue(self.item['requests'])
        result = self.app.restart_connection(self.sid, stop_running=True)
        self.assertEqual([], result['session']['requests'])
        self.assertEqual(1, sum(row['type'] == 'user' for row in self.frames()))

    def test_pending_automatic_work_is_preserved_and_paused_not_dispatched(self):
        queue = self.app.dispatch.queue
        row = queue.enqueue(self.sid, 'later', context=self.app.dispatch.context(self.item))
        schedule = queue.add_schedule(self.sid, 'scheduled', kind='once', run_at=time.time() + 60,
                                      context=self.app.dispatch.context(self.item))
        result = self.app.restart_connection(self.sid)
        self.app.dispatch.pump()
        snapshot = self.app.dispatch.snapshot(self.sid)
        self.assertTrue(result['workflowPaused'])
        self.assertTrue(snapshot['paused'])
        self.assertEqual(row['id'], snapshot['queue'][0]['id'])
        self.assertEqual('queued', snapshot['queue'][0]['status'])
        self.assertEqual(schedule['id'], snapshot['schedules'][0]['id'])
        self.assertFalse(any(frame['type'] == 'user' for frame in self.frames()))

    def test_preexisting_user_hold_is_not_changed(self):
        self.app.dispatch.queue.pause(self.sid, 'user')
        result = self.app.restart_connection(self.sid)
        self.assertTrue(result['workflowPaused'])
        self.assertEqual('user', self.app.dispatch.snapshot(self.sid)['reason'])

    def test_interrupted_queued_request_is_marked_for_review_not_replayed(self):
        self.app.dispatch.queue.enqueue(self.sid, 'wait for cancellation',
                                        context=self.app.dispatch.context(self.item))
        self.app.dispatch.pump()
        self.assertEqual('submitted', self.app.dispatch.snapshot(self.sid)['queue'][0]['status'])
        # Queue admission precedes the asynchronous user-frame write. This
        # case covers interruption of a submitted turn, not pre-send cancel.
        eventually(lambda: any(row['type'] == 'user' for row in self.frames()))
        result = self.app.restart_connection(self.sid, stop_running=True)
        self.assertTrue(result['workflowPaused'])
        self.app.dispatch.pump()
        self.assertEqual('needs_review', self.app.dispatch.snapshot(self.sid)['queue'][0]['status'])
        self.assertEqual(1, sum(row['type'] == 'user' for row in self.frames()))

    def test_close_failure_never_creates_replacement_or_drops_old_bridge(self):
        old = self.item['bridge']; starts = sum(row['type'] == 'startup' for row in self.frames())
        with patch.object(old, 'close', return_value=False):
            with self.assertRaises(BridgeError) as caught:
                self.app.restart_connection(self.sid)
        self.assertEqual('restart_close_unverified', caught.exception.code)
        self.assertIs(old, self.item['bridge'])
        self.assertFalse(self.item.get('_restarting'))
        self.assertFalse(self.item['_connecting'])
        self.assertEqual(starts, sum(row['type'] == 'startup' for row in self.frames()))
        self.assertFalse(self.item['events'][-1]['data']['connection']['restarting'])

    def test_failed_new_initialization_leaves_no_live_duplicate_or_retry(self):
        old = self.item['bridge']; sid = self.item['sessionId']
        self.config(rejectInit=True)
        with self.assertRaisesRegex(ValueError, '초기화가 거절'):
            self.app.restart_connection(self.sid)
        self.assertTrue(old.cleanup_complete)
        self.assertTrue(self.item['bridge'].cleanup_complete)
        self.assertEqual(sid, self.item['sessionId'])
        self.assertFalse(self.item.get('_restarting'))
        self.assertFalse(self.item['_connecting'])
        self.assertEqual(2, sum(row['type'] == 'startup' for row in self.frames()))
        self.assertFalse(any(row['type'] == 'user' for row in self.frames()))
        self.assertEqual('connection_restart_finished', self.item['events'][-1]['type'])
        self.assertFalse(self.item['events'][-1]['data']['connection'])

    def test_restart_rejects_different_resumed_identity(self):
        sid = self.item['sessionId']; self.config(wrongIdentity=True)
        with self.assertRaisesRegex(ValueError, '세션 ID'):
            self.app.restart_connection(self.sid)
        self.assertEqual(sid, self.item['sessionId'])
        self.assertTrue(self.item['bridge'].cleanup_complete)

    def test_late_retired_events_cannot_overwrite_new_connection(self):
        old = self.item['bridge']; self.app.restart_connection(self.sid)
        new = self.item['bridge']; before = self.app.public(self.item)
        self.app._emit_bridge(self.sid, old, 'connected', {'sessionId': 'wrong', 'tools': ['wrong']})
        self.app._emit_bridge(self.sid, old, 'error', {'message': 'late error'})
        self.app._emit_bridge(self.sid, old, 'status', {'state': 'stopped'})
        self.assertIs(new, self.item['bridge'])
        self.assertEqual(before, self.app.public(self.item))

    def test_restart_reserves_slot_and_blocks_send_controls_dispatch_and_second_restart(self):
        for index in range(2):
            other = self.app.create(str(self.root), True)
            self.app.connect(other['id'])
        fourth = self.app.create(str(self.root), True)
        entered, release = threading.Event(), threading.Event()
        old = self.item['bridge']; original = old.close; errors = []
        def delayed():
            entered.set(); release.wait(4)
            return original()
        def restart():
            try:
                self.app.restart_connection(self.sid)
            except Exception as exc:
                errors.append(exc)
        with patch.object(old, 'close', delayed):
            worker = threading.Thread(target=restart); worker.start()
            self.assertTrue(entered.wait(2))
            try:
                for action in (lambda: self.app.send(self.sid, 'must not send', []),
                               lambda: self.app.connect(self.sid),
                               lambda: self.app.set_model(self.sid, 'alternate-model'),
                               lambda: self.app.stop(self.sid),
                               lambda: self.app.restart_connection(self.sid, stop_running=True),
                               lambda: self.app.dispatch.action(self.sid, {'action': 'resume'}),
                               lambda: self.app.connect(fourth['id'])):
                    with self.assertRaises(ValueError):
                        action()
                self.app.dispatch.pump()
            finally:
                release.set(); worker.join(6)
        self.assertEqual([], errors)
        self.assertFalse(worker.is_alive())
        self.assertFalse(any(row['type'] == 'user' for row in self.frames()))

    def test_permission_restore_rejection_retains_choice_and_blocks_next_send(self):
        self.app.set_permission_mode(self.sid, 'auto')
        self.config(rejectPermission=True)
        result = self.app.restart_connection(self.sid)
        self.assertEqual('needs_input', result['connection']['controlRestore']['status'])
        self.assertEqual('auto', self.item['_sessionControls']['permissionMode'])
        with self.assertRaises(ControlRestoreRequired):
            self.app.send(self.sid, 'blocked until user handles controls', [])
        self.assertFalse(any(row['type'] == 'user' for row in self.frames()))

    def test_already_admitted_stop_cannot_interrupt_replacement_child(self):
        old = self.item['bridge']; original = old.interrupt
        entered, release = threading.Event(), threading.Event()
        def delayed():
            entered.set(); release.wait(4)
            original()
        with patch.object(old, 'interrupt', delayed):
            worker = threading.Thread(target=self.app.stop, args=(self.sid,)); worker.start()
            self.assertTrue(entered.wait(2))
            try:
                self.app.restart_connection(self.sid)
                replacement = self.item['bridge']
                self.assertIsNot(old, replacement)
            finally:
                release.set(); worker.join(5)
        self.assertFalse(replacement.closed)
        self.assertFalse(replacement.stopping)

    def test_bypass_opt_in_is_retained_without_inventing_new_confirmation(self):
        self.app.set_permission_mode(self.sid, 'bypassPermissions', bypass_confirmed=True)
        result = self.app.restart_connection(self.sid)
        self.assertEqual('bypassPermissions', result['connection']['permissionMode'])
        self.assertTrue(self.item['_allowBypass'])
        latest = next(row for row in reversed(self.frames()) if row['type'] == 'startup')
        self.assertIn('--allow-dangerously-skip-permissions', latest['argv'])

    def test_target_completion_cache_invalidated_after_restart(self):
        discovery = self.app.completion_discovery
        with patch.object(discovery.client, 'completion_inventory', return_value={'skills': []}) as inventory:
            discovery.inventory(self.item['workspace'])
            discovery.inventory(self.item['workspace'])
            self.assertEqual(1, inventory.call_count)
            self.app.restart_connection(self.sid)
            discovery.inventory(self.item['workspace'])
            self.assertEqual(2, inventory.call_count)

    def test_authenticated_route_returns_typed_busy_error(self):
        server = Server(self.app, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        def stop():
            server.shutdown(); server.server_close(); thread.join(2)
        self.addCleanup(stop)
        def post(value, authenticated=True):
            headers = {'Content-Type': 'application/json', 'Origin': server.origin}
            if authenticated:
                headers['Authorization'] = 'Bearer ' + self.app.token
            with urlopen(Request(server.origin + '/api/restart-connection', data=json.dumps(value).encode(), headers=headers), timeout=5) as response:
                return json.loads(response.read())
        with self.assertRaises(HTTPError) as caught:
            post({'id': self.sid}, False)
        self.assertEqual(403, caught.exception.code); caught.exception.close()
        self.app.send(self.sid, 'wait for cancellation', [])
        with self.assertRaises(HTTPError) as caught:
            post({'id': self.sid})
        self.assertEqual('restart_requires_stop', json.loads(caught.exception.read())['code']); caught.exception.close()
        self.assertTrue(post({'id': self.sid, 'stopRunning': True})['ok'])

    def test_confirmed_quit_drains_admitted_restart_without_starting_replacement(self):
        server = Server(self.app, 0)
        serving = threading.Thread(target=server.serve_forever, daemon=True); serving.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), serving.join(3)))
        entered, release, quit_finished = threading.Event(), threading.Event(), threading.Event()
        old = self.item['bridge']; close = old.close
        results, failures = {}, []
        def delayed_close():
            entered.set()
            if not release.wait(6):
                raise AssertionError('Restart gate was not released')
            return close()
        def post(route, value):
            headers = {'Content-Type': 'application/json', 'Origin': server.origin,
                       'Authorization': 'Bearer ' + self.app.token}
            with urlopen(Request(server.origin + route, data=json.dumps(value).encode(), headers=headers), timeout=10) as response:
                return json.loads(response.read())
        def request(name, route, value):
            try:
                results[name] = post(route, value)
            except Exception as exc:
                failures.append(exc)
            finally:
                if name == 'quit':
                    quit_finished.set()
        with patch.object(old, 'close', delayed_close):
            restarting = threading.Thread(target=request, args=('restart', '/api/restart-connection', {'id': self.sid}))
            restarting.start()
            self.assertTrue(entered.wait(3))
            quitting = threading.Thread(target=request, args=('quit', '/api/quit', {'confirmed': True})); quitting.start()
            try:
                eventually(lambda: self.app.shutdown_status()['closing'])
                self.assertFalse(quit_finished.is_set())
                self.assertEqual(1, self.app._active_operations)
                with self.assertRaises(HTTPError) as caught:
                    post('/api/restart-connection', {'id': self.sid})
                self.assertEqual(409, caught.exception.code); caught.exception.close()
            finally:
                release.set(); restarting.join(8); quitting.join(8)
        self.assertFalse(restarting.is_alive()); self.assertFalse(quitting.is_alive())
        self.assertEqual(1, len(failures))
        self.assertIsInstance(failures[0], HTTPError)
        self.assertEqual(409, failures[0].code)
        failures[0].close()
        self.assertTrue(results['quit']['closed'])
        self.assertIsNone(self.item['bridge'])
        self.assertTrue(old.cleanup_complete)
        self.assertIsNotNone(old.process.poll())
        self.assertEqual(1, sum(row['type'] == 'startup' for row in self.frames()))
        self.assertEqual(0, self.app._active_operations)
        self.assertFalse(any(row['type'] == 'user' for row in self.frames()))

    def transcript(self, config, session_id):
        path = config / 'projects' / 'fixture' / (session_id + '.jsonl')
        path.parent.mkdir(parents=True, exist_ok=True)
        prompt_id = str(uuid.uuid4())
        rows = [
            {'type': 'user', 'sessionId': session_id, 'uuid': prompt_id, 'parentUuid': None,
             'cwd': str(self.root), 'message': {'role': 'user', 'content': 'saved question'}},
            {'type': 'assistant', 'sessionId': session_id, 'uuid': str(uuid.uuid4()), 'parentUuid': prompt_id,
             'cwd': str(self.root), 'message': {'role': 'assistant', 'content': 'saved answer'}},
        ]
        path.write_text('\n'.join(map(json.dumps, rows)) + '\n', encoding='utf-8')
        return path

    def test_imported_session_restart_keeps_profile_transcript_and_native_identity(self):
        config = self.root / 'isolated-profile'; original_id = str(uuid.uuid4())
        transcript = self.transcript(config, original_id); source = transcript.read_bytes()
        with patch('local_app.server.runtime_context', return_value={'configRoot': str(config)}):
            imported = self.app.import_session(original_id)['session']
            item = self.app.get(imported['id']); item['trusted'] = True
            self.app.connect(item['id'])
            old = item['bridge']; before = list(item['messages'])
            result = self.app.restart_connection(item['id'])
        self.assertTrue(old.cleanup_complete)
        self.assertTrue(result['session']['imported'])
        self.assertEqual(original_id, result['session']['sessionId'])
        self.assertEqual(original_id, item['bridge']._expected_resume)
        self.assertEqual(str(config), item['importedConfigRoot'])
        self.assertEqual(before, item['messages'])
        self.assertEqual(source, transcript.read_bytes())
        latest = next(row for row in reversed(self.frames()) if row['type'] == 'startup')
        self.assertIn('--resume=' + original_id, latest['argv'])
        self.assertNotIn('--fork-session', latest['argv'])

    def test_active_branch_restart_resumes_child_and_never_reforks_parent(self):
        config = self.root / 'isolated-profile'; child_id = self.item['sessionId']
        transcript = self.transcript(config, child_id); source = transcript.read_bytes()
        parent_id = str(uuid.uuid4())
        self.item.update(importedConfigRoot=str(config), branch={
            'scope': 'latest', 'status': 'active', 'sourceTaskId': str(uuid.uuid4()),
            'sourceSessionId': parent_id, 'childSessionId': child_id,
            'sourceFingerprint': '0' * 64, 'configRoot': str(config),
            'sourceTitle': 'parent fixture', 'createdAt': time.time(),
        })
        with patch('local_app.server.runtime_context', return_value={'configRoot': str(config)}):
            result = self.app.restart_connection(self.sid)
        self.assertEqual(child_id, result['session']['sessionId'])
        self.assertEqual('active', result['session']['branch']['status'])
        latest = next(row for row in reversed(self.frames()) if row['type'] == 'startup')
        self.assertIn('--resume=' + child_id, latest['argv'])
        self.assertNotIn('--resume=' + parent_id, latest['argv'])
        self.assertNotIn('--fork-session', latest['argv'])
        self.assertEqual(source, transcript.read_bytes())

    def test_unconfirmed_windows_tree_cleanup_blocks_all_replacement_paths(self):
        bridge = ClaudeSession(['fixture'], {'help': ''}, self.root, lambda *_: None)
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired('fixture', .3), 0]
        bridge.process = process
        with patch('local_app.bridge.os.name', 'nt'), patch('local_app.bridge.subprocess.run', return_value=Mock(returncode=1)):
            self.assertFalse(bridge.close())
        self.assertFalse(bridge.cleanup_complete)
        process.kill.assert_called_once()
        old = self.item['bridge']; old.close(); self.item['bridge'] = bridge
        for action in (lambda: self.app.restart_connection(self.sid), lambda: self.app.connect(self.sid),
                       lambda: self.app.send(self.sid, 'not sent', [])):
            with self.assertRaises(ValueError):
                action()
        self.item['bridge'] = old


if __name__ == '__main__':
    unittest.main()
