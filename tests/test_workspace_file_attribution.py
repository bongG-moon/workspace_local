"""Run-specific write attribution through the real send/stream/finish path."""
from pathlib import Path
import stat
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from local_app.artifacts import confirmed_write, snapshot, write_target
from local_app.bridge import ClaudeSession
from local_app.server import LocalApp


class FileAttributionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name).resolve()
        self.work = root / 'shared work'
        self.work.mkdir()
        self.app = LocalApp(root / 'state', command=['fixture-cli'], info={'version': 'fixture'})
        self.addCleanup(self.app.close)

    def task(self):
        sid = self.app.create(str(self.work), True)['id']
        bridge = ClaudeSession(['fixture-cli'], {}, self.work,
            lambda kind, data: self.app._emit_bridge(sid, bridge, kind, data))
        self.app.get(sid)['bridge'] = bridge
        self.start(sid, bridge)
        return sid, bridge

    def start(self, sid, bridge):
        with patch.object(bridge, '_send_when_ready'):
            self.app.send(sid, 'fixture request', [])
        bridge._turn_submitted = True
        return self.app.get(sid)['lastRunId']

    def tool(self, bridge, identifier, name, inputs, parent=None):
        bridge.handle({'type': 'assistant', 'parent_tool_use_id': parent,
            'message': {'content': [{'type': 'tool_use', 'id': identifier, 'name': name, 'input': inputs}]}})

    def result(self, bridge, identifier, native=None, *, parent=None, error=False, **frame):
        bridge.handle({'type': 'user', 'parent_tool_use_id': parent, 'tool_use_result': native,
            **frame,
            'message': {'content': [{'type': 'tool_result', 'tool_use_id': identifier, 'is_error': error,
                                     'content': 'fixture response'}]}})

    def names(self, sid, run=None):
        return {Path(row['name']).as_posix() for row in self.app.results(sid)['artifacts']
                if run is None or row['runId'] == run}

    def test_overlapping_sibling_tasks_and_external_writes(self):
        a, first = self.task()
        b, second = self.task()
        for folder in ('A', 'B'):
            (self.work / folder).mkdir()
        for bridge, name in ((first, 'A'), (second, 'B')):
            path = self.work / name / 'report.md'
            self.tool(bridge, 'write', 'Write', {'file_path': str(path)})
            path.write_text(name)
            self.result(bridge, 'write', {'type': 'create', 'filePath': str(path)})
        shared = self.work / 'shared.md'
        for bridge, name, kind in ((first, 'one', 'Write'), (second, 'two', 'Edit')):
            self.tool(bridge, 'shared', kind, {'file_path': str(shared)})
            shared.write_text(name)
            self.result(bridge, 'shared', {'type': 'create' if kind == 'Write' else 'update', 'filePath': str(shared)})
        (self.work / 'external.md').write_text('not an output')
        first.handle({'type': 'result'})
        second.handle({'type': 'result'})
        self.assertEqual({'shared.md', 'A/report.md'}, self.names(a))
        self.assertEqual({'shared.md', 'B/report.md'}, self.names(b))
        self.assertEqual({'created', 'modified'},
            {row['change'] for sid in (a, b) for row in self.app.results(sid)['artifacts'] if row['name'] == 'shared.md'})
        for sid, folder in ((a, 'A'), (b, 'B')):
            runs = self.app.file_changes(sid)['runs']
            self.assertEqual([self.app.get(sid)['lastRunId']], [run['runId'] for run in runs])
            self.assertEqual({'shared.md', folder + '/report.md'}, {row['name'] for row in runs[0]['files']})

    def test_stale_failed_staged_read_or_orphaned_results_have_no_ownership(self):
        sid, bridge = self.task()
        old = self.app.get(sid)['lastRunId']
        path = self.work / 'external.md'
        self.tool(bridge, 'earlier', 'Edit', {'file_path': str(path)})
        bridge.handle({'type': 'result'})
        new = self.start(sid, bridge)
        path.write_text('written externally')
        self.tool(bridge, 'earlier', 'Edit', {'file_path': str(path)})
        self.result(bridge, 'earlier')
        self.app._emit_bridge(sid, bridge, 'file_write', {'path': str(path), 'runId': old})
        for identifier, meta in (('cancelled', {'non_execution_kind': 'cancelled'}),
                                 ('review', {'remedy': {'kind': 'staged_for_review'}})):
            self.tool(bridge, identifier, 'Write', {'file_path': str(path)})
            self.result(bridge, identifier, tool_result_meta=[{'id': identifier, **meta}])
        for name, tool, parent, error, detail in (
                ('read', 'Read', None, False, {'filePath': str(path)}),
                ('failed', 'Write', None, True, None),
                ('staged', 'Write', None, False, {'staged': True}),
                ('child', 'Edit', 'older-agent', False, None)):
            self.tool(bridge, name, tool, {'file_path': str(path)}, parent=parent)
            self.result(bridge, name, detail, error=error, parent=parent)
        self.tool(bridge, 'agent', 'Agent', {})
        target = self.work / 'sub' / 'deep' / 'nested' / 'more' / 'report.ipynb'
        target.parent.mkdir(parents=True)
        target.write_text('result')
        self.tool(bridge, 'notebook', 'NotebookEdit', {'notebook_path': 'report.ipynb'}, 'agent')
        self.result(bridge, 'notebook', {'notebook_path': str(path)}, parent='wrong-parent')
        self.result(bridge, 'notebook', {'notebook_path': str(target)}, parent='agent',
                    tool_result_meta=[{'id': 'other-call', 'non_execution_kind': 'interrupted'}])
        bridge.handle({'type': 'result'})
        self.assertEqual(set(), self.names(sid, old))
        self.assertEqual({target.relative_to(self.work).as_posix()}, self.names(sid, new))

    def test_shell_input_and_stdout_cannot_confirm_executed_destination(self):
        sid, bridge = self.task()
        path = self.work / 'literal output.md'
        other = self.work / 'other.md'
        for identifier, tool, command, target, native in (
                ('literal', 'Bash', f"printf 'body' > '{path.as_posix()}'", path, {'interrupted': False}),
                ('script', 'PowerShell', f"python build.py '{other}'", other, {'interrupted': False, 'stdout': str(other)}),
                ('queued', 'PowerShell', f"Set-Content -LiteralPath '{other}' -Value xyz", other,
                 {'interrupted': False, 'backgroundTaskId': 'task-id'})):
            self.tool(bridge, identifier, tool, {'command': command})
            target.write_text(identifier)
            self.result(bridge, identifier, native)
        bridge.handle({'type': 'result'})
        self.assertEqual(set(), self.names(sid))
        self.assertIsNone(write_target('PowerShell', {'command': 'Set-Content -Path C:/one.md,two.md -Value ok'}))
        for command in (f"echo 'x > {other}'", f"echo text > '{other}' && true",
                        "echo text > relative.txt", f"false && printf 'x' > '{other}'"):
            self.assertIsNone(write_target('Bash', {'command': command}))

    def test_malformed_native_receipts_and_conflicting_paths_do_not_confirm_writes(self):
        path = str(self.work / 'folder' / 'report.md')
        other = str(self.work / 'sibling' / 'report.md')
        for native in ([], 'bad', {}, {'interrupted': False}, {'filePath': path},
                       {'filePath': path, 'type': 'invalid'}, {'filePath': other, 'type': 'create'}):
            self.assertIsNone(confirmed_write('Write', path, native))
        self.assertIsNone(confirmed_write('Write', 'folder/report.md', {'filePath': other, 'type': 'update'}))
        self.assertIsNone(confirmed_write('Write', 'folder/report.md', None))
        self.assertIsNone(confirmed_write('Write', path, None, content=f'File created successfully at: {other}'))
        self.assertIsNone(confirmed_write('Write', path, None, content=f'Read {path} successfully'))
        self.assertEqual({'path': path, 'change': 'created'},
                         confirmed_write('Write', path, None, content=f'File created successfully at: {path}'))
        self.assertEqual({'path': path, 'change': 'modified'}, confirmed_write('Edit', path, None,
            content=[{'type': 'text', 'text': f'The file {path} has been updated successfully.'}]))
        self.assertEqual({'path': path, 'change': 'modified'},
                         confirmed_write('Write', 'folder/report.md', {'filePath': path, 'type': 'update'}))

    def test_replaced_parent_stops_target_metadata_lookup_at_the_junction(self):
        folder = self.work / 'junction'
        target = folder / 'outside.md'
        original = Path.lstat
        def lstat(path):
            if path == target:
                self.fail('target lookup through a junction')
            if path == folder:
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400, st_reparse_tag=0xA0000003)
            return original(path)
        with patch.object(Path, 'lstat', lstat):
            observed = snapshot(self.work, paths=['junction/outside.md'])
        self.assertEqual({}, observed.files)
