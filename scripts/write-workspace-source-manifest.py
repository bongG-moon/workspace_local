"""Write the plain source inventory from staged Git blobs before a release commit.

Stage source edits first, run this script, then stage workspace-source-manifest.json.
The descriptor checks accidental transfer changes; it is not a source signature.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from workspace_publisher.config import PublisherError, safe_path
from workspace_publisher.source_archive import MANIFEST, MAX_FILES, MAX_FILE, MAX_TOTAL, manifest_bytes


def staged_files(root):
    root = safe_path(Path(root).absolute())
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    def git(*args, data=None):
        result = subprocess.run(['git', '-C', str(root), *args], input=data,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
                                creationflags=flags)
        if result.returncode:
            raise PublisherError('Git 인덱스를 읽지 못했습니다. 소스 변경을 먼저 stage해 주세요.')
        return result.stdout
    top = git('rev-parse', '--show-toplevel').decode('utf-8').strip()
    if Path(top).resolve() != root.resolve():
        raise PublisherError('선택한 위치가 Git 저장소의 최상위 폴더가 아닙니다.')
    rows = git('ls-files', '--stage', '-z').split(b'\0')
    if len(rows) > MAX_FILES + 2:
        raise PublisherError('Git 소스 파일 수가 확인 범위를 넘었습니다.')
    names, objects = [], []
    for row in rows:
        if not row:
            continue
        metadata, name_bytes = row.split(b'\t', 1)
        mode, object_id, stage = metadata.decode('ascii').split()
        name = name_bytes.decode('utf-8')
        if name == MANIFEST:
            continue
        if mode not in {'100644', '100755'} or stage != '0' or not re.fullmatch(r'[a-f0-9]{40,64}', object_id):
            raise PublisherError('충돌, 연결된 파일 또는 서브모듈이 있어 소스 목록을 만들지 못했습니다.')
        names.append(name)
        objects.append(object_id)
    # cat-file reads the index's exact stored bytes. .gitattributes disables
    # newline conversion, so GitHub Download ZIP contains these same bytes.
    request = ('\n'.join(objects) + '\n').encode('ascii')
    sizes = git('cat-file', '--batch-check', data=request).decode('ascii').splitlines()
    if len(sizes) != len(objects):
        raise PublisherError('Git 소스 크기 목록이 완전하지 않습니다.')
    total = 0
    for row, object_id in zip(sizes, objects):
        fields = row.split()
        if len(fields) != 3 or fields[:2] != [object_id, 'blob'] or not fields[2].isdigit():
            raise PublisherError('Git 소스 파일 형식이 올바르지 않습니다.')
        size = int(fields[2])
        total += size
        if size > MAX_FILE or total > MAX_TOTAL:
            raise PublisherError('Git 소스의 크기가 확인 범위를 넘었습니다.')
    batch = git('cat-file', '--batch', data=request)
    offset, total, result = 0, 0, {}
    for name, expected_id in zip(names, objects):
        end = batch.find(b'\n', offset)
        if end < 0:
            raise PublisherError('Git 파일 내용을 끝까지 읽지 못했습니다.')
        fields = batch[offset:end].decode('ascii').split()
        if len(fields) != 3 or fields[:2] != [expected_id, 'blob'] or not fields[2].isdigit():
            raise PublisherError('Git 파일 형식이 올바르지 않습니다.')
        size = int(fields[2])
        total += size
        if size > MAX_FILE or total > MAX_TOTAL:
            raise PublisherError('Git 소스의 크기가 확인 범위를 넘었습니다.')
        start, finish = end + 1, end + 1 + size
        if finish >= len(batch) or batch[finish:finish + 1] != b'\n':
            raise PublisherError('Git 파일 내용이 완전하지 않습니다.')
        result[name] = batch[start:finish]
        offset = finish + 1
    if offset != len(batch):
        raise PublisherError('Git 파일 확인 결과에 예상하지 않은 내용이 있습니다.')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description='Stage된 원본 파일로 Download ZIP 확인 목록을 만듭니다.')
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--check', action='store_true', help='기존 목록과 인덱스의 일치 여부만 확인')
    args = parser.parse_args(argv)
    try:
        root = safe_path(args.root.absolute())
        raw = manifest_bytes(staged_files(root))
        target = safe_path(root / MANIFEST)
        if args.check:
            if target.read_bytes() != raw:
                raise PublisherError('소스 확인 목록이 인덱스와 다릅니다. 목록을 다시 생성한 뒤 stage해 주세요.')
        else:
            with target.open('wb') as stream:
                stream.write(raw)
        print('Source manifest verified.' if args.check else 'Source manifest written; stage workspace-source-manifest.json next.')
        return 0
    except (PublisherError, OSError, UnicodeError, ValueError, subprocess.SubprocessError) as exc:
        print(str(exc) if isinstance(exc, PublisherError) else '소스 확인 목록을 만들지 못했습니다. Git 인덱스와 파일 권한을 확인해 주세요.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
