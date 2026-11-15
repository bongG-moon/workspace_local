"""On-demand, read-only web previews behind narrow opaque-frame capabilities.

The main app authorizes the entry and workspace before issuing a short-lived
capability. This handler never serves app APIs, enumerates a folder, launches a
project command, or retains file bodies. Browser requests read bounded files;
HTML/CSS rewriting spills to a temporary file instead of retaining a project.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from html import escape
from html.parser import HTMLParser
import io
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
import threading
import time
from urllib.parse import quote, unquote, urlsplit

from .file_preview import MAX_HTML_BYTES, MAX_IMAGE_BYTES, MAX_IMAGE_PIXELS, private_name
from .windows_paths import workspace_path

CHUNK = 64 * 1024
SPOOL_MEMORY = 256 * 1024
MAX_OUTPUT = 16 * 1024 * 1024
MAX_TRANSFER = 64 * 1024 * 1024
MAX_REQUESTS = 512
MAX_WORKERS = 8
MAX_TOTAL_IMAGE_PIXELS = 32_000_000
DEFAULT_TTL = 15 * 60
TYPES = {
    '.html': ('text/html; charset=utf-8', MAX_HTML_BYTES),
    '.htm': ('text/html; charset=utf-8', MAX_HTML_BYTES),
    '.css': ('text/css; charset=utf-8', 4 * 1024 * 1024),
    '.js': ('text/javascript; charset=utf-8', 10 * 1024 * 1024),
    '.mjs': ('text/javascript; charset=utf-8', 10 * 1024 * 1024),
    '.json': ('application/json; charset=utf-8', 4 * 1024 * 1024),
    '.png': ('image/png', MAX_IMAGE_BYTES), '.jpg': ('image/jpeg', MAX_IMAGE_BYTES),
    '.jpeg': ('image/jpeg', MAX_IMAGE_BYTES), '.webp': ('image/webp', MAX_IMAGE_BYTES),
    '.gif': ('image/gif', MAX_IMAGE_BYTES), '.svg': ('image/svg+xml', 1024 * 1024),
    '.ico': ('image/x-icon', 1024 * 1024),
    '.woff': ('font/woff', 4 * 1024 * 1024), '.woff2': ('font/woff2', 4 * 1024 * 1024),
    '.ttf': ('font/ttf', 4 * 1024 * 1024), '.otf': ('font/otf', 4 * 1024 * 1024),
}
_ROOT_URL = re.compile(r'''(?P<open>url\(\s*["']?)(?P<url>/(?!/)[^\s"')]*)(?P<close>["']?\s*\))''', re.I)
_ROOT_IMPORT = re.compile(r'''(?P<open>@import\s+["'])(?P<url>/(?!/)[^"']*)(?P<close>["'])''', re.I)


class PreviewUnavailable(ValueError):
    pass


def _root_url(value, prefix):
    return prefix + value[1:] if value.startswith('/') and not value.startswith('//') else value


def _css_urls(text, prefix):
    def replace(match):
        return match['open'] + _root_url(match['url'], prefix) + match['close']
    return _ROOT_IMPORT.sub(replace, _ROOT_URL.sub(replace, text))


def _image_pixels(source, suffix):
    """Inspect raster headers/chunk metadata without reading decoded pixels."""
    if suffix not in {'.png', '.jpg', '.jpeg', '.webp', '.gif'}:
        return 0
    source.seek(0)
    header = source.read(32)
    dimensions = None
    if suffix == '.png':
        if header[:8] != b'\x89PNG\r\n\x1a\n' or header[12:16] != b'IHDR' or len(header) < 24:
            raise PreviewUnavailable('image')
        dimensions = int.from_bytes(header[16:20], 'big'), int.from_bytes(header[20:24], 'big')
        offset = 8
        # Reject animation and malformed/indefinitely long chunk sequences.
        for _ in range(4096):
            source.seek(offset)
            chunk = source.read(8)
            if len(chunk) != 8:
                raise PreviewUnavailable('image')
            size, kind = int.from_bytes(chunk[:4], 'big'), chunk[4:]
            if kind == b'acTL':
                raise PreviewUnavailable('animated image')
            offset += size + 12
            if kind == b'IEND':
                break
        else:
            raise PreviewUnavailable('image chunks')
    elif suffix in {'.jpg', '.jpeg'}:
        if header[:2] != b'\xff\xd8':
            raise PreviewUnavailable('image')
        source.seek(2)
        for _ in range(1024):
            marker = source.read(2)
            while marker == b'\xff\xff':
                marker = b'\xff' + source.read(1)
            if len(marker) != 2 or marker[0] != 0xff:
                raise PreviewUnavailable('image')
            size = int.from_bytes(source.read(2), 'big')
            if size < 2:
                raise PreviewUnavailable('image')
            if marker[1] in {0xc0, 0xc1, 0xc2}:
                frame = source.read(5)
                if len(frame) != 5:
                    raise PreviewUnavailable('image')
                dimensions = int.from_bytes(frame[3:5], 'big'), int.from_bytes(frame[1:3], 'big')
                break
            source.seek(size - 2, 1)
    elif suffix == '.webp':
        if header[:4] != b'RIFF' or header[8:12] != b'WEBP':
            raise PreviewUnavailable('image')
        offset = 12
        for _ in range(4096):
            source.seek(offset)
            chunk = source.read(18)
            if len(chunk) < 8:
                break
            kind, size = chunk[:4], int.from_bytes(chunk[4:8], 'little')
            data = chunk[8:]
            if kind in {b'ANIM', b'ANMF'} or (kind == b'VP8X' and data and data[0] & 2):
                raise PreviewUnavailable('animated image')
            if kind == b'VP8X' and len(data) >= 10:
                dimensions = 1 + int.from_bytes(data[4:7], 'little'), 1 + int.from_bytes(data[7:10], 'little')
            elif kind == b'VP8L' and len(data) >= 5 and data[0] == 0x2f:
                bits = int.from_bytes(data[1:5], 'little')
                dimensions = (bits & 0x3fff) + 1, ((bits >> 14) & 0x3fff) + 1
            elif kind == b'VP8 ' and len(data) >= 10 and data[3:6] == b'\x9d\x01\x2a':
                dimensions = int.from_bytes(data[6:8], 'little') & 0x3fff, int.from_bytes(data[8:10], 'little') & 0x3fff
            offset += size + 8 + (size & 1)
        else:
            raise PreviewUnavailable('image chunks')
    elif header[:6] in {b'GIF87a', b'GIF89a'} and len(header) >= 10:
        dimensions = int.from_bytes(header[6:8], 'little'), int.from_bytes(header[8:10], 'little')
        source.seek(0)
        screen = source.read(13)
        if len(screen) != 13:
            raise PreviewUnavailable('image')
        if screen[10] & 0x80:
            source.seek(3 * (2 ** ((screen[10] & 7) + 1)), 1)

        def blocks():
            for _ in range(8192):
                size = source.read(1)
                if not size:
                    raise PreviewUnavailable('image')
                if size == b'\0':
                    return
                source.seek(size[0], 1)
            raise PreviewUnavailable('image blocks')

        frames = 0
        for _ in range(1024):
            kind = source.read(1)
            if kind == b';':
                break
            if kind == b'!':
                if not source.read(1):
                    raise PreviewUnavailable('image')
                blocks()
            elif kind == b',':
                frames += 1
                frame = source.read(9)
                if frames > 1 or len(frame) != 9:
                    raise PreviewUnavailable('animated image')
                width, height = int.from_bytes(frame[4:6], 'little'), int.from_bytes(frame[6:8], 'little')
                left, top = int.from_bytes(frame[:2], 'little'), int.from_bytes(frame[2:4], 'little')
                if (not width or not height or width * height > MAX_IMAGE_PIXELS
                        or left + width > dimensions[0] or top + height > dimensions[1]):
                    raise PreviewUnavailable('image dimensions')
                if frame[8] & 0x80:
                    source.seek(3 * (2 ** ((frame[8] & 7) + 1)), 1)
                if not source.read(1):
                    raise PreviewUnavailable('image')
                blocks()
            else:
                raise PreviewUnavailable('image')
        else:
            raise PreviewUnavailable('image blocks')
        if frames != 1:
            raise PreviewUnavailable('image')
    if dimensions is None or min(dimensions) <= 0 or dimensions[0] * dimensions[1] > MAX_IMAGE_PIXELS:
        raise PreviewUnavailable('image dimensions')
    source.seek(0)
    return dimensions[0] * dimensions[1]


class _Document(HTMLParser):
    """Preserve scripts verbatim; rewrite only HTML resource attributes/styles."""
    def __init__(self, sink, prefix):
        super().__init__(convert_charrefs=False)
        self.sink, self.prefix, self.style = sink, prefix, False

    def handle_starttag(self, tag, attrs):
        self._tag(tag, attrs, False)

    def handle_startendtag(self, tag, attrs):
        self._tag(tag, attrs, True)

    def _tag(self, tag, attrs, closed):
        # A document cannot replace the capability-root URL base.
        if tag == 'base':
            return
        self.style = tag == 'style'
        fields = []
        for name, value in attrs:
            if value is None:
                fields.append(name)
                continue
            if name in {'href', 'src', 'poster', 'action'}:
                value = _root_url(value, self.prefix)
            elif name == 'srcset' and 'data:' not in value.casefold():
                value = re.sub(r'(^|,\s*)(/(?!/)[^\s,]*)',
                               lambda match: match[1] + _root_url(match[2], self.prefix), value)
            elif name == 'style':
                value = _css_urls(value, self.prefix)
            fields.append(name + '="' + escape(value, quote=True) + '"')
        self.sink('<' + tag + (' ' + ' '.join(fields) if fields else '') + ('/>' if closed else '>'))

    def handle_endtag(self, tag):
        if tag != 'base':
            self.sink('</' + tag + '>')
        if tag == 'style':
            self.style = False

    def handle_data(self, data):
        self.sink(_css_urls(data, self.prefix) if self.style else data)

    def handle_entityref(self, name):
        self.sink('&' + name + ';')

    def handle_charref(self, name):
        self.sink('&#' + name + ';')

    def handle_comment(self, data):
        self.sink('<!--' + data + '-->')

    def handle_decl(self, decl):
        self.sink('<!' + decl + '>')

    def handle_pi(self, data):
        self.sink('<?' + data + '>')


def _transformed(source, suffix, prefix, *, cancelled=None):
    """Bounded temporary output; no full HTML/CSS or source cache in memory."""
    output = tempfile.SpooledTemporaryFile(max_size=SPOOL_MEMORY, mode='w+b')
    first = source.read(4)
    encodings = ('utf-16',) if first.startswith((b'\xff\xfe', b'\xfe\xff')) else ('utf-8-sig', 'cp949')
    try:
        for encoding in encodings:
            source.seek(0)
            output.seek(0)
            output.truncate()
            length = 0

            def check_cancelled():
                if cancelled is not None and cancelled():
                    raise PreviewUnavailable('웹 미리보기가 닫혔습니다.')

            def sink(text):
                nonlocal length
                data = text.encode('utf-8')
                length += len(data)
                if length > MAX_OUTPUT:
                    raise PreviewUnavailable('미리보기의 표시 범위를 넘었습니다.')
                output.write(data)

            parser = _Document(sink, prefix) if suffix in {'.html', '.htm'} else None
            wrapper = io.TextIOWrapper(source, encoding=encoding, errors='strict', newline='')
            try:
                # Retain an URL-sized tail across minified CSS chunk boundaries.
                if parser is None:
                    pending = ''
                    while part := wrapper.read(CHUNK):
                        check_cancelled()
                        pending += part
                        if len(pending) > CHUNK * 2:
                            split = len(pending) - 8192
                            for pattern in (_ROOT_URL, _ROOT_IMPORT):
                                for match in pattern.finditer(pending):
                                    if match.start() < split < match.end():
                                        split = match.start()
                                        break
                            sink(_css_urls(pending[:split], prefix))
                            pending = pending[split:]
                        time.sleep(0)
                    sink(_css_urls(pending, prefix))
                else:
                    while part := wrapper.read(CHUNK):
                        check_cancelled()
                        parser.feed(part)
                        time.sleep(0)
                    parser.close()
                check_cancelled()
                output.seek(0)
                return output, length
            except UnicodeError:
                if encoding == encodings[-1]:
                    raise PreviewUnavailable('이 파일의 문자 인코딩을 확인할 수 없습니다.') from None
            finally:
                wrapper.detach()
    except Exception:
        output.close()
        raise


@dataclass
class _Lease:
    token: str
    entry: Path
    root: Path
    authorize: object
    owner: object
    origin: str
    touched: float
    revoked: threading.Event = field(default_factory=threading.Event)
    transferred: int = 0
    requests: int = 0
    image_pixels: int = 0
    images: set = field(default_factory=set)


def _safe_file(path, root):
    """Reject hidden/private components, ADS and link/redirection escapes."""
    path, root = Path(path), Path(root)
    if not path.is_absolute() or not root.is_absolute() or not path.is_relative_to(root):
        raise PreviewUnavailable('허용된 업무 폴더 안의 파일을 선택해 주세요.')
    relative = path.relative_to(root)
    if not relative.parts or any((private_name(part) and not (
                                     index == len(relative.parts) - 1
                                     and part.casefold() in {'tokens.css', 'design-tokens.css'}))
                                 or part in {'.', '..'} or ':' in part or '\\' in part
                                 or part.endswith((' ', '.')) or any(ord(c) < 32 for c in part)
                                 for index, part in enumerate(relative.parts)):
        raise PreviewUnavailable('이 파일은 웹 미리보기에서 열 수 없습니다.')
    workspace_path(root)
    workspace_path(path)
    for ancestor in (path, *tuple(path.parents)[:len(relative.parts) - 1]):
        if getattr(ancestor.lstat(), 'st_file_attributes', 0) & 6:
            raise PreviewUnavailable('숨겨진 자료는 웹 미리보기에서 열 수 없습니다.')
    if root.resolve(strict=True) != root or path.resolve(strict=True) != path:
        raise PreviewUnavailable('파일의 실제 위치를 확인해 주세요.')
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 2:
        raise PreviewUnavailable('일반 파일만 웹 미리보기에서 열 수 있습니다.')
    kind = TYPES.get(path.suffix.lower())
    if not kind or info.st_size > kind[1]:
        raise PreviewUnavailable('이 파일의 형식이나 크기는 웹 미리보기 범위를 넘었습니다.')
    return info, kind


class _Resource:
    def __init__(self, handler, manager):
        self.handler, self.manager = handler, manager
        self.headers_started = False

    def __getattr__(self, name):
        return getattr(self.handler, name)

    def _error(self, status):
        self.headers_started = True
        is_document = unquote(urlsplit(self.path).path).lower().endswith(('.html', '.htm'))
        body = ('''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{margin:0;padding:32px 24px;color:#596173;background:#f6f7fb;font:14px/1.7 'Noto Sans KR','Malgun Gothic',sans-serif}h1{font-size:17px;color:#30394c;margin:0 0 10px}p{margin:0 0 8px}</style>
<h1>웹 미리보기를 표시할 수 없습니다</h1><p>파일이 이동했거나 미리보기 연결이 종료되었을 수 있어요.</p>
<p>위의 ‘화면’ 또는 ‘코드’를 선택하거나, 아래의 ‘기본 앱에서 열기’로 확인해 주세요.</p>'''.encode('utf-8')
                if is_document else b'')
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8' if is_document else 'text/plain')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Content-Security-Policy', "default-src 'none'; sandbox; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'")
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        if self.command == 'GET' and body:
            self.wfile.write(body)

    def _serve(self, head, *, options=False):
        manager = self.manager
        parsed = urlsplit(self.path)
        if parsed.scheme or parsed.netloc or len(parsed.path) > 8192:
            return self._error(404)
        try:
            decoded = unquote(parsed.path, errors='strict')
            fields = decoded.split('/')
            if len(fields) < 4 or fields[:2] != ['', 'project-preview'] or any(not part or part in {'.', '..'} for part in fields[2:]):
                raise PreviewUnavailable('path')
            token, parts = fields[2], fields[3:]
            # Reject encoded separators and double decoding ambiguities.
            if ('\\' in decoded or '%' in decoded or ':' in decoded
                    or any(ord(char) < 32 for char in decoded)
                    or re.search(r'%2f|%5c', parsed.path, re.I)):
                raise PreviewUnavailable('path')
            lease = manager._lookup(token)
            if lease is None:
                return self._error(410)
            expected_host = urlsplit(lease.origin).netloc
            if (self.headers.get('Host') != expected_host
                    or self.headers.get('Origin') not in (None, 'null', lease.origin)):
                return self._error(403)
            path = lease.root.joinpath(*parts)
            manager._authorize(lease, path)
            info, (content_type, maximum) = _safe_file(path, lease.root)
            if options:
                manager._reserve(lease, 0)
                self.send_response(204)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Access-Control-Allow-Methods', 'GET, HEAD, OPTIONS')
                self.send_header('Access-Control-Allow-Headers', 'Content-Type')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
            with os.fdopen(os.open(path, flags), 'rb') as source:
                opened = os.fstat(source.fileno())
                current, _ = _safe_file(path, lease.root)
                if (not stat.S_ISREG(opened.st_mode) or opened.st_size > maximum
                        or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)):
                    raise PreviewUnavailable('changed')
                etag = f'W/"{opened.st_mtime_ns:x}-{opened.st_size:x}-{opened.st_ino:x}"'
                if self.headers.get('If-None-Match') == etag:
                    manager._reserve(lease, 0)
                    self._headers(304, lease, content_type, 0, etag)
                    return
                manager._reserve(lease, 0 if head else opened.st_size)
                suffix = path.suffix.lower()
                output = None
                try:
                    length = opened.st_size
                    pixels = _image_pixels(source, suffix)
                    if pixels and not head:
                        manager._reserve_image(lease, (path, opened.st_mtime_ns, opened.st_size, opened.st_ino), pixels)
                    if not head and suffix in {'.html', '.htm', '.css'}:
                        parent = lease.entry.parent.relative_to(lease.root).as_posix()
                        prefix = '/project-preview/' + token + '/' + (quote(parent, safe='/') + '/' if parent != '.' else '')
                        output, length = _transformed(source, suffix, prefix, cancelled=lease.revoked.is_set)
                        if length > opened.st_size:
                            manager._reserve(lease, length - opened.st_size, count=False)
                    body = output if output is not None else source
                    self._headers(200, lease, content_type, None if head and suffix in {'.html', '.htm', '.css'} else length, etag)
                    if not head:
                        remaining = length
                        while remaining and not lease.revoked.is_set():
                            data = body.read(min(CHUNK, remaining))
                            if not data:
                                break
                            self.wfile.write(data)
                            remaining -= len(data)
                finally:
                    if output is not None:
                        output.close()
        except (OSError, ValueError, RuntimeError):
            # No local path, source text, traceback, or credential names escape.
            if not self.headers_started and not self.wfile.closed:
                try:
                    self._error(404)
                except OSError:
                    pass

    def _headers(self, status, lease, content_type, length, etag):
        source = lease.origin + '/project-preview/' + lease.token + '/'
        csp = ("default-src 'none'; sandbox allow-scripts; "
               f"script-src 'unsafe-inline' {source}; style-src 'unsafe-inline' {source}; "
               f"img-src {source} data: blob:; font-src {source} data:; connect-src {source}; "
               "worker-src 'none'; frame-src 'none'; object-src 'none'; media-src 'none'; "
               f"base-uri 'none'; form-action 'none'; frame-ancestors {lease.origin}")
        self.headers_started = True
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        if status != 304 and length is not None:
            self.send_header('Content-Length', str(length))
        self.send_header('Content-Security-Policy', csp)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cross-Origin-Resource-Policy', 'cross-origin')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Cache-Control', 'private, max-age=0, must-revalidate')
        self.send_header('ETag', etag)
        self.end_headers()


class ProjectPreviewManager:
    """Two metadata-only capabilities; no listener, watcher or background worker."""
    def __init__(self, *, ttl=DEFAULT_TTL, max_previews=2, clock=time.monotonic):
        self.ttl, self.max_previews, self.clock = ttl, max_previews, clock
        self._lock = threading.RLock()
        self._leases = {}
        self._slots = threading.BoundedSemaphore(MAX_WORKERS)
        self._closed = False

    @staticmethod
    def _authorize(lease, path):
        try:
            allowed = not lease.revoked.is_set() and lease.authorize(path, lease.root) is not False
        except Exception:
            allowed = False
        if not allowed:
            raise PreviewUnavailable('파일 접근 권한이 바뀌었습니다. 파일을 다시 열어 주세요.')

    def create(self, path, root, *, origin, authorize, owner=None):
        path, root = Path(path), Path(root)
        parsed = urlsplit(origin)
        if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1'
                or not parsed.port or parsed.path or parsed.query or parsed.fragment
                or parsed.username or parsed.password):
            raise PreviewUnavailable('미리보기 연결을 확인해 주세요.')
        _safe_file(path, root)
        if path.suffix.lower() not in {'.html', '.htm'}:
            raise PreviewUnavailable('HTML 파일을 선택해 주세요.')
        lease = _Lease(secrets.token_urlsafe(24), path, root, authorize, owner, origin, self.clock())
        self._authorize(lease, path)
        with self._lock:
            if self._closed:
                raise PreviewUnavailable('앱을 종료하는 중입니다.')
            self._expire_locked()
            while len(self._leases) >= self.max_previews:
                oldest = min(self._leases.values(), key=lambda item: item.touched)
                self._leases.pop(oldest.token).revoked.set()
            self._leases[lease.token] = lease
        name = quote(path.relative_to(root).as_posix(), safe='/')
        return {'id': lease.token, 'url': f'/project-preview/{lease.token}/{name}',
                'name': path.name, 'message': '업무 폴더의 CSS, 이미지와 JavaScript를 함께 표시합니다. 외부 연결과 서버 실행은 지원하지 않습니다.'}

    def _expire_locked(self):
        now = self.clock()
        for token, lease in tuple(self._leases.items()):
            if now - lease.touched >= self.ttl:
                self._leases.pop(token).revoked.set()

    def _lookup(self, token):
        with self._lock:
            self._expire_locked()
            lease = self._leases.get(token)
            if lease:
                lease.touched = self.clock()
            return lease

    def _reserve(self, lease, size, *, count=True):
        with self._lock:
            if lease.revoked.is_set() or lease.requests >= MAX_REQUESTS or lease.transferred + size > MAX_TRANSFER:
                raise PreviewUnavailable('웹 미리보기의 조회 범위를 넘었습니다. 파일을 다시 열어 주세요.')
            lease.requests += int(count)
            lease.transferred += size

    def _reserve_image(self, lease, fingerprint, pixels):
        with self._lock:
            if lease.revoked.is_set():
                raise PreviewUnavailable('closed')
            if fingerprint not in lease.images:
                if lease.image_pixels + pixels > MAX_TOTAL_IMAGE_PIXELS:
                    raise PreviewUnavailable('image budget')
                lease.images.add(fingerprint)
                lease.image_pixels += pixels

    def serve(self, handler):
        """Handle only this capability prefix before main-app authentication."""
        if not urlsplit(handler.path).path.startswith('/project-preview/'):
            return False
        resource = _Resource(handler, self)
        if handler.command not in {'GET', 'HEAD', 'OPTIONS'}:
            resource._error(405)
            return True
        if not self._slots.acquire(blocking=False):
            resource._error(503)
            return True
        connection = getattr(handler, 'connection', None)
        previous_timeout = None
        try:
            if connection is not None:
                previous_timeout = connection.gettimeout()
                connection.settimeout(5)
            resource._serve(handler.command != 'GET', options=handler.command == 'OPTIONS')
        finally:
            if connection is not None:
                try:
                    connection.settimeout(previous_timeout)
                except OSError:
                    pass
            self._slots.release()
        return True

    def close(self, identifier, *, owner=None):
        with self._lock:
            lease = self._leases.get(identifier)
            if lease is None or (owner is not None and lease.owner != owner):
                return False
            self._leases.pop(identifier).revoked.set()
        return True

    def close_all(self):
        with self._lock:
            self._closed = True
            for lease in self._leases.values():
                lease.revoked.set()
            self._leases.clear()

    close_all_previews = close_all
