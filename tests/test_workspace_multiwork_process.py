"""Independent work sessions using real local child processes, no Claude/network."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

from local_app.idle_connections import ConnectionCapacityUnavailable, MAX_CONNECTIONS
from local_app.server import LocalApp


class MultiworkProcessTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='workspace-multiwork-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        fixture = Path(__file__).parent / 'fixtures' / 'workspace_stop_cli.py'
        self.app = LocalApp(self.root / 'state',
            command=[sys.executable, '-B', str(fixture.resolve())],
            info={'version': 'fixture', 'help': ''},
            managed_workspace_root=self.root / 'managed')
        self.addCleanup(self.app.close)

    def task(self, name):
        folder = self.root / name
        folder.mkdir()
        return self.app.get(self.app.create(str(folder), True)['id'])

    def wait(self, predicate):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.01)
        self.fail('Local multiwork protocol fixture did not settle')

    def wire(self, item):
        path = Path(item['workspace']) / 'stop-wire.jsonl'
        if not path.exists():
            return []
        # Ignore a trailing incomplete line while the fixture is appending.
        text = path.read_text(encoding='utf-8')
        return [json.loads(line) for line in text.splitlines(keepends=True) if line.endswith('\n')]

    def user_turns(self, item):
        return [row['message']['content'] for row in self.wire(item) if row.get('type') == 'user']

    def test_five_live_tasks_are_isolated_and_sixth_waits_for_capacity(self):
        tasks = [self.task(f'work-{index}') for index in range(MAX_CONNECTIONS)]
        with ThreadPoolExecutor(max_workers=MAX_CONNECTIONS) as pool:
            sends = [pool.submit(self.app.send, row['id'], f'wait task {index}', [])
                     for index, row in enumerate(tasks)]
            for send in sends:
                send.result(timeout=8)
        self.wait(lambda: all(len(self.user_turns(row)) == 1 for row in tasks))
        self.assertEqual(MAX_CONNECTIONS, len({row['bridge'].process.pid for row in tasks}))
        self.assertEqual(MAX_CONNECTIONS, len({row['sessionId'] for row in tasks}))
        self.assertTrue(all(row['state'] == 'running' for row in tasks))
        for index, row in enumerate(tasks):
            self.assertEqual([f'wait task {index}'], self.user_turns(row))

        sixth = self.task('work-six')
        with self.assertRaises(ConnectionCapacityUnavailable):
            self.app.send(sixth['id'], 'sixth request', [])
        self.assertEqual([], sixth['messages'])
        self.assertTrue(all(row['state'] == 'running' for row in tasks))

        self.app.stop(tasks[0]['id'], disconnect=True)
        self.wait(lambda: tasks[0]['bridge'].cleanup_complete)
        self.app.send(sixth['id'], 'sixth request', [])
        self.wait(lambda: sixth['state'] == 'done')
        self.assertEqual(['sixth request'], self.user_turns(sixth))
        self.assertTrue(all(row['state'] == 'running' for row in tasks[1:]))
        self.assertTrue(all(row['bridge'].process.poll() is None for row in tasks[1:]))

    def test_second_task_can_finish_and_send_again_while_first_waits_for_approval(self):
        first, second = self.task('approval-work'), self.task('other-work')
        self.app.send(first['id'], 'wait approval', [])
        self.wait(lambda: first['state'] == 'approval')
        first_pid = first['bridge'].process.pid
        for text in ('first independent request', 'second independent request'):
            self.app.send(second['id'], text, [])
            self.wait(lambda: second['state'] == 'done')
            self.assertEqual('approval', first['state'])
            self.assertEqual(first_pid, first['bridge'].process.pid)
            self.assertIsNone(first['bridge'].process.poll())
        self.assertEqual(['wait approval'], self.user_turns(first))
        self.assertEqual(['first independent request', 'second independent request'], self.user_turns(second))
        second_pid = second['bridge'].process.pid
        self.app.stop(first['id'])
        self.wait(lambda: first['state'] == 'stopped')
        self.assertEqual('done', second['state'])
        self.assertEqual(second_pid, second['bridge'].process.pid)
        self.assertEqual(['first independent request', 'second independent request'], self.user_turns(second))
