"""Isolated fixtures for signed dry-run mode; no platform or database imports."""
import argparse
from contextlib import contextmanager
from contextlib import redirect_stderr
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).with_name("ai_square_connection_check.py")
spec = importlib.util.spec_from_file_location("registration_probe_under_test", SOURCE)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
KEY = "fixture-registration-key-1234567890-abcdef"
GOOD = {"service": "P&T AI SQUARE", "protocol": "ax-registration-v1", "status": "ok",
        "dryRun": True, "persisted": False, "authenticated": True}


@contextmanager
def fixture(body=None, status=200, location=None, content_type="application/json"):
    requests = []
    payload = json.dumps(GOOD if body is None else body).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            content = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            requests.append({"method": "POST", "path": self.path, "body": content,
                             "headers": dict(self.headers)})
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            if location is not None:
                self.send_header("Location", location)
            self.send_header("Set-Cookie", "fixture=should-not-be-used")
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            requests.append({"method": "GET", "path": self.path})
            self.send_response(500)
            self.end_headers()

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield "http://127.0.0.1:" + str(server.server_port), requests
    finally:
        server.shutdown()
        server.server_close()


class RegistrationProbeTests(unittest.TestCase):
    def test_url_preserves_http_port_and_proxy_prefix(self):
        self.assertEqual(probe.registration_url("http://example.invalid:5080/square"),
                         "http://example.invalid:5080/square/api/ax-workspace/registrations")
        self.assertEqual(probe.registration_url("http://example.invalid/square/health"),
                         "http://example.invalid/square/api/ax-workspace/registrations")

    def test_deterministic_hmac_uses_fixed_protocol_path(self):
        body = b'{"dryRun":true}'
        headers = probe.registration_headers(body, KEY, timestamp=1234567890, nonce="a" * 32)
        canonical = "AX-REGISTRATION-V1\nPOST\n/api/ax-workspace/registrations\n1234567890\n" + "a" * 32 + "\n" + hashlib.sha256(body).hexdigest()
        expected = hmac.new(KEY.encode(), canonical.encode(), hashlib.sha256).hexdigest()
        self.assertEqual(headers["X-AX-Signature"], expected)

    def test_signed_dry_run_is_the_only_request(self):
        with fixture() as (url, requests), patch.object(probe, "proxy_settings", return_value={}):
            result = probe.probe(url + "/prefix", post_test=True, key=KEY)
        self.assertEqual(result["category"], "post_test_ok")
        self.assertEqual(result["collectionReadiness"], "dry_run_verified")
        self.assertTrue(result["platformConfirmed"])
        self.assertFalse(result["mongoPingConfirmed"])
        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["path"], "/prefix/api/ax-workspace/registrations")
        payload = json.loads(request["body"])
        self.assertEqual(payload, {"schema": 1, "dryRun": True, "employeeId": "TEST",
                                  "version": "0.0.0", "event": "initial_install"})
        headers = {key.lower(): value for key, value in request["headers"].items()}
        expected = probe.registration_headers(request["body"], KEY,
                                              timestamp=headers["x-ax-timestamp"], nonce=headers["x-ax-nonce"])
        self.assertEqual(headers["x-ax-signature"], expected["X-AX-Signature"])
        for key in ("cookie", "authorization", "proxy-authorization"):
            self.assertNotIn(key, headers)
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn(KEY, serialized)
        self.assertNotIn(headers["x-ax-signature"], serialized)
        self.assertNotIn("employeeId", serialized)

    def test_success_response_requires_all_explicit_flags(self):
        cases = []
        for key in GOOD:
            cases.append({name: value for name, value in GOOD.items() if name != key})
        cases += [dict(GOOD, dryRun=False), dict(GOOD, persisted=True),
                  dict(GOOD, authenticated=False), dict(GOOD, dryRun=1),
                  dict(GOOD, persisted=0), dict(GOOD, authenticated=1),
                  dict(GOOD, service="other"), dict(GOOD, protocol="other")]
        for body in cases:
            with self.subTest(body=body), fixture(body=body) as (url, requests), patch.object(probe, "proxy_settings", return_value={}):
                result = probe.probe(url, post_test=True, key=KEY)
            self.assertEqual(result["category"], "unexpected_response")
            self.assertEqual(result["collectionReadiness"], "not_tested")
            self.assertEqual(len(requests), 1)

    def test_redirect_never_becomes_another_post_or_get(self):
        for status in (301, 302, 307, 308):
            with self.subTest(status=status), fixture(status=status, location="/login") as (url, requests), patch.object(probe, "proxy_settings", return_value={}):
                result = probe.probe(url, post_test=True, key=KEY)
            self.assertEqual(result["category"], "redirect")
            self.assertEqual(len(requests), 1)

    def test_auth_and_disabled_responses_are_specific(self):
        for status, category in ((401, "registration_auth_failed"), (403, "registration_auth_failed"),
                                 (503, "registration_unavailable")):
            with self.subTest(status=status), fixture(status=status) as (url, _), patch.object(probe, "proxy_settings", return_value={}):
                result = probe.probe(url, post_test=True, key=KEY)
            self.assertEqual(result["category"], category)

    def test_bad_url_or_key_sends_no_request(self):
        with patch.object(probe, "build_opener", side_effect=AssertionError("network must not be used")):
            for url in ("http://example.invalid/login", "http://example.invalid?token=secret"):
                self.assertEqual(probe.probe(url, post_test=True, key=KEY)["category"], "invalid_url")
            for key in (None, "short", "a" * 257, "a" * 32 + "\n", "a" * 32 + "\ud800"):
                result = probe.probe("http://example.invalid", post_test=True, key=key)
                self.assertEqual(result["category"], "invalid_registration_key")
                self.assertEqual(result["requestCount"], 0)

    def test_cli_only_exposes_dry_run_and_does_not_write_without_report(self):
        with fixture() as (url, requests), tempfile.TemporaryDirectory() as directory:
            environment = dict(os.environ, AX_REGISTRATION_KEY=KEY, no_proxy="127.0.0.1", NO_PROXY="127.0.0.1")
            completed = subprocess.run([sys.executable, "-X", "utf8", "-B", str(SOURCE), "--url", url, "--post-test", "--json"],
                                       env=environment, cwd=directory, capture_output=True, timeout=15)
            self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
            result = json.loads(completed.stdout.decode("utf-8"))
            self.assertEqual(result["category"], "post_test_ok")
            self.assertEqual(list(Path(directory).iterdir()), [])
            self.assertEqual(len(requests), 1)
            self.assertNotIn(KEY.encode(), completed.stdout + completed.stderr)
        help_text = subprocess.check_output([sys.executable, "-X", "utf8", "-B", str(SOURCE), "--help"]).decode(errors="replace")
        self.assertNotIn("--key", help_text)
        self.assertNotIn("--employee", help_text)
        self.assertNotIn("--live", help_text)

    def test_hidden_key_prompt_never_falls_back_to_plaintext(self):
        output = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), redirect_stderr(output), \
                patch.object(probe.getpass, "getpass", side_effect=probe.getpass.GetPassWarning("no hidden input")), \
                patch.object(probe, "build_opener", side_effect=AssertionError("must not send")):
            code = probe.main(["--url", "http://example.invalid", "--post-test"])
        self.assertEqual(code, 2)
        self.assertIn("AX_REGISTRATION_KEY", output.getvalue())


if __name__ == "__main__":
    unittest.main()
