"""Same-user file-association handoff; never install or execute a package."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .extension_install import MAX_ARCHIVE, _read
from .extensions import ExtensionError


class OpenError(ValueError):
    pass


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise OpenError('앱 연결 주소가 변경되었습니다. AX Workspace를 다시 연 뒤 확장 파일을 열어 주세요.')


def runtime_connection(state):
    try:
        record = json.loads(_read(Path(state) / 'runtime.json', 65536).decode('utf-8-sig'))
        url = record.get('url')
        if not isinstance(url, str) or len(url) > 2048:
            raise ValueError()
        parsed = urlsplit(url)
        if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port
                or parsed.username is not None or parsed.password is not None or parsed.path != '/'
                or parsed.query or not re.fullmatch(r'token=[A-Za-z0-9_-]{20,200}', parsed.fragment)):
            raise ValueError()
        return 'http://127.0.0.1:' + str(parsed.port), parsed.fragment.removeprefix('token=')
    except (OSError, ValueError, UnicodeError, AttributeError, ExtensionError):
        raise OpenError('실행 중인 AX Workspace 연결 정보를 확인하지 못했습니다. 앱을 먼저 열어 주세요.') from None


def handoff(state, package, *, timeout=25, sleep=time.sleep):
    path = Path(package)
    if not path.is_absolute() or path.suffix.lower() not in {'.axextension', '.zip'}:
        raise OpenError('다운로드한 AX 확장(.axextension) 파일을 다시 열어 주세요.')
    try:
        raw = _read(path, MAX_ARCHIVE)
    except (OSError, ValueError):
        raise OpenError('확장 파일을 읽지 못했습니다. 압축 파일 안이 아닌 다운로드한 파일 자체를 열어 주세요.') from None
    opener = build_opener(ProxyHandler({}), NoRedirects())
    deadline = time.monotonic() + timeout
    while True:
        try:
            origin, token = runtime_connection(state)
            headers = {'Authorization': 'Bearer ' + token, 'Origin': origin}
            with opener.open(Request(origin + '/api/bootstrap', headers=headers), timeout=3) as response:
                boot = json.loads(response.read(2 * 1024 * 1024).decode('utf-8'))
            break
        except HTTPError:
            raise OpenError('현재 앱의 연결 권한을 확인할 수 없습니다. 앱과 같은 사용자·실행 권한으로 확장 파일을 열어 주세요.') from None
        except (URLError, TimeoutError, OSError, OpenError):
            if time.monotonic() >= deadline:
                raise OpenError('AX Workspace 시작을 확인하지 못했습니다. 앱을 연 뒤 확장 파일을 다시 열어 주세요.') from None
            sleep(.2)
        except (ValueError, UnicodeError):
            raise OpenError('앱 연결 정보를 확인하지 못했습니다. AX Workspace를 다시 열어 주세요.') from None
    if not isinstance(boot, dict) or boot.get('application') != 'company-workspace' or boot.get('extensionImportProtocol') != 1:
        raise OpenError('실행 중인 AX Workspace는 확장 파일 바로 열기를 지원하지 않습니다. 최신 설치본으로 업데이트하고, 작업을 저장한 뒤 앱을 완전히 종료·재실행해 주세요.')
    if boot.get('closing') or boot.get('closed'):
        raise OpenError('AX Workspace가 종료 중입니다. 앱을 다시 연 뒤 확장 파일을 열어 주세요.')
    try:
        request = Request(origin + '/api/extensions/import/file', data=raw,
                          headers={**headers, 'Content-Type': 'application/octet-stream'})
        with opener.open(request, timeout=20) as response:
            accepted = json.loads(response.read(8192).decode('utf-8'))
        if accepted.get('accepted') is not True or accepted.get('protocol') != 1:
            raise ValueError()
        return accepted
    except HTTPError as error:
        messages = {409: '앱에 열린 확장 설치 확인을 먼저 마쳐 주세요. 설치 또는 취소한 뒤 파일을 다시 열 수 있습니다.',
                    413: '확장 파일 크기가 지원 범위를 넘었습니다. 배포자에게 문의해 주세요.',
                    403: '앱과 같은 사용자·실행 권한으로 확장 파일을 열어 주세요.'}
        raise OpenError(messages.get(error.code, '확장 패키지를 확인하지 못했습니다. 원본 파일을 다시 받거나 배포자에게 문의해 주세요.')) from None
    except (OSError, ValueError, URLError):
        # Receipt can be ambiguous. Never retry the upload automatically.
        raise OpenError('확장 열기 결과를 확인하지 못했습니다. 먼저 앱의 설치 확인창을 확인해 주세요. 창이 없으면 파일을 다시 열어 주세요.') from None


def main(argv=None):
    parser = argparse.ArgumentParser(description='AX Workspace extension file handoff')
    parser.add_argument('--state', required=True)
    parser.add_argument('--package', required=True)
    args = parser.parse_args(argv)
    try:
        handoff(args.state, args.package)
    except OpenError as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
