"""Finite local previews. Callers must authorize the file against a task first.

No renderer, interpreter, network access, file write, or decoded image cache is
started here. Image headers bound browser decode dimensions; the browser still
owns the final raster decode and can reject damaged compressed image data.
"""
from __future__ import annotations

import base64
import codecs
import csv
import io
from pathlib import Path
import re
import struct
import zlib

CODE_LANGUAGES = {
    '.py': 'python', '.pyi': 'python', '.js': 'javascript', '.mjs': 'javascript',
    '.cjs': 'javascript', '.jsx': 'jsx', '.ts': 'typescript', '.tsx': 'tsx',
    '.json': 'json', '.jsonc': 'json', '.css': 'css', '.scss': 'scss',
    '.sql': 'sql', '.ps1': 'powershell', '.sh': 'bash', '.bash': 'bash',
    '.yaml': 'yaml', '.yml': 'yaml', '.toml': 'toml', '.xml': 'xml',
    '.cs': 'csharp', '.java': 'java', '.go': 'go', '.rs': 'rust',
    '.c': 'c', '.h': 'c', '.cpp': 'cpp', '.hpp': 'cpp', '.r': 'r',
    '.rb': 'ruby', '.php': 'php', '.swift': 'swift', '.kt': 'kotlin',
}
MAX_TEXT_BYTES = 1024 * 1024
MAX_CODE_CHARS = 100_000
# Encoded raster size; base64 in the authenticated JSON response is about 11 MiB.
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
MAX_TABLE_ROWS = 100
MAX_TABLE_COLUMNS = 30
IMAGE_TYPES = {'.png': 'image/png', '.jpg': 'image/jpeg',
               '.jpeg': 'image/jpeg', '.webp': 'image/webp'}
SECRET_NAME = re.compile(r'(^|[._-])(secret|secrets|credential|credentials|password|passwords|'
                         r'token|tokens|auth|apikey|api_key|private)([._-]|$)', re.I)


def private_name(name):
    return (name.startswith('.') or bool(SECRET_NAME.search(name))
            or name.casefold().startswith(('id_rsa', 'id_ed25519')))


def source_preview_allowed(path, root):
    """New source previews never implicitly expose hidden or credential files."""
    if path.suffix.lower() not in CODE_LANGUAGES:
        return True
    names = path.relative_to(root).parts if path.is_relative_to(root) else (path.name,)
    return not any(private_name(name) for name in names)


def external(path, message):
    return {'kind': 'external', 'name': path.name, 'message': message}


def _decode(raw, *, partial=False):
    encodings = ('utf-16',) if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else ('utf-8-sig', 'cp949')
    if encodings != ('utf-16',) and b'\0' in raw:
        raise UnicodeError('binary text')
    for encoding in encodings:
        try:
            return codecs.getincrementaldecoder(encoding)(errors='strict').decode(raw, final=not partial)
        except UnicodeError:
            continue
    raise UnicodeError('unsupported text encoding')


def _png_dimensions(raw):
    if not raw.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError('PNG signature')
    offset, dimensions, image_data, ended = 8, None, False, False
    while offset + 12 <= len(raw):
        size = int.from_bytes(raw[offset:offset + 4], 'big')
        end = offset + 12 + size
        if end > len(raw):
            raise ValueError('PNG chunk')
        kind = raw[offset + 4:offset + 8]
        data = raw[offset + 8:end - 4]
        crc = int.from_bytes(raw[end - 4:end], 'big')
        if zlib.crc32(kind + data) & 0xffffffff != crc:
            raise ValueError('PNG checksum')
        if offset == 8:
            if kind != b'IHDR' or size != 13:
                raise ValueError('PNG header')
            dimensions = struct.unpack('>II', data[:8])
        elif kind == b'IHDR':
            raise ValueError('duplicate PNG header')
        if kind == b'acTL':
            raise ValueError('animated PNG is not previewed')
        if kind == b'IDAT' and size:
            image_data = True
        if kind == b'IEND':
            ended = size == 0 and end == len(raw)
            break
        offset = end
    if not dimensions or not image_data or not ended:
        raise ValueError('incomplete PNG')
    return dimensions


def _jpeg_dimensions(raw):
    if not raw.startswith(b'\xff\xd8') or not raw.endswith(b'\xff\xd9'):
        raise ValueError('JPEG signature')
    offset, dimensions = 2, None
    while offset + 4 <= len(raw):
        if raw[offset] != 0xff:
            raise ValueError('JPEG marker')
        while offset < len(raw) and raw[offset] == 0xff:
            offset += 1
        if offset >= len(raw):
            break
        marker = raw[offset]
        offset += 1
        if marker in {0, 0xd8, 0xd9} or 0xd0 <= marker <= 0xd7:
            raise ValueError('unexpected JPEG marker')
        if offset + 2 > len(raw):
            break
        size = int.from_bytes(raw[offset:offset + 2], 'big')
        if size < 2 or offset + size > len(raw):
            raise ValueError('JPEG segment')
        if marker in {0xc0, 0xc1, 0xc2}:
            if size < 8 or dimensions:
                raise ValueError('JPEG frame')
            height, width = struct.unpack('>HH', raw[offset + 3:offset + 7])
            dimensions = width, height
        elif marker == 0xda:
            if dimensions is None:
                raise ValueError('JPEG missing frame')
            return dimensions
        elif 0xc0 <= marker <= 0xcf and marker not in {0xc4, 0xc8, 0xcc}:
            raise ValueError('unsupported JPEG frame')
        offset += size
    raise ValueError('incomplete JPEG')


