"""Package the read-only WS-38 comparison tool; never include diagnostic output."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[2]
DIAGNOSTIC = ROOT / 'scripts/test-lab/ws38-diagnostic'
FILES = {
    name: DIAGNOSTIC / name
    for name in ('Check-Python.cmd', 'Check-Python.ps1', 'CheckPython.Native.cs', 'README.txt')
}
FILES.update({f'deploy/{name}': ROOT / 'deploy' / name for name in (
    'CompanyWorkspace.Startup.ps1', 'CompanyWorkspace.NormalToken.cs',
    'CompanyAgent.UserContext.ps1', 'Start-CompanyWorkspace.ps1')})


def package(output):
    payload = {name: path.read_bytes() for name, path in FILES.items()}
    manifest = {
        'diagnosticVersion': 'ws38-1',
        'targetWorkspaceVersion': '0.23.4',
        'sha256': {name: hashlib.sha256(data).hexdigest() for name, data in payload.items()},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    # New asset only: leave published application ZIPs and earlier tools intact.
    with zipfile.ZipFile(output, 'x', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in payload.items():
            archive.writestr('Company-Workspace-Python-Diagnostic/' + name, data)
        archive.writestr('Company-Workspace-Python-Diagnostic/manifest.json',
                         json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        assert len(archive.namelist()) == len(FILES) + 1
        for name, data in payload.items():
            assert archive.read('Company-Workspace-Python-Diagnostic/' + name) == data
    return {'path': str(output), 'bytes': output.stat().st_size,
            'sha256': hashlib.sha256(output.read_bytes()).hexdigest(), 'files': len(FILES) + 1}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'dist/Company-Workspace-0.23.4-python-diagnostic-1.zip')
    print(json.dumps(package(parser.parse_args().output), indent=2))
