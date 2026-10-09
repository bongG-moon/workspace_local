"""Publisher tests use temporary repositories and an offline HTTPS registry."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from workspace_publisher.config import PublisherError, normalize, runtime_config
from workspace_publisher.core import Publisher
from workspace_publisher import git_sync
from workspace_publisher.publish import HTTPSClient, Response, connection, endpoint, publish, verify_files

VERSION = '0.23.0'
CONFIG = {'schema': 1, 'baseUrl': 'https://gitlab.corp:8443/company', 'projectId': '42',
          'remoteName': 'intranet', 'remoteUrl': 'git@gitlab.corp:team/workspace.git',
          'releaseTag': 'v0.23.0', 'title': '회사 업데이트', 'notes': '한국어 변경 내용',
          'allowedDownloadOrigins': [], 'tokenKind': 'deploy'}


def release_files(directory, source=None, *, branded=False):
    directory.mkdir(parents=True, exist_ok=True)
    source = source or runtime_config(CONFIG)
    entries = {'Company-Workspace.vbs': b'fixture', 'deploy/Start-CompanyWorkspace.ps1': b'fixture',
               'local_app/server.py': f'WORKSPACE_VERSION = "{VERSION}"\n'.encode(),
               'local_app/web/index.html': b'fixture', 'desktop/Workspace.Desktop.exe': b'MZfixture',
               'desktop/Microsoft.Web.WebView2.Core.dll': b'fixture',
               'desktop/Microsoft.Web.WebView2.WinForms.dll': b'fixture', 'desktop/WebView2Loader.dll': b'fixture',
               'workspace-update-source.json': json.dumps(source).encode()}
    vbs = directory / f'Company-Workspace-{VERSION}-vbs.zip'
    with zipfile.ZipFile(vbs, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr('Company-Workspace/' + name, content)
    exe = directory / f'Company-Workspace-{VERSION}-exe.zip'
    exe_name = f'Company-Workspace-{VERSION}.exe'
    executable = b'MZfixture'
    with zipfile.ZipFile(exe, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(exe_name, executable)
        archive.writestr(exe_name + '.sha256', hashlib.sha256(executable).hexdigest() + '  ' + exe_name + '\n')
        archive.writestr('README.txt', b'fixture')
    packages = [vbs, exe]
    if branded:
        ax_vbs = directory / f'AX-Workspace-{VERSION}-vbs.zip'
        ax_vbs.write_bytes(vbs.read_bytes())
        ax_exe = directory / f'AX-Workspace-{VERSION}-exe.zip'
        ax_name = f'AX-Workspace-{VERSION}.exe'
        with zipfile.ZipFile(ax_exe, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(ax_name, executable)
            archive.writestr(ax_name + '.sha256', hashlib.sha256(executable).hexdigest() + '  ' + ax_name + '\n')
            archive.writestr('README.txt', b'fixture')
        packages += [ax_vbs, ax_exe]
    (directory / 'SHA256SUMS.txt').write_text(''.join(hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + path.name + '\n'
                                                   for path in packages), encoding='utf-8')
    files = verify_files(directory, VERSION, source)
    return {'version': VERSION, 'commit': 'a' * 40, 'directory': str(directory), 'files': files, 'runtimeConfig': source}


class Registry:
    def __init__(self):
        self.files, self.calls = {}, []
        self.channel_mode = 'replace'
        self.fail_asset = False
        self.corrupt_asset = False

    def __call__(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if method == 'GET':
            self_body = self.files.get(url)
            if self_body is None:
                return Response(404, b'not found')
            if self.corrupt_asset and not url.endswith('latest.json'):
                return Response(200, b'corrupt')
            return Response(200, self_body)
        if self.fail_asset and not url.endswith('latest.json'):
            return Response(403, b'private fixture-token must not be displayed')
        if url.endswith('latest.json') and url in self.files:
            if self.channel_mode == 'reject':
                return Response(409, b'duplicate')
            if self.channel_mode == 'stale':
                return Response(201, b'created duplicate')
        self.files[url] = kwargs['data']
        return Response(201, b'created')


class ConfigTests(unittest.TestCase):
    def test_subpath_port_and_credential_free_runtime_contract(self):
        value = runtime_config(CONFIG)
        self.assertEqual('https://gitlab.corp:8443/company', value['baseUrl'])
        self.assertEqual(['https://gitlab.corp:8443'], value['allowedDownloadOrigins'])
        self.assertEqual('0.0.0', value['channelVersion'])
        self.assertNotIn('remoteUrl', value)
        self.assertEqual('https://gitlab.corp:8443/company/api/v4/projects/42/packages/generic/company-workspace/0.23.0/a.zip', endpoint(CONFIG, VERSION, 'a.zip'))

    def test_secrets_and_unsafe_urls_are_rejected(self):
        for extra in ({'token': 'never-save'}, {'password': 'never-save'}, {'allowedDownloadOrigins': None},
                      {'baseUrl': 'https://user:token@host'}, {'baseUrl': 'http://host'},
                      {'baseUrl': 'https://host/../private'}, {'baseUrl': 'https://host/%2e%2e/private'},
                      {'projectId': '../42'}, {'allowedDownloadOrigins': ['https://host/path']}):
            with self.subTest(extra=extra), self.assertRaises(PublisherError):
                normalize({**CONFIG, **extra})

    def test_runtime_config_uses_same_canonical_form_as_packaged_app(self):
        value = runtime_config({**CONFIG, 'baseUrl': 'https://GITLAB.corp:443/company/',
                                'allowedDownloadOrigins': ['https://Z.corp:443', 'https://a.corp/']})
        self.assertEqual('https://gitlab.corp/company', value['baseUrl'])
        self.assertEqual(['https://a.corp', 'https://gitlab.corp', 'https://z.corp'], value['allowedDownloadOrigins'])


class RedirectTests(unittest.TestCase):
    def call(self, target, *, method='GET', headers=None):
        client = HTTPSClient(['https://storage.corp'])
        class Body(io.BytesIO):
            status = 200
            headers = {}
        def opener(handler):
            class FakeOpener:
                def open(self, request, **kwargs):
                    redirected = handler.redirect_request(request, None, 302, 'Moved', {}, target)
                    if redirected is not None and any(key.upper().endswith('TOKEN') for key in redirected.headers):
                        raise AssertionError('Credential forwarded')
                    return Body(b'ok')
            return FakeOpener()
        with patch('workspace_publisher.publish.build_opener', side_effect=opener):
            return client(method, 'https://gitlab.corp/api/v4/packages/a.zip', headers=headers)

    def test_signed_anonymous_redirect_only_to_exact_allowed_origin(self):
        self.assertEqual(b'ok', self.call('https://storage.corp/file?signature=short-lived&expires=42').body)
        for url in ('https://storage.corp.attacker/file?signature=x', 'http://storage.corp/file',
                    'https://user:password@storage.corp/file?signature=x'):
            with self.subTest(url=url), self.assertRaises(PublisherError):
                self.call(url)

    def test_authenticated_request_never_follows_any_redirect(self):
        for method in ('PUT', 'GET'):
            with self.subTest(method=method), self.assertRaises(PublisherError):
                self.call('https://storage.corp/file?signature=x', method=method, headers={'DEPLOY-TOKEN': 'memory-only'})


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='publisher & ')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.result = release_files(self.directory)
        self.registry = Registry()
        self.events = []

    def publish(self, **kwargs):
        return publish(CONFIG, self.result, 'fixture-token', transport=self.registry,
                       emit=self.events.append, **kwargs)

    def test_assets_verified_anonymously_and_channel_written_last(self):
        result = self.publish()
        self.assertTrue(result['verified'])
        writes = [(url, values) for method, url, values in self.registry.calls if method == 'PUT']
        self.assertEqual(4, len(writes))
        self.assertTrue(writes[-1][0].endswith('/company-workspace-channel/0.0.0/latest.json'))
        self.assertEqual('fixture-token', writes[0][1]['headers']['DEPLOY-TOKEN'])
        for method, _, values in self.registry.calls:
            if method == 'GET':
                self.assertFalse(values.get('headers'))
        manifest = json.loads(writes[-1][1]['data'])
        self.assertEqual('stable', manifest['channel'])
        self.assertEqual(VERSION, manifest['version'])
        self.assertEqual('한국어 변경 내용', manifest['notes'])
        self.assertEqual(3, len(manifest['files']))
        self.assertNotIn('fixture-token', json.dumps(self.events))
        self.assertFalse(any(b'fixture-token' in path.read_bytes() for path in self.directory.iterdir()))

    def test_same_assets_and_channel_are_reused_without_put(self):
        self.publish()
        self.registry.calls.clear()
        self.assertTrue(self.publish()['verified'])
        self.assertFalse(any(method == 'PUT' for method, _, _ in self.registry.calls))

    def test_immutable_collision_and_failed_verification_never_touch_channel(self):
        first = endpoint(CONFIG, VERSION, self.result['files'][0]['name'])
        self.registry.files[first] = b'different version bytes'
        with self.assertRaisesRegex(PublisherError, '다른 파일'):
            self.publish()
        self.assertFalse(any(method == 'PUT' for method, _, _ in self.registry.calls))
        self.registry.files.clear()
        self.registry.corrupt_asset = True
        with self.assertRaisesRegex(PublisherError, '익명 다운로드'):
            self.publish()
        self.assertFalse(any(method == 'PUT' and url.endswith('latest.json') for method, url, _ in self.registry.calls))

    def test_authentication_failure_does_not_echo_token_or_server_body(self):
        self.registry.fail_asset = True
        with self.assertRaises(PublisherError) as raised:
            self.publish()
        self.assertNotIn('fixture-token', str(raised.exception))
        self.assertNotIn('private', str(raised.exception))

    def test_channel_duplicate_reject_and_stale_get_are_not_success(self):
        self.publish()
        old = self.registry.files[endpoint(CONFIG, VERSION, 'latest.json', channel=True)]
        for mode in ('reject', 'stale'):
            self.registry.channel_mode = mode
            self.registry.files[endpoint(CONFIG, VERSION, 'latest.json', channel=True)] = old
            with self.subTest(mode=mode), self.assertRaisesRegex(PublisherError, 'company-workspace-channel'):
                publish({**CONFIG, 'notes': '새 설명'}, self.result, 'fixture-token', transport=self.registry)
            self.assertEqual(old, (self.directory / 'previous-latest.json').read_bytes())

    def test_newer_channel_is_not_downgraded(self):
        self.publish()
        url = endpoint(CONFIG, VERSION, 'latest.json', channel=True)
        newer = json.loads(self.registry.files[url])
        newer['version'] = '0.24.0'
        for file in newer['files']:
            file['name'] = file['name'].replace(VERSION, '0.24.0')
        self.registry.files[url] = json.dumps(newer).encode()
        self.registry.calls.clear()
        with self.assertRaisesRegex(PublisherError, '더 새로운 버전'):
            self.publish()
        self.assertFalse(any(method == 'PUT' and url.endswith('latest.json') for method, url, _ in self.registry.calls))

    def test_connection_and_retry_reject_manifest_the_app_cannot_read(self):
        self.publish()
        url = endpoint(CONFIG, VERSION, 'latest.json', channel=True)
        original = json.loads(self.registry.files[url])
        for changes in ({'publishedAt': 'not-a-date'}, {'unexpected': True}):
            self.registry.files[url] = json.dumps({**original, **changes}).encode()
            self.registry.calls.clear()
            with self.subTest(changes=changes):
                with self.assertRaises(PublisherError):
                    connection(CONFIG, transport=self.registry)
                with self.assertRaises(PublisherError):
                    self.publish()
                self.assertFalse(any(method == 'PUT' for method, _, _ in self.registry.calls))

    def test_changed_payload_or_different_target_requires_rebuild(self):
        with self.assertRaises(PublisherError):
            publish({**CONFIG, 'projectId': '43'}, self.result, 'fixture-token', transport=self.registry)
        (self.directory / self.result['files'][0]['name']).write_bytes(b'changed')
        with self.assertRaises(PublisherError):
            self.publish()
        self.assertEqual([], self.registry.calls)

    def test_cancel_prevents_upload_and_job_token_header_is_explicit(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(PublisherError):
            self.publish(cancel=cancel)
        self.assertEqual([], self.registry.calls)
        self.publish(token_kind='job')
        self.assertIn('JOB-TOKEN', next(values['headers'] for method, _, values in self.registry.calls if method == 'PUT'))

    def test_check_has_no_publish_credentials_and_does_not_overstate_404(self):
        result = connection(CONFIG, transport=self.registry)
        self.assertFalse(result['published'])
        self.assertIn('허용되지', result['message'])
        self.assertEqual('GET', self.registry.calls[0][0])


class GitAndBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='publisher repo & ')
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.run_git('init', '-b', 'main')
        self.write('.gitignore', 'build/\n')
        self.write('local_app/server.py', f'WORKSPACE_VERSION = "{VERSION}"\n')
        sdk = b'fixture SDK package'
        self.write('deploy/WebView2.lock.json', json.dumps({'version': '1.0.fixture', 'sha256': hashlib.sha256(sdk).hexdigest()}))
        self.write('deploy/New-WorkspaceStandalone.ps1', '# fixture\n')
        self.write('deploy/New-WorkspaceRelease.ps1', '# fixture\n')
        self.write('scripts/test-lab/check-workspace-bundle.py', '# fixture\n')
        self.run_git('add', '.')
        self.run_git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'fixture source')
        self.run_git('tag', 'v0.23.0')
        self.write('build/desktop-sdk/1.0.fixture.nupkg', sdk)
        self.calls = []
        self.publisher = Publisher(self.repo, runner=self.runner)

    def write(self, relative, value):
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value if isinstance(value, bytes) else value.encode())

    def run_git(self, *args):
        result = subprocess.run(['git', '-C', str(self.repo), *args], capture_output=True,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            self.fail(result.stderr.decode(errors='replace'))
        return result.stdout.decode().strip()

    def runner(self, args, *, cwd, **kwargs):
        self.calls.append(args)
        if 'New-WorkspaceStandalone.ps1' in args[args.index('-File') + 1] if '-File' in args else False:
            output = Path(args[args.index('-OutputDirectory') + 1])
            output.mkdir()
            exe = output / f'AX-Workspace-{VERSION}.exe'
            exe.write_bytes(b'MZfixture')
            report = {'version': VERSION, 'embeddedPayloadVerified': True, 'pythonBundled': False,
                      'exe': str(exe), 'sha256': hashlib.sha256(exe.read_bytes()).hexdigest(), 'payloadFiles': 9}
            (cwd / f'build/workspace-standalone-{VERSION}-build.json').write_text(json.dumps(report))
        elif '-File' in args:
            output = Path(args[args.index('-OutputDirectory') + 1])
            injected = json.loads(Path(args[args.index('-UpdateConfig') + 1]).read_text())
            release_files(output, injected, branded=True)
            report = {'version': VERSION, 'pythonBundled': False, 'identicalSourceFiles': 9,
                      'vbsZip': str(output / f'AX-Workspace-{VERSION}-vbs.zip'),
                      'exeZip': str(output / f'AX-Workspace-{VERSION}-exe.zip')}
            (cwd / f'build/workspace-release-{VERSION}.json').write_text(json.dumps(report))
        return subprocess.CompletedProcess(args, 0)

    def build(self):
        with patch('local_app.windows_process.powershell_path', return_value='fixture-powershell.exe'):
            return self.publisher.build(CONFIG)

    def test_clean_archive_build_keeps_source_and_git_unchanged(self):
        before = self.run_git('rev-parse', 'HEAD')
        result = self.build()
        self.assertEqual(before, result['commit'])
        self.assertEqual('git', result['sourceKind'])
        self.assertEqual(before, result['sourceId'])
        self.assertTrue(self.publisher.inspect_source()['canSync'])
        self.assertEqual('', self.run_git('status', '--porcelain'))
        self.assertEqual(before, self.run_git('rev-parse', 'HEAD'))
        self.assertFalse((self.repo / 'workspace-update-source.json').exists())
        self.assertNotEqual(self.repo, Path(result['sourceRoot']))
        self.assertEqual(5, len(result['files']))
        self.assertEqual(3, len(self.calls))
        self.assertIn('-ConfigPython', self.calls[0])
        self.assertEqual(Path(result['directory']).parent / 'v',
                         Path(self.calls[0][self.calls[0].index('-VerificationDirectory') + 1]))
        self.assertIn('--update-config', self.calls[-1])
        self.assertTrue((Path(result['sourceRoot']) / 'build/desktop-sdk/1.0.fixture.nupkg').is_file())

    def test_dirty_source_and_bad_sdk_stop_before_build(self):
        self.write('unexpected.txt', 'dirty')
        with self.assertRaisesRegex(PublisherError, '변경'):
            self.build()
        (self.repo / 'unexpected.txt').unlink()
        self.write('build/desktop-sdk/1.0.fixture.nupkg', 'incorrect')
        with self.assertRaisesRegex(PublisherError, 'SDK'):
            self.build()
        self.assertEqual([], self.calls)

    def test_failed_build_writes_bounded_redacted_log_with_actionable_path(self):
        stage = self.publisher.work_root / ('build-' + 'f' * 32)
        source = stage / 'source'
        source.mkdir(parents=True)
        args = ['powershell', '-File', str(source / 'deploy/New-WorkspaceStandalone.ps1'),
                '-UpdateConfig', 'do-not-copy-arguments']
        failure = subprocess.CompletedProcess(args, 1, b'compiler output\n',
                    b'Embedded payload verification failed: 59\nDEPLOY-TOKEN: fixture-token\nhttps://storage.corp/file?signature=private\n' + b'x' * 100000)
        with patch.dict(os.environ, {'PUBLISHER_TEST_SECRET': 'fixture-token'}), patch('workspace_publisher.core.subprocess.run', return_value=failure):
            with self.assertRaisesRegex(PublisherError, '로컬 빌드 로그') as error:
                self.publisher._run(args, cwd=source)
        log = next((stage / 'logs').glob('*.log'))
        content = log.read_text(encoding='utf-8')
        self.assertIn(str(log), str(error.exception))
        self.assertIn('Embedded payload verification failed: 59', content)
        self.assertNotIn('fixture-token', content)
        self.assertNotIn('signature=private', content)
        self.assertNotIn('do-not-copy-arguments', content)
        self.assertLess(log.stat().st_size, 132000)

    def test_timeout_preserves_partial_output_in_local_build_log(self):
        source = self.publisher.work_root / ('build-' + 'e' * 32) / 'source'
        source.mkdir(parents=True)
        with patch('workspace_publisher.core.subprocess.run', side_effect=subprocess.TimeoutExpired('build', 5, output=b'partial compiler output')):
            with self.assertRaisesRegex(PublisherError, '시간이 초과'):
                self.publisher._run(['python', 'check.py'], cwd=source)
        content = next((source.parent / 'logs').glob('*.log')).read_text(encoding='utf-8')
        self.assertIn('TimeoutExpired', content)
        self.assertIn('partial compiler output', content)

    @unittest.skipUnless(os.name == 'nt', 'Windows PowerShell is required')
    def test_windows_powershell_build_child_restores_modules_without_changing_parent(self):
        from local_app.windows_process import powershell_path
        source = self.publisher.work_root / ('build-' + 'd' * 32) / 'source'
        source.mkdir(parents=True)
        script = source / 'check-modules.ps1'
        script.write_text("$ErrorActionPreference = 'Stop'\n"
                          "if (($env:PSModulePath -split ';') -contains (Join-Path $PSScriptRoot 'incompatible-parent-modules')) { throw 'parent module path leaked' }\n"
                          "[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'input.txt'), 'module probe')\n"
                          "$digest = (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot 'input.txt') -Algorithm SHA256).Hash\n"
                          "Compress-Archive -LiteralPath (Join-Path $PSScriptRoot 'input.txt') -DestinationPath (Join-Path $PSScriptRoot 'probe.zip')\n"
                          "Expand-Archive -LiteralPath (Join-Path $PSScriptRoot 'probe.zip') -DestinationPath (Join-Path $PSScriptRoot 'expanded')\n"
                          "if ((Get-FileHash -LiteralPath (Join-Path $PSScriptRoot 'expanded/input.txt') -Algorithm SHA256).Hash -ne $digest) { throw 'hash mismatch' }\n"
                          "[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'module-check.txt'), $digest)\n", encoding='utf-8')
        incompatible = source / 'incompatible-parent-modules'
        incompatible.mkdir()
        poisoned = os.environ.get('PSModulePath', '') + ';' + str(incompatible)
        with patch.dict(os.environ, {'PSModulePath': poisoned}):
            self.publisher._run([powershell_path(), '-NoProfile', '-NoLogo', '-ExecutionPolicy', 'Bypass',
                                 '-File', str(script)], cwd=source, timeout=30)
            self.assertEqual(poisoned, os.environ['PSModulePath'])
        self.assertEqual(hashlib.sha256(b'module probe').hexdigest(),
                         (source / 'module-check.txt').read_text().lower())
        with zipfile.ZipFile(source / 'probe.zip') as archive:
            self.assertEqual(b'module probe', archive.read('input.txt'))
        self.assertIn('결과: exit 0', next((source.parent / 'logs').glob('*.log')).read_text(encoding='utf-8'))

    def test_config_stays_ignored_and_rejects_token(self):
        self.publisher.save_config(CONFIG)
        self.assertEqual('', self.run_git('status', '--porcelain'))
        self.assertEqual(CONFIG['baseUrl'], self.publisher.load_config()['baseUrl'])
        with self.assertRaises(PublisherError):
            self.publisher.save_config({**CONFIG, 'token': 'do-not-save'})
        self.assertNotIn('do-not-save', self.publisher.config_path.read_text(encoding='utf-8'))

    def test_sync_preview_pins_destination_head_and_tag_without_mutating_remote(self):
        preview = self.publisher.preview_sync(CONFIG)
        self.assertEqual('v0.23.0', preview['tag'])
        self.assertEqual(preview['commit'], preview['tagObject'])
        self.assertEqual('', self.run_git('remote'))
        with self.assertRaises(PublisherError):
            self.publisher.sync(CONFIG, {**preview, 'commit': 'b' * 40})
        self.assertEqual('', self.run_git('remote'))

    def test_existing_remote_is_not_repointed_and_origin_is_never_used(self):
        self.run_git('remote', 'add', 'intranet', 'git@other.corp:team/other.git')
        with self.assertRaises(PublisherError):
            self.publisher.preview_sync(CONFIG)
        self.assertEqual('git@other.corp:team/other.git', self.run_git('remote', 'get-url', 'intranet'))
        with self.assertRaises(PublisherError):
            self.publisher.preview_sync({**CONFIG, 'remoteName': 'origin'})

    def test_real_git_push_only_selected_branch_and_annotated_tag_no_force(self):
        # Replace only the network boundary with an offline bare repository;
        # real Git still receives the production branch/tag refspecs.
        bare = self.repo / 'build/offline-remote.git'
        bare.mkdir(parents=True)
        self.run_git('init', '--bare', str(bare))
        self.run_git('remote', 'add', 'origin', 'https://github.com/example/original.git')
        self.run_git('tag', '-d', 'v0.23.0')
        self.run_git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'tag', '-a', 'v0.23.0', '-m', 'release')
        self.run_git('branch', 'unrelated-branch')
        self.run_git('tag', 'unrelated-tag')
        preview = self.publisher.preview_sync(CONFIG)
        self.assertNotEqual(preview['commit'], preview['tagObject'])
        real_git = git_sync.git
        def offline_git(repo, *args, **kwargs):
            if args[0] in {'push', 'ls-remote'}:
                args = tuple(str(bare) if arg == CONFIG['remoteUrl'] else arg for arg in args)
            return real_git(repo, *args, **kwargs)
        with patch.object(git_sync, 'git', side_effect=offline_git):
            result = self.publisher.sync(CONFIG, preview)
        self.assertTrue(result['verified'])
        refs = self.run_git('--git-dir=' + str(bare), 'show-ref')
        self.assertIn('refs/heads/main', refs)
        self.assertIn('refs/tags/v0.23.0', refs)
        self.assertNotIn('unrelated', refs)
        self.assertEqual('https://github.com/example/original.git', self.run_git('remote', 'get-url', 'origin'))

    def test_new_remote_url_rewrites_cannot_change_previewed_destination(self):
        for setting in ('insteadOf', 'pushInsteadOf'):
            key = 'url.ssh://other.corp/private.git.' + setting
            self.run_git('config', key, CONFIG['remoteUrl'])
            with self.subTest(setting=setting), self.assertRaisesRegex(PublisherError, '주소 치환'):
                self.publisher.preview_sync(CONFIG)
            self.assertEqual('', self.run_git('remote'))
            self.run_git('config', '--unset', key)

    def test_unrelated_git_url_rewrite_does_not_block_preview(self):
        self.run_git('config', 'url.ssh://elsewhere.corp/.insteadOf', 'https://elsewhere.corp/')
        self.assertEqual(CONFIG['remoteUrl'], self.publisher.preview_sync(CONFIG)['remoteUrl'])

    def test_detached_commit_can_build_but_cannot_push_unknown_branch(self):
        self.run_git('checkout', '--detach', 'HEAD')
        self.assertEqual('', self.publisher.inspect_source()['branch'])
        self.build()
        with self.assertRaisesRegex(PublisherError, '브랜치'):
            self.publisher.preview_sync(CONFIG)

    def test_core_publish_rejects_unowned_build_and_accepts_its_saved_result(self):
        with self.assertRaises(PublisherError):
            self.publisher.publish(CONFIG, {'directory': str(self.repo)}, 'fixture-token')
        result = self.build()
        self.publisher.transport = Registry()
        self.assertTrue(self.publisher.publish(CONFIG, result, 'fixture-token')['verified'])
        with self.assertRaises(PublisherError):
            self.publisher.publish(CONFIG, {**result, 'commit': 'f' * 40}, 'fixture-token')

    def test_last_build_recovers_original_bytes_after_source_head_changes(self):
        self.assertIsNone(self.publisher.load_last_build(CONFIG))
        result = self.build()
        self.write('new-source.txt', 'next source')
        self.run_git('add', '.')
        self.run_git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'next source')
        restarted = Publisher(self.repo, transport=Registry())
        recovered = restarted.load_last_build(CONFIG)
        self.assertEqual(result, recovered)
        self.assertNotEqual(self.run_git('rev-parse', 'HEAD'), recovered['commit'])
        self.assertTrue(restarted.publish(CONFIG, recovered, 'fixture-token')['verified'])

    def test_v0230_saved_build_without_source_metadata_remains_publishable(self):
        result = self.build()
        legacy = {key: value for key, value in result.items() if key not in {'sourceKind', 'sourceId'}}
        for path in (self.publisher.work_root / 'last-build.json', Path(result['directory']).parent / 'build-result.json'):
            path.write_text(json.dumps(legacy), encoding='utf-8')
        restarted = Publisher(self.repo, transport=Registry())
        self.assertEqual(legacy, restarted.load_last_build(CONFIG))
        self.assertTrue(restarted.publish(CONFIG, legacy, 'fixture-token')['verified'])

    def test_last_build_rejects_target_mismatch_missing_or_changed_artifacts(self):
        result = self.build()
        with self.assertRaises(PublisherError):
            self.publisher.load_last_build({**CONFIG, 'projectId': '99'})
        asset = Path(result['files'][0]['path'])
        original = asset.read_bytes()
        asset.write_bytes(b'changed')
        with self.assertRaises(PublisherError):
            self.publisher.load_last_build(CONFIG)
        asset.write_bytes(original)
        (Path(result['directory']).parent / 'build-result.json').unlink()
        with self.assertRaises(PublisherError):
            self.publisher.load_last_build(CONFIG)

    def test_last_build_rejects_unowned_pointer_without_reading_external_artifacts(self):
        result = self.build()
        result['directory'] = str(self.repo.parent)
        (self.publisher.work_root / 'last-build.json').write_text(json.dumps(result), encoding='utf-8')
        with patch('workspace_publisher.publish.verify_files') as verify:
            with self.assertRaises(PublisherError):
                self.publisher.load_last_build(CONFIG)
            verify.assert_not_called()
