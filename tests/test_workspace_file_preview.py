"""Bounded rich previews keep task authorization and execution boundaries."""
import base64
import io
import json
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import zlib

from local_app.artifacts import DOCUMENT_TYPES, snapshot
from local_app.file_preview import (build_preview, image_dimensions, MAX_CODE_CHARS,
    MAX_IMAGE_BYTES, MAX_IMAGE_PIXELS, MAX_TEXT_BYTES, source_preview_allowed)
from local_app.server import LocalApp, Server


def png(width=1, height=1, *, animation=False):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    header = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
    # Large dimensions exercise the header budget without allocating pixels.
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header)
            + (chunk(b'acTL', struct.pack('>II', 1, 0)) if animation else b'')
            + chunk(b'IDAT', zlib.compress(b'\0\xff\0\0')) + chunk(b'IEND', b''))


def webp(width=1, height=1, *, animation=False):
    dims = ((width - 1) | ((height - 1) << 14)).to_bytes(4, 'little')
    data = b'\x2f' + dims
    chunks = b'VP8L' + struct.pack('<I', len(data)) + data + b'\0'
    if animation:
        chunks += b'ANIM' + struct.pack('<I', 0)
    return b'RIFF' + struct.pack('<I', len(chunks) + 4) + b'WEBP' + chunks


class PreviewParsingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def preview(self, name, content):
        path = self.root / name
        path.write_bytes(content.encode('utf-8') if isinstance(content, str) else content)
        return build_preview(path)

    def test_csv_quoted_multiline_bom_korean_and_literal_formula(self):
        result = self.preview('표.csv', '\ufeff이름,설명,계산\r\n홍길동,"첫째\n둘째, \'인용\'",=1+1\r\n')
        self.assertEqual('table', result['kind'])
        self.assertEqual(['이름', '설명', '계산'], result['columns'])
        self.assertEqual([['홍길동', "첫째\n둘째, '인용'", '=1+1']], result['rows'])
        self.assertEqual(1, result['rowCount'])
        self.assertFalse(result['truncated'])

    def test_tsv_cp949_utf16_and_escaped_quotes(self):
        for encoding in ('cp949', 'utf-16'):
            with self.subTest(encoding=encoding):
                result = self.preview('표.tsv', '이름\t설명\n가\t"인용 ""둘"""\n'.encode(encoding))
                self.assertEqual([['가', '인용 "둘"']], result['rows'])

    def test_table_limits_count_all_rows_and_columns_without_retaining_them(self):
        header = ','.join('c' + str(i) for i in range(31))
        body = '\n'.join(','.join(str(i) for i in range(31)) for _ in range(103))
        result = self.preview('table.csv', header + '\n' + body)
        self.assertEqual(100, len(result['rows']))
        self.assertEqual(30, len(result['columns']))
        self.assertTrue(all(len(row) == 30 for row in result['rows']))
        self.assertEqual((103, 31), (result['rowCount'], result['columnCount']))
        self.assertTrue(result['rowsTruncated'])
        self.assertTrue(result['columnsTruncated'])
        self.assertTrue(result['truncated'])

    def test_exact_table_limit_not_truncated_and_ragged_rows_padded(self):
        result = self.preview('table.csv', 'a,b\n' + '\n'.join('1' for _ in range(100)))
        self.assertFalse(result['truncated'])
        self.assertEqual(['1', ''], result['rows'][0])
        result = self.preview('table.csv', 'a\n1,2,3')
        self.assertEqual(['a', '열 2', '열 3'], result['columns'])

    def test_empty_header_only_and_bad_csv_are_honest(self):
        result = self.preview('empty.csv', '')
        self.assertEqual(([], [], 0), (result['columns'], result['rows'], result['rowCount']))
        result = self.preview('header.csv', 'a,b\n')
        self.assertEqual((['a', 'b'], []), (result['columns'], result['rows']))
        self.assertEqual('external', self.preview('bad.csv', 'a,b\n"unterminated')['kind'])

    def test_large_csv_binary_or_bad_encoding_never_leaks_partial_table(self):
        for raw in (b'a' * (MAX_TEXT_BYTES + 1), b'a,b\n\0,1', b'\xff\x00\xfe'):
            with self.subTest(size=len(raw)):
                result = self.preview('table.csv', raw)
                self.assertEqual('external', result['kind'])
                self.assertNotIn('rows', result)

    def test_code_is_inert_language_labeled_bounded_and_not_executed(self):
        code = 'print("<script>출력</script>")\n'
        result = self.preview('code.py', code)
        self.assertEqual(('code', 'python', code, False),
                         (result['kind'], result['language'], result['text'], result['truncated']))
        result = self.preview('script.ps1', 'x' * (MAX_CODE_CHARS + 1))
        self.assertEqual(('powershell', MAX_CODE_CHARS, True),
                         (result['language'], len(result['text']), result['truncated']))
        self.assertNotIn('.py', DOCUMENT_TYPES)
        self.assertNotIn('.ps1', DOCUMENT_TYPES)

    def test_large_multibyte_source_decodes_partial_tail_without_replacement(self):
        result = self.preview('code.py', ('한글\n' * (MAX_TEXT_BYTES // 2)).encode())
        self.assertEqual('code', result['kind'])
        self.assertEqual(MAX_CODE_CHARS, len(result['text']))
        self.assertNotIn('\ufffd', result['text'])
        self.assertTrue(result['truncated'])

    def test_read_size_is_bounded_even_when_file_changes(self):
        path = self.root / 'code.py'
        stream = io.BytesIO(b'print(1)')
        with patch.object(Path, 'open', return_value=stream), patch.object(stream, 'read', wraps=stream.read) as read:
            result = build_preview(path)
            read.assert_called_once_with(MAX_TEXT_BYTES + 1)
        self.assertEqual('code', result['kind'])

    def test_png_inline_uses_header_size_and_exact_encoded_content(self):
        raw = png()
        result = self.preview('small.png', raw)
        self.assertEqual(('image', 1, 1), (result['kind'], result['width'], result['height']))
        self.assertEqual(raw, base64.b64decode(result['data'].split(',', 1)[1]))

    def test_real_png_over_old_one_megabyte_limit_is_previewed(self):
        raw = png()
        # A legal ancillary chunk grows the encoded file without decoded cost.
        data = b'Comment\0' + b'x' * (1024 * 1024)
        extra = struct.pack('>I', len(data)) + b'tEXt' + data + struct.pack('>I', zlib.crc32(b'tEXt' + data) & 0xffffffff)
        result = self.preview('large.png', raw[:-12] + extra + raw[-12:])
        self.assertEqual('image', result['kind'])

    def test_oversized_encoded_image_or_decode_dimensions_are_not_inline(self):
        for raw in (b'x' * (MAX_IMAGE_BYTES + 1), png(65535, 65535), png(0, 1)):
            result = self.preview('large.png', raw)
            self.assertEqual('external', result['kind'])
            self.assertNotIn('data', result)
        self.assertEqual((4000, 4000), image_dimensions(png(4000, 4000), '.png'))
        self.assertEqual(16_000_000, MAX_IMAGE_PIXELS)

    def test_mismatched_truncated_checksum_and_animated_images_rejected(self):
        for name, raw in [('wrong.jpg', png()), ('bad.png', png()[:-1]),
                          ('bad.png', png()[:45] + b'broken' + png()[45:]),
                          ('animated.png', png(animation=True)),
                          ('animated.webp', webp(animation=True)), ('bad.webp', webp()[:-1])]:
            with self.subTest(name=name, length=len(raw)):
                self.assertEqual('external', self.preview(name, raw)['kind'])

    def test_webp_lossless_and_jpeg_supported_headers_have_dimensions(self):
        self.assertEqual((12, 24), image_dimensions(webp(12, 24), '.webp'))
        # Header-only fixture tests the finite parser, not compressed raster decoding.
        raw = (b'\xff\xd8\xff\xc0\x00\x0b\x08\x00\x18\x00\x0c\x01\x01\x11\x00'
               b'\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00\x00\xff\xd9')
        self.assertEqual((12, 24), image_dimensions(raw, '.jpg'))

    def test_html_and_htm_have_isolated_preview_and_original_source(self):
        samples = (
            ('report.html', '<body data-style="minimalism"><main class="report-main">본문<script>alert(1)</script></main></body>'),
            ('other.html', '<!doctype html><h1>일반 문서</h1><script>alert(1)</script>'),
            ('other.HTM', "<BODY><main class='extra'>일반 문서</main></BODY>"),
        )
        for name, source in samples:
            with self.subTest(name=name):
                result = self.preview(name, source)
                self.assertEqual(('html', 'html', source, False),
                                 (result['kind'], result['language'], result['text'], result['truncated']))
                self.assertNotIn('<script', result['html'])
                self.assertEqual(source.encode(), (self.root / name).read_bytes())

    def test_html_source_limit_does_not_truncate_static_preview(self):
        source = '<p>' + '가' * MAX_CODE_CHARS + '</p><h1>마지막 제목</h1>'
        result = self.preview('long.html', source)
        self.assertEqual('html', result['kind'])
        self.assertEqual(source[:MAX_CODE_CHARS], result['text'])
        self.assertTrue(result['truncated'])
        self.assertIn('<h1>마지막 제목</h1>', result['html'])
        self.assertFalse(self.preview('exact.html', 'a' * MAX_CODE_CHARS)['truncated'])

    def test_html_retains_file_byte_and_encoding_limits(self):
        self.assertEqual('html', self.preview('exact.html', b'a' * MAX_TEXT_BYTES)['kind'])
        for raw in (b'a' * (MAX_TEXT_BYTES + 1), b'\xff\x00\xfe'):
            result = self.preview('invalid.html', raw)
            self.assertEqual('external', result['kind'])
            self.assertNotIn('html', result)
            self.assertNotIn('text', result)
        for encoding in ('utf-8-sig', 'cp949', 'utf-16'):
            with self.subTest(encoding=encoding):
                raw = '<h1>문서</h1>'.encode(encoding)
                result = self.preview('encoded.htm', raw)
                self.assertEqual(('html', '<h1>문서</h1>'), (result['kind'], result['text']))
                self.assertEqual(raw, (self.root / 'encoded.htm').read_bytes())

    def test_discovery_adds_source_but_excludes_hidden_and_secret_paths(self):
        for name in ('result.py', 'table.csv', 'credentials.json', '.config.json'):
            (self.root / name).write_text('{}')
        for folder in ('.claude', 'secrets'):
            (self.root / folder).mkdir()
            (self.root / folder / 'settings.json').write_text('{}')
        names = {Path(path).name for path in snapshot(self.root).files}
        self.assertEqual({'result.py', 'table.csv'}, names)
        self.assertFalse(source_preview_allowed(self.root / '.claude/settings.json', self.root))
        self.assertFalse(source_preview_allowed(self.root / 'credentials.json', self.root))


class PreviewRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.work = self.root / '업무'; self.work.mkdir()
        self.app = LocalApp(self.root / 'state', command=['never-start'], managed_workspace_root=self.root / 'managed')
        self.server = Server(self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.sid = self.app.create(str(self.work), True)['id']

    def tearDown(self):
        self.app.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path, *, token=True):
        route = '/api/preview?' + urlencode({'id': self.sid, 'path': str(path)})
        request = Request(self.server.origin + route, headers={'Authorization': 'Bearer ' + self.app.token} if token else {})
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            return exc.code, json.load(exc)

    def test_auth_scope_and_direct_attachment_authorization_still_required(self):
        outside = self.root / 'outside.py'; outside.write_text('print(1)')
        self.assertEqual(403, self.request(outside, token=False)[0])
        self.assertEqual(400, self.request(outside)[0])
        self.app.get(self.sid)['attachments'] = [str(outside)]
        status, result = self.request(outside)
        self.assertEqual((200, 'code'), (status, result['kind']))

    def test_traversal_redirect_and_new_hidden_source_are_rejected(self):
        code = self.work / 'result.py'; code.write_text('print(1)')
        self.assertEqual(400, self.request(self.work / '../업무/result.py')[0])
        with patch('local_app.server.workspace_path', side_effect=ValueError('replaced junction')):
            self.assertEqual(400, self.request(code)[0])
        private = self.work / '.claude'; private.mkdir()
        secret = private / 'settings.json'; secret.write_text('{"private":"value"}')
        status, result = self.request(secret)
        self.assertEqual(400, status)
        self.assertNotIn('value', json.dumps(result))

    def test_success_only_marks_preview_and_does_not_execute_source(self):
        code = self.work / 'result.ps1'; code.write_text('Write-Output 1')
        invalid = self.work / 'invalid.png'; invalid.write_bytes(b'not an image')
        observation = {'previewed': set()}
        self.app.get(self.sid)['observation'] = observation
        with patch('local_app.external_apps.os.startfile', create=True) as start:
            self.assertEqual('code', self.request(code)[1]['kind'])
            self.assertEqual('external', self.request(invalid)[1]['kind'])
            start.assert_not_called()
        self.assertEqual({str(code)}, observation['previewed'])

    def test_html_route_keeps_authorization_original_bytes_and_static_source_pair(self):
        source = '<!doctype html><h1>별도 첨부</h1><script>fetch("/api/quit")</script>'
        outside = self.root / 'outside.html'; outside.write_text(source, encoding='utf-8')
        self.assertEqual(403, self.request(outside, token=False)[0])
        self.assertEqual(400, self.request(outside)[0])
        self.app.get(self.sid)['attachments'] = [str(outside)]
        with patch('local_app.external_apps.os.startfile', create=True) as opened:
            status, result = self.request(outside)
            opened.assert_not_called()
        self.assertEqual((200, 'html', 'html', source, False),
                         (status, result['kind'], result['language'], result['text'], result['truncated']))
        self.assertIn('<h1>별도 첨부</h1>', result['html'])
        self.assertNotIn('<script', result['html'])
        self.assertEqual(source.encode(), outside.read_bytes())

    def test_new_source_types_stay_blocked_from_external_execution(self):
        from local_app.external_apps import open_document
        with patch('local_app.external_apps.os.startfile', create=True) as start:
            for extension in ('.py', '.ps1', '.sh', '.js', '.ts'):
                path = self.work / ('source' + extension); path.write_text('fixture')
                self.assertEqual(200, self.request(path)[0])
                with self.assertRaises(ValueError):
                    open_document(path)
            start.assert_not_called()


if __name__ == '__main__':
    unittest.main()
