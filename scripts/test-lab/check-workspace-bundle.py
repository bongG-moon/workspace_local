"""Verify the portable UI bundle matches current source and contains no state."""
import argparse
import hashlib
from pathlib import Path
import re
import sys
import zipfile

root = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser()
parser.add_argument("bundle", type=Path)
parser.add_argument("--update-config", type=Path, help="Expected non-secret GitLab settings injected during packaging")
args = parser.parse_args()
count = 0
expected = {"docs/WORKSPACE_0.16.0_PRODUCTIVITY.md", "docs/WORKSPACE_0.17.0_RELIABILITY.md","Company-Workspace.vbs", "deploy/Start-CompanyWorkspace.ps1", "docs/LOCAL_WORKSPACE.md", "docs/WORKSPACE_USER_GUIDE.html", "docs/WORKSPACE_0.15.0_CLAUDE.md",
            "docs/WORKSPACE_0.13.0_DESKTOP.md", "docs/WORKSPACE_0.14.0_CONTINUITY.md",
            "Check-Workspace.cmd", "Check-Workspace.ps1", "docs/WORKSPACE_STARTUP_DIAGNOSTIC.md",
            "docs/WORKSPACE_0.12.1_COMPATIBILITY.md", "docs/WORKSPACE_0.12.2_INPUT.md", "docs/WORKSPACE_0.12.3_AUTOCOMPLETE.md", "docs/WORKSPACE_0.12.4_CONTROLS.md", "docs/WORKSPACE_STANDALONE_EXE.md",
            "deploy/CompanyWorkspace.Startup.ps1", "deploy/CompanyWorkspace.NormalToken.cs",
            "deploy/CompanyAgent.UserContext.ps1",
                                                                        "local_app/__init__.py", "local_app/bridge.py", "local_app/server.py", "local_app/demo.py",
            "local_app/claude_inventory.py", "local_app/session_order.py", "local_app/file_diff.py", "local_app/conversation_fork.py", "local_app/history.py", "local_app/artifacts.py", "local_app/capabilities.py", "local_app/skill_inventory.py", "local_app/html_preview.py",             "local_app/Pick-Path.ps1", "local_app/WorkspacePicker.cs", "local_app/Invoke-TerminalClaude.ps1",
            "local_app/startup.py", "local_app/picker_protocol.py", "local_app/windows_paths.py",
            "local_app/windows_process.py", "local_app/windows_job.py", "local_app/owned_process.py", "local_app/picker_channel.py",
            "local_app/choices.py", "local_app/hook_status.py", "local_app/permission_contract.py",
            "local_app/external_apps.py", "local_app/completions.py", "local_app/attention.py", "local_app/app_window.py",
            "local_app/session_import.py", "local_app/desktop_notifications.py", "local_app/window_theme.py", "local_app/tray.py", "local_app/attachments.py", "local_app/work_queue.py", "local_app/schedule_time.py", "local_app/app_dispatch.py",
            "local_app/web/fonts/NotoSansKR-Variable.woff", "local_app/web/fonts/OFL.txt", "local_app/web/fonts/SOURCE.json",
            "local_app/web/index.html", "local_app/web/app.css", "local_app/web/app.js", "local_app/web/capabilities.js",
            "local_app/web/composer.js", "local_app/web/inline-controls.js", "local_app/web/attention.js",
            "local_app/web/desktop.js", "local_app/web/session-import.js", "local_app/web/rendering.js", "local_app/web/attachments.js", "local_app/web/workflow.js", "local_app/web/productivity.js", "local_app/web/palette.js", "local_app/web/layout.js", "local_app/web/productivity.css", "local_app/web/review.css",
            "local_app/web/icon.svg", "local_app/web/app-icon.ico",
            "local_app/web/app-icon-192.png", "local_app/web/app-icon-512.png"}
expected.update({'local_app/drafts.py', 'local_app/idle_connections.py', 'local_app/web/drafts.js',
                 'local_app/web/attachment-storage.js', 'local_app/web/attachment-storage.css',
                 'local_app/web/archived-tasks.js', 'local_app/web/archived-tasks.css'})
expected.update({'local_app/appearance.py', 'local_app/managed_launcher.py',
                 'local_app/web/appearance.js', 'local_app/web/appearance.css'})
seen = set()
expected.update({'local_app/app_updates.py', 'local_app/update_install.py',
                 'local_app/web/app-updates.js', 'local_app/web/app-updates.css',
                 'docs/WORKSPACE_APP_UPDATES.md', 'docs/WORKSPACE_GITLAB_PUBLISHER.md'})
