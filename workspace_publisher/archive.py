"""Verified streaming ZIP creation without Windows Compress-Archive locks.

The source tree is read only. Windows readers permit other readers/writers and
deletion, then content and file identities are checked to detect any change.
Only sharing/lock violations receive bounded retries; access checks stay intact.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import tempfile
import time
import zipfile
import zlib

from .config import PublisherError


CHUNK_SIZE = 256 * 1024


def _native(path):
    """Use long Windows paths without changing machine or account policy."""
    value = os.fspath(path)
    if os.name != 'nt' or value.startswith('\\\\?\\'):
        return value
    value = os.path.abspath(value)
    return '\\\\?\\UNC\\' + value[2:] if value.startswith('\\\\') else '\\\\?\\' + value


def _absolute(path):
    value = Path(path)
    if '..' in value.parts:
        raise PublisherError('압축 경로에 상위 폴더 이동을 넣을 수 없습니다.')
    return Path(os.path.abspath(value))


def _plain_name(value):
    if (not value or value in ('.', '..') or any(c in value for c in ('\\', '/', ':'))
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise PublisherError('압축할 파일이나 폴더의 이름을 확인해 주세요.')
    return value


def _linked(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def _safe_ancestors(path):
    for part in (path, *path.parents):
        try:
            info = os.lstat(_native(part))
        except FileNotFoundError:
            continue
        if _linked(info):
            raise PublisherError('연결된 파일이나 폴더는 압축 경로로 사용할 수 없습니다.')


def _fingerprint(info):
    # Windows path stat infers executable permission from .exe/.bat suffixes,
    # while fd stat cannot. Python versions also differ on Windows st_ctime;
    # explicit birth time is consistent for path and open-handle metadata.
    created = getattr(info, 'st_birthtime_ns', info.st_ctime_ns) if os.name == 'nt' else info.st_ctime_ns
    return (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode), info.st_size,
            info.st_mtime_ns, created)


def _inventory(root):
    """Snapshot every regular file and directory, including empty directories."""
    result = {}
    folded = set()
    pending = [(root, '')]
    while pending:
        path, relative = pending.pop()
        info = os.lstat(_native(path))
        if _linked(info):
            raise PublisherError(f'연결된 파일이나 폴더는 압축할 수 없습니다: {relative or root.name}')
        directory = stat.S_ISDIR(info.st_mode)
        if not directory and not stat.S_ISREG(info.st_mode):
            raise PublisherError(f'일반 파일이 아닌 항목은 압축할 수 없습니다: {relative or root.name}')
        result[relative] = (directory, _fingerprint(info))
        if directory:
            with os.scandir(_native(path)) as entries:
                children = sorted(entries, key=lambda entry: entry.name)
            for child in children:
                name = _plain_name(child.name)
                child_relative = relative + '/' + name if relative else name
                folded_name = child_relative.casefold()
                if folded_name in folded:
                    raise PublisherError(f'Windows에서 구분할 수 없는 중복 파일 이름이 있습니다: {child_relative}')
                folded.add(folded_name)
                pending.append((path / name, child_relative))
    if not result[''][0]:
        raise PublisherError('압축할 소스 폴더를 선택해 주세요.')
    return result


def _open_source(path):
    """Open read-only with explicit Windows sharing; never break existing locks."""
    if os.name != 'nt':
        return open(_native(path), 'rb', buffering=0)
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    # GENERIC_READ, SHARE_READ|SHARE_WRITE|SHARE_DELETE, OPEN_EXISTING,
    # FILE_FLAG_OPEN_REPARSE_POINT|FILE_FLAG_SEQUENTIAL_SCAN.
    handle = create(_native(path), 0x80000000, 0x7, None, 3, 0x08200000, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle(handle)
        raise
    try:
        return os.fdopen(descriptor, 'rb', buffering=0)
    except BaseException:
        os.close(descriptor)
        raise


class _Retries:
    def __init__(self, name, attempts, delay):
        self.name, self.attempts, self.delay, self.failures = name, attempts, delay, 0

    def call(self, function):
        while True:
            try:
                return function()
            except OSError as exc:
                code = getattr(exc, 'winerror', None)
                detail = f' (WinError {code})' if type(code) is int else ''
                if code not in (32, 33):
                    raise PublisherError(f'파일을 읽지 못했습니다: {self.name}{detail}. 접근 권한과 파일 상태를 확인해 주세요.') from exc
                self.failures += 1
                if self.failures >= self.attempts:
                    raise PublisherError(f'파일 잠금이 계속되어 압축을 중단했습니다: {self.name} ({self.attempts}회 시도, WinError {code}). 파일을 사용하는 작업이 끝난 뒤 다시 시도해 주세요.') from exc
                time.sleep(self.delay)


def _changed(name):
    return PublisherError(f'압축 중 소스가 변경되거나 사라졌습니다: {name}. 소스를 바꾸는 작업이 끝난 뒤 다시 빌드해 주세요.')


def _read_file(root, relative, fingerprint, attempts, delay, output=None):
    retries = _Retries(relative, attempts, delay)
    path = root / relative
    digest, size = hashlib.sha256(), 0
    with retries.call(lambda: _open_source(path)) as stream:
        if _fingerprint(os.fstat(stream.fileno())) != fingerprint:
            raise _changed(relative)
        while True:
            chunk = retries.call(lambda: stream.read(CHUNK_SIZE))
            if not chunk:
                break
            size += len(chunk)
            if size > fingerprint[3]:
                raise _changed(relative)
            digest.update(chunk)
            if output is not None:
                output.write(chunk)
        if size != fingerprint[3] or _fingerprint(os.fstat(stream.fileno())) != fingerprint:
            raise _changed(relative)
    info = os.lstat(_native(path))
    if _linked(info) or _fingerprint(info) != fingerprint:
        raise _changed(relative)
    return (size, digest.hexdigest())


def _verify_archive(path, expected):
    """Read every member in bounded chunks and verify inventory, size and SHA256."""
    with zipfile.ZipFile(_native(path), 'r') as archive:
        entries = archive.infolist()
        if len(entries) != len(expected) or {entry.filename for entry in entries} != set(expected):
            raise PublisherError('압축 파일의 항목 목록 검증이 실패했습니다.')
        for entry in entries:
            size, digest = expected[entry.filename]
            if entry.file_size != size or entry.flag_bits & 1:
                raise PublisherError(f'압축 파일의 크기 검증이 실패했습니다: {entry.filename}')
            actual, actual_size = hashlib.sha256(), 0
            with archive.open(entry) as stream:
                while True:
                    chunk = stream.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    actual_size += len(chunk)
                    if actual_size > size:
                        raise PublisherError(f'압축 파일의 크기 검증이 실패했습니다: {entry.filename}')
                    actual.update(chunk)
            if actual_size != size or actual.hexdigest() != digest:
                raise PublisherError(f'압축 파일의 SHA256 검증이 실패했습니다: {entry.filename}')


def _publish(temporary, destination):
    """Publish without replacing an existing destination, including races."""
    if os.name == 'nt':
        # Unlike POSIX rename, Windows rename refuses an existing destination.
        os.rename(_native(temporary), _native(destination))
    else:
        os.link(_native(temporary), _native(destination))
        os.unlink(_native(temporary))


def create_archive(source_directory, destination, *, include_root=True, attempts=4, retry_delay=0.25):
    """Create a complete verified ZIP, returning its path; never overwrite output."""
    if (type(include_root) is not bool or type(attempts) is not int or not 1 <= attempts <= 20
            or isinstance(retry_delay, bool) or not isinstance(retry_delay, (int, float))
            or not 0 <= retry_delay <= 5):
        raise PublisherError('압축 재시도 설정을 확인해 주세요.')
    temporary, failure = None, None
    try:
        source, target = _absolute(source_directory), _absolute(destination)
        _safe_ancestors(source)
        _safe_ancestors(target)
        if target == source or source in target.parents:
            raise PublisherError('압축 결과는 소스 폴더 밖에 저장해 주세요.')
        if os.path.lexists(_native(target)):
            raise PublisherError('같은 이름의 압축 파일이 이미 있습니다. 기존 파일을 유지했습니다.')
        inventory = _Retries(source.name, attempts, retry_delay).call(lambda: _inventory(source))
        prefix = _plain_name(source.name) + '/' if include_root else ''
        expected = {}
        fingerprints = {}
        for relative, (directory, fingerprint) in sorted(inventory.items()):
            if directory:
                # Existing app ZIPs prefix member names with the root folder,
                # but do not contain a standalone root-directory entry.
                if not relative:
                    continue
                entry = prefix + relative + ('/' if relative else '')
                if entry:
                    expected[entry] = (0, hashlib.sha256(b'').hexdigest())
            else:
                fingerprints[relative] = _read_file(source, relative, fingerprint, attempts, retry_delay)
                expected[prefix + relative] = fingerprints[relative]
        os.makedirs(_native(target.parent), exist_ok=True)
        _safe_ancestors(target.parent)
        descriptor, temporary_name = tempfile.mkstemp(prefix='.workspace-archive-', suffix='.tmp', dir=_native(target.parent))
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, 'w+b') as file:
            with zipfile.ZipFile(file, 'w', compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                for relative, (directory, fingerprint) in sorted(inventory.items()):
                    entry = prefix + relative + ('/' if directory and relative else '')
                    if directory:
                        if not relative:
                            continue
                        if entry:
                            archive.writestr(entry, b'')
                    else:
                        with archive.open(entry, 'w', force_zip64=True) as output:
                            actual = _read_file(source, relative, fingerprint, attempts, retry_delay, output)
                        if actual != fingerprints[relative]:
                            raise _changed(relative)
            file.flush()
            os.fsync(file.fileno())
            archive_fingerprint = _fingerprint(os.fstat(file.fileno()))
        _verify_archive(temporary, expected)
        _safe_ancestors(source)
        final = _inventory(source)
        if final != inventory:
            changed = next((name for name in sorted(set(final) | set(inventory)) if final.get(name) != inventory.get(name)), source.name)
            raise _changed(changed or source.name)
        for relative, expected_hash in fingerprints.items():
            if _read_file(source, relative, inventory[relative][1], attempts, retry_delay) != expected_hash:
                raise _changed(relative)
        if _inventory(source) != inventory:
            raise _changed(source.name)
        archive_info = os.lstat(_native(temporary))
        if _linked(archive_info) or _fingerprint(archive_info) != archive_fingerprint:
            raise PublisherError('검증 중 임시 압축 파일이 변경되었습니다. 다시 빌드해 주세요.')
        _safe_ancestors(target)
        _publish(temporary, target)
        temporary = None
        return target
    except PublisherError as exc:
        failure = exc
        raise
    except FileExistsError as exc:
        failure = PublisherError('같은 이름의 압축 파일이 이미 있습니다. 기존 파일을 유지했습니다.')
        raise failure from exc
    except (OSError, ValueError, zipfile.BadZipFile, zipfile.LargeZipFile, zlib.error, RuntimeError) as exc:
        failure = PublisherError('압축 파일을 만들거나 검증하지 못했습니다. 소스 파일 상태와 출력 폴더의 접근 권한을 확인해 주세요.')
        raise failure from exc
    finally:
        if temporary is not None:
            # Remove only the exact temporary file created by this invocation.
            for attempt in range(attempts):
                try:
                    os.unlink(_native(temporary))
                    break
                except FileNotFoundError:
                    break
                except OSError as exc:
                    code = getattr(exc, 'winerror', None)
                    if code in (32, 33) and attempt + 1 < attempts:
                        time.sleep(retry_delay)
                        continue
                    detail = f' (WinError {code})' if type(code) is int else ''
                    message = f'임시 압축 파일도 정리하지 못했습니다{detail}: {temporary.name}. 파일을 사용하는 작업이 끝나면 해당 임시 파일을 확인해 주세요.'
                    if failure is not None:
                        failure.args = (str(failure) + ' ' + message,)
                    else:
                        raise PublisherError(message) from exc
                    break
