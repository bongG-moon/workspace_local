"""Exercise path recovery through the loopback settings API with an offline Claude fixture."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from local_app.claude_shell import installed_shell
from local_app.picker_protocol import read_result
from local_app.server import LocalApp, Server
from tests import test_workspace_picker as native_picker


@unittest.skipUnless(os.name == 'nt', 'Windows PowerShell fixture')
class ShellPathRouteTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.automatic, self.manual = self.root / '기본 설정', self.root / '사용할 설정'
        for folder in [self.automatic, self.manual]:
            folder.mkdir()
            (folder / 'settings.json').write_text('{"sample":"private fixture value"}', encoding='utf-8')
        self.entry, self.custom_entry = self.root / '기본.ps1', self.root / '다른 실행.ps1'
        for path in [self.entry, self.custom_entry]:
            path.write_text("""
if ($args[0] -eq '--version') { [Console]::WriteLine('2.1.326 (Claude Code)') }
elseif ($args[0] -eq '--help') { [Console]::WriteLine('--input-format --output-format --permission-prompt-tool') }
else {
    & $env:AX_FIXTURE_PYTHON -X utf8 $env:AX_FIXTURE_CLI @args
    exit $LASTEXITCODE
}
[IO.File]::AppendAllText(($PSCommandPath + '.seen'), ($env:CLAUDE_CONFIG_DIR + [Environment]::NewLine))
""", encoding='utf-8-sig')
        environment = patch.dict(os.environ, {
            'CLAUDE_CONFIG_DIR': str(self.automatic), 'COMPANY_AGENT_CLAUDE': str(self.entry),
            'COMPANY_WORKSPACE_SHELL': installed_shell('windows'), 'LLMOPS_DEMO': '1',
            'AX_FIXTURE_PYTHON': sys.executable,
            'AX_FIXTURE_CLI': str(Path(__file__).parent / 'fixtures' / 'workspace_fake_cli.py'),
        })
        environment.start()
        self.addCleanup(environment.stop)

    def start(self):
        self.running = ExitStack()
        self.addCleanup(self.running.close)
        self.app = LocalApp(self.root / 'state', managed_workspace_root=self.root / 'tasks')
        self.running.callback(self.app.close)
        self.server = Server(self.app)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.running.callback(self.worker.join, 2)
        self.running.callback(self.server.server_close)
        self.running.callback(self.server.shutdown)

    def request(self, route='/api/claude-shell', **fields):
        request = Request(self.server.origin + route,
                          headers={'Authorization': 'Bearer ' + self.app.token, 'Content-Type': 'application/json'},
                          data=json.dumps(fields).encode() if fields else None)
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read())

    def test_check_apply_restart_restore_and_legacy_requests(self):
        self.start()
        original = {'configRoot': str(self.automatic), 'entry': str(self.entry)}
        selected = {'configRoot': str(self.manual), 'entry': str(self.custom_entry)}
        paths = {'configDir': str(self.manual), 'executable': str(self.custom_entry)}
        self.assertEqual(original, self.request()['runtime'])
        checked = self.request(choice='default', checkOnly=True, **paths)
        self.assertTrue(checked['ok'])
        self.assertEqual(original, checked['runtime'])
        self.assertFalse(self.app.shell_preference.path.exists())
        observed = Path(str(self.custom_entry) + '.seen')
        self.assertEqual([str(self.manual)] * 2, observed.read_text(encoding='utf-8-sig').splitlines())
        applied = self.request(choice='default', **paths)
        self.assertEqual(selected, applied['runtime'])
        self.assertEqual(('default', str(self.manual), str(self.custom_entry)),
                         (applied['choice'], applied['configDir'], applied['executable']))
        self.assertEqual(self.manual, self.app.customizations.config)
        self.assertEqual(self.manual, self.app.completion_discovery.client.config)
        with patch('local_app.session_import.SessionImporter') as importer:
            self.app.import_sessions()
            importer.assert_called_once_with(self.manual)
        self.assertNotIn('private fixture value', json.dumps(applied))
        self.assertEqual(str(self.automatic), os.environ['CLAUDE_CONFIG_DIR'])

        # A missing target cannot replace the saved command or change live clients.
        saved = self.app.shell_preference.path.read_bytes()
        with self.assertRaises(HTTPError) as response:
            self.request(choice='default', executable=str(self.root / '없는 경로.exe'))
        self.assertEqual(400, response.exception.code)
        response.exception.close()
        self.assertEqual(saved, self.app.shell_preference.path.read_bytes())
        self.assertEqual(selected, self.request()['runtime'])
        self.assertEqual(selected, self.request(choice='default')['runtime'])

        # Close the old app before restarting on the same isolated state.
        self.running.close()
        self.start()
        self.assertEqual(selected, self.request()['runtime'])
        self.assertEqual(self.manual, self.app.inventory_client.config)
        self.app.reconnect()
        self.assertEqual(selected, self.request()['runtime'])
        only_folder = self.request(choice='default', executable='')
        self.assertEqual({'entry': str(self.entry), 'configRoot': str(self.manual)}, only_folder['runtime'])
        self.assertEqual(str(self.manual), only_folder['configDir'])
        restored = self.request(choice='default', configDir='')
        self.assertEqual(original, restored['runtime'])
        self.assertEqual(('', ''), (restored['configDir'], restored['executable']))
        self.assertEqual(self.automatic, self.app.customizations.config)

    def test_old_task_keeps_its_catalog_and_writer_until_its_connection_retires(self):
        from local_app.idle_connections import _prepare_release, _finish_release
        for config, name in [(self.automatic, 'before-switch'), (self.manual, 'after-switch')]:
            skill = config / 'skills' / name / 'SKILL.md'
            skill.parent.mkdir(parents=True)
            skill.write_text(f'---\nname: {name}\ndescription: fixture\n---\n', encoding='utf-8')
        self.start()
        work = self.root / '작업'
        work.mkdir()
        sid = self.app.create(str(work), True)['id']
        self.app.connect(sid, _catalog=True)
        item = self.app.get(sid)
        old = item['bridge']
        self.request(choice='default', configDir=str(self.manual), executable=str(self.custom_entry))
        newer = self.app.create(str(work), True)['id']
        self.app.connect(newer, _catalog=True)

        def settings(task):
            return self.request('/api/customizations?id=' + task + '&scope=user')

        with patch('local_app.customizations.Customizations.policy', return_value=[]):
            for task, skill in [(sid, 'before-switch'), (newer, 'after-switch')]:
                catalog = self.request('/api/capabilities?id=' + task + '&scope=common')
                self.assertEqual([skill], [row['name'] for row in catalog['installed']['skills']])
                completion = self.request('/api/completions', id=task, kind='slash', query='switch')
                self.assertEqual(['/' + skill], [row['invocation'] for row in completion['items']])
            original = settings(sid)
            self.assertEqual(['before-switch'], [row['name'] for row in original['skills']])
            self.assertEqual(['after-switch'], [row['name'] for row in settings(newer)['skills']])
            self.assertTrue(self.request('/api/customizations', id=sid, scope='user', kind='skills',
                                         name='before-switch', value='off', revision=original['revision'])['ok'])
            automatic = json.loads((self.automatic / 'settings.json').read_text(encoding='utf-8'))
            self.assertEqual('off', automatic['skillOverrides']['before-switch'])
            self.assertNotIn('skillOverrides', json.loads((self.manual / 'settings.json').read_text(encoding='utf-8')))
            self.assertIs(old, item['bridge'])
            self.assertFalse(old.closed)
            with self.app.lock:
                bridge, resumable = _prepare_release(self.app, item, object())
            self.assertTrue(_finish_release(self.app, item, bridge, resumable))
            self.assertTrue(old.closed)
            self.assertNotIn('_workspace_metadata', vars(old))
            self.app.connect(sid, _catalog=True)
            self.assertIsNot(old, item['bridge'])
            self.assertEqual(self.app.command, item['bridge'].command)
            self.assertEqual(['after-switch'], [row['name'] for row in settings(sid)['skills']])


class SettingsPickerResultTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt' and native_picker.POWERSHELL, 'Windows native picker')
    def test_config_and_executable_use_dedicated_single_select_native_dialogs(self):
        result = native_picker.PickerLifecycleTests.run_ps(self, """
