"""Real XLS content, admission and bundled-library tests; no Excel or real CLI."""
import hashlib
import io
import json
from pathlib import Path
import re
import struct
import tempfile
import unittest
from unittest.mock import patch

from local_app import chat_attachments as chat, legacy_excel
from local_app import artifacts, attachments


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests/fixtures/chat-legacy-excel.xls'


class XlsAttachmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def prepare(self, data=None):
        path = self.root / '월간 실적.XLS'
        path.write_bytes(FIXTURE.read_bytes() if data is None else data)
        return chat.prepare_chat_content('셀을 요약해 주세요', [str(path)], self.root / 'cache')

    def test_real_compound_xls_values_dates_and_sheets_without_running_code(self):
        with patch('subprocess.Popen', side_effect=AssertionError('No workbook execution')):
            blocks, notices = self.prepare()
        self.assertEqual('read', notices[0]['status'])
        content = blocks[1]['text']
        for value in ('[시트: 월간 실적]', 'A1: 생산량 | B1: 1234', 'B2: 0.75',
                      'A3: TRUE', '2026-11-02 09:00:00', '[시트: 다음 시트]', '검증 완료'):
            self.assertIn(value, content)
        self.assertNotIn('1+2', content)  # Formula source is never executed or sent.
        self.assertIn('매크로·수식·외부 링크 실행 안 함', notices[0]['message'])

    def test_text_budget_is_partial_and_extract_cache_reused(self):
        with patch.object(chat, 'MAX_FILE_CHARS', 34), patch.object(legacy_excel, 'read_values', wraps=legacy_excel.read_values) as read:
            first = self.prepare()
            second = self.prepare()
        self.assertEqual('partial', first[1][0]['status'])
        self.assertLessEqual(first[1][0]['chars'], 34)
        self.assertEqual(first, second)
        self.assertEqual(1, read.call_count)

    def test_wrong_type_truncated_and_encrypted_xls_are_honest_errors(self):
        # FILEPASS after a valid BIFF8 BOF record, before any cell data.
        encrypted = struct.pack('<HHHH', 0x0809, 4, 0x0600, 0x0005) + struct.pack('<HHH', 0x002F, 2, 1)
        for raw, expected in ((b'MZ\0binary', '실제 형식'), (FIXTURE.read_bytes()[:100], 'XLS'),
                              (encrypted, '암호화')):
            with self.subTest(expected=expected):
                blocks, notices = self.prepare(raw)
                self.assertEqual('error', notices[0]['status'])
                self.assertIn(expected, notices[0]['message'])
                self.assertIn('내용을 읽었다고 답하지', blocks[1]['text'])

    def test_internal_string_or_record_excess_stops_before_workbook_allocation(self):
        from local_app._vendor import xlrd
        bof = struct.pack('<HHHH', 0x0809, 4, 0x0600, 0x0005)
        raw = bof + struct.pack('<HHII', 0x00FC, 8, 0xFFFFFFFF, 0xFFFFFFFF)
        with patch.object(xlrd, 'open_workbook') as opened:
            _, notices = self.prepare(raw)
        self.assertIn('읽기 한도', notices[0]['message'])
        opened.assert_not_called()
        with patch.object(legacy_excel, 'MAX_RECORDS', 1), patch.object(xlrd, 'open_workbook') as opened:
            _, notices = self.prepare()
        self.assertEqual('error', notices[0]['status'])
        opened.assert_not_called()

    def test_malformed_zero_padding_stops_before_workbook_parser(self):
        from local_app._vendor import xlrd
        bof = struct.pack('<HHHH', 0x0809, 4, 0x0600, 0x0005)
        with patch.object(xlrd, 'open_workbook') as opened:
            _, notices = self.prepare(bof + b'\0' * (1024 * 1024) + b'\x01')
        self.assertEqual('error', notices[0]['status'])
        self.assertIn('손상', notices[0]['message'])
        opened.assert_not_called()

    def test_sparse_wide_rows_are_bounded_before_xlrd_allocates_cells(self):
        from local_app._vendor import xlrd
        bof = struct.pack('<HHHHH', 0x0209, 6, 0, 0x10, 0)
        # Although only one cell holds data, xlrd materializes preceding cells
        # through column IV. A compact BIFF file must not bypass memory bounds.
        label = lambda row: struct.pack('<HHHHHHc', 0x0204, 9, row, 255, 0, 1, b'x')
        raw = bof + b''.join(label(row) for row in range(10_000)) + struct.pack('<HH', 0x000A, 0)
        with patch.object(xlrd, 'open_workbook') as opened:
            _, notices = self.prepare(raw)
        self.assertEqual('error', notices[0]['status'])
        self.assertIn('읽기 한도', notices[0]['message'])
        opened.assert_not_called()

    def test_bad_compound_sector_header_returns_attachment_error_before_parser(self):
        from local_app._vendor import xlrd
        for shift in (0, 1, 2, 15, 65535):
            with self.subTest(shift=shift):
                raw = bytearray(FIXTURE.read_bytes())
                struct.pack_into('<HH', raw, 30, shift, 6)
                with patch.object(xlrd.compdoc, 'CompDoc') as opened:
                    blocks, notices = self.prepare(raw)
                self.assertEqual('error', notices[0]['status'])
                self.assertIn('저장 구조', notices[0]['message'])
                self.assertIn('내용을 읽었다고 답하지', blocks[1]['text'])
                opened.assert_not_called()

    def test_bad_xls_does_not_abort_other_files_in_same_request(self):
        raw = bytearray(FIXTURE.read_bytes())
        struct.pack_into('<HH', raw, 30, 0, 0)
        broken = self.root / 'broken.xls'
        broken.write_bytes(raw)
        good = self.root / 'valid.xls'
        good.write_bytes(FIXTURE.read_bytes())
        note = self.root / 'note.txt'
        note.write_text('나머지 첨부는 계속 읽음', encoding='utf-8')
        blocks, notices = chat.prepare_chat_content('요약', [str(broken), str(good), str(note)], self.root / 'cache')
        self.assertEqual(['error', 'read', 'read'], [row['status'] for row in notices])
        self.assertIn('생산량', blocks[2]['text'])
        self.assertIn('나머지 첨부는 계속 읽음', blocks[3]['text'])

    def test_xls_policy_and_native_picker_consistent_for_chat_and_work(self):
        self.assertIn('.xls', chat.chat_attachment_policy()['extensions'])
        self.assertIn('.xls', attachments.attachment_policy()['extensions'])
        self.assertIn('.xls', artifacts.DOCUMENT_TYPES)
        self.assertEqual(chat.MAX_XLS_BYTES, chat.validate_chat_file('book.XLS', 10))
        with self.assertRaisesRegex(chat.AttachmentReadError, '8 MB'):
            chat.validate_chat_file('book.xls', chat.MAX_XLS_BYTES + 1)
        source = (ROOT / 'local_app/WorkspacePicker.cs').read_text(encoding='utf-8')
        filters = dict(re.findall(r'new FilterSpec\("([^"]+)", "([^"]+)"\)', source))
        self.assertIn('*.xls', filters['지원하는 모든 자료'].split(';'))
        self.assertIn('*.xls', filters['문서'].split(';'))

    def test_workbook_resources_released_after_partial_read(self):
        from local_app._vendor import xlrd
        real_open = xlrd.open_workbook
        opened = []
        def capture(**kwargs):
            book = real_open(**kwargs)
            opened.append(book)
            return book
        with patch.object(xlrd, 'open_workbook', side_effect=capture):
            text, partial, _ = legacy_excel.read_values(io.BytesIO(FIXTURE.read_bytes()), 40)
        self.assertTrue(partial)
        self.assertLessEqual(len(text), 40)
        self.assertIsNone(opened[0].mem)

    def test_vendor_sources_unchanged_and_explicitly_included_in_offline_bundle(self):
        vendor = ROOT / 'local_app/_vendor/xlrd'
        source = json.loads((vendor / 'SOURCE.json').read_text(encoding='utf-8'))
        self.assertEqual('2.0.2', source['version'])
        bundle = (ROOT / 'deploy/New-WorkspaceBundle.ps1').read_text(encoding='utf-8')
        for name, expected in source['files'].items():
            self.assertEqual(expected, hashlib.sha256((vendor / name).read_bytes()).hexdigest(), name)
            self.assertIn('local_app\\_vendor\\xlrd\\' + name, bundle)
        self.assertIn('local_app\\legacy_excel.py', bundle)
        self.assertIn('local_app\\_vendor\\__init__.py', bundle)


if __name__ == '__main__':
    unittest.main()
