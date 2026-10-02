"""Product workflow regressions without touching a real Claude profile."""
import json
from pathlib import Path
import tempfile
import threading
import sys
import stat
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import uuid

from local_app.artifacts import changes, snapshot
from local_app.bridge import probe_cli
from local_app.history import HistoryStore, row, safe
from local_app.server import LocalApp, Server, workspace_folder
from local_app.windows_paths import desktop_folder, DESKTOP_UNAVAILABLE, redirects_path


class WorkspaceLocationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='workspace-location-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.desktop = self.root / 'OneDrive 회사' / '바탕 화면'
        self.desktop.mkdir(parents=True)

    def app(self, suffix='state', **kwargs):
        with patch('local_app.server.desktop_folder', return_value=self.desktop):
            app = LocalApp(self.root / suffix, command=['fixture-cli'], **kwargs)
        self.addCleanup(app.close)
        return app

    def test_redirected_desktop_default_is_lazy_and_internal_state_stays_separate(self):
        app = self.app()
        expected = self.desktop / 'Company Workspace'
        self.assertFalse(expected.exists())
        boot = app.bootstrap()
        self.assertEqual(str(self.desktop), boot['defaultWorkspace'])
        self.assertEqual(str(expected), boot['managedWorkspaceRoot'])
        self.assertIsNone(boot['workspaceLocationError'])
        created = app.create('', True, managed=True, title='실제 파일명이 아닌 업무 제목')
        self.assertEqual(expected, Path(created['workspace']).parent)
        self.assertEqual(self.root / 'state', app.history.root)
        self.assertFalse((self.desktop / 'history.json').exists())

    def test_explicit_existing_parent_and_existing_workspace_modes_are_distinct(self):
        app = self.app()
        chosen = self.root / '선택 위치'
        chosen.mkdir()
        created = app.create('', True, managed=True, managed_root=str(chosen))
        self.assertEqual(chosen, Path(created['workspace']).parent)
        existing = app.create(str(chosen), True, managed=False)
        self.assertEqual(str(chosen), existing['workspace'])
        self.assertFalse((self.desktop / 'Company Workspace').exists())
        self.assertEqual(str(self.desktop / 'Company Workspace'), app.bootstrap()['managedWorkspaceRoot'])

    def test_bad_parent_does_not_create_dirs_or_session_and_does_not_override_existing_mode(self):
        app = self.app()
        file = self.root / 'file.txt'
        file.write_text('preserved', encoding='utf-8')
        for selected in ['', 'relative', str(self.root / 'missing'), str(file), True]:
            with self.subTest(selected=selected), self.assertRaises((ValueError, OSError)):
                app.create('', True, managed=True, managed_root=selected)
        with self.assertRaises(ValueError):
            app.create(str(self.desktop), True, managed=False, managed_root=str(self.root))
        self.assertEqual({}, app.sessions)
        self.assertFalse((self.root / 'missing').exists())
        self.assertEqual('preserved', file.read_text(encoding='utf-8'))

    def test_unavailable_desktop_does_not_fall_back_and_user_can_choose_existing_location(self):
        with patch('local_app.server.desktop_folder', side_effect=ValueError(DESKTOP_UNAVAILABLE)):
            app = LocalApp(self.root / 'state', command=['fixture-cli'])
        self.addCleanup(app.close)
        self.assertIsNone(app.bootstrap()['managedWorkspaceRoot'])
        self.assertEqual('', app.bootstrap()['defaultWorkspace'])
        self.assertEqual(DESKTOP_UNAVAILABLE, app.bootstrap()['workspaceLocationError'])
        with self.assertRaisesRegex(ValueError, '바탕화면'):
            app.create('', True, managed=True)
        self.assertFalse((app.state / 'workspaces').exists())
        created = app.create('', True, managed=True, managed_root=str(self.desktop))
        self.assertEqual(self.desktop, Path(created['workspace']).parent)

    def test_demo_and_explicit_test_roots_never_resolve_interactive_desktop(self):
        with patch('local_app.server.desktop_folder', side_effect=AssertionError('no Desktop lookup')):
            demo = LocalApp(self.root / 'demo', demo=True)
            isolated = LocalApp(self.root / 'state', command=['fixture-cli'],
                                managed_workspace_root=self.root / 'isolated')
        self.addCleanup(demo.close)
        self.addCleanup(isolated.close)
        self.assertEqual(demo.state / 'workspaces', Path(demo.create('', True, managed=True)['workspace']).parent)
        self.assertEqual(self.root / 'isolated', Path(isolated.create('', True, managed=True)['workspace']).parent)
        with self.assertRaises(ValueError):
            demo.create('', True, managed=True, managed_root=str(self.desktop))
        self.assertEqual([], list(self.desktop.iterdir()))

    def test_old_task_files_and_location_are_unchanged_after_new_default(self):
        app = self.app(managed_workspace_root=self.root / 'state' / 'workspaces')
        old = app.create('', True, managed=True)
        original = Path(old['workspace']) / '원본.md'
        original.write_text('preserve', encoding='utf-8')
        app.close()
        restored = self.app()
        self.assertEqual(old['workspace'], restored.get(old['id'])['workspace'])
        self.assertFalse(restored.get(old['id'])['trusted'])
        self.assertEqual('preserve', original.read_text(encoding='utf-8'))
        new = restored.create('', True, managed=True)
        self.assertEqual(self.desktop / 'Company Workspace', Path(new['workspace']).parent)

    def test_name_collision_never_reuses_existing_directory_or_session(self):
        app = self.app()
        first = app.create('', True, managed=True)
        original = Path(first['workspace']) / 'preserve.txt'
        original.write_text('original', encoding='utf-8')
        with patch('local_app.server.uuid.uuid4', return_value=uuid.UUID(first['id'])), self.assertRaises(FileExistsError):
            app.create('', True, managed=True)
        self.assertEqual(1, len(app.sessions))
        self.assertEqual('original', original.read_text(encoding='utf-8'))

    def test_missing_desktop_is_not_recreated(self):
        app = self.app()
        self.desktop.rmdir()
        with self.assertRaises(FileNotFoundError):
            app.create('', True, managed=True)
        self.assertFalse(self.desktop.exists())

    def test_known_folder_result_uses_redirected_path_and_rejects_invalid_results(self):
        with patch('local_app.windows_paths._known_desktop_path', return_value=str(self.desktop)):
            self.assertEqual(self.desktop, desktop_folder())
        for value in ('relative', str(self.root / 'missing')):
            with patch('local_app.windows_paths._known_desktop_path', return_value=value), self.assertRaisesRegex(ValueError, '바탕화면'):
                desktop_folder()

    def test_cloud_tags_allow_workspace_creation_and_use_but_state_stays_strict(self):
        original = Path.lstat
        def cloud_info(path, *args, **kwargs):
            info = original(path, *args, **kwargs)
            if path == self.desktop:
                attrs = {key: getattr(info, key) for key in dir(info) if key.startswith('st_')}
                attrs.update(st_file_attributes=0x400, st_reparse_tag=0x9000301A)
                return SimpleNamespace(**attrs)
            return info
        app = self.app()
        with patch.object(Path, 'lstat', cloud_info):
            created = app.create('', True, managed=True)
            task = app.get(created['id'])
            self.assertEqual(Path(created['workspace']), workspace_folder(task))
            document = Path(created['workspace']) / '결과.txt'
            document.write_text('synthetic cloud-tag fixture', encoding='utf-8')
            self.assertEqual(1, len(app.files(created['id'])))
            with self.assertRaises(ValueError):
                safe(self.desktop / 'internal-state.json')

    def test_junction_and_unknown_reparse_tags_are_still_blocked(self):
        for tag in (0xA0000003, 0xA000000C, 0, 0x9000001B):
            with self.subTest(tag=tag):
                info = SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400, st_reparse_tag=tag)
                self.assertTrue(redirects_path(info))
        for variant in range(16):
            info = SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400,
                                   st_reparse_tag=0x9000001A | (variant << 12))
            self.assertFalse(redirects_path(info))
        self.assertTrue(redirects_path(SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0)))


class ProductTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.workspace = self.root / '업무 자료'
        self.workspace.mkdir()
        self.app = LocalApp(self.root / 'state', command=['fixture-cli'], info={'version': 'fixture'},
                            managed_workspace_root=self.root / 'managed-workspaces')
        self.addCleanup(self.app.close)
        self.sid = self.app.create(str(self.workspace), True)['id']

    def start_request(self, changes_during_send=None):
        bridge = Mock(closed=False, _effort_baselines={})
        bridge.model_state.return_value = {}
        bridge.ready = threading.Event()
        bridge.ready.set()
        bridge.send.side_effect = changes_during_send
        self.app.get(self.sid)['bridge'] = bridge
        self.app.send(self.sid, '문서 정리', [])
        return bridge

    def test_managed_workspace_requires_trust_and_ignores_title_as_path(self):
        with self.assertRaises(ValueError):
            self.app.create('', False, managed=True, title='보고서')
        self.assertFalse(self.app.managed_workspace_root.exists())
        with patch('local_app.server.ClaudeSession') as cli:
            created = self.app.create('', True, managed=True, title='../../다른 폴더')
            cli.assert_not_called()
        path = Path(created['workspace'])
        self.assertTrue(path.is_dir())
        self.assertEqual(self.app.managed_workspace_root, path.parent)
        self.assertEqual('../../다른 폴더', created['title'])
        self.assertEqual(str(path.parent), self.app.bootstrap()['managedWorkspaceRoot'])

    def test_titles_and_pins_validate_before_mutation_and_sort(self):
        second = self.app.create(str(self.workspace), True, title='두 번째 업무')
        self.app.update_session(self.sid, {'title': '이름을 바꾼 업무', 'pinned': True})
        with self.assertRaises(ValueError):
            self.app.update_session(self.sid, {'title': '반영되면 안 됨', 'pinned': 'yes'})
        self.assertEqual('이름을 바꾼 업무', self.app.get(self.sid)['title'])
        self.assertEqual(self.sid, self.app.bootstrap()['sessions'][0]['id'])
        self.app.update_session(second['id'], {'title': '가장 최근 업무'})
        self.assertEqual(self.sid, self.app.bootstrap()['sessions'][0]['id'])
        self.app.update_session(self.sid, {'pinned': False})
        restored = LocalApp(self.app.state, command=['fixture-cli'])
        self.addCleanup(restored.close)
        self.assertFalse(restored.get(self.sid)['trusted'])
        self.assertEqual('이름을 바꾼 업무', restored.get(self.sid)['title'])

    def test_history_retains_more_than_forty_and_preserves_original_files(self):
        items = []
        for index in range(45):
            items.append({'id': str(uuid.uuid4()), 'title': str(index), 'workspace': str(self.workspace),
                          'created': float(index), 'sessionId': None, 'messages': [], 'pinned': index == 0})
        store = HistoryStore(self.root / 'many')
        store.save(items)
        loaded = HistoryStore(self.root / 'many').load()
        self.assertEqual(45, len(loaded))
        self.assertEqual(items[0]['id'], loaded[0]['id'])
        self.assertTrue(loaded[0]['pinned'])
        self.assertEqual(44, loaded[-1]['updated'])

    def test_run_results_are_observed_differences_not_all_source_files(self):
        unchanged = self.workspace / '원본.txt'
        changed = self.workspace / '수정.md'
        unchanged.write_text('unchanged', encoding='utf-8')
        changed.write_text('before', encoding='utf-8')
        self.start_request()
        changed.write_text('after, bigger', encoding='utf-8')
        made = self.workspace / '보고서.txt'
        made.write_text('result', encoding='utf-8')
        self.app.emit(self.sid, 'result', {'sessionId': str(uuid.uuid4())})
        result = self.app.results(self.sid)
        self.assertEqual({'수정.md': 'modified', '보고서.txt': 'created'},
                         {r['name']: r['change'] for r in result['artifacts']})
        self.assertTrue(all(r['runId'] == result['lastRunId'] for r in result['artifacts']))
        self.assertNotIn('원본.txt', {r['name'] for r in result['artifacts']})
        self.assertEqual('unchanged', unchanged.read_text(encoding='utf-8'))
        self.app.emit(self.sid, 'result', {})
        self.assertEqual(2, len(self.app.results(self.sid)['artifacts']))
        restored = LocalApp(self.app.state, command=['fixture-cli'])
        self.addCleanup(restored.close)
        self.assertEqual(result['artifacts'], restored.results(self.sid)['artifacts'])

    def test_error_and_stop_capture_only_current_observation_once(self):
        for index, (kind, data) in enumerate([('error', {'message': 'fixture failure'}), ('status', {'state': 'stopped'})]):
            self.start_request()
            (self.workspace / ('report-%d.txt' % index)).write_text('observed', encoding='utf-8')
            self.app.emit(self.sid, kind, data)
            self.assertEqual(index + 1, len(self.app.results(self.sid)['artifacts']))
            self.assertNotIn('_artifactSnapshot', self.app.get(self.sid))

    def test_failed_send_is_not_left_running(self):
        def fail(prompt):
            (self.workspace / 'partial.txt').write_text('partial', encoding='utf-8')
            raise ValueError('send failed')
        with self.assertRaises(ValueError):
            self.start_request(fail)
        self.assertEqual('error', self.app.get(self.sid)['state'])
        self.assertEqual('partial.txt', self.app.results(self.sid)['artifacts'][0]['name'])

    def test_scanning_is_bounded_and_does_not_claim_unsampled_files_are_new(self):
        for index in range(4):
            (self.workspace / ('document-%d.txt' % index)).write_text('input', encoding='utf-8')
        before = snapshot(self.workspace, max_files=1)
        after = snapshot(self.workspace)
        self.assertTrue(before.limited)
        self.assertEqual(1, len(before.files))
        self.assertEqual([], changes(self.workspace, before, after, 'fixture-run', 1.0))
        private = self.workspace / '.private'
        private.mkdir()
        (private / 'secret.txt').write_text('never listed', encoding='utf-8')
        deep = self.workspace / 'first' / 'second'
        deep.mkdir(parents=True)
        (deep / 'deep.txt').write_text('never listed', encoding='utf-8')
        self.assertEqual(4, len(snapshot(self.workspace).files))

    def test_symlink_documents_and_directories_are_not_followed(self):
        outside = self.root / 'outside'
        outside.mkdir()
        document = outside / 'private.txt'
        document.write_text('private', encoding='utf-8')
        try:
            (self.workspace / 'shortcut').symlink_to(outside, target_is_directory=True)
            (self.workspace / 'linked.txt').symlink_to(document)
        except OSError:
            self.skipTest('This account cannot create symbolic links')
        self.assertEqual({}, snapshot(self.workspace).files)

    def test_replaced_workspace_link_cannot_redirect_existing_session(self):
        document = self.workspace / 'source.txt'
        document.write_text('original', encoding='utf-8')
        with patch('local_app.server.linked', side_effect=lambda path: path == self.workspace):
            for action in (lambda: self.app.files(self.sid),
                           lambda: self.app.allowed_file(self.sid, str(document)),
                           lambda: self.app.send(self.sid, '정리', [])):
                with self.assertRaisesRegex(ValueError, '실제 위치가 바뀌었습니다'):
                    action()
        self.assertEqual('original', document.read_text(encoding='utf-8'))
        self.assertIsNone(self.app.get(self.sid)['bridge'])

    def test_reconnect_probes_injected_cli_without_replaying_running_work(self):
        bridge = self.start_request()
        self.app.error = 'old failure'
        with patch('local_app.server.resolve_cli') as resolve, patch('local_app.server.probe_cli', return_value={'version': 'fresh'}) as probe:
            result = self.app.reconnect(self.sid)
        probe.assert_called_once_with(['fixture-cli'])
        resolve.assert_not_called()
        bridge.send.assert_called_once()
        bridge.interrupt.assert_not_called()
        bridge.close.assert_not_called()
        self.assertIsNone(result['error'])
        self.assertEqual('fresh', result['version'])
        self.assertEqual('starting', self.app.get(self.sid)['state'])

    def test_model_control_wait_does_not_hold_app_lock_or_overwrite_history(self):
        with self.assertRaises(ValueError):
            self.app.set_model(self.sid, 'model')
        bridge = self.start_request()
        with self.assertRaises(ValueError):
            self.app.set_model(self.sid, 'model')
        self.app.emit(self.sid, 'result', {})
        def control(model):
            state = {'model': model, 'modelOverride': model}
            bridge.model_state.return_value = state
            thread = threading.Thread(target=lambda: self.app.emit(self.sid, 'model_changed', state))
            thread.start()
            thread.join(1)
            self.assertFalse(thread.is_alive(), 'app lock deadlocked model response')
            return state
        bridge.set_model.side_effect = control
        result = self.app.set_model(self.sid, 'company-model')
        self.assertEqual('company-model', result['modelOverride'])
        self.assertIsNone(self.app.set_model(self.sid, None)['modelOverride'])
        self.assertNotIn('modelOverride', row(self.app.get(self.sid)))

    def test_real_child_stream_and_model_change_then_reset(self):
        command = [sys.executable, '-X', 'utf8', str(Path(__file__).parent / 'fixtures/workspace_fake_cli.py')]
        self.app.command = command
        self.app.info = probe_cli(command)
        self.app.send(self.sid, 'STREAM_PROTOCOL_TEST', [])
        deadline = time.monotonic() + 5
        while self.app.get(self.sid)['state'] not in {'done', 'error'} and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertEqual('done', self.app.get(self.sid)['state'])
        item = self.app.get(self.sid)
        self.assertEqual(1, sum(m['role'] == 'assistant' for m in item['messages']))
        self.assertTrue(any(e['type'] == 'assistant_delta' for e in item['events']))
        selected = self.app.set_model(self.sid, 'fake-alternative')
        self.assertEqual('fake-alternative', selected['session']['connection']['model'])
        with self.assertRaises(ValueError):
            self.app.set_model(self.sid, 'fake-rejected')
        self.assertEqual('fake-alternative', item['modelOverride'])
        reset = self.app.set_model(self.sid, None)
        self.assertEqual('fake-only', reset['session']['connection']['model'])
        self.assertIsNone(reset['modelOverride'])
        item['bridge'].close()
        closed = self.app.public(item)
        self.assertFalse(closed['connection']['capabilities']['setModel'])
        self.assertFalse(closed['connection']['connected'])
        self.assertIsNone(closed['modelOverride'])

    def test_new_routes_share_auth_and_use_expected_json_contract(self):
        server = Server(self.app)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            def request(route, data=None, authenticated=True):
                headers = {'Content-Type': 'application/json'}
                if authenticated:
                    headers['Authorization'] = 'Bearer ' + self.app.token
                req = Request(server.origin + route, headers=headers,
                              data=None if data is None else json.dumps(data).encode())
                with urlopen(req, timeout=3) as response:
                    return json.load(response)
            with self.assertRaises(HTTPError) as error:
                request('/api/session/update', {'id': self.sid, 'pinned': True}, False)
            self.assertEqual(403, error.exception.code)
            created = request('/api/create', {'managed': True, 'trusted': True, 'title': '주간 보고'})
            self.assertEqual('주간 보고', created['title'])
            self.assertTrue(request('/api/session/update', {'id': created['id'], 'pinned': True})['pinned'])
            self.assertEqual([], request('/api/results?id=' + created['id'])['artifacts'])
            custom = request('/api/create', {'managed': True, 'trusted': True, 'managedRoot': str(self.workspace)})
            self.assertEqual(self.workspace, Path(custom['workspace']).parent)
            with patch('local_app.server.probe_cli', return_value={'version': 'fresh'}):
                self.assertTrue(request('/api/reconnect', {})['ok'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