expected.update({'local_app/session_visibility.py', 'local_app/path_browser.py', 'local_app/web/path-picker.js', 'local_app/web/path-picker.css'})
expected.update({'local_app/tool_activity.py', 'local_app/web/tool-activity.js', 'local_app/web/tool-activity.css'})
expected.update({'local_app/progress_log.py', 'local_app/web/progress-view.js', 'local_app/web/progress-view.css'})
expected.update({'local_app/upgrade_handoff.py', 'local_app/upgrade_launcher.py', 'local_app/web/upgrade-handoff.js', 'local_app/web/upgrade-handoff.css'})
expected.update({'local_app/web/input-keys.js', 'local_app/web/chat-shortcuts.js', 'local_app/web/chat-shortcuts.css'})
expected.update({'local_app/ui_health.py','local_app/web/startup-health.js','local_app/web/startup-health.css'})
expected.update({'local_app/native_window.py','deploy/Workspace.Desktop.cs','deploy/WebView2.lock.json',
                 'deploy/New-WorkspaceDesktop.ps1','deploy/CompanyWorkspace.Standalone.manifest',
                 'docs/WORKSPACE_0.18.0_NATIVE_WINDOW.md'})
expected.add('docs/WORKSPACE_0.20.0_NOTIFICATIONS.md')
expected.add('local_app/update_source.py')
expected.update({'local_app/execution_mode.py', 'local_app/web/execution-mode.js'})
expected.add('docs/WORKSPACE_EXECUTION_MODE.md')
expected.add('local_app/windows_peer.py')
expected.update({'local_app/file_preview.py','local_app/executions.py','local_app/web/rich-content.js','local_app/web/rich-content.css','local_app/web/execution-view.js','local_app/web/execution-view.css','docs/WORKSPACE_0.21.0_RICH_CHAT.md'})
generated = {'desktop/' + name for name in ('Workspace.Desktop.exe','Microsoft.Web.WebView2.Core.dll',
             'Microsoft.Web.WebView2.WinForms.dll','WebView2Loader.dll','WebView2-LICENSE.txt','WebView2-NOTICE.txt','desktop-build.json')}
expected.update(generated)
import json
configuration_bytes = None
if args.update_config is not None:
    sys.path.insert(0, str(root))
    from local_app.update_source import source_from_config
    configured = source_from_config(json.loads(args.update_config.read_text(encoding='utf-8-sig')))
    assert configured.provider == 'gitlab'
    configuration_bytes = (json.dumps(configured.config, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')
    expected.add('workspace-update-source.json')
desktop = json.loads((root/'build/desktop-host/desktop-build.json').read_text())
lock = json.loads((root/'deploy/WebView2.lock.json').read_text())
assert desktop['sdkSha256'] == lock['sha256'] and desktop['sdkVersion'] == lock['version']
assert set(desktop['files']) == {Path(name).name for name in generated} - {'desktop-build.json'}
for relative, digest in desktop['sources'].items():
    assert hashlib.sha256((root/relative).read_bytes()).hexdigest() == digest, relative
for name, digest in desktop['files'].items():
    assert hashlib.sha256((root/'build/desktop-host'/name).read_bytes()).hexdigest() == digest, name
with zipfile.ZipFile(args.bundle) as bundle:
    for entry in bundle.infolist():
        if entry.is_dir():
            continue
        name = entry.filename.replace("\\", "/")
        assert name.startswith("Company-Workspace/"), name
        relative = name.removeprefix("Company-Workspace/")
        assert relative in expected and relative not in seen, name
        seen.add(relative)
        assert ".." not in Path(relative).parts and not relative.startswith("/"), name
        assert not any(part in {"__pycache__", "history.json", "runtime.json", ".env", ".claude"} for part in Path(relative).parts), name
        if relative == 'workspace-update-source.json':
            assert configuration_bytes is not None and bundle.read(entry) == configuration_bytes, "Unexpected update settings"
        else:
            source = root / 'build/desktop-host' / Path(relative).name if relative in generated else root / relative
            assert source.is_file(), name
            assert bundle.read(entry) == source.read_bytes(), "Stale bundle content: " + name
        count += 1
    assert seen == expected, expected - seen
    assert {name for name in seen if name.startswith("docs/") and name.endswith(".html")} == {
        "docs/WORKSPACE_USER_GUIDE.html"}
    # Read-only diagnostics deliberately pin the reviewed startup helpers.
    # Keep the portable ZIP useful without executing the diagnostic or launcher.
    diagnostic = bundle.read("Company-Workspace/Check-Workspace.ps1").decode("utf-8-sig")
    pins = re.findall(r"'(deploy/[^']+)'\s*=\s*'([a-f0-9]{64})'", diagnostic)
    assert len(pins) == 4, "Expected four reviewed diagnostic startup helper pins."
    for relative, expected_hash in pins:
        source_text = bundle.read("Company-Workspace/" + relative).decode("utf-8-sig").replace("\r\n", "\n")
        assert hashlib.sha256(source_text.encode("utf-8")).hexdigest() == expected_hash, (
            "Startup diagnostic is incompatible with the bundled helper: " + relative)
print(f"Verified {count-len(generated)} source files and {len(generated)} pinned build assets; no runtime state or credentials bundled.")
print("SHA256: " + hashlib.sha256(args.bundle.read_bytes()).hexdigest())
