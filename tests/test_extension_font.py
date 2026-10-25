"""Only the bundled, non-sensitive font is readable by opaque extension frames."""
import threading
import unittest
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from local_app.server import Server


class ExtensionFontTests(unittest.TestCase):
    def setUp(self):
        self.server = Server(SimpleNamespace(token='test-only'), 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)

    def test_opaque_frame_can_read_only_cacheable_bundled_font(self):
        headers = {'Origin': 'null', 'Sec-Fetch-Site': 'cross-site'}
        with urlopen(Request(self.server.origin + '/fonts/NotoSansKR-Variable.woff', headers=headers)) as response:
            self.assertEqual(response.headers['Content-Type'], 'font/woff')
            self.assertEqual(response.headers['Access-Control-Allow-Origin'], '*')
            self.assertIn('max-age=3600', response.headers['Cache-Control'])
            self.assertNotIn('Access-Control-Allow-Credentials', response.headers)
            self.assertEqual(response.read()[:4], b'wOFF')
        for path in ('/api/bootstrap', '/app.css', '/fonts/OFL.txt',
                     '/fonts/NotoSansKR-Variable.woff?other=path'):
            with self.subTest(path=path), self.assertRaises(HTTPError) as caught:
                urlopen(Request(self.server.origin + path, headers=headers))
            self.assertEqual(caught.exception.code, 403)

    def test_foreign_origin_and_host_are_rejected(self):
        for headers in ({'Origin': 'https://unrelated.example'}, {'Host': 'unrelated.example'}):
            with self.subTest(headers=headers), self.assertRaises(HTTPError) as caught:
                urlopen(Request(self.server.origin + '/fonts/NotoSansKR-Variable.woff', headers=headers))
            self.assertEqual(caught.exception.code, 403)
