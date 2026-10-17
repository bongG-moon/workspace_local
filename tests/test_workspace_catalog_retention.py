"""Catalog-only CLI handshakes are reclaimable without losing user conversations."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from local_app.idle_connections import idle_candidate, occupies_slot
from local_app.server import LocalApp, Server
from tests.test_workspace_idle_connections import MemoryBridge
from tests.test_workspace_prepare_connection import FIXTURE


class CatalogRetentionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='workspace-catalog-retention-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.app = LocalApp(self.root / 'state', command=['fixture-only'],
                            managed_workspace_root=self.root / 'managed')
        self.addCleanup(self.app.close)

    def create(self, trusted=True):
        return self.app.get(self.app.create(str(self.root), trusted)['id'])

    def connect(self, item, **kwargs):
        with patch('local_app.server.ClaudeSession', side_effect=MemoryBridge):
            return self.app.connect(item['id'], **kwargs)

    def test_catalog_marking_never_reclassifies_an_existing_user_connection(self):
        item = self.create()
        self.connect(item)
        original = item['bridge']
        self.assertFalse(original._workspace_catalog_only)
        self.connect(item, _catalog=True)
        self.assertIs(original, item['bridge'])
        self.assertFalse(original._workspace_catalog_only)
        with self.app.lock:
            self.assertIsNone(idle_candidate(self.app, None))

    def test_explicit_reuse_promotes_catalog_connection(self):
        item = self.create()
        self.connect(item, _catalog=True)
        original = item['bridge']
        self.assertTrue(original._workspace_catalog_only)
        self.connect(item)
        self.assertIs(original, item['bridge'])
        self.assertFalse(original._workspace_catalog_only)
        with self.app.lock:
            self.assertIsNone(idle_candidate(self.app, None))

    def test_pending_work_and_transitions_still_protect_unused_catalog_connection(self):
        item = self.create()
        self.connect(item, _catalog=True)
        bridge = item['bridge']
        bridge.session_id = None  # initialize need not report a durable session ID.
        with self.app.lock:
            self.assertIs(item, idle_candidate(self.app, None))
        for owner, key, value in (
            (item, 'state', 'running'), (item, 'messages', [{'role':'user', 'text':'started'}]),
            (item, 'lastRunId', 'run'), (item, 'requests', {'approval': {}}),
            (item, 'choice', {'id':'choice'}), (item, '_connecting', True),
            (item, '_modelUpdating', True), (item, '_permissionUpdating', True),
            (item, '_stopAdmission', True), (bridge, 'busy', True),
            (bridge, 'tasks', {'child'}), (bridge, 'pending', {'approval': {}}),
            (bridge, 'descendant_cleanup_uncertain', True),
        ):
            with self.subTest(key=key):
                before = owner.get(key) if isinstance(owner, dict) else getattr(owner, key, None)
                if isinstance(owner, dict): owner[key] = value
                else: setattr(owner, key, value)
                with self.app.lock:
                    self.assertIsNone(idle_candidate(self.app, None))
                if isinstance(owner, dict): owner[key] = before
                else: setattr(owner, key, before)
        self.app.dispatch.queue.enqueue(item['id'], 'queued work', context=self.app.dispatch.context(item))
        with self.app.lock:
            self.assertIsNone(idle_candidate(self.app, None))
        self.assertFalse(bridge.closed)

    def test_catalog_flag_never_bypasses_trust(self):
        item = self.create()
        item["trusted"] = False
        with patch('local_app.server.ClaudeSession') as factory:
            with self.assertRaisesRegex(ValueError, '동의'):
                self.app.connect(item['id'], _catalog=True)
        factory.assert_not_called()
        self.assertFalse(item['trusted'])

    def test_real_catalog_children_remain_bounded_and_never_receive_user_prompts(self):
        fixture = self.root / 'fixture.py'
        fixture.write_text(FIXTURE, encoding='utf-8')
        log = self.root / 'wire.jsonl'
        self.app.command = [sys.executable, '-B', str(fixture), 'normal', str(log)]
        self.app.info = {'version':'fixture', 'help':''}
        rows = [self.create() for _ in range(8)]
        originals = []
        for item in rows:
            self.app.connect(item['id'], _catalog=True)
            originals.append(item['bridge'])
            self.assertLessEqual(sum(occupies_slot(row) for row in rows), 5)
        for _ in range(8):
            self.app.dispatch.maintain_connections()
        self.assertEqual(3, sum(occupies_slot(row) for row in rows))
        closed = [row for row in rows if row['bridge'].closed]
        self.assertEqual(5, len(closed))
        for item in closed:
            self.assertTrue(item['bridge'].cleanup_complete)
            self.assertIsNotNone(item['bridge'].process.poll())
            self.assertIsNone(item['sessionId'])
            self.assertFalse(item['_requireResumeIdentity'])
        for item in rows:
            self.assertEqual([], item['messages'])
            self.assertIsNone(item['lastRunId'])
            self.assertEqual('idle', item['state'])
            self.assertFalse(self.app.dispatch.queue.snapshot(item['id'])['paused'])
        # A reclaimed catalog handshake starts fresh, never --resume with a fake ID.
        self.app.connect(closed[0]['id'], _catalog=True)
        self.assertIsNone(closed[0]['bridge'].resume_id)
        frames = [json.loads(line) for line in log.read_text(encoding='utf-8').splitlines()]
        self.assertTrue(frames)
        self.assertTrue(all(frame['type'] == 'control_request' for frame in frames))
        self.assertTrue(all(frame['request']['subtype'] in {'initialize', 'mcp_status'} for frame in frames))

    def test_http_catalog_flag_is_boolean_and_uses_existing_capacity_gate(self):
        server = Server(self.app)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        item = self.create()
        def post(value):
            return urlopen(Request(server.origin + '/api/connect',
                data=json.dumps({'id':item['id'], 'automatic':True, 'catalog':value}).encode(),
                headers={'Content-Type':'application/json', 'Origin':server.origin,
                         'Authorization':'Bearer ' + self.app.token}), timeout=5)
        with patch('local_app.server.ClaudeSession', side_effect=MemoryBridge) as factory:
            for value in (1, 'true', None):
                with self.subTest(value=value), self.assertRaises(HTTPError) as denied:
                    post(value)
                self.assertEqual(400, denied.exception.code)
            factory.assert_not_called()
            with post(True) as response:
                self.assertTrue(json.load(response)['ok'])
            self.assertTrue(item['bridge']._workspace_catalog_only)
            self.assertEqual([], item['messages'])


if __name__ == '__main__':
    unittest.main()
