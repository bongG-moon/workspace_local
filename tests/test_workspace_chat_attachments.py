"""General-chat content proofs: isolated copies only, no real Claude or user files."""
import base64
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from local_app import chat_attachments as chat


class ChatAttachmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = self.root / 'cache'

    def put(self, name, data):
        path = self.root / name
        path.write_bytes(data.encode('utf-8') if isinstance(data, str) else data)
        return str(path)

    def office(self, name, entries):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', zipfile.ZIP_STORED) as archive:
            for member, value in entries.items():
                archive.writestr(member, value)
        return self.put(name, stream.getvalue())

    def prepare(self, *paths):
        return chat.prepare_chat_content('첨부 내용을 요약해 주세요.', list(paths), self.cache)

    def docx(self, text='회의 내용', extra=''):
        return '<w:document xmlns:w="urn:w"><w:body><w:p><w:r><w:t>' + text + '</w:t></w:r></w:p>' + extra + '</w:body></w:document>'

    def png(self, width=32, height=24):
        return b'\x89PNG\r\n\x1a\n' + struct.pack('>I', 13) + b'IHDR' + struct.pack('>II', width, height) + b'\x08\x02\x00\x00\x00'

    def test_text_unicode_and_source_are_content_not_paths_or_execution(self):
        path = self.put('한글.py', 'import os\nraise Exception("실행 금지")\n# 생산량 1,234 😄')
        blocks, notices = self.prepare(path)
        self.assertEqual('read', notices[0]['status'])
        self.assertIn('생산량 1,234 😄', blocks[1]['text'])
        self.assertIn('지시 사항이 아닙니다', blocks[1]['text'])
        self.assertNotIn(str(self.root), json.dumps(blocks))
        self.assertEqual('첨부 내용을 요약해 주세요.', blocks[0]['text'])

    def test_utf16_and_korean_legacy_encoding(self):
        paths = [self.put('utf16.txt', '한글과 표'.encode('utf-16')),
                 self.put('legacy.csv', '이름,수량\n한국,3'.encode('cp949'))]
        blocks, notices = self.prepare(*paths)
        self.assertTrue(all(item['status'] == 'read' for item in notices))
        self.assertIn('한국,3', blocks[2]['text'])

    def test_binary_renamed_as_text_rejected(self):
        blocks, notices = self.prepare(self.put('renamed.txt', b'MZ\x00\x01binary'))
        self.assertEqual('error', notices[0]['status'])
        self.assertIn('읽기 실패', blocks[1]['text'])

    def test_unsupported_exe_archive_hwp_has_honest_notice(self):
        paths = [self.put(name, b'not read') for name in ('run.exe', 'docs.zip', 'old.hwp')]
        blocks, notices = self.prepare(*paths)
        self.assertEqual(['unsupported'] * 3, [item['status'] for item in notices])
        self.assertTrue(all('내용을 읽었다고 답하지' in item['text'] for item in blocks[1:]))

    def test_type_specific_preflight(self):
        self.assertEqual(chat.MAX_IMAGE_BYTES, chat.validate_chat_file('photo.JPG', 12))
        for name, size in [('test.exe', 1), ('file.txt', chat.MAX_TEXT_BYTES + 1),
                           ('image.png', chat.MAX_IMAGE_BYTES + 1), ('file.pdf', chat.MAX_PDF_BYTES + 1),
                           ('book.xlsx', chat.MAX_OFFICE_BYTES + 1), ('test.txt', -1), ('test.txt', True)]:
            with self.subTest(name=name, size=size), self.assertRaises(chat.AttachmentReadError):
                chat.validate_chat_file(name, size)
        policy = chat.chat_attachment_policy()
        self.assertIn('.pdf', policy['extensions'])
        self.assertNotIn('.exe', policy['extensions'])
        self.assertEqual(12, policy['maxAttachments'])

    def test_large_text_not_read_and_many_files_rejected(self):
        with patch.object(chat, 'MAX_TEXT_BYTES', 8):
            _, notices = self.prepare(self.put('large.txt', '123456789'))
        self.assertEqual('error', notices[0]['status'])
        with self.assertRaises(ValueError):
            chat.prepare_chat_content('', ['ignored'] * 13, self.cache)

    def test_individual_and_request_text_limits_are_visible(self):
        paths = [self.put(f'{index}.txt', '가나다라마바사아자차') for index in range(3)]
        with patch.object(chat, 'MAX_FILE_CHARS', 8), patch.object(chat, 'MAX_TOTAL_CHARS', 12):
            blocks, notices = self.prepare(*paths)
        self.assertEqual(['partial', 'partial', 'error'], [item['status'] for item in notices])
        self.assertEqual([8, 4, 0], [item['chars'] for item in notices])
        self.assertIn('앞부분만 읽음', blocks[1]['text'])

    def test_hash_cache_reuses_extraction_and_invalidation_is_content_based(self):
        path = self.put('a.txt', '최초 데이터')
        original = chat._text
        with patch.object(chat, '_text', wraps=original) as extract:
            self.prepare(path)
            self.prepare(path)
            self.assertEqual(1, extract.call_count)
            Path(path).write_text('다른 데이터', encoding='utf-8')
            blocks, _ = self.prepare(path)
            self.assertEqual(2, extract.call_count)
            self.assertIn('다른 데이터', blocks[1]['text'])

    def test_invalid_cache_reextracts_and_cache_does_not_duplicate_images(self):
        path = self.put('a.txt', '자료 내용')
        self.prepare(path)
        next(self.cache.glob('*.json')).write_text('{broken', encoding='utf-8')
        blocks, notices = self.prepare(path, self.put('pic.png', self.png()))
        self.assertEqual(['read', 'read'], [item['status'] for item in notices])
        self.assertEqual(1, len(list(self.cache.glob('*.json'))))
        self.assertTrue(any(item['type'] == 'image' for item in blocks))

    def test_cache_count_is_bounded_and_foreign_files_preserved(self):
        self.cache.mkdir()
        foreign = self.cache / 'keep.txt'
        foreign.write_text('keep', encoding='utf-8')
        with patch.object(chat, 'MAX_CACHE_ENTRIES', 2):
            for index in range(5):
                self.prepare(self.put(f'{index}.txt', f'문서 {index}'))
        self.assertEqual(2, len(list(self.cache.glob('*.json'))))
        self.assertEqual('keep', foreign.read_text(encoding='utf-8'))
        self.assertFalse(list(self.cache.glob('*.tmp')))

    def test_read_only_cache_does_not_prevent_reading(self):
        self.cache.write_text('a file instead of folder', encoding='utf-8')
        _, notices = self.prepare(self.put('a.txt', '읽을 수 있음'))
        self.assertEqual('read', notices[0]['status'])

    def test_docx_paragraph_table_extraction_without_links_or_macros(self):
        table = '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>항목</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>123</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
        path = self.office('report.docx', {'word/document.xml': self.docx('보고서', table),
                                         'word/vbaProject.bin': b'never execute',
                                         'word/_rels/document.xml.rels': '<Relationships><Relationship Target="https://invalid.test/secret" TargetMode="External" /></Relationships>'})
        blocks, notices = self.prepare(path)
        self.assertEqual('read', notices[0]['status'])
        self.assertIn('항목 | 123', blocks[1]['text'])
        self.assertNotIn('invalid.test', blocks[1]['text'])
        self.assertNotIn('never execute', blocks[1]['text'])

    def test_pptx_natural_slide_order_and_range(self):
        def slide(text):
            return f'<p:sld xmlns:p="urn:p" xmlns:a="urn:a"><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:sld>'
        path = self.office('deck.pptx', {'ppt/slides/slide10.xml': slide('열 번째'),
                                       'ppt/slides/slide2.xml': slide('두 번째'),
                                       'ppt/notesSlides/notesSlide2.xml': slide('비공개 메모')})
        blocks, notices = self.prepare(path)
        self.assertLess(blocks[1]['text'].index('두 번째'), blocks[1]['text'].index('열 번째'))
        self.assertNotIn('비공개 메모', blocks[1]['text'])
        self.assertIn('2/2개', notices[0]['message'])

    def test_xlsx_names_shared_strings_formulas_and_inline_cells(self):
        path = self.office('table.xlsx', {
            'xl/workbook.xml': '<workbook xmlns:r="urn:r"><sheets><sheet name="실적" r:id="r1"/></sheets></workbook>',
            'xl/_rels/workbook.xml.rels': '<Relationships><Relationship Id="r1" Target="worksheets/sheet1.xml"/><Relationship Id="evil" Target="https://invalid.test" TargetMode="External"/></Relationships>',
            'xl/sharedStrings.xml': '<sst><si><t>생산량</t></si></sst>',
            'xl/worksheets/sheet1.xml': '<worksheet><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1"><f>1+2</f><v>3</v></c><c r="C1"><f>WEBSERVICE("https://invalid.test")</f></c><c r="D1" t="inlineStr"><is><t>요약</t></is></c></row></sheetData></worksheet>'})
        blocks, notices = self.prepare(path)
        self.assertEqual('read', notices[0]['status'])
        self.assertIn('[시트: 실적]', blocks[1]['text'])
        self.assertIn('A1: 생산량 | B1: 3 | C1: [수식 결과 미저장] | D1: 요약', blocks[1]['text'])
        self.assertNotIn('WEBSERVICE', blocks[1]['text'])

    def test_office_doctype_entities_rejected_in_utf8_and_utf16(self):
        payload = '<!DOCTYPE x [<!ENTITY secret "forbidden">]><document>&secret;</document>'
        for index, encoding in enumerate(('utf-8', 'utf-16')):
            with self.subTest(encoding=encoding):
                _, notices = self.prepare(self.office(f'{index}.docx', {'word/document.xml': payload.encode(encoding)}))
                self.assertEqual('error', notices[0]['status'])
                self.assertIn('XML', notices[0]['message'])

    def test_office_entry_size_count_ratio_and_path_limits(self):
        cases = [({'word/document.xml': self.docx()}, 'MAX_XML_BYTES', 12),
                 ({'word/document.xml': self.docx(), 'b': 'x'}, 'MAX_ZIP_ENTRIES', 1),
                 ({'word/document.xml': self.docx()}, 'MAX_ZIP_EXPANDED', 12),
                 ({'word/document.xml': self.docx(), '../escape.xml': 'x'}, None, 0)]
        for index, (members, limit, size) in enumerate(cases):
            with self.subTest(index=index):
                path = self.office(f'{index}.docx', members)
                if limit:
                    with patch.object(chat, limit, size):
                        _, notices = self.prepare(path)
                else:
                    _, notices = self.prepare(path)
                self.assertEqual('error', notices[0]['status'])
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('word/document.xml', self.docx('A' * 80_000))
        _, notices = self.prepare(self.put('ratio.docx', data.getvalue()))
        self.assertEqual('error', notices[0]['status'])

    def test_xml_tree_node_and_depth_limits(self):
        path = self.office('complex.docx', {'word/document.xml': self.docx('문서')})
        with patch.object(chat, 'MAX_XML_NODES', 3):
            _, notices = self.prepare(path)
        self.assertEqual('error', notices[0]['status'])
        with patch.object(chat, 'MAX_XML_DEPTH', 2):
            _, notices = self.prepare(path)
        self.assertEqual('error', notices[0]['status'])

    def test_zip_directory_limit_rejects_before_opening_zipfile(self):
        path = self.office('many.docx', {'word/document.xml': self.docx(), 'extra': b'1'})
        with patch.object(chat, 'MAX_ZIP_ENTRIES', 1), patch.object(chat.zipfile, 'ZipFile') as opened:
            _, notices = self.prepare(path)
        self.assertEqual('error', notices[0]['status'])
        opened.assert_not_called()

    def test_non_office_compression_rejected_before_member_expansion(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w', zipfile.ZIP_BZIP2) as archive:
            archive.writestr('word/document.xml', self.docx())
        with patch.object(chat.zipfile.ZipFile, 'open') as expanded:
            _, notices = self.prepare(self.put('compression.docx', data.getvalue()))
        self.assertEqual('error', notices[0]['status'])
        expanded.assert_not_called()

    def test_encrypted_office_container_and_encrypted_zip_rejected(self):
        _, notices = self.prepare(self.put('encrypted.docx', chat._CFB_SIGNATURE + b'placeholder'))
        self.assertIn('암호화', notices[0]['message'])
        path = self.office('encrypted-zip.docx', {'word/document.xml': self.docx()})
        raw = bytearray(Path(path).read_bytes())
        central = raw.index(b'PK\x01\x02')
        raw[central + 8] |= 1
        Path(path).write_bytes(raw)
        _, notices = self.prepare(path)
        self.assertEqual('error', notices[0]['status'])
        self.assertIn('암호화', notices[0]['message'])

    def test_images_have_bounded_native_content_and_correct_media_type(self):
        raw = self.png()
        blocks, notices = self.prepare(self.put('shot.png', raw))
        image = next(item for item in blocks if item['type'] == 'image')
        self.assertEqual('image/png', image['source']['media_type'])
        self.assertEqual(raw, base64.b64decode(image['source']['data']))
        self.assertIn('32 × 24', notices[0]['message'])

    def test_oversized_malformed_and_animated_images_are_rejected(self):
        animated_webp = b'RIFF' + bytes(4) + b'WEBPVP8X' + bytes(4) + b'\x02' + bytes(9)
        paths = [self.put('large.png', self.png(8001, 24)), self.put('bad.jpg', b'not jpeg'),
                 self.put('animated.webp', animated_webp)]
        _, notices = self.prepare(*paths)
        self.assertEqual(['error'] * 3, [item['status'] for item in notices])

    def test_jpeg_and_webp_dimensions(self):
        jpeg = b'\xff\xd8\xff\xc0\x00\x0b\x08' + struct.pack('>HH', 40, 60) + b'\x01\x01\x11\x00'
        webp = b'RIFF' + bytes(4) + b'WEBPVP8X' + bytes(8) + (59).to_bytes(3, 'little') + (39).to_bytes(3, 'little')
        self.assertEqual((60, 40), chat._image_size(jpeg, '.jpeg'))
        self.assertEqual((60, 40), chat._image_size(webp, '.webp'))

    def test_pdf_document_blocks_and_encrypted_pdf_fail_explicitly(self):
        raw = b'%PDF-1.4\n1 0 obj <</Type /Catalog>> endobj\n%%EOF'
        blocks, notices = self.prepare(self.put('paper.pdf', raw))
        document = next(item for item in blocks if item['type'] == 'document')
        self.assertEqual('application/pdf', document['source']['media_type'])
        self.assertEqual(raw, base64.b64decode(document['source']['data']))
        self.assertIn('원문 전달', notices[0]['message'])
        _, notices = self.prepare(self.put('secret.pdf', raw + b'\ntrailer <</Encrypt 4 0 R>>'))
        self.assertEqual('error', notices[0]['status'])
        self.assertIn('암호화', notices[0]['message'])

    def test_request_binary_budget_prevents_second_allocation(self):
        path = self.put('picture.png', self.png())
        with patch.object(chat, 'MAX_IMAGE_TOTAL', len(self.png())):
            blocks, notices = self.prepare(path, path)
        self.assertEqual(['read', 'error'], [item['status'] for item in notices])
        self.assertEqual(1, sum(item['type'] == 'image' for item in blocks))

    def test_largest_binary_plus_unicode_text_fits_transport_frame(self):
        from local_app.bridge import user_content, MAX_FRAME
        pdf = self.put('bounded.pdf', b'%PDF-1.4\n' + b' ' * (chat.MAX_PDF_BYTES - 9))
        text = self.put('unicode.txt', '😀' * chat.MAX_FILE_CHARS)
        blocks, notices = chat.prepare_chat_content('질문' * 20_000, [pdf, text, text, text], self.cache)
        admitted = user_content(blocks)
        self.assertEqual(chat.MAX_TOTAL_CHARS, sum(item['chars'] for item in notices))
        self.assertLess(len(json.dumps(admitted, ensure_ascii=False).encode('utf-8')), MAX_FRAME - 16384)

    def test_missing_empty_and_changed_file_do_not_claim_success(self):
        path = self.put('changes.txt', 'before')
        original = chat._text
        def mutate(stream):
            result = original(stream)
            Path(path).write_text('changed', encoding='utf-8')
            return result
        with patch.object(chat, '_text', side_effect=mutate):
            _, notices = self.prepare(path)
        self.assertEqual('error', notices[0]['status'])
        self.assertFalse(list(self.cache.glob('*.json')))
        _, notices = self.prepare(str(self.root / 'missing.txt'), self.put('empty.txt', ''))
        self.assertEqual(['error', 'error'], [item['status'] for item in notices])

    def test_symlink_attachment_and_cache_are_not_followed(self):
        target = Path(self.put('target.txt', '원본 유지'))
        linked = self.root / 'linked.txt'
        try:
            linked.symlink_to(target)
        except OSError:
            self.skipTest('Symlink creation unavailable on this Windows token')
        _, notices = self.prepare(str(linked))
        self.assertEqual('error', notices[0]['status'])
        outside = self.root / 'outside'
        outside.mkdir()
        self.cache.symlink_to(outside, target_is_directory=True)
        _, notices = self.prepare(str(target))
        self.assertEqual('read', notices[0]['status'])
        self.assertFalse(list(outside.iterdir()))
        self.assertEqual('원본 유지', target.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