def _webp_dimensions(raw):
    if (len(raw) < 20 or raw[:4] != b'RIFF' or raw[8:12] != b'WEBP'
            or int.from_bytes(raw[4:8], 'little') + 8 != len(raw)):
        raise ValueError('WebP signature')
    offset, dimensions, canvas = 12, None, None
    while offset + 8 <= len(raw):
        kind = raw[offset:offset + 4]
        size = int.from_bytes(raw[offset + 4:offset + 8], 'little')
        end = offset + 8 + size
        if end > len(raw):
            raise ValueError('WebP chunk')
        data = raw[offset + 8:end]
        if kind in {b'ANIM', b'ANMF'}:
            raise ValueError('animated WebP is not previewed')
        if kind == b'VP8X':
            if size != 10 or data[0] & 2 or canvas:
                raise ValueError('WebP extended header')
            canvas = (1 + int.from_bytes(data[4:7], 'little'),
                      1 + int.from_bytes(data[7:10], 'little'))
        elif kind == b'VP8 ':
            if size < 10 or data[0] & 1 or data[3:6] != b'\x9d\x01\x2a' or dimensions:
                raise ValueError('WebP keyframe')
            dimensions = (int.from_bytes(data[6:8], 'little') & 0x3fff,
                          int.from_bytes(data[8:10], 'little') & 0x3fff)
        elif kind == b'VP8L':
            if size < 5 or data[0] != 0x2f or data[4] & 0xe0 or dimensions:
                raise ValueError('WebP lossless header')
            bits = int.from_bytes(data[1:5], 'little')
            dimensions = ((bits & 0x3fff) + 1, ((bits >> 14) & 0x3fff) + 1)
        offset = end + (size & 1)
    if offset != len(raw) or not dimensions or (canvas and canvas != dimensions):
        raise ValueError('incomplete WebP')
    return dimensions


def image_dimensions(raw, suffix):
    parser = {'.png': _png_dimensions, '.jpg': _jpeg_dimensions,
              '.jpeg': _jpeg_dimensions, '.webp': _webp_dimensions}[suffix]
    width, height = parser(raw)
    if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
        raise ValueError('image dimensions exceed preview budget')
    return width, height


def _table(path, text):
    reader = csv.reader(io.StringIO(text, newline=''), delimiter='\t' if path.suffix.lower() == '.tsv' else ',', strict=True)
    header = next(reader, [])
    columns = len(header)
    rows, total = [], 0
    for row in reader:
        columns = max(columns, len(row))
        total += 1
        if total <= MAX_TABLE_ROWS:
            rows.append(row[:MAX_TABLE_COLUMNS])
    shown = min(columns, MAX_TABLE_COLUMNS)
    labels = [(header[index] if index < len(header) and header[index] else f'열 {index + 1}')
              for index in range(shown)]
    rows = [row + [''] * (shown - len(row)) for row in rows]
    row_limit, column_limit = total > MAX_TABLE_ROWS, columns > MAX_TABLE_COLUMNS
    return {'kind': 'table', 'name': path.name, 'columns': labels, 'rows': rows,
            'rowCount': total, 'columnCount': columns, 'rowsTruncated': row_limit,
            'columnsTruncated': column_limit, 'truncated': row_limit or column_limit}


def build_preview(path: Path):
    suffix = path.suffix.lower()
    is_image = suffix in IMAGE_TYPES
    is_code = suffix in CODE_LANGUAGES
    if not is_image and not is_code and suffix not in {'.md', '.txt', '.csv', '.tsv', '.html', '.htm'}:
        return external(path, 'Office·PDF 원본은 원래 앱에서 열어 확인해 주세요.')
    limit = MAX_IMAGE_BYTES if is_image else MAX_TEXT_BYTES
    # Read a finite amount even if a file grows after the authorization/stat.
    with path.open('rb') as handle:
        raw = handle.read(limit + 1)
    limited = len(raw) > limit
    if limited and not is_code:
        return external(path, '미리보기 크기를 넘는 파일입니다. 원래 앱에서 열어 주세요.')
    if is_image:
        try:
            width, height = image_dimensions(raw, suffix)
        except (ValueError, struct.error):
            return external(path, '이미지 형식이나 크기를 확인할 수 없습니다. 원래 앱에서 열어 주세요.')
        return {'kind': 'image', 'name': path.name, 'width': width, 'height': height,
                'data': 'data:' + IMAGE_TYPES[suffix] + ';base64,' + base64.b64encode(raw).decode('ascii')}
    try:
        text = _decode(raw[:limit], partial=limited)
    except UnicodeError:
        return external(path, '문자 인코딩을 확인하지 못했습니다. 원래 앱이나 메모장에서 확인해 주세요.')
    if is_code:
        return {'kind': 'code', 'name': path.name, 'language': CODE_LANGUAGES[suffix],
                'text': text[:MAX_CODE_CHARS], 'truncated': limited or len(text) > MAX_CODE_CHARS}
    if suffix in {'.csv', '.tsv'}:
        try:
            return _table(path, text)
        except csv.Error:
            return external(path, '표 형식을 확인하지 못했습니다. 원래 앱에서 열어 주세요.')
    if suffix in {'.html', '.htm'}:
        from .html_preview import render
        return {'kind': 'html', 'name': path.name, 'html': render(text),
                'text': text[:MAX_CODE_CHARS], 'language': 'html',
                'truncated': len(text) > MAX_CODE_CHARS,
                'message': '정적 미리보기 · 외부 자료나 스크립트가 필요한 내용은 기본 앱에서 확인하세요.'}
    return {'kind': 'text', 'name': path.name, 'text': text}
