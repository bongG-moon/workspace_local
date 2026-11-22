from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from local_app.bridge import ClaudeSession
from local_app.history import HistoryStore


class ContextUsageTests(unittest.TestCase):
    def setUp(self):
        self.session = str(uuid.uuid4())
        self.events = []
        self.bridge = ClaudeSession(['fixture'], {}, Path.cwd(), lambda k, d: self.events.append((k, d)))
        self.addCleanup(self.bridge.close)
        self.bridge.session_id = self.session
        self.bridge.busy = self.bridge._turn_submitted = True

    def frame(self, kind, **fields):
        self.bridge.handle({'type': kind, 'session_id': self.session, **fields})

    def message(self, usage, model='gateway-model', ident='model-message'):
        return {'model': model, 'id': ident, 'usage': usage, 'content': []}

    def result(self, models=None, **fields):
        if models is None:
            models = {'gateway-model': {'contextWindow': 1000, 'inputTokens': 99999}}
        self.frame('result', modelUsage=models, usage={'input_tokens': 999999}, **fields)
        return self.events[-1][1].get('contextUsage')

    def test_only_last_root_response_cache_inclusive_and_exact_model(self):
        self.frame('assistant', message=self.message({'input_tokens': 880}))
        self.frame('assistant', message=self.message({'input_tokens': 401, 'cache_creation_input_tokens': 88,
            'cache_read_input_tokens': 120, 'output_tokens': 800}, ident='last'))
        self.frame('assistant', isReplay=True, message=self.message({'input_tokens': 920}))
        self.frame('assistant', parent_tool_use_id='worker', message=self.message({'input_tokens': 10}))
        with patch.object(self.bridge, '_write', side_effect=AssertionError('unexpected CLI request')):
            self.assertEqual({'usedTokens': 609, 'windowTokens': 1000, 'percent': 61}, self.result({
                'worker': {'contextWindow': 200000}, 'gateway-model': {'contextWindow': 1000}}))

    def test_missing_inconsistent_or_unknown_model_yields_no_usage(self):
        for message, models in [
            (self.message({'input_tokens': 900}, model='alias'), {'actual': {'contextWindow': 1000}}),
            (self.message({'input_tokens': '900'}), {'gateway-model': {'contextWindow': 1000}}),
            (self.message({'input_tokens': 700}), {'gateway-model': {'contextWindow': True}}),
            (self.message({'input_tokens': 3, 'cache_read_input_tokens': -5}), {}),
            (self.message({'output_tokens': 700}), {'gateway-model': {'contextWindow': 1000}}),
        ]:
            with self.subTest(message=message, models=models):
                self.bridge.busy = True
                self.frame('assistant', message=message)
                self.assertIsNone(self.result(models))

    def test_compaction_drops_old_reading_and_next_response_can_start_low(self):
        self.frame('assistant', message=self.message({'input_tokens': 800}))
        self.frame('system', subtype='compact_boundary')
        self.assertIsNone(self.result())
        self.bridge.busy = True
        self.frame('assistant', message=self.message({'input_tokens': 101}))
        self.assertEqual(10, self.result()['percent'])
        self.bridge.busy = True
        self.assertIsNone(self.result())  # Aggregate-only response on another turn.

    def test_stream_usage_is_reused_only_for_the_matching_final_message(self):
        for ident, expected in [('first', 620), ('next', None)]:
            with self.subTest(ident=ident):
                self.bridge.busy = True
                self.frame('stream_event', event={'type': 'message_start',
                    'message': self.message({'input_tokens': 620}, ident='first')})
                self.frame('assistant', message=self.message(None, ident=ident))
                reading = self.result()
                self.assertEqual(expected, reading and reading['usedTokens'])

    def test_explicit_context_response_uses_its_reported_window(self):
        self.frame('assistant', message=self.message(None), context_usage={
            'totalTokens': 751, 'maxTokens': 1000, 'rawMaxTokens': 1000, 'categories': []})
        self.assertEqual(75, self.result({'gateway-model': {'contextWindow': 2000}})['percent'])

    def test_reconnect_and_stale_other_session_result_cannot_publish_previous_usage(self):
        self.frame('assistant', message=self.message({'input_tokens': 800}))
        self.assertIsNone(self.result(session_id=str(uuid.uuid4())))
        self.bridge.busy = False
        self.frame('assistant', message=self.message({'input_tokens': 750}))
        self.bridge.busy = True
        self.assertIsNone(self.result())

    def test_history_saves_only_counts_and_reloads_them_only_with_selected_body(self):
        with tempfile.TemporaryDirectory() as folder:
            store = HistoryStore(Path(folder))
            item = {'id': str(uuid.uuid4()), 'title': 'task', 'workspace': folder, 'created': 1.0,
                    'sessionId': self.session, 'messages': [],
                    'contextUsage': {'usedTokens': 610, 'windowTokens': 1000, 'percent': 90, 'private': 'omit'}}
            store.save([item], item['id'])
            restored = HistoryStore(Path(folder))
            single, = restored.load(lazy=True)
            self.assertNotIn('contextUsage', single)
            restored.hydrate(single)
            self.assertEqual({'percent': 61, 'usedTokens': 610, 'windowTokens': 1000}, single['contextUsage'])


if __name__ == '__main__':
    unittest.main()
