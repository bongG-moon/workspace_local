"""Optional extension isolation, request boundaries and finite worker ownership."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

from local_app.extensions import ExtensionError, ExtensionHost, MAX_PAYLOAD, UI_CSP, no_links
from local_app.owned_process import CancelledError
from local_app.server import LocalApp, Server


def package(root, extension_id='example.tools', *, worker=None):
    directory = root / 'extensions' / extension_id
    directory.mkdir(parents=True, exist_ok=True)
    source = worker or "import json,sys\nr=json.load(sys.stdin)\nprint(json.dumps({'ok':True,'result':{'protocol':r['protocol'],'workspace':r['context']['workspace'],'payload':r['payload']}}))\n"
    contents = {'worker.py': source, 'ui.html': '<!doctype html><html><body><script>parent.postMessage({type:"ready"},"*")</script></body></html>'}
    for name, value in contents.items():
        (directory / name).write_text(value, encoding='utf-8')
    manifest = {'schemaVersion': 1, 'id': extension_id, 'name': 'Example tools', 'version': '1.0.0',
                'hostApi': 1, 'entrypoint': 'worker.py', 'ui': 'ui.html', 'actions': ['status', 'install'],
                'files': {name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in contents}}
    (directory / 'extension.json').write_text(json.dumps(manifest), encoding='utf-8')
    return directory, manifest


def completed(result=None):
    return subprocess.CompletedProcess([], 0, json.dumps({'ok': True, 'result': result}).encode(), b'')


class ExtensionHostTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.host = ExtensionHost(self.root / 'state', runner=Mock(return_value=completed({'ready': True})))
        self.addCleanup(self.host.close)

    def test_absent_extensions_never_spawn_walk_create_or_connect_and_cache_empty(self):
        with patch('local_app.extensions.os.scandir', side_effect=AssertionError('no scan')), \
             patch('local_app.extensions.subprocess.Popen', side_effect=AssertionError('no process')), \
             patch('socket.create_connection', side_effect=AssertionError('no network')):
            self.assertEqual({'extensions': [], 'warnings': []}, self.host.snapshot())
            with patch.object(Path, 'exists', side_effect=AssertionError('cached')):
                self.assertEqual([], self.host.snapshot()['extensions'])
        self.assertEqual([], list(self.root.iterdir()))
        self.host.runner.assert_not_called()

    def test_discovery_only_reads_manifest_and_explicit_refresh_discovers_new_install(self):
        self.host.snapshot()
        directory, _ = package(self.root)
        (directory / 'worker.py').write_text('must not import or execute', encoding='utf-8')
        self.assertFalse(self.host.snapshot()['extensions'])
        result = self.host.snapshot(refresh=True)
        self.assertEqual('example.tools', result['extensions'][0]['id'])
        self.assertNotIn('files', result['extensions'][0])
        self.assertFalse((self.root / 'extension-data').exists())
        self.host.runner.assert_not_called()

    def test_incompatible_manifest_is_omitted_without_affecting_other_extensions(self):
        directory, manifest = package(self.root)
        package(self.root, 'example.good')
        for changes in ({'hostApi': 2}, {'schemaVersion': True}, {'id': 'other'}, {'actions': ['bad action']},
                        {'entrypoint': '../worker.py'}, {'ui': 'C:/index.html'}, {'version': 'latest'}):
            with self.subTest(changes=changes):
                (directory / 'extension.json').write_text(json.dumps(manifest | changes), encoding='utf-8')
                result = self.host.snapshot(refresh=True)
                self.assertEqual(['example.good'], [row['id'] for row in result['extensions']])
                self.assertEqual(1, len(result['warnings']))

    def test_path_escape_extra_missing_and_modified_files_cannot_execute(self):
        directory, manifest = package(self.root)
        for name in ('../outside.py', '/outside.py', 'C:/outside.py', 'dir\\worker.py', 'a/../b.py'):
            with self.subTest(name=name), self.assertRaises(ExtensionError):
                (directory / 'extension.json').write_text(json.dumps(manifest | {'entrypoint': name}), encoding='utf-8')
                self.host.action('example.tools', 'status', {})
        (directory / 'extension.json').write_text(json.dumps(manifest), encoding='utf-8')
        (directory / 'extra.py').write_text('unexpected', encoding='utf-8')
        with self.assertRaises(ExtensionError):
            self.host.action('example.tools', 'status', {})
        (directory / 'extra.py').unlink()
        (directory / 'worker.py').write_text('changed', encoding='utf-8')
        with self.assertRaises(ExtensionError):
            self.host.action('example.tools', 'status', {})
        (directory / 'worker.py').unlink()
        with self.assertRaises(ExtensionError):
            self.host.action('example.tools', 'status', {})
        self.host.runner.assert_not_called()

    def test_reparse_point_in_install_ancestry_is_rejected(self):
        info = Mock(st_mode=0, st_file_attributes=0x400)
        with patch.object(Path, 'lstat', return_value=info), self.assertRaises(ExtensionError):
            no_links(self.root / 'extensions' / 'example.tools')

    def test_versioned_package_switch_ignores_inert_previous_versions_but_rejects_mixed_manifest(self):
        directory, manifest = package(self.root)
        prefix = 'packages/' + 'a' * 64
        selected = directory / prefix
        selected.mkdir(parents=True)
        for name in manifest['files']:
            (directory / name).rename(selected / name)
        current = manifest | {'entrypoint': prefix + '/worker.py', 'ui': prefix + '/ui.html',
                              'files': {prefix + '/' + name: checksum for name, checksum in manifest['files'].items()}}
        previous = directory / 'packages' / ('b' * 64)
        previous.mkdir()
        (previous / 'worker.py').write_text('raise AssertionError("old package executed")', encoding='utf-8')
        (directory / '.ax-extension-owner.json').write_text('{"owned":true}', encoding='utf-8')
        (directory / 'extension.json').write_text(json.dumps(current), encoding='utf-8')
        self.host.action('example.tools', 'status', {})
        self.assertTrue(Path(self.host.runner.call_args.args[0][-1]).samefile(selected / 'worker.py'))
        (selected / 'unexpected.py').write_text('unexpected', encoding='utf-8')
        with self.assertRaises(ExtensionError):
            self.host.view('example.tools')
        (selected / 'unexpected.py').unlink()
        current['files']['packages/' + 'b' * 64 + '/worker.py'] = hashlib.sha256((previous / 'worker.py').read_bytes()).hexdigest()
        (directory / 'extension.json').write_text(json.dumps(current), encoding='utf-8')
        with self.assertRaises(ExtensionError):
            self.host.view('example.tools')

    def test_view_ticket_is_single_use_short_lived_and_does_not_launch_worker(self):
        directory, _ = package(self.root)
        clock = Mock(return_value=100.0)
        self.host.clock = clock
        first = self.host.view('example.tools')
        ticket = parse_qs(urlsplit(first['url']).query)['ticket'][0]
        self.assertEqual((directory / 'ui.html').read_bytes(), self.host.consume_view('example.tools', ticket))
        with self.assertRaises(ExtensionError) as caught:
            self.host.consume_view('example.tools', ticket)
        self.assertEqual(403, caught.exception.status)
        second = self.host.view('example.tools')
        ticket = parse_qs(urlsplit(second['url']).query)['ticket'][0]
        clock.return_value = 131.0
        with self.assertRaises(ExtensionError):
            self.host.consume_view('example.tools', ticket)
        self.host.runner.assert_not_called()

    def test_reopen_and_close_revoke_unconsumed_views(self):
        package(self.root)
        first = parse_qs(urlsplit(self.host.view('example.tools')['url']).query)['ticket'][0]
        second = parse_qs(urlsplit(self.host.view('example.tools')['url']).query)['ticket'][0]
        with self.assertRaises(ExtensionError):
            self.host.consume_view('example.tools', first)
        self.host.close()
        with self.assertRaises(ExtensionError):
            self.host.consume_view('example.tools', second)
        with self.assertRaises(ExtensionError):
            self.host.view('example.tools')

    def test_declared_action_only_and_bounded_json_before_execution(self):
        package(self.root)
        for action, payload in [('undeclared', {}), ('status', []), ('status', {'huge': 'x' * MAX_PAYLOAD}),
                                ('status', {'value': float('nan')})]:
            with self.subTest(action=action), self.assertRaises(ExtensionError):
                self.host.action('example.tools', action, payload)
        self.host.runner.assert_not_called()
        self.assertFalse(self.host.data_root.exists())

    def test_worker_contract_has_isolation_flags_sanitized_environment_and_external_data(self):
        directory, _ = package(self.root)
        with patch.dict(os.environ, {'CLAUDE_CODE_OAUTH_TOKEN': 'private', 'OPENAI_API_KEY': 'private',
                                     'COMPANY_WORKSPACE_TOKEN': 'private', 'PYTHONPATH': 'untrusted'}):
            result = self.host.action('example.tools', 'status', {'q': 'hello'}, context={'workspace': 'C:/allowed'})
        self.assertEqual({'ok': True, 'result': {'ready': True}}, result)
        args, options = self.host.runner.call_args
        self.assertEqual(['-I', '-B', '-X', 'utf8'], args[0][1:5])
        self.assertTrue(Path(args[0][-1]).samefile(directory / 'worker.py'))
        self.assertNotIn('shell', options)
        for key in ('CLAUDE_CODE_OAUTH_TOKEN', 'OPENAI_API_KEY', 'COMPANY_WORKSPACE_TOKEN', 'PYTHONPATH'):
            self.assertNotIn(key, options['env'])
        request = json.loads(options['input'])
        self.assertEqual({'workspace': 'C:/allowed'}, request['context'])
        self.assertEqual(1, request['protocol'])
        self.assertTrue(Path(request['dataDirectory']).samefile(self.root / 'extension-data' / 'example.tools'))
        self.assertTrue(Path(request['dataDirectory']).is_dir())
        self.assertIs(options['cancel_event'], self.host._cancel)
        self.assertEqual(120, options['timeout'])

    def test_timeout_is_verification_required_no_retry_and_release_serialization(self):
        package(self.root)
        self.host.runner.side_effect = subprocess.TimeoutExpired('worker', 120)
        with self.assertRaises(ExtensionError) as caught:
            self.host.action('example.tools', 'install', {})
        self.assertEqual('extension_interrupted', caught.exception.code)
        self.assertTrue(caught.exception.verification_required)
        self.host.runner.assert_called_once()
        self.assertFalse(self.host._active)

    def test_worker_failures_never_return_stderr_or_echo_input_key(self):
        package(self.root)
        self.host.runner.return_value = subprocess.CompletedProcess([], 3, b'', b'secret traceback')
        with self.assertRaises(ExtensionError) as caught:
            self.host.action('example.tools', 'install', {})
        self.assertNotIn('secret', str(caught.exception))
        self.assertTrue(caught.exception.verification_required)
        self.host.runner.return_value = subprocess.CompletedProcess([], 0, json.dumps({'ok': False, 'error': 'invalid private-key-value'}).encode(), b'')
        with self.assertRaises(ExtensionError) as caught:
            self.host.action('example.tools', 'status', {'apiKey': 'private-key-value'})
        self.assertNotIn('private-key-value', str(caught.exception))

    def test_one_action_per_extension_and_shutdown_cancels_without_retry(self):
        package(self.root)
        package(self.root, 'other.tools')
        started = threading.Event()
        failures = []
        def runner(args, **options):
            if 'other.tools' in args[-1]:
                return completed('other ready')
            started.set()
            options['cancel_event'].wait(3)
            raise CancelledError()
        self.host.runner = runner
        def invoke():
            try:
                self.host.action('example.tools', 'install', {})
            except ExtensionError as exc:
                failures.append(exc)
        thread = threading.Thread(target=invoke)
        thread.start()
        self.addCleanup(lambda: thread.join(3))
        self.assertTrue(started.wait(2))
        with self.assertRaises(ExtensionError) as caught:
            self.host.action('example.tools', 'status', {})
        self.assertEqual('extension_busy', caught.exception.code)
        self.assertEqual('other ready', self.host.action('other.tools', 'status', {})['result'])
        self.assertTrue(self.host.close())
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(1, len(failures))
        self.assertTrue(failures[0].verification_required)

    def test_shutdown_during_file_verification_never_starts_worker_or_issues_view(self):
        package(self.root)
        for operation in ('view', 'action'):
            with self.subTest(operation=operation):
                runner = Mock(return_value=completed('unexpected'))
                host = ExtensionHost(self.root / 'state', runner=runner)
                verified = host._verified('example.tools')
                started, release = threading.Event(), threading.Event()
                outcomes = []
                def verify(_):
                    started.set()
                    release.wait(2)
                    return verified
                host._verified = verify
                def invoke():
                    try:
                        host.view('example.tools') if operation == 'view' else host.action('example.tools', 'status', {})
                    except ExtensionError as exc:
                        outcomes.append(exc.status)
                thread = threading.Thread(target=invoke)
                thread.start()
                self.assertTrue(started.wait(1))
                self.assertTrue(host.close())
                release.set()
                thread.join(2)
                self.assertEqual([409], outcomes)
                runner.assert_not_called()
                self.assertFalse(host._tickets)

    def test_real_finite_worker_round_trip(self):
        package(self.root)
        host = ExtensionHost(self.root / 'state')
        self.addCleanup(host.close)
        result = host.action('example.tools', 'status', {'message': '정상'})
        self.assertEqual({'protocol': 1, 'workspace': None, 'payload': {'message': '정상'}}, result['result'])
        self.assertFalse(list((self.root / 'extensions').rglob('__pycache__')))

    def test_native_directory_capability_is_bound_expiring_and_revalidated(self):
        package(self.root)
        package(self.root, 'example.other')
        selected = self.root / 'selected'; selected.mkdir()
        clock = Mock(return_value=100.0)
        self.host.clock = clock
        picker = Mock(return_value={'status': 'success', 'paths': [str(selected)]})
        choice = self.host.pick_directory('example.tools', picker)
        picker.assert_called_once_with('folder')
        self.assertEqual(str(selected), choice['path'])
        context = self.host.directory_context('example.tools', choice['directoryToken'])
        self.assertEqual(str(selected), context['selectedDirectory'])
        with self.assertRaises(ExtensionError):
            self.host.directory_context('example.other', choice['directoryToken'])
        next_choice = self.host.pick_directory('example.tools', picker)
        with self.assertRaises(ExtensionError):
            self.host.directory_context('example.tools', choice['directoryToken'])
        clock.return_value = 1001.0
        with self.assertRaises(ExtensionError):
            self.host.directory_context('example.tools', next_choice['directoryToken'])
        clock.return_value = 2000.0
        choice = self.host.pick_directory('example.tools', picker)
        selected.rename(self.root / 'previous')
        selected.mkdir()
        with self.assertRaises(ExtensionError):
            self.host.directory_context('example.tools', choice['directoryToken'])
        self.host.runner.assert_not_called()

    def test_directory_cancel_and_root_selection_do_not_create_authorization(self):
        package(self.root)
        self.assertEqual({'cancelled': True}, self.host.pick_directory('example.tools', lambda _: {'status': 'cancel', 'paths': []}))
        self.assertFalse(self.host._directories)
        with self.assertRaises(ExtensionError):
            self.host.pick_directory('example.tools', lambda _: {'status': 'success', 'paths': [self.root.anchor]})
        self.assertFalse(self.host._directories)


class ExtensionRoutesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        package(self.root)
        self.app = LocalApp(self.root / 'state', command=['never-start'], managed_workspace_root=self.root / 'managed')
        self.addCleanup(self.app.close)
        self.app.extensions.runner = Mock(return_value=completed('ready'))
        self.server = Server(self.app, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)

    def shutdown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request(self, path, data=None, auth=True, origin=None):
        headers = {'Origin': origin or self.server.origin}
        if auth:
            headers['Authorization'] = 'Bearer ' + self.app.token
        if data is not None:
            headers['Content-Type'] = 'application/json'
        return urlopen(Request(self.server.origin + path, data=json.dumps(data).encode() if data is not None else None, headers=headers), timeout=5)

    def post(self, path, data, **kwargs):
        with self.request(path, data, **kwargs) as response:
            return json.loads(response.read())

    def test_bootstrap_metadata_and_authenticated_catalog(self):
        boot = self.app.bootstrap()
        self.assertEqual('example.tools', boot['extensions'][0]['id'])
        self.assertEqual('0.23.32', boot['workspaceVersion'])
        for path, data in [('/api/extensions', None), ('/api/extensions/example.tools/view', {}),
                           ('/api/extensions/example.tools/action', {'action': 'status'})]:
            with self.subTest(path=path), self.assertRaises(HTTPError) as caught:
                self.request(path, data, auth=False)
            self.assertEqual(403, caught.exception.code)
        self.app.extensions.runner.assert_not_called()

    def test_ticket_iframe_get_has_isolated_csp_and_cannot_be_replayed(self):
        view = self.post('/api/extensions/example.tools/view', {})
        with self.request(view['url'], auth=False) as response:
            self.assertEqual(UI_CSP, response.headers['Content-Security-Policy'])
            self.assertEqual('no-store', response.headers['Cache-Control'])
            self.assertIn(b'<script>', response.read())
        with self.assertRaises(HTTPError) as caught:
            self.request(view['url'], auth=False)
        self.assertEqual(403, caught.exception.code)
        view = self.post('/api/extensions/example.tools/view', {})
        with self.assertRaises(HTTPError) as caught:
            self.request(view['url'], auth=False, origin='https://attacker.invalid')
        self.assertEqual(403, caught.exception.code)

    def test_context_is_known_trusted_workspace_not_supplied_path_or_chat_contents(self):
        work = self.root / 'work'; work.mkdir()
        sid = self.app.create(str(work), True)['id']
        self.post('/api/extensions/example.tools/action', {'action': 'status', 'sessionId': sid})
        payload = json.loads(self.app.extensions.runner.call_args.kwargs['input'])
        self.assertEqual({'workspace': str(work)}, payload['context'])
        self.assertNotIn(self.app.token, json.dumps(payload))
        for data in ({'action': 'status', 'workspace': 'C:/foreign'}, {'action': 'status', 'sessionId': 'missing'}):
            with self.assertRaises(HTTPError):
                self.post('/api/extensions/example.tools/action', data)
        self.app.sessions[sid]['trusted'] = False
        self.post('/api/extensions/example.tools/action', {'action': 'status', 'sessionId': sid})
        payload = json.loads(self.app.extensions.runner.call_args.kwargs['input'])
        self.assertIsNone(payload['context']['workspace'])

    def test_directory_route_requires_native_selection_and_preserves_chat_trust(self):
        selected = self.root / 'chosen folder'; selected.mkdir()
        with patch.object(self.app, 'pick', return_value={'status': 'success', 'paths': [str(selected)]}) as picker:
            for data in ({'path': str(selected)}, {'initialDirectory': str(selected)}):
                with self.assertRaises(HTTPError):
                    self.post('/api/extensions/example.tools/directory', data)
            picker.assert_not_called()
            with self.assertRaises(HTTPError) as caught:
                self.post('/api/extensions/example.tools/directory', {}, auth=False)
            self.assertEqual(403, caught.exception.code)
            choice = self.post('/api/extensions/example.tools/directory', {})
            picker.assert_called_once_with('folder')
        self.post('/api/extensions/example.tools/action', {'action': 'install', 'directoryToken': choice['directoryToken'], 'payload': {'path': 'C:/forged'}})
        wire = json.loads(self.app.extensions.runner.call_args.kwargs['input'])
        self.assertIsNone(wire['context']['workspace'])
        self.assertEqual(str(selected), wire['context']['selectedDirectory'])
        self.assertEqual({}, self.app.sessions)
        with self.assertRaises(HTTPError):
            self.post('/api/extensions/example.tools/action', {'action': 'install', 'directoryToken': 'a' * 43})

    def test_slow_extension_does_not_hold_common_app_lock_or_block_bootstrap(self):
        started, release = threading.Event(), threading.Event()
        outcomes = []
        def runner(*args, **kwargs):
            started.set()
            release.wait(3)
            return completed('done')
        self.app.extensions.runner = runner
        def invoke():
            outcomes.append(self.post('/api/extensions/example.tools/action', {'action': 'status'}))
        worker = threading.Thread(target=invoke)
        worker.start()
        self.addCleanup(lambda: worker.join(4))
        self.addCleanup(release.set)
        self.assertTrue(started.wait(2))
        quit_preview = self.app.request_quit()
        self.assertTrue(quit_preview['confirmationRequired'])
        self.assertEqual(1, quit_preview['activeOperationCount'])
        begin = time.monotonic()
        with self.request('/api/bootstrap') as response:
            self.assertEqual('company-workspace', json.loads(response.read())['application'])
        self.assertLess(time.monotonic() - begin, .75)
        with self.assertRaises(HTTPError) as caught:
            self.post('/api/extensions/example.tools/action', {'action': 'status'})
        self.assertEqual(409, caught.exception.code)
        self.assertEqual('extension_busy', json.loads(caught.exception.read())['code'])
        release.set()
        worker.join(3)
        self.assertEqual([{'ok': True, 'result': 'done'}], outcomes)

    def test_interrupted_mutation_http_contract_requires_verification(self):
        self.app.extensions.runner.side_effect = subprocess.TimeoutExpired('worker', 120)
        with self.assertRaises(HTTPError) as caught:
            self.post('/api/extensions/example.tools/action', {'action': 'install'})
        result = json.loads(caught.exception.read())
        self.assertTrue(result['verificationRequired'])
        self.assertEqual('extension_interrupted', result['code'])
        self.app.extensions.runner.assert_called_once()


if __name__ == '__main__':
    unittest.main()
