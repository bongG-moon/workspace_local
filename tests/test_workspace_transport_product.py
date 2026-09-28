"""Product transport contracts, exercised without credentials, network or an LLM."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from local_app.bridge import BridgeError, ClaudeSession, cli_arguments, probe_cli

FAKE = [sys.executable, "-X", "utf8", str(ROOT / "tests/fixtures/workspace_fake_cli.py")]


def eventually(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError("Local protocol fixture did not finish")


class ProductTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="workspace-product-")
        self.events = []
        self.bridge = ClaudeSession(FAKE, probe_cli(FAKE), Path(self.temp.name),
                                    lambda kind, data: self.events.append((kind, data)))

    def tearDown(self):
        self.bridge.close()
        self.temp.cleanup()

    def send_and_finish(self):
        count = sum(kind == "result" for kind, _ in self.events)
        self.bridge.send("STREAM_PROTOCOL_TEST")
        eventually(lambda: sum(kind == "result" for kind, _ in self.events) > count)

    def stream(self, event, parent=None):
        self.bridge.handle({"type": "stream_event", "parent_tool_use_id": parent, "event": event})

    def test_partial_flag_is_optional_and_does_not_change_model_or_policy(self):
        self.assertIn("--include-partial-messages", cli_arguments(FAKE, self.bridge.info))
        old_args = cli_arguments(FAKE, {"help": "--input-format --output-format --permission-prompt-tool"})
        self.assertNotIn("--include-partial-messages", old_args)
        for flag in ("--model", "--settings", "--permission-mode", "--dangerously-skip-permissions"):
            self.assertNotIn(flag, cli_arguments(FAKE, self.bridge.info))

    def test_real_child_stream_final_and_result_are_one_persistable_message(self):
        self.send_and_finish()
        deltas = [data for kind, data in self.events if kind == "assistant_delta"]
        finals = [data for kind, data in self.events if kind == "assistant"]
        self.assertEqual("".join(data["text"] for data in deltas), "부분 응답 확인")
        self.assertEqual(len(finals), 1)
        self.assertEqual(finals[0]["text"], "부분 응답 확인")
        self.assertEqual(finals[0]["index"], 2)
        self.assertTrue(all(data["messageId"] == finals[0]["messageId"] for data in deltas))
        self.assertFalse(self.bridge.busy)

    def test_old_cli_still_emits_complete_answer(self):
        self.bridge.info = {}
        self.send_and_finish()
        self.assertFalse(any(kind == "assistant_delta" for kind, _ in self.events))
        self.assertEqual(sum(kind == "assistant" for kind, _ in self.events), 1)

    def test_nested_agent_and_tool_json_never_become_main_answer_deltas(self):
        self.stream({"type": "message_start", "message": {"id": "main"}})
        self.stream({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "private nested"}}, "tool-child")
        self.stream({"type": "message_start", "message": {"id": "child"}}, "tool-child")
        self.stream({"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "{}"}})
        self.stream({"type": "content_block_delta", "index": 2, "delta": {"type": "text_delta", "text": "main answer"}})
        self.bridge.handle({"type": "assistant", "parent_tool_use_id": "tool-child", "message": {"id": "child", "content": [{"type": "text", "text": "private nested"}]}})
        deltas = [data for kind, data in self.events if kind == "assistant_delta"]
        self.assertEqual(deltas, [{"messageId": "main", "index": 2, "text": "main answer"}])
        self.assertFalse(any(kind == "assistant" for kind, _ in self.events))

    def test_multiple_block_finalization_matches_stream_index(self):
        self.stream({"type": "message_start", "message": {"id": "main"}})
        for index, text in [(1, "첫 내용"), (3, "둘째 내용")]:
            self.stream({"type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": text}})
            frame = {"type": "assistant", "message": {"id": "main", "content": [{"type": "text", "text": text}]}}
            self.bridge.handle(frame)
            self.bridge.handle(frame)
        finals = [data for kind, data in self.events if kind == "assistant"]
        self.assertEqual([data["index"] for data in finals], [1, 3])
        self.assertEqual(len(finals), 2)

    def test_complete_blocks_sharing_an_id_are_preserved_without_partial_support(self):
        for text in ("앞부분", "뒷부분", "뒷부분"):
            self.bridge.handle({"type": "assistant", "message": {"id": "main", "content": [{"type": "text", "text": text}]}})
        finals = [data for kind, data in self.events if kind == "assistant"]
        self.assertEqual([data["text"] for data in finals], ["앞부분", "뒷부분"])
        self.assertEqual([data["index"] for data in finals], [0, 1])

    def test_result_can_finalize_matching_partial_if_complete_frame_is_absent(self):
        self.stream({"type": "message_start", "message": {"id": "main"}})
        self.stream({"type": "content_block_delta", "index": 4, "delta": {"type": "text_delta", "text": "결과"}})
        self.bridge.handle({"type": "result", "is_error": False, "result": "결과"})
        result = next(data for kind, data in self.events if kind == "assistant")
        self.assertEqual((result["messageId"], result["index"]), ("main", 4))

    def test_init_models_commands_are_from_child_response(self):
        self.assertFalse(self.bridge.capabilities["setModel"])
        self.send_and_finish()
        connected = next(data for kind, data in self.events if kind == "connected")
        self.assertEqual(connected["availableModels"][0]["value"], "fake-only")
        self.assertEqual(connected["slashCommands"][0]["name"], "test-skill")
        self.assertTrue(connected["capabilities"]["setModel"])
        self.assertEqual(self.bridge.original_model, "fake-only")

    def test_missing_capability_data_does_not_invent_models(self):
        self.bridge.handle({"type": "control_response", "response": {"request_id": self.bridge.initialize_id, "subtype": "success", "response": {}}})
        self.bridge.handle({"type": "system", "subtype": "init", "model": "actual-company-model"})
        self.assertEqual(self.bridge.available_models, [])
        self.assertEqual(self.bridge.slash_commands, [])

    def test_idle_model_control_round_trip_and_reset_preserve_global_environment(self):
        self.send_and_finish()
        previous = dict(os.environ)
        state = self.bridge.set_model("fake-alternative")
        self.assertEqual(state["model"], "fake-alternative")
        self.assertEqual(state["modelOverride"], "fake-alternative")
        self.send_and_finish()
        self.assertEqual(self.bridge.original_model, "fake-only")
        state = self.bridge.set_model(None)
        self.assertEqual(state["model"], "fake-only")
        self.assertIsNone(state["modelOverride"])
        self.assertEqual(dict(os.environ), previous)
        self.assertEqual(sum(kind == "model_changed" for kind, _ in self.events), 2)

    def test_model_rejection_keeps_active_model_and_connection(self):
        self.send_and_finish()
        with self.assertRaises(BridgeError) as caught:
            self.bridge.set_model("fake-rejected")
        self.assertEqual(caught.exception.code, "model_rejected")
        self.assertEqual(self.bridge.model, "fake-only")
        self.assertIsNone(self.bridge.model_override)
        self.assertFalse(self.bridge.closed)

    def test_model_timeout_retires_uncertain_connection(self):
        self.send_and_finish()
        with patch("local_app.bridge.CONTROL_TIMEOUT", .05):
            with self.assertRaises(BridgeError) as caught:
                self.bridge.set_model("fake-timeout")
        self.assertEqual(caught.exception.code, "model_timeout")
        self.assertTrue(self.bridge.closed)
        self.assertFalse(self.bridge.capabilities["setModel"])
        self.assertIsNone(self.bridge.model_override)

    def test_model_change_rejects_not_connected_busy_and_invalid_values(self):
        with self.assertRaises(BridgeError) as caught:
            self.bridge.set_model("valid")
        self.assertEqual(caught.exception.code, "model_unavailable")
        self.send_and_finish()
        self.bridge.busy = True
        with self.assertRaises(BridgeError) as caught:
            self.bridge.set_model("valid")
        self.assertEqual(caught.exception.code, "session_busy")
        self.bridge.busy = False
        for invalid in ("", " ", "a\nb", "a" * 201, 123, []):
            with self.subTest(invalid=invalid), self.assertRaises(BridgeError):
                self.bridge.set_model(invalid)

    def test_control_change_blocks_concurrent_prompt_until_response(self):
        self.send_and_finish()
        errors = []
        def change():
            try:
                self.bridge.set_model("fake-timeout")
            except BridgeError as exc:
                errors.append(exc)
        with patch("local_app.bridge.CONTROL_TIMEOUT", 2):
            worker = threading.Thread(target=change)
            worker.start()
            eventually(lambda: self.bridge._control_active)
            with self.assertRaises(ValueError):
                self.bridge.send("Do not send concurrently")
            self.bridge.close()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors[0].code, "connection_closed")


if __name__ == "__main__":
    unittest.main()
