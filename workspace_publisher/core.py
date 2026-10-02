"""Public GUI/CLI contract for an isolated, repeatable publisher workflow."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import uuid
import zipfile

from . import git_sync, source_archive
from .config import PublisherError, active, atomic_json, checked_version, normalize, runtime_config, safe_path
from . import publish as publishing


class Publisher:
    def __init__(self, repo_root, emit=None, *, transport=None, runner=None):
        self.repo_root = safe_path(Path(repo_root).absolute())
        self.emit = emit or (lambda event: None)
        self.transport = transport
        self.runner = runner or self._run
        self.work_root = self.repo_root / 'build/publisher'
        self.config_path = self.work_root / 'config.json'

    def _info(self, message):
        self.emit({'kind': 'progress', 'message': message})

    def load_config(self):
        value = normalize({}, require_target=False)
        if not self.config_path.exists():
            return self._note_defaults(value)
        try:
            path = safe_path(self.config_path)
            if path.stat().st_size > 65536:
                raise ValueError()
            value = normalize(json.loads(path.read_text(encoding='utf-8-sig')), require_target=False)
        except (OSError, ValueError, TypeError) as exc:
            raise PublisherError('로컬 게시 설정을 읽지 못했습니다. 게시 창에서 설정을 다시 저장해 주세요. 토큰은 저장하지 않습니다.') from exc
        return self._note_defaults(value)

    def current_version(self):
        """Read just the local version; a fresh build still verifies all source."""
        try:
            path = safe_path(self.repo_root / 'local_app/server.py')
            if path.stat().st_size > 1024 * 1024:
                raise ValueError()
            matches = re.findall(r'^WORKSPACE_VERSION = [\"\']([^\"\']+)[\"\']\s*$',
                                 path.read_text(encoding='utf-8-sig'), re.M)
            if len(matches) != 1:
                raise ValueError()
            return checked_version(matches[0])
        except (OSError, UnicodeError, ValueError) as exc:
            raise PublisherError('현재 소스의 버전을 확인하지 못했습니다. 새 소스 ZIP을 확인해 주세요.') from exc

    def release_notes(self):
        from .release_notes import load_notes
        return load_notes(self.repo_root, self.current_version())

    def _note_defaults(self, value):
        # Settings/artifact recovery remains available even if the source or
        # its bundled notes need repair. The GUI surfaces that notice separately.
        try:
            current = self.current_version()
            old_version = value.get('notesVersion', '')
            if not old_version:
                tag = value.get('releaseTag', '').removeprefix('v')
                if re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', tag):
                    old_version = tag
                else:
                    saved = self._owned(self.work_root / 'last-build.json')
                    if saved.exists() and saved.stat().st_size <= 256 * 1024:
                        try:
                            old_version = checked_version(json.loads(saved.read_text(encoding='utf-8'))['version'])
                        except (OSError, ValueError, KeyError, TypeError):
                            pass
            if old_version == current:
                return {**value, 'notesVersion': current}
            if not old_version and (value['title'] or value['notes']):
                value['notesVersion'] = current
                return value  # Preserve explicit notes saved by older versions.
            defaults = self.release_notes()
            return {**value, 'title': defaults['title'], 'notes': defaults['notes'], 'notesVersion': current}
        except (PublisherError, OSError):
            return value

    def save_config(self, config):
        value = normalize(config, require_target=False)
        if not value.get('notesVersion'):
            try:
                value['notesVersion'] = self.current_version()
            except PublisherError:
                pass  # A verified completed build may still be republished.
        # Refuse accidental settings commits in a differently configured checkout.
        if self._has_git():
            git_sync.git(self.repo_root, 'check-ignore', '--quiet', '--', 'build/publisher/config.json')
        else:
            # Settings always live under this fixed generated path, outside
            # archive inventories and isolated source copies. They must remain
            # editable when retrying an already verified build after source
            # edits; every NEW build independently verifies the whole archive.
            self._owned(self.config_path)
        atomic_json(self.config_path, value)
        return value

    def _has_git(self):
        # Never pick up an unrelated repository containing an extracted ZIP.
        marker = self.repo_root / '.git'
        return marker.exists() or marker.is_symlink()

    def inspect_source(self, *, require_clean=False, cancel=None):
        if not self._has_git():
            return source_archive.inspect(self.repo_root, cancel=cancel)
        value = git_sync.source(self.repo_root, require_clean=require_clean, cancel=cancel)
        return {**value, 'sourceKind': 'git', 'sourceId': value['commit'], 'canSync': True}

    def _require_git_sync(self):
        if not self._has_git():
            raise PublisherError('Download ZIP에는 Git 이력이 없어 소스 반영은 사용할 수 없습니다. 빌드와 배포 파일 게시를 진행해 주세요.')

    def preview_sync(self, config, *, cancel=None):
        self._require_git_sync()
        return git_sync.preview(self.repo_root, config, cancel=cancel)

    def sync(self, config, preview, *, cancel=None):
        self._require_git_sync()
        self._info('확인한 커밋과 선택한 릴리스 태그를 사내 저장소에 반영합니다.')
        return git_sync.sync(self.repo_root, config, preview, cancel=cancel)

    def check_connection(self, config, *, cancel=None):
        self._info('앱과 같은 인증 없는 읽기 방식으로 GitLab 채널을 확인합니다.')
        return publishing.connection(config, transport=self.transport, cancel=cancel)

    def publish_source(self, config, token, token_kind='auto', *, build_result=None, cancel=None):
        """Publish verified original source separately from executable packages."""
        from .source_publish import publish_source
        active(cancel)
        config = normalize(config)
        info = self.inspect_source(require_clean=True, cancel=cancel)
        if build_result is not None:
            self._validated_build(config, build_result)
            identity = build_result.get('sourceId') or build_result.get('commit')
            if identity != info['sourceId'] or build_result['version'] != info['version']:
                raise PublisherError('완성된 배포 파일과 현재 소스가 다릅니다. 같은 버전의 소스에서 다시 배포해 주세요.')
        source = self.repo_root
        if info['sourceKind'] == 'git':
            self.work_root.mkdir(parents=True, exist_ok=True)
            stage = self._owned(self.work_root / ('source-' + uuid.uuid4().hex))
            stage.mkdir()
            source = self._archive(stage, info['commit'], cancel)
        latest = self.inspect_source(require_clean=True, cancel=cancel)
        if latest['sourceId'] != info['sourceId'] or latest['sourceKind'] != info['sourceKind']:
            raise PublisherError('게시 준비 중 소스가 바뀌었습니다. 소스를 다시 확인해 주세요.')
        self._info('프로젝트 기본 브랜치에 검증한 소스·구성 파일을 올립니다. 다른 파일과 기존 이력은 보존합니다.')
        result = publish_source(source, config, token, token_kind=token_kind,
                                expected_source_id=info['sourceId'] if info['sourceKind'] == 'archive' else None,
                                transport=self.transport, emit=self.emit, cancel=cancel)
        self._info(result.get('message', '프로젝트 구성 파일 게시를 확인했습니다.'))
        return result

    def _build_log(self, cwd, step, status, stdout=b'', stderr=b''):
        """Only local tool output; never command arguments, settings or tokens."""
        log_root = self._owned(Path(cwd).parent / 'logs')
        log_root.mkdir(parents=True, exist_ok=True)
        def clean(raw):
            if isinstance(raw, bytes):
                raw = raw[:65536]
                try:
                    value = raw.decode('utf-8')
                except UnicodeError:
                    value = raw.decode('cp949' if os.name == 'nt' else 'utf-8', errors='replace')
            else:
                value = str(raw or '')[:65536]
            for name, secret in os.environ.items():
                if re.search(r'TOKEN|PASSWORD|SECRET|API_?KEY|CREDENTIAL|AUTH', name, re.I) and len(secret) >= 4:
                    value = value.replace(secret, '[비밀값 숨김]')
            value = re.sub(r'(https?://)[^\s/@]+:[^\s/@]+@', r'\1[계정 숨김]@', value, flags=re.I)
            value = re.sub(r'(https?://[^\s?]+)\?[^\s]+', r'\1?[쿼리 숨김]', value, flags=re.I)
            value = re.sub(r'(?i)(\b(?:[\w-]{0,64}token|password|secret|api[_-]?key|authorization)[\"\s]*[:=]\s*[\"]?)[^\r\n,\"]+', r'\1[비밀값 숨김]', value)
            return value
        path = self._owned(log_root / (step + '-' + uuid.uuid4().hex[:12] + '.log'))
        with path.open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(f'단계: {step}\n결과: {status}\n\n표준 출력\n{clean(stdout)}\n\n오류 출력\n{clean(stderr)}\n')
        return path

    def _run(self, arguments, *, cwd, cancel=None, timeout=900):
        active(cancel)
        script = Path(arguments[arguments.index('-File') + 1]).name if '-File' in arguments else 'bundle-check'
        step = {'New-WorkspaceStandalone.ps1': 'standalone', 'New-WorkspaceRelease.ps1': 'release'}.get(script, 'bundle-check')
        label = {'standalone': 'EXE 만들기', 'release': '배포 ZIP 만들기', 'bundle-check': '배포 파일 검사'}[step]
        environment = os.environ.copy()
        environment['PYTHONDONTWRITEBYTECODE'] = '1'
        environment['PYTHONUTF8'] = '1'
        if Path(arguments[0]).name.lower() == 'powershell.exe':
            # PowerShell 7 can export its own incompatible modules to Python.
            # Let only this Windows PowerShell child rebuild its default paths.
            for key in list(environment):
                if key.casefold() == 'psmodulepath':
                    del environment[key]
        try:
            completed = subprocess.run(arguments, cwd=cwd, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), env=environment)
        except (OSError, subprocess.SubprocessError) as exc:
            log = self._build_log(cwd, step, type(exc).__name__, getattr(exc, 'stdout', b''), getattr(exc, 'stderr', b''))
            raise PublisherError(f'{label} 도구를 실행하지 못했거나 시간이 초과되었습니다. 담당자에게 로컬 빌드 로그를 전달해 주세요: {log}') from exc
        log = self._build_log(cwd, step, 'exit ' + str(completed.returncode), completed.stdout, completed.stderr)
        active(cancel)  # A running build step finishes safely before cancellation.
        if completed.returncode:
            raise PublisherError(f'{label} 단계가 실패했습니다. 담당자에게 로컬 빌드 로그를 전달해 주세요: {log}')
        return completed

    def _owned(self, path):
        path = safe_path(Path(path))
        try:
            path.relative_to(safe_path(self.work_root))
        except ValueError as exc:
            raise PublisherError('이 도구가 만든 빌드 폴더만 사용할 수 있습니다.') from exc
        return path

    def _archive(self, stage, commit, cancel):
        archive_path = stage / 'source.zip'
        git_sync.git(self.repo_root, 'archive', '--format=zip', '--output=' + str(archive_path), commit, cancel=cancel)
        source = stage / 'source'
        source.mkdir()
        seen, total = set(), 0
        with zipfile.ZipFile(archive_path) as archive:
            if len(archive.infolist()) > 20000:
                raise PublisherError('소스 파일 수가 게시 도구의 확인 범위를 넘었습니다.')
            for member in archive.infolist():
                active(cancel)
                name = member.filename.replace('\\', '/')
                parts = name.rstrip('/').split('/')
                mode = stat.S_IFMT(member.external_attr >> 16)
                if (name != member.orig_filename.replace('\\', '/') or name.casefold() in seen
                        or any(not part or part in {'.', '..'} or ':' in part for part in parts)
                        or mode not in {0, stat.S_IFREG, stat.S_IFDIR} or member.external_attr & 0x400):
                    raise PublisherError('소스에 연결된 파일이나 안전하지 않은 경로가 있어 별도 빌드를 만들지 못했습니다.')
                seen.add(name.casefold())
                total += member.file_size
                if total > 512 * 1024 * 1024:
                    raise PublisherError('반입한 소스가 게시 도구의 확인 크기를 넘었습니다.')
                target = safe_path(source.joinpath(*parts))
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member) as incoming, target.open('xb') as outgoing:
                        shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
        return source

    def _copy_sdk(self, source):
        try:
            lock = json.loads((source / 'deploy/WebView2.lock.json').read_text(encoding='utf-8-sig'))
            version, digest = lock['version'], lock['sha256']
            if not re.fullmatch(r'[A-Za-z0-9_.-]+', version) or not re.fullmatch(r'[a-f0-9]{64}', digest):
                raise ValueError()
            original = safe_path(self.repo_root / 'build/desktop-sdk' / (version + '.nupkg'))
            if not original.is_file() or not 0 < original.stat().st_size < 256 * 1024 * 1024:
                raise ValueError()
            raw = original.read_bytes()
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError()
            target = source / 'build/desktop-sdk' / original.name
            target.parent.mkdir(parents=True)
            target.write_bytes(raw)
        except (ValueError, KeyError, OSError) as exc:
            raise PublisherError('검증된 WebView2 SDK가 없습니다. 배포 창에서 SDK 다운로드 또는 사내 SDK 파일 선택으로 준비해 주세요. 자동 다운로드는 하지 않았습니다.') from exc

    def build(self, config, *, cancel=None):
        active(cancel)
        config = normalize(config)
        info = self.inspect_source(require_clean=True, cancel=cancel)
        self.save_config(config)
        self.work_root.mkdir(parents=True, exist_ok=True)
        stage = self._owned(self.work_root / ('build-' + uuid.uuid4().hex))
        stage.mkdir()
        if info['sourceKind'] == 'git':
            self._info('확인한 Git 커밋을 별도 빌드 폴더로 복사합니다. 반입한 소스 파일은 수정하지 않습니다.')
            source = self._archive(stage, info['commit'], cancel)
        else:
            self._info('다운로드 ZIP의 파일 목록과 체크섬을 확인해 별도 폴더로 복사합니다. Git 이력을 생성하거나 원본을 수정하지 않습니다.')
            source = source_archive.copy_verified(self.repo_root, stage / 'source', info['sourceId'], cancel=cancel)
        self._copy_sdk(source)
        injected = runtime_config(config)
        config_path = stage / 'workspace-update-source.json'
        atomic_json(config_path, injected)
        # Refuse a moving checkout; build output is always tied to one reviewed HEAD.
        latest = self.inspect_source(require_clean=True, cancel=cancel)
        if latest['sourceKind'] != info['sourceKind'] or latest['sourceId'] != info['sourceId']:
            raise PublisherError('빌드 준비 중 소스가 바뀌었습니다. 다시 빌드해 주세요.')
        try:
            from local_app.windows_process import powershell_path
            powershell = powershell_path()
        except (OSError, AttributeError) as exc:
            raise PublisherError('배포 빌드는 Windows PowerShell이 있는 Windows PC에서 실행해 주세요.') from exc
        prefix = [powershell, '-NoProfile', '-NoLogo', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File']
        output = stage / 'standalone'
        release = stage / 'release'
        self._info('회사 업데이트 주소를 포함한 EXE를 별도 폴더에서 빌드·검증합니다. 취소 요청은 현재 빌드 단계가 끝난 뒤 반영됩니다.')
        self.runner([*prefix, str(source / 'deploy/New-WorkspaceStandalone.ps1'), '-UpdateConfig', str(config_path),
                     '-ConfigPython', sys.executable, '-OutputDirectory', str(output),
                     '-VerificationDirectory', str(stage / 'v')], cwd=source, cancel=cancel)
        active(cancel)
        exe = output / f"Company-Workspace-{info['version']}.exe"
        self._info('EXE와 같은 앱 파일의 VBS ZIP·체크섬을 만들고 서로 비교합니다.')
        self.runner([*prefix, str(source / 'deploy/New-WorkspaceRelease.ps1'), '-UpdateConfig', str(config_path),
                     '-ConfigPython', sys.executable, '-StandaloneExe', str(exe), '-OutputDirectory', str(release)],
                    cwd=source, cancel=cancel)
        active(cancel)
        try:
            standalone = json.loads((source / f"build/workspace-standalone-{info['version']}-build.json").read_text(encoding='utf-8-sig'))
            released = json.loads((source / f"build/workspace-release-{info['version']}.json").read_text(encoding='utf-8-sig'))
            if (standalone['version'] != info['version'] or standalone['embeddedPayloadVerified'] is not True
                    or standalone['pythonBundled'] is not False or Path(standalone['exe']) != exe
                    or hashlib.sha256(safe_path(exe).read_bytes()).hexdigest() != standalone['sha256']
                    or type(standalone['payloadFiles']) is not int or standalone['payloadFiles'] < 1
                    or released['version'] != info['version'] or released['pythonBundled'] is not False
                    or released['identicalSourceFiles'] != standalone['payloadFiles']
                    or Path(released['vbsZip']) != release / f"Company-Workspace-{info['version']}-vbs.zip"
                    or Path(released['exeZip']) != release / f"Company-Workspace-{info['version']}-exe.zip"):
                raise ValueError()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise PublisherError('EXE 내장 파일과 VBS 공통 파일의 빌드 검증 결과가 일치하지 않습니다. 게시하지 않았습니다.') from exc
        self.runner([sys.executable, str(source / 'scripts/test-lab/check-workspace-bundle.py'),
                     str(release / f"Company-Workspace-{info['version']}-vbs.zip"), '--update-config', str(config_path)],
                    cwd=source, cancel=cancel, timeout=120)
        files = publishing.verify_files(release, info['version'], injected)
        # Archive users have no Git status to expose edits made while the
        # compiler was running. Revalidate both copies before recording success.
        if info['sourceKind'] == 'archive':
            for root in (self.repo_root, source):
                if source_archive.inspect(root, cancel=cancel)['sourceId'] != info['sourceId']:
                    raise PublisherError('빌드 중 다운로드 소스가 바뀌었습니다. 배포 성공으로 기록하지 않았습니다.')
        result = {'version': info['version'], 'commit': info['commit'], 'directory': str(release),
                  'sourceRoot': str(source), 'files': files, 'runtimeConfig': injected,
                  'sourceKind': info['sourceKind'], 'sourceId': info['sourceId']}
        atomic_json(stage / 'build-result.json', result)
        atomic_json(self.work_root / 'last-build.json', result)
        self._info('소스를 바꾸지 않고 배포 파일 세 개를 만들고 검증했습니다. 게시 버튼으로 서버에 반영할 수 있습니다.')
        return result

    def _validated_build_metadata(self, build_result):
        try:
            legacy_fields = {'version', 'commit', 'directory', 'sourceRoot', 'files', 'runtimeConfig'}
            if (not isinstance(build_result, dict)
                    or set(build_result) not in (legacy_fields, legacy_fields | {'sourceKind', 'sourceId'})
                    or not isinstance(build_result['commit'], str)):
                raise ValueError()
            source_kind = build_result.get('sourceKind', 'git')
            source_id = build_result.get('sourceId', build_result['commit'])
            if (source_kind not in {'git', 'archive'} or not isinstance(source_id, str)
                    or (source_kind == 'git' and (not re.fullmatch(r'[a-f0-9]{40,64}', source_id)
                                                 or source_id != build_result['commit']))
                    or (source_kind == 'archive' and (not re.fullmatch(r'[a-f0-9]{64}', source_id)
                                                     or build_result['commit'] != ''))):
                raise ValueError()
            directory = self._owned(Path(build_result['directory']))
            stage = directory.parent
            if (stage.parent != self.work_root or not re.fullmatch(r'build-[a-f0-9]{32}', stage.name)
                    or directory.name != 'release' or Path(build_result['sourceRoot']) != stage / 'source'):
                raise ValueError()
            saved_path = self._owned(stage / 'build-result.json')
            if saved_path.stat().st_size > 256 * 1024 or json.loads(saved_path.read_text(encoding='utf-8')) != build_result:
                raise ValueError()
            checked_version(build_result['version'])
            from local_app.update_source import source_from_config
            source = source_from_config(build_result['runtimeConfig'])
            if source.provider != 'gitlab' or source.config != build_result['runtimeConfig']:
                raise ValueError()
            if not isinstance(build_result['files'], list) or len(build_result['files']) != 3:
                raise ValueError()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise PublisherError('이전 빌드 기록을 확인하지 못했습니다. 원래 빌드 폴더와 기록을 복원하거나 새 소스 폴더에서 진행해 주세요.') from exc
        return directory

    def _validated_build(self, config, build_result):
        directory = self._validated_build_metadata(build_result)
        try:
            expected_source = runtime_config(config)
            if build_result['runtimeConfig'] != expected_source:
                raise ValueError()
            files = publishing.verify_files(directory, build_result['version'], expected_source)
            if files != build_result['files']:
                raise ValueError()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise PublisherError('저장된 배포 파일의 위치·설정·체크섬을 확인하지 못했습니다. 원래 빌드 폴더와 게시 대상 설정을 복원해 주세요. 같은 버전 파일을 새로 만들면 기존 게시 파일과 충돌할 수 있습니다.') from exc
        return build_result

    def load_last_build(self, config, *, cancel=None):
        """Recover exact retry bytes even when the source HEAD has moved."""
        active(cancel)
        try:
            saved = self._owned(self.work_root / 'last-build.json')
            if not saved.exists():
                return None
            if saved.stat().st_size > 256 * 1024:
                raise ValueError()
            result = json.loads(saved.read_text(encoding='utf-8'))
        except (OSError, ValueError, TypeError) as exc:
            raise PublisherError('이전 빌드 기록을 읽지 못했습니다. build/publisher의 원래 빌드 폴더와 기록을 복원해 주세요.') from exc
        recovered = self._validated_build(config, result)
        active(cancel)
        return recovered

    def publish(self, config, build_result, token, token_kind='auto', *, cancel=None):
        active(cancel)
        config = normalize(config)
        self._validated_build(config, build_result)
        return publishing.publish(config, build_result, token, token_kind=token_kind,
                                  transport=self.transport, emit=self.emit, cancel=cancel)

    def prepare_deploy(self, config, *, build_result=None, cancel=None):
        """Reuse verified bytes on retry; build when this version has no artifact."""
        active(cancel)
        config = normalize(config)
        candidate = build_result
        if candidate is None:
            saved = self._owned(self.work_root / 'last-build.json')
            if saved.exists():
                # Inspect only bounded metadata here. Applicable artifacts are
                # always fully verified before reuse, never silently rebuilt if
                # corrupt. A different target/version gets a separate new build.
                try:
                    if saved.stat().st_size > 256 * 1024:
                        raise ValueError()
                    candidate = json.loads(saved.read_text(encoding='utf-8'))
                    if not isinstance(candidate, dict):
                        raise ValueError()
                except (OSError, ValueError, TypeError) as exc:
                    raise PublisherError('이전 빌드 기록을 확인하지 못했습니다. 원래 기록을 복원하거나 새 소스 폴더에서 진행해 주세요.') from exc
        if candidate is not None:
            self._validated_build_metadata(candidate)
        try:
            version = self.current_version()
        except PublisherError:
            if candidate is None:
                raise
            return self._validated_build(config, candidate)
        if (isinstance(candidate, dict) and candidate.get('version') == version
                and candidate.get('runtimeConfig') == runtime_config(config)):
            checked = self._validated_build(config, candidate)
            self._info('이전에 만든 배포 파일을 확인했습니다. 같은 파일로 게시를 이어갑니다.')
            return checked
        return self.build(config, cancel=cancel)
