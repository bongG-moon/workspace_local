"""Disposable localhost HTTP fixtures and shared GET protocol cases.

Used only by test_python_probe.py; no platform, database or external hosts.
"""
from __future__ import annotations

import gzip
import json
import socket
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HEALTH = {"status": "ok", "service": "P&T AI SQUARE", "storage": "mongodb", "version": "3.3"}


class FixtureServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), FixtureHandler)
        self.requests: list[dict] = []
        self.status = 200
        self.body = json.dumps(HEALTH).encode()
        self.headers = {"Content-Type": "application/json"}
        self.slow = False
        self.thread = threading.Thread(target=lambda: self.serve_forever(poll_interval=0.05), daemon=True)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=2)


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        self._reply()

    def do_POST(self):
        self._reply()

    def do_HEAD(self):
        self._reply()

    def _reply(self):
        server = self.server
        server.requests.append({"method": self.command, "path": self.path, "headers": dict(self.headers)})
        self.send_response(server.status)
        for key, value in server.headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(server.body)))
        self.end_headers()
        try:
            if server.slow:
                self.wfile.write(server.body[:1])
                self.wfile.flush()
                time.sleep(12)
                self.wfile.write(server.body[1:])
            else:
                self.wfile.write(server.body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass


class ProbeCases(unittest.TestCase):
    def run_probe(self, url):
        raise NotImplementedError("The Python runner supplies the probe invocation")

    def assert_single_get(self, server, path="/health"):
        self.assertEqual(len(server.requests), 1, server.requests)
        request = server.requests[0]
        self.assertEqual((request["method"], request["path"]), ("GET", path))
        headers = {key.lower(): value for key, value in request["headers"].items()}
        for credential in ("cookie", "authorization", "proxy-authorization", "x-csrf-token"):
            self.assertNotIn(credential, headers)

    def test_health_and_url_normalization(self):
        for suffix, expected in (("", "/health"), ("/", "/health"), ("/health", "/health"),
                                 ("/square", "/square/health"), ("/square/", "/square/health"),
                                 ("/square/health", "/square/health")):
            with self.subTest(suffix=suffix), FixtureServer() as server:
                report = self.run_probe(server.url + suffix)
                self.assertEqual(report["category"], "health_ok")
                self.assertTrue(report["platformConfirmed"])
                self.assertTrue(report["mongoPingConfirmed"])
                self.assertEqual(report["httpStatus"], 200)
                self.assert_single_get(server, expected)

    def test_dummy_mode_does_not_confirm_database(self):
        with FixtureServer() as server:
            server.body = json.dumps({**HEALTH, "storage": "dummy"}).encode()
            report = self.run_probe(server.url)
            self.assertEqual(report["category"], "dummy_mode")
            self.assertFalse(report["mongoPingConfirmed"])
            self.assert_single_get(server)

    def test_unexpected_content_is_not_a_success(self):
        bodies = [b"{}", b"not-json", b"[]", b"", json.dumps({**HEALTH, "service": "other"}).encode(),
                  json.dumps({**HEALTH, "status": "error"}).encode(),
                  json.dumps({key: value for key, value in HEALTH.items() if key != "storage"}).encode()]
        for body in bodies:
            with self.subTest(body=body[:80]), FixtureServer() as server:
                server.body = body
                report = self.run_probe(server.url)
                self.assertEqual(report["category"], "unexpected_response")
                self.assertFalse(report["platformConfirmed"])
                self.assertFalse(report["mongoPingConfirmed"])
                self.assert_single_get(server)

    def test_html_login_response_is_not_success(self):
        with FixtureServer() as server:
            server.headers["Content-Type"] = "text/html; charset=utf-8"
            server.body = b"<!doctype html><html><body>SSO login</body></html>"
            report = self.run_probe(server.url)
            self.assertEqual(report["category"], "html_response")
            self.assertFalse(report["platformConfirmed"])
            self.assert_single_get(server)

    def test_redirect_is_never_followed(self):
        for status in (301, 302, 307, 308):
            with self.subTest(status=status), FixtureServer() as server:
                server.status = status
                server.headers["Location"] = server.url + "/login?token=DO_NOT_DISPLAY"
                server.headers["Set-Cookie"] = "session=fixture-only"
                server.body = b""
                report = self.run_probe(server.url)
                self.assertEqual(report["category"], "redirect")
                self.assert_single_get(server)
                self.assertNotIn("DO_NOT_DISPLAY", json.dumps(report))

    def test_http_failures_are_distinguished(self):
        for status, category in ((401, "unauthorized"), (403, "unauthorized"), (407, "proxy_auth"),
                                 (404, "not_found"), (503, "server_error"), (429, "http_error")):
            with self.subTest(status=status), FixtureServer() as server:
                server.status = status
                server.body = b'{"error":"fixture"}'
                if status == 401:
                    server.headers["WWW-Authenticate"] = "Negotiate"
                report = self.run_probe(server.url)
                self.assertEqual(report["category"], category)
                self.assertEqual(report["httpStatus"], status)
                self.assertFalse(report["mongoPingConfirmed"])
                self.assert_single_get(server)

    def test_cookies_are_not_retained_between_probes(self):
        with FixtureServer() as server:
            server.headers["Set-Cookie"] = "session=fixture-only; Path=/"
            self.run_probe(server.url)
            self.run_probe(server.url)
            self.assertEqual(len(server.requests), 2)
            self.assertTrue(all(row["method"] == "GET" and row["path"] == "/health" for row in server.requests))
            self.assertTrue(all("cookie" not in {key.lower() for key in row["headers"]} for row in server.requests))

    def test_response_size_is_bounded(self):
        for compressed in (False, True):
            with self.subTest(compressed=compressed), FixtureServer() as server:
                raw = b" " * (128 * 1024)
                server.body = gzip.compress(raw) if compressed else raw
                if compressed:
                    server.headers["Content-Encoding"] = "gzip"
                report = self.run_probe(server.url)
                self.assertEqual(report["category"], "response_too_large")
                self.assertFalse(report["mongoPingConfirmed"])
                self.assertLess(len(json.dumps(report)), 16 * 1024)
                self.assert_single_get(server)

    def test_slow_body_times_out(self):
        with FixtureServer() as server:
            server.slow = True
            started = time.monotonic()
            report = self.run_probe(server.url)
            self.assertEqual(report["category"], "timeout")
            self.assertLess(time.monotonic() - started, 11)
            self.assert_single_get(server)

    def test_network_failure_has_no_false_success(self):
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            report = self.run_probe(f"http://127.0.0.1:{reserved.getsockname()[1]}")
        self.assertEqual(report["category"], "network_error")
        self.assertEqual(report["httpStatus"], 0)
        self.assertFalse(report["platformConfirmed"])

    def test_invalid_urls_never_reach_server(self):
        with FixtureServer() as server:
            authority = server.url.split("://", 1)[1]
            invalid = ["", "ftp://" + authority, "file:///C:/temp/example.html",
                       "http://employee:secret@" + authority, server.url + "?key=secret",
                       server.url + "#login", server.url + "/login", server.url + "/login/health",
                       server.url + "/%6cogin", server.url + "/square/../health",
                       server.url + "\\login", "http://local host", server.url + "/hea\nlth"]
            for url in invalid:
                with self.subTest(url=url):
                    report = self.run_probe(url)
                    self.assertEqual(report["category"], "invalid_url")
                    self.assertFalse(report["platformConfirmed"])
            self.assertEqual(server.requests, [])
