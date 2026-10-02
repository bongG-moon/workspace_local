"""Create a verified Windows-compatible archive using the existing Python standard library."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from workspace_publisher.archive import create_archive
from workspace_publisher.config import PublisherError


def main(argv=None):
    parser = argparse.ArgumentParser(description='Verify source files and create a ZIP without overwriting existing output.')
    parser.add_argument('--source', required=True, type=Path, help='Source directory')
    parser.add_argument('--output', required=True, type=Path, help='New ZIP path outside the source directory')
    parser.add_argument('--contents-only', action='store_true', help='Archive contents without their parent directory')
    args = parser.parse_args(argv)
    try:
        target = create_archive(args.source, args.output, include_root=not args.contents_only)
    except PublisherError as exc:
        parser.exit(1, str(exc) + '\n')
    print(f'압축 파일 검증 완료: {target}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
