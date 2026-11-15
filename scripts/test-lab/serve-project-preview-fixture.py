"""Real project-preview API with owned files, isolated state and no real CLI."""
import argparse
import json
import os
from pathlib import Path
import sys
import threading
import struct
import zlib
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', required=True, type=Path)
args = parser.parse_args()
output = args.output.absolute()
output.mkdir(parents=True, exist_ok=False)
config = output / 'isolated-claude'
config.mkdir()
(config / 'settings.json').write_text('{}', encoding='utf-8')
os.environ['CLAUDE_CONFIG_DIR'] = str(config)
os.environ['LOCALAPPDATA'] = str(output / 'local')
os.environ['LLMOPS_DEMO'] = '1'
for key in ('SK_BUDGET_HUD_API_KEY', 'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'OPENAI_API_KEY'):
    os.environ.pop(key, None)

from local_app.server import Handler, LocalApp, Server

work = output / 'project'
for name in ('styles', 'js', 'images', 'data', 'unused'):
    (work / name).mkdir(parents=True, exist_ok=True)
(work / 'index.html').write_text('''<!doctype html><html lang="ko"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>프로젝트 미리보기 확인</title>
<link rel="stylesheet" href="styles/site.css"><body><main>
<div class="eyebrow">PROJECT PREVIEW</div><h1>함께 연결된 웹 화면</h1>
<p>별도 CSS와 이미지, JavaScript와 데이터가 같은 화면에 반영됩니다.</p>
<img id="fixture-image" src="/images/pixel.png" alt="로컬 이미지 확인">
<section class="metric"><small>검증용 데이터</small><strong id="metric">준비 중</strong>
<div class="track"><div id="chart-bar"></div></div><button id="fixture-action">결과 확인</button>
<p id="fixture-result" role="status">버튼을 눌러 동작을 확인하세요.</p></section>
<aside id="fixture-security">연결 확인 중</aside></main><script type="module" src="js/main.js"></script></body></html>''', encoding='utf-8')
(work / 'styles/tokens.css').write_text(':root{--fixture-accent:rgb(92,81,181)}', encoding='utf-8')
(work / 'styles/site.css').write_text('''@import url("./tokens.css");
*{box-sizing:border-box}body{margin:0;background:#f4f3fa;color:#26283a;font:15px/1.6 sans-serif}
main{max-width:780px;margin:auto;padding:38px 30px}.eyebrow{font-size:11px;letter-spacing:.15em;color:var(--fixture-accent);font-weight:700}
h1{font-size:30px;line-height:1.3;margin:10px 0 14px}p{color:#666b80}
#fixture-image{display:block;width:100%;height:64px;border-radius:14px;object-fit:cover;background-image:url('/images/pixel.png')}
.metric{border-radius:18px;background:white;border:1px solid #e0deef;padding:24px;margin:24px 0}
.metric small{display:block;color:#74738a}.metric strong{display:block;font-size:42px;color:var(--fixture-accent)}
.track{height:8px;border-radius:8px;background:#eeedf7;overflow:hidden;margin:15px 0 22px}#chart-bar{height:100%;width:0;background:var(--fixture-accent)}
button{background:var(--fixture-accent);color:white;border:0;border-radius:9px;font:inherit;padding:10px 18px;cursor:pointer}
aside{font-size:12px;color:#72778c;border-top:1px solid #e0deef;padding-top:14px}''', encoding='utf-8')
(work / 'js/math.js').write_text('export const double = value => value * 2;', encoding='utf-8')
(work / 'data/metrics.json').write_text('{"count":21,"title":"42건"}', encoding='utf-8')
(work / 'js/main.js').write_text('''import {double} from './math.js';
const data=await fetch(new URL('../data/metrics.json',import.meta.url)).then(response=>response.json());
const count=double(data.count);document.querySelector('#metric').textContent=data.title;
document.querySelector('#chart-bar').style.width=count+'%';
document.querySelector('#fixture-action').onclick=()=>document.querySelector('#fixture-result').textContent='분석 완료 '+count+'건';
let parentDenied=false;try{void parent.document.body;}catch(_){parentDenied=true;}
const blocked=async url=>{try{await fetch(url);return false;}catch(_){return true;}};
const appApiBlocked=await blocked('/api/bootstrap');
const externalBlocked=await blocked('https://preview-network-check.invalid/never-send');
window.previewFixture={parentDenied,appApiBlocked,externalBlocked,count};
document.querySelector('#fixture-security').textContent='프로젝트 파일 연결 확인 · 앱 화면과 외부 연결은 분리됨';
document.body.dataset.fixtureReady='true';''', encoding='utf-8')
def png_chunk(kind, body):
    return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body))


# A small, visibly patterned local image makes screenshot evidence unambiguous.
width, height = 128, 48
rows = []
for y in range(height):
    row = bytearray()
    for x in range(width):
        stripe = (x // 24 + y // 12) % 3
        row.extend(((92, 81, 181), (126, 158, 224), (185, 201, 238))[stripe] + (255,))
    rows.append(b'\0' + row)
(work / 'images/pixel.png').write_bytes(b'\x89PNG\r\n\x1a\n' +
    png_chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)) +
    png_chunk(b'IDAT', zlib.compress(b''.join(rows))) + png_chunk(b'IEND', b''))
for index in range(300):
    (work / 'unused' / f'unrequested-{index}.js').write_text('/* not requested */', encoding='utf-8')

app = LocalApp(output / 'state', command=['project-preview-fixture-never-start'],
               info={'version':'fixture-only'}, managed_workspace_root=output / 'managed')
item = app.create(str(work), True, title='웹 프로젝트 미리보기 확인')
server = Server(app)
resource_requests = []


class FixtureHandler(Handler):
    def do_GET(self):
        route = urlsplit(self.path).path
        if route == '/fixture-status' and self.valid_request():
            with app.project_previews._lock:
                count = len(app.project_previews._leases)
            return self.reply({'activePreviews':count,'resourceRequests':resource_requests,
                               'isolatedProfile':True,'productionKeyUsed':False,'realCliUsed':False})
        if route.startswith('/project-preview/'):
            resource_requests.append('/'.join(unquote(route).split('/')[3:]))
        return super().do_GET()

    def do_POST(self):
        if urlsplit(self.path).path == '/fixture-stop' and self.valid_request():
            self.reply({'stopping':True})
            threading.Thread(target=server.shutdown, daemon=True).start()
            return
        return super().do_POST()


server.RequestHandlerClass = FixtureHandler
runtime = {'url':server.origin + '/#token=' + app.token,'origin':server.origin,
           'workId':item['id'],'entry':str(work / 'index.html'),'output':str(output),'pid':os.getpid()}
(output / 'runtime.json').write_text(json.dumps(runtime, ensure_ascii=False), encoding='utf-8')
print('Project-preview fixture ready. Owned local data and fake CLI only.', flush=True)
try:
    server.serve_forever(poll_interval=.1)
finally:
    app.close()
    server.server_close()
    (output / 'stopped.json').write_text(json.dumps({'closed':True,'remainingPreviews':len(app.project_previews._leases)}), encoding='utf-8')
