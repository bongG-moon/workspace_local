"""Explicit drag/drop copies, bounded and stored only in app-owned state."""
from __future__ import annotations

import os
from pathlib import Path
import re
import threading
import uuid

from .completions import REFERENCE_FILE_TYPES
from .history import safe

MAX_UPLOAD = 50 * 1024 * 1024
MAX_STORAGE = 512 * 1024 * 1024


class AttachmentStore:
    def __init__(self, state):
        self.root = Path(state) / 'attachments'
        self.lock = threading.Lock()

    @staticmethod
    def filename(value):
        if (not isinstance(value, str) or not value or len(value) > 200
                or any(ord(char) < 32 for char in value) or re.search(r'[<>:"/\\|?*]', value)
                or value.endswith((' ', '.')) or value in {'.', '..'}):
            raise ValueError('첨부할 파일 이름을 확인해 주세요.')
        if value.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}:
            raise ValueError('이 파일 이름은 첨부할 수 없습니다.')
        if Path(value).suffix.lower() not in REFERENCE_FILE_TYPES:
            raise ValueError('문서·이미지 또는 소스 파일을 첨부해 주세요. 폴더는 경로로 추가할 수 있습니다.')
        return value

    def save(self, sid, name, source, size):
        name = self.filename(name)
        if type(size) is not int or not 0 <= size <= MAX_UPLOAD:
            raise ValueError('끌어서 첨부하는 파일은 한 개당 50 MB까지 지원합니다. 큰 파일은 경로로 추가해 주세요.')
        sid = str(uuid.UUID(sid))
        with self.lock:
            safe(self.root).mkdir(parents=True, exist_ok=True)
            total = 0
            for bucket in self.root.iterdir():
                safe(bucket)
                if not bucket.is_dir():
                    raise ValueError('첨부 저장 위치를 확인해 주세요.')
                for group in bucket.iterdir():
                    safe(group)
                    if not group.is_dir():
                        raise ValueError('첨부 저장 위치를 확인해 주세요.')
                    for entry in group.iterdir():
                        safe(entry)
                        if entry.is_file():
                            total += entry.stat().st_size
            if total + size > MAX_STORAGE:
                raise ValueError('앱에 복사한 첨부 자료가 512 MB를 넘습니다. 자료 추가나 경로 추가로 원본 파일을 선택해 주세요.')
            bucket = safe(self.root / sid / uuid.uuid4().hex)
            bucket.mkdir(parents=True, exist_ok=False)
            target = safe(bucket / name)
            temporary = safe(bucket / '.upload-part')
            try:
                with temporary.open('xb') as out:
                    remaining = size
                    while remaining:
                        chunk = source.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise ValueError('파일 전송이 끝나지 않았습니다. 다시 끌어 놓아 주세요.')
                        out.write(chunk)
                        remaining -= len(chunk)
                    out.flush()
                    os.fsync(out.fileno())
                safe(target)
                temporary.replace(target)
            except BaseException:
                temporary.unlink(missing_ok=True)
                bucket.rmdir()
                raise
        return {'path': str(target.resolve(strict=True)), 'name': name, 'size': size, 'copied': True}
