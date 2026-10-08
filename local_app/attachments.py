"""Explicit drag/drop copies with a bounded, conservative storage lifecycle.

Only copies created with this manifest can be reclaimed. Existing untracked files
and copies ever passed to Claude remain protected, including after UI history is
compacted. Inventory is loaded once, then adjusted under a lock; refresh is an
explicit storage-screen action, never a periodic poll.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import threading
import time
import uuid

from .completions import MAX_ATTACHMENTS, REFERENCE_FILE_TYPES
from .history import safe, read

MAX_UPLOAD = 50 * 1024 * 1024
MAX_STORAGE = 512 * 1024 * 1024
MAX_COPIES = 10000
UNUSED_GRACE_SECONDS = 300
MAX_CLEANUP_BATCH = 100
CLEANUP_BUDGET_SECONDS = .1


def attachment_policy():
    return {'extensions': sorted(REFERENCE_FILE_TYPES), 'maxUploadBytes': MAX_UPLOAD,
            'maxAttachments': MAX_ATTACHMENTS}


class UploadReader:
    """Track exactly the declared body bytes without buffering the entire file."""
    def __init__(self, source, size):
        self.source, self.remaining = source, size

    def read(self, size):
        chunk = self.source.read(min(size, self.remaining))
        self.remaining -= len(chunk)
        return chunk


class AttachmentStore:
    def __init__(self, state):
        self.root = Path(state) / 'attachments'
        self.index = Path(state) / 'attachment-index.json'
        self.lock = threading.RLock()
        self._entries = None
        self._records = {}
        self._record_keys = {}
        self._used_bytes = 0
        self._inflight = {}
        self._manifest_warning = False
        self._metadata_dirty = False

    @staticmethod
    def filename(value):
        if (not isinstance(value, str) or not value or len(value) > 200
                or any(ord(char) < 32 for char in value) or re.search(r'[<>:"/\\|?*]', value)
                or value.endswith((' ', '.')) or value in {'.', '..'}):
            raise ValueError('첨부할 파일 이름을 확인해 주세요.')
        if value.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}:
            raise ValueError('이 파일 이름은 첨부할 수 없습니다.')
        if Path(value).suffix.lower() not in REFERENCE_FILE_TYPES:
            raise ValueError('지원하지 않는 파일 형식입니다. 문서·이미지·소스·EXE·압축 파일을 첨부할 수 있습니다. 폴더는 경로로 추가해 주세요.')
        return value

    @staticmethod
    def validate_size(size):
        if type(size) is not int or not 0 <= size <= MAX_UPLOAD:
            raise ValueError('끌어서 첨부하는 파일은 한 개당 50 MB까지 지원합니다. 큰 파일은 경로로 추가해 주세요.')

    @staticmethod
    def _identity(path):
        stat = safe(path).stat()
        return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns]

    @staticmethod
    def _key(path):
        # This application is Windows-first; accept either slash style for refs.
        return os.path.normcase(os.path.abspath(str(path).replace('\\', '/')))

    def _record(self, value):
        if not isinstance(value, dict):
            raise ValueError('첨부 기록을 확인하지 못했습니다.')
        relative = value.get('path')
        parts = relative.split('/') if isinstance(relative, str) else []
        if (len(parts) != 3 or str(uuid.UUID(parts[0])) != parts[0]
                or not re.fullmatch('[0-9a-f]{32}', parts[1])
                or self.filename(parts[2]) != parts[2]):
            raise ValueError('첨부 기록 경로를 확인하지 못했습니다.')
        created = value.get('created')
        identity = value.get('identity')
        if (type(created) not in (int, float) or not math.isfinite(created)
                or type(value.get('used')) is not bool
                or not isinstance(identity, list) or len(identity) != 4
                or any(type(part) is not int or part < 0 for part in identity)):
            raise ValueError('첨부 기록을 확인하지 못했습니다.')
        return {'path': relative, 'created': created, 'used': value['used'], 'identity': identity}

    def _load(self):
        if self._entries is not None:
            return
        records = {}
        if safe(self.index).exists():
            try:
                payload = read(self.index, 16 * 1024 * 1024)
                if (not isinstance(payload, dict) or payload.get('schemaVersion') != 1
                        or not isinstance(payload.get('copies'), list)
                        or len(payload['copies']) > MAX_COPIES):
                    raise ValueError('첨부 기록을 확인하지 못했습니다.')
                for value in payload['copies']:
                    record = self._record(value)
                    if record['path'] in records:
                        raise ValueError('중복된 첨부 기록입니다.')
                    records[record['path']] = record
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                # Never infer ownership or overwrite damaged provenance.
                self._manifest_warning = True
                records = {}
        self._records = records
        self._record_keys = {self._key(self.root / relative): relative for relative in records}
        self._scan()

    def _scan(self):
        entries = {}
        root = safe(self.root)
        inspected = 0
        if root.exists():
            for bucket in root.iterdir():
                inspected += 1
                if inspected > MAX_COPIES * 3:
                    raise ValueError('첨부 저장 위치에 항목이 너무 많습니다.')
                safe(bucket)
                if not bucket.is_dir():
                    raise ValueError('첨부 저장 위치를 확인해 주세요.')
                for group in bucket.iterdir():
                    inspected += 1
                    if inspected > MAX_COPIES * 3:
                        raise ValueError('첨부 저장 위치에 항목이 너무 많습니다.')
                    safe(group)
                    if not group.is_dir():
                        raise ValueError('첨부 저장 위치를 확인해 주세요.')
                    for entry in group.iterdir():
                        safe(entry)
                        inspected += 1
                        if inspected > MAX_COPIES * 3 or len(entries) >= MAX_COPIES:
                            raise ValueError('첨부 복사본이 너무 많습니다. 저장 위치를 확인해 주세요.')
                        if not entry.is_file():
                            raise ValueError('첨부 저장 위치를 확인해 주세요.')
                        # The active writer owns its partial file and quota claim.
                        if group in self._inflight:
                            continue
                        relative = entry.relative_to(root).as_posix()
                        identity = self._identity(entry)
                        record = self._records.get(relative)
                        # Replaced or externally modified copies lose cleanup
                        # eligibility. They are still counted, but never deleted.
                        managed = record is not None and record['identity'] == identity
                        entries[relative] = {'size': identity[2], 'managed': managed}
        self._entries = entries
        self._used_bytes = sum(item['size'] for item in entries.values())
        for relative in tuple(self._records):
            if relative not in entries:
                self._records.pop(relative)
                self._record_keys.pop(self._key(self.root / relative), None)
                self._metadata_dirty = True

    def _write_index(self):
        if self._manifest_warning:
            raise ValueError('첨부 관리 기록을 확인하지 못했습니다. 기존 자료는 보존했습니다. 기록 위치를 확인해 주세요.')
        safe(self.index.parent).mkdir(parents=True, exist_ok=True)
        temporary = safe(self.index.with_name(self.index.name + '.' + uuid.uuid4().hex + '.tmp'))
        try:
            with temporary.open('xb') as stream:
                stream.write(json.dumps({'schemaVersion': 1, 'copies': list(self._records.values())},
                                        ensure_ascii=False, allow_nan=False).encode('utf-8'))
                stream.flush()
                os.fsync(stream.fileno())
            safe(self.index)
            temporary.replace(self.index)
            self._metadata_dirty = False
        finally:
            temporary.unlink(missing_ok=True)

    def _check_capacity(self, size):
        self._load()
        if self._manifest_warning:
            raise ValueError('첨부 관리 기록을 확인하지 못했습니다. 원본 경로로 추가하거나 기록 위치를 확인해 주세요.')
        if self._used_bytes + sum(self._inflight.values()) + size > MAX_STORAGE:
            raise ValueError('앱에 복사한 첨부 자료가 512 MB를 넘습니다. 설정의 첨부 저장공간에서 미사용 복사본을 정리하거나 원본 경로로 추가해 주세요.')
        if len(self._entries) + len(self._inflight) >= MAX_COPIES or len(self._records) >= MAX_COPIES:
            raise ValueError('첨부 복사본이 너무 많습니다. 설정의 첨부 저장공간에서 미사용 복사본을 정리해 주세요.')

    def prepare(self, name, size):
        name = self.filename(name)
        self.validate_size(size)
        with self.lock:
            self._check_capacity(size)
        return {'ok': True, 'name': name, 'size': size}

    def save(self, sid, name, source, size):
        name = self.filename(name)
        self.validate_size(size)
        sid = str(uuid.UUID(sid))
        with self.lock:
            self._check_capacity(size)
            safe(self.root).mkdir(parents=True, exist_ok=True)
            bucket = safe(self.root / sid / uuid.uuid4().hex)
            bucket.mkdir(parents=True, exist_ok=False)
            target = safe(bucket / name)
            temporary = safe(bucket / '.upload-part')
            self._inflight[bucket] = size
        try:
            # Streaming is outside the metadata lock: status remains responsive,
            # and concurrent uploads cannot oversubscribe the reserved quota.
            with temporary.open('xb') as out:
                remaining = size
                while remaining:
                    chunk = source.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError('파일 전송이 끝나지 않았습니다. 다시 끌어 놓아 주세요.')
                    if len(chunk) > remaining:
                        raise ValueError('파일 전송 크기를 확인하지 못했습니다.')
                    out.write(chunk)
                    remaining -= len(chunk)
                out.flush()
                os.fsync(out.fileno())
            with self.lock:
                safe(target)
                temporary.replace(target)
                identity = self._identity(target)
                relative = target.relative_to(self.root).as_posix()
                self._records[relative] = {'path': relative, 'created': time.time(), 'used': False,
                                          'identity': identity}
                try:
                    self._write_index()
                except BaseException:
                    self._records.pop(relative, None)
                    target.unlink(missing_ok=True)
                    raise
                self._entries[relative] = {'size': size, 'managed': True}
                self._record_keys[self._key(target)] = relative
                self._used_bytes += size
        except BaseException:
            safe(temporary).unlink(missing_ok=True)
            if not any(bucket.iterdir()):
                safe(bucket).rmdir()
            raise
        finally:
            with self.lock:
                self._inflight.pop(bucket, None)
        return {'path': str(target.resolve(strict=True)), 'name': name, 'size': size,
                'copied': True, 'copyId': bucket.name}

    def mark_used(self, paths):
        """Persist before transmission. Once used, never reclaim from this UI."""
        prefix = self._key(self.root) + os.sep
        keys = {self._key(path) for path in paths if isinstance(path, (str, Path))}
        keys = {key for key in keys if key.startswith(prefix)}
        if not keys:
            return 0
        with self.lock:
            self._load()
            changed = []
            for key in keys:
                relative = self._record_keys.get(key)
                record = self._records.get(relative)
                if record is not None and not record['used']:
                    record['used'] = True
                    changed.append(record)
            if changed:
                self._metadata_dirty = True
            if self._metadata_dirty:
                try:
                    self._write_index()
                except BaseException:
                    # Fail closed in memory too: a caller may retry/stop safely.
                    raise
            return len(changed)

    def _status(self, protected_paths, now):
        protected = {self._key(path) for path in protected_paths if isinstance(path, (str, Path))}
        totals = {'usedBytes': self._used_bytes, 'maxBytes': MAX_STORAGE,
                  'totalCount': len(self._entries), 'removableBytes': 0, 'removableCount': 0,
                  'protectedBytes': 0, 'retainedBytes': 0, 'graceBytes': 0,
                  'uploadingBytes': sum(self._inflight.values()), 'uploadingCount': len(self._inflight),
                  'graceSeconds': UNUSED_GRACE_SECONDS, 'warning': None, 'preview': []}
        totals['cleanupBatchLimit'] = MAX_CLEANUP_BATCH
        if self._manifest_warning:
            totals['warning'] = '첨부 관리 기록을 확인하지 못해 복사본을 모두 보존합니다.'
        candidates = []
        for relative, entry in self._entries.items():
            record = self._records.get(relative)
            if not entry['managed'] or not record:
                totals['retainedBytes'] += entry['size']
            elif record['used'] or self._key(self.root / relative) in protected:
                totals['protectedBytes'] += entry['size']
            elif now < record['created'] + UNUSED_GRACE_SECONDS:
                totals['graceBytes'] += entry['size']
            else:
                candidates.append(relative)
                totals['removableBytes'] += entry['size']
                totals['removableCount'] += 1
                if len(totals['preview']) < 8:
                    totals['preview'].append({'name': Path(relative).name, 'size': entry['size']})
        return totals, candidates

    def status(self, protected_paths=(), *, now=None, refresh=False):
        with self.lock:
            self._load()
            if refresh:
                self._scan()
            result, _ = self._status(protected_paths, time.time() if now is None else now)
            return result

    def cleanup(self, protected_paths=(), *, now=None):
        """Caller serializes reference changes with this explicit manual action."""
        with self.lock:
            if self._entries is None:
                # The storage screen primes inventory outside the application
                # lock. Never unexpectedly walk thousands of files in cleanup.
                raise ValueError('첨부 저장공간을 먼저 확인한 뒤 정리해 주세요.')
            instant = time.time() if now is None else now
            _, candidates = self._status(protected_paths, instant)
            removed_count = removed_bytes = skipped = 0
            attempted = 0
            started = time.monotonic()
            for relative in candidates:
                if attempted >= MAX_CLEANUP_BATCH or (attempted and time.monotonic() - started >= CLEANUP_BUDGET_SECONDS):
                    break
                attempted += 1
                target = self.root / relative
                record = self._records[relative]
                try:
                    identity = self._identity(target)
                    if identity != record['identity']:
                        self._used_bytes += identity[2] - self._entries[relative]['size']
                        self._entries[relative] = {'size': identity[2], 'managed': False}
                        skipped += 1
                        continue
                    # Paths come exclusively from the owned manifest, never from
                    # a request body. Ancestors were checked for reparse points.
                    size = self._entries[relative]['size']
                    safe(target).unlink()
                except FileNotFoundError:
                    self._used_bytes -= self._entries.pop(relative)['size']
                    self._records.pop(relative, None)
                    self._record_keys.pop(self._key(target), None)
                    self._metadata_dirty = True
                    skipped += 1
                    continue
                except OSError:
                    # Retrying an inaccessible file first on every batch could
                    # starve all remaining copies; require an explicit refresh.
                    self._entries[relative]['managed'] = False
                    skipped += 1
                    continue
                self._entries.pop(relative, None)
                self._records.pop(relative, None)
                self._record_keys.pop(self._key(target), None)
                self._used_bytes -= size
                removed_count += 1
                removed_bytes += size
                try:
                    safe(target.parent).rmdir()
                    safe(target.parent.parent).rmdir()
                except OSError:
                    pass
            if removed_count or self._metadata_dirty:
                self._write_index()
            result, _ = self._status(protected_paths, instant)
            return dict(result, removedCount=removed_count, removedBytes=removed_bytes, skippedCount=skipped,
                        remainingCount=result['removableCount'], batchLimited=attempted < len(candidates))
