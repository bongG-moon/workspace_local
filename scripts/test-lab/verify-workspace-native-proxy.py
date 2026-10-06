"""Opt-in real WebView2 proxy regression, using only an isolated synthetic server.

Build deploy/New-WorkspaceDesktop.ps1 first, then pass --desktop and a fresh
--state directory. This enables the administrator HTTP proxy without elevating
Windows, running Claude, or reading a real Workspace profile. The fixture holds
file/catalog reads and abandoned event polls while a clicked control is sent.
"""
import argparse
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import subprocess
import threading
import time


PAGE = '''<!doctype html><meta charset="utf-8"><title>Workspace proxy verification</title>
<p>Isolated Workspace responsiveness check</p><button id="control">Check control</button>
<script>
const headers={Authorization:'Bearer '+location.hash.slice(7)};
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function run(){
  const requests=[];
  for(let i=0;i<EVENT_COUNT;i++){
    const abort=new AbortController();
    requests.push(fetch('/api/events?n='+i,{headers,signal:abort.signal}).catch(()=>{}));
    ABORT_EVENTS
  }
  for(let i=0;i<READ_COUNT;i++) requests.push(fetch('/api/slow-read?n='+i,{headers}).catch(()=>{}));
  for(let i=0;i<UPLOAD_COUNT;i++) requests.push(fetch('/api/attachments/upload',{method:'POST',headers:{...headers,'Content-Type':'application/octet-stream'},body:'fixture'}).catch(()=>{}));
  await sleep(300);
  let measured;
  document.querySelector('#control').addEventListener('click',async()=>{
    const start=performance.now();
    const response=await fetch('/api/control',{method:'POST',headers:{...headers,'Content-Type':'application/json'},body:'{}'});
    const result=await response.json();
    measured={latencyMs:performance.now()-start,ok:result.ok,status:response.status};
    await fetch('/api/measure',{method:'POST',headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify(measured)});
  });
  document.querySelector('#control').click();
}run().catch(error=>document.body.textContent=error.message);
</script>'''


def scenario(binary, state, *, event_count, read_count, abort_events, upload_count):
    token = secrets.token_urlsafe(32)
    page = (PAGE.replace('EVENT_COUNT', str(event_count)).replace('READ_COUNT', str(read_count))
            .replace('UPLOAD_COUNT', str(upload_count))
            .replace('ABORT_EVENTS', 'setTimeout(()=>abort.abort(),100);' if abort_events else '')).encode('utf-8')
    release = threading.Event()
    measured = threading.Event()
    lock = threading.Lock()
    result = {'eventRequests': 0, 'readRequests': 0, 'uploadRequests': 0}
    timer = None

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def handle(self):
            try:
                super().handle()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # This fixture deliberately aborts fetches and closes its host.

        def reply(self, data, content_type='application/json'):
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # Closing only the fixture browser abandons pending polls.

        def do_GET(self):
            nonlocal timer
            if self.path == '/':
                return self.reply(page, 'text/html; charset=utf-8')
            if self.headers.get('Authorization') != 'Bearer ' + token:
                return self.send_error(403)
            if self.path.startswith(('/api/events?', '/api/slow-read?')):
                with lock:
                    result['eventRequests' if self.path.startswith('/api/events?') else 'readRequests'] += 1
                    if timer is None:
                        timer = threading.Timer(3, release.set)
                        timer.daemon = True
                        timer.start()
                release.wait(8)
            return self.reply(b'{"ok":true}')

        def do_POST(self):
            nonlocal timer
            if self.headers.get('Authorization') != 'Bearer ' + token:
                return self.send_error(403)
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length < 1024:
                return self.send_error(400)
            payload = self.rfile.read(length)
            if self.path == '/api/attachments/upload':
                with lock:
                    result['uploadRequests'] += 1
                    if timer is None:
                        timer = threading.Timer(3, release.set)
                        timer.daemon = True
                        timer.start()
                release.wait(8)
            if self.path == '/api/measure':
                with lock:
                    result.update(json.loads(payload))
                measured.set()
            self.reply(b'{"ok":true}')

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    process = subprocess.Popen([str(binary)], cwd=binary.parent,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW)
    events = deque(maxlen=32)
    reader = threading.Thread(target=lambda: events.extend(process.stdout), daemon=True)
    reader.start()
    try:
        config = {'url': f'http://127.0.0.1:{server.server_port}/#token={token}',
                  'profile': str(state.resolve()), 'background': False, 'adminApiBridge': True}
        process.stdin.write(json.dumps(config) + '\n')
        process.stdin.flush()
        if not measured.wait(20):
            raise AssertionError('Fixture did not finish: ' + str(list(events)))
        with lock:
            report = dict(result)
        report['nativeFailures'] = [json.loads(row) for row in events if 'failed' in row]
        assert report['ok'] and report['status'] == 200 and not report['nativeFailures'], report
        # WebView may prioritize the control ahead of some queued reads when
        # abandoned polls already occupy browser-side origin connections.
        # The reads-only case proves all four read slots were occupied.
        assert report['readRequests'] >= (min(read_count, 2) if event_count else read_count), report
        if event_count:
            assert report['eventRequests'] >= 1, report
        if upload_count:
            assert report['uploadRequests'] >= 1, report
        return report
    finally:
        release.set()
        if timer is not None:
            timer.cancel()
        if process.poll() is None:
            process.stdin.write('{"id":"fixture-close","command":"close"}\n')
            process.stdin.flush()
            try:
                process.wait(8)
            except subprocess.TimeoutExpired:
                process.terminate()  # Only this exact fixture-owned subprocess.
                process.wait(5)
        process.stdin.close()
        reader.join(2)
        process.stdout.close()
        server.shutdown()
        server.server_close()
        worker.join(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--desktop', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--record-baseline', action='store_true', help='Record an old binary without enforcing the latency threshold.')
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('This check requires Windows and WebView2.')
    binary = args.desktop.resolve(strict=True)
    args.state.mkdir(parents=True, exist_ok=False)
    results = {}
    for name, events, reads, abort, uploads in (
            ('events', 8, 0, False, 0), ('aborted-events', 8, 0, True, 0),
            ('slow-reads', 0, 4, False, 0), ('mixed', 8, 4, True, 0),
            ('uploads', 0, 0, False, 4)):
        results[name] = scenario(binary, args.state / name,
                                 event_count=events, read_count=reads, abort_events=abort, upload_count=uploads)
    report = {'scope': 'isolated real WebView2 administrator proxy; actual Windows role unchanged; no Claude',
              'results': results}
    (args.state / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not args.record_baseline:
        assert all(row['latencyMs'] < 1000 for row in results.values()), results


if __name__ == '__main__':
    main()
