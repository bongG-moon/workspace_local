"""Verify the portable UI bundle matches current source and contains no state."""
import argparse
import hashlib
from pathlib import Path
import re
import zipfile

root = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser()
parser.add_argument("bundle", type=Path)
args = parser.parse_args()
count = 0
expected = {"Company-Workspace.vbs", "deploy/Start-CompanyWorkspace.ps1", "docs/LOCAL_WORKSPACE.md",
            "Check-Workspace.cmd", "Check-Workspace.ps1", "docs/WORKSPACE_STARTUP_DIAGNOSTIC.md",
            "docs/WORKSPACE_0.12.1_COMPATIBILITY.md", "docs/WORKSPACE_0.12.2_INPUT.md", "docs/WORKSPACE_0.12.3_AUTOCOMPLETE.md", "docs/WORKSPACE_0.12.4_CONTROLS.md", "docs/WORKSPACE_STANDALONE_EXE.md",
            "deploy/CompanyWorkspace.Startup.ps1", "deploy/CompanyWorkspace.NormalToken.cs",
            "deploy/CompanyAgent.UserContext.ps1",
            "docs/SKILL_PRIORITY.md",
            "docs/README.md", "docs/CLAUDE_CODE_BASICS.md", "docs/DESIGN_TERMS.md",
            "docs/COMPANY_AGENT_HANDBOOK.md", "docs/ONBOARDING_COURSE.md",
            "docs/Company-Agent-사용자-안내서.html",
            "docs/USER_GUIDE.md", "docs/CLAUDE_CODE_COMMANDS.md",
            "local_app/__init__.py", "local_app/bridge.py", "local_app/server.py", "local_app/demo.py",
            "local_app/companion.py", "local_app/harness_client.py", "local_app/history.py", "local_app/artifacts.py", "local_app/capabilities.py", "local_app/skill_inventory.py", "local_app/html_preview.py", "company-agent-plugin/resources/onboarding-course.json",
            "local_app/Pick-Path.ps1", "local_app/WorkspacePicker.cs", "local_app/Invoke-TerminalClaude.ps1",
            "local_app/startup.py", "local_app/picker_protocol.py", "local_app/windows_paths.py",
            "local_app/windows_process.py", "local_app/picker_channel.py",
            "local_app/choices.py", "local_app/hook_status.py", "local_app/permission_contract.py",
            "local_app/external_apps.py", "local_app/completions.py", "local_app/attention.py",
            "local_app/web/fonts/NotoSansKR-Variable.woff", "local_app/web/fonts/OFL.txt", "local_app/web/fonts/SOURCE.json",
            "local_app/web/index.html", "local_app/web/app.css", "local_app/web/app.js", "local_app/web/companion.js", "local_app/web/capabilities.js",
            "local_app/web/composer.js", "local_app/web/inline-controls.js", "local_app/web/attention.js",
            "local_app/web/icon.svg", "local_app/web/app-icon.ico",
            "local_app/web/app-icon-192.png", "local_app/web/app-icon-512.png"}
seen = set()
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
        source = root / relative
        assert source.is_file(), name
        assert bundle.read(entry) == source.read_bytes(), "Stale bundle content: " + name
        count += 1
    assert seen == expected, expected - seen
    assert {name for name in seen if name.startswith("docs/") and name.endswith(".html")} == {
        "docs/Company-Agent-사용자-안내서.html"}
    # Read-only diagnostics deliberately pin the reviewed startup helpers.
    # Keep the portable ZIP useful without executing the diagnostic or launcher.
    diagnostic = bundle.read("Company-Workspace/Check-Workspace.ps1").decode("utf-8-sig")
    pins = re.findall(r"'(deploy/[^']+)'\s*=\s*'([a-f0-9]{64})'", diagnostic)
    assert len(pins) == 4, "Expected four reviewed diagnostic startup helper pins."
    for relative, expected_hash in pins:
        source_text = bundle.read("Company-Workspace/" + relative).decode("utf-8-sig").replace("\r\n", "\n")
        assert hashlib.sha256(source_text.encode("utf-8")).hexdigest() == expected_hash, (
            "Startup diagnostic is incompatible with the bundled helper: " + relative)
print(f"Verified {count} source-identical files; no runtime state or credentials bundled.")
print("SHA256: " + hashlib.sha256(args.bundle.read_bytes()).hexdigest())
