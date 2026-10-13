"""Exercise the installed CLI's plugin toggle in a disposable offline profile."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from local_app.customizations import Customizations
from local_app.owned_process import run_owned

parser=argparse.ArgumentParser()
parser.add_argument('--claude',required=True)
args=parser.parse_args()
with tempfile.TemporaryDirectory(prefix='native-customizations-',dir=ROOT/'build') as temp:
    root=Path(temp);config=root/'profile';workspace=root/'work';workspace.mkdir()
    os.environ['CLAUDE_CONFIG_DIR']=str(config)
    os.environ['CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC']='1'
    plugin=root/'plugin';manifest=plugin/'.claude-plugin/plugin.json';manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({'name':'workspace-fixture','version':'1.0.0','description':'Offline test only'}))
    registry=config/'plugins/installed_plugins.json';registry.parent.mkdir(parents=True)
    name='workspace-fixture@offline-fixture'
    registry.write_text(json.dumps({'version':2,'plugins':{name:[{'scope':'user','installPath':str(plugin),
        'version':'1.0.0','installedAt':'2026-10-13T00:00:00Z','lastUpdated':'2026-10-13T00:00:00Z'}]}}))
    def run(args, **kwargs):
        result=run_owned(args,**kwargs)
        if result.returncode:
            print('Fixture CLI error:',result.stdout,result.stderr)
        return result
    manager=Customizations(config,[str(Path(args.claude).resolve())],{'version':'2.1.296'},runner=run)
    for scope in ['user','local']:
        for value in [True,False]:
            snapshot=manager.snapshot(str(workspace),scope)
            result=manager.change(str(workspace),{'scope':scope,'kind':'plugins','name':name,'value':value,
                                                'revision':snapshot['revision']})
            assert result['ok']
            assert json.loads(manager.target(workspace,scope).read_text())['enabledPlugins'][name] is value
    print('PASS: native CLI enable/disable, local/user scopes, isolated profile; no AI prompt sent.')
