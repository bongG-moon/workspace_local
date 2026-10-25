"""Reviewed native file imports, no helper EXE, no package execution."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import zipfile

from local_app.extension_install import ExtensionImporter, MAX_ARCHIVE, OWNER, PRODUCT, load_package
import local_app.extension_install as extension_install
from local_app.extensions import ExtensionHost, ExtensionError, fs_path
from local_app.server import LocalApp, Server


def package(path, *, version='1.0.0', origin='https://square.example', identifier='example.extension', mutate=None, worker=None, extra=None):
    sources = {'worker.py': worker or ('raise AssertionError("must never execute during install ' + version + '")\n').encode(),
               'ui.html': b'<!doctype html><p>Extension</p>'}
    sources.update(extra or {})
    digests = {name: hashlib.sha256(data).hexdigest() for name, data in sources.items()}
    build = hashlib.sha256(''.join(name + '\t' + digests[name] + '\n' for name in sorted(digests)).encode()).hexdigest()
    prefix = 'packages/' + build + '/'
    manifest = dict(schemaVersion=1, hostApi=1, id=identifier, name='Example extension', version=version,
                    entrypoint=prefix + 'worker.py', ui=prefix + 'ui.html', actions=['status'],
                    files={prefix + name: digest for name, digest in digests.items()})
    files = {prefix + name: data for name, data in sources.items()}
    files['extension.json'] = json.dumps(manifest).encode()
    files['README.txt'] = b'Use the app Settings import button.'
    files['ai-square.json'] = b'{"type":"plugin"}'
    if origin is not None:
        files['connection.json'] = json.dumps(dict(schemaVersion=1, baseUrl=origin)).encode()
    if mutate:
        mutate(files, manifest)
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return manifest


class ExtensionImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'profile' / 'local-ui'
        self.host = ExtensionHost(self.state, runner=Mock(side_effect=AssertionError('no execution')))
        self.addCleanup(self.host.close)
        self.importer = ExtensionImporter(self.host)
        self.archive = self.root / 'extension.zip'
        self.manifest = package(self.archive)
        self.picker = Mock(return_value={'status': 'success', 'paths': [str(self.archive)]})

    def prepare(self):
        return self.importer.prepare(self.picker)

    def install(self):
        review = self.prepare()
        return self.importer.confirm(review['token'], True)

    def test_preview_and_cancel_never_create_profile_or_run_code(self):
        with patch('subprocess.Popen', side_effect=AssertionError('no process')), patch('socket.create_connection', side_effect=AssertionError('no network')):
            review = self.prepare()
            self.assertEqual('https://square.example', review['serverUrl'])
            self.assertEqual('user', review['scope'])
            self.assertFalse(review['replacing'])
            self.assertFalse(self.state.parent.exists())
            self.importer.cancel(review['token'])
            with self.assertRaises(ExtensionError):
                self.importer.confirm(review['token'], True)
        self.picker.assert_called_once_with('files')
        self.host.runner.assert_not_called()

    def test_picker_cancel_and_invalid_count_are_safe(self):
        self.assertEqual({'cancelled': True}, self.importer.prepare(lambda _: {'status': 'cancel'}))
        for selection in [[], [str(self.archive)] * 2, ['relative.zip']]:
            with self.subTest(selection=selection), self.assertRaises(ExtensionError):
                self.importer.prepare(lambda _: {'status': 'success', 'paths': selection})
        self.assertFalse(self.state.parent.exists())

    def test_install_then_update_preserves_data_old_package_and_existing_server(self):
        result = self.install()
        self.assertTrue(result['installed'])
        directory, old = self.host._verified('example.extension')
        data = self.host.data_root / 'example.extension'
        (data / 'private-key.dpapi').write_text('unchanged encrypted data')
        old_worker = directory / old['entrypoint']
        package(self.archive, version='1.0.1', origin='https://other.example')
        review = self.prepare()
        self.assertTrue(review['replacing'])
        self.assertEqual('https://square.example', review['serverUrl'])
        self.assertIn('기존 서버 주소', ' '.join(review['warnings']))
        result = self.importer.confirm(review['token'], True)
        self.assertTrue(result['existingDataPreserved'])
        self.assertTrue(old_worker.is_file())
        self.assertEqual('1.0.1', self.host._verified('example.extension')[1]['version'])
        self.assertEqual('unchanged encrypted data', (data / 'private-key.dpapi').read_text())
        self.assertEqual('https://square.example', json.loads((data / 'connection.json').read_text())['baseUrl'])
        self.host.runner.assert_not_called()

    def test_confirmation_required_single_use_and_expiry(self):
        review = self.prepare()
        with self.assertRaises(ExtensionError):
            self.importer.confirm(review['token'], False)
        self.assertFalse(self.state.parent.exists())
        self.importer.confirm(review['token'], True)
        with self.assertRaises(ExtensionError):
            self.importer.confirm(review['token'], True)
        self.host.clock = Mock(return_value=1)
        review = self.prepare()
        self.host.clock.return_value = 500
        with self.assertRaises(ExtensionError):
            self.importer.confirm(review['token'], True)

    def test_archive_change_after_review_never_installs(self):
        review = self.prepare()
        package(self.archive, version='1.0.2')
        with self.assertRaisesRegex(ExtensionError, 'ZIP이 변경'):
            self.importer.confirm(review['token'], True)
        self.assertFalse(self.state.parent.exists())

    def test_target_changed_after_review_is_preserved(self):
        self.install()
        package(self.archive, version='1.0.1')
        review = self.prepare()
        directory, manifest = self.host._verified('example.extension')
        target = directory / manifest['entrypoint']
        target.write_text('user changed file')
        with self.assertRaises(ExtensionError):
            self.importer.confirm(review['token'], True)
        self.assertEqual('user changed file', target.read_text())

    def test_unowned_directory_and_extra_file_are_not_overwritten(self):
        destination = self.host.root / 'example.extension'
        destination.mkdir(parents=True)
        (destination / 'personal.txt').write_text('keep')
        with self.assertRaises(ExtensionError):
            self.prepare()
        self.assertEqual('keep', (destination / 'personal.txt').read_text())

    def test_busy_extension_and_shutdown_do_not_install(self):
        review = self.prepare()
        self.host._active.add('example.extension')
        with self.assertRaises(ExtensionError) as caught:
            self.importer.confirm(review['token'], True)
        self.assertEqual('extension_busy', caught.exception.code)
        self.host._active.clear()
        self.host.close()
        with self.assertRaises(ExtensionError):
            self.prepare()
        self.assertFalse(self.state.parent.exists())

    def test_path_traversal_links_duplicates_and_undeclared_files_rejected(self):
        mutations = [
            lambda files, _: files.update({'../escape.py': b'no'}),
            lambda files, _: files.update({'extra.py': b'no'}),
            lambda files, _: files.update({'README.TXT': b'no'}),
            lambda files, manifest: files.update({manifest['entrypoint']: b'tampered'}),
            lambda files, _: files.update({'connection.json': b'{"schemaVersion":1,"baseUrl":"https://a.example","baseUrl":"https://b.example"}'}),
        ]
        for mutation in mutations:
            package(self.archive, mutate=mutation)
            with self.subTest(mutation=mutation), self.assertRaises(ExtensionError):
                self.prepare()
        package(self.archive)
        with zipfile.ZipFile(self.archive, 'a') as archive:
            entry = zipfile.ZipInfo('link'); entry.external_attr = 0o120777 << 16
            archive.writestr(entry, 'somewhere')
        with self.assertRaises(ExtensionError):
            self.prepare()
        self.assertFalse(self.state.parent.exists())

    def test_disallowed_server_presets_are_rejected(self):
        for origin in ['file:///secret', 'http://0.0.0.0', 'http://[::]', 'https://user:secret@square.example', 'https://square.example/path', 'https://square.example?key=secret']:
            package(self.archive, origin=origin)
            with self.subTest(origin=origin), self.assertRaises(ExtensionError):
                self.prepare()

    def test_oversized_archive_is_rejected_before_read(self):
        with self.archive.open('wb') as output:
            output.truncate(MAX_ARCHIVE + 1)
        with self.assertRaises(ExtensionError):
            self.prepare()

    def test_native_marker_cannot_claim_an_unrelated_extension(self):
        self.install()
        directory = self.host.root / 'example.extension'
        marker = json.loads((directory / OWNER).read_text())
        marker['product'] = 'AX.OrganizationStore'
        (directory / OWNER).write_text(json.dumps(marker))
        # The native installer only owned pt-ai-square.store, never arbitrary IDs.
        with self.assertRaises(ExtensionError):
            self.prepare()

    def test_old_native_install_marker_migrates_and_preserves_history(self):
        package(self.archive, identifier='pt-ai-square.store')
        self.install()
        directory = self.host.root / 'pt-ai-square.store'
        old = json.loads((directory / 'extension.json').read_text())
        marker = json.loads((directory / OWNER).read_text())
        marker['product'] = 'AX.OrganizationStore'
        (directory / OWNER).write_text(json.dumps(marker))
        package(self.archive, identifier='pt-ai-square.store', version='1.0.1')
        self.install()
        self.assertEqual(PRODUCT, json.loads((directory / OWNER).read_text())['product'])
        self.assertTrue((directory / old['entrypoint']).is_file())
        self.assertEqual('1.0.1', self.host._verified('pt-ai-square.store')[1]['version'])

    def test_same_build_cannot_rewrite_registered_manifest(self):
        self.install()
        def change_name(files, manifest):
            manifest['name'] = 'Different name'
            files['extension.json'] = json.dumps(manifest).encode()
        package(self.archive, mutate=change_name)
        with self.assertRaisesRegex(ExtensionError, '같은 확장 빌드'):
            self.prepare()
        self.assertEqual('Example extension', self.host._verified('example.extension')[1]['name'])

    def test_failed_manifest_switch_keeps_previous_active_version_and_is_recoverable(self):
        self.install()
        package(self.archive, version='1.0.1')
        review = self.prepare()
        original = extension_install._atomic
        def fail_manifest(path, data):
            if path.name == 'extension.json':
                raise OSError('simulated interrupted manifest switch')
            return original(path, data)
        with patch.object(extension_install, '_atomic', side_effect=fail_manifest), self.assertRaises(OSError):
            self.importer.confirm(review['token'], True)
        self.assertEqual('1.0.0', self.host._verified('example.extension')[1]['version'])
        self.install()
        self.assertEqual('1.0.1', self.host._verified('example.extension')[1]['version'])

    def test_connection_changed_after_review_is_preserved(self):
        self.install()
        package(self.archive, version='1.0.1')
        review = self.prepare()
        path = self.host.data_root / 'example.extension' / 'connection.json'
        path.write_text(json.dumps(dict(schemaVersion=1, baseUrl='https://user.example')))
        with self.assertRaisesRegex(ExtensionError, '서버 설정이 변경'):
            self.importer.confirm(review['token'], True)
        self.assertEqual('https://user.example', json.loads(path.read_text())['baseUrl'])
        self.assertEqual('1.0.0', self.host._verified('example.extension')[1]['version'])

    def test_in_progress_install_refuses_duplicate_without_holding_host_lock(self):
        review = self.prepare()
        entered, release = threading.Event(), threading.Event()
        result = []
        original = self.importer._install
        def delayed(*args):
            entered.set()
            if not release.wait(5):
                raise AssertionError('install test timed out')
            return original(*args)
        def install():
            try:
                result.append(self.importer.confirm(review['token'], True))
            except BaseException as error:
                result.append(error)
        with patch.object(self.importer, '_install', side_effect=delayed):
            thread = threading.Thread(target=install)
            thread.start()
            try:
                self.assertTrue(entered.wait(2))
                self.assertTrue(self.host._lock.acquire(timeout=.2))
                self.host._lock.release()
                with self.assertRaises(ExtensionError):
                    self.importer.confirm(review['token'], True)
                with self.assertRaises(ExtensionError):
                    self.prepare()
            finally:
                release.set(); thread.join(3)
        self.assertEqual(1, len(result))
        self.assertIsInstance(result[0], dict)
        self.assertTrue(result[0]['installed'])

    def test_http_preset_warns_but_stays_key_only_without_a_network_request(self):
        package(self.archive, origin='http://localhost:5000')
        with patch('socket.create_connection', side_effect=AssertionError('no network')):
            review = self.prepare()
            self.assertEqual('http://localhost:5000', review['serverUrl'])
            self.assertIn('암호화되지 않습니다', ' '.join(review['warnings']))

    def test_stray_entries_do_not_consume_valid_extension_slots_or_hide_catalog(self):
        for index in range(15):
            package(self.archive, identifier='example.extension-' + str(index))
            self.install()
        (self.host.root / 'broken.extension').mkdir()
        (self.host.root / 'readme.txt').write_text('unrelated file preserved')
        package(self.archive, identifier='example.last')
        self.install()
        result = self.host.snapshot(refresh=True)
        self.assertEqual(16, len(result['extensions']))
        self.assertIn('example.last', [row['id'] for row in result['extensions']])
        self.assertTrue(result['warnings'])
        package(self.archive, identifier='example.over-limit')
        review = self.prepare()
        with self.assertRaisesRegex(ExtensionError, '확장 수'):
            self.importer.confirm(review['token'], True)
        self.assertEqual(16, len(self.host.snapshot(refresh=True)['extensions']))
        self.assertEqual('unrelated file preserved', (self.host.root / 'readme.txt').read_text())

    @unittest.skipUnless(os.name == 'nt', 'Windows extended-path regression')
    def test_unsupported_overlong_worker_root_is_rejected_before_any_write(self):
        deep = self.root.joinpath(*(('profile-' + str(index) + '-' + 'x' * 52) for index in range(5)))
        host = ExtensionHost(deep / 'local-ui', runner=Mock())
        self.addCleanup(host.close)
        importer = ExtensionImporter(host)
        with self.assertRaises(ExtensionError) as error:
            importer.prepare(self.picker)
        self.assertEqual('extension_path_too_long', error.exception.code)
        self.assertIn('260자', str(error.exception))
        self.assertFalse(fs_path(deep).exists())
        # Also protect legacy installed entries before starting a worker/writing
        # its data directory, even if their files can be read with extended paths.
        with patch.object(host, '_verified', return_value=(fs_path(host.root / 'example.extension'), self.manifest)):
            with self.assertRaises(ExtensionError) as error:
                host.action('example.extension', 'status', {})
        self.assertEqual('extension_path_too_long', error.exception.code)
        self.assertFalse(fs_path(deep).exists())
        host.runner.assert_not_called()

    @unittest.skipUnless(os.name == 'nt', 'Windows extended-path regression')
    def test_deep_profile_install_view_worker_and_update_keep_display_paths_normal(self):
        # Reproduce a conventional profile with a hash-prefixed nested package
        # exceeding MAX_PATH. Windows still limits the working directory itself.
        cleanup_root = self.root / ('profile-' + 'x' * 75)
        deep_root = cleanup_root / ('nested-' + 'x' * 45)
        self.assertTrue(deep_root.is_relative_to(self.root))
        def cleanup_deep():
            target = fs_path(cleanup_root)
            if target.exists():
                self.assertTrue(target.is_relative_to(fs_path(self.root)))
                shutil.rmtree(target)
        self.addCleanup(cleanup_deep)
        self.host = ExtensionHost(deep_root / 'local-ui')
        self.addCleanup(self.host.close)
        self.importer = ExtensionImporter(self.host)
        asset = 'vendor/' + 'nested-' * 12 + '/version.txt'
        worker = ("import json,sys\nfrom pathlib import Path\n"
                  "request=json.load(sys.stdin)\n"
                  "stamp=(Path(__file__).parent/" + repr(asset) + ").read_text()\n"
                  "target=Path(request['dataDirectory'])/'status.txt'\ntarget.write_text(stamp)\n"
                  "print(json.dumps({'ok':True,'result':{'version':stamp}}))\n").encode()
        previous = None
        for version in ['1.0.0', '1.0.1']:
            package(self.archive, version=version, worker=worker,
                    extra={asset: version.encode()})
            review = self.prepare()
            self.assertLess(len(review['destination']), 240)
            self.assertFalse(review['destination'].startswith('\\\\?\\'))
            self.importer.confirm(review['token'], True)
            directory, manifest = self.host._verified('example.extension')
            self.assertGreater(len(str(directory / manifest['entrypoint'])), 260)
            self.assertGreater(len(str(directory / Path(manifest['entrypoint']).parent / asset)), 360)
            view = self.host.view('example.extension')
            ticket = view['url'].split('ticket=', 1)[1]
            self.assertIn(b'<p>Extension</p>', self.host.consume_view('example.extension', ticket))
            self.assertEqual(version, self.host.action('example.extension', 'status', {})['result']['version'])
            self.assertEqual(version, fs_path(self.host.data_root / 'example.extension' / 'status.txt').read_text())
            if previous is not None:
                self.assertTrue(previous.is_file())
            previous = directory / manifest['entrypoint']
        self.assertEqual(1, len(self.host.snapshot(refresh=True)['extensions']))

    def test_interrupted_package_move_is_recovered_from_exact_reviewed_zip_only(self):
        self.install()
        package(self.archive, version='1.0.1')
        incoming = load_package(self.archive)
        directory = self.host.root / 'example.extension'
        for name, content in incoming['files'].items():
            path = directory / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(content)
        self.install()
        self.assertEqual('1.0.1', self.host._verified('example.extension')[1]['version'])


class ExtensionImportRoutesTests(unittest.TestCase):
    def test_authenticated_native_review_then_confirm_and_cancel(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / 'extension.zip'; package(archive)
            app = LocalApp(root / 'state', command=['never-start'], managed_workspace_root=root / 'managed')
            server = Server(app, 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            def post(operation, data, auth=True):
                headers = {'Origin': server.origin, 'Content-Type': 'application/json'}
                if auth: headers['Authorization'] = 'Bearer ' + app.token
                with urlopen(Request(server.origin + '/api/extensions/import/' + operation,
                                     data=json.dumps(data).encode(), headers=headers), timeout=5) as response:
                    return json.loads(response.read())
            try:
                self.assertIsNone(app.extensions._importer)
                with patch.object(app, 'pick', return_value={'status': 'success', 'paths': [str(archive)]}) as picker:
                    with self.assertRaises(HTTPError) as caught:
                        post('prepare', {}, auth=False)
                    self.assertEqual(403, caught.exception.code)
                    self.assertIsNone(app.extensions._importer)
                    with self.assertRaises(HTTPError):
                        post('prepare', {'path': str(archive)})
                    picker.assert_not_called()
                    review = post('prepare', {})
                    self.assertTrue(post('confirm', {'token': review['token'], 'confirmed': True})['installed'])
                    review = post('prepare', {})
                    self.assertTrue(post('cancel', {'token': review['token']})['cancelled'])
                    with self.assertRaises(HTTPError):
                        post('confirm', {'token': review['token'], 'confirmed': True})
            finally:
                app.close(); server.shutdown(); server.server_close(); thread.join(2)


if __name__ == '__main__':
    unittest.main()
