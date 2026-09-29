"""Catalog browsing must never replace a task's execution context."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from local_app.server import LocalApp, Server


class CatalogScopeRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.a, self.b = self.root / '업무 A', self.root / '조회 B'
        self.a.mkdir(); self.b.mkdir()
        self.app = LocalApp(self.root / 'state', command=['must-not-start'],
                            managed_workspace_root=self.root / 'managed')
        self.addCleanup(self.app.close)
        self.sid = self.app.create(str(self.a), True)['id']
        self.app.get(self.sid)['trusted'] = False
        self.server = Server(self.app, 0)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.shutdown)

    def shutdown(self):
        self.server.shutdown(); self.server.server_close(); self.worker.join(2)

    def request(self, **params):
        request = Request(self.server.origin + '/api/capabilities?' + urlencode(params),
                          headers={'Authorization': 'Bearer ' + self.app.token})
        with urlopen(request, timeout=3) as response:
            return json.loads(response.read())

    def test_common_has_no_execution_context_and_folder_browsing_does_not_grant_trust(self):
        with patch('local_app.server.catalog', return_value={'schemaVersion': 2}) as build, \
             patch('local_app.server.ClaudeSession', side_effect=AssertionError('must not execute')):
            self.request(scope='common')
            self.assertIsNone(build.call_args.args[0])
            self.assertIsNone(build.call_args.kwargs['workspace'])
            self.request(scope='folder', workspace=str(self.b))
            self.assertIsNone(build.call_args.args[0])
            self.assertEqual(str(self.b), build.call_args.kwargs['workspace'])
        item = self.app.get(self.sid)
        self.assertEqual(str(self.a), item['workspace'])
        self.assertFalse(item['trusted'])
        self.assertIsNone(item['bridge'])
        self.assertEqual(1, len(self.app.sessions))

    def test_runtime_must_match_explicit_folder(self):
        with patch('local_app.server.catalog', return_value={}) as build:
            self.request(scope='folder', workspace=str(self.a), id=self.sid)
            self.assertEqual(self.sid, build.call_args.args[0]['id'])
            self.assertFalse(build.call_args.args[0]['trusted'])
            with self.assertRaises(HTTPError) as caught:
                self.request(scope='folder', workspace=str(self.b), id=self.sid)
            self.assertEqual(400, caught.exception.code)
            self.assertEqual(1, build.call_count)

    def test_invalid_browse_scope_never_reaches_inventory(self):
        cases = [dict(scope='all'), dict(scope='folder'),
                 dict(scope='folder', workspace='relative'),
                 dict(scope='folder', workspace=str(self.root / 'missing')),
                 dict(scope='common', id=self.sid),
                 dict(scope='common', workspace=str(self.b))]
        with patch('local_app.server.catalog') as build:
            for params in cases:
                with self.subTest(params=params), self.assertRaises(HTTPError) as caught:
                    self.request(**params)
                self.assertEqual(400, caught.exception.code)
            build.assert_not_called()


if __name__ == '__main__':
    unittest.main()
