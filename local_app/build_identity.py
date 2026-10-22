"""Cached runtime-source identity, independent of version, path and user state.

This is an installed-build diagnostic, not an authenticity/signature check.
Keep the selection and framing in sync with Get-WorkspaceBuildIdentity.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUFFIXES = {'.py', '.ps1', '.cs', '.js', '.css', '.html'}
# Optional companion experiments are intentionally outside the shipped app.
EXCLUDED = {'companion.py', 'harness_client.py', 'companion.js'}


def build_identity(root: Path) -> str:
    try:
        paths = sorted((path for folder in (root / 'local_app', root / 'local_app/web')
                        for path in folder.iterdir()
                        if path.is_file() and path.suffix.lower() in SUFFIXES
                        and path.name not in EXCLUDED),
                       key=lambda path: path.relative_to(root).as_posix())
        if not paths or len(paths) > 256:
            return ''
        digest = hashlib.sha256()
        total = 0
        for path in paths:
            total += path.stat().st_size
            if total > 16 * 1024 * 1024:
                return ''
            content = hashlib.sha256(path.read_bytes()).hexdigest()
            digest.update((path.relative_to(root).as_posix() + '\0' + content + '\n').encode('utf-8'))
        return digest.hexdigest()
    except OSError:
        return ''


# Read once, before the server starts: replacing files later must not make an
# old process advertise the identity of code it has never loaded.
BUILD_ID = build_identity(ROOT)
