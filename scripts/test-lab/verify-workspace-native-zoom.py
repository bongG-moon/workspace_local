"""Opt-in actual WebView2 zoom event check using an isolated local HTTP fixture."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import subprocess
import threading

ROOT = Path(__file__).resolve().parents[2]
PAGE = b'''<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="/zoom-feedback.css">
<script src="/zoom-feedback.js" defer></script><input id="editor" value="preserve draft">
<dialog id="fixture-dialog"><input id="dialog-input" value="preserve modal input"></dialog>'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--desktop', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('Windows with WebView2 is required.')
    binary = args.desktop.resolve(strict=True)
    args.output.mkdir(parents=True, exist_ok=False)
    state = (args.output / 'profile').resolve()
    report = (args.output / 'verification.json').resolve()
    fixture = binary.parent / 'Workspace.ZoomTest.exe'
    compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    subprocess.run([str(compiler), '/nologo', '/target:exe', '/platform:x64', f'/out:{fixture}',
        '/reference:System.dll', '/reference:System.Core.dll', '/reference:System.Drawing.dll',
        '/reference:System.Windows.Forms.dll', '/reference:System.Web.Extensions.dll',
        f'/reference:{binary.parent / "Microsoft.Web.WebView2.Core.dll"}',
        f'/reference:{binary.parent / "Microsoft.Web.WebView2.WinForms.dll"}',
        str(ROOT / 'scripts/test-lab/test-desktop-zoom.cs')], check=True, capture_output=True, text=True)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            if self.path == '/':
                data, mime = PAGE, 'text/html; charset=utf-8'
            elif self.path in {'/zoom-feedback.css', '/zoom-feedback.js'}:
                data = (ROOT / 'local_app/web' / self.path[1:]).read_bytes()
                mime = 'text/css' if self.path.endswith('.css') else 'text/javascript'
            else:
                return self.send_error(404)
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        token = secrets.token_urlsafe(32)
        result = subprocess.run([str(fixture), str(binary), f'http://127.0.0.1:{server.server_port}/#token={token}',
            str(state), str(report)], cwd=binary.parent, capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=35, creationflags=subprocess.CREATE_NO_WINDOW)
        print(report.read_text(encoding='utf-8') if report.exists() else result.stdout+result.stderr)
        if result.returncode: print(result.stdout+result.stderr)
        assert result.returncode == 0, 'Native zoom fixture failed'
    finally:
        server.shutdown()
        server.server_close()
        worker.join(2)


if __name__ == '__main__':
    main()
