"""Prepare GitHub installer downloads without changing existing updater ZIP bytes.

This command is offline: it neither builds, installs, nor uploads anything. The
versioned direct EXE names become installer aliases, while the ZIPs retain the
standalone launcher contract used by already installed applications.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from local_app.app_updates import MAX_ARCHIVE, MAX_CHECKSUMS, _checksum
from workspace_publisher.config import PublisherError, checked_version, safe_path
from workspace_publisher.source_archive import (
    MANIFEST, MAX_FILES, MAX_FILE, MAX_MANIFEST, MAX_TOTAL, manifest_bytes, source_path,
)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def regular_file(path, limit):
    path = safe_path(Path(path).absolute())
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
        raise PublisherError('입력 파일의 종류 또는 크기가 올바르지 않습니다.')
    return path


def source_version(root):
    text = regular_file(Path(root) / 'local_app/server.py', MAX_FILE).read_text(encoding='utf-8-sig')
    versions = re.findall(r'^WORKSPACE_VERSION = [\"\']([^\"\']+)[\"\']\s*$', text, re.M)
    if len(versions) != 1:
        raise PublisherError('현재 소스에서 앱 버전을 확인하지 못했습니다.')
    return checked_version(versions[0])


def verify_source_zip(path, version):
    """Check the source-only inventory in memory, without extracting ZIP paths."""
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        if not 1 <= len(members) <= MAX_FILES * 2 + 2:
            raise PublisherError('소스 ZIP의 파일 수가 확인 범위를 넘었습니다.')
        manifest_names = [item.filename for item in members if not item.is_dir()
                          and item.filename.split('/')[-1] == MANIFEST]
        if len(manifest_names) != 1:
            raise PublisherError('소스 ZIP에 소스 확인 목록이 하나 있어야 합니다.')
        manifest_name = manifest_names[0]
        prefix = manifest_name[:-len(MANIFEST)]
        if prefix:
            source_path(prefix.rstrip('/'))
            if prefix.count('/') != 1:
                raise PublisherError('소스 ZIP은 한 단계의 최상위 폴더만 지원합니다.')
        files, seen, total, manifest_raw = {}, set(), 0, None
        for item in members:
            if not item.filename.startswith(prefix):
                raise PublisherError('소스 ZIP에 다른 최상위 경로가 있습니다.')
            name = item.filename[len(prefix):]
            mode = item.external_attr >> 16
            if stat.S_ISLNK(mode) or item.flag_bits & 1:
                raise PublisherError('소스 ZIP에 연결된 파일 또는 암호화된 파일이 있습니다.')
            if item.is_dir():
                if name:
                    source_path(name.rstrip('/'))
                continue
            if name.casefold() in seen:
                raise PublisherError('소스 ZIP에 중복된 파일이 있습니다.')
            seen.add(name.casefold())
            if name != MANIFEST:
                source_path(name)
            limit = MAX_MANIFEST if name == MANIFEST else MAX_FILE
            total += item.file_size
            if item.file_size > limit or total > MAX_TOTAL:
                raise PublisherError('소스 ZIP의 크기가 확인 범위를 넘었습니다.')
            raw = archive.read(item)
            if name == MANIFEST:
                manifest_raw = raw
            else:
                files[name] = raw
        expected = manifest_bytes(files)
        if manifest_raw != expected or json.loads(expected)['version'] != version:
            raise PublisherError('소스 ZIP의 버전 또는 소스 확인 목록이 일치하지 않습니다.')


def prepare(release_directory, installer_report, source_zip, output_directory, *, root=ROOT):
    version = source_version(root)
    release_directory = safe_path(Path(release_directory).absolute())
    output = safe_path(Path(output_directory).absolute())
    if output.exists():
        raise PublisherError('출력 위치에는 아직 존재하지 않는 새 폴더를 지정해 주세요.')
    report_path = regular_file(installer_report, 65536)
    report = json.loads(report_path.read_text(encoding='utf-8-sig'))
    if (not isinstance(report, dict) or report.get('version') != version
            or report.get('productId') != 'AX.Workspace.Desktop'
            or report.get('testMode') is not False
            or not isinstance(report.get('installer'), str)
            or not Path(report['installer']).is_absolute()
            or type(report.get('bytes')) is not int
            or not isinstance(report.get('sha256'), str)
            or not re.fullmatch(r'[a-f0-9]{64}', report['sha256'])
            or not isinstance(report.get('standaloneSha256'), str)
            or not re.fullmatch(r'[a-f0-9]{64}', report['standaloneSha256'])):
        raise PublisherError('현재 버전의 정식 NSIS 설치 파일 검증 기록이 필요합니다.')
    installer = regular_file(report['installer'], MAX_ARCHIVE)
    if (installer.name != f'AX-Workspace-{version}-Setup.exe'
            or installer.stat().st_size != report['bytes'] or digest(installer) != report['sha256']):
        raise PublisherError('NSIS 설치 파일과 검증 기록이 일치하지 않습니다.')
    with installer.open('rb') as stream:
        if stream.read(2) != b'MZ':
            raise PublisherError('NSIS 설치 파일이 Windows 실행 파일이 아닙니다.')
    checksums = regular_file(release_directory / 'SHA256SUMS.txt', MAX_CHECKSUMS).read_bytes()
    entries = {}
    for brand in ('AX-Workspace', 'Company-Workspace'):
        for edition in ('exe', 'vbs'):
            name = f'{brand}-{version}-{edition}.zip'
            path = regular_file(release_directory / name, MAX_ARCHIVE)
            expected = _checksum(checksums, name)
            if digest(path) != expected:
                raise PublisherError('기존 업데이트 ZIP의 체크섬이 일치하지 않습니다.')
            entries[name] = (path, expected)
    for name in (installer.name, f'AX-Workspace-{version}.exe',
                 f'Company-Workspace-{version}.exe', 'AX-Workspace-Setup.exe'):
        entries[name] = (installer, report['sha256'])
    source_zip = regular_file(source_zip, MAX_TOTAL)
    verify_source_zip(source_zip, version)
    entries[f'workspace-local-{version}-source.zip'] = (source_zip, digest(source_zip))
    if any(output == path or output in path.parents for path, _ in entries.values()):
        raise PublisherError('입력 파일과 출력 위치가 겹칩니다.')
    output.mkdir(parents=True, exist_ok=False)
    for name, (path, expected) in entries.items():
        destination = safe_path(output / name)
        with path.open('rb') as source, destination.open('xb') as target:
            shutil.copyfileobj(source, target, length=1024 * 1024)
        if digest(destination) != expected:
            raise PublisherError('복사 중 입력 파일이 변경되었습니다. 새 출력 폴더로 다시 시도해 주세요.')
    checksum_text = ''.join(f'{expected}  {name}\n' for name, (_, expected) in sorted(entries.items()))
    with (output / 'SHA256SUMS.txt').open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(checksum_text)
    return {'version': version, 'directory': str(output), 'installerSha256': report['sha256'],
            'files': [{'name': name, 'sha256': digest(output / name), 'bytes': (output / name).stat().st_size}
                      for name in sorted([*entries, 'SHA256SUMS.txt'])]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-directory', required=True, type=Path)
    parser.add_argument('--installer-report', required=True, type=Path)
    parser.add_argument('--source-zip', required=True, type=Path)
    parser.add_argument('--output-directory', required=True, type=Path)
    args = parser.parse_args()
    try:
        result = prepare(args.release_directory, args.installer_report, args.source_zip, args.output_directory)
    except (OSError, ValueError, TypeError, KeyError, zipfile.BadZipFile) as exc:
        parser.exit(1, f'배포 파일 준비 실패: {exc}\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
