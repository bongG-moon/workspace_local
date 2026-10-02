"""Source publication stays tied to the original, verified deployment source."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from workspace_publisher.core import Publisher
from workspace_publisher.config import PublisherError, normalize
from workspace_publisher.source_archive import MANIFEST, manifest_bytes


class SourceWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.files = {'.gitignore': b'build/\ndist/\n',
                      'local_app/server.py': b'WORKSPACE_VERSION = "0.23.4"\n'}
        for name, raw in self.files.items():
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        (self.root / MANIFEST).write_bytes(manifest_bytes(self.files))
        self.publisher = Publisher(self.root)
        self.config = normalize({'baseUrl': 'https://gitlab.example.test', 'projectId': '42'})

    def test_archive_source_publishes_without_git_build_or_configuration_injection(self):
        before = {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with patch('workspace_publisher.source_publish.publish_source', return_value={'verified': True}) as publish, \
                patch('workspace_publisher.git_sync.git', side_effect=AssertionError('No Git')):
            self.assertEqual({'verified': True}, self.publisher.publish_source(self.config, 'glpat-fixture'))
        self.assertEqual(self.root, publish.call_args.args[0])
        self.assertEqual(self.publisher.inspect_source()['sourceId'], publish.call_args.kwargs['expected_source_id'])
        self.assertEqual(before, {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_source_must_match_the_verified_build_before_remote_write(self):
        source = self.publisher.inspect_source()
        for identity, version in [('f' * 64, source['version']), (source['sourceId'], '0.23.0')]:
            build = {'sourceId': identity, 'version': version}
            with self.subTest(build=build), patch.object(self.publisher, '_validated_build'), \
                    patch('workspace_publisher.source_publish.publish_source') as publish, self.assertRaises(PublisherError):
                self.publisher.publish_source(self.config, 'glpat-fixture', build_result=build)
            publish.assert_not_called()

    def test_changed_or_unverified_source_cannot_reach_gitlab(self):
        (self.root / 'local_app/server.py').write_bytes(b'changed')
        with patch('workspace_publisher.source_publish.publish_source') as publish, self.assertRaises(PublisherError):
            self.publisher.publish_source(self.config, 'glpat-fixture')
        publish.assert_not_called()

    def test_checkbox_default_and_strict_boolean_persist_without_runtime_credentials(self):
        self.assertTrue(self.config['includeSource'])
        self.assertFalse(normalize({**self.config, 'includeSource': False})['includeSource'])
        for value in ('false', 0, None):
            with self.subTest(value=value), self.assertRaises(PublisherError):
                normalize({**self.config, 'includeSource': value})


if __name__ == '__main__':
    unittest.main()
