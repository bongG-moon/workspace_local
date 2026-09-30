"""Isolated synthetic rich-chat UI fixture. No Claude or personal data is used."""
import argparse
import json
from pathlib import Path
import struct
import sys
import time
import zlib

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from local_app.server import LocalApp, Server


def chart(path):
    # Small deterministic raster fixture, no external images or font dependency.
    width,height=640,280
    colors=[(135,122,207),(161,153,221),(103,145,134)]
    rows=[]
    for y in range(height):
        row=bytearray()
        for x in range(width):
            color=(247,248,253)
            for left,top,tint in zip((90,250,410),(150,105,55),colors):
                if left<=x<left+100 and top<=y<230:color=tint
            if y in (230,231) and 60<=x<=565:color=(215,220,233)
            row.extend(color)
        rows.append(b'\0'+bytes(row))
    def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    path.write_bytes(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b''.join(rows)))+chunk(b'IEND',b''))


parser=argparse.ArgumentParser()
parser.add_argument('--state',type=Path,required=True)
args=parser.parse_args()
state=args.state.resolve();state.mkdir(parents=True,exist_ok=False)
app=LocalApp(state,demo=True)
task=app.create('',True,managed=True,title='화면 검증 · 월간 실적')
item=app.get(task['id']);workspace=Path(item['workspace'])
chart(workspace/'실적 그래프.png')
(workspace/'실적.csv').write_text('월,매출,목표 달성률\n7월,120,95%\n8월,156,104%\n9월,192,112%\n',encoding='utf-8-sig')
(workspace/'analysis.py').write_text('values = [120, 156, 192]\nprint(sum(values))\n',encoding='utf-8')
run='rich-chat-fixture';now=time.time()
item['lastRunId']=run
item['messages']=[
    {'role':'user','text':'월별 실적을 표와 그래프로 정리하고 계산 코드도 보여줘.','files':[str(workspace/'실적.csv')],'runId':run},
    {'role':'assistant','text':'## 월별 실적\n매출은 **3개월 연속 증가**했습니다.\n\n| 월 | 매출 | 목표 달성률 |\n| --- | ---: | ---: |\n| 7월 | 120 | 95% |\n| 8월 | 156 | 104% |\n| 9월 | 192 | 112% |\n\n```python\nvalues = [120, 156, 192]\ntotal = sum(values)\nprint(f"총 매출: {total}")\n```\n\n아래 파일에서 상세 내용을 확인하세요.','runId':run}
]
item['executions']=[{'id':'fixture-shell','tool':'Bash','command':'python analysis.py','description':'월별 실적 합계 계산','state':'completed','output':'468\n','truncated':False,'startedAt':now-2,'finishedAt':now-1,'runId':run}]
item['artifacts']=[{'path':str(workspace/name),'name':name,'runId':run,'change':'created','observedAt':now,'size':(workspace/name).stat().st_size} for name in ('실적 그래프.png','실적.csv','analysis.py')]
item['state']='done';app.save(item['id'])
server=Server(app)
metadata={'url':server.origin+'/#token='+app.token,'sessionId':item['id'],'workspace':str(workspace)}
(state/'fixture.json').write_text(json.dumps(metadata,ensure_ascii=False),encoding='utf-8')
print(json.dumps(metadata,ensure_ascii=False),flush=True)
try:server.serve_forever(poll_interval=.2)
finally:app.close();server.server_close()
