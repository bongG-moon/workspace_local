"""Employee report regressions: bounded discovery and honest restart state."""
from pathlib import Path
import os
import tempfile
import unittest
import uuid
from unittest.mock import patch

from local_app.artifacts import snapshot, changes
from local_app.history import HistoryStore, row, normalize_artifact_observation
from local_app.server import LocalApp


class BoundedNestedResultsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()

    def write(self, name, text='report'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def test_nested_existing_and_new_folders_are_detected_without_reading_contents(self):
        old = self.write('output/q2/report.html', 'old')
        before = snapshot(self.root)
        old.write_text('revised report', encoding='utf-8')
        new = self.write('output/q3/revised/report.html')
        with patch.object(Path, 'open', side_effect=AssertionError('metadata only')):
            after = snapshot(self.root)
        found = changes(self.root, before, after, 'run', 1)
        self.assertEqual({str(old): 'modified', str(new): 'created'},
                         {r['path']: r['change'] for r in found})

    def test_existing_but_unscanned_folder_does_not_claim_creation(self):
        self.write('output/q2/report.html')
        before = snapshot(self.root, max_depth=0)
        self.assertTrue(before.limited)
        self.assertIn(str(self.root / 'output'), before.directories)
        self.assertEqual([], changes(self.root, before, snapshot(self.root), 'run', 1))

    def test_depth_count_and_time_limits_remain_explicit(self):
        self.write('output/year/q2/revised/report.html')
        result = snapshot(self.root)
        self.assertTrue(result.limited)
        self.assertEqual({}, result.files)
        self.assertEqual(3, result.public()['maxDepth'])
        self.write('root.txt')
        self.write('other.txt')
        self.assertEqual(1, len(snapshot(self.root, max_files=1).files))
        self.assertTrue(snapshot(self.root, max_directories=0).limited)
        self.assertEqual({}, snapshot(self.root, seconds=0).files)
        self.assertTrue(snapshot(self.root, seconds=0).limited)

    def test_private_and_excluded_nested_folders_remain_ignored(self):
        self.write('output/.claude/private.txt')
        self.write('output/node_modules/secret.txt')
        report = self.write('output/q2/report.html')
        self.assertEqual({str(report)}, set(snapshot(self.root).files))

    def test_changed_ancestor_is_rejected_before_scanning_deep_directory(self):
        self.write('output/q2/report.html')
        original_resolve, original_scandir = Path.resolve, os.scandir
        child = self.root / 'output' / 'q2'
        def resolved(path, **kwargs):
            if path == child:
                return self.root / 'outside' / 'q2'
            return original_resolve(path, **kwargs)
        def scan(path):
            self.assertNotEqual(child, path, 'redirected ancestry was traversed')
            return original_scandir(path)
        with patch.object(Path, 'resolve', resolved), patch('local_app.artifacts.os.scandir', scan):
            self.assertEqual({}, snapshot(self.root).files)


class IncompleteRequestHistoryTests(unittest.TestCase):
    def test_restart_retains_marker_and_content_without_starting_cli(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            work = root / 'work'
            work.mkdir()
            sid = str(uuid.uuid4())
            item = dict(id=sid, title='report', workspace=str(work), created=1., updated=2.,
                        pinned=False, sessionId=None, lastRunId='pending-run', state='running',
                        _runTimer={'runId': 'pending-run'},
                        messages=[dict(role='user', text='prepare report', runId='pending-run', files=[str(work/'input.csv')])])
            item['artifactObservation'] = {'limited': True, 'errors': 1, 'scanned': 12,
                                          'scope': 'workspace-bounded-depth', 'maxDepth': 3,
                                          'retainedLimit': 100, 'directories': ['private-path']}
            HistoryStore(root/'state').save([item])
            with patch('local_app.server.ClaudeSession', side_effect=AssertionError('must not resume automatically')):
                app = LocalApp(root/'state', command=['fixture-never-started'])
                try:
                    result = app.public(app.get(sid))
                    self.assertEqual('idle', result['state'])
                    self.assertEqual('pending-run', result['pendingRunId'])
                    self.assertEqual(item['messages'], result['messages'])
                    observed = app.results(sid)['observation']
                    self.assertTrue(observed['limited'])
                    self.assertEqual(3, observed['maxDepth'])
                    self.assertNotIn('directories', observed)
                    app.update_session(sid, {'title': 'renamed'})
                    self.assertEqual('pending-run', HistoryStore(root/'state').load()[0]['pendingRunId'])
                finally:
                    app.close()

    def test_terminal_and_unrelated_runs_do_not_retain_pending_marker(self):
        item = dict(id=str(uuid.uuid4()), title='report', workspace='C:/work', created=1.,
                    sessionId=None, messages=[], lastRunId='run', pendingRunId='run')
        for state in ('done', 'error', 'stopped'):
            self.assertNotIn('pendingRunId', row(dict(item, state=state)))
        self.assertNotIn('pendingRunId', row(dict(item, state='idle', lastRunId='next')))
        self.assertNotIn('pendingRunId', row({k:v for k,v in item.items() if k!='pendingRunId'}))
        self.assertNotIn('pendingRunId', row(dict({k:v for k,v in item.items() if k!='pendingRunId'}, state='starting', _connecting=True)))

    def test_observation_metadata_is_bounded_and_legacy_optional(self):
        legacy = {'limited': True, 'errors': 0, 'scanned': 1, 'scope': 'workspace-top-two-levels'}
        self.assertEqual(legacy, normalize_artifact_observation(legacy))
        self.assertIsNone(normalize_artifact_observation(None))
        self.assertIsNone(normalize_artifact_observation(dict(legacy, errors='bad')))
        self.assertIsNone(normalize_artifact_observation(dict(legacy, scanned=10**9)))
