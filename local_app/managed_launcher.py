"""Verified, per-user EXE entry points; never overwrite an arbitrary old EXE.

An old bootstrap may already have discarded its elevated token before opening
new app files. A new Windows shortcut is the explicit migration path; launching
that shortcut inherits Windows' chosen token. There is no UAC switch or poller.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import threading
import uuid
import zipfile

from . import update_install as install
from .owned_process import run_owned
from .windows_process import powershell_path
from .update_source import GITHUB_SOURCE

MAX_EXE_BYTES = 80 * 1024 * 1024
_LOCK = threading.RLock()


def unpack_archive(raw, version):
    """Accept the release's three fixed EXE ZIP members and embedded checksum."""
    install._version(version)
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_EXE_BYTES:
        raise ValueError('실행기 압축 파일의 크기를 확인하지 못했습니다.')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            items = archive.infolist()
            exe = next((f'{brand}-{version}.exe' for brand in ('AX-Workspace', 'Company-Workspace')
                        if f'{brand}-{version}.exe' in archive.namelist()), '')
            expected = {exe, exe + '.sha256', 'README.txt'}
            if len(items) != 3 or {item.filename for item in items} != expected:
                raise ValueError('실행기 압축 파일의 구성을 확인하지 못했습니다.')
            for item in items:
                limit = MAX_EXE_BYTES if item.filename == exe else 64 * 1024
                if (item.orig_filename != item.filename or item.flag_bits & 1
                        or not 0 < item.file_size <= limit
                        or stat.S_IFMT(item.external_attr >> 16) not in {0, stat.S_IFREG}
                        or item.external_attr & 0x400):
                    raise ValueError('실행기 압축 파일을 확인하지 못했습니다.')
            data = archive.read(exe)
            checksum = archive.read(exe + '.sha256').decode('utf-8-sig').strip()
            if checksum != hashlib.sha256(data).hexdigest() + '  ' + exe or not data.startswith(b'MZ'):
                raise ValueError('실행기 파일의 무결성을 확인하지 못했습니다.')
            return data
    except (zipfile.BadZipFile, UnicodeError, RuntimeError, NotImplementedError) as exc:
        raise ValueError('실행기 압축 파일을 확인하지 못했습니다.') from exc


def _record(version, data, source_identity):
    install._version(version)
    if not isinstance(data, bytes) or not 2 < len(data) <= MAX_EXE_BYTES or not data.startswith(b'MZ'):
        raise ValueError('실행기 파일을 확인하지 못했습니다.')
    digest = hashlib.sha256(data).hexdigest()
    if not isinstance(source_identity, str) or not install._HASH.fullmatch(source_identity):
        raise ValueError('실행기 배포 위치를 확인하지 못했습니다.')
    return {'schema': 1, 'version': version, 'sha256': digest, 'sourceIdentity': source_identity,
            'path': f'launchers/{version}-{digest[:16]}/Company-Workspace.exe'}


def _read(path):
    path = install._safe(path)
    if path.stat().st_size > 4096:
        raise ValueError('실행기 정보를 확인하지 못했습니다.')
    value = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=install._pairs)
    if (not isinstance(value, dict) or set(value) != {'schema', 'version', 'sha256', 'path', 'sourceIdentity'}
            or type(value['schema']) is not int or value['schema'] != 1
            or not isinstance(value['sha256'], str) or not install._HASH.fullmatch(value['sha256'])
            or not isinstance(value['sourceIdentity'], str) or not install._HASH.fullmatch(value['sourceIdentity'])):
        raise ValueError('실행기 정보를 확인하지 못했습니다.')
    install._version(value['version'])
    if value['path'] != f"launchers/{value['version']}-{value['sha256'][:16]}/Company-Workspace.exe":
        raise ValueError('실행기 경로를 확인하지 못했습니다.')
    return value


def _verified(root, value):
    path = install._safe(root / value['path'])
    if install._digest(path) != value['sha256']:
        raise ValueError('저장한 실행기 파일이 변경되었습니다.')
    return path


def stage(state, version, data, *, pending=False, cancel=None, source_identity=GITHUB_SOURCE.identity):
    value = _record(version, data, source_identity)
    with _LOCK:
        install._cancelled(cancel)
        root = install._root(state)
        path = install._safe(root / value['path'])
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            temporary = install._safe(path.parent / ('.exe-' + uuid.uuid4().hex))
            try:
                install._write_new(temporary, data)
                install._cancelled(cancel)
                os.replace(temporary, path)
            finally:
                if temporary.exists():
                    install._safe(temporary).unlink()
        _verified(root, value)
        install._cancelled(cancel)
        if pending:
            install._atomic_manifest(root / 'pending-launcher.json', value)
        return value