Initialize-WorkspacePicker
$results = foreach ($kind in @('claude-config', 'claude-executable')) {
    $dialog = New-WorkspacePathDialog $kind
    try {
        @{folder=[bool]($dialog.NativeOptions -band 0x20); multiple=[bool]($dialog.NativeOptions -band 0x200)}
    } finally { $dialog.Dispose() }
}
ConvertTo-Json -InputObject @($results) -Compress
""")
        self.assertEqual([{'folder': True, 'multiple': False}, {'folder': False, 'multiple': False}], result)

    def test_config_and_command_are_single_selections_and_keep_unicode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            selected = root / '도구 폴더'
            selected.mkdir()
            executable = selected / '실행.cmd'
            executable.touch()
            result = root / 'result.json'
            for kind, good, wrong in [('claude-config', selected, executable),
                                      ('claude-executable', executable, selected)]:
                with self.subTest(kind=kind):
                    def write(items):
                        result.write_text(json.dumps({'version': 1, 'status': 'success',
                                                      'paths': list(map(str, items))}), encoding='utf-8')
                    write([good])
                    self.assertEqual([str(good)], read_result(result, kind)['paths'])
                    for items in ([wrong], [good, good]):
                        write(items)
                        with self.assertRaises(ValueError):
                            read_result(result, kind)
