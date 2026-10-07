import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from local_app.appearance import Appearance
from local_app.server import LocalApp, Server

ROOT = Path(__file__).resolve().parents[1]


class AppearanceTests(unittest.TestCase):
    def test_default_persistence_and_system_resolution(self):
        with tempfile.TemporaryDirectory() as directory:
            appearance = Appearance(directory)
            self.assertEqual({'theme': 'light', 'effective': 'light'}, appearance.snapshot())
            appearance.configure('dark')
            self.assertEqual('dark', Appearance(directory).snapshot()['theme'])
            with patch('local_app.appearance.system_theme', return_value='dark'):
                self.assertEqual({'theme': 'system', 'effective': 'dark'}, appearance.configure('system'))
            self.assertEqual('system', json.loads(appearance.path.read_text())['theme'])

    def test_invalid_or_failed_save_retains_preference(self):
        with tempfile.TemporaryDirectory() as directory:
            appearance = Appearance(directory)
            for value in ('auto', None, [], {}, '<script>'):
                with self.assertRaises(ValueError):
                    appearance.configure(value)
            with patch('local_app.appearance.os.replace', side_effect=OSError('fixture')):
                with self.assertRaises(OSError):
                    appearance.configure('dark')
            self.assertEqual('light', appearance.snapshot()['theme'])
            self.assertEqual([], list(Path(directory).iterdir()))

    def test_corrupt_or_oversized_preference_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'appearance.json'
            for raw in ('{', '{"theme": []}', '{"theme":"dark"}' + ' ' * 1025):
                path.write_text(raw)
                self.assertEqual('light', Appearance(directory).snapshot()['theme'])

    def test_routes_are_authenticated_and_first_html_has_saved_theme(self):
        with tempfile.TemporaryDirectory() as directory:
            app = LocalApp(Path(directory), demo=True)
            server = Server(app)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            def request(route, data=None, auth=True):
                req = Request(server.origin + route,
                    data=json.dumps(data).encode() if data is not None else None,
                    headers={'Content-Type': 'application/json', **({'Authorization':'Bearer '+app.token} if auth else {})})
                try:
                    response = urlopen(req, timeout=5)
                except HTTPError as error:
                    response = error
                with response:
                    return response.status, response.read()
            try:
                self.assertEqual(403, request('/api/appearance', {'theme':'dark'}, False)[0])
                self.assertEqual(400, request('/api/appearance', {'theme':'dark','unexpected':True})[0])
                self.assertEqual(400, request('/api/appearance', {'theme':[]})[0])
                app._desktop_window = Mock()
                app._desktop_window.set_appearance.return_value = False
                code, raw = request('/api/appearance', {'theme':'dark'})
                self.assertEqual(200, code)
                self.assertFalse(json.loads(raw)['nativeApplied'])
                app._desktop_window.set_appearance.assert_called_once_with('dark')
                app._desktop_window = None
                self.assertEqual('dark', json.loads(request('/api/bootstrap')[1])['appearance']['theme'])
                html = request('/', auth=False)[1].decode()
                self.assertIn('data-appearance="dark" data-theme="dark"', html)
                self.assertLess(html.index('/appearance.js'), html.index('/app.css'))
                self.assertGreater(html.index('/appearance.css'), html.index('/app.css'))
                for asset in ('appearance.js', 'appearance.css'):
                    self.assertEqual(200, request('/'+asset, auth=False)[0])
            finally:
                app._desktop_window = None
                server.shutdown(); server.server_close(); thread.join(2); app.close()

    @unittest.skipUnless(shutil.which('node'), 'Node required')
    def test_frontend_persistence_failure_and_event_driven_system_mode(self):
        script = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const root={dataset:{appearance:'dark'}},select={value:''},note={},metas=new Map();
const node=()=>({attrs:{},setAttribute(k,v){this.attrs[k]=v;}}),toggle=node(),icon=node();
const nodes={'appearance-select':select,'appearance-message':note,'appearance-toggle':toggle,'appearance-toggle-icon':icon};
let callback, listeners=0, requests=0, reject=false, release=null, pause=false, notices=[];
const media={matches:false,addEventListener(name,handler){assert.equal(name,'change');callback=handler;listeners++;}};
const context={document:{documentElement:root,getElementById:id=>nodes[id]||null,
querySelector:id=>({setAttribute(k,v){metas.set(id,v);}})},matchMedia:()=>media,toast:message=>notices.push(message),
api:async(path,data)=>{requests++;assert.equal(path,'/api/appearance');if(pause)await new Promise(resolve=>release=resolve);if(reject)throw Error('failure');return {theme:data.theme};}};
vm.createContext(context);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),context);
(async()=>{
assert.equal(root.dataset.theme,'dark');assert.equal(listeners,1);assert.equal(toggle.disabled,true);
context.WorkspaceAppearance.start({theme:'dark'});context.WorkspaceAppearance.start({theme:'dark'});
assert.equal(toggle.disabled,false);assert.equal(icon.attrs.href,'#i-sun');
select.value='system';await select.onchange();assert.equal(root.dataset.theme,'light');
media.matches=true;callback();assert.equal(root.dataset.theme,'dark');assert.equal(requests,1);
reject=true;select.value='light';await select.onchange();assert.equal(root.dataset.appearance,'system');assert.equal(select.disabled,false);
reject=false;select.value='light';await select.onchange();media.matches=true;callback();assert.equal(root.dataset.theme,'light');
assert.equal(listeners,1);assert.equal(requests,3);
// Toggle uses the effective system mode and saves one explicit choice.
select.value='system';await select.onchange();assert.equal(root.dataset.theme,'dark');
assert.equal(icon.attrs.href,'#i-sun');assert.equal(toggle.attrs['aria-label'],'밝은 모드로 전환');
await toggle.onclick();assert.equal(root.dataset.appearance,'light');assert.equal(select.value,'light');
assert.equal(icon.attrs.href,'#i-moon');assert.equal(toggle.attrs['aria-label'],'어두운 모드로 전환');
// Repeated input during save must not create competing theme writes.
pause=true;const pending=toggle.onclick();assert.equal(toggle.disabled,true);assert.equal(select.disabled,true);
const before=requests;await toggle.onclick();assert.equal(requests,before);release();await pending;pause=false;
assert.equal(root.dataset.theme,'dark');assert.equal(select.value,'dark');assert.equal(toggle.attrs['aria-busy'],'false');
// Failure remains visible with the settings dialog closed and restores both controls.
reject=true;await toggle.onclick();assert.equal(root.dataset.theme,'dark');assert.equal(icon.attrs.href,'#i-sun');
assert.equal(select.value,'dark');assert.equal(toggle.disabled,false);assert.equal(notices.at(-1),'failure');
assert.equal(listeners,1);
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        result = subprocess.run([shutil.which('node'), '-e', script, str(ROOT/'local_app/web/appearance.js')], capture_output=True, text=True, timeout=15)
        self.assertEqual(0, result.returncode, result.stderr)


if __name__ == '__main__':
    unittest.main()
