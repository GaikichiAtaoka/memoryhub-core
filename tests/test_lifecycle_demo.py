# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Run the published offline lifecycle example through its user-facing CLI."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import memoryhub


class LifecycleDemoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="memoryhub-demo-tests-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.script = Path(__file__).resolve().parents[1] / "examples/lifecycle_demo.py"
        self.environment = dict(os.environ)
        # Exercise the same imported package in editable and installed builds;
        # do not depend on subprocess working-directory imports.
        self.environment["PYTHONPATH"] = str(Path(memoryhub.__file__).resolve().parents[1])

    def run_demo(self, *arguments):
        return subprocess.run(
            [sys.executable, str(self.script), *map(str, arguments)],
            cwd=self.base, env=self.environment,
            capture_output=True, text=True, timeout=30,
        )

    def check_report(self, process):
        self.assertEqual(process.returncode, 0, process.stderr)
        report = json.loads(process.stdout)
        self.assertEqual(report["fixture"], "invented_scripted_actions")
        self.assertIs(report["live_model"], False)
        self.assertTrue(report["checks"])
        self.assertTrue(all(value is True for value in report["checks"].values()), report)
        self.assertGreater(len(report["steps"]), 1)
        return report

    def test_default_demo_completes_and_cleans_its_temporary_workspace(self):
        report = self.check_report(self.run_demo())
        state = Path(report["state_path"])
        self.assertEqual(state.name, "lifecycle.sqlite")
        self.assertFalse(state.exists())
        self.assertFalse(state.parents[2].exists())
        self.assertEqual(list(self.base.iterdir()), [])

    def test_explicit_new_workspace_is_preserved_and_existing_path_is_refused(self):
        workspace = self.base / "chosen_workspace"
        report = self.check_report(self.run_demo("--base", workspace))
        state = Path(report["state_path"])
        self.assertEqual(state, workspace / "data/memory/lifecycle.sqlite")
        self.assertTrue(state.is_file())
        self.assertTrue((workspace / "data/logs/chat_20260115.jsonl").is_file())
        before = {p.relative_to(workspace): p.read_bytes()
                  for p in workspace.rglob("*") if p.is_file()}
        rejected = self.run_demo("--base", workspace)
        self.assertEqual(rejected.returncode, 1)
        self.assertIn("FileExistsError", rejected.stderr)
        self.assertEqual(rejected.stdout, "")
        after = {p.relative_to(workspace): p.read_bytes()
                 for p in workspace.rglob("*") if p.is_file()}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
