"""Build an isolated, unsigned usage-connection test EXE from the working source.

No user KEY or server URL is bundled. The test app stores only its URL in its
own state folder. It never installs over or updates the regular AX Workspace.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def build_env():
    # Windows PowerShell launched by Python must not import PowerShell 7's
    # same-named built-in modules from an inherited PSModulePath.
    return {**os.environ, 'PSModulePath': str(Path(os.environ['WINDIR'])/'System32/WindowsPowerShell/v1.0/Modules')}


def prepare(destination, revision=1):
    destination.mkdir(parents=True, exist_ok=False)
    names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    names += ['local_app/llm_usage_config.py', 'scripts/prepare-workspace-llm-usage.py',
              'scripts/test-lab/build-usage-test.py']
    for name in sorted(set(filter(None, names))):
        source = ROOT/name
        if not source.is_file():
            continue
        target = destination/name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)

    def patch(name, old, new):
        path = destination/name
        text = path.read_text(encoding='utf-8-sig')
        if old not in text:
            raise ValueError('Test build patch target missing: ' + name)
        path.write_text(text.replace(old, new), encoding='utf-8-sig' if name.endswith(('.cs','.ps1')) else 'utf-8')

    patch('local_app/server.py', 'UsageService(demo=demo or os.environ.get("LLMOPS_DEMO") == "1")',
          'UsageService(test_settings=state / "llm-usage-test.json")')
    patch('local_app/server.py', '        app.app_updates.start()', '        # Usage test edition: no update scheduler.')
    for arguments in ('', 'manual=True', 'startup=True'):
        patch('local_app/server.py', 'app.app_updates.check(' + arguments + ')', 'app.app_updates.snapshot()')
    patch('local_app/server.py', "        if route == '/api/app-update':",
          "        if route == '/api/app-update' and data.get('action') == 'install':\n"
          "            raise ValueError('연결 테스트 앱은 자동 업데이트하지 않습니다.')\n"
          "        if route == '/api/app-update':")
    patch('deploy/CompanyWorkspace.Standalone.cs', 'Options options = ParseOptions(args);', '''Options options = ParseOptions(args);
                if (!options.VerifyOnly) {
                    options.StateRoot = options.StateRoot ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "CompanyAgent", "llm-usage-test", "state");
                    options.CacheRoot = options.CacheRoot ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "CompanyAgent", "llm-usage-test", "runtime");
                }''')
    patch('deploy/CompanyWorkspace.Standalone.cs', 'AX.Workspace.Desktop', 'AX.Workspace.UsageTest')
    patch('deploy/Start-CompanyWorkspace.ps1', "'Local\\CompanyWorkspace-'", "'Local\\CompanyWorkspaceUsageTest-'")
    path = destination/'deploy/Start-CompanyWorkspace.ps1'
    text = path.read_text(encoding='utf-8-sig')
    path.write_text('\n'.join(('# Test edition: ' + line) if 'if (Invoke-WorkspaceManagedUpdate -State' in line else line
                              for line in text.split('\n')), encoding='utf-8-sig')
    for name in ('local_app/web/index.html', 'deploy/Workspace.Desktop.cs', 'local_app/attention.py',
                 'deploy/New-WorkspaceStandalone.ps1'):
        patch(name, 'AX Workspace', 'AX Workspace LLM Test')
    patch('deploy/Workspace.Desktop.cs', 'AX.Workspace.Desktop', 'AX.Workspace.UsageTest')
    if revision > 1:
        # Each diagnostic revision has its own mutex/state so an older test
        # process cannot intercept the new executable and display stale UI.
        patch('deploy/CompanyWorkspace.Standalone.cs', '"llm-usage-test"', '"llm-usage-test-' + str(revision) + '"')
        patch('deploy/Start-CompanyWorkspace.ps1', 'CompanyWorkspaceUsageTest-', 'CompanyWorkspaceUsageTest' + str(revision) + '-')
        for name in ('deploy/CompanyWorkspace.Standalone.cs', 'deploy/Workspace.Desktop.cs'):
            patch(name, 'AX.Workspace.UsageTest', 'AX.Workspace.UsageTest' + str(revision))
        for name in ('local_app/web/index.html', 'deploy/Workspace.Desktop.cs', 'local_app/attention.py', 'deploy/New-WorkspaceStandalone.ps1'):
            patch(name, 'AX Workspace LLM Test', 'AX Workspace LLM Test ' + str(revision))
        patch('local_app/web/index.html', '>연결 테스트</span>', '>연결 테스트 · ' + str(revision) + '</span>')
    # Version sync also refreshes the reviewed startup-helper checksums after
    # the test-only mutex/update-isolation patches above.
    powershell = str(Path(__import__('os').environ['WINDIR'])/'System32/WindowsPowerShell/v1.0/powershell.exe')
    subprocess.run([powershell, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                    str(destination/'scripts/Sync-WorkspaceVersion.ps1')], cwd=destination, check=True, env=build_env())
    sdk = ROOT/'build/desktop-sdk'
    if sdk.is_dir():
        for package in sdk.glob('*.nupkg'):
            target = destination/'build/desktop-sdk'/package.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(package, target)
    return powershell


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--revision', type=int, choices=range(1,100), default=1)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise SystemExit('Choose a new output folder; existing files are not replaced.')
    source = output/'source'
    powershell = prepare(source, args.revision)
    subprocess.run([powershell, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                    str(source/'deploy/New-WorkspaceStandalone.ps1'), '-ConfigPython', sys.executable,
                    '-OutputDirectory', str(output/'build'), '-VerificationDirectory', str(output/'verified')],
                   cwd=source, check=True, env=build_env())
    exes = list((output/'build').glob('AX-Workspace-*.exe'))
    if len(exes) != 1:
        raise RuntimeError('Expected one verified test executable.')
    target = output/(exes[0].stem + '-LLM-Test' + (str(args.revision) if args.revision > 1 else '') + '.exe')
    shutil.copyfile(exes[0], target)
    import hashlib
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix('.exe.sha256').write_text(digest + '  ' + target.name + '\n', encoding='utf-8')
    print(json.dumps({'exe':str(target), 'sha256':digest, 'isolatedState':True, 'automaticUpdates':False}, indent=2))


if __name__ == '__main__':
    main()
