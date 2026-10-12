"""Autocomplete reads bounded task metadata and never starts a CLI or file."""
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
import unicodedata
from unittest.mock import Mock, patch

from local_app.completions import CompletionDiscovery, REFERENCE_FILE_TYPES, complete


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='workspace-completions-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / '업무 폴더'
        self.root.mkdir()
        self.item = {'id': 'task-a', 'workspace': str(self.root), 'attachments': []}

    def file(self, name, root=None):
        path = (root or self.root) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('must not be read by autocomplete', encoding='utf-8')
        return path

    def files(self, query='', attachments=None, **kwargs):
        return complete(self.item, {'kind': 'file', 'query': query,
                                    'attachments': attachments or []}, seconds=.5, **kwargs)

    def commands(self, raw, *, query='', reported=True, **kwargs):
        self.item['bridge'] = SimpleNamespace(closed=False, process=Mock(poll=Mock(return_value=None)))
        self.item['connection'] = {'reported': {'commands': reported}, 'slashCommands': raw}
        return complete(self.item, {'kind': 'slash', 'query': query}, seconds=.5, **kwargs)

    def test_supported_session_effort_command_is_local_and_deduplicated(self):
        self.item['bridge'] = SimpleNamespace(closed=False, process=Mock(poll=Mock(return_value=None)))
        self.item['connection'] = {'reported': {'commands': True}, 'slashCommands': ['effort', 'company:effort'],
                                   'capabilities': {'setEffort': True}}
        result = complete(self.item, {'kind': 'slash', 'query': 'eff'})
        local = [row for row in result['items'] if row['invocation'] == '/effort']
        self.assertEqual(1, len(local))
        self.assertEqual('workspace', local[0]['source'])
        self.assertEqual('local', local[0]['availability'])
        self.assertTrue(local[0]['supported'])
        self.assertEqual('runtime', next(row for row in result['items'] if row['invocation'] == '/company:effort')['source'])
        self.item['connection']['slashCommands'] = []
        self.assertEqual(['/effort'], [row['invocation'] for row in complete(self.item, {'kind': 'slash'})['items']])
        self.item['connection']['capabilities']['setEffort'] = False
        self.assertEqual([], complete(self.item, {'kind': 'slash'})['items'])

    def test_file_metadata_only_korean_spaces_symbols_and_stable_identity(self):
        path = self.file('보고서/팀 A (최종) & 결과 😀.PDF')
        with patch.object(Path, 'open', side_effect=AssertionError('no content reads')):
            result = self.files('@최종')
            second = self.files('보고서/팀 A')
        self.assertFalse(result['limited'])
        self.assertEqual(1, len(result['items']))
        row = result['items'][0]
        self.assertEqual(str(path), row['path'])
        self.assertEqual('보고서/팀 A (최종) & 결과 😀.PDF', row['label'])
        self.assertTrue(row['supported'])
        self.assertEqual(row['id'], second['items'][0]['id'])
        self.assertNotIn('must not be read', str(result))

    def test_file_metadata_includes_exe_archives_excludes_other_binaries_hidden_and_build_folders(self):
        expected = {str(self.file(name)) for name in ['report.md', 'app.py', 'run.ps1',
                                                     'a.json', 'src/view.TSX', 'query.sql',
                                                     'program.exe', 'archive.zip', '한글.tar.gz']}
        for name in ['library.dll', '.secret.txt',
                     '.claude/CLAUDE.md', 'node_modules/dependency.md', 'dist/copy.md']:
            self.file(name)
        with patch.object(Path, 'open', side_effect=AssertionError('no content reads')):
            result = self.files()
        self.assertEqual(expected, {row['path'] for row in result['items']})
        self.assertFalse(result['limited'])

    def test_source_reference_suffixes_do_not_expand_external_open_document_types(self):
        from local_app.artifacts import DOCUMENT_TYPES
        self.assertIn('.py', REFERENCE_FILE_TYPES)
        self.assertIn('.ipynb', REFERENCE_FILE_TYPES)
        self.assertNotIn('.py', DOCUMENT_TYPES)
        self.assertIn('.exe', REFERENCE_FILE_TYPES)
        self.assertNotIn('.exe', DOCUMENT_TYPES)
        self.assertIn('.zip', REFERENCE_FILE_TYPES)
        self.assertNotIn('.zip', DOCUMENT_TYPES)

    def test_explicit_external_and_saved_attachments_only_no_parent_enumeration(self):
        selected = self.file('outside/선택.txt', self.base)
        saved = self.file('old/이전 문서.docx', self.base)
        self.file('outside/선택하지 않음.txt', self.base)
        self.item['attachments'] = [str(saved), str(selected)]
        original = __import__('os').scandir
        seen = []
        def scan(path):
            seen.append(Path(path))
            self.assertTrue(Path(path).is_relative_to(self.root))
            return original(path)
        with patch('local_app.completions.os.scandir', side_effect=scan):
            result = self.files(attachments=[str(selected), str(selected)])
        self.assertEqual({str(selected), str(saved)}, {row['path'] for row in result['items']})
        self.assertEqual(2, len(result['items']))
        self.assertTrue(all('직접 선택' in row['description'] for row in result['items']))
        self.assertEqual([self.root], seen)

    def test_selected_file_inside_workspace_deduplicates_and_cannot_expand_suffixes(self):
        path = self.file('same.txt')
        script = self.file('script.py')
        executable = self.file('program.exe')
        library = self.file('program.dll')
        result = self.files(attachments=[str(path), str(script), str(executable), str(library)],
                            safe_suffixes={'.txt', '.py', '.exe', '.dll'})
        self.assertEqual({str(path), str(script), str(executable)}, {row['path'] for row in result['items']})
        result = self.files(safe_suffixes={'.txt'})
        self.assertEqual([str(path)], [row['path'] for row in result['items']])

    def test_file_prefixes_precede_word_and_substring_matches(self):
        for name in ['alpha-preport.md', 'team-report.md', 'z/report-2026.md',
                     'report-archive/summary.md', 'archive/reporting-notes.md']:
            self.file(name)
        result = self.files('report')
        labels = [row['label'] for row in result['items']]
        self.assertEqual(['archive/reporting-notes.md', 'report-archive/summary.md',
                          'z/report-2026.md', 'team-report.md', 'alpha-preport.md'], labels)

    def test_late_file_exact_basename_displaces_earlier_substring_before_limit(self):
        selected = self.file('selected/old-report.md', self.base)
        exact = self.file('z/nested/report.md')
        self.file('a/report.md.backup.md')
        result = self.files('report.md', attachments=[str(selected)], max_items=1)
        self.assertEqual([str(exact)], [row['path'] for row in result['items']])
        self.assertTrue(result['limited'])

    def test_korean_file_queries_normalize_without_changing_path_or_label(self):
        path = self.file('자료/보고서 초안.py')
        composed = self.files('보고')
        decomposed = self.files(unicodedata.normalize('NFD', '보고'))
        self.assertEqual(composed, decomposed)
        self.assertEqual(str(path), decomposed['items'][0]['path'])
        self.assertEqual('자료/보고서 초안.py', decomposed['items'][0]['label'])

    def test_query_is_only_filter_never_filesystem_scope(self):
        outside = self.file('outside.txt', self.base)
        self.file('inside.txt')
        for query in ['../outside', str(outside), '..\\outside', '*', 'C:/Users']:
            with self.subTest(query=query):
                self.assertEqual([], self.files(query)['items'])
        self.item['catalog'] = {'workspace': str(self.base)}
        self.assertEqual([], self.files('outside')['items'])

    def test_missing_and_bad_attachment_does_not_discard_valid_matches(self):
        expected = self.file('valid.txt')
        for bad in [str(self.base / 'deleted.txt'), 'relative.txt', str(self.root / '..' / 'escape.txt')]:
            with self.subTest(bad=bad):
                result = self.files(attachments=[bad])
                self.assertEqual([str(expected)], [row['path'] for row in result['items']])
                self.assertTrue(result['limited'])

    def test_no_cache_keeps_tasks_and_file_deletion_isolated(self):
        path = self.file('old.txt')
        first = self.files()
        path.unlink()
        self.assertEqual([], self.files()['items'])
        other = self.base / 'other-task'
        other.mkdir()
        self.item['workspace'] = str(other)
        new = self.file('new.txt', other)
        self.assertEqual([str(new)], [row['path'] for row in self.files()['items']])
        self.assertNotEqual(first['items'][0]['id'], self.files()['items'][0]['id'])

    def test_depth_limit_reads_four_nested_folders_and_reports_omissions(self):
        found = self.file('a/b/c/d/found.txt')
        hidden = self.file('a/b/c/d/e/deeper.txt')
        result = self.files()
        self.assertEqual([str(found)], [row['path'] for row in result['items']])
        self.assertTrue(result['limited'])
        self.assertNotIn(str(hidden), str(result))

    def test_entry_directory_and_result_limits_are_explicit(self):
        for number in range(8):
            self.file(f'root-{number}.txt')
        result = self.files(max_entries=3)
        self.assertEqual(3, len(result['items']))
        self.assertTrue(result['limited'])
        result = self.files(max_items=2)
        self.assertEqual(2, len(result['items']))
        self.assertTrue(result['limited'])
        self.file('child/child.txt')
        result = self.files(max_directories=1)
        self.assertTrue(result['limited'])
        self.assertFalse(any('child' in row['label'] for row in result['items']))

    def test_deadline_stops_before_enumerating_and_closes_active_iterator(self):
        self.file('first.txt')
        with patch('local_app.completions.time.monotonic', side_effect=[0., 1.]), \
                patch('local_app.completions.os.scandir', side_effect=AssertionError('deadline')):
            result = complete(self.item, {'kind': 'file'})
        self.assertEqual({'items': [], 'limited': True}, result)
        iterator = Mock()
        iterator.__enter__ = Mock(return_value=iter([SimpleNamespace(name='x')]))
        iterator.__exit__ = Mock(return_value=False)
        with patch('local_app.completions.time.monotonic', side_effect=[0., 0., 1.]), \
                patch('local_app.completions.os.scandir', return_value=iterator):
            result = complete(self.item, {'kind': 'file'})
        self.assertTrue(result['limited'])
        iterator.__exit__.assert_called_once()

    def test_symlinks_are_never_followed_for_folders_or_selected_files(self):
        outside = self.base / 'outside'
        outside.mkdir()
        secret = self.file('secret.txt', outside)
        folder_link, file_link = self.root / 'linked-folder', self.root / 'linked-file.txt'
        try:
            folder_link.symlink_to(outside, target_is_directory=True)
            file_link.symlink_to(secret)
        except OSError:
            self.skipTest('This account cannot create symbolic links.')
        self.assertEqual([], self.files()['items'])
        self.assertEqual([], self.files(attachments=[str(file_link)])['items'])

    def test_queued_reparse_directory_is_blocked_but_cloud_folder_allowed(self):
        path = self.file('folder/document.txt')
        original = Path.lstat
        def tagged(tag):
            def read(candidate, *args, **kwargs):
                info = original(candidate, *args, **kwargs)
                if candidate == path.parent:
                    values = {key: getattr(info, key) for key in dir(info) if key.startswith('st_')}
                    values.update(st_file_attributes=0x400, st_reparse_tag=tag)
                    return SimpleNamespace(**values)
                return info
            return read
        for tag in (0xA0000003, 0xA000000C, 0x9000001B):
            with self.subTest(tag=tag), patch.object(Path, 'lstat', tagged(tag)):
                result = self.files()
                self.assertEqual([], result['items'])
                self.assertTrue(result['limited'])
        with patch.object(Path, 'lstat', tagged(0x9000301A)):
            self.assertEqual([str(path)], [row['path'] for row in self.files()['items']])

    def test_invalid_request_is_rejected_without_scanning(self):
        bad = [None, {}, {'kind': 'wat'}, {'kind': 'file', 'query': '\ud800'},
               {'kind': 'file', 'query': 'a\nb'}, {'kind': 'file', 'query': 'a' * 513},
               {'kind': 'file', 'attachments': 'path'}, {'kind': 'file', 'attachments': [None]},
               {'kind': 'file', 'attachments': ['a'] * 13}]
        with patch('local_app.completions.os.scandir', side_effect=AssertionError('invalid request')):
            for request in bad:
                with self.subTest(request=request), self.assertRaises(ValueError):
                    complete(self.item, request)

    def test_live_runtime_names_only_namespace_and_prefix_preserved(self):
        result = self.commands(['company:report', {'name': '/other:report', 'description': '다른 보고서'},
                                'review', 'report'], query='/co')
        self.assertEqual(['/company:report'], [row['invocation'] for row in result['items']])
        self.assertTrue(result['items'][0]['supported'])
        self.assertFalse(result['limited'])
        result = self.commands(['company:report', 'other:report'])
        self.assertEqual(2, len({row['id'] for row in result['items']}))

    def test_slash_rank_exact_then_namespace_prefix_then_word_then_substring_description(self):
        raw = ['preport', 'tools:daily-report', 'report:build', 'company:report', 'report',
               {'name': 'analyze', 'description': 'Create a report from the current data'}]
        result = self.commands(raw, query='/report')
        self.assertEqual(['/report', '/company:report', '/report:build', '/tools:daily-report',
                          '/preport', '/analyze'], [row['invocation'] for row in result['items']])
        self.assertFalse(result['limited'])
        self.assertEqual(result, self.commands(list(reversed(raw)), query='/report'))

    def test_late_exact_runtime_match_survives_result_limit(self):
        raw = [f'tools:old-report-{number}' for number in range(60)] + ['report']
        result = self.commands(raw, query='report', max_items=1)
        self.assertEqual(['/report'], [row['invocation'] for row in result['items']])
        self.assertTrue(result['limited'])

    def test_korean_namespace_and_description_search_preserves_exact_invocation(self):
        decomposed_name = unicodedata.normalize('NFD', 'company:보고서-생성')
        raw = [decomposed_name, {'name': 'analyze', 'description': '보고서를 만드는 도구'}]
        composed = self.commands(raw, query='보고')
        decomposed = self.commands(raw, query=unicodedata.normalize('NFD', '보고'))
        self.assertEqual(composed, decomposed)
        self.assertEqual('/' + decomposed_name, decomposed['items'][0]['invocation'])
        self.assertEqual('/analyze', decomposed['items'][1]['invocation'])

    def test_generic_and_conflicting_descriptions_do_not_invent_search_matches(self):
        self.assertEqual([], self.commands(['report'], query='현재')['items'])
        result = self.commands([{'name': 'report', 'description': 'source A'},
                                {'name': '/report', 'description': 'source B'}], query='source')
        self.assertEqual([], result['items'])

    def test_unknown_report_differs_from_reported_empty_and_catalog_not_used(self):
        self.assertEqual({'items': [], 'limited': False}, self.commands([]))
        self.assertEqual({'items': [], 'limited': True}, self.commands(['report'], reported=False))
        self.item['connection']['skills'] = ['unreported-skill']
        self.item['catalog'] = {'commands': ['catalog-only']}
        self.item['connection'].pop('reported')
        self.assertEqual({'items': [], 'limited': True}, complete(self.item, {'kind': 'slash'}))
        self.item['connection'] = None
        self.assertEqual({'items': [], 'limited': True}, complete(self.item, {'kind': 'slash'}))
        self.assertEqual({'items': [], 'limited': True}, self.commands('report'))

    def test_closed_or_exited_or_absent_process_never_reuses_last_connection(self):
        self.commands(['report'])
        bridge = self.item['bridge']
        for field, value in [('closed', True), ('process', None),
                             ('process', Mock(poll=Mock(return_value=0)))]:
            with self.subTest(field=field, value=value):
                self.item['bridge'] = SimpleNamespace(closed=False, process=bridge.process)
                setattr(self.item['bridge'], field, value)
                self.assertEqual({'items': [], 'limited': True, 'connectRequired': True},
                                 complete(self.item, {'kind': 'slash'}))
        self.item.pop('bridge')
        self.assertEqual({'items': [], 'limited': True, 'connectRequired': True},
                         complete(self.item, {'kind': 'slash'}))

    def test_first_slash_query_requests_connection_without_starting_it(self):
        with patch('subprocess.Popen', side_effect=AssertionError('no process')):
            result = complete(self.item, {'kind': 'slash', 'query': 'rep'})
        self.assertEqual({'items': [], 'limited': True, 'connectRequired': True}, result)

    def test_known_terminal_ui_commands_are_explained_without_inventing_missing_ones(self):
        result = self.commands(['login', 'permissions', 'theme', 'company:login', 'model'])
        by_name = {row['invocation']: row for row in result['items']}
        for command in ['/login', '/permissions', '/theme']:
            self.assertFalse(by_name[command]['supported'])
            self.assertIn('이 앱', by_name[command]['reason'])
        self.assertTrue(by_name['/company:login']['supported'])
        self.assertTrue(by_name['/model']['supported'])  # Has a headless form; not TUI-only.
        self.assertNotIn('/logout', by_name)

    def test_runtime_reported_headless_commands_are_not_blocked_as_terminal_dialogs(self):
        commands = ['clear', 'reset', 'new', 'compact', 'context', 'usage', 'model',
                    'effort', 'fast', 'color', 'rename', 'mcp', 'config', 'settings',
                    'output-style', 'reload-plugins', 'resume', 'continue', 'exit', 'quit']
        result = self.commands(commands)
        self.assertEqual({'/' + name for name in commands},
                         {row['invocation'] for row in result['items']})
        self.assertTrue(all(row['supported'] and 'reason' not in row for row in result['items']))
        # A supported name is still not invented when this connection omits it.
        self.assertEqual([], self.commands(['compact'], query='clear')['items'])

    def test_malformed_runtime_names_and_invocation_injection_do_not_get_suggested(self):
        raw = [None, {}, True, {'name': 'skill', 'invocation': '/login; bad'},
               {'name': 'bad\nname'}, {'name': 'bad arg'}, {'name': '//bad'},
               {'name': 'bad/child'}, {'name': '\ud800'}, {'name': 'bad$(cmd)'}]
        result = self.commands(raw)
        self.assertEqual(['/skill'], [row['invocation'] for row in result['items']])
        self.assertTrue(result['limited'])

    def test_runtime_nested_skill_keeps_qualified_name_and_matches_leaf(self):
        raw = [{'name': 'apps/web:deploy', 'description': 'Web project deployment'},
               {'name': '/apps/web:deploy', 'description': 'Web project deployment'},
               {'name': 'packages/\ud55c\uae00-ui:review', 'description': 'Nested review'},
               {'name': 'team:deploy', 'description': 'Plugin deployment'}]
        result = self.commands(raw, query='deploy')
        self.assertEqual({'/apps/web:deploy', '/team:deploy'},
                         {row['invocation'] for row in result['items']})
        self.assertFalse(result['limited'])
        nested = self.commands(raw, query='apps/web')['items']
        self.assertEqual(1, len(nested))
        self.assertEqual('/apps/web:deploy', nested[0]['invocation'])
        self.assertEqual('runtime', nested[0]['source'])
        self.assertEqual('reported', nested[0]['availability'])
        self.assertEqual('/packages/\ud55c\uae00-ui:review',
                         self.commands(raw, query='review')['items'][0]['invocation'])

    def test_runtime_nested_names_reject_traversal_absolute_and_empty_segments(self):
        invalid = ['../apps:deploy', 'apps/../web:deploy', 'apps/./web:deploy',
                   'apps//web:deploy', 'apps/:deploy', '//apps/web:deploy',
                   'C:/apps/web:deploy', '/C:/apps/web:deploy', 'apps\\web:deploy',
                   'apps/web:', 'apps/web:/deploy', 'apps/web:bad arg',
                   'apps/web:bad\nname', 'apps/web:$(cmd)', 'apps/web']
        for name in invalid:
            with self.subTest(name=name):
                result = self.commands([name])
                self.assertEqual([], result['items'])
                self.assertTrue(result['limited'])

    def test_installed_metadata_cannot_invent_runtime_nested_invocations(self):
        client = Mock()
        client.completion_inventory.return_value = {'skills': [{
            'invocation': 'apps/web:deploy', 'storageScope': 'project',
            'source': 'project', 'userInvocable': True}]}
        result = self.commands([], discovery=CompletionDiscovery(client))
        self.assertEqual([], result['items'])

    def test_repeated_invocation_has_one_row_and_no_arbitrary_origin_claim(self):
        result = self.commands([{'name': 'report', 'description': 'source A'},
                                {'name': '/report', 'description': 'source B'}])
        self.assertEqual(1, len(result['items']))
        self.assertIn('같은 호출명', result['items'][0]['description'])
        self.assertEqual('/report', result['items'][0]['invocation'])

    def test_command_caps_and_deadline_remain_bounded(self):
        result = self.commands([f'skill-{i}' for i in range(1100)], max_items=3)
        self.assertEqual(3, len(result['items']))
        self.assertTrue(result['limited'])
        with patch('local_app.completions.time.monotonic', side_effect=[0., 1.]):
            result = complete(self.item, {'kind': 'slash'})
        self.assertEqual({'items': [], 'limited': True}, result)

    def test_command_query_never_scans_files_or_starts_a_process(self):
        with patch('local_app.completions.os.scandir', side_effect=AssertionError('no filesystem scan')), \
                patch('subprocess.Popen', side_effect=AssertionError('no process')):
            result = self.commands(['report'])
        self.assertEqual('/report', result['items'][0]['invocation'])

    def test_installed_fallback_is_not_runtime_proof_and_omits_library_internal_rows(self):
        client = Mock()
        def row(name, **kwargs):
            return {'invocation': name, 'description': 'Skill metadata', 'storageScope': 'personal',
                    'userInvocable': True, **kwargs}
        client.completion_inventory.return_value = {'skills': [row('skill-creator'), row('company:skills'),
            row('internal', userInvocable=False), row(''), row('bad arg'), row('project', storageScope='project')]}
        discovery = CompletionDiscovery(client)
        result = complete({}, {'kind': 'slash', 'query': 'sk'}, discovery=discovery)
        self.assertEqual(['/company:skills', '/skill-creator'], [item['invocation'] for item in result['items']])
        self.assertTrue(result['discovery'])
        self.assertTrue(result['connectRequired'])
        self.assertTrue(all(item['availability'] == 'discovered' and item['source'] == 'installed'
                            and item['scope'] == 'common' for item in result['items']))
        # A live startup report is not a filesystem watcher. Installed skill
        # names remain discoverable without pretending the CLI reported them.
        self.commands([])
        result = complete(self.item, {'kind': 'slash'}, discovery=discovery)
        self.assertEqual(['/company:skills', '/project', '/skill-creator'],
                         [item['invocation'] for item in result['items']])
        self.assertTrue(result['discovery'])
        self.assertFalse(result['limited'])
        self.assertNotIn('connectRequired', result)
        self.assertEqual([None, str(self.root)],
                         [call.args[0] for call in client.completion_inventory.call_args_list])

    def test_live_runtime_merges_new_installed_skill_without_starting_cli(self):
        client = Mock()
        client.completion_inventory.return_value = {'skills': [
            {'invocation': 'mumu-pocket-launch', 'storageScope': 'project',
             'userInvocable': True, 'description': 'Launch the selected instance'},
            {'invocation': 'mumu-internal', 'storageScope': 'project', 'userInvocable': False}]}
        with patch('subprocess.Popen', side_effect=AssertionError('must not start Claude')):
            result = self.commands(['compact'], query='/mumu', discovery=CompletionDiscovery(client))
        self.assertEqual(['/mumu-pocket-launch'], [row['invocation'] for row in result['items']])
        self.assertEqual(('installed', 'discovered', 'folder'),
                         tuple(result['items'][0][key] for key in ('source', 'availability', 'scope')))
        self.assertTrue(result['discovery'])
        self.assertNotIn('connectRequired', result)

    def test_runtime_collision_keeps_support_description_and_exact_namespace(self):
        client = Mock()
        client.completion_inventory.return_value = {'skills': [
            {'invocation': name, 'description': 'LOCAL-CONFLICT', 'storageScope': 'personal',
             'userInvocable': True} for name in ('login', 'report', 'plugin:report')]}
        discovery = CompletionDiscovery(client)
        raw = ['login', {'name': 'report', 'description': 'Live description'}]
        result = self.commands(raw, discovery=discovery)
        rows = {row['invocation']: row for row in result['items']}
        self.assertEqual(3, len(rows))
        self.assertFalse(rows['/login']['supported'])
        self.assertEqual('runtime', rows['/login']['source'])
        self.assertEqual('Live description', rows['/report']['description'])
        self.assertEqual('installed', rows['/plugin:report']['source'])
        result = self.commands(raw, query='LOCAL-CONFLICT', discovery=discovery)
        self.assertEqual(['/plugin:report'], [row['invocation'] for row in result['items']])

    def test_personal_skill_visibility_shadows_same_project_name_before_filtering(self):
        client = Mock()
        personal = {'invocation': 'same', 'source': 'user', 'storageScope': 'personal',
                    'userInvocable': False, 'description': 'Personal skill'}
        project = {'invocation': 'same', 'source': 'project', 'storageScope': 'project',
                   'userInvocable': True, 'description': 'Project skill'}
        plugin = {'invocation': 'plugin:same', 'source': 'plugin', 'storageScope': 'personal',
                  'userInvocable': True, 'description': 'Plugin skill'}
        for rows in ([personal, project, plugin], [plugin, project, personal]):
            with self.subTest(order=[row['source'] for row in rows]):
                client.completion_inventory.return_value = {'skills': rows}
                result = self.commands([], query='same', discovery=CompletionDiscovery(client))
                self.assertEqual(['/plugin:same'], [row['invocation'] for row in result['items']])
        personal['userInvocable'], project['userInvocable'] = True, False
        client.completion_inventory.return_value = {'skills': [project, personal, plugin]}
        result = self.commands([], query='same', discovery=CompletionDiscovery(client))
        self.assertEqual(['/same', '/plugin:same'], [row['invocation'] for row in result['items']])
        self.assertEqual('Personal skill', result['items'][0]['description'])

    def test_unknown_same_scope_visibility_collision_waits_for_runtime_report(self):
        client = Mock()
        client.completion_inventory.return_value = {'skills': [
            {'invocation': 'same', 'storageScope': 'project', 'userInvocable': True},
            {'invocation': 'same', 'storageScope': 'project', 'userInvocable': False}]}
        self.assertEqual([], self.commands([], discovery=CompletionDiscovery(client))['items'])
        result = self.commands(['same'], discovery=CompletionDiscovery(client))
        self.assertEqual(['/same'], [row['invocation'] for row in result['items']])
        self.assertEqual('runtime', result['items'][0]['source'])

    def test_plugin_installation_scope_does_not_choose_an_unreported_version(self):
        client = Mock()
        client.completion_inventory.return_value = {'skills': [
            {'invocation': 'plugin:same', 'source': 'plugin', 'pluginNamespace': 'plugin',
             'storageScope': 'personal', 'userInvocable': True, 'description': 'User install'},
            {'invocation': 'plugin:same', 'source': 'plugin', 'pluginNamespace': 'plugin',
             'storageScope': 'project', 'userInvocable': False, 'description': 'Project install'}]}
        self.assertEqual([], self.commands([], discovery=CompletionDiscovery(client))['items'])
        client.completion_inventory.return_value['skills'][1]['userInvocable'] = True
        result = self.commands([], discovery=CompletionDiscovery(client))
        self.assertEqual(1, len(result['items']))
        self.assertIn('같은 호출명', result['items'][0]['description'])
        result = self.commands([{'name': 'plugin:same', 'description': 'Actual install'}],
                               discovery=CompletionDiscovery(client))
        self.assertEqual('Actual install', result['items'][0]['description'])
        self.assertEqual('runtime', result['items'][0]['source'])

    def test_exact_installed_match_outranks_broad_runtime_matches_before_trimming(self):
        client = Mock()
        client.completion_inventory.return_value = {'skills': [
            {'invocation': 'report', 'storageScope': 'personal', 'userInvocable': True}]}
        result = self.commands([f'archive:old-report-{i}' for i in range(70)], query='report',
                               max_items=1, discovery=CompletionDiscovery(client))
        self.assertEqual(['/report'], [row['invocation'] for row in result['items']])
        self.assertTrue(result['limited'])

    def test_live_skill_addition_and_removal_refresh_after_ttl_without_keystroke_scans(self):
        from local_app.claude_inventory import ClaudeInventory
        client = ClaudeInventory(config=self.base / 'config')
        discovery = CompletionDiscovery(client, ttl=5)
        with patch.object(client, 'completion_inventory', wraps=client.completion_inventory) as read, \
                patch('local_app.completions.time.monotonic', return_value=0):
            self.assertEqual([], self.commands(['compact'], query='mumu', discovery=discovery)['items'])
            path = self.root / '.claude/skills/mumu-pocket-launch/SKILL.md'
            path.parent.mkdir(parents=True)
            path.write_text('---\nname: mumu-pocket-launch\n---\nNever execute this body.', encoding='utf-8')
            self.assertEqual([], self.commands(['compact'], query='mumu', discovery=discovery)['items'])
            self.assertEqual(1, read.call_count)
        with patch('local_app.completions.time.monotonic', return_value=6):
            result = self.commands(['compact'], query='mumu', discovery=discovery)
        self.assertEqual(['/mumu-pocket-launch'], [row['invocation'] for row in result['items']])
        path.unlink()
        with patch('local_app.completions.time.monotonic', return_value=12):
            self.assertEqual([], self.commands(['compact'], query='mumu', discovery=discovery)['items'])

    def test_live_discovery_read_failure_keeps_runtime_rows_and_reports_partial(self):
        client = Mock()
        client.completion_inventory.side_effect = OSError('PRIVATE details')
        result = self.commands(['compact'], discovery=CompletionDiscovery(client))
        self.assertEqual(['/compact'], [row['invocation'] for row in result['items']])
        self.assertEqual('runtime', result['items'][0]['source'])
        self.assertTrue(result['limited'])
        self.assertNotIn('PRIVATE', str(result))

    def test_installed_skill_cannot_override_supported_effort_session_control(self):
        client = Mock()
        client.completion_inventory.return_value = {'skills': [
            {'invocation': 'effort', 'storageScope': 'personal', 'userInvocable': True}]}
        self.commands([])
        self.item['connection']['capabilities'] = {'setEffort': True}
        result = complete(self.item, {'kind': 'slash', 'query': 'eff'}, discovery=CompletionDiscovery(client))
        self.assertEqual(1, len(result['items']))
        self.assertEqual('workspace', result['items'][0]['source'])
        self.assertEqual('/effort', result['items'][0]['invocation'])

    def test_discovery_cache_coalesces_queries_and_isolates_contexts_and_expiration(self):
        client = Mock()
        client.completion_inventory.side_effect = lambda workspace: {'skills': [], 'context': workspace}
        discovery = CompletionDiscovery(client, ttl=5, max_contexts=2)
        with patch('local_app.completions.time.monotonic', return_value=0):
            first = discovery.inventory(None)
            first['skills'].append('should-not-leak')
            self.assertEqual([], discovery.inventory(None)['skills'])
            discovery.inventory('folder-a')
            discovery.inventory(None)
            discovery.inventory('folder-b')
            self.assertEqual(2, len(discovery._cache))
            discovery.inventory('folder-a')  # a was evicted when b was inserted.
        self.assertEqual(4, client.completion_inventory.call_count)
        with patch('local_app.completions.time.monotonic', return_value=6):
            discovery.inventory('folder-a')
        self.assertEqual(5, client.completion_inventory.call_count)

    def test_discovery_errors_are_bounded_and_do_not_expose_paths_or_error_text(self):
        client = Mock()
        client.completion_inventory.side_effect = ValueError('SECRET path')
        result = complete({}, {'kind': 'slash'}, discovery=CompletionDiscovery(client))
        self.assertEqual([], result['items'])
        self.assertTrue(result['limited'])
        self.assertNotIn('SECRET', str(result))


if __name__ == '__main__':
    unittest.main()
