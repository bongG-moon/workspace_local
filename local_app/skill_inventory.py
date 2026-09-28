"""Read-only skill metadata adapter for an already installed Company Agent.

Runs in the registered interpreter, never in the Workspace server process.
Only the installed registry chooses files; skill bodies are never executed or
returned. Existing core versions need no profile or installation changes.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import stat
import sys

MAX_ITEMS = 1000
MAX_HEADER_BYTES = 16 * 1024
MAX_SKILL_BYTES = 256 * 1024
SOURCES = {'user', 'project', 'personal', 'company', 'plugin', 'corporate'}


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
