"""SDK preparation is explicit, hash-pinned, and leaves source/user state alone."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import URLError
from urllib.request import Request

from workspace_publisher.config import PublisherError
from workspace_publisher import sdk


class FakeResponse(io.BytesIO):
    def __init__(self, raw, url, *, status=200, length=None):
        super().__init__(raw)
        self.url, self.status = url, status
        self.headers = {} if length is None else {'Content-Length': length}

    def geturl(self):
        return self.url


class PublisherSDKTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'deploy').mkdir()
        self.raw = b'pinned SDK test fixture\x00' * 4000
        self.version = '1.0.4258.31'
        self.url = ('https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/'
                    + self.version + '/microsoft.web.webview2.' + self.version + '.nupkg')
        self.lock = {'package': 'Microsoft.Web.WebView2', 'version': self.version,
                     'url': self.url, 'sha256': hashlib.sha256(self.raw).hexdigest()}
        self.lock_path = self.root / 'deploy/WebView2.lock.json'
        self.lock_path.write_text(json.dumps(self.lock), encoding='utf-8')
        self.cache = self.root / 'build/desktop-sdk' / (self.version + '.nupkg')
        self.input = self.root / 'approved.nupkg'
        self.input.write_bytes(self.raw)

    def put_cache(self, raw):
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        self.cache.write_bytes(raw)

    def assert_no_temporary(self):
        self.assertEqual(list(self.root.rglob('.sdk-*.tmp')), [])

    def download(self, response, address=None, **kwargs):
        with patch.object(sdk, 'build_opener') as factory:
            factory.return_value.open.return_value = response
            result = sdk.download_sdk(self.root, address, **kwargs)
            return result, factory

    def test_inspection_is_read_only_and_never_connects(self):
        with patch.object(sdk, 'build_opener') as network:
            result = sdk.inspect_sdk(self.root)
        self.assertEqual(result['status'], 'missing')
        self.assertEqual(result['version'], self.version)
        self.assertFalse((self.root / 'build').exists())
        network.assert_not_called()

    def test_import_verifies_and_preserves_source_files(self):
        before = self.lock_path.read_bytes()
        events = []
        result = sdk.import_sdk(self.root, self.input, emit=events.append)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(self.cache.read_bytes(), self.raw)
        self.assertEqual(self.input.read_bytes(), self.raw)
        self.assertEqual(self.lock_path.read_bytes(), before)
        self.assertEqual(sdk.inspect_sdk(self.root)['status'], 'ready')
        self.assertGreater(len(events), 0)
        self.assert_no_temporary()

    def test_invalid_cache_repaired_only_after_successful_hash(self):
        self.put_cache(b'previous bad cache')
        self.assertEqual(sdk.inspect_sdk(self.root)['status'], 'invalid')
        self.input.write_bytes(b'wrong file')
        with self.assertRaisesRegex(PublisherError, '체크섬'):
            sdk.import_sdk(self.root, self.input)
        self.assertEqual(self.cache.read_bytes(), b'previous bad cache')
        self.assert_no_temporary()
        self.input.write_bytes(self.raw)
        self.assertEqual(sdk.import_sdk(self.root, self.input)['status'], 'ready')

    def test_ready_cache_not_replaced_and_download_does_not_connect(self):
        self.put_cache(self.raw)
        original = self.cache.stat().st_mtime_ns
        with patch.object(sdk, 'build_opener') as network:
            sdk.import_sdk(self.root, self.root / 'missing-input.nupkg')
            sdk.download_sdk(self.root)
        self.assertEqual(self.cache.stat().st_mtime_ns, original)
        network.assert_not_called()

    def test_invalid_lock_rejected_without_writes_or_network(self):
        for fields in ({'version': '../x'}, {'sha256': 'short'}, {'package': 'something-else'},
                       {'url': 'https://other.example/sdk.nupkg'}, {'url': 'https://user:secret@example/x'}):
            with self.subTest(fields=fields):
                self.lock_path.write_text(json.dumps({**self.lock, **fields}), encoding='utf-8')
                with self.assertRaises(PublisherError), patch.object(sdk, 'build_opener') as network:
                    sdk.inspect_sdk(self.root)
                network.assert_not_called()
                self.assertFalse((self.root / 'build').exists())

    def test_import_empty_or_directory_rejected(self):
        self.input.write_bytes(b'')
        for path in (self.input, self.root / 'deploy'):
            with self.subTest(path=path), self.assertRaises(PublisherError):
                sdk.import_sdk(self.root, path)
        self.assertFalse(self.cache.exists())
        self.assert_no_temporary()

    def test_download_pinned_default_and_no_credentials(self):
        result, factory = self.download(FakeResponse(self.raw, self.url, length=str(len(self.raw))))
        self.assertEqual(result['status'], 'ready')
        request = factory.return_value.open.call_args.args[0]
        self.assertEqual(request.full_url, self.url)
        self.assertEqual(request.get_method(), 'GET')
        self.assertFalse(any('token' in key.lower() or 'authorization' in key.lower()
                             for key, value in request.header_items()))
        self.assertEqual(self.cache.read_bytes(), self.raw)
        self.assert_no_temporary()

    def test_explicit_internal_url_is_not_saved_or_logged(self):
        address = 'https://intranet.example:8443/packages/sdk.nupkg'
        events = []
        result, _ = self.download(FakeResponse(self.raw, address), address, emit=events.append)
        self.assertEqual(result['url'], self.url)  # The public pinned URL only.
        self.assertNotIn('intranet.example', str(events))
        self.assertEqual(list(self.cache.parent.iterdir()), [self.cache])
        self.assertEqual(json.loads(self.lock_path.read_text())['url'], self.url)

    def test_download_bad_hash_preserves_existing_cache(self):
        self.put_cache(b'previous cache')
        with self.assertRaisesRegex(PublisherError, '체크섬'):
            self.download(FakeResponse(b'wrong SDK', self.url))
        self.assertEqual(self.cache.read_bytes(), b'previous cache')
        self.assert_no_temporary()

    def test_download_rejects_unsafe_urls_before_network(self):
        values = ['http://intranet.example/sdk', 'https://a:b@example.test/sdk',
                  'https://example.test/sdk?token=topsecret', 'https://example.test/sdk#token',
                  'https://example.test\\other/sdk', 'https://example.test:70000/sdk',
                  'https://example.test/\nsecret', 'file:///sdk']
        for address in values:
            with self.subTest(address=address), patch.object(sdk, 'build_opener') as network:
                with self.assertRaises(PublisherError) as error:
                    sdk.download_sdk(self.root, address)
                self.assertNotIn('topsecret', str(error.exception))
                network.assert_not_called()
        self.assertFalse(self.cache.exists())

    def test_download_rejects_wrong_response_origin_or_status(self):
        for response in (FakeResponse(self.raw, 'https://evil.example/sdk'),
                         FakeResponse(self.raw, self.url, status=401),
                         FakeResponse(self.raw, self.url, status=404)):
            with self.subTest(response=response), self.assertRaises(PublisherError):
                self.download(response)
        self.assertFalse(self.cache.exists())
        self.assert_no_temporary()

    def test_download_rejects_oversize_header_and_stream(self):
        for length in ('999999999999', '-1', 'text', '0'):
            with self.subTest(length=length), self.assertRaises(PublisherError):
                self.download(FakeResponse(self.raw, self.url, length=length))
        with patch.object(sdk, 'MAX_SDK_BYTES', 10), self.assertRaises(PublisherError):
            self.download(FakeResponse(self.raw, self.url))
        self.assertFalse(self.cache.exists())
        self.assert_no_temporary()

    def test_cancellation_preserves_files_before_and_during_download(self):
        cancel = threading.Event()
        cancel.set()
        with patch.object(sdk, 'build_opener') as network, self.assertRaisesRegex(PublisherError, '취소'):
            sdk.download_sdk(self.root, cancel=cancel)
        network.assert_not_called()
        cancel.clear()
        response = FakeResponse(self.raw, self.url)
        original_read = response.read

        def read_chunk(size):
            chunk = original_read(size)
            cancel.set()
            return chunk

        response.read = read_chunk
        self.put_cache(b'previous cache')
        with self.assertRaisesRegex(PublisherError, '취소'):
            self.download(response, cancel=cancel)
        self.assertEqual(self.cache.read_bytes(), b'previous cache')
        self.assert_no_temporary()

    def test_import_cancelled_just_before_commit_keeps_cache(self):
        self.put_cache(b'previous cache')
        cancel = threading.Event()
        original = sdk._copy_verified

        def copied(*args, **kwargs):
            original(*args, **kwargs)
            cancel.set()

        with patch.object(sdk, '_copy_verified', side_effect=copied), self.assertRaisesRegex(PublisherError, '취소'):
            sdk.import_sdk(self.root, self.input, cancel=cancel)
        self.assertEqual(self.cache.read_bytes(), b'previous cache')
        self.assert_no_temporary()

    def test_download_deadline_cleans_temporary(self):
        with patch.object(sdk, 'DOWNLOAD_TIMEOUT', -1), self.assertRaisesRegex(PublisherError, '초과'):
            self.download(FakeResponse(self.raw, self.url))
        self.assertFalse(self.cache.exists())
        self.assert_no_temporary()

    def test_network_error_never_exposes_server_or_secrets(self):
        with patch.object(sdk, 'build_opener') as factory:
            factory.return_value.open.side_effect = URLError('proxy token=TOPSECRET')
            with self.assertRaises(PublisherError) as error:
                sdk.download_sdk(self.root)
        self.assertNotIn('TOPSECRET', str(error.exception))
        self.assertFalse(self.cache.exists())
        self.assert_no_temporary()

    def test_redirect_policy_allows_only_exact_approved_https_origins(self):
        request = Request(self.url)
        handler = sdk._SDKRedirects({('api.nuget.org', 443), ('globalcdn.nuget.org', 443)}, None)
        result = handler.redirect_request(request, None, 302, 'Found', {},
                                          'https://globalcdn.nuget.org/packages/pinned.nupkg')
        self.assertEqual(result.full_url, 'https://globalcdn.nuget.org/packages/pinned.nupkg')
        for address in ('https://evil.nuget.org/sdk', 'http://api.nuget.org/sdk',
                        'https://globalcdn.nuget.org:8443/sdk', 'https://user:secret@api.nuget.org/sdk',
                        'https://globalcdn.nuget.org/sdk?token=secret'):
            with self.subTest(address=address), self.assertRaises(PublisherError):
                handler.redirect_request(request, None, 302, 'Found', {}, address)
        internal = sdk._SDKRedirects({('intranet.example', 443)}, None)
        with self.assertRaises(PublisherError):
            internal.redirect_request(request, None, 302, 'Found', {}, self.url)

    def test_symlink_input_rejected(self):
        link = self.root / 'linked.nupkg'
        try:
            link.symlink_to(self.input)
        except OSError:
            self.skipTest('Symbolic link creation not allowed on this Windows host')
        with self.assertRaises(PublisherError):
            sdk.import_sdk(self.root, link)
        self.assertFalse(self.cache.exists())


if __name__ == '__main__':
    unittest.main()
