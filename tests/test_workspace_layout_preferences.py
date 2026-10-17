import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from local_app.layout_preferences import LayoutPreferences
from local_app.server import LocalApp, Server


LAYOUT = {'sidebarCollapsed': True, 'inspectorCollapsed': False, 'inspectorWidth': 440, 'miniMode': True}


class LayoutPreferencesTests(unittest.TestCase):
    def test_unconfigured_persistence_and_independent_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            layout = LayoutPreferences(directory)
            self.assertIsNone(layout.snapshot())
            self.assertFalse(layout.path.exists())
            supplied = dict(LAYOUT)
            result = layout.configure(supplied)
            supplied['miniMode'] = result['miniMode'] = False
            self.assertEqual(LAYOUT, layout.snapshot())
            snapshot = layout.snapshot()
            snapshot['inspectorWidth'] = 260
            self.assertEqual(LAYOUT, LayoutPreferences(directory).snapshot())
            self.assertEqual(['layout.json'], [path.name for path in Path(directory).iterdir()])

    def test_exact_fields_types_and_width_range(self):
        with tempfile.TemporaryDirectory() as directory:
            layout = LayoutPreferences(directory)
            invalid = [None, [], {}, {**LAYOUT, 'theme': 'dark'},
                       {key: value for key, value in LAYOUT.items() if key != 'miniMode'}]
            for key in ('sidebarCollapsed', 'inspectorCollapsed', 'miniMode'):
                invalid.extend({**LAYOUT, key: value} for value in (None, 0, 1, 'true', [], {}))
            invalid.extend({**LAYOUT, 'inspectorWidth': value} for value in
                           (True, False, 259, 561, 440.0, '440', None, float('nan'), float('inf')))
            for value in invalid:
                with self.subTest(value=value), self.assertRaises(ValueError):
                    layout.configure(value)
                self.assertIsNone(layout.snapshot())
                self.assertFalse(layout.path.exists())
            for width in (260, 560):
                self.assertEqual(width, layout.configure({**LAYOUT, 'inspectorWidth': width})['inspectorWidth'])

    def test_corrupt_oversized_or_unknown_fields_fall_back_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'layout.json'
            for raw in ('{', 'null', '[]', json.dumps({**LAYOUT, 'unexpected': True}),
                        json.dumps({**LAYOUT, 'miniMode': 1}), json.dumps(LAYOUT) + ' ' * 1025):
                path.write_text(raw, encoding='utf-8')
                self.assertIsNone(LayoutPreferences(directory).snapshot())
                self.assertEqual(raw, path.read_text(encoding='utf-8'))

    def test_idempotent_and_failed_atomic_write_preserve_saved_value(self):
        with tempfile.TemporaryDirectory() as directory:
            layout = LayoutPreferences(directory)
            layout.configure(LAYOUT)
            before = layout.path.read_bytes()
            modified = layout.path.stat().st_mtime_ns
            with patch('local_app.layout_preferences.os.replace') as replace:
                self.assertEqual(LAYOUT, layout.configure(dict(LAYOUT)))
                replace.assert_not_called()
            self.assertEqual(modified, layout.path.stat().st_mtime_ns)
            with patch('local_app.layout_preferences.os.replace', side_effect=OSError('fixture')):
                with self.assertRaises(OSError):
                    layout.configure({**LAYOUT, 'miniMode': False})
            self.assertEqual(LAYOUT, layout.snapshot())
            self.assertEqual(before, layout.path.read_bytes())
            self.assertEqual([], list(Path(directory).glob('.layout-*.tmp')))

    def test_symlink_is_not_read_or_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'original.json'
            target.write_text(json.dumps(LAYOUT), encoding='utf-8')
            path = root / 'layout.json'
            try:
                path.symlink_to(target)
            except OSError:
                self.skipTest('Symbolic links are unavailable for this account')
            layout = LayoutPreferences(root)
            self.assertIsNone(layout.snapshot())
            with self.assertRaises(ValueError):
                layout.configure({**LAYOUT, 'miniMode': False})
            self.assertTrue(path.is_symlink())
            self.assertEqual(LAYOUT, json.loads(target.read_text(encoding='utf-8')))

    def test_authenticated_routes_bootstrap_and_busy_task_are_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            app = LocalApp(Path(directory), demo=True)
            server = Server(app)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()

            def request(route, data=None, auth=True):
                req = Request(server.origin + route,
                    data=json.dumps(data).encode() if data is not None else None,
                    headers={'Content-Type': 'application/json',
                             **({'Authorization': 'Bearer ' + app.token} if auth else {})})
                try:
                    response = urlopen(req, timeout=5)
                except HTTPError as error:
                    response = error
                with response:
                    return response.status, json.loads(response.read())

            try:
                self.assertIsNone(request('/api/bootstrap')[1]['layout'])
                self.assertEqual(403, request('/api/bootstrap', auth=False)[0])
                self.assertEqual(403, request('/api/layout', LAYOUT, auth=False)[0])
                self.assertFalse(app.layout_preferences.path.exists())
                self.assertEqual(400, request('/api/layout', {**LAYOUT, 'id': 'unwanted'})[0])
                self.assertEqual(400, request('/api/layout', {**LAYOUT, 'inspectorWidth': True})[0])
                self.assertIsNone(app.layout_preferences.snapshot())
                sid = app.create(kind='chat')['id']
                item = app.get(sid)
                item['state'] = 'running'
                before = copy.deepcopy(item)
                with patch.object(app, 'connect', side_effect=AssertionError('No Claude connection')) as connect, \
                     patch.object(app, 'send', side_effect=AssertionError('No Claude request')) as send:
                    self.assertEqual((200, LAYOUT), request('/api/layout', LAYOUT))
                    self.assertEqual(LAYOUT, request('/api/bootstrap')[1]['layout'])
                    connect.assert_not_called()
                    send.assert_not_called()
                self.assertEqual(before, item)
                self.assertEqual(LAYOUT, LayoutPreferences(directory).snapshot())
                item['state'] = 'idle'
            finally:
                server.shutdown()
                server.server_close()
                thread.join(2)
                app.close()


if __name__ == '__main__':
    unittest.main()
