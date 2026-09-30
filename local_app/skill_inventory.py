"""Read-only Claude skill and plugin metadata discovery.

Native discovery reads metadata in the Workspace process without executing or
returning skill bodies. The legacy registered-core CLI adapter below is retained
for source compatibility and is not invoked by the application.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

MAX_ITEMS = 1000
MAX_HEADER_BYTES = 16 * 1024
MAX_SKILL_BYTES = 256 * 1024
SOURCES = {'user', 'project', 'personal', 'company', 'plugin', 'corporate'}


def _metadata_path(path):
    if not path.is_absolute() or '..' in path.parts or len(str(path)) > 2048:
        raise ValueError('Invalid metadata path')
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        # Allow Cloud Files placeholders, but never follow name-surrogate links.
        tag = getattr(info, 'st_reparse_tag', 0)
        cloud = tag in {0x9000001A | (index << 12) for index in range(16)}
        if stat.S_ISLNK(info.st_mode) or (getattr(info, 'st_file_attributes', 0) & 0x400 and not cloud):
            raise ValueError('Linked metadata path')
    return path


def _json_metadata(path):
    _metadata_path(path)
    if not path.exists():
        return {}
    if not path.is_file():
        raise ValueError('Invalid metadata file')
    with path.open('rb') as stream:
        raw = stream.read(512 * 1024 + 1)
    if len(raw) > 512 * 1024:
        raise ValueError('Oversized metadata')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate metadata key')
            result[key] = value
        return result
    try:
        value = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=unique)
    except RecursionError as exc:
        raise ValueError('Metadata nesting limit exceeded') from exc
    if not isinstance(value, dict):
        raise ValueError('Invalid metadata object')
    pending = [(value, 0)]
    while pending:
        node, depth = pending.pop()
        if isinstance(node, (dict, list)):
            if depth > 32:
                raise ValueError('Metadata nesting limit exceeded')
            pending.extend((item, depth + 1) for item in (node.values() if isinstance(node, dict) else node))
    return value


def project_lineage(workspace, config):
    """Bound ancestor discovery to a repository or nearest local Claude root."""
    if workspace is None:
        return []
    project = _metadata_path(Path(workspace))
    if not project.is_dir():
        raise ValueError('Unavailable metadata folder')
    lineage = []
    for directory in (project, *list(project.parents)[:31]):
        if directory == Path(directory.anchor) or directory == Path.home() or directory == config or directory / '.claude' == config:
            break
        _metadata_path(directory)
        lineage.append(directory)
        marker = directory / '.git'
        # Git worktree marker contents are never read or followed.
        if marker.exists() or marker.is_symlink():
            return lineage
    for index, directory in enumerate(lineage):
        marker = _metadata_path(directory / '.claude')
        if marker.is_dir():
            return lineage[:index + 1]
    return lineage[:1]


def effective_plugins(config, workspace=None):
    """Exact user -> ancestor project -> local overlays; unreadable is unknown."""
    lineage = project_lineage(workspace, config)
    settings = [config / 'settings.json']
    for directory in reversed(lineage):
        settings.extend((directory / '.claude/settings.json', directory / '.claude/settings.local.json'))
    enabled = {}
    for path in settings:
        value = _json_metadata(path).get('enabledPlugins', {})
        if not isinstance(value, dict) or len(value) > MAX_ITEMS:
            raise ValueError('Invalid enabled plugin settings')
        for name, flag in value.items():
            if not isinstance(name, str) or len(name) > 256 or type(flag) is not bool:
                raise ValueError('Invalid enabled plugin setting')
            enabled[name] = flag
    return enabled


def same_profile_path(path):
    """Do not traverse a sibling Windows user profile via metadata records."""
    home = Path.home().absolute()
    path = _metadata_path(Path(path))
    if home.parent.name.casefold() in {'users', 'documents and settings'} and path.is_relative_to(home.parent) and not path.is_relative_to(home):
        raise ValueError('Another user profile is outside metadata scope')
    return path


def _scalar(value):
    value = value.strip()
    if value.startswith('"'):
        decoded, end = json.JSONDecoder().raw_decode(value)
        suffix = value[end:].strip()
        if not isinstance(decoded, str) or (suffix and not suffix.startswith('#')):
            raise ValueError('Invalid metadata scalar')
        return decoded
    if value.startswith("'"):
        match = re.fullmatch(r"'((?:[^']|'')*)'\s*(?:#.*)?", value)
        if match is None:
            raise ValueError('Invalid metadata scalar')
        return match.group(1).replace("''", "'")
    return value.split(' #', 1)[0].strip()


def _skill_header(path, *, command=False):
    _metadata_path(path)
    info = path.stat()
    valid_name = path.suffix.casefold() == '.md' if command else path.name == 'SKILL.md'
    if not valid_name or not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SKILL_BYTES:
        raise ValueError('Invalid skill file')
    lines = []
    with path.open('rb') as stream:
        first = stream.readline(MAX_HEADER_BYTES + 1)
        if len(first) > MAX_HEADER_BYTES:
            raise ValueError('Oversized metadata header')
        if first.decode('utf-8-sig').strip() != '---':
            return {}
        remaining = MAX_HEADER_BYTES - len(first)
        while remaining > 0:
            raw = stream.readline(remaining + 1)
            if not raw or len(raw) > remaining:
                raise ValueError('Incomplete metadata header')
            remaining -= len(raw)
            line = raw.decode('utf-8').rstrip('\r\n')
            if line.strip() in {'---', '...'}:
                break
            lines.append(line)
        else:
            raise ValueError('Oversized metadata header')
    fields, block = {}, None
    for line in lines:
        if line[:1].isspace():
            if block == 'description':
                fields['description'] += ' ' + line.strip()
            continue
        block = None
        match = re.match(r'^(name|description|user-invocable|disable-model-invocation)\s*:\s*(.*?)\s*$', line)
        if not match:
            continue
        key, value = match.groups()
        if key in fields:
            raise ValueError('Duplicate skill metadata')
        if key == 'description' and value in {'>', '|', '>-', '|-', '>+', '|+'}:
            fields[key], block = '', key
        else:
            fields[key] = _scalar(value)
            if key == 'description':
                block = key
        if key in {'user-invocable', 'disable-model-invocation'} and fields[key].casefold() not in {'true', 'false'}:
            raise ValueError('Invalid invocation metadata')
    return fields


def discover_native_metadata(config, workspace=None, *, reference_roots=(), include_commands=False):
    """Discover bounded native metadata without executing any installation code.

    Missing registrations do not hide ~/.claude/skills, selected-project skills,
    or enabled plugin metadata. Results are discovery evidence, not CLI state.
    """
    config = Path(config)
    rows, seen, failures, limited = [], set(), [], False
    command_entries = 0
    def fail(code):
        if len(failures) < MAX_ITEMS:
            failures.append(code)
    def read(path):
        try:
            return _json_metadata(path)
        except (ValueError, OSError, UnicodeError, TypeError):
            fail('metadata_unreadable')
            return {}
    try:
        _metadata_path(config)
        lineage = project_lineage(workspace, config)
    except (ValueError, OSError):
        lineage = []
        fail('scope_unreadable')

    def add(file, source, scope, namespace, installation, label, scope_label='', command=False, command_root=None):
        nonlocal limited
        if len(rows) >= MAX_ITEMS:
            limited = True
            return
        key = (os.path.normcase(str(file)), source, scope, installation)
        if key in seen:
            return
        seen.add(key)
        try:
            if not file.exists():
                return
            fields = _skill_header(file, command=command)
            name = file.stem if command else fields.get('name') or file.parent.name
            if not name or len(name) > 100 or re.search(r'[\s/:\\\x00-\x1f]', name):
                raise ValueError('Invalid skill name')
            command_name = name
            if command and command_root is not None:
                segments = file.relative_to(command_root).with_suffix('').parts
                if any(not part or len(part) > 100 or re.search(r'[\s/:\\\x00-\x1f]', part) for part in segments):
                    raise ValueError('Invalid command namespace')
                command_name = ':'.join(segments)
            invocation = namespace + ':' + command_name if namespace else command_name if source in {'user', 'project'} else ''
            visible = fields.get('user-invocable', 'true').casefold() == 'true'
            digest = hashlib.sha256('\0'.join(key).encode('utf-8')).hexdigest()[:24]
            rows.append({'candidateId': source + ':' + digest, 'name': name,
                         'description': _text(fields.get('description', ''), 800),
                         'source': source, 'storageScope': scope, 'invocation': invocation,
                         'explicitOnly': fields.get('disable-model-invocation', 'false').casefold() == 'true',
                         'userInvocable': visible, 'kind': 'internal' if not visible else 'skill' if invocation else 'reference',
                         'pluginNamespace': namespace, 'originLabel': label,
                         'scopeLabel': scope_label or ('선택 폴더' if scope == 'project' else '공통'),
                         'installationId': hashlib.sha256(installation.encode('utf-8')).hexdigest()[:24]
                                           if installation else ''})
        except (ValueError, OSError, UnicodeError, TypeError):
            fail('skill_header_unreadable')

    def scan_commands(root, source, scope, namespace='', installation='', label='', scope_label=''):
        # Legacy command filenames set the final segment; nested directories
        # preserve their colon namespace. Verified against Claude 2.1.284:
        # commands/sc/task.md is reported as /sc:task, not /task. Frontmatter
        # name does not rename them. Scan only the selected configuration roots
        # and explicit enabled-plugin roots, never arbitrary work files.
        # https://code.claude.com/docs/en/skills#how-a-skill-gets-its-command-name
        nonlocal limited, command_entries
        if not include_commands:
            return
        if command_entries >= MAX_ITEMS:
            limited = True
            return
        pending = [(root, 0)]
        while pending:
            directory, depth = pending.pop()
            try:
                _metadata_path(directory)
                if not directory.exists():
                    continue
                if directory.is_file():
                    if directory.suffix.casefold() == '.md':
                        add(directory, source, scope, namespace, installation, label, scope_label, command=True)
                    continue
                with os.scandir(directory) as entries:
                    for entry in entries:
                        command_entries += 1
                        if command_entries > MAX_ITEMS or len(rows) >= MAX_ITEMS:
                            limited = True
                            return
                        child = _metadata_path(Path(entry.path))
                        if child.is_file() and child.suffix.casefold() == '.md':
                            add(child, source, scope, namespace, installation, label, scope_label,
                                command=True, command_root=root)
                        elif child.is_dir() and not child.name.startswith('.'):
                            if depth < 4:
                                pending.append((child, depth + 1))
                            else:
                                limited = True
            except (ValueError, OSError, TypeError):
                fail('command_directory_unreadable')

    def scan(root, source, scope, namespace='', installation='', label='', scope_label=''):
        nonlocal limited
        if len(rows) >= MAX_ITEMS:
            limited = True
            return
        try:
            _metadata_path(root)
            if not root.exists():
                return
            if root.is_file():
                if root.name == 'SKILL.md':
                    add(root, source, scope, namespace, installation, label, scope_label)
                return
            if (root / 'SKILL.md').exists():
                add(root / 'SKILL.md', source, scope, namespace, installation, label, scope_label)
                return
            entries = []
            with os.scandir(root) as stream:
                for entry in stream:
                    if len(entries) >= MAX_ITEMS:
                        limited = True
                        break
                    entries.append(entry.name)
            for name in sorted(entries, key=str.casefold):
                try:
                    child = _metadata_path(root / name)
                    if child.is_dir():
                        add(child / 'SKILL.md', source, scope, namespace, installation, label, scope_label)
                except (ValueError, OSError):
                    fail('skill_directory_unreadable')
        except (ValueError, OSError, TypeError):
            fail('skill_directory_unreadable')

    scan(config / 'skills', 'user', 'personal', label='Claude 사용자 스킬')
    scan_commands(config / 'commands', 'user', 'personal', label='Claude 사용자 명령')
    for directory in reversed(lineage):
        distance = lineage.index(directory)
        label = '선택 폴더' if distance == 0 else f'상위 {distance}단계 폴더'
        scan(directory / '.claude/skills', 'project', 'project', label='폴더의 Claude 스킬', scope_label=label)
        scan_commands(directory / '.claude/commands', 'project', 'project', label='폴더의 Claude 명령', scope_label=label)
    for root, source, scope in reference_roots:
        if workspace is None and scope == 'project':
            continue
        try:
            root = same_profile_path(root)
            scan(root, source, scope, label='Company Agent 개인 참고 스킬' if source == 'personal' else '회사 참고 스킬')
        except (ValueError, OSError, TypeError):
            fail('reference_scope_unreadable')
    try:
        enabled = effective_plugins(config, workspace)
    except (ValueError, OSError, UnicodeError, TypeError):
        enabled = {}
        fail('plugin_settings_invalid')
    installed = read(config / 'plugins/installed_plugins.json').get('plugins', {})
    if not isinstance(installed, dict):
        fail('plugin_registry_invalid')
        installed = {}
    if len(installed) > MAX_ITEMS:
        limited = True
    for plugin_id, records in list(installed.items())[:MAX_ITEMS]:
        if len(rows) >= MAX_ITEMS:
            limited = True
            break
        if not isinstance(plugin_id, str) or len(plugin_id) > 256 or enabled.get(plugin_id) is not True:
            continue
        if not isinstance(records, list) or len(records) > MAX_ITEMS:
            fail('plugin_records_invalid')
            continue
        eligible = []
        for record in records:
            try:
                if not isinstance(record, dict):
                    raise ValueError('Invalid plugin record')
                native = record.get('scope')
                rank = 0
                if native in {'project', 'local'}:
                    if workspace is None:
                        continue
                    target = _metadata_path(Path(record['projectPath']))
                    if target not in lineage:
                        continue
                    rank = len(target.parts) * 2 + (native == 'local')
                elif native != 'user':
                    continue
                path = same_profile_path(Path(record['installPath']))
                eligible.append((rank, path, 'personal' if native == 'user' else 'project'))
            except (ValueError, OSError, KeyError, TypeError):
                fail('plugin_record_unreadable')
        if not eligible:
            continue
        best = max(item[0] for item in eligible)
        chosen = list(dict.fromkeys(item for item in eligible if item[0] == best))
        if len(chosen) > 1:
            fail('plugin_installation_ambiguous')
            continue
        _, plugin, scope = chosen[0]
        try:
            manifest = _json_metadata(plugin / '.claude-plugin/plugin.json')
        except (ValueError, OSError, UnicodeError, TypeError):
            fail('plugin_manifest_invalid')
            continue
        namespace = manifest.get('name', plugin_id.split('@', 1)[0])
        if not isinstance(namespace, str) or not namespace or len(namespace) > 100 or re.search(r'[\s/:\\\x00-\x1f]', namespace):
            fail('plugin_manifest_invalid')
            continue
        roots = [plugin / 'skills']
        extra = manifest.get('skills', [])
        if isinstance(extra, str):
            extra = [extra]
        if not isinstance(extra, list) or len(extra) > 32:
            fail('plugin_manifest_invalid')
            extra = []
        for value in extra:
            try:
                if not isinstance(value, str) or not value or ':' in value or '\\' in value:
                    raise ValueError('Invalid plugin skill path')
                relative = Path(value)
                if relative.is_absolute() or '..' in relative.parts:
                    raise ValueError('Plugin skill path traversal')
                roots.append(_metadata_path(plugin / relative))
            except (ValueError, OSError):
                fail('plugin_manifest_invalid')
        source = 'plugin'
        label = '플러그인 ' + _text(namespace, 100)
        installation = plugin_id + '\0' + os.path.normcase(str(plugin)) + '\0' + scope
        for root in dict.fromkeys(roots):
            scan(root, source, scope, namespace, installation, label)
        if not include_commands:
            continue
        command_roots = [plugin / 'commands']
        extra_commands = manifest.get('commands', [])
        if isinstance(extra_commands, str):
            extra_commands = [extra_commands]
        if not isinstance(extra_commands, list) or len(extra_commands) > 32:
            extra_commands = []
            if include_commands:
                fail('plugin_commands_invalid')
        for value in extra_commands:
            try:
                if not isinstance(value, str) or not value or ':' in value or '\\' in value:
                    raise ValueError('Invalid plugin command path')
                relative = Path(value)
                if relative.is_absolute() or '..' in relative.parts:
                    raise ValueError('Plugin command path traversal')
                command_roots.append(_metadata_path(plugin / relative))
            except (ValueError, OSError):
                if include_commands:
                    fail('plugin_commands_invalid')
        for root in dict.fromkeys(command_roots):
            scan_commands(root, source, scope, namespace, installation, label)
    rows.sort(key=lambda item: (item['name'].casefold(), item['source'], item['candidateId']))
    names = {}
    for row in rows:
        names[row['name']] = names.get(row['name'], 0) + 1
    return {'skills': rows, 'readFailures': len(failures), 'skillsLimited': limited or bool(failures),
            'skillWarnings': ['일부 스킬 메타데이터를 확인하지 못했습니다.'] if failures else [],
            'skillConflicts': sum(count > 1 for count in names.values())}


def _text(value, limit=200):
    if not isinstance(value, str):
        return ''
    return ' '.join(''.join(c if ord(c) >= 32 else ' ' for c in value[:limit * 4]).split())[:limit]


def _safe_file(path):
    if not path.is_absolute() or '..' in path.parts or len(str(path)) > 2048 or path.name != 'SKILL.md':
        raise ValueError('Invalid skill metadata path')
    for part in (path, *path.parents):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Linked metadata path')
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SKILL_BYTES:
        raise ValueError('Invalid metadata file size')


def user_invocable(path):
    """Read just the bounded YAML header, never instructions in the body.

    An absent field means user-invocable. Malformed/ambiguous metadata is not
    silently treated as an available user command.
    """
    _safe_file(path)
    with path.open('rb') as stream:
        first = stream.readline(MAX_HEADER_BYTES + 1)
        if len(first) > MAX_HEADER_BYTES:
            raise ValueError('Oversized metadata header')
        if first.decode('utf-8-sig').strip() != '---':
            return True
        remaining = MAX_HEADER_BYTES - len(first)
        value = True
        found = False
        while remaining > 0:
            raw = stream.readline(remaining + 1)
            if not raw or len(raw) > remaining:
                break
            remaining -= len(raw)
            line = raw.decode('utf-8').rstrip('\r\n')
            if line.strip() in {'---', '...'}:
                return value
            match = re.match(r'^user-invocable\s*:\s*(.*?)\s*$', line)
            if not match:
                continue
            if found:
                raise ValueError('Duplicate invocation metadata')
            found = True
            scalar = match.group(1).split(' #', 1)[0].strip()
            boolean = re.fullmatch(r"(true|false)|(['\"])(true|false)\2", scalar, re.IGNORECASE)
            if boolean is None:
                raise ValueError('Invalid invocation metadata')
            value = (boolean.group(1) or boolean.group(3)).casefold() == 'true'
    raise ValueError('Incomplete metadata header')


def project_inventory(inventory, record):
    """Project only safe display fields; unreadable headers stay unclassified."""
    raw = inventory.get('skills')
    if not isinstance(raw, list):
        raise ValueError('Unsupported skill registry')
    rows, failures = [], 0
    for item in raw[:MAX_ITEMS]:
        try:
            if not isinstance(item, dict) or not isinstance(item.get('path'), str):
                raise ValueError('Missing exact skill file')
            visible = user_invocable(Path(item['path']))
            name = _text(item.get('name'))
            if not name:
                raise ValueError('Missing skill name')
            invocation = _text(item.get('invocation'))
            source = item.get('source') if item.get('source') in SOURCES else 'unknown'
            scope = item.get('storageScope')
            if scope not in {'personal', 'project', 'company'}:
                scope = ('personal' if source == 'user' else 'project' if source == 'project'
                         else 'company' if source == 'corporate'
                         else ('project' if record.get('scope') == 'Project' else 'personal')
                         if source == 'company' else 'unknown')
            namespace = invocation.lstrip('/').split(':', 1)[0] if ':' in invocation else ''
            rows.append({'name': name, 'description': _text(item.get('description'), 800),
                         'source': source, 'storageScope': scope, 'invocation': invocation,
                         'explicitOnly': item.get('explicitOnly') is True,
                         'userInvocable': visible,
                         'kind': 'internal' if not visible else 'skill' if invocation else 'reference',
                         'pluginNamespace': namespace})
        except (ValueError, OSError, UnicodeError, TypeError):
            failures += 1
    warnings = inventory.get('warnings')
    conflicts = inventory.get('conflicts')
    return {'skills': rows, 'readFailures': failures,
            'skillsLimited': len(raw) > MAX_ITEMS or failures > 0 or inventory.get('complete') is not True,
            'skillWarnings': ['일부 스킬 메타데이터를 확인하지 못했습니다.']
                             if failures or (isinstance(warnings, list) and warnings) else [],
            'skillConflicts': len(conflicts) if isinstance(conflicts, list) else 0}


def main():
    parser = argparse.ArgumentParser()
    for name in ('workspace', 'plugin', 'config', 'registrations'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    # The helper repeats registration validation before importing its core.
    # A folder/profile change between server lookup and subprocess cannot
    # substitute another installation or silently fall back to this checkout.
    from harness_client import HarnessClient
    client = HarnessClient(config=args.config, registrations=args.registrations)
    project, record, plugin, _ = client._installation(args.workspace)
    if plugin != Path(args.plugin):
        raise ValueError('Active installation changed')
    sys.path.insert(0, str(plugin / 'scripts'))
    from company_agent.skill_registry import inventory_skills
    inventory = inventory_skills(Path(record['userStateRoot']), project_root=project,
                                 claude_root=client.config, plugin_root=plugin,
                                 knowledge_root=Path(record['knowledgeBaseRoot'])
                                 if record.get('knowledgeBaseRoot') else None,
                                 metadata_cache=False)
    print(json.dumps(project_inventory(inventory, record), ensure_ascii=True))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, UnicodeError, TypeError, KeyError, ImportError):
        print('Skill metadata is unavailable.', file=sys.stderr)
        raise SystemExit(1)
