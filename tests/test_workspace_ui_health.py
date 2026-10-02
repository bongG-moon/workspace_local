from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import threading

from local_app.ui_health import UiHealthLog, frontend_row, MAX_EVENTS
from local_app.server import LocalApp, Server


def event(**changes):
    return {'documentId': 'document-test-123', 'event': 'startup', 'module': 'app',
            'status': 'failed', 'reason': 'missing-handler', 'missing': ['workflow'], **changes}


class UiHealthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        self.log = UiHealthLog(self.state, '0.21.1')

    def test_fixed_fields_exclude_user_content_and_credentials(self):
        self.assertTrue(self.log.frontend(event(message='PRIVATE', token='SECRET',
            url='http://127.0.0.1/#token=SECRET', stack='PRIVATE', path='PRIVATE')))
        text = self.log.path.read_text()
        self.assertNotIn('PRIVATE', text)
        self.assertNotIn('SECRET', text)
        row = json.loads(text)['events'][0]
        self.assertEqual(['workflow'], row['missing'])
        self.assertIn('serverPid', row)
        self.assertEqual(self.log.launch_id, row['launchId'])

    def test_updater_load_failure_is_accepted_and_retained_without_remote_metadata(self):
        self.assertTrue(self.log.frontend(event(event='module', module='app-updates',
            reason='resource-error', missing=['app-updates'], url='https://private.example/release',
            message='PRIVATE RELEASE NOTES')))
        row = self.log.rows[-1]
        self.assertEqual(('app-updates', 'resource-error', ['app-updates']),
                         (row['module'], row['reason'], row['missing']))
        restarted = UiHealthLog(self.state, '0.22.0')
        self.assertEqual('app-updates', restarted.rows[-1]['module'])
        self.assertNotIn('PRIVATE', self.log.path.read_text())
        self.assertNotIn('private.example', self.log.path.read_text())

    def test_rejects_freeform_values_nonfinite_numbers_and_unbounded_lists(self):
        for changes in ({'documentId':'http://private/path'}, {'module':'secrets'},
                        {'reason':'file content'}, {'event':[]}, {'status':'CUSTOM'},
                        {'elapsedMs':float('nan')}, {'column':True}, {'missing':['workflow']*100},
                        {'missing':['/secret']}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                frontend_row(event(**changes))
        self.assertFalse(self.log.path.exists())

    def test_progress_load_failure_survives_restart_without_record_contents(self):
        self.assertTrue(self.log.frontend(event(event='module', module='progress-view',
            reason='missing-handler', missing=['progress-view'],
            message='PRIVATE TOOL OUTPUT', records=[{'text': 'PRIVATE TOOL OUTPUT'}])))
        restarted = UiHealthLog(self.state, '0.23.4')
        row = restarted.rows[-1]
        self.assertEqual(('progress-view', 'missing-handler', ['progress-view']),
                         (row['module'], row['reason'], row['missing']))
        self.assertNotIn('records', row)
        self.assertNotIn('PRIVATE', self.log.path.read_text())

    def test_native_context_correlates_server_window_and_document(self):
        self.assertTrue(self.log.native({'type':'ready', 'pid':321, 'hwnd':456,
            'runtime':'154.0.123.5', 'url':'secret'}))
        self.log.frontend(event())
        row = self.log.rows[-1]
        self.assertEqual((321,456,'154.0.123.5'), (row['hostPid'],row['hwnd'],row['runtime']))
        self.assertEqual('document-test-123', row['documentId'])
        self.assertFalse(self.log.native({'type':'ack','id':1}))

    def test_malformed_native_values_cannot_poison_host_context(self):
        for value in (None, [], {}, {'type': []}, {'type': {}}, {'type': None}):
            with self.subTest(value=value):
                self.assertFalse(self.log.native(value))
        self.log.native({'type': 'ready', 'pid': 321, 'hwnd': 456, 'runtime': '154.0.123.5'})
        self.log.native({'type': 'error', 'pid': 654, 'hwnd': True,
                         'runtime': ['PRIVATE'], 'code': 10**400})
        self.log.frontend(event())
        self.assertEqual({'hostPid': 654}, self.log.native_context)
        self.assertEqual(654, self.log.rows[-1]['hostPid'])
        self.assertNotIn('hwnd', self.log.rows[-1])
        self.assertNotIn('runtime', self.log.rows[-1])
        self.log.native({'type': 'exited', 'pid': 654})
        self.log.frontend(event())
        self.assertNotIn('hostPid', self.log.rows[-1])
        self.assertNotIn('PRIVATE', self.log.path.read_text())

    def test_rate_limited_host_change_still_updates_document_correlation(self):
        now = [0]
        log = UiHealthLog(self.state, '0.21.1', monotonic=lambda: now[0])
        log.native({'type': 'ready', 'pid': 321, 'hwnd': 456, 'runtime': '154.0.123.5'})
        for _ in range(299):
            log.frontend(event())
        self.assertFalse(log.native({'type': 'error', 'pid': 654}))
        now[0] = 61
        self.assertTrue(log.frontend(event()))
        self.assertEqual(654, log.rows[-1]['hostPid'])
        self.assertNotIn('hwnd', log.rows[-1])
        self.assertNotIn('runtime', log.rows[-1])

    def test_record_count_and_write_rate_are_bounded(self):
        log = UiHealthLog(self.state, '0.21.1', monotonic=lambda:1)
        for _ in range(300):
            self.assertTrue(log.frontend(event()))
        self.assertEqual(MAX_EVENTS, len(log.rows))
        self.assertFalse(log.frontend(event()))
        self.assertLess(log.path.stat().st_size, 256*1024)

    def test_restart_retains_sanitized_history_not_private_modified_values(self):
        self.log.frontend(event())
        data = json.loads(self.log.path.read_text())
        data['events'][0]['message'] = 'PRIVATE'
        data['events'].append({**data['events'][0], 'reason':'PRIVATE'})
        self.log.path.write_text(json.dumps(data))
        restarted = UiHealthLog(self.state,'0.21.1')
        self.assertEqual(1,len(restarted.rows))
        self.assertNotEqual(self.log.launch_id,restarted.launch_id)
        restarted.frontend(event(status='ready',reason=''))
        self.assertNotIn('PRIVATE',restarted.path.read_text())

    def test_restart_skips_overflowing_timestamps_and_malformed_native_records(self):
        self.log.frontend(event())
        valid = self.log.rows[0]
        malformed = [{**valid, 'at': value} for value in (10**400, float('nan'), float('inf'))]
        malformed += [{**valid, 'module': 'native', 'event': []},
                      {**valid, 'module': 'native', 'status': {}}]
        self.log.path.write_text(json.dumps({'events': [*malformed, valid]}))
        restarted = UiHealthLog(self.state, '0.21.1')
        self.assertEqual([valid], list(restarted.rows))
        self.assertEqual({}, restarted.native_context)
        self.assertTrue(restarted.frontend(event()))

    def test_deeply_nested_diagnostics_never_prevent_app_startup(self):
        self.log.path.write_text('[' * 3000 + '0' + ']' * 3000)
        app = LocalApp(self.state, demo=True)
        try:
            self.assertEqual([], list(app.ui_health.rows))
            self.assertTrue(app.ui_health.frontend(event()))
        finally:
            app.close()

    def test_storage_failure_never_stops_app(self):
        with patch('local_app.ui_health.os.replace',side_effect=OSError('unavailable')):
            self.assertFalse(self.log.frontend(event()))
        self.assertTrue(self.log.frontend(event()))


class UiHealthRouteTests(unittest.TestCase):
    def test_endpoint_requires_same_existing_bearer_and_never_creates_tasks(self):
        with tempfile.TemporaryDirectory() as temp:
            app = LocalApp(Path(temp), demo=True)
            server = Server(app)
            worker = threading.Thread(target=server.serve_forever,daemon=True);worker.start()
            try:
                def call(token):
                    headers={'Content-Type':'application/json'}
                    if token:headers['Authorization']='Bearer '+token
                    return urlopen(Request(server.origin+'/api/ui-health',data=json.dumps(event()).encode(),headers=headers),timeout=3)
                with self.assertRaises(HTTPError) as denied:call(None)
                self.assertEqual(403,denied.exception.code)
                with call(app.token) as response:self.assertTrue(json.load(response)['recorded'])
                self.assertFalse(app.sessions)
                self.assertEqual('failed',app.ui_health.rows[-1]['status'])
            finally:
                server.shutdown();server.server_close();app.close();worker.join(3)


if __name__ == '__main__':
    unittest.main()
