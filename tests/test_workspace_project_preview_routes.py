"""Project resources use their own capability; app APIs retain their auth boundary."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from local_app.server import LocalApp, Server


class ProjectPreviewRoutes(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.work = self.root / 'website'
        self.work.mkdir()
        self.page = self.work / 'index.html'
        self.page.write_text('<link rel="stylesheet" href="theme.css"><h1>hello</h1><script src="main.js"></script>')
        (self.work / 'theme.css').write_text('h1{color:purple}')
        (self.work / 'main.js').write_text('document.querySelector("h1").textContent="ready"')
        self.app = LocalApp(self.root / 'state', demo=True)
        self.addCleanup(self.app.close)
        self.sid = self.app.create(str(self.work), True)['id']
        self.server = Server(self.app, 0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def post(self, route, data, *, authenticated=True, origin=None):
        headers = {'Content-Type': 'application/json'}
        if authenticated:
            headers['Authorization'] = 'Bearer ' + self.app.token
        if origin is not None:
            headers['Origin'] = origin
        request = Request(self.server.origin + route, data=json.dumps(data).encode(), headers=headers)
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def open(self, **options):
        return self.post('/api/project-preview/open', {'id': self.sid, 'path': str(self.page)}, **options)

    def get(self, url, *, origin='null'):
        try:
            response = urlopen(Request(self.server.origin + url, headers={'Origin': origin}), timeout=5)
        except HTTPError as error:
            response = error
        with response:
            return response.status, response.headers, response.read()

    def test_capability_and_sibling_resources_without_relaxing_app_api(self):
        self.assertEqual(403, self.open(authenticated=False)[0])
        self.assertEqual(403, self.open(origin='null')[0])
        status, lease = self.open()
        self.assertEqual(200, status)
        self.assertTrue(lease['url'].startswith('/project-preview/'))
        status, headers, body = self.get(lease['url'])
        self.assertEqual(200, status)
        self.assertIn(b'<script src="main.js">', body)
        prefix = lease['url'].rsplit('/', 1)[0] + '/'
        self.assertEqual(200, self.get(prefix + 'theme.css')[0])
        self.assertEqual(200, self.get(prefix + 'main.js')[0])
        self.assertIn('sandbox allow-scripts', headers['Content-Security-Policy'])
        self.assertNotIn("script-src 'self'", headers['Content-Security-Policy'])
        self.assertNotIn(self.app.token, json.dumps(lease))
        self.assertEqual(403, self.get('/api/bootstrap')[0])
        self.assertEqual(403, self.post('/api/project-preview/close',
            {'id': self.sid, 'previewId': lease['id']}, origin='null')[0])

    def test_an_external_attachment_does_not_grant_its_siblings(self):
        outside = self.root / 'outside.html'
        outside.write_text('<script src="secret.js"></script>')
        self.app.get(self.sid)['attachments'] = [str(outside)]
        status, data = self.post('/api/project-preview/open', {'id': self.sid, 'path': str(outside)})
        self.assertEqual(400, status)
        self.assertNotIn('secret.js', json.dumps(data))

    def test_revoked_trust_and_close_revoke_asset_access(self):
        _, lease = self.open()
        other = self.app.create(str(self.work), True)['id']
        self.post('/api/project-preview/close', {'id': other, 'previewId': lease['id']})
        self.assertEqual(200, self.get(lease['url'])[0])
        self.post('/api/project-preview/close', {'id': self.sid, 'previewId': lease['id']})
        self.assertGreaterEqual(self.get(lease['url'])[0], 400)
        _, lease = self.open()
        self.app.get(self.sid)['trusted'] = False
        self.assertGreaterEqual(self.get(lease['url'])[0], 400)

    def test_shutdown_releases_previews_without_starting_extra_servers(self):
        _, lease = self.open()
        self.assertTrue(self.app.close())
        self.assertGreaterEqual(self.get(lease['url'])[0], 400)


if __name__ == '__main__':
    unittest.main()
