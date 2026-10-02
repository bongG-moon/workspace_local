"""Publisher UI jobs: secret handling, cancellation and explicit publication."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from workspace_publisher.gui import (DEFAULT_CONFIG, PublisherJobs, PublisherWindow,
                                     build_identity, public_text, settings_only)


ROOT = Path(__file__).resolve().parents[1]
CONFIG = {**DEFAULT_CONFIG, "baseUrl": "https://gitlab.example", "projectId": "42",
          "remoteUrl": "git@gitlab.example:group/workspace.git", "releaseTag": "v0.22.0"}
SOURCE = {"version": "0.22.0", "branch": "main", "commit": "a" * 40, "clean": True,
          "releaseTag": "v0.22.0"}
BUILD = {"version": "0.22.0", "commit": "a" * 40, "directory": "C:/build/output",
         "files": [{"name": "package.zip", "path": "C:/build/output/package.zip"}]}


class FakeCore:
    def __init__(self):
        self.calls = []
        self.block = None
        self.entered = threading.Event()
        self.cancel_seen = None
        self.failure = None
        self.verified = True
        self.leak_token = False
        self.last_build = None
        self.restore_failure = None
        self.source = deepcopy(SOURCE)

    def factory(self, root, *, emit):
        state = self

        class Publisher:
            def load_config(self):
                return deepcopy(CONFIG)

            def inspect_source(self):
                return deepcopy(state.source)

            def load_last_build(self, config, *, cancel=None):
                state.calls.append(("restore", deepcopy(config)))
                state.cancel_seen = cancel
                if state.restore_failure:
                    raise ValueError(state.restore_failure)
                return deepcopy(state.last_build)

            def save_config(self, config):
                state.calls.append(("save", deepcopy(config)))
                return deepcopy(config)

            def check_connection(self, config, *, cancel=None):
                return self.run("connection", config, cancel)

            def preview_sync(self, config, *, cancel=None):
                self.run("preview", config, cancel)
                return {**SOURCE, "remoteUrl": config["remoteUrl"], "tag": config["releaseTag"]}

            def sync(self, config, preview, *, cancel=None):
                state.calls.append(("preview-value", deepcopy(preview)))
                return self.run("sync", config, cancel)

            def build(self, config, *, cancel=None):
                self.run("build", config, cancel)
                state.last_build = deepcopy(BUILD)
                return deepcopy(BUILD)

            def publish(self, config, built, token, token_kind="deploy", *, cancel=None):
                state.calls.append(("publish-args", deepcopy(built), token, token_kind))
                self.run("publish", config, cancel)
                if state.leak_token:
                    emit({"kind": "info", "message": "token=" + token})
                return {"verified": state.verified, "version": "0.22.0",
                        "message": token if state.leak_token else "게시 완료", "token": token}

            def run(self, action, config, cancel):
                state.calls.append((action, deepcopy(config)))
                state.cancel_seen = cancel
                state.entered.set()
                if state.block:
                    while not state.block.wait(.01):
                        if cancel.is_set():
                            raise ValueError("작업을 취소했습니다.")
                if state.failure:
                    raise ValueError(state.failure)
                emit({"kind": "progress", "message": action + " 진행 중"})
                return {"ok": True, "message": action + " 완료"}

        return Publisher()


class PublisherJobsTests(unittest.TestCase):
    def setUp(self):
        self.core = FakeCore()
        self.jobs = PublisherJobs(ROOT, self.core.factory)

    def finish(self):
        self.jobs.thread.join(3)
        self.assertFalse(self.jobs.thread.is_alive())
        return self.jobs.drain()

    def test_load_reads_source_version_without_any_mutation(self):
        self.jobs.start("load")
        events = self.finish()
        self.assertEqual("done", events[-1]["kind"])
        self.assertEqual(SOURCE, events[-1]["result"]["source"])
        self.assertEqual([("restore", CONFIG)], self.core.calls)
        self.assertIsNone(events[-1]["result"]["build"])

    def test_startup_restores_verified_build_without_building_saving_or_publishing(self):
        self.core.last_build = deepcopy(BUILD)
        self.jobs.start("load")
        event = self.finish()[-1]
        self.assertEqual("done", event["kind"])
        self.assertEqual(BUILD, event["result"]["build"])
        self.assertEqual([("restore", CONFIG)], self.core.calls)
        self.assertIs(self.jobs.cancel_event, self.core.cancel_seen)

    def test_bad_saved_build_is_notice_and_does_not_lose_settings_or_source(self):
        self.core.restore_failure = "이전 파일의 해시가 다릅니다."
        self.jobs.start("load")
        event = self.finish()[-1]
        self.assertEqual("done", event["kind"])
        self.assertEqual(CONFIG, event["result"]["config"])
        self.assertEqual(SOURCE, event["result"]["source"])
        self.assertIsNone(event["result"]["build"])
        self.assertIn("해시", event["result"]["buildNotice"])

    def test_explicit_restore_checks_current_config_without_mutation(self):
        self.core.last_build = deepcopy(BUILD)
        config = {**CONFIG, "projectId": "99"}
        self.jobs.start("restore", config)
        event = self.finish()[-1]
        self.assertEqual(BUILD, event["result"]["build"])
        self.assertEqual(build_identity(config), event["configIdentity"])
        self.assertEqual([("restore", config)], self.core.calls)

    def test_config_allowlist_excludes_token_and_password_before_persistence(self):
        self.jobs.start("connection", {**CONFIG, "token": "SECRET", "password": "PRIVATE"})
        self.finish()
        saved = self.core.calls[0][1]
        self.assertEqual(CONFIG, saved)
        self.assertNotIn("SECRET", json.dumps(saved))
        self.assertNotIn("PRIVATE", json.dumps(saved))
        self.assertEqual(["save", "connection"], [row[0] for row in self.core.calls])

    def test_preview_never_pushes_builds_or_publishes(self):
        self.jobs.start("preview", CONFIG)
        result = self.finish()[-1]
        self.assertEqual(["save", "preview"], [row[0] for row in self.core.calls])
        self.assertEqual(CONFIG["remoteUrl"], result["result"]["remoteUrl"])

    def test_sync_uses_exact_reviewed_preview(self):
        preview = {**SOURCE, "remoteUrl": CONFIG["remoteUrl"], "tag": "v0.22.0"}
        self.jobs.start("sync", CONFIG, preview=preview)
        preview["commit"] = "changed-after-click"
        self.finish()
        recorded = next(row[1] for row in self.core.calls if row[0] == "preview-value")
        self.assertEqual("a" * 40, recorded["commit"])

    def test_worker_is_nonblocking_rejects_duplicate_and_receives_cancellation(self):
        self.core.block = threading.Event()
        began = time.monotonic()
        self.assertTrue(self.jobs.start("connection", CONFIG))
        self.assertLess(time.monotonic() - began, .5)
        self.assertTrue(self.core.entered.wait(1))
        self.assertTrue(self.jobs.busy)
        self.assertFalse(self.jobs.start("publish", CONFIG, token="NEVER"))
        self.assertTrue(self.jobs.cancel())
        result = self.finish()[-1]
        self.assertEqual("failed", result["kind"])
        self.assertTrue(self.core.cancel_seen.is_set())
        self.assertNotIn("publish", [row[0] for row in self.core.calls])

    def test_build_identity_tracks_destination_but_allows_notes_and_token_kind_changes(self):
        self.assertEqual(build_identity(CONFIG), build_identity({**CONFIG, "notes": "새 설명", "tokenKind": "job"}))
        self.assertNotEqual(build_identity(CONFIG), build_identity({**CONFIG, "projectId": "99"}))
        self.jobs.start("build", CONFIG)
        result = self.finish()[-1]
        self.assertEqual(BUILD, result["result"])
        self.assertEqual(build_identity(CONFIG), result["configIdentity"])

    def test_publish_passes_memory_token_only_to_publish_and_redacts_all_events(self):
        self.core.leak_token = True
        self.jobs.start("publish", {**CONFIG, "tokenKind": "job"}, build_result=BUILD, token="TEST_SECRET")
        events = self.finish()
        publish = next(row for row in self.core.calls if row[0] == "publish-args")
        self.assertEqual(("TEST_SECRET", "job"), publish[2:])
        self.assertNotIn("TEST_SECRET", json.dumps(events))
        self.assertNotIn("TEST_SECRET", json.dumps(self.core.calls[0][1]))
        self.assertNotIn("token", events[-1]["result"])
        self.assertEqual("done", events[-1]["kind"])

    def test_unverified_publish_never_claims_completion(self):
        self.core.verified = False
        self.jobs.start("publish", CONFIG, build_result=BUILD, token="TEST_SECRET")
        result = self.finish()[-1]
        self.assertEqual("failed", result["kind"])
        self.assertIn("인증 없는 다운로드", result["message"])

    def test_failure_message_redacts_token_and_encoded_token(self):
        self.core.failure = "token TEST/SECRET query TEST%2FSECRET"
        self.jobs.start("publish", CONFIG, build_result=BUILD, token="TEST/SECRET")
        events = self.finish()
        self.assertNotIn("SECRET", json.dumps(events))
        self.assertEqual("failed", events[-1]["kind"])

    def test_missing_token_or_build_never_calls_publish(self):
        for options in ({"build_result": BUILD}, {"token": "SECRET"}):
            self.jobs.start("publish", CONFIG, **options)
            self.assertEqual("failed", self.finish()[-1]["kind"])
        self.assertNotIn("publish-args", [row[0] for row in self.core.calls])

    def test_event_queue_is_bounded_and_terminal_outcome_survives(self):
        def factory(root, *, emit):
            class Publisher:
                def inspect_source(self):
                    for number in range(700):
                        emit({"kind": "info", "message": str(number)})
                    return SOURCE
            return Publisher()
        self.jobs = PublisherJobs(ROOT, factory)
        self.jobs.start("inspect")
        events = self.finish()
        self.assertLessEqual(len(events), 512)
        self.assertEqual("done", events[-1]["kind"])

    def test_long_notes_are_not_silently_truncated_when_loading_settings(self):
        notes = "설명" * 9000
        self.assertEqual(notes, settings_only({**CONFIG, "notes": notes})["notes"])
        self.assertEqual("[게시 토큰 숨김]", public_text("MY_SECRET", "MY_SECRET"))


class PublisherWindowTests(unittest.TestCase):
    def setUp(self):
        try:
            import tkinter as tk
            self.root = tk.Tk()
            self.root.withdraw()
        except Exception as exc:
            self.skipTest("Tk 화면 세션이 없어 창 검증을 생략합니다: " + str(exc))
        self.core = FakeCore()
        self.window = PublisherWindow(self.root, ROOT, self.core.factory)
        self.addCleanup(self.cleanup_window)
        self.finish()

    def reopen(self):
        import tkinter as tk
        self.window.destroy()
        self.root = tk.Tk()
        self.root.withdraw()
        self.window = PublisherWindow(self.root, ROOT, self.core.factory)
        self.finish()

    def cleanup_window(self):
        self.window.jobs.cancel()
        if self.core.block:
            self.core.block.set()
        if self.window.jobs.thread:
            self.window.jobs.thread.join(3)
        if not self.window.closed:
            self.window.destroy()

    def finish(self):
        self.window.jobs.thread.join(3)
        self.assertFalse(self.window.jobs.thread.is_alive())
        self.window.poll()

    def test_initial_window_shows_current_source_and_masks_token(self):
        self.assertIn("0.22.0", self.window.source_label.get())
        self.assertEqual("v0.22.0", self.window.fields["releaseTag"].get())
        self.assertEqual("●", self.window.token_entry.cget("show"))
        self.window.token.set("DO_NOT_SAVE")
        self.assertNotIn("DO_NOT_SAVE", json.dumps(self.window.config()))
        self.assertEqual("disabled", str(self.window.publish_button.cget("state")))

    def test_publish_clears_entry_immediately_and_waits_for_verified_result(self):
        self.window.build_result = deepcopy(BUILD)
        self.window.build_config_identity = build_identity(self.window.config())
        self.window.token.set("MEMORY_ONLY")
        self.core.block = threading.Event()
        self.window.start("publish")
        self.assertEqual("", self.window.token.get())
        self.assertIsNotNone(self.window.active_action)
        self.assertNotIn("게시 완료", self.window.status.get())
        self.core.block.set()
        self.finish()
        self.assertIn("인증 없는 다운로드 검증", self.window.status.get())
        self.assertNotIn("MEMORY_ONLY", self.window.log.get("1.0", "end"))

    def test_changing_destination_disables_publish_until_rebuild(self):
        self.window.build_result = deepcopy(BUILD)
        self.window.build_config_identity = build_identity(self.window.config())
        self.window.refresh_controls()
        self.assertEqual("normal", str(self.window.publish_button.cget("state")))
        self.window.fields["projectId"].set("99")
        self.assertEqual("disabled", str(self.window.publish_button.cget("state")))

    def test_restart_after_failed_publish_reuses_exact_build_without_automatic_publish(self):
        self.window.start("build")
        self.finish()
        original = deepcopy(self.window.build_result)
        self.core.failure = "게시 서버에 연결하지 못했습니다."
        self.window.token.set("FIRST_ATTEMPT")
        self.window.start("publish")
        self.finish()
        self.assertIn("연결하지", self.window.status.get())
        self.core.failure = None
        self.core.calls.clear()
        self.reopen()
        self.assertEqual(original, self.window.build_result)
        self.assertEqual("normal", str(self.window.publish_button.cget("state")))
        self.assertIn("이전 빌드 복원됨", self.window.build_label.get())
        self.assertIn(SOURCE["commit"][:12], self.window.build_label.get())
        self.assertEqual("", self.window.token.get())
        self.assertEqual(["restore"], [row[0] for row in self.core.calls])
        self.window.token.set("NEW_ATTEMPT")
        self.window.start("publish")
        self.finish()
        recorded = next(row for row in self.core.calls if row[0] == "publish-args")
        self.assertEqual(original, recorded[1])
        self.assertIn("게시 완료", self.window.status.get())

    def test_restored_older_build_is_labeled_separately_from_current_source(self):
        self.core.last_build = deepcopy(BUILD)
        self.core.source = {**SOURCE, "version": "0.23.0", "commit": "b" * 40, "releaseTag": "v0.23.0"}
        self.reopen()
        self.assertIn("0.23.0", self.window.source_label.get())
        self.assertIn("0.22.0", self.window.build_label.get())
        self.assertIn("다른 커밋", self.window.build_label.get())
        self.assertEqual("normal", str(self.window.publish_button.cget("state")))

    def test_restore_failure_keeps_window_usable_and_publish_disabled(self):
        self.core.restore_failure = "이전 빌드의 배포 대상이 다릅니다."
        self.reopen()
        self.assertEqual(CONFIG["baseUrl"], self.window.fields["baseUrl"].get())
        self.assertIn("복원하지 못했습니다", self.window.status.get())
        self.assertEqual("disabled", str(self.window.publish_button.cget("state")))
        self.assertIsNone(self.window.active_action)
        self.core.restore_failure = None
        self.core.last_build = deepcopy(BUILD)
        self.window.start("restore")
        self.finish()
        self.assertEqual("normal", str(self.window.publish_button.cget("state")))
        self.core.restore_failure = "해시 검증 실패"
        self.window.start("restore")
        self.finish()
        self.assertIsNone(self.window.build_result)
        self.assertEqual("disabled", str(self.window.publish_button.cget("state")))

    def test_busy_close_requests_cancel_and_waits_before_destroying(self):
        self.core.block = threading.Event()
        self.window.start("connection")
        self.assertTrue(self.core.entered.wait(1))
        self.window.messagebox = SimpleNamespace(askyesno=lambda *args, **kwargs: True)
        self.window.close()
        self.assertTrue(self.window.closing)
        self.assertFalse(self.window.closed)
        self.assertTrue(self.window.jobs.cancel_event.is_set())
        self.finish()
        self.assertTrue(self.window.closed)


class PublisherEntrypointTests(unittest.TestCase):
    def test_default_entry_uses_its_source_directory_not_the_terminal_directory(self):
        spec = importlib.util.spec_from_file_location("publisher_entry_fixture", ROOT / "Publish-Workspace.py")
        entry = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(entry)
        with patch.object(entry.sys, "argv", ["Publish-Workspace.py"]), patch("workspace_publisher.gui.main", return_value=0) as run:
            self.assertEqual(0, entry.main())
        run.assert_called_once_with(ROOT)

    @unittest.skipUnless(shutil.which("git"), "Git is required for the real publisher contract check")
    def test_actual_core_load_save_and_source_contract_in_an_isolated_repository(self):
        from workspace_publisher.core import Publisher
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "local_app").mkdir()
            (repo / "local_app/server.py").write_text('WORKSPACE_VERSION = "0.22.0"\n', encoding="utf-8")
            (repo / ".gitignore").write_text("build/\n", encoding="utf-8")
            def git(*args):
                return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, timeout=10)
            git("init", "-b", "main")
            git("add", ".")
            git("-c", "user.name=Publisher Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "fixture")
            jobs = PublisherJobs(repo, Publisher)
            jobs.start("save", {**CONFIG, "token": "DO_NOT_SAVE"})
            jobs.thread.join(5)
            self.assertFalse(jobs.thread.is_alive())
            self.assertEqual("done", jobs.drain()[-1]["kind"])
            self.assertNotIn("DO_NOT_SAVE", (repo / "build/publisher/config.json").read_text())
            jobs.start("load")
            jobs.thread.join(5)
            self.assertFalse(jobs.thread.is_alive())
            result = jobs.drain()[-1]
            self.assertEqual("done", result["kind"])
            self.assertEqual("0.22.0", result["result"]["source"]["version"])
            self.assertTrue(result["result"]["source"]["clean"])
            self.assertEqual(CONFIG, result["result"]["config"])


if __name__ == "__main__":
    unittest.main()
