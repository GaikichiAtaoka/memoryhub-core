# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Public, synthetic-only regressions for the adapted real MemoryHub core."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from memoryhub import (
    HUB_TOOL, rebuild_memory_index, execute_memory_hub,
    render_memory_hub_context, search_temporal_memory,
    clear_embedding_cache, embedding_cache_stats,
)
from memoryhub.memory_hub_temporal_bridge import (
    _local_embedding, date_target, hinted_days,
)
from memoryhub.memory_sidecar_index import (
    parse_dialogue_jsonl, search_candidates,
)

FAKE_DIALOGUE = {
    "20261010": [
        {"time": "2026-10-10T21:15:00+09:00", "role": "user",
         "message_id": "u_fictional_1", "content": "明日は図書館で青い本を借りる予定です。"},
        {"time": "2026-10-10T21:15:08+09:00", "role": "assistant",
         "message_id": "a_fictional_1", "content": "青い本を借りたんですね。"},
        {"time": "2026-10-10T21:41:00+09:00", "role": "user",
         "message_id": "u_test_1", "is_test": True,
         "content": "隔離されたテスト発言を検索に混ぜない"},
    ],
    "20261011": [
        {"time": "2026-10-11T09:05:00+09:00", "role": "user",
         "message_id": "u_fictional_2", "content": "昨日は家でゆっくり休みました。"},
        {"time": "2026-10-11T11:20:00+09:00", "role": "user",
         "message_id": "u_fictional_3",
         "content": "図書館には行かなかった。青い本はまだ借りていません。"},
        {"time": "2026-10-11T11:21:00+09:00", "role": "assistant",
         "message_id": "a_fictional_2", "content": "了解しました。"},
    ],
}


