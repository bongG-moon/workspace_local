"""Frequent progress reports must leave sidebar click targets in place."""
from pathlib import Path
import subprocess
import unittest

from tests.test_workspace_frontend_state import HARNESS, NODE

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(NODE, "Node.js is required")
class WorkspaceStatusResponsivenessTests(unittest.TestCase):
    def test_repeated_progress_preserves_click_target_and_updates_status(self):
        scenario = r"""
active={id:'A',title:'작업 A',workspace:'C:/fixture',state:'running',connection:{}};
sessions=[{...active},{id:'B',title:'작업 B',workspace:'C:/other',state:'done'}];
renderSessions();
const target=$('sessions').children[1].querySelector('.session');
target.focus();
for(let i=0;i<100;i++)setStatus('running','파일 확인 '+i,'run-1');
assert.equal($('sessions').children[1].querySelector('.session'),target,
  'a status update between pointerdown and pointerup must not remove the button');
assert.equal(document.activeElement,target);
assert.equal($('status-text').textContent,'파일 확인 99');
assert.equal($('status').classList.contains('busy'),true);
setStatus('done');
assert.equal(sessions[0].state,'done');
assert.equal($('status').classList.contains('busy'),false);
assert.equal($('sessions').children[0].querySelector('.session-state-dot'),null);
enterShutdown('closing');
assert.equal($('sessions').children[0].querySelector('.session-pin').disabled,true);
assert.equal($('sessions').children[0].querySelector('.session-remove').disabled,true);
assert.equal($('sessions').children[0].querySelector('.session-drag').disabled,true);
"""
        result = subprocess.run(
            [NODE, '-', str(ROOT / 'local_app/web/app.js'), scenario],
            input=HARNESS, text=True, encoding='utf-8', capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
