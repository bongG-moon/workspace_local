"""Previewed, non-forced publication of one branch and its selected release tag."""
from __future__ import annotations

from pathlib import Path
import os
import re
import subprocess

from .config import PublisherError, active, checked_version, normalize, safe_path


def git(repo, *arguments, cancel=None, timeout=120, accepted_codes=(0,)):
    active(cancel)
    environment = os.environ.copy()
    environment['GIT_TERMINAL_PROMPT'] = '0'
    try:
        result = subprocess.run(['git', '-C', str(repo), *arguments], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), env=environment)
    except (OSError, subprocess.SubprocessError) as exc:
        raise PublisherError('Git 작업을 완료하지 못했습니다. Git 설치, 사내망 연결과 기존 SSH 인증을 확인해 주세요.') from exc
    active(cancel)
    if result.returncode not in accepted_codes:
        raise PublisherError('Git 작업을 완료하지 못했습니다. 저장소·브랜치·태그와 기존 SSH 권한을 확인해 주세요. 강제 반영은 하지 않았습니다.')
    return result.stdout.decode('utf-8', errors='replace').strip()


def source(repo, *, require_clean=False, cancel=None):
    repo = safe_path(Path(repo).absolute())
    if Path(git(repo, 'rev-parse', '--show-toplevel', cancel=cancel)).resolve() != repo.resolve():
        raise PublisherError('선택한 폴더가 Git 저장소의 최상위 폴더가 아닙니다.')
    commit = git(repo, 'rev-parse', '--verify', 'HEAD^{commit}', cancel=cancel)
    if not re.fullmatch(r'[a-f0-9]{40,64}', commit):
        raise PublisherError('소스 커밋을 확인하지 못했습니다.')
    branch = git(repo, 'rev-parse', '--abbrev-ref', 'HEAD', cancel=cancel)
    if branch == 'HEAD':
        branch = ''
    clean = not git(repo, 'status', '--porcelain=v1', '--untracked-files=normal', cancel=cancel)
    text = git(repo, 'show', commit + ':local_app/server.py', cancel=cancel)
    versions = re.findall(r'^WORKSPACE_VERSION = [\"\']([^\"\']+)[\"\']\s*$', text, re.M)
    if len(versions) != 1:
        raise PublisherError('소스에서 앱 버전을 확인하지 못했습니다.')
    version = checked_version(versions[0])
    expected_tag = 'v' + version
    tags = git(repo, 'tag', '--points-at', commit, cancel=cancel).splitlines()
    if require_clean and not clean:
        raise PublisherError('반입한 소스에 저장하지 않은 변경이 있습니다. 변경을 커밋하거나 정리한 뒤 다시 실행해 주세요.')
    return {'repo': str(repo), 'commit': commit, 'branch': branch, 'version': version,
            'clean': clean, 'releaseTag': expected_tag if expected_tag in tags else ''}


def _remote(config):
    name, url = config['remoteName'], config['remoteUrl']
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,79}', name) or name.lower() == 'origin':
        raise PublisherError('사내 원격 이름은 origin과 다른 이름(예: intranet)을 사용해 주세요.')
    if (not re.fullmatch(r'(?:[A-Za-z0-9_.-]+@)?[A-Za-z0-9.-]+:[A-Za-z0-9_./-]+', url)
            and not re.fullmatch(r'ssh://(?:[A-Za-z0-9_.-]+@)?[A-Za-z0-9.-]+(?::[0-9]{1,5})?/[A-Za-z0-9_./-]+', url)):
        raise PublisherError('사내 저장소의 SSH 주소를 입력해 주세요. HTTPS 주소나 토큰은 Git 반영 주소로 사용하지 않습니다.')
    if '..' in url.split('/') or url.startswith('-'):
        raise PublisherError('사내 SSH 주소를 확인해 주세요.')
    return name, url


