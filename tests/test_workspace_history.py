import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from local_app.history import HistoryStore, read


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def item(self, text='확인한 내용'):
        return {'id':str(uuid.uuid4()),'title':'한글 업무','workspace':str(self.root),'created':1.0,
                'sessionId':str(uuid.uuid4()),'messages':[{'role':'assistant','text':text}],
                'trusted':True,'requests':{'secret':'do not persist'},'connection':{'auth':'never persist'}}

    def test_migrate_on_save_and_preserve_legacy_bytes_and_ids(self):
        items = [self.item(), self.item()]
        legacy = self.root/'history.json'
        legacy.write_text(json.dumps(items, ensure_ascii=False), encoding='utf-8')
        raw = legacy.read_bytes()
        store = HistoryStore(self.root)
        loaded = store.load()
        self.assertEqual([x['id'] for x in items],[x['id'] for x in loaded])
        self.assertFalse((self.root/'history-index.json').exists())
        store.save(loaded, loaded[0]['id'])
        self.assertEqual(raw, legacy.read_bytes())
        self.assertEqual(loaded, HistoryStore(self.root).load())
        for path in (self.root/'history-sessions').glob('*.json'):
            for secret in ('requests','trusted','connection','do not persist','never persist'):
                self.assertNotIn(secret,path.read_text(encoding='utf-8'))

    def test_one_event_writes_only_changed_session_not_other_39_histories(self):
        items = [self.item() for _ in range(40)]
        for item in items:
            item['messages'] = [{'role':'assistant','text':'가'*100000} for _ in range(5)]
        store = HistoryStore(self.root)
        store.save(items)
        before = (self.root/'history-sessions'/(items[0]['id']+'.json')).stat().st_mtime_ns
        items[-1]['messages'][-1]['text'] = '변경된 대화'
        with patch.object(store,'_write',wraps=store._write) as saved:
            store.save(items,items[-1]['id'])
        self.assertEqual(1,saved.call_count)
        self.assertEqual(items[-1]['id']+'.json',saved.call_args.args[0].name)
        self.assertLess(len(saved.call_args.args[1]),1501000)
        self.assertEqual(before,(self.root/'history-sessions'/(items[0]['id']+'.json')).stat().st_mtime_ns)

    def test_broken_index_is_preserved_and_not_overwritten(self):
        path = self.root/'history-index.json'
        path.write_bytes(b'broken')
        store = HistoryStore(self.root)
        self.assertEqual([],store.load())
        self.assertIsNotNone(store.warning)
        store.save([self.item()])
        self.assertEqual(b'broken',path.read_bytes())
        self.assertFalse((self.root/'history-sessions').exists())

    def test_failed_atomic_replace_preserves_previous_session(self):
        store = HistoryStore(self.root)
        item = self.item()
        store.save([item])
        path = self.root/'history-sessions'/(item['id']+'.json')
        before = path.read_bytes()
        item['messages'] = [{'role':'assistant','text':'new'}]
        with patch.object(Path,'replace',side_effect=OSError('fixture failure')), self.assertRaises(OSError):
            store.save([item],item['id'])
        self.assertEqual(before,path.read_bytes())
        self.assertFalse(list(path.parent.glob('*.tmp')))

    def test_untrusted_index_id_cannot_select_another_path(self):
        (self.root/'history-index.json').write_text(json.dumps({'schemaVersion':1,'sessions':[{'id':'../outside'}]}),encoding='utf-8')
        store = HistoryStore(self.root)
        self.assertEqual([],store.load())
        self.assertTrue(store.warning)

    def test_long_attachment_paths_also_fit_the_read_bound(self):
        item = self.item()
        item['messages'] = [{'role':'user','text':'자료 확인','files':['가'*4000]*12} for _ in range(150)]
        store = HistoryStore(self.root)
        store.save([item])
        restored = HistoryStore(self.root)
        loaded = restored.load()
        self.assertIsNone(restored.warning)
        self.assertEqual(item['id'],loaded[0]['id'])
        self.assertLessEqual(sum(len(m['text'])+sum(len(p) for p in m.get('files',[])) for m in loaded[0]['messages']),500000)

    def tracked_stream(self, path, after_read=None):
        stream = path.open('rb')
        calls = []
        class Tracked:
            def __enter__(self): return self
            def __exit__(self, *args): return stream.__exit__(*args)
            def fileno(self): return stream.fileno()
            def read(self, count):
                calls.append(count)
                raw = stream.read(count)
                if after_read is not None: after_read()
                return raw
        return Tracked(), calls

    def test_small_file_read_allocation_tracks_actual_size_not_maximum_bound(self):
        path = self.root/'small-index.json'
        raw = b'{"sessions":[]}'
        path.write_bytes(raw)
        stream, calls = self.tracked_stream(path)
        with patch.object(Path, 'open', return_value=stream):
            self.assertEqual({'sessions': []}, read(path, 16*1024*1024))
        self.assertEqual([len(raw)+1], calls)

    def test_oversized_file_is_rejected_before_allocating_body_buffer(self):
        path = self.root/'oversized.json'
        path.write_bytes(b'{"too_large":true}')
        stream, calls = self.tracked_stream(path)
        with patch.object(Path, 'open', return_value=stream), self.assertRaisesRegex(ValueError, '범위'):
            read(path, 4)
        self.assertEqual([], calls)

    def test_file_changed_during_read_is_rejected_without_overwriting_it(self):
        path = self.root/'changing.json'
        path.write_bytes(b'{"sessions":[]}')
        original_open = Path.open
        def append():
            with original_open(path, 'ab') as writer:
                writer.write(b' ')
        stream, calls = self.tracked_stream(path, after_read=append)
        with patch.object(Path, 'open', return_value=stream), self.assertRaisesRegex(ValueError, '변경'):
            read(path, 16*1024*1024)
        self.assertEqual(b'{"sessions":[]} ', path.read_bytes())

    def test_transient_windows_replace_lock_retries_same_atomic_payload(self):
        target = self.root/'atomic.json'
        target.write_bytes(b'old')
        error = PermissionError(13, 'temporary Windows scanner lock')
        error.winerror = 5
        original_replace = Path.replace
        calls = []
        def replace(path, destination):
            calls.append((path, destination))
            if len(calls) <= 2: raise error
            return original_replace(path, destination)
        with patch.object(Path, 'replace', autospec=True, side_effect=replace), patch('local_app.history.time.sleep') as sleep:
            HistoryStore(self.root)._write(target, b'new')
        self.assertEqual(b'new', target.read_bytes())
        self.assertEqual(3, len(calls))
        self.assertEqual(1, len({path for path, _ in calls}))
        self.assertEqual([.01, .025], [call.args[0] for call in sleep.call_args_list])
        self.assertFalse(list(self.root.glob('*.tmp')))

    def test_permanent_windows_replace_denial_stays_bounded_and_preserves_original(self):
        target = self.root/'atomic.json'
        target.write_bytes(b'old')
        error = PermissionError(13, 'permanent denied access')
        error.winerror = 5
        with patch.object(Path, 'replace', side_effect=error) as replace, patch('local_app.history.time.sleep') as sleep:
            with self.assertRaises(PermissionError): HistoryStore(self.root)._write(target, b'new')
        self.assertEqual(4, replace.call_count)
        self.assertAlmostEqual(.085, sum(call.args[0] for call in sleep.call_args_list))
        self.assertEqual(b'old', target.read_bytes())
        self.assertFalse(list(self.root.glob('*.tmp')))

    def test_replace_retry_revalidates_destination_path_before_next_attempt(self):
        from local_app import history
        target = self.root/'atomic.json'
        target.write_bytes(b'old')
        error = PermissionError(13, 'temporary lock')
        error.winerror = 32
        original_safe = history.safe
        checked = []
        def safe_path(path):
            if path == target:
                checked.append(path)
                if len(checked) == 3: raise ValueError('fixture destination changed')
            return original_safe(path)
        with patch.object(history, 'safe', side_effect=safe_path), patch.object(Path, 'replace', side_effect=error) as replace, patch('local_app.history.time.sleep'):
            with self.assertRaisesRegex(ValueError, 'destination changed'):
                HistoryStore(self.root)._write(target, b'new')
        self.assertEqual(1, replace.call_count)
        self.assertEqual(b'old', target.read_bytes())


if __name__=='__main__':unittest.main()
