"""Project previews expose bounded local resources, never the app or secrets."""
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from local_app.project_preview import (CHUNK, DEFAULT_TTL, MAX_TRANSFER, MAX_WORKERS, MAX_REQUESTS,
    PreviewUnavailable, ProjectPreviewManager, SPOOL_MEMORY, _transformed)
from tests.test_workspace_file_preview import png


class Handler:
    def __init__(self, path, *, method='GET', headers=None):
        self.path, self.command = path, method
        self.headers = {'Host': '127.0.0.1:4242', 'Origin': 'null', **(headers or {})}
        self.wfile = io.BytesIO()
        self.status, self.response_headers = None, {}

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.response_headers[name] = value

    def end_headers(self):
        pass


class ProjectPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.entry = self.root / 'index.html'
        self.entry.write_text('<!doctype html><link rel="stylesheet" href="style.css">'
                              '<img src="images/logo.svg"><script type="module" src="main.js"></script>', encoding='utf-8')
        self.manager = ProjectPreviewManager()
        self.addCleanup(self.manager.close_all)

    def create(self, **kwargs):
        return self.manager.create(self.entry, self.root, origin='http://127.0.0.1:4242',
                                   authorize=kwargs.pop('authorize', lambda path, root: True),
                                   owner=kwargs.pop('owner', 'work-1'), **kwargs)

    def request(self, url, **kwargs):
        handler = Handler(url, **kwargs)
        self.assertTrue(self.manager.serve(handler))
        return handler

    def test_absent_previews_do_not_spawn_scan_read_or_cache(self):
        with patch('threading.Thread', side_effect=AssertionError('no background workers')), \
             patch('socket.socket', side_effect=AssertionError('no listener')), \
             patch.object(Path, 'read_bytes', side_effect=AssertionError('no file reads')), \
             patch('os.scandir', side_effect=AssertionError('no scans')):
            manager = ProjectPreviewManager()
            self.assertFalse(manager.serve(Handler('/api/anything')))
            self.assertEqual({}, manager._leases)
            manager.close_all()

    def test_create_keeps_only_metadata_and_reads_only_requested_resources(self):
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('no bundle')), \
             patch('os.scandir', side_effect=AssertionError('no scan')):
            lease = self.create()
        self.assertNotIn(str(self.root), str(lease))
        result = self.request(lease['url'])
        self.assertEqual(200, result.status)
        self.assertIn(b'main.js', result.wfile.getvalue())
        self.assertEqual({'id', 'url', 'name', 'message'}, set(lease))
        row = self.manager._leases[lease['id']]
        self.assertFalse(any(isinstance(value, bytes) for value in vars(row).values()))

    def test_sibling_css_module_json_font_and_image_stream_unchanged(self):
        samples = {'style.css': b'body{color:green}', 'main.js': b'import "./other.mjs";',
                   'other.mjs': b'export const x=1;', 'data.json': b'{"number":7}',
                   'font.woff2': b'wOF2test', 'images/logo.svg': b'<svg></svg>'}
        lease = self.create()
        base = lease['url'].rsplit('/', 1)[0] + '/'
        for name, body in samples.items():
            with self.subTest(name=name):
                path = self.root / name
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(body)
                response = self.request(base + name)
                self.assertEqual(200, response.status)
                self.assertEqual(body, response.wfile.getvalue())
                self.assertEqual('*', response.response_headers['Access-Control-Allow-Origin'])
                self.assertEqual('nosniff', response.response_headers['X-Content-Type-Options'])

    def test_root_urls_rewrite_only_resource_attributes_not_javascript(self):
        self.entry.write_text('<base href="https://outside.invalid/">'
                              '<link href="/styles/base.css"><img src="/images/logo.png">'
                              '<div style="background:url(/images/bg.png)"></div>'
                              '<script>const source="/main.js"; fetch("/data.json")</script>', encoding='utf-8')
        lease = self.create()
        response = self.request(lease['url'])
        html = response.wfile.getvalue().decode()
        prefix = '/project-preview/' + lease['id'] + '/'
        self.assertIn('href="' + prefix + 'styles/base.css"', html)
        self.assertIn('src="' + prefix + 'images/logo.png"', html)
        self.assertIn('url(' + prefix + 'images/bg.png)', html)
        self.assertIn('const source="/main.js"; fetch("/data.json")', html)
        self.assertNotIn('<base', html)

    def test_css_relative_urls_preserved_root_urls_and_imports_rewritten(self):
        (self.root / 'style.css').write_text('@import "/shared.css";'
                                           'a{background:url(/images/a.png)}'
                                           'b{background:url(../images/b.png)}'
                                           'c{background:url(https://outside.invalid/a.png)}', encoding='utf-8')
        lease = self.create()
        response = self.request(lease['url'].replace('index.html', 'style.css'))
        css = response.wfile.getvalue().decode()
        prefix = '/project-preview/' + lease['id'] + '/'
        self.assertIn('@import "' + prefix + 'shared.css"', css)
        self.assertIn('url(' + prefix + 'images/a.png)', css)
        self.assertIn('url(../images/b.png)', css)
        self.assertIn('url(https://outside.invalid/a.png)', css)
        # External spelling may remain; the response CSP is the boundary.
        policy = response.response_headers['Content-Security-Policy']
        self.assertNotIn('outside.invalid', policy)

    def test_capability_csp_is_path_scoped_and_forces_opaque_script_frame(self):
        lease = self.create()
        response = self.request(lease['url'])
        policy = response.response_headers['Content-Security-Policy']
        source = 'http://127.0.0.1:4242/project-preview/' + lease['id'] + '/'
        self.assertIn('sandbox allow-scripts;', policy)
        self.assertNotIn('allow-same-origin', policy)
        self.assertNotIn("'self'", policy)
        self.assertNotIn('unsafe-eval', policy)
        self.assertIn('connect-src ' + source + ';', policy)
        for directive in ("worker-src 'none'", "frame-src 'none'", "object-src 'none'", "form-action 'none'"):
            self.assertIn(directive, policy)
        self.assertEqual('no-referrer', response.response_headers['Referrer-Policy'])

    def test_wrong_host_origin_and_unknown_capability_cannot_read(self):
        lease = self.create()
        for headers in ({'Host': 'outside.invalid'}, {'Origin': 'http://outside.invalid'}, {'Origin': 'file://'}):
            with self.subTest(headers=headers):
                self.assertEqual(403, self.request(lease['url'], headers=headers).status)
        self.assertEqual(410, self.request(lease['url'].replace(lease['id'], 'not-the-capability')).status)

    def test_canonical_path_encoded_traversal_ads_hidden_private_and_unknown_types_denied(self):
        lease = self.create()
        base = lease['url'].rsplit('/', 1)[0] + '/'
        files = ['.env.json', 'auth.json', 'secrets.json', 'private/data.json', '.git/config.json', 'worker.py']
        for name in files:
            path = self.root / name
            path.parent.mkdir(exist_ok=True)
            path.write_text('do not reveal', encoding='utf-8')
        for name in files + ['../index.html', '%2e%2e/index.html', '%252e%252e/index.html',
                             'nested%2Findex.html', 'nested%5Cindex.html', 'index.html:stream',
                             'C:/index.html', '/index.html', 'missing.json']:
            with self.subTest(name=name):
                response = self.request(base + name)
                self.assertEqual(404, response.status)
                self.assertNotIn(b'do not reveal', response.wfile.getvalue())
                self.assertNotIn(str(self.root).encode(), response.wfile.getvalue())

    def test_authorization_rechecked_before_every_request_without_manager_lock(self):
        allowed, seen = [True], []

        def authorize(path, root):
            self.assertFalse(self.manager._lock._is_owned())
            seen.append(path.name)
            return allowed[0]

        lease = self.create(authorize=authorize)
        self.assertEqual(200, self.request(lease['url']).status)
        allowed[0] = False
        self.assertEqual(404, self.request(lease['url']).status)
        self.assertEqual(['index.html'] * 3, seen)

    def test_head_conditional_get_revalidates_without_streaming_and_updates_after_file_change(self):
        lease = self.create()
        first = self.request(lease['url'])
        head = self.request(lease['url'], method='HEAD')
        self.assertEqual((200, b''), (head.status, head.wfile.getvalue()))
        unchanged = self.request(lease['url'], headers={'If-None-Match': first.response_headers['ETag']})
        self.assertEqual((304, b''), (unchanged.status, unchanged.wfile.getvalue()))
        self.entry.write_text('<h1>changed and longer</h1>', encoding='utf-8')
        changed = self.request(lease['url'], headers={'If-None-Match': first.response_headers['ETag']})
        self.assertEqual(200, changed.status)
        self.assertNotEqual(first.response_headers['ETag'], changed.response_headers['ETag'])

    def test_close_owner_close_all_expiry_and_lru_keep_capabilities_bounded(self):
        now = [1.0]
        self.manager = ProjectPreviewManager(ttl=10, clock=lambda: now[0])
        self.addCleanup(self.manager.close_all)
        first = self.create()
        self.assertFalse(self.manager.close(first['id'], owner='other-work'))
        self.assertEqual(200, self.request(first['url']).status)
        second = self.create(owner='work-2')
        third = self.create(owner='work-3')
        self.assertEqual(2, len(self.manager._leases))
        self.assertEqual(410, self.request(first['url']).status)
        self.assertTrue(self.manager.close(second['id'], owner='work-2'))
        self.assertEqual(410, self.request(second['url']).status)
        now[0] += 11
        self.assertEqual(410, self.request(third['url']).status)
        self.assertEqual({}, self.manager._leases)
        self.manager.close_all()
        with self.assertRaises(PreviewUnavailable):
            self.create()

    def test_large_transformed_document_spills_and_returns_finite_output(self):
        content = b'<html><body>' + b'<p>content</p>' * (SPOOL_MEMORY // 7) + b'</body></html>'
        with io.BytesIO(content) as source:
            output, size = _transformed(source, '.html', '/project-preview/example/')
            with output:
                self.assertTrue(output._rolled)
                self.assertEqual(size, len(output.read()))
                self.assertEqual(len(content), size)

    def test_file_growth_transfer_budget_and_worker_budget_do_not_affect_other_routes(self):
        lease = self.create()
        row = self.manager._leases[lease['id']]
        row.transferred = MAX_TRANSFER
        self.assertEqual(404, self.request(lease['url']).status)
        for _ in range(MAX_WORKERS):
            self.assertTrue(self.manager._slots.acquire(blocking=False))
        try:
            self.assertEqual(503, self.request(lease['url']).status)
            self.assertFalse(self.manager.serve(Handler('/api/preview')))
        finally:
            for _ in range(MAX_WORKERS):
                self.manager._slots.release()
        self.entry.write_bytes(b'x')
        with self.assertRaises(PreviewUnavailable):
            self.manager.create(self.entry, self.root.parent, origin='https://outside.invalid', authorize=lambda *_: True)

    def test_options_are_narrow_and_mutating_methods_never_serve_files(self):
        lease = self.create()
        options = self.request(lease['url'], method='OPTIONS')
        self.assertEqual(204, options.status)
        self.assertEqual('GET, HEAD, OPTIONS', options.response_headers['Access-Control-Allow-Methods'])
        for method in ('POST', 'PUT', 'DELETE'):
            with self.subTest(method=method):
                self.assertEqual(405, self.request(lease['url'], method=method).status)

    def test_nested_site_root_urls_use_entry_folder_and_relative_resources_still_work(self):
        site = self.root / 'site'
        site.mkdir()
        self.entry = site / 'index.html'
        self.entry.write_text('<link href="/assets/main.css"><img srcset="/a.png 1x, /b.png 2x">', encoding='utf-8')
        lease = self.create()
        response = self.request(lease['url'])
        prefix = '/project-preview/' + lease['id'] + '/site/'
        self.assertIn(('href="' + prefix + 'assets/main.css"').encode(), response.wfile.getvalue())
        self.assertIn(('srcset="' + prefix + 'a.png 1x, ' + prefix + 'b.png 2x"').encode(), response.wfile.getvalue())
        (site / 'main.js').write_text('import "../shared.mjs";', encoding='utf-8')
        (self.root / 'shared.mjs').write_text('export const value=7;', encoding='utf-8')
        self.assertEqual(200, self.request(lease['url'].replace('index.html', 'main.js')).status)
        self.assertEqual(200, self.request(lease['url'].replace('site/index.html', 'shared.mjs')).status)

    def test_large_raster_and_aggregate_decode_budget_rejected_without_pixel_decode(self):
        lease = self.create()
        base = lease['url'].rsplit('/', 1)[0] + '/'
        (self.root / 'huge.png').write_bytes(png(4001, 4000))
        self.assertEqual(404, self.request(base + 'huge.png').status)
        (self.root / 'animated.png').write_bytes(png(animation=True))
        self.assertEqual(404, self.request(base + 'animated.png').status)
        for name in ('a.png', 'b.png', 'c.png'):
            (self.root / name).write_bytes(png(4000, 4000))
        self.assertEqual(200, self.request(base + 'a.png').status)
        self.assertEqual(200, self.request(base + 'a.png').status)
        self.assertEqual(200, self.request(base + 'b.png').status)
        self.assertEqual(404, self.request(base + 'c.png').status)
        self.assertEqual(32_000_000, self.manager._leases[lease['id']].image_pixels)
        self.assertEqual(2, len(self.manager._leases[lease['id']].images))

    def test_conditional_and_options_requests_share_finite_request_budget(self):
        lease = self.create()
        first = self.request(lease['url'])
        row = self.manager._leases[lease['id']]
        self.request(lease['url'], method='OPTIONS')
        self.assertEqual(2, row.requests)
        self.request(lease['url'], headers={'If-None-Match': first.response_headers['ETag']})
        self.assertEqual(3, row.requests)
        row.requests = MAX_REQUESTS
        self.assertEqual(404, self.request(lease['url'], method='OPTIONS').status)
        self.assertEqual(404, self.request(lease['url'], headers={'If-None-Match': first.response_headers['ETag']}).status)

    def test_minified_css_root_url_survives_chunk_boundary(self):
        original = b'a{color:red}' * (CHUNK // 6) + b'b{background:url(/images/a.png)}'
        original += b'c{color:blue}' * (CHUNK // 6)
        with io.BytesIO(original) as source:
            output, _ = _transformed(source, '.css', '/project-preview/example/')
            with output:
                result = output.read()
        self.assertIn(b'url(/project-preview/example/images/a.png)', result)

    def test_static_gif_works_but_multiframe_gif_does_not_accumulate_decoded_frames(self):
        # A finite 1px GIF: graphic metadata, one image descriptor and compressed block.
        gif = (b'GIF89a\x01\0\x01\0\x80\0\0' + b'\0\0\0\xff\xff\xff'
               + b',\0\0\0\0\x01\0\x01\0\0\x02\x02D\x01\0')
        lease = self.create()
        path = self.root / 'sample.gif'
        path.write_bytes(gif + b';')
        url = lease['url'].replace('index.html', 'sample.gif')
        self.assertEqual(200, self.request(url).status)
        path.write_bytes(gif + gif[19:] + b';')
        self.assertEqual(404, self.request(url).status)
        # A larger image descriptor cannot evade the logical-canvas budget.
        path.write_bytes(gif[:24] + b'\xa0\x0f\xa0\x0f' + gif[28:] + b';')
        self.assertEqual(404, self.request(url).status)

    def test_design_token_stylesheet_exception_does_not_expose_private_files(self):
        lease = self.create()
        base = lease['url'].rsplit('/', 1)[0] + '/'
        for name in ('tokens.css', 'design-tokens.css'):
            (self.root / name).write_text(':root{--color:green}', encoding='utf-8')
            self.assertEqual(200, self.request(base + name).status)
        for name in ('.tokens.css', 'tokens.json', 'auth.css', 'private/tokens.css',
                     'tokens/tokens.css', '.private/design-tokens.css'):
            path = self.root / name
            path.parent.mkdir(exist_ok=True)
            path.write_text('sensitive', encoding='utf-8')
            self.assertEqual(404, self.request(base + name).status)

    def test_closed_or_missing_html_has_useful_generic_guidance_without_private_details(self):
        lease = self.create()
        self.manager.close(lease['id'])
        response = self.request(lease['url'])
        self.assertEqual(410, response.status)
        message = response.wfile.getvalue().decode()
        self.assertIn('웹 미리보기를 표시할 수 없습니다', message)
        self.assertIn('기본 앱에서 열기', message)
        self.assertNotIn(lease['id'], message)
        self.assertNotIn(str(self.root), message)
        self.assertIn("default-src 'none'", response.response_headers['Content-Security-Policy'])

    def test_close_interrupts_long_transform_at_next_chunk_and_closes_temporary_output(self):
        calls = [0]

        def cancelled():
            calls[0] += 1
            return calls[0] > 2

        for suffix in ('.html', '.css'):
            calls[0] = 0
            with self.subTest(suffix=suffix), io.BytesIO(b'<p>x</p>' * CHUNK) as source:
                with self.assertRaises(PreviewUnavailable):
                    _transformed(source, suffix, '/project-preview/example/', cancelled=cancelled)
                self.assertLessEqual(source.tell(), 4 * CHUNK)

    def test_resource_write_timeout_is_bounded_and_original_socket_setting_restored(self):
        lease = self.create()
        handler = Handler(lease['url'])
        handler.connection = Mock()
        handler.connection.gettimeout.return_value = 23
        self.assertTrue(self.manager.serve(handler))
        self.assertEqual([((5,), {}), ((23,), {})], handler.connection.settimeout.call_args_list)

    def test_symlink_resource_and_symlink_root_are_rejected_when_supported(self):
        outside = self.root.parent / (self.root.name + '-outside.json')
        outside.write_text('outside', encoding='utf-8')
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        link = self.root / 'link.json'
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest('symlink permission unavailable')
        lease = self.create()
        self.assertEqual(404, self.request(lease['url'].replace('index.html', 'link.json')).status)


if __name__ == '__main__':
    unittest.main()
