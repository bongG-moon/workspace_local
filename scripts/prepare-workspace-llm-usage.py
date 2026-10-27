"""Copy an operator-selected usage endpoint into the application, never a KEY."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from local_app.llm_usage_config import encoded, from_bytes


def prepare(source, target):
    if source.is_symlink() or not source.is_file():
        raise ValueError('Invalid configuration file')
    with source.open('rb') as stream:
        data = encoded(from_bytes(stream.read(8193)))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        prepare(args.input, args.output)
    except (OSError, ValueError, TypeError):
        parser.exit(1, '사용량 MCP 주소 설정을 확인해 주세요. KEY는 포함하지 않습니다.\n')
    print('Validated usage endpoint; no credentials included.')
