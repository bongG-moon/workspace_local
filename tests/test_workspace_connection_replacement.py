"""Five live slots and explicit, single-use replacement of the exact child."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from local_app.server import LocalApp, Server
from local_app.bridge import probe_cli
from local_app.idle_connections import ConnectionCapacityUnavailable, MAX_CONNECTIONS
from tests.test_workspace_idle_connections import MemoryBridge


class ReplacementTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.app = LocalApp(self.root / 'state', command=['fixture-only'], managed_workspace_root=self.root / 'managed')
        self.addCleanup(self.app.close)
        self.rows = []
        for index in range(5):
            item = self.app.get(self.app.create(str(self.root), True)['id'])
            bridge = MemoryBridge()
            bridge._workspace_connected_at = index + 1
            item.update(bridge=bridge, state='running', lastRunId=f'run-{index}', sessionId=bridge.session_id)
            self.rows.append(item)
        self.target = self.app.get(self.app.create(str(self.root), True)['id'])

    def offer(self, method='connect', *args):
        with self.assertRaises(ConnectionCapacityUnavailable) as result:
            getattr(self.app, method)(self.target['id'], *args, _offer_replacement=True)
        return result.exception.replacement['token']

    def test_five_slots_and_no_automatic_interruption(self):
        self.assertEqual(5, MAX_CONNECTIONS)
        for row in self.rows:
            self.assertTrue(self.app.connection_capacity_available(row))
        self.assertFalse(self.app.connection_capacity_available(self.target))
        with self.assertRaises(ConnectionCapacityUnavailable) as failure:
            self.app.connect(self.target['id'])
        self.assertFalse(hasattr(failure.exception, 'replacement'))
        token = self.offer()
        self.assertTrue(token)
        self.assertTrue(all(row['bridge'].close_count == 0 for row in self.rows))
        self.assertEqual([], self.target['messages'])

    def test_confirm_replaces_oldest_connection_preserves_controls_and_pauses_work(self):
        old = self.rows[0]; bridge = old['bridge']; identity = old['sessionId']
        old['created'] = time.time()  # Task creation time does not select the victim.
        old['messages'] = [{'role': 'user', 'text': 'keep history'}]
        bridge.values.update(model='chosen', effort='high', permissionMode='auto')
        queue = self.app.dispatch.queue
        queue.enqueue(old['id'], 'next', context=self.app.dispatch.context(old))
        queue.add_schedule(old['id'], 'later', [], kind='once', run_at=queue.clock()+60,
                           context=self.app.dispatch.context(old))
        token = self.offer('send', 'new request', [])
        with patch('local_app.server.ClaudeSession', side_effect=MemoryBridge) as factory:
            self.app.send(self.target['id'], 'new request', [], _offer_replacement=True, _replacement_token=token)
            self.assertEqual(1, factory.call_count)
            self.assertEqual(['new request'], self.target['bridge'].sent)
            self.assertTrue(bridge.cleanup_complete)
            self.assertEqual('stopped', old['state'])
            self.assertTrue(queue.snapshot(old['id'])['paused'])
            self.assertEqual('keep history', old['messages'][0]['text'])
            self.app.send(old['id'], 'resume', [])
        self.assertEqual(identity, old['bridge'].session_id)
        self.assertTrue(old['bridge'].options['require_resume_identity'])
        self.assertEqual({'model':'chosen','effort':'high','permissionMode':'auto'}, old['bridge'].values)

    def test_ticket_is_bound_to_request_and_consumed_once(self):
        token = self.offer('send', 'original', [])
        with self.assertRaises(ConnectionCapacityUnavailable):
            self.app.send(self.target['id'], 'different', [], _offer_replacement=True, _replacement_token=token)
        self.assertFalse(self.rows[0]['bridge'].closed)
        token = self.offer('send', 'original', [])
        with patch('local_app.server.ClaudeSession', side_effect=MemoryBridge):
            self.app.send(self.target['id'], 'original', [], _offer_replacement=True, _replacement_token=token)
            with self.assertRaises(ConnectionCapacityUnavailable):
                self.app.send(self.target['id'], 'original', [], _offer_replacement=True, _replacement_token=token)
        self.assertEqual(['original'], self.target['bridge'].sent)

    def test_state_run_connection_change_or_expiry_never_stops_another_child(self):
        old = self.rows[0]
        for change in ('state', 'run', 'bridge', 'expiry'):
            with self.subTest(change=change):
                token = self.offer()
                bridge, state, run = old['bridge'], old['state'], old['lastRunId']
                if change == 'state': old['state'] = 'approval'
                elif change == 'run': old['lastRunId'] = 'new-run'
                elif change == 'bridge': old['bridge'] = MemoryBridge()
                else:
                    proposal = list(self.target['_replacementOffer']); proposal[1] = 0
                    self.target['_replacementOffer'] = tuple(proposal)
                with self.assertRaises(ConnectionCapacityUnavailable):
                    self.app.connect(self.target['id'], _offer_replacement=True, _replacement_token=token)
                self.assertFalse(bridge.closed)
                old.update(bridge=bridge, state=state, lastRunId=run)

    def test_failed_cleanup_never_starts_sixth_connection(self):
        token = self.offer(); old = self.rows[0]['bridge']
        with patch.object(old, 'close', return_value=False), patch('local_app.server.ClaudeSession') as factory:
            with self.assertRaises(ConnectionCapacityUnavailable):
                self.app.connect(self.target['id'], _offer_replacement=True, _replacement_token=token)
        factory.assert_not_called()
        self.assertIs(old, self.rows[0]['bridge'])
        self.assertFalse(self.app.connection_capacity_available(self.target))
        self.assertNotIn('_capacityReservation', self.target)
        self.assertTrue(self.app.dispatch.queue.snapshot(self.rows[0]['id'])['paused'])
        self.assertEqual('error', self.rows[0]['state'])

    def test_initialized_but_unused_connection_is_not_resumed_as_a_saved_conversation(self):
        old = self.rows[0]; old.update(state='idle', lastRunId=None, messages=[])
        previous = old['bridge'].session_id
        token = self.offer()
        with patch('local_app.server.ClaudeSession', side_effect=MemoryBridge):
            self.app.connect(self.target['id'], _offer_replacement=True, _replacement_token=token)
            self.assertIsNone(old['sessionId'])
            self.target['state'] = 'done'
            self.app.send(old['id'], 'first request', [])
        self.assertNotEqual(previous, old['bridge'].session_id)
        self.assertFalse(old['bridge'].options.get('require_resume_identity'))

    def test_invalid_request_or_transitions_do_not_offer_destructive_replacement(self):
        with self.assertRaises(ValueError):
            self.app.restart_connection(self.target['id'], stop_running='bad', _offer_replacement=True)
        self.assertNotIn('_replacementOffer', self.target)
        for row in self.rows: row['_restarting'] = True
        with self.assertRaises(ConnectionCapacityUnavailable) as failure:
            self.app.connect(self.target['id'], _offer_replacement=True)
        self.assertFalse(hasattr(failure.exception, 'replacement'))
        self.assertTrue(all(row['bridge'].close_count == 0 for row in self.rows))

    def test_pending_approval_is_cleared_without_accepting_it(self):
        old = self.rows[0]; old['state'] = 'approval'
        old['requests'] = {'r': {'id': 'r', 'tool': 'Bash'}}
        token = self.offer()
        with patch('local_app.server.ClaudeSession', side_effect=MemoryBridge):
            self.app.connect(self.target['id'], _offer_replacement=True, _replacement_token=token)
        self.assertEqual({}, old['requests'])
        self.assertEqual('stopped', old['state'])

    def test_confirmation_reserves_capacity_while_cleanup_waits(self):
        token = self.offer(); old = self.rows[0]['bridge']
        entered, release = threading.Event(), threading.Event(); errors = []
        original = old.close
        def close():
            entered.set(); release.wait(3); return original()
        def connect():
            try: self.app.connect(self.target['id'], _offer_replacement=True, _replacement_token=token)
            except Exception as exc: errors.append(exc)
        with patch.object(old, 'close', side_effect=close), patch('local_app.server.ClaudeSession', side_effect=MemoryBridge):
            worker = threading.Thread(target=connect); worker.start()
            try:
                self.assertTrue(entered.wait(2))
                self.assertTrue(self.app.lock.acquire(timeout=.5)); self.app.lock.release()
                with self.assertRaises(ConnectionCapacityUnavailable): self.app.connect(self.target['id'])
                competitor = self.app.create(str(self.root), True)['id']
                with self.assertRaises(ConnectionCapacityUnavailable): self.app.connect(competitor)
            finally:
                release.set(); worker.join(5)
        self.assertEqual([], errors)
        self.assertEqual(5, sum(bool(row.get('bridge') and not row['bridge'].closed) for row in self.app.sessions.values()))

    def test_http_automatic_waits_and_interactive_confirmation_retries_once(self):
        server = Server(self.app); worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        def request(data):
            req = Request(server.origin+'/api/connect', data=json.dumps({'id':self.target['id'], **data}).encode(),
                          headers={'Content-Type':'application/json','Authorization':'Bearer '+self.app.token})
            try: response = urlopen(req, timeout=5)
            except HTTPError as exc: response = exc
            with response: return response.status, json.load(response)
        try:
            status, value = request({'automatic':True})
            self.assertEqual(400, status); self.assertNotIn('replacement', value)
            status, value = request({})
            self.assertEqual('connection_capacity_wait', value['code'])
            with patch('local_app.server.ClaudeSession', side_effect=MemoryBridge):
                status, value = request({'replacementToken': value['replacement']['token']})
            self.assertEqual(200, status, value)
        finally:
            server.shutdown(); server.server_close(); worker.join(2)


class ReplacementProcessTests(unittest.TestCase):
    def test_real_owned_cli_process_stops_and_new_request_runs_once(self):
        fixture = Path(__file__).parent/'fixtures/workspace_restart_cli.py'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); command = [sys.executable, '-B', str(fixture.resolve())]
            app = LocalApp(root/'state', command=command, info=probe_cli(command), managed_workspace_root=root/'managed')
            try:
                rows = [app.get(app.create(str(root), True)['id']) for _ in range(6)]
                for row in rows[:5]: app.send(row['id'], 'wait for cancellation', [])
                deadline = time.monotonic()+5
                while any(not row['bridge']._initialized for row in rows[:5]) and time.monotonic()<deadline:
                    time.sleep(.02)
                self.assertTrue(all(row['bridge']._initialized for row in rows[:5]))
                old = rows[0]['bridge']
                with self.assertRaises(ConnectionCapacityUnavailable) as failure:
                    app.send(rows[5]['id'], 'replacement request', [], _offer_replacement=True)
                with app.lock: self.assertIs(old, rows[0]['bridge'])
                app.send(rows[5]['id'], 'replacement request', [], _offer_replacement=True,
                         _replacement_token=failure.exception.replacement['token'])
                deadline = time.monotonic()+5
                while rows[5]['state'] != 'done' and time.monotonic()<deadline: time.sleep(.02)
                self.assertTrue(old.cleanup_complete); self.assertIsNotNone(old.process.poll())
                self.assertEqual('stopped', rows[0]['state']); self.assertEqual('done', rows[5]['state'])
                wire = [json.loads(line) for line in (root/'restart-wire.jsonl').read_text().splitlines()]
                self.assertEqual(6, sum(frame['type']=='user' for frame in wire))
                self.assertEqual(5, sum(not row['bridge'].closed for row in rows))
            finally: self.assertTrue(app.close())
