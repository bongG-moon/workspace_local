"""The source-only command line needs no GUI, Git sync or implicit SDK network."""
from contextlib import redirect_stdout, redirect_stderr
import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from workspace_publisher import __main__ as cli
from workspace_publisher.config import DEFAULTS, PublisherError


ROOT = Path(__file__).resolve().parents[1]
SDK = {"status": "ready", "version": "1.0.4258.31", "message": "준비 완료"}
ARCHIVE = {"version": "0.23.1", "sourceKind": "archive", "sourceId": "b" * 64,
           "commit": "", "branch": "", "releaseTag": "", "canSync": False}


class PublisherCliTests(unittest.TestCase):
    def setUp(self):
        self.publisher = Mock()
        self.publisher.load_config.return_value = dict(DEFAULTS)
        self.publisher.inspect_source.return_value = dict(ARCHIVE)
        self.publisher.save_config.side_effect = lambda config: config
        self.publisher.publish_source.return_value = {'verified': True, 'commit': 'c' * 40}
        self.factory = patch.object(cli, "Publisher", return_value=self.publisher)
        self.factory.start()
        self.addCleanup(self.factory.stop)

    def invoke(self, args):
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli.main(["--repo", str(ROOT), *args])
        return code, output.getvalue()

    def test_sdk_status_only_checks_local_state(self):
        with patch("workspace_publisher.sdk.inspect_sdk", return_value=SDK) as inspect, \
                patch("workspace_publisher.sdk.download_sdk") as download:
            code, output = self.invoke(["sdk-status"])
        self.assertEqual(0, code)
        self.assertIn("ready", output)
        inspect.assert_called_once_with(ROOT)
        download.assert_not_called()
        self.publisher.load_config.assert_not_called()
        self.publisher.save_config.assert_not_called()

    def test_import_uses_explicit_local_file_without_config_mutation(self):
        with patch("workspace_publisher.sdk.import_sdk", return_value=SDK) as prepare:
            code, _ = self.invoke(["sdk-import", "--file", "C:/approved/sdk.nupkg"])
        self.assertEqual(0, code)
        self.assertEqual((ROOT, Path("C:/approved/sdk.nupkg")), prepare.call_args.args)
        self.publisher.save_config.assert_not_called()

    def test_download_url_is_explicit_and_never_saved(self):
        for address in (None, "https://packages.example/sdk.nupkg"):
            with self.subTest(address=address), patch("workspace_publisher.sdk.download_sdk", return_value=SDK) as prepare:
                code, _ = self.invoke(["sdk-download", *(["--url", address] if address else [])])
                self.assertEqual(0, code)
                self.assertEqual((ROOT, address), prepare.call_args.args)
        self.publisher.save_config.assert_not_called()

    def test_invalid_sdk_url_returns_safe_error(self):
        with patch("workspace_publisher.sdk.download_sdk", side_effect=PublisherError("토큰 없는 HTTPS 주소를 사용해 주세요.")):
            code, output = self.invoke(["sdk-download", "--url", "https://example/sdk?token=secret"])
        self.assertEqual(1, code)
        self.assertIn("HTTPS", output)
        self.assertNotIn("secret", output)

    def test_zip_configure_skips_git_remote_and_release_prompts(self):
        prompts = []
        values = iter(["https://gitlab.example", "42"])
        def answer(prompt):
            prompts.append(prompt)
            return next(values)
        with patch("builtins.input", side_effect=answer):
            code, output = self.invoke(["configure"])
        self.assertEqual(0, code)
        self.assertIn("Download ZIP", output)
        self.assertNotIn("SSH", " ".join(prompts))
        self.assertNotIn("릴리스 태그", " ".join(prompts))
        saved = self.publisher.save_config.call_args.args[0]
        self.assertEqual("", saved["releaseTag"])
        self.assertEqual("42", saved["projectId"])
        self.assertEqual("https://gitlab.example", saved["baseUrl"])
        self.assertEqual(2, len(prompts))

    def test_http_configure_rejected_before_save(self):
        with patch("builtins.input", side_effect=["http://gitlab.example", "42"]):
            code, _ = self.invoke(["configure"])
        self.assertEqual(1, code)
        self.publisher.save_config.assert_not_called()

    def test_deploy_prepares_verified_build_then_publishes_with_runtime_only_token(self):
        build = {'version': '0.23.2', 'files': []}
        self.publisher.prepare_deploy.return_value = build
        self.publisher.publish.return_value = {'verified': True}
        with patch('getpass.getpass', return_value='fixture-private-token'):
            code, output = self.invoke(['deploy'])
        self.assertEqual(0, code)
        self.publisher.prepare_deploy.assert_called_once()
        self.assertEqual(build, self.publisher.publish.call_args.args[1])
        self.assertEqual('fixture-private-token', self.publisher.publish.call_args.args[2])
        self.assertNotIn('fixture-private-token', output)

    def test_deploy_without_token_does_not_build(self):
        with patch('getpass.getpass', return_value=''):
            code, _ = self.invoke(['deploy'])
        self.assertEqual(1, code)
        self.publisher.prepare_deploy.assert_not_called()

    def test_source_only_uses_token_without_building_or_publishing_packages(self):
        with patch('getpass.getpass', return_value='glpat-fixture'):
            code, output = self.invoke(['source-publish'])
        self.assertEqual(0, code)
        self.publisher.publish_source.assert_called_once()
        self.publisher.prepare_deploy.assert_not_called()
        self.publisher.publish.assert_not_called()
        self.assertNotIn('glpat-fixture', output)

    def test_source_failure_does_not_advance_package_channel(self):
        self.publisher.publish_source.side_effect = PublisherError('기본 브랜치 쓰기 권한이 없습니다.')
        with patch('getpass.getpass', return_value='glpat-fixture'):
            code, output = self.invoke(['deploy'])
        self.assertEqual(1, code)
        self.assertIn('브랜치', output)
        self.publisher.publish.assert_not_called()

    def test_package_only_config_skips_source_write(self):
        self.publisher.load_config.return_value = {**DEFAULTS, 'includeSource': False}
        self.publisher.publish.return_value = {'verified': True}
        with patch('getpass.getpass', return_value='fixture-token'):
            code, _ = self.invoke(['deploy'])
        self.assertEqual(0, code)
        self.publisher.publish_source.assert_not_called()
        self.publisher.publish.assert_called_once()

    def test_missing_or_misplaced_sdk_arguments_fail_before_action(self):
        for args in (["sdk-import"], ["status", "--url", "https://example"], ["build", "--file", "sdk.nupkg"]):
            with self.subTest(args=args), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.invoke(args)

    def test_cli_entry_does_not_import_or_launch_tk(self):
        spec = importlib.util.spec_from_file_location("publisher_cli_entry_fixture", ROOT / "Publish-Workspace.py")
        entry = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(entry)
        with patch.object(entry.sys, "argv", ["Publish-Workspace.py", "--cli", "sdk-status"]), \
                patch("workspace_publisher.__main__.main", return_value=0) as launch:
            self.assertEqual(0, entry.main())
        launch.assert_called_once_with(["sdk-status"])

    def test_missing_tk_has_actionable_cli_fallback(self):
        from workspace_publisher.gui import main
        output = io.StringIO()
        with patch.dict("sys.modules", {"tkinter": None}), redirect_stdout(output):
            self.assertEqual(1, main(ROOT))
        self.assertIn("--cli --help", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())


if __name__ == "__main__":
    unittest.main()
