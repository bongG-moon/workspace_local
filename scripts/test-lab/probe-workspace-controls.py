"""Inspect session-only CLI controls without sending a user/AI message.

Only allowlisted runtime fields are retained; raw settings and stderr are not
printed or saved. Existing personal settings are hashed before and after.
"""
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from local_app.bridge import resolve_cli, probe_cli, cli_arguments, HIDDEN

out = ROOT / 'build/qa-workspace-control-protocol'
out.mkdir(exist_ok=True)
workspace = out / 'empty-workspace'
workspace.mkdir(exist_ok=True)
config = Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude')
def hashes():
    return {name: hashlib.sha256((config/name).read_bytes()).hexdigest() if (config/name).is_file() else None
            for name in ('settings.json','settings.local.json','CLAUDE.md','.mcp.json','plugins/installed_plugins.json')}
before = hashes()
command = resolve_cli()
info = probe_cli(command)
process = subprocess.Popen(cli_arguments(command, info), cwd=workspace, stdin=subprocess.PIPE,
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=HIDDEN)
frames = queue.Queue()
def read():
    for line in process.stdout:
        try: frames.put(json.loads(line))
        except (ValueError, UnicodeError): pass
threading.Thread(target=read, daemon=True).start()
events = []
def project_event(frame):
    if frame.get('type') == 'system' and frame.get('subtype') in ('status','init'):
        events.append({key:frame[key] for key in ('type','subtype','status','permissionMode','effort','model') if key in frame})
def control(subtype, **fields):
    rid = str(uuid.uuid4())
    process.stdin.write((json.dumps({'type':'control_request','request_id':rid,'request':{'subtype':subtype,**fields}})+'\n').encode())
    process.stdin.flush()
    until=time.monotonic()+60
    while time.monotonic()<until:
        frame=frames.get(timeout=max(.1,until-time.monotonic()))
        project_event(frame)
        response=frame.get('response',{})
        if frame.get('type')=='control_response' and response.get('request_id')==rid:
            return response
    raise TimeoutError(subtype)
def state():
    response=control('get_settings')
    detail=response.get('response') or {}
    applied=detail.get('applied') or {}
    return {'subtype':response.get('subtype'), 'responseKeys':list(detail), 'appliedKeys':list(applied),
            'applied':{key:applied[key] for key in ('model','effort','permissionMode','ultracode','ultracodeRequested','availablePermissionModes','permissionModes') if key in applied}}
result={'cliVersion':info['version'],'userMessagesSent':0}
try:
    initialized=control('initialize')
    detail=initialized.get('response') or {}
    result['initializeKeys']=list(detail)
    result['initialPermissionMode']=detail.get('current_permission_mode')
    session_state=detail.get('session_state') or {}
    result['sessionStateKeys']=list(session_state) if isinstance(session_state,dict) else []
    result['permissionMetadata']={key:value for key,value in session_state.items()
                                  if any(term in key.casefold() for term in ('mode','effort','ultracode'))
                                  and (value is None or isinstance(value,(str,bool,int)))} if isinstance(session_state,dict) else {}
    result['models']=[{key:row[key] for key in ('value','displayName','resolvedModel','supportsEffort','supportedEffortLevels') if key in row}
                      for row in detail.get('models',[]) if isinstance(row,dict)]
    result['initial']=state()
    result['modeChanges']=[]
    for mode in ('plan','acceptEdits','auto','default'):
        response=control('set_permission_mode',mode=mode)
        result['modeChanges'].append({'requested':mode,'responseType':response.get('subtype'),'actual':(response.get('response') or {}).get('mode'),
                                      'responseKeys':list(response.get('response') or {}),'state':state()})
    response=control('apply_flag_settings',settings={'effortLevel':'high','ultracode':result['initial']['applied'].get('ultracodeRequested',False)})
    result['effortChange']={'requested':'high','responseType':response.get('subtype'),'state':state()}
    result['events']=events
finally:
    process.stdin.close()
    try: process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        if os.name == 'nt':
            killer = Path(os.environ['SystemRoot']) / 'System32/taskkill.exe'
            subprocess.run([str(killer),'/PID',str(process.pid),'/T','/F'],
                           capture_output=True,creationflags=HIDDEN,timeout=10,check=True)
        else:
            process.kill()
        process.wait(timeout=5)
    result['personalSettingsUnchanged']=before==hashes()
    assert result['personalSettingsUnchanged']
    (out/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({key:result[key] for key in ('cliVersion','userMessagesSent','initialPermissionMode','sessionStateKeys','permissionMetadata','modeChanges','effortChange','events','personalSettingsUnchanged')},ensure_ascii=False))
