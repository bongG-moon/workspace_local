import concurrent.futures
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from local_app.file_diff import FileDiffStore, MAX_RUN_BYTES


class ObservedDiffTests(unittest.TestCase):
    """Completed commands expose review candidates, never automatic ownership."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name).resolve()
        self.workspace = self.base / 'workspace'
        self.workspace.mkdir()
        self.store = FileDiffStore(self.base / 'state')

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, name, text):
        path = self.workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def start(self, run='run', owner='task'):
        self.assertEqual('captured', self.store.start(self.workspace, run, session_id=owner)['status'])

    def candidates(self, run='run', owner='task'):
        return self.store.candidates(self.workspace, run, session_id=owner)

    def files(self, run='run', owner='task', **kwargs):
        return self.store.list(self.workspace, run, session_id=owner, **kwargs)['runs'][0]['files']

    def resolve(self, row, action, run='run', owner='task'):
        return self.store.resolve_candidate(self.workspace, run, row['id'],
                                            session_id=owner, action=action)

    def test_candidates_never_enter_primary_or_default_comparison_without_confirmation(self):
        self.write('updated.py', 'old = 1\n')
        self.start()
        self.write('updated.py', 'new = 2\n')
        self.write('generated.csv', 'label,count\na,5\n')
        result = self.store.finish(self.workspace, 'run', allowed_paths=[], observe_commands=True)
        self.assertEqual(0, result['changeCount'])
        self.assertEqual(2, result['observedCount'])
        rows = self.candidates()
        self.assertEqual({'updated.py', 'generated.csv'}, {row['name'] for row in rows})
        self.assertEqual([], self.files())
        for row in rows:
            self.assertEqual(row['name'], row['path'])
            self.assertEqual('observed', row['attribution'])
            with self.assertRaises(ValueError):
                self.store.get(self.workspace, 'run', row['id'], session_id='task')
            preview = self.store.list(self.workspace, 'run', session_id='task', candidate_id=row['id'])
            self.assertEqual('observed', preview['runs'][0]['attribution'])
            self.assertEqual([row['id']], [file['id'] for file in preview['runs'][0]['files']])
            comparison = self.store.get(self.workspace, 'run', row['id'],
                                        session_id='task', candidate_id=row['id'])
            self.assertEqual('text', comparison['status'])
            self.assertEqual('observed', comparison['attribution'])

    def test_exact_receipt_and_observed_candidate_stay_separate(self):
        self.start()
        self.write('direct.md', 'written using Write\n')
        self.write('script-output.txt', 'written by unknown command or another task\n')
        self.store.finish(self.workspace, 'run', allowed_paths=['direct.md'], observe_commands=True)
        self.assertEqual(['direct.md'], [row['name'] for row in self.files()])
        self.assertEqual(['script-output.txt'], [row['name'] for row in self.candidates()])
        row = self.candidates()[0]
        self.resolve(row, 'include')
        files = {row['name']: row for row in self.files()}
        self.assertEqual('tool', files['direct.md']['attribution'])
        self.assertEqual('confirmed', files['script-output.txt']['attribution'])
        self.assertEqual('run', files['script-output.txt']['runId'])

    def test_exact_write_receipt_does_not_prove_a_later_command_deletion(self):
        removed = self.write('report.py', 'before = 1\n')
        self.start()
        # A valid Write/Edit during the run proves that write, but neither it
        # nor a successful shell completion establishes who later removed it.
        removed.write_text('after = 2\n')
        removed.unlink()
        result = self.store.finish(self.workspace, 'run', allowed_paths=['report.py'], observe_commands=True)
        self.assertEqual(0, result['changeCount'])
        self.assertEqual([], self.files())
        self.assertEqual(1, result['observedCount'])
        row = self.candidates()[0]
        self.assertEqual('deleted', row['change'])
        with self.assertRaises(ValueError):
            self.store.get(self.workspace, 'run', row['id'], session_id='task')
        self.resolve(row, 'include')
        self.assertEqual('deleted', self.files()[0]['change'])
        comparison = self.store.get(self.workspace, 'run', row['id'], session_id='task')
        self.assertIn('-before = 1', comparison['diff'])
        self.assertNotIn('after = 2', comparison['diff'])

    def test_delete_keeps_captured_before_and_never_reads_missing_original(self):
        removed = self.write('old/report.py', 'old = 1\n')
        self.start()
        removed.unlink()
        removed.parent.rmdir()
        self.store.finish(self.workspace, 'run', allowed_paths=[], observe_commands=True)
        row = self.candidates()[0]
        self.assertEqual('deleted', row['change'])
        self.assertTrue(row['textAvailable'])
        with mock.patch.object(self.store, '_read_source', side_effect=AssertionError('read original')):
            comparison = self.store.get(self.workspace, 'run', row['id'],
                                        session_id='task', candidate_id=row['id'])
            confirmed = self.resolve(row, 'include')
            final = self.store.get(self.workspace, 'run', row['id'], session_id='task')
        self.assertIn('-old = 1', comparison['diff'])
        self.assertEqual('confirmed', confirmed['attribution'])
        self.assertEqual({'added': 0, 'removed': 1}, confirmed['lineCounts'])
        self.assertEqual('deleted', final['change'])
        self.assertEqual([], self.candidates())
        self.assertFalse(removed.exists())

    def test_decisions_and_conflicts_survive_restart_and_actions_are_idempotent(self):
        self.start()
        self.write('result.py', 'answer = 42\n')
        self.write('ignored.txt', 'keep original file\n')
        self.store.finish(self.workspace, 'run', observe_commands=True, conflict_paths=['result.py'])
        rows = {row['name']: row for row in self.candidates()}
        self.assertTrue(rows['result.py']['conflict'])
        self.resolve(rows['result.py'], 'include')
        self.resolve(rows['ignored.txt'], 'dismiss')
        self.store = FileDiffStore(self.base / 'state')
        self.assertEqual([], self.candidates())
        included = self.files()[0]
        self.assertEqual('confirmed', included['attribution'])
        self.assertTrue(included['conflict'])
        self.assertEqual('result.py', included['name'])
        self.resolve(rows['result.py'], 'include')
        self.resolve(rows['ignored.txt'], 'dismiss')
        for name, action in [('result.py', 'dismiss'), ('ignored.txt', 'include')]:
            with self.assertRaises(ValueError):
                self.resolve(rows[name], action)
        self.assertEqual('keep original file\n', (self.workspace / 'ignored.txt').read_text())

    def test_observed_v2_is_rejected_by_prior_reader_and_current_reader_restores_it(self):
        self.start()
        self.write('result.txt', 'review-only result\n')
        self.store.finish(self.workspace, 'run', observe_commands=True)
        target = self.store._target(self.workspace, 'run')
        # Prior installed readers accept version 1 only. A downgraded reader
        # must reject the record before its old _changes can claim candidates.
        data = json.loads(target.read_text(encoding='utf-8'))
        def prior_version_check(record):
            if not isinstance(record, dict) or record.get('version') != 1:
                raise ValueError('unsupported stored comparison')
        self.assertEqual(2, data['version'])
        with self.assertRaises(ValueError):
            prior_version_check(data)
        self.store = FileDiffStore(self.base / 'state')
        row = self.candidates()[0]
        self.assertEqual([], self.files())
        self.resolve(row, 'include')
        self.store = FileDiffStore(self.base / 'state')
        self.assertEqual('confirmed', self.files()[0]['attribution'])
        self.assertEqual(2, self.store._read(target)['version'])

    def test_normal_exact_capture_keeps_legacy_v1_without_observed_fields(self):
        self.start()
        self.write('direct.txt', 'exact receipt result')
        self.store.finish(self.workspace, 'run', allowed_paths=['direct.txt'])
        data = self.store._read(self.store._target(self.workspace, 'run'))
        self.assertEqual(1, data['version'])
        self.assertNotIn('observedCandidates', data)
        self.assertEqual(['direct.txt'], [row['name'] for row in self.files()])

    def test_mixed_or_unsupported_observation_record_versions_fail_closed(self):
        self.start()
        self.write('result.txt', 'review result')
        self.store.finish(self.workspace, 'run', observe_commands=True)
        target = self.store._target(self.workspace, 'run')
        data = self.store._read(target)
        for version in [1, 3, None]:
            malformed = dict(data, version=version)
            self.store._write(target, malformed)
            with self.assertRaises(ValueError):
                self.store._read(target)
        malformed = dict(data)
        del malformed['observedCandidates']
        self.store._write(target, malformed)
        with self.assertRaises(ValueError):
            self.store._read(target)
        self.store._write(target, data)
        self.assertEqual(1, len(self.candidates()))

    def test_cross_session_reads_and_mutation_are_rejected(self):
        self.start()
        self.write('result.txt', 'result')
        self.store.finish(self.workspace, 'run', observe_commands=True)
        row = self.candidates()[0]
        self.assertEqual([], self.candidates(owner='other-task'))
        self.assertEqual([], self.store.list(self.workspace, 'run', session_id='other-task',
                                            candidate_id=row['id'])['runs'])
        with self.assertRaises(ValueError):
            self.resolve(row, 'include', owner='other-task')
        with self.assertRaises(ValueError):
            self.store.get(self.workspace, 'run', row['id'], session_id='other-task', candidate_id=row['id'])
        other = self.base / 'other-workspace'
        other.mkdir()
        self.assertEqual([], self.store.candidates(other, 'run', session_id='task'))
        with self.assertRaises((OSError, ValueError)):
            self.store.resolve_candidate(other, 'run', row['id'], session_id='task', action='include')
        self.assertEqual(1, len(self.candidates()))

    def test_invalid_candidate_and_action_cannot_reveal_or_change_records(self):
        self.start()
        self.write('result.txt', 'result')
        self.store.finish(self.workspace, 'run', observe_commands=True)
        row = self.candidates()[0]
        for bad in ['../escape', '', None, 'x'*24, [], 'a'*25]:
            with self.assertRaises(ValueError):
                self.store.list(self.workspace, 'run', session_id='task', candidate_id=bad) if bad is not None else (
                    self.store.resolve_candidate(self.workspace, 'run', bad, session_id='task', action='include'))
        for bad in ['delete', '', None, []]:
            with self.assertRaises(ValueError):
                self.resolve(row, bad)
        with self.assertRaises(ValueError):
            self.store.get(self.workspace, 'run', row['id'], session_id='task', candidate_id='0'*24)
        with self.assertRaises(ValueError):
            self.store.resolve_candidate(self.workspace, 'run', row['id'], session_id=None, action='include')

    def test_incomplete_before_or_after_does_not_invent_creation_or_deletion_candidates(self):
        removed = self.write('removed.txt', 'still existing at start')
        self.start()
        target = self.store._target(self.workspace, 'run')
        data = self.store._read(target)
        data['before']['complete'] = []
        self.store._write(target, data)
        removed.unlink()
        self.write('created.txt', 'not proven absent at start')
        original = self.store._capture
        def incomplete(root, **kwargs):
            capture = original(root, **kwargs)
            capture['complete'] = []
            return capture
        with mock.patch.object(self.store, '_capture', side_effect=incomplete):
            self.store.finish(self.workspace, 'run', observe_commands=True)
        self.assertEqual([], self.candidates())
        self.assertEqual([], self.files())

    def test_internal_list_collects_pending_and_confirmed_with_one_read_per_run(self):
        self.start()
        self.write('included.txt', 'confirmed')
        self.write('pending.txt', 'pending')
        self.write('dismissed.txt', 'dismissed')
        self.store.finish(self.workspace, 'run', observe_commands=True)
        rows = {row['name']: row for row in self.candidates()}
        self.resolve(rows['included.txt'], 'include')
        self.resolve(rows['dismissed.txt'], 'dismiss')
        with mock.patch.object(self.store, '_read', wraps=self.store._read) as reads:
            result = self.store.list(self.workspace, session_id='task', include_observed=True)
        self.assertEqual(1, reads.call_count)
        self.assertEqual(['included.txt'], [row['name'] for row in result['runs'][0]['files']])
        self.assertEqual(['pending.txt'], [row['name'] for row in result['observedChanges']])
        self.assertNotIn('observedChanges', self.store.list(self.workspace, session_id='task'))
        other = self.store.list(self.workspace, session_id='other', include_observed=True)
        self.assertEqual([], other['runs'])
        self.assertEqual([], other['observedChanges'])

    def test_exact_receipts_keep_capture_budget_priority_in_mixed_command_run(self):
        self.store = FileDiffStore(self.base / 'state', max_files=2, capture_bytes=40)
        self.write('a.py', 'before = 1\n')
        self.write('child/z.py', 'before = 1\n')
        self.start()
        self.write('child/z.py', 'after = 2222\n')
        # Root files can fill a full scan before it reaches the exact child
        # receipt. The targeted path must keep the prior capture behavior.
        self.write('b.txt', 'unrelated bytes'*8)
        self.write('c.txt', 'unrelated bytes'*8)
        result = self.store.finish(self.workspace, 'run', allowed_paths=['child/z.py'], observe_commands=True)
        self.assertTrue(result['capture']['limited'])
        self.assertEqual(['child/z.py'], [row['name'] for row in self.files()])
        file = self.files()[0]
        comparison = self.store.get(self.workspace, 'run', file['id'], session_id='task')
        self.assertEqual('text', comparison['status'])
        self.assertIn('-before = 1', comparison['diff'])
        self.assertIn('+after = 2222', comparison['diff'])
        data = self.store._read(self.store._target(self.workspace, 'run'))
        self.assertLessEqual(len(data['after']['files']), 2)
        self.assertLessEqual(data['after']['textBytes'], 40)

    def test_deeper_exact_path_is_not_read_or_claimed_and_reports_existing_depth_limit(self):
        self.start()
        self.write('child/deeper/z.py', 'outside existing two-level scope')
        with mock.patch.object(self.store, '_read_source', wraps=self.store._read_source) as reads:
            result = self.store.finish(self.workspace, 'run', allowed_paths=['child/deeper/z.py'], observe_commands=True)
        self.assertTrue(result['capture']['limited'])
        self.assertEqual(0, reads.call_count)
        self.assertEqual([], self.files())
        self.assertEqual([], self.candidates())

    def test_same_folder_parallel_runs_never_automatically_claim_sibling_files(self):
        self.start('a', 'task-a')
        self.start('b', 'task-b')
        self.write('a.py', 'a = 1\n')
        self.write('b.py', 'b = 2\n')
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.store.finish, self.workspace, run,
                                   allowed_paths=[f'{run}.py'], observe_commands=True,
                                   conflict_paths=[f'{other}.py']) for run, other in [('a', 'b'), ('b', 'a')]]
            for future in futures:
                self.assertEqual('captured', future.result()['status'])
        self.assertEqual(['a.py'], [row['name'] for row in self.files('a', 'task-a')])
        self.assertEqual(['b.py'], [row['name'] for row in self.files('b', 'task-b')])
        self.assertEqual(['b.py'], [row['name'] for row in self.candidates('a', 'task-a')])
        self.assertEqual(['a.py'], [row['name'] for row in self.candidates('b', 'task-b')])
        self.assertTrue(self.candidates('a', 'task-a')[0]['conflict'])

    def test_no_valid_command_keeps_prior_scoped_behavior_and_does_not_read_unrelated_files(self):
        self.write('other.txt', 'before')
        self.start()
        self.write('other.txt', 'after')
        with mock.patch.object(self.store, '_read_source', side_effect=AssertionError('unrelated read')):
            self.store.finish(self.workspace, 'run', allowed_paths=[])
        self.assertEqual([], self.candidates())
        self.assertEqual([], self.files())
        stored = self.store._read(self.store._target(self.workspace, 'run'))
        self.assertEqual({}, stored['before']['files'])

    def test_capture_and_retention_limits_apply_to_observed_candidates(self):
        self.store = FileDiffStore(self.base / 'state', max_runs=2, max_files=2, file_bytes=16, capture_bytes=20)
        for run in ['first', 'second', 'third']:
            self.start(run)
            for index in range(4):
                self.write(f'result-{index}.txt', f'{run}-'+'x'*20)
            result = self.store.finish(self.workspace, run, observe_commands=True)
            self.assertLessEqual(result['observedCount'], 2)
            self.assertLessEqual(len(self.candidates(run)), 2)
            self.assertLessEqual(self.store._target(self.workspace, run).stat().st_size, MAX_RUN_BYTES)
            data = self.store._read(self.store._target(self.workspace, run))
            self.assertLessEqual(data['after']['textBytes'], 20)
        self.assertEqual([], self.candidates('first'))
        self.assertEqual(2, len(self.store._entries()))

    def test_candidate_union_has_its_own_limit_and_marks_truncation(self):
        self.store = FileDiffStore(self.base / 'state', max_files=2)
        old = [self.write(f'old-{index}.txt', 'old') for index in range(2)]
        self.start()
        for path in old:
            path.unlink()
        for index in range(2):
            self.write(f'new-{index}.txt', 'new')
        result = self.store.finish(self.workspace, 'run', observe_commands=True)
        self.assertTrue(result['observedLimited'])
        self.assertEqual(2, result['observedCount'])
        self.assertEqual(2, len(self.candidates()))

    def test_hidden_secrets_unsupported_and_links_are_never_candidates(self):
        self.start()
        self.write('.env', 'do not capture')
        self.write('credentials.txt', 'do not capture')
        self.write('child/private.key', 'do not capture')
        self.write('child/deeper/result.txt', 'outside depth')
        outside = self.base / 'outside.txt'
        outside.write_text('external text')
        try:
            os.link(outside, self.workspace / 'linked.txt')
        except OSError:
            pass
        self.write('result.csv', 'visible,data\n1,2\n')
        self.store.finish(self.workspace, 'run', observe_commands=True)
        self.assertEqual(['result.csv'], [row['name'] for row in self.candidates()])

    def test_failed_repeat_finish_does_not_duplicate_or_replace_frozen_candidates(self):
        self.start()
        self.write('result.txt', 'first result\n')
        self.store.finish(self.workspace, 'run', observe_commands=True)
        row = self.candidates()[0]
        self.write('result.txt', 'later edit\n')
        result = self.store.finish(self.workspace, 'run', observe_commands=True)
        self.assertEqual('already_captured', result['status'])
        comparison = self.store.get(self.workspace, 'run', row['id'], session_id='task', candidate_id=row['id'])
        self.assertIn('+first result', comparison['diff'])
        self.assertNotIn('later edit', comparison['diff'])


if __name__ == '__main__':
    unittest.main()
