import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from local_app.session_import import SessionImporter, SessionImportError


class SessionImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / 'custom config'
        self.workspace = self.root / '한글 업무 & files'
        self.workspace.mkdir()
        self.sid = str(uuid.uuid4())
        self.importer = SessionImporter(self.config)

    def row(self, kind, text='', *, uid=None, parent=None, **values):
        return {'type': kind, 'sessionId': self.sid, 'uuid': uid or str(uuid.uuid4()),
                'parentUuid': parent, 'cwd': str(self.workspace), 'isSidechain': False,
                'timestamp': '2026-09-30T01:00:00.000Z',
                'message': {'role': kind, 'content': text}, **values}

    def write(self, rows, *, project='custom-name', sid=None, config=None, tail=b''):
        path = (config or self.config) / 'projects' / project / ((sid or self.sid) + '.jsonl')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'\n'.join(json.dumps(row, ensure_ascii=False).encode('utf-8') for row in rows) + b'\n' + tail)
        return path

    def ordinary(self):
        user = self.row('user', 'Please review this report')
        answer = self.row('assistant', 'Reviewed the report', parent=user['uuid'])
        return [user, answer]

    def test_existing_ordinary_session_without_index_has_original_unicode_cwd(self):
        source = self.write(self.ordinary())
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        preview = self.importer.discover()
        self.assertEqual(1, len(preview['sessions']))
        self.assertNotIn('messages', preview['sessions'][0])
        loaded = self.importer.load(self.sid.upper())
        self.assertEqual(self.sid, loaded['sessionId'])
        self.assertEqual(str(self.workspace), loaded['workspace'])
        self.assertTrue(loaded['workspaceAvailable'])
        self.assertFalse(loaded['trusted'])
        self.assertEqual('unknown', loaded['activeStatus'])
        self.assertEqual(['Please review this report', 'Reviewed the report'], [m['text'] for m in loaded['messages']])
        self.assertEqual(before, hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertEqual([source], list(self.config.rglob('*.jsonl')))
        self.assertFalse((self.config / 'projects' / 'custom-name' / 'sessions-index.json').exists())

    def test_selected_configuration_only_and_missing_store_not_created(self):
        other = self.root / 'other-user-config'
        self.write(self.ordinary(), config=other)
        self.assertEqual([], self.importer.discover()['sessions'])
        self.assertFalse(self.config.exists())
        with self.assertRaises(SessionImportError) as error:
            self.importer.load(self.sid)
        self.assertEqual('not_found', error.exception.code)

    def test_excessive_json_nesting_is_an_invalid_record_not_an_import_crash(self):
        source = self.write(self.ordinary())
        nested = b'[' * 20000 + b'0' + b']' * 20000 + b'\n'
        source.write_bytes(nested + source.read_bytes())
        before = source.read_bytes()
        loaded = self.importer.load(self.sid)
        self.assertEqual(2, len(loaded['messages']))
        self.assertTrue(loaded['truncated'])
        self.assertTrue(loaded['warnings'])
        self.assertEqual(1, len(self.importer.discover()['sessions']))
        self.assertEqual(before, source.read_bytes())
        source.write_bytes(nested)
        with self.assertRaises(SessionImportError) as error:
            self.importer.load(self.sid)
        self.assertEqual('no_messages', error.exception.code)
        self.assertEqual([], self.importer.discover()['sessions'])
        self.assertEqual(nested, source.read_bytes())

    def test_malformed_record_types_and_surrogates_cannot_break_response_encoding(self):
        rows = self.ordinary()
        # Keep unsupported records in the parent chain to exercise display projection.
        malformed = self.row(['not', 'a', 'role'], parent=rows[0]['uuid'])
        rows[1]['parentUuid'] = malformed['uuid']
        rows[1]['message']['content'] = 'Answer \ud800 with valid 한글'
        path = self.write([])
        path.write_text('\n'.join(json.dumps(row, ensure_ascii=True) for row in
                                  [rows[0], malformed, rows[1]]) + '\n', encoding='utf-8')
        before = path.read_bytes()
        loaded = self.importer.load(self.sid)
        self.assertEqual(2, len(loaded['messages']))
        self.assertEqual('Answer  with valid 한글', loaded['messages'][1]['text'])
        json.dumps(loaded, ensure_ascii=False).encode('utf-8')
        self.assertEqual(before, path.read_bytes())
        rows[1]['cwd'] = str(self.workspace) + '\ud800'
        path.write_text('\n'.join(json.dumps(row, ensure_ascii=True) for row in rows), encoding='utf-8')
        with self.assertRaises(SessionImportError) as error:
            self.importer.load(self.sid)
        self.assertEqual('workspace_unknown', error.exception.code)

    def test_deterministic_identity_is_separate_for_configuration_roots(self):
        self.write(self.ordinary())
        first = self.importer.load(self.sid)
        self.assertEqual(first['id'], self.importer.load(self.sid)['id'])
        self.assertEqual(first['id'], self.importer.discover()['sessions'][0]['id'])
        other = self.root / 'other-config'
        self.write(self.ordinary(), config=other)
        self.assertNotEqual(first['id'], SessionImporter(other).load(self.sid)['id'])

    def test_invalid_uuid_cannot_be_a_path_name_or_argument(self):
        for value in ['../session', self.sid + ' --model other', '{' + self.sid + '}', self.sid.replace('-', ''), '', None]:
            with self.subTest(value=value), self.assertRaises(SessionImportError) as error:
                self.importer.load(value)
            self.assertEqual('invalid_id', error.exception.code)

    def test_duplicate_session_copies_are_ambiguous_not_newest_selected(self):
        self.write(self.ordinary(), project='first')
        self.write(self.ordinary(), project='second')
        with self.assertRaises(SessionImportError) as error:
            self.importer.load(self.sid)
        self.assertEqual('ambiguous', error.exception.code)
        result = self.importer.discover()
        self.assertEqual([], result['sessions'])
        self.assertTrue(result['warnings'])

    def test_only_plain_text_and_no_trust_tools_thinking_or_settings_import(self):
        user = self.row('user', 'normal prompt')
        tool = self.row('assistant', parent=user['uuid'], message={'role': 'assistant', 'content': [
            {'type': 'thinking', 'thinking': 'PRIVATE_THINKING'},
            {'type': 'tool_use', 'name': 'Bash', 'input': {'command': 'PRIVATE_COMMAND'}},
            {'type': 'text', 'text': 'Visible answer'}]})
        result = self.row('user', parent=tool['uuid'], message={'role': 'user', 'content': [
            {'type': 'tool_result', 'content': 'PRIVATE_RESULT'}, {'type': 'image', 'source': {'data': 'PRIVATE_IMAGE'}}]},
            toolUseResult={'secret': 'PRIVATE_RESULT2'})
        meta = self.row('user', 'PRIVATE_META', parent=result['uuid'], isMeta=True)
        final = self.row('assistant', 'Finished', parent=meta['uuid'], permissionMode='bypassPermissions', apiKey='PRIVATE_KEY')
        side = self.row('assistant', 'PRIVATE_AGENT', parent=final['uuid'], isSidechain=True)
        self.write([user, tool, result, meta, final, side])
        loaded = self.importer.load(self.sid)
        self.assertEqual(['normal prompt', 'Visible answer', 'Finished'], [m['text'] for m in loaded['messages']])
        self.assertNotIn('PRIVATE_', json.dumps(loaded))
        self.assertNotIn('permissionMode', loaded)
        self.assertNotIn('apiKey', loaded)

    def test_last_parent_chain_excludes_rewound_branch_and_foreign_session(self):
        first = self.row('user', 'first')
        old = self.row('assistant', 'discarded answer', parent=first['uuid'])
        old_prompt = self.row('user', 'discarded followup', parent=old['uuid'])
        revised = self.row('assistant', 'revised answer', parent=first['uuid'])
        alien = self.row('assistant', 'wrong session', parent=revised['uuid'], sessionId=str(uuid.uuid4()))
        self.write([first, old, old_prompt, revised, alien])
        self.assertEqual(['first', 'revised answer'], [m['text'] for m in self.importer.load(self.sid)['messages']])

    def test_assistant_fragments_and_replayed_uuid_do_not_duplicate_bubbles(self):
        first = self.row('user', 'first')
        a = self.row('assistant', parent=first['uuid'], message={'id': 'msg-1', 'content': [{'type': 'text', 'text': 'Hello'}]})
        b = self.row('assistant', parent=a['uuid'], message={'id': 'msg-1', 'content': [{'type': 'text', 'text': 'Hello world'}]})
        c = self.row('assistant', parent=b['uuid'], message={'id': 'msg-1', 'content': [{'type': 'text', 'text': 'Next paragraph'}]})
        second = self.row('user', 'first', parent=c['uuid'])
        self.write([first, a, b, c, c, second])
        self.assertEqual(['first', 'Hello world\nNext paragraph', 'first'],
                         [m['text'] for m in self.importer.load(self.sid)['messages']])

    def test_latest_cwd_after_directory_change_is_not_decoded_from_storage_name(self):
        rows = self.ordinary()
        moved = self.root / 'moved'
        moved.mkdir()
        rows[-1]['cwd'] = str(moved)
        self.write(rows, project='an-arbitrary-project-name')
        self.assertEqual(str(moved), self.importer.load(self.sid)['workspace'])

    def test_missing_or_unsafe_cwd_is_not_replaced_with_current_directory(self):
        for cwd in [None, '', 'relative/path', '\\\\host\\share', 'C:\\bad\npath']:
            with self.subTest(cwd=cwd):
                rows = self.ordinary()
                for row in rows:
                    row['cwd'] = cwd
                self.write(rows)
                with self.assertRaises(SessionImportError) as error:
                    self.importer.load(self.sid)
                self.assertEqual('workspace_unknown', error.exception.code)

    def test_deleted_workspace_reports_unavailable_without_recreating_it(self):
        rows = self.ordinary()
        path = self.root / 'deleted-folder'
        for row in rows:
            row['cwd'] = str(path)
        self.write(rows)
        result = self.importer.load(self.sid)
        self.assertEqual(str(path), result['workspace'])
        self.assertFalse(result['workspaceAvailable'])
        self.assertTrue(result['warnings'])
        self.assertFalse(path.exists())

    def test_partial_tail_and_missing_parent_are_explicit_partial_previews(self):
        rows = self.ordinary()
        rows[0]['parentUuid'] = str(uuid.uuid4())
        self.write(rows, tail=b'{"type":"unfinished')
        loaded = self.importer.load(self.sid)
        self.assertTrue(loaded['truncated'])
        self.assertTrue(loaded['warnings'])
        self.assertEqual(2, len(loaded['messages']))

    def test_metadata_preview_is_bounded_and_full_load_restores_visible_chain(self):
        rows = self.ordinary()
        middle = {'type': 'attachment', 'sessionId': self.sid, 'attachment': 'PRIVATE_' * 600}
        self.write([rows[0], middle, rows[1]])
        with patch('local_app.session_import.PREVIEW_BYTES', 1024):
            preview = self.importer.discover()['sessions'][0]
        self.assertTrue(preview['truncated'])
        self.assertNotIn('PRIVATE_', json.dumps(preview))
        loaded = self.importer.load(self.sid)
        self.assertFalse(loaded['truncated'])
        self.assertEqual(2, len(loaded['messages']))

    def test_append_during_import_is_not_reported_as_a_stable_snapshot(self):
        path = self.write(self.ordinary())
        parse, changed = json.loads, False

        def append_once(value, *args, **kwargs):
            nonlocal changed
            if not changed:
                changed = True
                with path.open('ab') as stream:
                    stream.write(b'\n')
            return parse(value, *args, **kwargs)

        with patch('local_app.session_import.json.loads', side_effect=append_once):
            with self.assertRaises(SessionImportError) as error:
                self.importer.load(self.sid)
        self.assertEqual('changed', error.exception.code)

    def test_cli_generated_envelopes_and_ansi_controls_are_not_displayed(self):
        prompt = self.row('user', '\n\x1b[31mReal prompt\x1b[0m\x00')
        command = self.row('user', '<local-command-stdout>PRIVATE_TOOL_OUTPUT</local-command-stdout>', parent=prompt['uuid'])
        answer = self.row('assistant', 'Visible answer', parent=command['uuid'])
        self.write([prompt, command, answer])
        loaded = self.importer.load(self.sid)
        self.assertEqual('Real prompt', loaded['title'])
        self.assertEqual(['\nReal prompt', 'Visible answer'], [m['text'] for m in loaded['messages']])
        self.assertNotIn('PRIVATE_TOOL_OUTPUT', json.dumps(loaded))

    def test_limits_never_choose_an_id_from_an_incomplete_scan(self):
        self.write(self.ordinary())
        with patch('local_app.session_import.MAX_PROJECTS', 0):
            with self.assertRaises(SessionImportError) as error:
                self.importer.load(self.sid)
            self.assertEqual('scan_limit', error.exception.code)
            self.assertTrue(self.importer.discover()['truncated'])
        with patch('local_app.session_import.MAX_FILE_BYTES', 10):
            with self.assertRaises(SessionImportError) as error:
                self.importer.load(self.sid)
            self.assertEqual('too_large', error.exception.code)
        with patch('local_app.session_import.MAX_LINE_BYTES', 10):
            with self.assertRaises(SessionImportError) as error:
                self.importer.load(self.sid)
            self.assertEqual('line_limit', error.exception.code)

    def test_history_cap_keeps_latest_messages_and_marks_partial(self):
        rows = []
        for index in range(155):
            rows.append(self.row('user' if index % 2 == 0 else 'assistant', f'message {index}',
                                 parent=rows[-1]['uuid'] if rows else None))
        self.write(rows)
        loaded = self.importer.load(self.sid)
        self.assertEqual(150, len(loaded['messages']))
        self.assertEqual('message 154', loaded['messages'][-1]['text'])
        self.assertTrue(loaded['truncated'])

    def test_symlink_projects_are_not_followed(self):
        external = self.root / 'external'
        source = self.write(self.ordinary(), config=external)
        projects = self.config / 'projects'
        projects.mkdir(parents=True)
        try:
            (projects / 'linked').symlink_to(source.parent, target_is_directory=True)
        except OSError:
            self.skipTest('Symlink creation permission is unavailable')
        self.assertEqual([], self.importer.discover()['sessions'])
        with self.assertRaises(SessionImportError) as error:
            self.importer.load(self.sid)
        self.assertEqual('not_found', error.exception.code)


if __name__ == '__main__':
    unittest.main()
