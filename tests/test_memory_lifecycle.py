# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Synthetic public regressions for source-backed memory lifecycle operations."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from memoryhub.memory_lifecycle import (
    execute_memory_management, memory_lifecycle_rows, RELATIVE_DB,
)
from memoryhub.memory_hub import execute_memory_hub, render_memory_hub_context


class MemoryLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="memoryhub-lifecycle-fixtures-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        (self.base / "data/logs").mkdir(parents=True)
        self.context = self.source("私は駅で青いかばんを忘れた。", "source_1")

    def source(self, content, mid, *, stamp="2026-11-20T01:00:00+09:00", role="user", **extra):
        day = datetime.fromisoformat(stamp).astimezone(timezone(timedelta(hours=9))).strftime("%Y%m%d")
        row = {"message_id": mid, "time": stamp, "role": role, "content": content,
               "turn_id": "turn_" + mid, "trace_id": "trace_" + mid, **extra}
        with (self.base / f"data/logs/chat_{day}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return {"user_message": content, "message_id": mid, "timestamp": stamp,
                "turn_id": row["turn_id"], "trace_id": row["trace_id"], "actor": "assistant", "is_test": False}

    def args(self, **extra):
        return {"action": "remember", "memory_key": "station_bag", "title": "駅で忘れたかばん",
                "quote": "私は駅で青いかばんを忘れた。", "subject": "user", **extra}

    def call(self, arguments, *, context=None, policy=None):
        return execute_memory_management(self.base, arguments,
                                         turn_context=self.context if context is None else context, policy=policy)

    def create(self, **extra):
        result = self.call(self.args(**extra))
        self.assertTrue(result["ok"], result)
        return result

    def mutate(self, receipt, action, **extra):
        return self.call({"action": action, "memory_id": receipt["memory_id"],
                          "expected_revision": receipt["revision"], **extra})

    def inspect(self, receipt):
        return self.call({"action": "inspect", "memory_id": receipt["memory_id"]})

    def test_empty_reads_do_not_create_store(self):
        self.assertTrue(self.call({"action": "list"})["ok"])
        self.assertEqual(memory_lifecycle_rows(self.base), ([], []))
        self.assertFalse((self.base / RELATIVE_DB).exists())

    def test_exact_retention_reaches_hub_without_current_fact_promotion(self):
        external = self.base / "data/external/current.json"
        external.parent.mkdir(parents=True)
        external.write_text('{"user_profile":{"bag":"current canonical unaffected"}}')
        before = external.read_bytes()
        result = self.create()
        self.assertEqual(result["status"], "active")
        self.assertTrue(result["mutation_applied"])
        self.assertFalse(result["canonical_current_fact_changed"])
        self.assertEqual(external.read_bytes(), before)
        rows, errors = memory_lifecycle_rows(self.base)
        self.assertFalse(errors)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["speaker"], "user")
        self.assertEqual(rows[0]["event_time"], self.context["timestamp"])
        self.assertEqual(rows[0]["source_authority"], "retained_user_statement_not_current_fact")
        searched = execute_memory_hub(base_dir=self.base, action="search", query="青いかばん", scope="episodic")
        retained = [x for x in searched["results"] if x["memory_id"].startswith("hub:lm:")]
        self.assertEqual(len(retained), 1, searched)
        exact = execute_memory_hub(base_dir=self.base, action="inspect", memory_id=retained[0]["memory_id"])
        self.assertEqual(exact["status"], "FOUND", exact)
        self.assertIn("fact_authority=none", render_memory_hub_context(exact))
        self.assertIn("revision=1", exact["results"][0]["text"])
        semantic = execute_memory_hub(base_dir=self.base, action="search", query="青いかばん", scope="semantic")
        self.assertFalse(any(x["memory_id"].startswith("hub:lm:") for x in semantic["results"]))

    def test_inference_stays_candidate_and_requires_separate_accept_permission(self):
        result = self.create(source_kind="assistant_interpretation", statement="かばんの話が本人には大切なのかもしれない。")
        self.assertEqual(result["status"], "candidate")
        self.assertEqual(memory_lifecycle_rows(self.base)[0], [])
        refused = self.mutate(result, "accept")
        self.assertEqual(refused["status"], "INFERENCE_ACCEPTANCE_NOT_ALLOWED")
        accepted = self.call({"action": "accept", "memory_id": result["memory_id"], "expected_revision": 1},
                             policy={"permissions": {"memory.manage.accept_inference": "allow"}})
        self.assertTrue(accepted["ok"], accepted)
        row = memory_lifecycle_rows(self.base)[0][0]
        self.assertEqual(row["memory_type"], "reflective")
        self.assertEqual(row["source_authority"], "subjective_non_fact")
        self.assertEqual(row["speaker"], "assistant")
        self.assertIn("user_source=msg:source_1", row["primary_text"])

    def test_propose_defer_accept_revision_lifecycle(self):
        candidate = self.create(action="propose")
        self.assertEqual(candidate["status"], "candidate")
        deferred = self.mutate(candidate, "defer", reason="あとで文脈を見直す")
        self.assertEqual(deferred["status"], "deferred")
        self.assertEqual(deferred["revision"], 2)
        self.assertEqual(memory_lifecycle_rows(self.base)[0], [])
        active = self.mutate(deferred, "accept")
        self.assertEqual(active["status"], "active")
        self.assertEqual(active["revision"], 3)
        stale = self.mutate(candidate, "defer")
        self.assertEqual(stale["status"], "REVISION_CONFLICT")
        self.assertFalse(stale["mutation_applied"])

    def test_correction_preserves_id_and_hides_previous_version_from_hub(self):
        before = self.create()
        context = self.source("さっきのかばんは青ではなく赤だった。", "source_2")
        changed = self.call({"action": "correct", "memory_id": before["memory_id"], "expected_revision": 1,
                             "quote": context["user_message"]}, context=context)
        self.assertTrue(changed["ok"], changed)
        self.assertEqual(changed["memory_id"], before["memory_id"])
        self.assertEqual(changed["revision"], 2)
        rows, _ = memory_lifecycle_rows(self.base)
        self.assertNotIn("青いかばん", rows[0]["primary_text"])
        self.assertIn("赤だった", rows[0]["primary_text"])
        old_mutation = self.mutate(before, "forget")
        self.assertEqual(old_mutation["status"], "REVISION_CONFLICT")
        self.assertEqual(self.inspect(changed)["record"]["revision"], 2)

    def test_merge_keeps_source_quotes_and_supersedes_inputs(self):
        first = self.create()
        context = self.source("かばんは駅の窓口に届けられていた。", "source_2")
        second = self.call(self.args(memory_key="station_bag_found", quote=context["user_message"]), context=context)
        self.assertTrue(second["ok"], second)
        merged = self.mutate(first, "merge", merge_targets=[{"memory_id": second["memory_id"], "revision": 1}])
        self.assertTrue(merged["ok"], merged)
        self.assertEqual(len(merged["record"]["sources"]), 2)
        self.assertEqual(self.inspect(second)["record"]["status"], "superseded")
        self.assertNotIn("statement", self.inspect(second)["record"])
        rows, _ = memory_lifecycle_rows(self.base)
        self.assertEqual(len(rows), 1)
        self.assertIn("青いかばん", rows[0]["primary_text"])
        self.assertIn("窓口", rows[0]["primary_text"])

    def test_merge_revision_failure_is_atomic(self):
        first = self.create()
        second = self.create(memory_key="another_bag")
        third = self.create(memory_key="third_bag")
        result = self.mutate(first, "merge", merge_targets=[
            {"memory_id": second["memory_id"], "revision": 1},
            {"memory_id": third["memory_id"], "revision": 99},
        ])
        self.assertEqual(result["status"], "REVISION_CONFLICT")
        for old in (first, second, third):
            current = self.inspect(old)["record"]
            self.assertEqual(current["status"], "active")
            self.assertEqual(current["revision"], 1)

    def test_merge_never_conflates_subjects(self):
        first = self.create()
        other = self.create(memory_key="friends_bag", subject="third_party", subject_name="友人")
        result = self.mutate(first, "merge", merge_targets=[{"memory_id": other["memory_id"], "revision": 1}])
        self.assertEqual(result["status"], "MERGE_DOMAIN_MISMATCH")
        self.assertEqual(len(memory_lifecycle_rows(self.base)[0]), 2)

    def test_forget_suppresses_inspect_search_and_old_success_replay_body(self):
        created = self.create()
        deleted = self.mutate(created, "forget", reason="保持対象から外す")
        self.assertTrue(deleted["ok"], deleted)
        self.assertEqual(deleted["status"], "forgotten")
        self.assertEqual(deleted["forget_scope"], "retained_memory_retrieval_only")
        self.assertEqual(memory_lifecycle_rows(self.base)[0], [])
        self.assertNotIn("statement", self.inspect(created)["record"])
        self.assertNotIn("sources", self.inspect(created)["record"])
        replay = self.call(self.args())
        self.assertTrue(replay["replayed"])
        self.assertFalse(replay["mutation_applied"])
        self.assertEqual(replay["status"], "forgotten")
        self.assertNotIn("青いかばん", json.dumps(replay, ensure_ascii=False))
        self.assertTrue((self.base / "data/logs/chat_20261120.jsonl").exists())
        self.assertEqual(self.call({"action": "list"})["records"], [])

    def test_source_change_or_deletion_cannot_resurface_retained_content(self):
        self.create()
        path = self.base / "data/logs/chat_20261120.jsonl"
        path.write_text(path.read_text().replace("青い", "緑の"))
        rows, errors = memory_lifecycle_rows(self.base)
        self.assertEqual(rows, [])
        self.assertTrue(any("SOURCE_UNAVAILABLE" in e for e in errors))
        path.unlink()
        self.assertEqual(memory_lifecycle_rows(self.base)[0], [])

    def test_new_quarantine_reaches_retained_memory(self):
        self.create()
        manifest = self.base / "data/memory/dialogue_quarantine.json"
        manifest.write_text(json.dumps({"incidents": [{"incident_id": "test-case", "message_ids": ["source_1"]}]}))
        self.assertEqual(memory_lifecycle_rows(self.base)[0], [])
        self.assertEqual(self.call({"action": "list"})["records"], [])

    def test_derived_quarantine_source_line_is_also_enforced(self):
        self.create()
        manifest = self.base / "data/memory/dialogue_quarantine.json"
        manifest.write_text(json.dumps({"incidents": [{"incident_id": "line-case", "derived_source_prefixes": [
            {"source_file": "data/logs/chat_20261120.jsonl", "source_locator_prefix": "line:1"}]}]}))
        self.assertEqual(memory_lifecycle_rows(self.base)[0], [])

    def test_source_quote_and_user_statement_cannot_be_fabricated(self):
        for fields, status in [
            ({"quote": "私はバスで傘をなくした。"}, "QUOTE_NOT_IN_SOURCE"),
            ({"statement": "私はバスで傘をなくした。"}, "USER_STATEMENT_MUST_BE_EXACT_QUOTE"),
            ({"source_message_id": "missing", "source_date": "2026-11-20"}, "SOURCE_UNAVAILABLE"),
            ({"source_date": "2026-11-20"}, "SOURCE_MESSAGE_ID_REQUIRED"),
        ]:
            result = self.call(self.args(**fields))
            self.assertEqual(result["status"], status, result)
            self.assertFalse(result["mutation_applied"])

    def test_historical_exact_user_source_and_assistant_refusal(self):
        historical = self.source("友人が傘を忘れた。", "old_user", stamp="2026-11-19T22:00:00+09:00")
        retained = self.call(self.args(memory_key="friend_umbrella", quote=historical["user_message"],
                                      source_message_id="old_user", source_date="2026-11-19", subject="third_party"))
        self.assertTrue(retained["ok"], retained)
        self.assertEqual(retained["record"]["sources"][0]["message_id"], "old_user")
        self.source("あなたは傘を忘れた。", "old_assistant", role="assistant")
        refused = self.call(self.args(memory_key="fake_umbrella", quote="あなたは傘を忘れた。",
                                     source_message_id="old_assistant", source_date="2026-11-20"))
        self.assertEqual(refused["status"], "USER_SOURCE_REQUIRED")

    def test_current_log_attachment_suffix_keeps_raw_source_boundary(self):
        path = self.base / "data/logs/chat_20261120.jsonl"
        row = json.loads(path.read_text())
        row["content"] += "\n[attachment: synthetic.png]"
        path.write_text(json.dumps(row, ensure_ascii=False)+"\n")
        self.assertTrue(self.call(self.args())["ok"])
        refused = self.call(self.args(memory_key="attachment", quote="synthetic.png"))
        self.assertEqual(refused["status"], "CURRENT_SOURCE_MISMATCH")

    def test_current_log_midnight_boundary_uses_actual_durable_source_day(self):
        context = self.source("零時の出来事。", "midnight", stamp="2026-11-21T00:00:00+09:00")
        context["timestamp"] = "2026-11-20T23:59:59+09:00"
        result = self.call(self.args(memory_key="midnight", quote="零時の出来事。"), context=context)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["record"]["sources"][0]["source_date"], "2026-11-21")

    def test_public_and_explicit_denied_permissions_do_not_mutate_or_expose(self):
        created = self.create()
        for context in ({**self.context, "mode": "PUBLIC"}, {**self.context, "is_public": True}):
            self.assertEqual(self.call(self.args(memory_key="public"), context=context)["status"], "PRIVATE_ONLY")
            self.assertEqual(self.call({"action": "inspect", "memory_id": created["memory_id"]}, context=context)["status"], "PRIVATE_ONLY")
        denied = {"permissions": {"memory.manage.write": "deny", "memory.manage.read": "deny"}}
        self.assertEqual(self.call(self.args(memory_key="denied"), policy=denied)["status"], "PERMISSION_DENIED")
        self.assertEqual(self.call({"action": "list"}, policy=denied)["status"], "PERMISSION_DENIED")

    def test_concurrent_retry_has_one_commit_receipt(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.call(self.args()), range(2)))
        self.assertTrue(all(r["ok"] for r in results), results)
        self.assertEqual(results[0]["memory_id"], results[1]["memory_id"])
        self.assertEqual(sum(r["mutation_applied"] for r in results), 1)
        self.assertEqual(sum(bool(r.get("replayed")) for r in results), 1)
        db = sqlite3.connect(self.base / RELATIVE_DB)
        self.addCleanup(db.close)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM memories").fetchone()[0], 1)

    def test_key_collision_requires_explicit_revision_bound_update(self):
        first = self.create()
        context = self.source("かばんは見つかった。", "source_2")
        result = self.call(self.args(quote=context["user_message"]), context=context)
        self.assertEqual(result["status"], "KEY_CONFLICT_USE_EXISTING_ID")
        self.assertEqual(result["detail"], first["memory_id"])

    def test_unknown_fields_and_revision_types_rejected(self):
        result = self.call(self.args(approval=True))
        self.assertEqual(result["status"], "UNKNOWN_ARGUMENT")
        created = self.create()
        for revision in (None, True, "1", 0):
            result = self.call({"action": "forget", "memory_id": created["memory_id"], "expected_revision": revision})
            self.assertEqual(result["status"], "EXPECTED_REVISION_REQUIRED", result)
        self.assertEqual(self.inspect(created)["record"]["status"], "active")

    def test_test_and_quarantined_dialogue_cannot_be_promoted(self):
        context = self.source("本番には混ざらない実験。", "test_source", is_test=True)
        result = self.call(self.args(memory_key="test", quote=context["user_message"]), context=context)
        self.assertEqual(result["status"], "SOURCE_UNAVAILABLE")
        manifest = self.base / "data/memory/dialogue_quarantine.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps({"incidents": [{"message_ids": ["source_1"]}]}))
        result = self.call(self.args())
        self.assertEqual(result["status"], "TURN_NOT_ELIGIBLE")

    def test_corrupt_store_is_unavailable_not_empty(self):
        path = self.base / RELATIVE_DB
        path.parent.mkdir(parents=True)
        path.write_bytes(b"not a SQLite database")
        result = self.call({"action": "list"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "STORE_UNAVAILABLE")
        self.assertEqual(memory_lifecycle_rows(self.base), ([], ["memory_lifecycle:STORE_UNAVAILABLE"]))

    def test_read_list_cursor_does_not_hide_later_pages(self):
        for i in range(3):
            self.create(memory_key=f"bag_{i}")
        first = self.call({"action": "list", "limit": 2})
        self.assertEqual(len(first["records"]), 2)
        self.assertFalse(first["mutation_applied"])
        self.assertEqual(first["next_offset"], 2)
        second = self.call({"action": "list", "limit": 2, "offset": first["next_offset"]})
        self.assertEqual(len(second["records"]), 1)
        self.assertIsNone(second["next_offset"])

    def test_source_coordinate_and_timestamp_revision_invalidate_projection(self):
        self.create()
        path = self.base / "data/logs/chat_20261120.jsonl"
        original = path.read_text()
        path.write_text("{}\n" + original)
        self.assertEqual(memory_lifecycle_rows(self.base)[0], [])
        path.write_text(original.replace("01:00:00", "01:05:00"))
        self.assertEqual(memory_lifecycle_rows(self.base)[0], [])

    def test_turn_provenance_cannot_create_memory_from_other_valid_source(self):
        result = self.call(self.args(), context={**self.context, "is_test": True})
        self.assertEqual(result["status"], "TURN_NOT_ELIGIBLE")
        self.assertFalse((self.base / RELATIVE_DB).exists())

    def test_duplicate_current_id_across_dates_is_ambiguous(self):
        self.source(self.context["user_message"], "source_1", stamp="2026-11-19T23:59:59+09:00")
        result = self.call(self.args())
        self.assertEqual(result["status"], "SOURCE_UNAVAILABLE")
        self.assertEqual(result["detail"], "SOURCE_AMBIGUOUS")

    def test_existing_operator_permission_file_is_respected_by_direct_api(self):
        config = self.base / "data/config"
        config.mkdir(parents=True)
        path = config / "capability_policy.json"
        path.write_text(json.dumps({"permissions": {"memory.manage.write": "deny"}}))
        self.assertEqual(self.call(self.args())["status"], "PERMISSION_DENIED")
        path.write_text("{broken")
        self.assertEqual(self.call(self.args())["status"], "INVALID_POLICY")
        self.assertFalse((self.base / RELATIVE_DB).exists())

    def test_forgotten_lifecycle_entry_cannot_reappear_from_phase1_cache(self):
        from memoryhub.memory_sidecar_index import rebuild_memory_index
        result = self.create(title="retained_unique_label")
        rebuild_memory_index(self.base)
        active = execute_memory_hub(base_dir=self.base, action="search", query="retained_unique_label")
        self.assertTrue(any(r["memory_id"].startswith("hub:lm:") for r in active["results"]))
        self.assertTrue(self.mutate(result, "forget")["ok"])
        rebuild_memory_index(self.base)
        forgotten = execute_memory_hub(base_dir=self.base, action="search", query="retained_unique_label")
        self.assertFalse(any(r["memory_id"].startswith("hub:lm:") for r in forgotten["results"]))
        self.assertFalse(any("retained_unique_label" in r["text"] for r in forgotten["results"]))
        # Original conversation is a different source and remains unchanged.
        dialogue = execute_memory_hub(base_dir=self.base, action="search", query="青いかばん")
        self.assertTrue(any(r["source_class"] == "dialogue" for r in dialogue["results"]))

    def test_hub_projection_respects_permission_revocation_and_corrupt_policy(self):
        created = self.create(title="private_lifecycle_label")
        mid = "hub:lm:" + created["memory_id"]
        first = execute_memory_hub(base_dir=self.base, action="inspect", memory_id=mid)
        self.assertEqual(first["status"], "FOUND")
        config = self.base / "data/config"
        config.mkdir(parents=True)
        path = config / "capability_policy.json"
        path.write_text(json.dumps({"permissions": {"memory.manage.read": "deny"}}))
        self.assertEqual(self.call({"action": "list"})["status"], "PERMISSION_DENIED")
        rows, errors = memory_lifecycle_rows(self.base)
        self.assertEqual(rows, [])
        self.assertEqual(errors, ["memory_lifecycle:PERMISSION_DENIED"])
        for action, arguments in (("search", {"query": "private_lifecycle_label"}),
                                  ("inspect", {"memory_id": mid}),
                                  ("inspect", {"memory_id": mid, "expected_sha256": first["results"][0]["record_revision_sha256"]})):
            result = execute_memory_hub(base_dir=self.base, action=action, **arguments)
            self.assertFalse(any(r["memory_id"].startswith("hub:lm:") for r in result["results"]))
            self.assertNotIn("private_lifecycle_label", render_memory_hub_context(result).split("content=", 1)[-1])
        path.write_text("{broken")
        self.assertEqual(memory_lifecycle_rows(self.base), ([], ["memory_lifecycle:STORE_UNAVAILABLE"]))
        broken = execute_memory_hub(base_dir=self.base, action="inspect", memory_id=mid)
        self.assertEqual(broken["status"], "SOURCE_UNAVAILABLE")
        self.assertEqual(broken["results"], [])

    def test_hub_lookup_reads_each_source_day_once(self):
        for i in range(4):
            self.create(memory_key=f"same_day_{i}")
        from memoryhub.memory_lifecycle import _SourceVerifier
        original = _SourceVerifier._read_day
        calls = []
        def tracked(checker, day):
            calls.append(day)
            return original(checker, day)
        with patch.object(_SourceVerifier, "_read_day", tracked):
            rows, errors = memory_lifecycle_rows(self.base)
        self.assertEqual(len(rows), 4)
        self.assertFalse(errors)
        self.assertEqual(calls, ["2026-11-20"])

    def test_existing_hub_read_actions_do_not_create_optional_lifecycle_store(self):
        from memoryhub.memory_sidecar_index import DEFAULT_DB_REL_PATH, rebuild_memory_index

        rebuild_memory_index(self.base)
        # SQLite may create lock sidecars when opening the existing WAL-mode
        # Phase1 index read-only. These are not source or retained-memory writes.
        lock_sidecars = {Path(DEFAULT_DB_REL_PATH + suffix) for suffix in ("-wal", "-shm")}
        before = {p.relative_to(self.base): p.read_bytes()
                  for p in self.base.rglob("*")
                  if p.is_file() and p.relative_to(self.base) not in lock_sidecars}
        search = execute_memory_hub(base_dir=self.base, action="search", query="青いかばん")
        self.assertTrue(search["ok"], search)
        source_id = next(row["memory_id"] for row in search["results"]
                         if row["source_class"] == "dialogue")
        for action, arguments in (
            ("chronology", {"query": "青いかばん"}),
            ("day", {"date": "2026-11-20"}),
            ("inspect", {"memory_id": source_id}),
        ):
            result = execute_memory_hub(base_dir=self.base, action=action, **arguments)
            self.assertTrue(result["ok"], result)
        self.assertFalse((self.base / RELATIVE_DB).exists())
        after = {p.relative_to(self.base): p.read_bytes()
                 for p in self.base.rglob("*")
                 if p.is_file() and p.relative_to(self.base) not in lock_sidecars}
        self.assertEqual(before, after)

    def test_existing_lifecycle_reads_do_not_rewrite_sources_or_database(self):
        created = self.create()
        before = {p.relative_to(self.base): p.read_bytes()
                  for p in self.base.rglob("*") if p.is_file()}
        self.assertTrue(self.call({"action": "list"})["ok"])
        self.assertTrue(self.inspect(created)["ok"])
        self.assertTrue(memory_lifecycle_rows(self.base)[0])
        looked_up = execute_memory_hub(base_dir=self.base, action="inspect",
                                      memory_id="hub:lm:" + created["memory_id"])
        self.assertEqual(looked_up["status"], "FOUND", looked_up)
        after = {p.relative_to(self.base): p.read_bytes()
                 for p in self.base.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_saved_memory_is_readable_from_a_new_python_process(self):
        import memoryhub

        created = self.create()
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(Path(memoryhub.__file__).resolve().parents[1])
        program = (
            "import json,sys; from pathlib import Path; "
            "from memoryhub.memory_hub import execute_memory_hub; "
            "print(json.dumps(execute_memory_hub(base_dir=Path(sys.argv[1]), "
            "action='inspect', memory_id=sys.argv[2]), ensure_ascii=False))"
        )
        process = subprocess.run(
            [sys.executable, "-c", program, str(self.base), "hub:lm:" + created["memory_id"]],
            cwd=self.base, env=environment, capture_output=True, text=True,
            check=True, timeout=15,
        )
        result = json.loads(process.stdout)
        self.assertEqual(result["status"], "FOUND", result)
        self.assertEqual(result["results"][0]["memory_id"], "hub:lm:" + created["memory_id"])
        self.assertIn(self.context["user_message"], result["results"][0]["text"])
        self.assertIn("revision=1", result["results"][0]["text"])

    def test_model_arguments_cannot_supply_trusted_host_context(self):
        for key, value in (
            ("turn_id", "made_up_turn"),
            ("message_id", "source_1"),
            ("timestamp", self.context["timestamp"]),
            ("user_message", self.context["user_message"]),
            ("is_test", False),
            ("actor", "assistant"),
            ("policy", {"permissions": {"memory.manage.write": "allow"}}),
        ):
            with self.subTest(field=key):
                result = self.call(self.args(**{key: value}))
                self.assertEqual(result["status"], "UNKNOWN_ARGUMENT", result)
                self.assertFalse(result["mutation_applied"])
        self.assertFalse((self.base / RELATIVE_DB).exists())

    def test_missing_or_invalid_host_context_cannot_create_a_store(self):
        contexts = [None, {},
                    {**self.context, "message_id": ""},
                    {**self.context, "turn_id": ""},
                    {**self.context, "user_message": ""},
                    {**self.context, "timestamp": "not-a-time"},
                    {**self.context, "timestamp": "2026-11-20T01:00:00"}]
        for context in contexts:
            with self.subTest(context=context):
                result = execute_memory_management(self.base, self.args(), turn_context=context)
                self.assertFalse(result["ok"], result)
                self.assertFalse(result["mutation_applied"])
                self.assertFalse((self.base / RELATIVE_DB).exists())

    def test_source_change_replay_does_not_return_old_body(self):
        created = self.create()
        path = self.base / "data/logs/chat_20261120.jsonl"
        path.write_text(path.read_text().replace("青いかばん", "緑のかばん"))
        replay = self.call(self.args())
        self.assertEqual(replay["status"], "SOURCE_UNAVAILABLE", replay)
        self.assertTrue(replay["replayed"])
        self.assertFalse(replay["mutation_applied"])
        self.assertEqual(replay["memory_id"], created["memory_id"])
        self.assertNotIn("青いかばん", json.dumps(replay, ensure_ascii=False))
        self.assertNotIn("sources", replay.get("record", {}))

    def test_quarantine_replay_does_not_return_old_body(self):
        created = self.create()
        manifest = self.base / "data/memory/dialogue_quarantine.json"
        # Quarantine the source line to reach replay validation. Quarantining
        # this turn's message ID would instead fail the earlier turn guard.
        manifest.write_text(json.dumps({"incidents": [{"derived_source_prefixes": [
            {"source_file": "data/logs/chat_20261120.jsonl", "source_locator_prefix": "line:1"}
        ]}]}))
        replay = self.call(self.args())
        self.assertEqual(replay["status"], "SOURCE_UNAVAILABLE", replay)
        self.assertTrue(replay["replayed"])
        self.assertFalse(replay["mutation_applied"])
        self.assertEqual(replay["memory_id"], created["memory_id"])
        self.assertNotIn("青いかばん", json.dumps(replay, ensure_ascii=False))

    def test_corrected_request_replay_returns_current_revision(self):
        created = self.create()
        context = self.source("かばんは赤だった。", "source_2")
        corrected = self.call(
            {"action": "correct", "memory_id": created["memory_id"], "expected_revision": 1,
             "quote": context["user_message"]}, context=context,
        )
        self.assertTrue(corrected["ok"], corrected)
        replay = self.call(self.args())
        self.assertTrue(replay["ok"], replay)
        self.assertTrue(replay["replayed"])
        self.assertFalse(replay["mutation_applied"])
        self.assertEqual(replay["revision"], 2)
        self.assertIn("赤だった", replay["record"]["statement"])
        self.assertNotIn("青いかばん", json.dumps(replay, ensure_ascii=False))

    def test_original_request_replay_honors_revoked_read_permission(self):
        created = self.create()
        context = self.source("かばんは赤だった。", "source_2")
        corrected = self.call(
            {"action": "correct", "memory_id": created["memory_id"], "expected_revision": 1,
             "quote": context["user_message"]}, context=context,
        )
        self.assertTrue(corrected["ok"], corrected)
        config = self.base / "data/config"
        config.mkdir(parents=True)
        (config / "capability_policy.json").write_text(json.dumps({"permissions": {
            "memory.manage.read": "deny", "memory.manage.write": "allow",
        }}))
        before = (self.base / RELATIVE_DB).read_bytes()
        replay = self.call(self.args())
        self.assertEqual(replay["status"], "PERMISSION_DENIED", replay)
        self.assertFalse(replay["ok"])
        self.assertFalse(replay["mutation_applied"])
        self.assertNotIn("record", replay)
        self.assertNotIn("青いかばん", json.dumps(replay, ensure_ascii=False))
        self.assertNotIn("赤だった", json.dumps(replay, ensure_ascii=False))
        self.assertEqual((self.base / RELATIVE_DB).read_bytes(), before)

    def test_duplicate_merge_target_leaves_every_record_unchanged(self):
        first = self.create()
        other = self.create(memory_key="related_bag")
        result = self.mutate(first, "merge", merge_targets=[
            {"memory_id": other["memory_id"], "revision": 1},
            {"memory_id": other["memory_id"], "revision": 1},
        ])
        self.assertEqual(result["status"], "DUPLICATE_MERGE_TARGET", result)
        self.assertFalse(result["mutation_applied"])
        for receipt in (first, other):
            record = self.inspect(receipt)["record"]
            self.assertEqual(record["revision"], 1)
            self.assertEqual(record["status"], "active")


if __name__ == "__main__":
    unittest.main()
