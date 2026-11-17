"""Prepare private corporate registration settings without printing the key.

With no --input, prompt for the server's registration key using hidden input.
The default output is ignored by Git and is never picked up by public builds.
Pass the resulting file explicitly with -RegistrationConfig for an internal build.
"""
import argparse
import getpass
from pathlib import Path
import sys
import warnings

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from local_app.registration import DEFAULT_BASE_URL, MAX_CONFIG, encoded, from_bytes

DEFAULT_OUTPUT = ROOT / 'build/private-config/workspace-registration.json'


class PrivateArgumentParser(argparse.ArgumentParser):
    def error(self, _message):
        # argparse's normal message echoes unknown values. A mistaken --key
        # argument must not repeat a credential into a terminal or build log.
        self.exit(2, '지원되는 인수를 확인해 주세요. 인증키는 명령행 인수 대신 숨김 입력으로 받습니다.\n')


def prepare(source, target, *, base_url=DEFAULT_BASE_URL, prompt=None):
    """Validate before opening a new output, and never replace an existing key."""
    target = Path(target)
    if source is not None:
        source = Path(source)
        if source.is_symlink() or not source.is_file():
            raise ValueError('Invalid configuration file')
        with source.open('rb') as stream:
            data = encoded(from_bytes(stream.read(MAX_CONFIG + 1)))
    else:
        ask = prompt or getpass.getpass
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            key = ask('집계 서버에 설정한 인증키: ')
        data = encoded({'schema': 1, 'baseUrl': base_url, 'key': key})
    target.parent.mkdir(parents=True, exist_ok=True)
    # The generated staging path and ignored operator path are explicit. Never
    # replace an existing private file or follow an existing output symlink.
    with target.open('xb') as stream:
        stream.write(data)


def main(argv=None):
    parser = PrivateArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, help='Copy a validated existing private configuration')
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--base-url', default=DEFAULT_BASE_URL,
                        help='Web base address; only needed when the default corporate address changes')
    args = parser.parse_args(argv)
    try:
        prepare(args.input, args.output, base_url=args.base_url)
    except (OSError, ValueError, TypeError, EOFError, getpass.GetPassWarning):
        parser.exit(1, '집계 배포 설정을 만들지 못했습니다. 주소와 키 형식, 출력 파일 존재 여부를 확인해 주세요.\n')
    print('Private registration settings prepared. Use for internal distribution only; no key was printed.')


if __name__ == '__main__':
    main()
