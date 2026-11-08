"""Completed command deltas require review, including captured deletions."""
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

from local_app.server import LocalApp
from tests import test_workspace_file_attribution as attribution


class CommandResultReviewTests(attribution.FileAttributionTests):
    def command(self, bridge, identifier='command', code=None, native=None):
        self.tool(bridge, identifier, 'Bash', {'command': 'python fixture.py'})
        if code:
            executed = subprocess.run([sys.executable, '-B', '-c', code], cwd=self.work,
                                      capture_output=True, text=True, timeout=10)
            self.assertEqual(0, executed.returncode, executed.stderr)
        self.result(bridge, identifier, {'interrupted': False} if native is None else native)

    def finish(self, sid, bridge):
        bridge.handle({'type': 'result'})
        return self.app.results(sid)

    def test_real_script_create_modify_delete_requires_explicit_review(self):
        (self.work / 'existing.py').write_text('value = 1\n')
        (self.work / 'removed.md').write_text('previous content\n')
        sid, bridge = self.task()
        run = self.app.get(sid)['lastRunId']
        self.command(bridge, code=(
            "from pathlib import Path; Path('generated.csv').write_text('x,y\\n1,2\\n'); "
            "Path('existing.py').write_text('value = 2\\n'); Path('removed.md').unlink()"))
        result = self.finish(sid, bridge)
        self.assertEqual([], result['artifacts'])
        pending = {Path(row['name']).as_posix(): row for row in result['observedChanges']}
        self.assertEqual({'generated.csv', 'existing.py', 'removed.md'}, pending.keys())
        self.assertEqual({'created', 'modified', 'deleted'}, {row['change'] for row in pending.values()})
        self.assertEqual([], self.app.file_changes(sid, run)['runs'][0]['files'])
        for row in pending.values():
            with self.assertRaises(ValueError):
                self.app.file_changes(sid, run, row['id'])
            review = self.app.file_changes(sid, run, row['id'], candidate_id=row['id'])
            self.assertEqual('observed', review['attribution'])
            self.assertFalse(review['currentFileRead'])
            self.app.confirm_result(sid, run, row['id'], 'include')
        confirmed = self.app.results(sid)
        self.assertEqual([], confirmed['observedChanges'])
        self.assertEqual(3, len(confirmed['artifacts']))
        self.assertTrue(all(row['attribution'] == 'confirmed' for row in confirmed['artifacts']))
        self.assertEqual(3, len(self.app.file_changes(sid, run)['runs'][0]['files']))
        deleted = pending['removed.md']
        compared = self.app.file_changes(sid, run, deleted['id'])
        self.assertIn('-previous content', compared['diff'])
        self.assertEqual('deleted', compared['change'])
        self.assertEqual({'added': 0, 'removed': 1}, next(
            row['lineCounts'] for row in confirmed['artifacts'] if row['change'] == 'deleted'))
        self.assertTrue(any(event['type'] == 'results_changed' for event in self.app.get(sid)['events']))

    def test_same_folder_sibling_writes_stay_unassigned_with_conflict_hint(self):
        a, first = self.task()
        b, second = self.task()
        own = self.work / 'own.csv'
        sibling = self.work / 'sibling.md'
        self.tool(first, 'script', 'PowerShell', {'command': 'python generator.py'})
        own.write_text('x\n1\n')
        self.tool(second, 'write', 'Write', {'file_path': str(sibling)})
        sibling.write_text('other task')
        self.result(second, 'write', {'type': 'create', 'filePath': str(sibling)})
        self.finish(b, second)
        self.result(first, 'script', {'interrupted': False})
        result = self.finish(a, first)
        self.assertEqual([], result['artifacts'])
        pending = {row['name']: row for row in result['observedChanges']}
        self.assertEqual({'own.csv', 'sibling.md'}, pending.keys())
        self.assertTrue(pending['sibling.md']['conflict'])
        self.assertFalse(pending['own.csv']['conflict'])
        self.assertEqual({'sibling.md'}, self.names(b))
        self.app.confirm_result(a, pending['own.csv']['runId'], pending['own.csv']['id'], 'include')
        self.assertEqual({'own.csv'}, self.names(a))
        self.assertEqual({'sibling.md'}, self.names(b))

    def test_cancelled_or_background_command_does_not_enable_observation(self):
        for native, error in (({'interrupted': True}, False),
                              ({'interrupted': False, 'backgroundTaskId': 'bg'}, False),
                              ({'exitCode': 1}, True)):
            with self.subTest(native=native):
                sid, bridge = self.task()
                self.tool(bridge, 'script', 'Bash', {'command': 'python generator.py'})
                (self.work / (sid + '.md')).write_text('external or incomplete')
                self.result(bridge, 'script', native, error=error)
                self.assertEqual([], self.finish(sid, bridge)['observedChanges'])

    def test_late_sibling_receipt_updates_conflict_before_publishing(self):
        a, first = self.task()
        _, second = self.task()
        target = self.work / 'late.md'
        self.tool(second, 'late-write', 'Write', {'file_path': str(target)})
        target.write_text('sibling output')
        self.command(first)
        finish = self.app.file_diffs.finish
        def delayed_receipt(*args, **kwargs):
            result = finish(*args, **kwargs)
            self.result(second, 'late-write', {'type': 'create', 'filePath': str(target)})
            return result
        with patch.object(self.app.file_diffs, 'finish', side_effect=delayed_receipt):
            result = self.finish(a, first)
        self.assertEqual([], result['artifacts'])
        self.assertTrue(result['observedChanges'][0]['conflict'])
        self.assertTrue(self.app.results(a)['observedChanges'][0]['conflict'])

    def test_unrelated_disk_change_without_command_is_not_a_candidate(self):
        sid, bridge = self.task()
        (self.work / 'external.md').write_text('unrelated')
        with patch.object(self.app.file_diffs, 'finish', wraps=self.app.file_diffs.finish) as finish:
            self.assertEqual([], self.finish(sid, bridge)['observedChanges'])
        self.assertIs(False, finish.call_args.kwargs['observe_commands'])

    def test_wrong_session_and_wrong_run_cannot_review(self):
        a, first = self.task()
        b, second = self.task()
        self.command(first, code="from pathlib import Path; Path('output.md').write_text('body')")
        row = self.finish(a, first)['observedChanges'][0]
        for sid, run in ((b, row['runId']), (a, self.app.get(b)['lastRunId'])):
            with self.subTest(sid=sid, run=run):
                with self.assertRaises(ValueError):
                    self.app.confirm_result(sid, run, row['id'], 'include')
                if sid == b:
                    self.assertEqual([], self.app.file_changes(sid, row['runId'], candidate_id=row['id'])['runs'])
        self.assertEqual([], self.app.results(b)['observedChanges'])

    def test_dismiss_preserves_original_and_idempotent_include_has_no_duplicates(self):
        sid, bridge = self.task()
        self.command(bridge, code=("from pathlib import Path; Path('skip.md').write_text('keep'); "
                                   "Path('include.md').write_text('new')"))
        rows = {row['name']: row for row in self.finish(sid, bridge)['observedChanges']}
        for name, action in (('skip.md', 'dismiss'), ('include.md', 'include')):
            row = rows[name]
            for _ in range(2):
                self.app.confirm_result(sid, row['runId'], row['id'], action)
            with self.assertRaises(ValueError):
                self.app.confirm_result(sid, row['runId'], row['id'], 'include' if action == 'dismiss' else 'dismiss')
        self.assertEqual('keep', (self.work / 'skip.md').read_text())
        self.assertEqual({'include.md'}, self.names(sid))
        self.assertEqual(1, len(self.app.results(sid)['artifacts']))
        self.assertEqual([], self.app.results(sid)['observedChanges'])

    def test_deletion_history_and_confirmation_survive_restart_and_recreation(self):
        deleted = self.work / 'removed.md'
        deleted.write_text('original\n')
        sid, bridge = self.task()
        self.command(bridge, code="from pathlib import Path; Path('removed.md').unlink()")
        row = self.finish(sid, bridge)['observedChanges'][0]
        self.app.confirm_result(sid, row['runId'], row['id'], 'include')
        state = self.app.state
        self.app.close()
        self.app = LocalApp(state, command=['fixture-cli'], info={'version': 'fixture'})
        self.addCleanup(self.app.close)
        self.assertEqual('deleted', self.app.results(sid)['artifacts'][0]['change'])
        deleted.write_text('a later recreation\n')
        compared = self.app.file_changes(sid, row['runId'], row['id'])
        self.assertIn('-original', compared['diff'])
        self.assertNotIn('a later recreation', compared['diff'])
        self.assertEqual('deleted', self.app.results(sid)['artifacts'][0]['change'])

    def test_missing_live_confirmed_file_is_hidden_but_not_the_captured_deletion(self):
        sid, bridge = self.task()
        self.command(bridge, code="from pathlib import Path; Path('later.md').write_text('new')")
        row = self.finish(sid, bridge)['observedChanges'][0]
        self.app.confirm_result(sid, row['runId'], row['id'], 'include')
        (self.work / 'later.md').unlink()
        result = self.app.results(sid)
        self.assertEqual([], result['artifacts'])
        self.assertEqual(1, result['availability']['missing'])

    def test_confirmation_io_does_not_hold_other_tasks_global_lock(self):
        sid, bridge = self.task()
        self.command(bridge, code="from pathlib import Path; Path('result.md').write_text('new')")
        row = self.finish(sid, bridge)['observedChanges'][0]
        entered, release = threading.Event(), threading.Event()
        resolve = self.app.file_diffs.resolve_candidate
        errors = []
        def slow(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('test timeout')
            return resolve(*args, **kwargs)
        def confirm():
            try:
                self.app.confirm_result(sid, row['runId'], row['id'], 'include')
            except Exception as exc:
                errors.append(exc)
        with patch.object(self.app.file_diffs, 'resolve_candidate', side_effect=slow):
            thread = threading.Thread(target=confirm)
            thread.start()
            try:
                self.assertTrue(entered.wait(5))
                self.assertTrue(self.app.lock.acquire(timeout=.2))
                self.app.lock.release()
                with self.assertRaises(ValueError):
                    self.app.confirm_result(sid, row['runId'], row['id'], 'include')
            finally:
                release.set()
                thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual([], errors)


if __name__ == '__main__':
    unittest.main()
