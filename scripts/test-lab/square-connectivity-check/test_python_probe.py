"""Verify the single-file Python diagnostic against disposable HTTP fixtures.

This uses isolated localhost fixtures and shared GET protocol cases. It never imports or starts the real platform.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import probe_fixtures as fixtures


SOURCE: Path
SCENARIOS = 0


class PythonProbeTests(fixtures.ProbeCases):
    def run_probe(self, url):
        global SCENARIOS
        SCENARIOS += 1
        with tempfile.TemporaryDirectory(prefix="square-python-probe-case-") as temporary:
            target = Path(temporary) / "report.json"
            completed = subprocess.run(
                [sys.executable, "-X", "utf8", "-B", str(SOURCE), "--url", url, "--report", str(target)],
                cwd=temporary, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=22,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
            self.assertTrue(target.is_file(), "The Python CLI did not produce its explicit report")
            self.assertEqual({path.name for path in Path(temporary).iterdir()}, {"report.json"})
            report = json.loads(target.read_text(encoding="utf-8-sig"))
        for key in ("category", "title", "summary", "requestUrl", "httpStatus", "platformConfirmed",
                    "mongoPingConfirmed", "collectionReadiness", "durationMs", "notes"):
            self.assertIn(key, report)
        self.assertEqual(report["collectionReadiness"], "not_tested")
        self.assertIsInstance(report["platformConfirmed"], bool)
        self.assertIsInstance(report["mongoPingConfirmed"], bool)
        return report

    def test_no_files_written_without_report_option(self):
        global SCENARIOS
        SCENARIOS += 1
        with fixtures.FixtureServer() as server, tempfile.TemporaryDirectory(prefix="square-probe-nosave-") as temporary:
            completed = subprocess.run(
                [sys.executable, "-X", "utf8", "-B", str(SOURCE), "--url", server.url],
                cwd=temporary, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
            self.assertEqual(list(Path(temporary).iterdir()), [])
            self.assertTrue(completed.stdout, "The console should display a result")
            self.assert_single_get(server)

    def test_only_standard_library_dependencies(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8-sig"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        standard = getattr(sys, "stdlib_module_names", set())
        self.assertTrue(standard, "Run this regression test using Python 3.10 or newer")
        self.assertEqual(imported - standard - {"__future__"}, set())

    def test_invalid_encoded_paths_never_reach_server(self):
        suffixes = ("/%", "/%zz", "/%FF", "/%E0%A4%A", "/%256cogin",
                    "/square/%2e%2e/health", "/square%2F..%2Fhealth", "/%255capi",
                    "/square/%253fkey=secret", "/square/%250a")
        with fixtures.FixtureServer() as server:
            for suffix in suffixes:
                with self.subTest(suffix=suffix):
                    report = self.run_probe(server.url + suffix)
                    self.assertEqual(report["category"], "invalid_url")
                    self.assertFalse(report["platformConfirmed"])
            self.assertEqual(server.requests, [])


def main():
    global SOURCE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    arguments = parser.parse_args()
    SOURCE = arguments.source.resolve(strict=True)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(PythonProbeTests)
    result = unittest.TextTestRunner(stream=sys.stderr, verbosity=2).run(suite)
    print(json.dumps({"tests": result.testsRun, "probeCases": SCENARIOS,
                      "failures": len(result.failures), "errors": len(result.errors),
                      "isolatedHttpFixturesOnly": True, "realDatabaseUsed": False,
                      "pythonStandardLibraryOnly": True}))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
