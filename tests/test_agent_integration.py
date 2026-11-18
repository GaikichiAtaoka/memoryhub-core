"""Synthetic integration tests; always index inside an ephemeral workspace."""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from examples.agent_integration import (
    SYNTHETIC_ROOT,
    build_agent_messages,
    main,
    retrieve_for_agent,
)


class MemoryHubAgentIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.sandbox = tempfile.TemporaryDirectory(prefix="memoryhub-integration-")
        self.addCleanup(self.sandbox.cleanup)
        self.root = Path(self.sandbox.name) / "workspace"
        shutil.copytree(
            SYNTHETIC_ROOT, self.root,
            ignore=shutil.ignore_patterns("memory_index.sqlite*"),
        )

    def test_readonly_source_and_derived_index(self):
        source = self.root / "data/logs/chat_20261011.jsonl"
        before = source.read_bytes()
        result, messages = retrieve_for_agent(self.root, "青い本")
        self.assertTrue(result["ok"], result)
        self.assertGreaterEqual(result["candidate_count"], 1)
        self.assertEqual(source.read_bytes(), before)
        self.assertTrue((self.root / "data/memory/memory_index.sqlite").exists())
        self.assertEqual([m["role"] for m in messages], ["system", "system", "user"])
        self.assertIn("MEMORY HUB", messages[1]["content"])
        self.assertIn("青い本", messages[2]["content"])

    def test_offline_mode_does_not_contact_ollama(self):
        with patch("examples.agent_integration.call_local_ollama") as call_model:
            with patch("builtins.print") as display:
                status = main([
                    "--base", str(self.root), "--query", "図書館",
                ])
        self.assertEqual(status, 0)
        call_model.assert_not_called()
        self.assertTrue(display.called)

    def test_build_messages_does_not_promote_memory_to_user(self):
        messages = build_agent_messages("今の質問", "過去の引用")
        self.assertEqual([m["role"] for m in messages], ["system", "system", "user"])
        self.assertEqual(messages[-1]["content"], "今の質問")
        self.assertNotIn("過去の引用", messages[-1]["content"])


if __name__ == "__main__":
    unittest.main()
