"""Opt-in real browser/API check across two persistent registration versions.

Only the native update launcher is synthetic; the shipped UI, local HTTP routes,
RegistrationManager, disk state and HMAC transport run unchanged. No production
address, user state, real employee or key is read or used.
"""
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import threading
import unittest

from local_app.registration import CONFIG_NAME, PATH, RegistrationManager, encoded
from local_app.server import LocalApp, Server

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which('node')
PLAYWRIGHT = os.environ.get('WORKSPACE_PLAYWRIGHT_PATH')
BROWSER = os.environ.get('WORKSPACE_BROWSER_PATH')
ENABLED = NODE and PLAYWRIGHT and BROWSER


class FixtureUpdates:
    def __init__(self, version, target):
        self.version, self.target = version, target
        self.installs = []

    def snapshot(self):
        return {'currentVersion': self.version, 'status': 'available', 'autoCheck': False,
                'canInstall': True, 'release': {'version': self.target,
                    'title': '업데이트 검증용 합성 버전', 'notes': '실제 설치 파일을 실행하지 않는 검증입니다.'}}

    def check(self, **_kwargs):
        return self.snapshot()

    def install(self, version):
        self.installs.append(version)
        return {**self.snapshot(), 'status': 'downloading'}

    def close(self):
        return True


@unittest.skipUnless(ENABLED, 'Opt-in WORKSPACE_PLAYWRIGHT_PATH and WORKSPACE_BROWSER_PATH are required')
class RegistrationLifecycleTests(unittest.TestCase):
    def setUp(self):
        (ROOT / 'build').mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix='rl-', dir=ROOT / 'build')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.state = self.root / 'state'
        self.output = ROOT / 'outputs/registration-lifecycle'
        self.output.mkdir(parents=True, exist_ok=True)
        self.key = secrets.token_hex(32)
        self.records = []
        records, key = self.records, self.key

        class RegistrationEndpoint(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                raw = self.rfile.read(int(self.headers['Content-Length']))
                value = json.loads(raw)
                canonical = '\n'.join(('AX-REGISTRATION-V1', 'POST', PATH,
                    self.headers['X-AX-Timestamp'], self.headers['X-AX-Nonce'],
                    hashlib.sha256(raw).hexdigest()))
                expected = hmac.new(key.encode(), canonical.encode('ascii'), hashlib.sha256).hexdigest()
                valid = self.path == PATH and hmac.compare_digest(expected, self.headers['X-AX-Signature'])
                records.append({'valid': valid, 'payload': value})
                response = json.dumps({'service': 'P&T AI SQUARE', 'protocol': 'ax-registration-v1',
                    'status': 'ok', 'authenticated': valid, 'persisted': True,
                    'dryRun': False, 'version': value['version']}).encode()
                self.send_response(201 if valid else 401)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(response)))
                self.end_headers()
                self.wfile.write(response)

        self.endpoint = ThreadingHTTPServer(('127.0.0.1', 0), RegistrationEndpoint)
        self.endpoint_thread = threading.Thread(target=self.endpoint.serve_forever, daemon=True)
        self.endpoint_thread.start()
        self.addCleanup(self.close_endpoint)

    def close_endpoint(self):
        self.endpoint.shutdown()
        self.endpoint.server_close()
        self.endpoint_thread.join(3)

    def application(self, version, target, *, bundle, configured=True, updated=False):
        bundle.mkdir(parents=True, exist_ok=True)
        if configured:
            (bundle / CONFIG_NAME).write_bytes(encoded({'schema': 1,
                'baseUrl': 'http://127.0.0.1:' + str(self.endpoint.server_port), 'key': self.key}))
        app = LocalApp(self.state, demo=True)
        app.registration.close()
        app.app_updates.close()
        app.registration = RegistrationManager(self.state, version, application_root=bundle)
        app.app_updates = FixtureUpdates(version, target)
        app.registration.startup_ready(updated=updated)
        server = Server(app)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return app, server, thread

    def browser(self, app, server, phase, *, prompt, install):
        options = {'origin': server.origin, 'token': app.token, 'output': str(self.output),
                   'phase': phase, 'expectPrompt': prompt, 'install': install}
        result = subprocess.run([NODE, str(ROOT / 'scripts/test-lab/verify-registration-upgrade-lifecycle.cjs')],
            input=json.dumps(options), text=True, encoding='utf-8', capture_output=True, timeout=40)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return json.loads(result.stdout)

    def stop(self, app, server, thread):
        worker = app.registration._worker
        if worker:
            worker.join(5)
            self.assertFalse(worker.is_alive())
        app.close()
        server.shutdown()
        server.server_close()
        thread.join(3)

    def test_first_update_prompts_once_then_restart_and_next_update_do_not(self):
        first = self.application('7.0.1', '7.0.2', bundle=self.root / 'first-bundle', updated=True)
        try:
            evidence_first = self.browser(*first[:2], 'first-update', prompt=True, install=True)
            self.assertEqual(first[0].app_updates.installs, ['7.0.2'])
        finally:
            self.stop(*first)
        self.assertEqual(len(self.records), 1)

        # A real new manager/process view loads the employee from disk, with no
        # bundled private file. Same-version normal startup cannot send again.
        restart = self.application('7.0.1', '7.0.2', bundle=self.root / 'restart-bundle', configured=False)
        try:
            evidence_restart = self.browser(*restart[:2], 'same-version-restart', prompt=False, install=False)
        finally:
            self.stop(*restart)
        self.assertEqual(len(self.records), 1)

        # Only confirmed new-version startup creates the next event. Saved
        # configuration and employee both survive without another input prompt.
        second = self.application('7.0.2', '7.0.3', bundle=self.root / 'second-bundle', configured=False, updated=True)
        try:
            evidence_second = self.browser(*second[:2], 'next-update', prompt=False, install=True)
            self.assertEqual(second[0].app_updates.installs, ['7.0.3'])
        finally:
            self.stop(*second)
        self.assertEqual(len(self.records), 2)
        self.assertTrue(all(row['valid'] for row in self.records))
        self.assertEqual([row['payload']['version'] for row in self.records], ['7.0.1', '7.0.2'])
        self.assertEqual([row['payload']['employeeId'] for row in self.records], ['TEST1234', 'TEST1234'])
        self.assertEqual(self.records[1]['payload']['previousVersion'], '7.0.1')
        self.assertTrue(all(row['payload']['event'] == 'app_update' for row in self.records))
        summary = {'scope': 'isolated shipped browser UI and real local API; synthetic native installer',
            'phases': [evidence_first, evidence_restart, evidence_second],
            'signedPosts': [{'version': row['payload']['version'], 'hmacValid': row['valid']} for row in self.records],
            'employeePromptCount': 1, 'sameVersionRepeatPosts': 0, 'passed': True}
        (self.output / 'lifecycle-verification.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    def test_public_build_without_private_configuration_never_prompts_or_posts(self):
        public = self.application('7.0.1', '7.0.2', bundle=self.root / 'public-bundle', configured=False, updated=True)
        try:
            evidence = self.browser(*public[:2], 'public-build', prompt=False, install=True)
            self.assertFalse(evidence['initial']['enabled'])
            self.assertEqual(public[0].app_updates.installs, ['7.0.2'])
        finally:
            self.stop(*public)
        self.assertEqual(self.records, [])


if __name__ == '__main__':
    unittest.main()