class CoreTests(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="memoryhub-public-fixtures-")
        self.addCleanup(td.cleanup)
        self.base = Path(td.name)
        (self.base / "data/logs").mkdir(parents=True)
        (self.base / "data/memory").mkdir(parents=True)
        (self.base / "data/projects").mkdir(parents=True)
        (self.base / "data/policy").mkdir(parents=True)
        self.logs = {}
        for day, entries in FAKE_DIALOGUE.items():
            p = self.base / "data/logs" / ("chat_" + day + ".jsonl")
            p.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n"
                                 for x in entries), encoding="utf8")
            self.logs[day] = p
        (self.base / "data/projects/current.json").write_text(
            json.dumps({"catalog": {"topic": "架空の図書リスト制作", "status": "planning"}},
                       ensure_ascii=False), encoding="utf8")
        (self.base / "data/policy/current_snapshot.json").write_text(
            '{"unauthorized_secret":"PRIVATE_CANARY_NEVER_SHOW"}',
            encoding="utf8")
        self.reflection = self.base / "data/memory/introspection_structured.jsonl"
        self.reflection.write_text(json.dumps({
            "time": "2026-10-11T23:00:00+09:00",
            "structured": {
                "user_explicit": [{"content": "昨日は家で休んだとの申告",
                                   "source_refs": ["u_fictional_2"]}],
                "agent_interpretations": [{
                    "content": "図書館の話題を今度もう一度確かめたい",
                    "confidence": "tentative",
                }],
            },
        }, ensure_ascii=False) + "\n", encoding="utf8")

    def build(self):
        result = rebuild_memory_index(self.base)
        self.assertTrue(result["ok"], result)
        return result

    def hub(self, action="search", **args):
        return execute_memory_hub(action=action, base_dir=self.base, **args)

    def test_hub_entry_is_read_only(self):
        self.assertEqual(HUB_TOOL["function"]["name"], "memory.hub")
        self.assertEqual(
            HUB_TOOL["function"]["parameters"]["properties"]["action"]["enum"],
            ["search", "inspect", "chronology", "day"],
        )

    def test_synthetic_log_parses_and_excludes_fixture(self):
        rows = parse_dialogue_jsonl(self.logs["20261010"], self.base)
        self.assertEqual(len(rows), 2)
        self.assertFalse(any("隔離されたテスト発言" in x.get("primary_text", "")
                             for x in rows))

    def test_index_does_not_change_original_dialogue_bytes(self):
        before = {day: hashlib.sha256(p.read_bytes()).hexdigest()
                  for day, p in self.logs.items()}
        self.build()
        after = {day: hashlib.sha256(p.read_bytes()).hexdigest()
                 for day, p in self.logs.items()}
        self.assertEqual(before, after)

    def test_eposodic_search_finds_original_user_source(self):
        self.build()
        result = self.hub(query="図書館", scope="episodic")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["status"], "FOUND")
        self.assertTrue(any(x.get("speaker") == "user"
                            and "図書館" in x.get("text", "")
                            for x in result["results"]))

    def test_inspect_resolves_original_source(self):
        self.build()
        result = self.hub(query="図書館", scope="episodic")
        source = next(x for x in result["results"] if x.get("speaker") == "user")
        inspected = self.hub(action="inspect", memory_id=source["memory_id"])
        self.assertEqual(inspected["status"], "FOUND")
        self.assertEqual(inspected["results"][0]["source_locator"],
                         source["source_locator"])

    def test_day_results_are_time_scoped(self):
        self.build()
        result = self.hub(action="day", date="2026-10-10", scope="episodic")
        self.assertTrue(result["ok"], result)
        self.assertTrue(any(x.get("speaker") == "user" for x in result["results"]))
        self.assertTrue(any(x.get("speaker") == "assistant" for x in result["results"]))
        rendered = render_memory_hub_context(result)
        self.assertNotIn("隔離されたテスト発言", rendered)

    def test_chronology_retains_source(self):
        self.build()
        result = self.hub(action="chronology", query="図書館",
                          scope="episodic", limit=8)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["results"])

    def test_semantic_scope_reads_whitelist_only(self):
        self.build()
        result = self.hub(query="図書リスト", scope="semantic")
        self.assertEqual(result["status"], "FOUND")
        self.assertIn("図書", render_memory_hub_context(result))
        self.assertNotIn("PRIVATE_CANARY_NEVER_SHOW",
                         json.dumps(result, ensure_ascii=False))

    def test_reflective_distinguishes_firsthand_and_interpretation(self):
        self.build()
        result = self.hub(query="図書館", scope="reflective")
        self.assertTrue(result["ok"], result)
        authorities = {x.get("source_authority") for x in result["results"]}
        self.assertIn("subjective_non_fact", authorities)

    def test_procedural_never_invented(self):
        result = self.hub(query="新しい機能", scope="procedural")
        self.assertEqual(result["status"], "PROCEDURAL_STORE_NOT_CONNECTED")
        self.assertFalse(result["ok"])

    def test_no_index_is_not_false_absence(self):
        result = self.hub(query="図書館", scope="episodic")
        self.assertEqual(result["status"], "INCOMPLETE_SOURCES")
        self.assertFalse(result["ok"])

    def test_stale_index_reports_uncertainty(self):
        self.build()
        p = self.logs["20261010"]
        p.write_text(p.read_text(encoding="utf8").replace("借りる予定",
                                                           "借りない予定"), encoding="utf8")
        result = self.hub(query="借りない", scope="episodic")
        self.assertIn(result["status"], {"INCOMPLETE_SOURCES", "PARTIAL_UNVERIFIED"})
        self.assertEqual(result["source_health"]["index"]["status"], "INDEX_STALE")

    def test_nonexistent_inspect_id_fails(self):
        self.build()
        result = self.hub(action="inspect", memory_id="hub:e:this-is-not-real")
        self.assertFalse(result["ok"])
        self.assertEqual(result["results"], [])

    def test_date_relative_to_source_not_question(self):
        source = datetime.fromisoformat("2026-10-11T09:05:00+09:00")
        self.assertEqual(hinted_days(source, "昨日は家で休みました。"),
                         ["2026-10-10"])
        target = date_target("10月10日に休んだ？",
                             reference_now="2026-10-11T23:00:00+09:00")
        self.assertEqual(target["day"], "2026-10-10")

    def test_temporal_search_source_backed_without_ollama(self):
        def fake_embed(strings, *, model):
            # Deliberately simplistic; verifies plumbing, never model accuracy.
            return [[1.0, 0.0] for _ in strings]
        result = search_temporal_memory(
            base_dir=self.base, query="10月10日に休んだ？",
            reference_now="2026-10-11T23:00:00+09:00",
            embed_fn=fake_embed,
        )
        self.assertEqual(result["status"], "FOUND")
        self.assertTrue(any(x.get("source_message_id") == "u_fictional_2"
                            for x in result["results"]))
        self.assertTrue(all(x["original_text_changed"] is False
                            for x in result["results"]))
        self.assertTrue(all(x["asserted_event_time"] is None
                            for x in result["results"]))

    def test_embedding_unavailable_does_not_change_sources(self):
        original = self.logs["20261011"].read_bytes()
        def unavailable(strings, *, model):
            raise TimeoutError("synthetic test transport offline")
        result = search_temporal_memory(
            base_dir=self.base, query="10月10日に休んだ？",
            reference_now="2026-10-11T23:00:00+09:00",
            embed_fn=unavailable,
        )
        self.assertEqual(result["status"], "EMBEDDING_UNAVAILABLE")
        self.assertEqual(self.logs["20261011"].read_bytes(), original)

    def test_cached_embeddings_only_request_missing_strings(self):
        from memoryhub import memory_hub_temporal_bridge as bridge
        clear_embedding_cache()
        calls = []
        def embed(strings, *, model, timeout_s):
            calls.append(list(strings))
            return [[float(len(s)), 2.0] for s in strings]
        with patch.object(bridge, "_fetch_local_vectors", side_effect=embed):
            bridge._local_embedding(["query1", "record1", "record2"])
            bridge._local_embedding(["query2", "record1", "record2"])
        self.assertEqual([len(x) for x in calls], [3, 1])
        self.assertEqual(embedding_cache_stats()["requests"], 2)
        clear_embedding_cache()

    def test_index_returns_episodic_sources_without_mutating_policy(self):
        before = (self.base / "data/policy/current_snapshot.json").read_bytes()
        self.build()
        result = search_candidates(
            query="図書館",
            db_path=self.base / "data/memory/memory_index.sqlite",
        )
        self.assertTrue(result["ok"])
        self.assertEqual((self.base / "data/policy/current_snapshot.json").read_bytes(),
                         before)


if __name__ == "__main__":
    unittest.main()
