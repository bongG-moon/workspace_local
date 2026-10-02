"""Run isolated Workspace regressions and save a compact, non-sensitive report.

Three interactive-user launcher fixtures run separately: the sandbox account is
deliberately rejected by the real startup identity guard. No guard is bypassed.
"""
import argparse
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
SEPARATE = {'test_launcher_uses_existing_terminal_alias_and_configuration',
            'test_different_running_build_is_not_reused_or_killed',
            'test_hidden_launcher_and_reopen_reuse_with_spaced_state_path'}


def cases(suite):
    for case in suite:
        if isinstance(case, unittest.TestSuite):
            yield from cases(case)
        else:
            yield case


parser = argparse.ArgumentParser()
parser.add_argument('--report', type=Path, default=ROOT / 'build/qa-workspace-0.23.1-tests.json')
args = parser.parse_args()
loader = unittest.TestLoader()
suite = loader.discover(str(ROOT / 'tests'), pattern='test_workspace*.py')
suite.addTests(loader.loadTestsFromName('test_local_workspace'))
selected = [case for case in cases(suite) if case._testMethodName not in SEPARATE]
result = unittest.TextTestRunner(verbosity=1).run(unittest.TestSuite(selected))
# Some older fixtures use self.id for a task ID. Call the base method explicitly.
identifier = unittest.TestCase.id
report = {'run': result.testsRun, 'success': result.wasSuccessful(),
          'failures': [identifier(case) for case, _ in result.failures],
          'errors': [identifier(case) for case, _ in result.errors],
          'skipped': [{'id': identifier(case), 'reason': reason} for case, reason in result.skipped],
          'separateCurrentUserTests': sorted(SEPARATE)}
args.report.parent.mkdir(parents=True, exist_ok=True)
args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(report, ensure_ascii=False))
sys.exit(not result.wasSuccessful())
