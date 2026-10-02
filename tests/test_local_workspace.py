from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen
from urllib.parse import quote
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from local_app.bridge import ClaudeSession, cli_arguments, probe_cli, resolve_cli, runtime_context
from local_app.server import LocalApp, Server

FAKE = [sys.executable, "-X", "utf8", str(ROOT / "tests/fixtures/workspace_fake_cli.py")]


def eventually(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.03)
    raise AssertionError("Timed out waiting for deterministic local test child")


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="workspace-한글 ")
        self.events = []
        self.bridge = ClaudeSession(FAKE, {"permissionMode": "manual"}, Path(self.temp.name),
                                    lambda kind, data: self.events.append((kind, data)))

    def tearDown(self):
        self.bridge.close()
        self.temp.cleanup()

    def test_probe_and_preserve_settings(self):
        info = probe_cli(FAKE)
        self.assertEqual(info["version"], "test-cli 1.0")
        args = cli_arguments(FAKE, info)
        for forbidden in ("--bare", "--safe-mode", "--system-prompt", "--settings", "--strict-mcp-config",
                          "--dangerously-skip-permissions", "--disable-slash-commands", "--no-session-persistence",
                          "--permission-mode", "--setting-sources", "--model"):
            self.assertNotIn(forbidden, args)

    def test_exact_inherited_login_config_model_and_provider_environment(self):
        preserved = {"CLAUDE_CONFIG_DIR": str(Path(self.temp.name) / "기존 Claude"),
                     "ANTHROPIC_AUTH_TOKEN": "test-only-auth", "CLAUDE_CODE_OAUTH_TOKEN": "test-only-oauth",
                     "ANTHROPIC_BASE_URL": "https://company.invalid", "ANTHROPIC_MODEL": "existing-model",
                     "USERPROFILE": self.temp.name, "HOME": self.temp.name}
        with patch.dict(os.environ, preserved), patch("local_app.bridge.subprocess.Popen") as popen, \
                patch("local_app.bridge.threading.Thread"), patch.object(self.bridge, "_write"):
            self.bridge.start()
            env = popen.call_args.kwargs["env"]
            for key, value in preserved.items():
                self.assertEqual(env[key], value)
            self.assertEqual(popen.call_args.kwargs["cwd"], Path(self.temp.name))
            context = runtime_context(FAKE)
            self.assertEqual(context["configRoot"], preserved["CLAUDE_CONFIG_DIR"])
            self.assertNotIn("test-only-auth", json.dumps(context))
            self.assertNotIn("test-only-oauth", json.dumps(context))
            self.bridge.process = None

    def test_auth_error_uses_shared_login_and_retires_only_failed_child(self):
        self.bridge.busy = True
        self.bridge.session_id = str(uuid.uuid4())
        self.bridge.handle({"type": "result", "is_error": True,
                            "result": "Failed to authenticate: OAuth session expired and could not be refreshed"})
        self.assertTrue(self.bridge.closed)
        self.assertFalse(self.bridge.busy)
        self.assertFalse(any(k in {"assistant", "result"} for k, _ in self.events))
        error = next(d for k, d in self.events if k == "error")
        self.assertEqual(error["code"], "cli_authentication")
        self.assertIsNone(error["resumeSessionId"])
        self.assertIn("별도 계정 설정은 없습니다", error["message"])

    def test_auth_failure_preserves_last_confirmed_resume_id(self):
        sid = str(uuid.uuid4())
        self.bridge.resume_id = sid
        self.bridge.handle({"type": "assistant", "error": "authentication_failed",
                            "message": {"content": [{"type": "text", "text": "OAuth session expired"}]}})
        self.assertEqual(self.events[-1][1]["resumeSessionId"], sid)
        self.assertFalse(any(k == "assistant" for k, _ in self.events))

    def test_auth_words_in_successful_document_content_are_not_an_error(self):
        self.bridge.handle({"type": "result", "is_error": False, "result": "문서 예시: OAuth session expired"})
        self.assertFalse(self.bridge.closed)
        self.assertTrue(any(k == "result" for k, _ in self.events))

    def test_round_trip_questions_permission_unicode_and_followup(self):
        self.bridge.send('한글 경로 "C:/업무 & 자료/보고서.pptx" 읽어줘')
        eventually(lambda: "q1" in self.bridge.pending)
        self.assertIn("한글 경로", " ".join(d.get("text", "") for k,d in self.events))
        self.bridge.respond("q1", True, {"정리 방식?": "간단히"})
        eventually(lambda: "p1" in self.bridge.pending)
        self.bridge.respond("p1", False)
        eventually(lambda: any(k == "result" for k,d in self.events))
        self.assertFalse(self.bridge.busy)
        self.assertTrue(any(d.get("text") == "처리 완료: deny" for k,d in self.events))
        self.bridge.send("다시 정리해줘")
        eventually(lambda: "q2" in self.bridge.pending)
        self.bridge.respond("q2", True, {"정리 방식?": "자세히"})
        eventually(lambda: "p2" in self.bridge.pending)
        self.bridge.respond("p2", True)
        eventually(lambda: sum(k == "result" for k,d in self.events) == 2)
        self.assertTrue(any(d.get("text") == "처리 완료: allow" for k,d in self.events))

    def test_resume_exact_id_and_reject_flag_injection(self):
        sid = str(uuid.uuid4())
        self.assertIn("--resume=" + sid, cli_arguments(FAKE, {}, sid))
        with self.assertRaises(ValueError):
            cli_arguments(FAKE, {}, "--dangerously-skip-permissions")

    def test_question_validation_and_duplicate_replies(self):
        self.bridge.send("질문")
        eventually(lambda: "q1" in self.bridge.pending)
        with self.assertRaises(ValueError):
            self.bridge.respond("q1", True, {})
        self.assertIn("q1", self.bridge.pending)
        self.bridge.respond("q1", False)
        with self.assertRaises(ValueError):
            self.bridge.respond("q1", False)

    def test_busy_prevents_double_execution(self):
        self.bridge.send("첫 작업")
        with self.assertRaises(ValueError):
            self.bridge.send("두 번째 작업")

    def test_background_result_does_not_claim_completion(self):
        self.bridge.busy = True
        self.bridge.handle({"type": "system", "subtype": "task_started", "task_type": "local_agent", "task_id": "worker"})
        self.bridge.handle({"type": "result", "is_error": False})
        self.assertTrue(self.bridge.busy)
        self.assertFalse(any(k == "result" for k,d in self.events))
        self.bridge.handle({"type": "system", "subtype": "task_notification", "task_id": "worker"})
        self.assertTrue(self.bridge.busy)
        self.bridge.handle({"type": "result", "is_error": False})
        self.assertFalse(self.bridge.busy)

    def test_cancel_expired_request(self):
        self.bridge.pending["p"] = {"tool_name": "Bash", "input": {}}
        self.bridge.handle({"type": "control_cancel_request", "request_id": "p"})
        with self.assertRaises(ValueError):
            self.bridge.respond("p", True)

    def test_output_deduplication(self):
        self.bridge.handle({"type": "assistant", "message": {"content": [{"type": "text", "text": "한 번만"}]}})
        self.bridge.handle({"type": "result", "result": "한 번만", "is_error": False})
        self.assertEqual(sum(k == "assistant" for k,d in self.events), 1)

    def test_unknown_control_is_rejected_not_auto_approved(self):
        with patch.object(self.bridge, "_write") as write:
            self.bridge.handle({"type": "control_request", "request_id": "x", "request": {"subtype": "new_unsupported_type"}})
        self.assertEqual(write.call_args[0][0]["response"]["subtype"], "error")

    def test_interrupt_stops_owned_child(self):
        self.bridge.send("실행")
        eventually(lambda: "q1" in self.bridge.pending)
        self.bridge.interrupt()
        eventually(lambda: self.bridge.closed)
        eventually(lambda: self.bridge.process.poll() is not None)

    def test_stop_before_start_never_launches_child(self):
        self.bridge.interrupt()
        with patch("local_app.bridge.subprocess.Popen") as popen:
            with self.assertRaises(ValueError):
                self.bridge.start()
            popen.assert_not_called()
        self.assertTrue(self.bridge.closed)
        self.assertFalse(any(k == "error" for k,d in self.events))

    def test_actual_error_is_not_success(self):
        self.bridge.busy = True
        self.bridge.handle({"type": "result", "is_error": True, "result": "API unavailable"})
        self.assertFalse(self.bridge.busy)
        self.assertTrue(any(k == "error" for k,d in self.events))
        self.assertFalse(any(k == "result" for k,d in self.events))


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "한글 업무 & 폴더"
        self.workspace.mkdir()
        self.app = LocalApp(self.root / "state", command=FAKE, info={"permissionMode": "manual"})
        self.server = Server(self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.id = self.app.create(str(self.workspace), True)["id"]

    def tearDown(self):
        self.app.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path, data=None, token=True, headers=None):
        hdr = {"Content-Type": "application/json"}
        if token:
            hdr["Authorization"] = "Bearer " + self.app.token
        hdr.update(headers or {})
        request = Request(self.server.origin + path, data=None if data is None else json.dumps(data).encode(), headers=hdr)
        return urlopen(request, timeout=5)

    def test_loopback_only_and_unauthorized_api(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        with self.assertRaises(HTTPError) as caught:
            self.request("/api/bootstrap", token=False)
        self.assertEqual(caught.exception.code, 403)

    def test_cross_origin_and_dns_rebinding_blocked(self):
        for headers in ({"Origin":"https://evil.invalid"}, {"Host":"evil.invalid"}, {"Sec-Fetch-Site":"cross-site"}):
            with self.subTest(headers=headers), self.assertRaises(HTTPError) as caught:
                self.request("/api/create", {"workspace":str(self.workspace),"trusted":True}, headers=headers)
            self.assertEqual(caught.exception.code, 403)

    def test_rejected_post_burst_returns_403_without_reset_or_state_change(self):
        before = set(self.app.sessions)
        for i in range(30):
            with self.assertRaises(HTTPError) as caught:
                self.request('/api/create', {'workspace':str(self.workspace),'trusted':True},
                             headers={'Origin':'https://rejected.invalid'})
            self.assertEqual(403, caught.exception.code)
            caught.exception.close()
        self.assertEqual(before, set(self.app.sessions))

    def test_static_assets_no_secrets_and_csp(self):
        with self.request("/", token=False) as response:
            text = response.read().decode("utf-8")
            self.assertNotIn(self.app.token, text)
            self.assertIn("script-src 'self'", response.headers["Content-Security-Policy"])
            self.assertIn("no-store", response.headers["Cache-Control"])
        with self.assertRaises(HTTPError):
            self.request("/../server.py", token=False)

    def test_app_icons_load_without_auth_and_match_bundled_assets(self):
        from html.parser import HTMLParser
        from urllib.parse import urlsplit
        class Icons(HTMLParser):
            def __init__(self):
                super().__init__()
                self.links = []

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == 'link' and attrs.get('rel') in {'icon', 'apple-touch-icon'}:
                    self.links.append(attrs)

        parser = Icons()
        with self.request('/', token=False) as response:
            parser.feed(response.read().decode('utf-8'))
        self.assertEqual(4, len(parser.links))
        for item in parser.links:
            with self.subTest(url=item['href']), self.request(item['href'], token=False) as response:
                route = urlsplit(item['href']).path
                name = 'app-icon.ico' if route == '/favicon.ico' else route.removeprefix('/')
                self.assertEqual((ROOT / 'local_app/web' / name).read_bytes(), response.read())
                self.assertEqual('nosniff', response.headers['X-Content-Type-Options'])
                self.assertTrue(response.headers['Content-Type'].startswith('image/'))

    def test_manual_routes_are_fixed_public_static_documents(self):
        def assert_manual_policy(response):
            policy = {parts[0]: parts[1:] for directive in response.headers['Content-Security-Policy'].split(';')
                      if (parts := directive.split())}
            self.assertEqual(['data:'], policy['font-src'])
            self.assertEqual(["'unsafe-inline'"], policy['style-src'])
            for directive in ('default-src', 'script-src', 'connect-src', 'object-src', 'base-uri', 'frame-ancestors'):
                self.assertEqual(["'none'"], policy[directive], directive)

        canonical = (ROOT / 'docs/WORKSPACE_USER_GUIDE.html').read_bytes()
        docs = self.root / 'docs'
        docs.mkdir()
        (docs / 'WORKSPACE_USER_GUIDE.html').write_bytes(canonical)
        # A bundle containing only the canonical document must serve all links.
        with patch('local_app.server.ASSETS', self.root / 'local_app/web'):
            for route in ('guide', 'WORKSPACE_USER_GUIDE.html', 'Company-Agent-사용자-안내서.html'):
                with self.request('/manual/' + quote(route), token=False) as response:
                    self.assertEqual(canonical, response.read())
                    self.assertIn('text/html', response.headers['Content-Type'])
                    assert_manual_policy(response)
            for route in ('handbook', 'onboarding', 'usage', 'commands', 'Company-Agent-Guide.html',
                          'Company-Agent-Handbook.html', 'Company-Agent-Onboarding.html',
                          'Claude-Code-필수-사용법.html', 'First-Work.html', 'first-work.html'):
                with self.subTest(route=route), self.request('/manual/' + quote(route), token=False) as response:
                    self.assertEqual(canonical, response.read())
                    assert_manual_policy(response)
        body = canonical.decode('utf-8')
        self.assertIn('<html lang="ko">', body)
        self.assertIn("기존 Claude Code", body)
        self.assertNotIn("<script", body.lower())
        self.assertNotIn('<script', body.lower())
        self.assertNotIn(self.app.token, body)
        for route in ('../server.py','COMPANY_AGENT_HANDBOOK.md','../../.claude.json','%2e%2e%2fserver.py',
                      '%252e%252e%252fserver.py', 'guide/../server.py', 'unknown.html', 'cua','Company-Agent-Cua-Pilot.html'):
            with self.assertRaises(HTTPError) as caught:
                self.request('/manual/'+route,token=False)
            self.assertEqual(404,caught.exception.code)

    def test_manual_csp_does_not_change_app_api_or_preview_policy(self):
        with self.request('/', token=False) as response:
            policy = response.headers['Content-Security-Policy']
            self.assertIn("script-src 'self'", policy)
            self.assertIn("connect-src 'self'", policy)
            self.assertNotIn('font-src data:', policy)
        source = self.workspace / 'preview.md'
        source.write_text('단순 미리보기', encoding='utf-8')
        for route in ('/api/bootstrap', '/api/preview?id=' + self.id + '&path=' + quote(str(source))):
            with self.subTest(route=route), self.request(route) as response:
                self.assertEqual(policy, response.headers['Content-Security-Policy'])

    def test_legacy_manual_redirects_are_fixed_local_section_targets(self):
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        opener = build_opener(NoRedirect)
        aliases = {
            'handbook': '#handbook', 'onboarding': '#onboarding', 'usage': '#usage', 'commands': '#commands',
            'Company-Agent-Guide.html': '', 'Company-Agent-Handbook.html': '#handbook',
            'Company-Agent-Onboarding.html': '#onboarding', 'Claude-Code-필수-사용법.html': '#commands',
            'First-Work.html': '#onboarding', 'first-work.html': '#onboarding',
        }
        for route, anchor in aliases.items():
            with self.subTest(route=route), self.assertRaises(HTTPError) as caught:
                opener.open(self.server.origin + '/manual/' + quote(route) + '?next=https://invalid.test/', timeout=5)
            self.assertEqual(302, caught.exception.code)
            self.assertEqual('/manual/guide' + anchor, caught.exception.headers['Location'])
            self.assertEqual('no-store', caught.exception.headers['Cache-Control'])
            caught.exception.close()
        with self.assertRaises(HTTPError) as caught:
            self.request('/manual/handbook', token=False, headers={'Origin': 'https://invalid.test'})
        self.assertEqual(403, caught.exception.code)

    def test_removed_harness_routes_cannot_execute(self):
        body = {'id': self.id, 'action': 'computer-check'}
        with self.assertRaises(HTTPError) as caught:
            self.request('/api/companion', body, token=False)
        self.assertEqual(403, caught.exception.code)
        with patch('local_app.server.ClaudeSession') as cli, patch('local_app.harness_client.HarnessClient.call') as harness:
            for path, data in (('/api/companion', body), ('/api/companion?id='+self.id, None), ('/api/course', None)):
                with self.subTest(path=path), self.assertRaises(HTTPError) as caught:
                    self.request(path, data)
                self.assertEqual(404, caught.exception.code)
                caught.exception.close()
            cli.assert_not_called()
            harness.assert_not_called()
        self.assertFalse(hasattr(self.app, 'companion'))

    def test_bootstrap_identifies_shared_cli_without_claiming_login_success(self):
        with self.request("/api/bootstrap") as response:
            value = json.load(response)
        self.assertEqual(value["workspaceVersion"], "0.23.5")
        self.assertEqual(value["appRoot"], str(ROOT))
        self.assertEqual(value["runtime"]["authentication"], "shared-with-cli")
        self.assertNotIn("loggedIn", value["runtime"])
        self.assertNotIn("token", value["runtime"])

    def test_workspace_trust_required(self):
        with self.assertRaises(ValueError):
            self.app.create(str(self.workspace), False)

    def test_path_scope_no_traversal(self):
        outside = self.root / "secret.txt"
        outside.write_text("do not disclose", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.app.allowed_file(self.id, str(self.workspace / "../secret.txt"))

    def test_symlink_escape(self):
        outside = self.root / "private.md"
        outside.write_text("private", encoding="utf-8")
        link = self.workspace / "link.md"
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("symlink permission unavailable")
        with self.assertRaises(ValueError):
            self.app.allowed_file(self.id, link)

    def test_executable_not_opened(self):
        path = self.workspace / "bad.cmd"
        path.write_text("should not run", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.app.allowed_file(self.id, path)

    def test_original_attachment_is_not_copied_or_modified(self):
        path = self.root / "자료.pptx"
        path.write_bytes(b"<DOCUMENT SAFER test fixture>")
        self.app.send(self.id, "읽어줘", [str(path)])
        eventually(lambda: self.app.get(self.id)["requests"])
        self.assertEqual(path.read_bytes(), b"<DOCUMENT SAFER test fixture>")
        self.assertEqual(list(self.workspace.iterdir()), [])
        self.assertEqual(self.app.allowed_file(self.id, path), path)

    def test_hang_is_status_not_invented_completion(self):
        self.app.send(self.id, "질문", [])
        eventually(lambda: self.app.get(self.id)["requests"])
        self.assertEqual(self.app.get(self.id)["state"], "question")
        self.assertNotEqual(self.app.get(self.id)["state"], "done")

    def test_history_requires_new_trust_and_does_not_store_permission_payloads(self):
        self.app.emit(self.id, "assistant", {"text": "안녕"})
        self.app.emit(self.id, "request", {"id":"r", "tool":"Bash", "input":{"command":"sensitive"}})
        self.app.save()
        saved=(self.root / 'state/history-sessions' / (self.id+'.json')).read_text(encoding="utf-8")
        self.assertNotIn("sensitive", saved)
        restored=LocalApp(self.root / "state",command=FAKE,info={})
        self.assertFalse(restored.get(self.id)["trusted"])
        with self.assertRaises(ValueError):
            restored.send(self.id, "다시", [])

    def test_utf8_preview_not_lossy(self):
        path=self.workspace / "한글.md"
        path.write_text("# 실적 요약\n안녕하세요",encoding="utf-8")
        from urllib.parse import urlencode
        with self.request("/api/preview?" + urlencode({"id":self.id,"path":str(path)})) as response:
            self.assertIn("안녕하세요", json.load(response)["text"])
        path.write_bytes("한글".encode("cp949"))
        with self.request("/api/preview?" + urlencode({"id":self.id,"path":str(path)})) as response:
            self.assertEqual('한글', json.load(response)['text'])
        self.assertEqual(path.read_bytes(), "한글".encode("cp949"))
        path.write_bytes('UTF16 한글'.encode('utf-16'))
        with self.request('/api/preview?' + urlencode({'id': self.id, 'path': str(path)})) as response:
            self.assertEqual('UTF16 한글', json.load(response)['text'])
        path.write_bytes(b'\xff\x00\xfe')
        with self.request('/api/preview?' + urlencode({'id': self.id, 'path': str(path)})) as response:
            data = json.load(response)
            self.assertEqual('external', data['kind'])
            self.assertNotIn('text', data)

    def test_all_html_previews_are_static_with_original_source_available(self):
        from urllib.parse import urlencode
        path = self.workspace/'보고서.html'
        text = '<body data-style="minimalism"><main class="report-main">확인용<script>alert(1)</script></main></body>'
        path.write_text(text, encoding='utf-8')
        route = '/api/preview?' + urlencode({'id':self.id, 'path':str(path)})
        with self.request(route) as response:
            data = json.load(response)
        self.assertEqual('html', data['kind'])
        self.assertNotIn('<script', data['html'])
        self.assertEqual((text, 'html', False), (data['text'], data['language'], data['truncated']))
        self.assertEqual(text, path.read_text(encoding='utf-8'))
        path.write_text('<html>첨부 원본</html>', encoding='utf-8')
        with self.request(route) as response:
            data = json.load(response)
            self.assertEqual('html', data['kind'])
            self.assertEqual('<html>첨부 원본</html>', data['text'])
            self.assertIn('첨부 원본', data['html'])
        with patch('local_app.external_apps.open_document', return_value={'ok': True, 'requested': True}) as opened:
            with self.request('/api/open', {'id': self.id, 'path': str(path)}) as response:
                self.assertTrue(json.load(response)['requested'])
            opened.assert_called_once_with(path, 'open')

    def test_full_http_send_question_permission_result(self):
        with self.request("/api/send", {"id":self.id,"text":"업무 요청","attachments":[]}) as response:
            self.assertTrue(json.load(response)["ok"])
        eventually(lambda: "q1" in self.app.get(self.id)["requests"])
        self.request("/api/respond", {"id":self.id,"requestId":"q1","allow":True,"answers":{"정리 방식?":"간단히"}}).close()
        eventually(lambda: "p1" in self.app.get(self.id)["requests"])
        self.request("/api/respond", {"id":self.id,"requestId":"p1","allow":True}).close()
        eventually(lambda: self.app.get(self.id)["state"] == "done")
        self.assertTrue(self.app.get(self.id)["sessionId"])

    def test_invalid_approval_type(self):
        with self.assertRaises(ValueError):
            self.app.respond(self.id,"x","true",None)

    def test_late_approval_close_does_not_undo_completed_result(self):
        self.app.emit(self.id, 'request', {'id':'p','tool':'Bash','input':{}})
        self.app.emit(self.id, 'result', {'sessionId':'test','usage':{}})
        self.app.emit(self.id, 'request_closed', {'id':'p'})
        self.assertEqual(self.app.get(self.id)['state'],'done')
        self.assertEqual(self.app.get(self.id)['events'][-1]['data']['state'],'done')

    def test_removed_harness_mutations_stay_unavailable_in_every_task_state(self):
        for state in ('idle', 'running', 'approval'):
            self.app.get(self.id)['state'] = state
            for action in ('apply', 'learning', 'rollback', 'share'):
                with self.subTest(state=state, action=action), self.assertRaises(HTTPError) as caught:
                    self.request('/api/companion', {'id': self.id, 'action': action, 'confirmed': True})
                self.assertEqual(404, caught.exception.code)
                caught.exception.close()

    def test_only_explicit_demo_mode_can_simulate(self):
        self.assertFalse(self.app.demo)
        self.assertNotIn("demo", self.app.get(self.id)["title"])

    def test_terminal_login_refresh_reused_on_next_send_without_app_restart(self):
        config = self.root / "existing-cli-config"
        config.mkdir()
        shared = config / "fake-auth.txt"
        shared.write_text("expired", encoding="utf-8")
        with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(config), "WORKSPACE_FAKE_AUTH": "1"}):
            self.app.send(self.id, "AUTH_PARITY_CHECK", [])
            eventually(lambda: self.app.get(self.id)["state"] == "error")
            failed = self.app.get(self.id)["bridge"]
            self.assertTrue(failed.closed)
            self.assertIsNone(self.app.get(self.id)["sessionId"])
            self.assertEqual(shared.read_text(encoding="utf-8"), "expired")
            # Simulate the existing terminal updating its own shared login.
            shared.write_text("valid", encoding="utf-8")
            self.app.send(self.id, "AUTH_PARITY_CHECK", [])
            eventually(lambda: self.app.get(self.id)["state"] == "done")
            self.assertIsNot(self.app.get(self.id)["bridge"], failed)
            self.assertEqual(self.app.get(self.id)["messages"][-1]["text"], "기존 CLI 인증 사용")
            self.assertFalse((self.root / "state/.credentials.json").exists())
            self.assertEqual(shared.read_text(encoding="utf-8"), "valid")


class ResolutionTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows shell-context integration")
    def test_launcher_uses_existing_terminal_alias_and_configuration(self):
        with tempfile.TemporaryDirectory(prefix="workspace-alias-한글 & ") as raw:
            state = Path(raw)
            runtime = state / "runtime.json"
            fixture = ROOT / "tests/fixtures/workspace_fake_cli.ps1"
            quoted = lambda value: "'" + str(value).replace("'", "''") + "'"
            script = "Set-Alias claude " + quoted(fixture) + "\n& " + quoted(ROOT / "deploy/Start-CompanyWorkspace.ps1")
            script += " -NoBrowser -StateRoot " + quoted(state)
            details = None
            with patch.dict(os.environ, {"WORKSPACE_FAKE_PYTHON": sys.executable,
                    "COMPANY_AGENT_CLAUDE": "", "CLAUDE_CONFIG_DIR": str(state / "shared-config")}):
                try:
                    result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", script],
                                            cwd=ROOT, capture_output=True, timeout=50)
                    self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
                    details = json.loads(runtime.read_text(encoding="utf-8"))
                    from urllib.parse import urlsplit, parse_qs
                    parsed = urlsplit(details["url"])
                    headers = {"Authorization": "Bearer " + parse_qs(parsed.fragment)["token"][0]}
                    with urlopen(Request(f"http://127.0.0.1:{parsed.port}/api/bootstrap", headers=headers), timeout=5) as response:
                        bootstrap = json.load(response)
                    self.assertIsNone(bootstrap["error"])
                    self.assertEqual(bootstrap["runtime"]["entry"], str(fixture.resolve()))
                    self.assertEqual(bootstrap["runtime"]["configRoot"], str(state / "shared-config"))
                    self.assertFalse((state / "shared-config").exists())
                finally:
                    if details:
                        headers["Content-Type"] = "application/json"
                        with urlopen(Request(f"http://127.0.0.1:{parsed.port}/api/quit", data=b"{}", headers=headers), timeout=5):
                            pass
                        eventually(lambda: not runtime.exists())

    @unittest.skipUnless(os.name == "nt", "Windows launcher version guard")
    def test_different_running_build_is_not_reused_or_killed(self):
        with tempfile.TemporaryDirectory(prefix="workspace-version-") as raw:
            state = Path(raw)
            app = LocalApp(state, demo=True)
            server = Server(app)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            runtime = state / "demo/runtime.json"
            runtime.parent.mkdir()
            runtime.write_text(json.dumps({"url": server.origin + "/#token=" + app.token, "pid": os.getpid()}), encoding="utf-8")
            try:
                with patch("local_app.server.WORKSPACE_VERSION", "old-build"):
                    result = subprocess.run(["powershell.exe", "-NoProfile", "-File",
                        str(ROOT / "deploy/Start-CompanyWorkspace.ps1"), "-Demo", "-NoBrowser", "-StateRoot", str(state)],
                        capture_output=True, timeout=15)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(b"WS-39", result.stderr)
                self.assertEqual(json.loads(runtime.read_text(encoding="utf-8"))["pid"], os.getpid())
                self.assertTrue(thread.is_alive())
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

    @unittest.skipUnless(os.name == "nt", "Windows launcher integration")
    def test_hidden_launcher_and_reopen_reuse_with_spaced_state_path(self):
        with tempfile.TemporaryDirectory(prefix="workspace-launch-한글 & ") as raw:
            state = Path(raw)
            runtime = state / "demo/runtime.json"
            args = ["powershell.exe", "-NoProfile", "-File", str(ROOT / "deploy/Start-CompanyWorkspace.ps1"),
                    "-Demo", "-NoBrowser", "-StateRoot", str(state)]
            details = None
            try:
                first = subprocess.run(args, cwd=ROOT, capture_output=True, timeout=50)
                self.assertEqual(first.returncode, 0, first.stderr.decode(errors="replace"))
                details = json.loads(runtime.read_text(encoding="utf-8"))
                second = subprocess.run(args, cwd=ROOT, capture_output=True, timeout=20)
                self.assertEqual(second.returncode, 0, second.stderr.decode(errors="replace"))
                self.assertEqual(json.loads(runtime.read_text(encoding="utf-8"))["pid"], details["pid"])
            finally:
                if details:
                    from urllib.parse import urlsplit, parse_qs
                    parsed=urlsplit(details["url"])
                    token=parse_qs(parsed.fragment)["token"][0]
                    request=Request(f"http://127.0.0.1:{parsed.port}/api/quit", data=b"{}", headers={
                        "Authorization":"Bearer "+token, "Content-Type":"application/json"})
                    with urlopen(request, timeout=5):
                        pass
                    eventually(lambda: not runtime.exists())

    @unittest.skipUnless(os.name == "nt", "Windows terminal shim")
    def test_selected_wrapper_is_preserved_not_replaced_with_sibling_binary(self):
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)/"claude.cmd"
            path.write_text("echo company-wrapper",encoding="utf-8")
            with patch("local_app.bridge.subprocess.Popen") as popen:
                args = resolve_cli(str(path))
                self.assertEqual(args[args.index("-Entry") + 1], str(path.resolve()))
                popen.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows terminal shim")
    def test_npm_native_layout_and_spaces(self):
        with tempfile.TemporaryDirectory(prefix="cli path ") as raw:
            path=Path(raw)/"claude.cmd";path.touch()
            exe=path.parent/"node_modules/@anthropic-ai/claude-code/bin/claude.exe"
            exe.parent.mkdir(parents=True);exe.touch()
            args = resolve_cli(str(path))
            self.assertEqual(args[args.index("-Entry") + 1], str(path.resolve()))
            self.assertNotIn(str(exe.resolve()), args)

    def test_profile_selected_command_takes_precedence_over_path(self):
        with tempfile.TemporaryDirectory() as raw:
            selected = Path(raw) / "terminal-claude.exe"
            selected.touch()
            with patch.dict(os.environ, {"COMPANY_WORKSPACE_CLAUDE_ENTRY": str(selected),
                                        "COMPANY_WORKSPACE_CLAUDE_PROFILE": "0", "COMPANY_AGENT_CLAUDE": ""}), \
                    patch("local_app.bridge.shutil.which") as which:
                self.assertEqual(resolve_cli(), [str(selected.resolve())])
                which.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows profile functions")
    def test_profile_function_is_reloaded_instead_of_falling_back_to_path(self):
        with patch.dict(os.environ, {"COMPANY_AGENT_CLAUDE": "", "COMPANY_WORKSPACE_CLAUDE_ENTRY": "claude",
                                     "COMPANY_WORKSPACE_CLAUDE_PROFILE": "1"}):
            args = resolve_cli()
            self.assertEqual(args[args.index("-Entry") + 1], "claude")
            self.assertIn("-LoadProfiles", args)

    @unittest.skipUnless(os.name == "nt", "Windows terminal shim")
    def test_real_powershell_wrapper_streams_without_waiting_for_stdin_eof(self):
        fixture = ROOT / "tests/fixtures/workspace_fake_cli.ps1"
        events = []
        with tempfile.TemporaryDirectory(prefix="wrapper-한글 & ") as raw, \
                patch.dict(os.environ, {"WORKSPACE_FAKE_PYTHON": sys.executable}):
            command = resolve_cli(str(fixture))
            info = probe_cli(command)
            bridge = ClaudeSession(command, info, Path(raw), lambda k, d: events.append((k,d)))
            try:
                bridge.send("한글 요청")
                eventually(lambda: "q1" in bridge.pending, timeout=15)
                bridge.respond("q1", True, {"정리 방식?": "간단히"})
                eventually(lambda: "p1" in bridge.pending)
                bridge.respond("p1", False)
                eventually(lambda: any(k == "result" for k,d in events))
                self.assertTrue(any("한글 요청" in d.get("text", "") for k,d in events))
            finally:
                bridge.close()

    def test_double_click_does_not_skip_existing_terminal_profile(self):
        source = (ROOT / "Company-Workspace.vbs").read_text(encoding="utf-8")
        self.assertNotIn("-NoProfile", source)


if __name__ == "__main__":
    unittest.main()
