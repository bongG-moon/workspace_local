"""Validate and copy site settings into a build payload without editing source.

This accepts only the closed, non-secret update-source schema. It never contacts
the server and never reads personal Git, SSH, or Claude credentials.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from local_app.update_source import source_from_config


def prepare(input_path: Path, output_path: Path):
    if input_path.is_symlink() or not input_path.is_file() or input_path.stat().st_size > 32 * 1024:
        raise ValueError('업데이트 설정 파일을 확인해 주세요.')
    value = json.loads(input_path.read_text(encoding='utf-8-sig'))
    source = source_from_config(value)
    if source.provider != 'gitlab':
        raise ValueError('사내 배포에는 GitLab 업데이트 설정이 필요합니다.')
    data = (json.dumps(source.config, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(data)
    return source.identity


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        prepare(args.input, args.output)
    except (OSError, ValueError, TypeError) as exc:
        # No raw settings or exception bodies: input must never become a log.
        parser.exit(1, '업데이트 배포 설정이 올바르지 않습니다. 주소와 프로젝트 ID를 확인해 주세요.\n')
    print('Validated GitLab update settings; no credentials included.')
