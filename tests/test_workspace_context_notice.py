import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from local_app.server import LocalApp


class ContextNoticeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.app = LocalApp(root / 'state', demo=True)
        self.addCleanup(self.app.close)
        self.sid = self.app.create(str(root), True)['id']
        self.item = self.app.get(self.sid)

    def usage(self, used):
        self.item['contextUsage'] = {'usedTokens': used, 'windowTokens': 100000, 'percent': 60}

    def test_exact_threshold_and_busy_states_do_not_consume_notice_or_save(self):
        with patch.object(self.app, 'save') as save:
            self.assertFalse(self.app.claim_context_notice(self.sid)['show'])
            self.usage(60000)
            self.assertFalse(self.app.claim_context_notice(self.sid)['show'])
            self.usage(60001)
            for state in ('running', 'starting', 'approval', 'question', 'stopping', 'error'):
                self.item['state'] = state
                self.assertFalse(self.app.claim_context_notice(self.sid)['show'], state)
            save.assert_not_called()
        self.item['state'] = 'done'
        self.assertTrue(self.app.claim_context_notice(self.sid)['show'])

    def test_concurrent_views_get_one_notice_and_no_replay_after_restart(self):
        self.usage(60001)
        with ThreadPoolExecutor(max_workers=4) as pool:
            replies = list(pool.map(self.app.claim_context_notice, [self.sid] * 4))
        self.assertEqual(sum(reply['show'] for reply in replies), 1)
        self.assertTrue(self.app.public(self.item)['contextNoticeShown'])
        restarted = LocalApp(self.app.state, demo=True)
        self.addCleanup(restarted.close)
        restored = restarted.get(self.sid)
        self.assertTrue(restarted.public(restored)['contextNoticeShown'])
        with patch.object(restarted, 'save') as save:
            self.assertFalse(restarted.claim_context_notice(self.sid)['show'])
            save.assert_not_called()

    def test_failed_save_can_retry_and_other_task_is_independent(self):
        self.usage(60001)
        with patch.object(self.app, 'save', side_effect=OSError('fixture')):
            with self.assertRaises(OSError):
                self.app.claim_context_notice(self.sid)
        self.assertFalse(self.app.public(self.item)['contextNoticeShown'])
        self.assertTrue(self.app.claim_context_notice(self.sid)['show'])
        other = self.app.create(self.item['workspace'], True)['id']
        self.app.get(other)['contextUsage'] = dict(self.item['contextUsage'])
        self.assertTrue(self.app.claim_context_notice(other)['show'])