def _shortcut_bytes(target, arguments, *, cancel=None):
    """One finite COM helper; a job owns and retires it even on timeout."""
    environment = os.environ.copy()
    environment['WORKSPACE_LINK_TARGET'] = str(target)
    environment['WORKSPACE_LINK_ARGUMENTS'] = subprocess.list2cmdline(arguments)
    temporary = install._safe(target.parent / ('.workspace-' + uuid.uuid4().hex + '.lnk'))
    environment['WORKSPACE_LINK_TEMP'] = str(temporary)
    # The helper writes only a unique temporary link in the managed EXE folder.
    # Python verifies ownership and atomically installs the final named link.
    script = """[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
$ErrorActionPreference='Stop'; $temp=$null; $shell=$null; $link=$null
try {
 $desktop=[Environment]::GetFolderPath('DesktopDirectory')
 $temp=$env:WORKSPACE_LINK_TEMP
 $shell=New-Object -ComObject WScript.Shell; $link=$shell.CreateShortcut($temp)
 $link.TargetPath=$env:WORKSPACE_LINK_TARGET; $link.Arguments=$env:WORKSPACE_LINK_ARGUMENTS
 $link.WorkingDirectory=[IO.Path]::GetDirectoryName($env:WORKSPACE_LINK_TARGET)
 $link.IconLocation=$env:WORKSPACE_LINK_TARGET+',0'; $link.Description='AX Workspace managed launcher'; $link.Save()
 $bytes=[IO.File]::ReadAllBytes($temp)
 [Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
 @{desktop=$desktop;data=[Convert]::ToBase64String($bytes)} | ConvertTo-Json -Compress
} finally {
 if($temp -and [IO.File]::Exists($temp)){[IO.File]::Delete($temp)}
 if($link){[Runtime.InteropServices.Marshal]::FinalReleaseComObject($link) | Out-Null}
 if($shell){[Runtime.InteropServices.Marshal]::FinalReleaseComObject($shell) | Out-Null}
} """
    try:
        result = run_owned([powershell_path(), '-NoProfile', '-NonInteractive', '-NoLogo', '-Command', script],
            env=environment, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=12,
            output_limit=64 * 1024, cancel_event=cancel,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), check=True)
    finally:
        if temporary.exists():
            install._safe(temporary).unlink()
    import base64
    value = json.loads(result.stdout)
    destination = install._safe(Path(value['desktop']) / 'AX Workspace (최신).lnk')
    raw = base64.b64decode(value['data'], validate=True)
    if not 76 <= len(raw) <= 32 * 1024:
        raise ValueError('바로가기 파일을 만들지 못했습니다.')
    return destination, raw


def activate(state, value, *, create_shortcut=False, demo=False, cancel=None):
    """Promote only verified EXEs. Restore owned shortcut on persistence failure."""
    with _LOCK:
        root = install._root(state)
        target = _verified(root, value)
        current = root / 'launcher.json'
        if current.exists() and install._version(_read(current)['version']) > install._version(value['version']):
            raise ValueError('이전 실행기로 되돌리지 않습니다.')
        record_path = root / 'launcher-shortcut.json'
        previous_record = None
        if record_path.exists():
            if install._safe(record_path).stat().st_size > 4096:
                raise ValueError('기존 바로가기 정보를 확인하지 못했습니다.')
            previous_record = json.loads(record_path.read_text(encoding='utf-8'))
        if not create_shortcut and previous_record is None:
            install._atomic_manifest(current, value)
            return {'version': value['version'], 'shortcut': False}
        arguments = ['--state', str(Path(state).parent if demo else Path(state)), '--python', sys.executable]
        if demo:
            arguments.append('--demo')
        destination, raw = _shortcut_bytes(target, arguments, cancel=cancel)
        before = None
        if destination.exists():
            if (not isinstance(previous_record, dict) or previous_record.get('path') != str(destination)
                    or install._digest(destination) != previous_record.get('sha256')):
                raise ValueError('같은 이름의 기존 바로가기가 있어 변경하지 않았습니다. 바탕화면의 AX Workspace (최신) 바로가기를 확인해 주세요.')
            before = destination.read_bytes()
        install._cancelled(cancel)
        temporary = install._safe(destination.parent / ('.workspace-' + uuid.uuid4().hex + '.lnk'))
        try:
            install._write_new(temporary, raw)
            os.replace(temporary, install._safe(destination))
            install._atomic_manifest(record_path, {'path': str(destination), 'sha256': hashlib.sha256(raw).hexdigest()})
            install._atomic_manifest(current, value)
        except Exception:
            if before is None:
                if destination.exists() and install._digest(destination) == hashlib.sha256(raw).hexdigest():
                    destination.unlink()
            else:
                restore = install._safe(destination.parent / ('.workspace-' + uuid.uuid4().hex + '.lnk'))
                install._write_new(restore, before)
                os.replace(restore, destination)
            if previous_record is not None:
                install._atomic_manifest(record_path, previous_record)
            elif record_path.exists():
                record_path.unlink()
            raise
        finally:
            if temporary.exists():
                temporary.unlink()
        return {'version': value['version'], 'shortcut': True}


def confirm_pending(state, version, *, demo=False):
    with _LOCK:
        path = install._safe(Path(state) / 'updates/pending-launcher.json')
        if not path.exists():
            return False
        value = _read(path)
        if value['version'] != version:
            return False
        activate(state, value, demo=demo)
        path.unlink()
        return True


def current_record(state, version, *, source_identity=GITHUB_SOURCE.identity):
    with _LOCK:
        root = install._safe(Path(state) / 'updates')
        pointer = root / 'launcher.json'
        if not pointer.exists():
            return None
        value = _read(pointer)
        if value['version'] != version or value['sourceIdentity'] != source_identity:
            return None
        _verified(root, value)
        return value


def entry_details(runtime_version, *, environment=None):
    """Display-only bootstrap hints, never used to authorize privilege or launch."""
    env = os.environ if environment is None else environment
    reported = env.get('COMPANY_WORKSPACE_ENTRY_VERSION', '')
    protocol = env.get('COMPANY_WORKSPACE_ENTRY_PROTOCOL', '')
    try:
        install._version(reported)
    except ValueError:
        reported = None
    modern = reported is not None and protocol == '1' and install._version(reported) >= (0, 23, 21)
    return {'appVersion': runtime_version, 'entryVersion': reported,
            'entryKind': 'exe' if reported else 'script_or_legacy', 'modernEntry': modern,
            'repairRecommended': not modern}
