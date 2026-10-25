"""Explicit, reviewed extension ZIP imports. Never execute a package at install time."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
import threading
from urllib.parse import urlsplit
import zipfile

from .extensions import (ACTION, HOST_API, ID, MAX_FILES, MAX_MANIFEST,
                         MAX_PACKAGE, MAX_UI, RESERVED, SHA, VERSION, ExtensionError,
                         check_worker_directory, fs_path, no_links, relative_path)

MAX_ARCHIVE = 16 * 1024 * 1024
MAX_INSTALLED = 256 * 1024 * 1024
REVIEW_TTL = 5 * 60
OWNER = '.ax-extension-owner.json'
PRODUCT = 'AX.ExtensionImport'
EXTRAS = {'README.txt', 'ai-square.json', 'SHA256SUMS.txt', 'connection.json'}


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')


def _object(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON property')
            result[key] = value
        return result
    try:
        value = json.loads(data.decode('utf-8-sig'), object_pairs_hook=pairs)
        if not isinstance(value, dict):
            raise ValueError('object required')
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise ExtensionError('확장 패키지의 JSON 형식을 확인해 주세요.') from None


def _path(name):
    relative_path(name)
    if any(c in name for c in '<>"|?*'):
        raise ExtensionError('확장 파일 이름에 사용할 수 없는 문자가 있습니다.')
    return name


def _read(path, maximum):
    path = fs_path(no_links(path))
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1 or info.st_size > maximum:
        raise ExtensionError('확장 파일의 형식이나 크기를 확인해 주세요.')
    with path.open('rb') as stream:
        data = stream.read(maximum + 1)
    after = path.stat()
    if len(data) > maximum or (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ExtensionError('확장 파일이 확인 중 변경되었습니다. 다시 선택해 주세요.')
    return data


def _connection(data):
    if len(data) > 4096:
        raise ExtensionError('서버 주소 파일이 너무 큽니다.')
    value = _object(data)
    url = value.get('baseUrl')
    if set(value) != {'schemaVersion', 'baseUrl'} or type(value.get('schemaVersion')) is not int or value['schemaVersion'] != 1:
        raise ExtensionError('서버 주소 파일 형식이 올바르지 않습니다.')
    try:
        if not isinstance(url, str) or len(url) > 500 or any(ord(c) <= 32 for c in url) or '\\' in url:
            raise ValueError()
        parsed = urlsplit(url)
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment or parsed.path not in {'', '/'}):
            raise ValueError()
        port = parsed.port
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            address = None
        if address is not None and address.is_unspecified:
            raise ValueError()
        hostname = parsed.hostname.lower()
        if ':' in hostname:
            hostname = '[' + hostname + ']'
        suffix = '' if port is None or (parsed.scheme, port) in {('http', 80), ('https', 443)} else ':' + str(port)
        return {'schemaVersion': 1, 'baseUrl': parsed.scheme + '://' + hostname + suffix}
    except ValueError:
        raise ExtensionError('서버 주소는 계정 정보가 없는 HTTP(S) 기본 주소여야 합니다.') from None


def load_package(path):
    raw = _read(path, MAX_ARCHIVE)
    files, total, names = {}, 0, set()
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if not 3 <= len(entries) <= MAX_FILES * 2 + 12:
                raise ExtensionError('확장 ZIP의 파일 수가 지원 범위를 넘었습니다.')
            for entry in entries:
                name = _path(entry.filename.rstrip('/') if entry.is_dir() else entry.filename)
                folded = name.casefold()
                unix_type = (entry.external_attr >> 16) & 0xf000
                if (folded in names or entry.flag_bits & 1 or entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                        or entry.external_attr & 0x400 or unix_type not in {0, stat.S_IFREG, stat.S_IFDIR}
                        or unix_type == stat.S_IFDIR and not entry.is_dir()):
                    raise ExtensionError('확장 ZIP에 중복·링크·암호화 파일이 있습니다.')
                names.add(folded)
                if entry.is_dir():
                    continue
                total += entry.file_size
                if total > MAX_PACKAGE or entry.file_size > MAX_PACKAGE:
                    raise ExtensionError('압축을 푼 확장 크기가 지원 범위를 넘었습니다.')
                with archive.open(entry) as source:
                    content = source.read(min(entry.file_size, MAX_PACKAGE) + 1)
                if len(content) != entry.file_size:
                    raise ExtensionError('확장 압축 파일의 크기가 올바르지 않습니다.')
                files[name] = content
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError):
        raise ExtensionError('정상적인 확장 ZIP 파일을 선택해 주세요.') from None
    if len(files.get('extension.json', b'')) > MAX_MANIFEST or 'extension.json' not in files:
        raise ExtensionError('확장 등록 정보가 없습니다.')
    manifest = _object(files['extension.json'])
    extension_id, file_map = manifest.get('id'), manifest.get('files')
    actions = manifest.get('actions')
    if (not isinstance(extension_id, str) or not ID.fullmatch(extension_id) or '..' in extension_id
            or extension_id.split('.')[0] in RESERVED or type(manifest.get('hostApi')) is not int
            or manifest['hostApi'] != HOST_API or type(manifest.get('schemaVersion')) is not int
            or manifest['schemaVersion'] != 1 or not isinstance(manifest.get('version'), str)
            or not 1 <= len(manifest['version']) <= 50 or not VERSION.fullmatch(manifest['version'])
            or not isinstance(manifest.get('name'), str)
            or not 1 <= len(manifest['name']) <= 60 or any(ord(c) < 32 for c in manifest['name'])
            or not isinstance(actions, list) or not 1 <= len(actions) <= 48
            or any(not isinstance(action, str) or not ACTION.fullmatch(action) for action in actions)
            or len(set(actions)) != len(actions) or not isinstance(file_map, dict) or not 2 <= len(file_map) <= MAX_FILES):
        raise ExtensionError('현재 앱과 호환되는 확장 패키지인지 확인해 주세요.')
    prefixes = set()
    for name, digest in file_map.items():
        _path(name)
        prefixes.add('/'.join(name.split('/')[:2]))
        if not isinstance(digest, str) or not SHA.fullmatch(digest) or name not in files or _hash(files[name]) != digest:
            raise ExtensionError('확장 파일 해시가 일치하지 않습니다. 원본 ZIP을 다시 받아 주세요.')
    if len(prefixes) != 1 or not re.fullmatch(r'packages/[a-f0-9]{64}', next(iter(prefixes))):
        raise ExtensionError('확장 파일은 하나의 검증된 설치 버전에 있어야 합니다.')
    prefix = next(iter(prefixes)) + '/'
    if (not isinstance(manifest.get('entrypoint'), str) or manifest['entrypoint'] not in file_map
            or not manifest['entrypoint'].endswith('.py') or not isinstance(manifest.get('ui'), str)
            or manifest['ui'] not in file_map or not manifest['ui'].endswith('.html')
            or len(files[manifest['ui']]) > MAX_UI or set(files) - set(file_map) - EXTRAS - {'extension.json'}):
        raise ExtensionError('확장 시작 파일 또는 동봉 파일을 확인해 주세요.')
    file_names = {name.casefold() for name in files}
    for name in files:
        parts = name.split('/')
        if any('/'.join(parts[:index]).casefold() in file_names for index in range(1, len(parts))):
            raise ExtensionError('확장 파일과 폴더 경로가 겹칩니다.')
    fingerprint = ''.join(name[len(prefix):] + '\t' + file_map[name] + '\n' for name in sorted(file_map))
    if _hash(fingerprint.encode('utf-8')) != prefix.split('/')[1]:
        raise ExtensionError('확장 빌드 식별자가 파일 목록과 다릅니다.')
    try:
        files[manifest['ui']].decode('utf-8')
    except UnicodeError:
        raise ExtensionError('확장 화면의 문자 형식이 올바르지 않습니다.') from None
    for name in EXTRAS & set(files):
        if len(files[name]) > MAX_MANIFEST:
            raise ExtensionError('확장 동봉 안내 파일이 너무 큽니다.')
    return {'rawHash': _hash(raw), 'manifest': manifest, 'manifestBytes': files['extension.json'],
            'files': {name: files[name] for name in file_map}, 'build': prefix.split('/')[1],
            'connection': _connection(files['connection.json']) if 'connection.json' in files else None}


def _within(directory, name):
    return fs_path(no_links(directory / _path(name)))


def _inventory(directory):
    directory = fs_path(no_links(directory))
    result, count, total = {}, 0, 0
    pending = [no_links(directory)]
    while pending:
        current = pending.pop()
        for path in current.iterdir():
            count += 1
            if count > MAX_FILES * 64 + 130:
                raise ExtensionError('보관된 확장 파일 수가 너무 많습니다.')
            no_links(path)
            if path.is_dir():
                pending.append(path)
            else:
                data = _read(path, MAX_PACKAGE)
                total += len(data)
                if total > MAX_INSTALLED:
                    raise ExtensionError('보관된 확장 파일 크기가 너무 큽니다.')
                result[path.relative_to(directory).as_posix()] = _hash(data)
    return result


def owned_state(directory, incoming):
    directory = fs_path(no_links(directory))
    if not directory.exists():
        return None, None
    if not directory.is_dir():
        raise ExtensionError('확장 설치 위치에 다른 파일이 있습니다.')
    try:
        owner = _object(_read(directory / OWNER, 2 * 1024 * 1024))
    except OSError:
        raise ExtensionError('이 앱의 설치 기록이 없는 폴더이므로 기존 파일을 유지했습니다.') from None
    identifier = incoming['manifest']['id']
    if (owner.get('product') not in {PRODUCT, 'AX.OrganizationStore'} or owner.get('id') != identifier
            or type(owner.get('schemaVersion')) is not int or owner['schemaVersion'] != 1
            or not isinstance(owner.get('packages'), dict) or not 1 <= len(owner['packages']) <= 64):
        raise ExtensionError('이 앱이 관리하는 확장인지 확인할 수 없어 기존 파일을 유지했습니다.')
    if owner['product'] == 'AX.OrganizationStore' and identifier != 'pt-ai-square.store':
        raise ExtensionError('이전 설치 기록과 확장 ID가 일치하지 않습니다.')
    expected, current_known = {}, False
    current_hash = _hash(_read(directory / 'extension.json', MAX_MANIFEST))
    for build, record in owner['packages'].items():
        if (not SHA.fullmatch(build) or not isinstance(record, dict) or not isinstance(record.get('files'), dict)
                or not 2 <= len(record['files']) <= MAX_FILES):
            raise ExtensionError('기존 확장 소유권 기록을 확인해 주세요.')
        digest = record.get('manifestSha256')
        if not isinstance(digest, str) or not SHA.fullmatch(digest):
            raise ExtensionError('기존 확장 등록 정보의 해시가 올바르지 않습니다.')
        current_known |= digest == current_hash
        for name, digest in record['files'].items():
            _path(name)
            if (not name.startswith('packages/' + build + '/') or name in expected or not isinstance(digest, str)
                    or not SHA.fullmatch(digest)):
                raise ExtensionError('기존 확장 파일의 소유권 기록이 올바르지 않습니다.')
            expected[name] = digest
    if not current_known:
        raise ExtensionError('기존 확장 등록 정보가 변경되었습니다. 기존 파일을 유지했습니다.')
    previous = owner['packages'].get(incoming['build'])
    if previous is not None and previous['manifestSha256'] != _hash(incoming['manifestBytes']):
        raise ExtensionError('같은 확장 빌드의 등록 정보가 달라 덮어쓰지 않았습니다. 배포자에게 문의해 주세요.')
    actual = _inventory(directory)
    extra = set(actual) - set(expected) - {OWNER, 'extension.json'}
    if extra and incoming['build'] not in owner['packages'] and extra == set(incoming['files']):
        # An interrupted immutable-package move is recoverable only from the
        # exact reviewed ZIP, with every previously owned file still unchanged.
        expected.update(incoming['manifest']['files'])
        owner['packages'][incoming['build']] = {'manifestSha256': _hash(incoming['manifestBytes']),
                                               'files': incoming['manifest']['files']}
    if set(actual) != set(expected) | {OWNER, 'extension.json'} or any(actual.get(n) != digest for n, digest in expected.items()):
        raise ExtensionError('확장 폴더에 변경되거나 알 수 없는 파일이 있어 덮어쓰지 않았습니다.')
    info = directory.stat()
    fingerprint = _hash(_encode({'files': actual, 'device': info.st_dev, 'inode': info.st_ino}))
    return owner, fingerprint


def _preset_state(host, identifier):
    path = fs_path(no_links(host.data_root / identifier / 'connection.json'))
    if path.exists():
        raw = _read(path, 4096)
        return path, _hash(raw), _connection(raw)
    return path, None, None


def _atomic(path, data):
    path = fs_path(no_links(path))
    temporary = path.parent / ('.extension-write-' + secrets.token_hex(10))
    try:
        with temporary.open('xb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        no_links(path)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


class ExtensionImporter:
    def __init__(self, host):
        self.host = host
        self._lock = threading.Lock()
        self._pending = None

    def _open(self):
        with self.host._lock:
            if self.host._closing:
                raise ExtensionError('앱 종료 중에는 확장을 설치할 수 없습니다.', status=409)

    def prepare(self, picker):
        if not self._lock.acquire(blocking=False):
            raise ExtensionError('다른 확장 설치 확인을 마친 뒤 다시 시도하세요.', status=409)
        try:
            self._open()
            self._pending = None
            selected = picker('files')
            if selected.get('status') == 'cancel':
                return {'cancelled': True}
            paths = selected.get('paths')
            if (selected.get('status') != 'success' or not isinstance(paths, list) or len(paths) != 1
                    or not isinstance(paths[0], str) or not Path(paths[0]).is_absolute() or Path(paths[0]).suffix.lower() != '.zip'):
                raise ExtensionError('확장 ZIP 파일 하나를 선택해 주세요.')
            path = no_links(paths[0])
            package = load_package(path)
            identifier = package['manifest']['id']
            destination = no_links(self.host.root / identifier)
            check_worker_directory(destination)
            owner, fingerprint = owned_state(destination, package)
            _, preset_hash, existing_preset = _preset_state(self.host, identifier)
            existing_data = fs_path(no_links(self.host.data_root / identifier)).is_dir()
            self._open()
            token = secrets.token_urlsafe(32)
            self._pending = {'token': token, 'expires': self.host.clock() + REVIEW_TTL, 'path': str(path),
                             'sha': package['rawHash'], 'target': fingerprint, 'preset': preset_hash,
                             'id': identifier}
            warnings = ['확장은 현재 Windows 사용자 권한으로 실행됩니다. 신뢰할 수 있는 배포자의 ZIP만 설치하세요.']
            if existing_data:
                warnings.append('표시된 주소는 초기 연결용 기본값입니다. 기존 확장의 개인 설정과 연결 KEY는 변경하지 않습니다.')
            if existing_preset is not None and package['connection'] != existing_preset:
                warnings.append('기존 서버 주소와 연결 KEY를 유지합니다. 새 ZIP의 주소로 자동 전환하지 않습니다.')
            server_url = (existing_preset or package['connection'] or {}).get('baseUrl')
            if server_url and server_url.startswith('http://'):
                warnings.append('HTTP 연결에서는 KEY와 통신 내용이 암호화되지 않습니다. 조직에서 안내한 서버 주소인지 확인하세요.')
            return {'cancelled': False, 'token': token,
                    'extension': {key: package['manifest'][key] for key in ('id', 'name', 'version')},
                    'destination': str(destination), 'packageSha256': package['rawHash'], 'scope': 'user',
                    'serverUrl': server_url,
                    'replacing': owner is not None, 'warnings': warnings}
        finally:
            self._lock.release()

    def cancel(self, token):
        with self._lock:
            if self._pending and secrets.compare_digest(str(token), self._pending['token']):
                self._pending = None
        return {'cancelled': True}

    def confirm(self, token, confirmed):
        if confirmed is not True:
            raise ExtensionError('확장 정보와 설치 위치를 확인한 뒤 설치를 선택해 주세요.')
        if not self._lock.acquire(blocking=False):
            raise ExtensionError('다른 확장 설치를 처리하고 있습니다.', code='extension_busy', status=409)
        try:
            record, self._pending = self._pending, None
            if (not record or not isinstance(token, str) or not secrets.compare_digest(token, record['token'])
                    or record['expires'] <= self.host.clock()):
                raise ExtensionError('설치 확인이 만료되었습니다. ZIP을 다시 선택해 주세요.', status=409)
            identifier = record['id']
            with self.host.installing(identifier):
                package = load_package(record['path'])
                if package['rawHash'] != record['sha'] or package['manifest']['id'] != identifier:
                    raise ExtensionError('확인한 ZIP이 변경되었습니다. 다시 선택해 주세요.', status=409)
                destination = no_links(self.host.root / identifier)
                check_worker_directory(destination)
                owner, fingerprint = owned_state(destination, package)
                preset_path, preset_hash, existing_preset = _preset_state(self.host, identifier)
                existing_data = fs_path(no_links(self.host.data_root / identifier)).is_dir()
                if fingerprint != record['target'] or preset_hash != record['preset']:
                    raise ExtensionError('확인 후 설치 위치나 서버 설정이 변경되었습니다. 다시 선택해 주세요.', status=409)
                if owner is None:
                    self.host.check_install_capacity()
                if owner and len(owner['packages']) >= 64 and package['build'] not in owner['packages']:
                    raise ExtensionError('보관된 확장 버전 수를 넘었습니다. 운영자에게 문의해 주세요.')
                self._install(destination, owner, package, preset_path if preset_hash is None else None)
                self.host.snapshot(refresh=True)
            result = self.host.snapshot()
            return {'installed': True, 'extension': {key: package['manifest'][key] for key in ('id', 'name', 'version')},
                    'extensions': result['extensions'], 'warnings': result['warnings'],
                    'existingDataPreserved': existing_data}
        finally:
            self._lock.release()

    def _install(self, destination, owner, package, preset_path):
        destination = fs_path(no_links(destination))
        parent = fs_path(no_links(self.host.root.parent))
        parent.mkdir(parents=True, exist_ok=True)
        fs_path(no_links(self.host.root)).mkdir(exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix='.extension-import-', dir=parent))
        created = []
        record = {'manifestSha256': _hash(package['manifestBytes']), 'files': package['manifest']['files']}
        committed = False
        preset_created = False
        try:
            for name, content in package['files'].items():
                path = _within(stage, name)
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('xb') as target:
                    target.write(content)
                created.append(name)
            if owner is None:
                marker = {'product': PRODUCT, 'id': package['manifest']['id'], 'schemaVersion': 1,
                          'packages': {package['build']: record}}
                (stage / OWNER).write_bytes(_encode(marker)); created.append(OWNER)
                (stage / 'extension.json').write_bytes(package['manifestBytes']); created.append('extension.json')
            if package['connection'] is not None and preset_path is not None:
                no_links(preset_path.parent).mkdir(parents=True, exist_ok=True)
                # Exclusive creation preserves a concurrently stored connection.
                with no_links(preset_path).open('xb') as target:
                    target.write(_encode(package['connection']))
                preset_created = True
            no_links(destination)
            if owner is None:
                if destination.exists():
                    raise ExtensionError('설치 위치가 변경되어 중단했습니다.')
                stage.rename(destination)
                committed = True
            else:
                target = _within(destination, 'packages/' + package['build'])
                if not target.exists():
                    (stage / 'packages' / package['build']).rename(target)
                marker = deepcopy(owner)
                marker['product'] = PRODUCT
                marker['packages'][package['build']] = record
                # Marker first makes a crash before the manifest switch harmless:
                # the old manifest remains known, and every package is immutable.
                _atomic(destination / OWNER, _encode(marker))
                _atomic(destination / 'extension.json', package['manifestBytes'])
                committed = True
        finally:
            if preset_created and not committed:
                if no_links(preset_path).is_file() and _read(preset_path, 4096) == _encode(package['connection']):
                    preset_path.unlink()
            if stage.exists():
                # Only remove paths created by this operation, never recursive
                # deletion of a supplied path or the user's extension/data tree.
                for name in reversed(created):
                    path = _within(stage, name)
                    if path.is_file():
                        path.unlink()
                directories = sorted((p for p in stage.rglob('*') if p.is_dir()), key=lambda p: len(p.parts), reverse=True)
                for path in directories:
                    no_links(path)
                    if not any(path.iterdir()):
                        path.rmdir()
                if not any(stage.iterdir()):
                    stage.rmdir()
