"""Record reviewed release notes before regenerating the source ZIP manifest."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from workspace_publisher.config import PublisherError
from workspace_publisher.release_notes import MAX_NOTES, record_release


def main(argv=None):
    parser = argparse.ArgumentParser(description='Save reviewed offline release notes in the source repository.')
    parser.add_argument('--version', required=True, help='App version, for example 0.23.2')
    parser.add_argument('--title', required=True, help='Release title')
    parser.add_argument('--notes-file', required=True, type=Path, help='Reviewed UTF-8 release description')
    parser.add_argument('--repo-root', type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        with args.notes_file.open('rb') as stream:
            raw = stream.read(MAX_NOTES + 4)
        notes = raw.decode('utf-8-sig')
        path = record_release(args.repo_root, args.version, args.title, notes)
    except (OSError, UnicodeError, PublisherError):
        parser.exit(1, '릴리스 기록을 저장하지 못했습니다. 버전, UTF-8 변경 내용 파일과 기존 기록을 확인해 주세요.\n')
    print(f'Saved release notes: {path}')
    print('Regenerate the source manifest after staging this release record.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
