"""Release note defaults and one-click publication preserve retry bytes."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests import test_workspace_publisher_core as fixtures
from workspace_publisher.config import PublisherError, atomic_json
from workspace_publisher.core import Publisher


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='publisher-workflow-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.publisher = Publisher(self.root)
        self.version = fixtures.VERSION
        self.source(self.version)
        self.notes(self.version)

    def source(self, version):
        path = self.root / 'local_app/server.py'
        path.parent.mkdir(exist_ok=True)
        path.write_text(f'WORKSPACE_VERSION = "{version}"\n', encoding='utf-8')

    def notes(self, version):
        atomic_json(self.root / 'deploy/workspace-release-notes.json', {
            'schema': 1, 'currentVersion': version, 'releases': [
                {'version': version, 'title': '자동 제목 ' + version, 'notes': '이번 변경 내용'}]})

    def saved_build(self):
        stage = self.publisher.work_root / ('build-' + 'a' * 32)
        result = fixtures.release_files(stage / 'release')
        result['sourceRoot'] = str(stage / 'source')
        atomic_json(stage / 'build-result.json', result)
        atomic_json(self.publisher.work_root / 'last-build.json', result)
        return result

    def test_notes_load_offline_and_preserve_same_version_edits_including_blank(self):
        with patch('subprocess.run', side_effect=AssertionError('no external process')):
            config = self.publisher.load_config()
            self.assertEqual('자동 제목 ' + self.version, config['title'])
            self.assertEqual(self.version, config['notesVersion'])
            for text in ('사내에서 수정한 내용', ''):
                self.publisher.save_config({**config, 'notes': text})
                self.assertEqual(text, Publisher(self.root).load_config()['notes'])

    def test_new_version_refreshes_notes_and_keeps_target(self):
        config = self.publisher.load_config()
        self.publisher.save_config({**config, 'baseUrl': 'https://gitlab.example', 'projectId': '42', 'notes': '이전 편집'})
        self.source('0.23.2')
        self.notes('0.23.2')
        actual = self.publisher.load_config()
        self.assertEqual('이번 변경 내용', actual['notes'])
        self.assertEqual('0.23.2', actual['notesVersion'])
        self.assertEqual('42', actual['projectId'])

    def test_legacy_notes_known_to_be_old_refresh_from_tag_or_saved_build(self):
        for reference in ('tag', 'build'):
            with self.subTest(reference=reference):
                config = {**fixtures.CONFIG, 'releaseTag': 'v0.23.0' if reference == 'tag' else ''}
                atomic_json(self.publisher.config_path, config)
                if reference == 'build':
                    self.saved_build()
                self.source('0.23.2')
                self.notes('0.23.2')
                actual = self.publisher.load_config()
                self.assertEqual('이번 변경 내용', actual['notes'])
                self.assertEqual('0.23.2', actual['notesVersion'])

    def test_bad_notes_do_not_prevent_loading_target_or_saving_retry_settings(self):
        self.publisher.save_config(fixtures.CONFIG)
        self.source('0.23.2')
        (self.root / 'deploy/workspace-release-notes.json').write_text('{}')
        with self.assertRaises(PublisherError):
            self.publisher.release_notes()
        self.assertEqual('42', self.publisher.load_config()['projectId'])
        self.publisher.save_config(self.publisher.load_config())

    def test_retry_reuses_exact_files_after_restart_and_note_or_token_kind_change(self):
        saved = self.saved_build()
        before = {path.name: path.read_bytes() for path in Path(saved['directory']).iterdir()}
        restarted = Publisher(self.root)
        with patch.object(restarted, 'build', side_effect=AssertionError('must not rebuild')):
            result = restarted.prepare_deploy({**fixtures.CONFIG, 'notes': '편집', 'tokenKind': 'access'})
        self.assertEqual(saved, result)
        self.assertEqual(before, {path.name: path.read_bytes() for path in Path(saved['directory']).iterdir()})

    def test_corrupt_retry_file_is_not_silently_rebuilt(self):
        saved = self.saved_build()
        (Path(saved['directory']) / saved['files'][0]['name']).write_bytes(b'changed')
        with patch.object(self.publisher, 'build') as build, self.assertRaises(PublisherError):
            self.publisher.prepare_deploy(fixtures.CONFIG)
        build.assert_not_called()

    def test_malformed_metadata_or_changed_saved_pointer_is_not_silently_rebuilt(self):
        saved = self.saved_build()
        for broken in ({}, {**saved, 'version': '0.99.0'}, {**saved, 'runtimeConfig': {}}, []):
            with self.subTest(broken=broken):
                atomic_json(self.publisher.work_root / 'last-build.json', broken)
                with patch.object(self.publisher, 'build') as build, self.assertRaises(PublisherError):
                    self.publisher.prepare_deploy(fixtures.CONFIG)
                build.assert_not_called()

    def test_new_version_or_new_target_builds_instead_of_publishing_old_files(self):
        self.saved_build()
        with patch.object(self.publisher, 'build', return_value={'new': True}) as build:
            self.source('0.23.2')
            self.assertEqual({'new': True}, self.publisher.prepare_deploy(fixtures.CONFIG))
            build.assert_called_once()
        self.source(self.version)
        with patch.object(self.publisher, 'build', return_value={'new': True}) as build:
            self.publisher.prepare_deploy({**fixtures.CONFIG, 'projectId': '99'})
            build.assert_called_once()

    def test_unreadable_source_still_allows_verified_existing_build(self):
        saved = self.saved_build()
        (self.root / 'local_app/server.py').unlink()
        with patch.object(self.publisher, 'build', side_effect=AssertionError('must not rebuild')):
            self.assertEqual(saved, self.publisher.prepare_deploy(fixtures.CONFIG))


if __name__ == '__main__':
    unittest.main()
