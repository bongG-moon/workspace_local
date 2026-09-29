"""Explicit original-file opening must preserve scope and existing associations."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from local_app.external_apps import open_document
from local_app.server import LocalApp


class ExternalAppsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.work = self.root / 'work'; self.work.mkdir()
        self.html = self.work / '한글 보고서 & 원본.html'
        self.html.write_text('<script>example()</script>', encoding='utf-8')

    def test_html_and_office_follow_existing_association_only_on_explicit_call(self):
        doc = self.work / 'report.xlsx'; doc.write_bytes(b'fixture')
        with patch('local_app.external_apps.os.startfile', create=True) as start:
            for path in [self.html, doc]:
                self.assertEqual({'ok': True, 'action': 'open', 'requested': True}, open_document(path))
                start.assert_called_with(str(path), 'open')
        self.assertEqual('<script>example()</script>', self.html.read_text(encoding='utf-8'))

    def test_reveal_and_text_use_fixed_program_and_literal_arguments(self):
        with patch('local_app.external_apps.windows_program', side_effect=lambda name: 'C:/Windows/' + name), \
             patch('local_app.external_apps.subprocess.Popen') as run:
            open_document(self.html, 'reveal')
            self.assertEqual(['C:/Windows/explorer.exe', '/select,', str(self.html)], run.call_args.args[0])
            self.assertNotIn('shell', run.call_args.kwargs)
            open_document(self.html, 'text')
            self.assertEqual(['C:/Windows/notepad.exe', str(self.html)], run.call_args.args[0])

    def test_executable_protocol_relative_or_wrong_action_never_launches(self):
        exe = self.work / 'bad.exe'; exe.write_bytes(b'test')
        with patch('local_app.external_apps.os.startfile', create=True) as start, \
             patch('local_app.external_apps.subprocess.Popen') as run:
            for value, action in [(exe, 'open'), ('https://invalid.example/x.html', 'open'),
                                  ('relative.html', 'open'), (self.html, 'execute')]:
                with self.subTest(value=value, action=action), self.assertRaises(ValueError):
                    open_document(value, action)
            binary = self.work / 'a.pdf'; binary.write_bytes(b'pdf')
            with self.assertRaises(ValueError):
                open_document(binary, 'text')
            start.assert_not_called(); run.assert_not_called()

    def test_missing_association_returns_actionable_message_without_claiming_success(self):
        error = OSError('fixture'); error.winerror = 1155
        with patch('local_app.external_apps.os.startfile', side_effect=error, create=True):
            with self.assertRaisesRegex(ValueError, '연결 프로그램이 없습니다'):
                open_document(self.html)

    def test_file_scope_is_checked_before_external_action(self):
        app = LocalApp(self.root / 'state', command=['never-start'], managed_workspace_root=self.root / 'managed')
        self.addCleanup(app.close)
        sid = app.create(str(self.work), True)['id']
        outside = self.root / 'outside.html'; outside.write_text('outside', encoding='utf-8')
        with self.assertRaises(ValueError):
            app.allowed_file(sid, outside)
        app.get(sid)['attachments'] = [str(outside)]
        self.assertEqual(outside, app.allowed_file(sid, outside))
        with patch('local_app.server.workspace_path', side_effect=ValueError('replaced junction')):
            with self.assertRaises(ValueError):
                app.allowed_file(sid, self.html)


if __name__ == '__main__':
    unittest.main()