def preview(repo, config, *, cancel=None):
    config = normalize(config, require_target=False)
    info = source(repo, require_clean=True, cancel=cancel)
    if not info['branch']:
        raise PublisherError('태그만 체크아웃한 소스는 빌드할 수 있지만 반영할 브랜치가 없습니다. 반영할 로컬 브랜치를 선택해 주세요.')
    name, url = _remote(config)
    # An explicit URL is still subject to user/system/local Git rewriting.
    # Refuse matching rules before creating a remote or accessing the network.
    # Read effective configuration, including includes and environment config.
    rewrites = git(repo, 'config', '--null', '--get-regexp',
                   r'^url\..*\.(insteadof|pushinsteadof)$',
                   cancel=cancel, accepted_codes=(0, 1))
    for row in rewrites.split('\0'):
        if not row:
            continue
        key, separator, prefix = row.partition('\n')
        if not separator or url.startswith(prefix):
            raise PublisherError('기존 Git 주소 치환 설정이 입력한 저장소에 적용됩니다. 사내 저장소의 실제 SSH 주소와 Git 설정을 확인해 주세요. 원격을 만들거나 소스를 전송하지 않았습니다.')
    remotes = git(repo, 'remote', cancel=cancel).splitlines()
    if name in remotes:
        existing = git(repo, 'remote', 'get-url', '--all', name, cancel=cancel).splitlines()
        if existing != [url]:
            raise PublisherError('같은 원격 이름에 다른 주소가 등록되어 있습니다. 다른 사내 원격 이름을 지정해 주세요.')
        push_urls = git(repo, 'remote', 'get-url', '--push', '--all', name, cancel=cancel).splitlines()
        if push_urls != [url]:
            raise PublisherError('원격의 별도 push 주소가 다릅니다. 반영할 사내 원격을 다시 확인해 주세요.')
    tag = config['releaseTag']
    tag_object = ''
    if tag:
        if tag != 'v' + info['version']:
            raise PublisherError('선택한 태그가 현재 앱 버전과 다릅니다.')
        if git(repo, 'rev-parse', '--verify', 'refs/tags/' + tag + '^{commit}', cancel=cancel) != info['commit']:
            raise PublisherError('선택한 릴리스 태그가 현재 소스 커밋을 가리키지 않습니다.')
        tag_object = git(repo, 'rev-parse', '--verify', 'refs/tags/' + tag, cancel=cancel)
    return {**info, 'remoteName': name, 'remoteUrl': url, 'tag': tag, 'tagObject': tag_object,
            'summary': f"{url}\n브랜치: {info['branch']}\n버전: {info['version']}\n커밋: {info['commit']}\n태그: {tag or '반영하지 않음'}"}


def sync(repo, config, approved_preview, *, cancel=None):
    current = preview(repo, config, cancel=cancel)
    fields = ('remoteName', 'remoteUrl', 'branch', 'commit', 'tag', 'tagObject', 'version')
    if not isinstance(approved_preview, dict) or any(current[key] != approved_preview.get(key) for key in fields):
        raise PublisherError('미리보기 이후 소스나 반영 위치가 바뀌었습니다. 소스 반영 미리보기를 다시 확인해 주세요.')
    name, url = current['remoteName'], current['remoteUrl']
    if name not in git(repo, 'remote', cancel=cancel).splitlines():
        git(repo, 'remote', 'add', name, url, cancel=cancel)
    # An explicit URL avoids remote push configuration and URL-selection races.
    refs = [current['commit'] + ':refs/heads/' + current['branch']]
    if current['tag']:
        refs.append(current['tagObject'] + ':refs/tags/' + current['tag'])
    git(repo, 'push', '--atomic', '--no-follow-tags', url, *refs, cancel=cancel, timeout=180)
    remote = git(repo, 'ls-remote', url, 'refs/heads/' + current['branch'], cancel=cancel)
    if remote.split('\t', 1)[0] != current['commit']:
        raise PublisherError('원격에 반영된 커밋을 확인하지 못했습니다. 소스 반영 결과를 다시 확인해 주세요.')
    if current['tag']:
        actual = git(repo, 'ls-remote', url, 'refs/tags/' + current['tag'], 'refs/tags/' + current['tag'] + '^{}', cancel=cancel)
        rows = dict(line.split('\t', 1)[::-1] for line in actual.splitlines() if '\t' in line)
        if rows.get('refs/tags/' + current['tag'] + '^{}', rows.get('refs/tags/' + current['tag'])) != current['commit']:
            raise PublisherError('원격 릴리스 태그의 커밋을 확인하지 못했습니다.')
    return {**current, 'verified': True, 'message': '선택한 브랜치와 태그의 사내 저장소 반영을 확인했습니다.'}
