#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Offline, invented demonstration of source-backed memory management.

All messages and tool choices below are scripted fixtures. No model or network
is used. By default the entire workspace is temporary. --base creates a new
directory and refuses an existing path so it cannot overwrite another workspace.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any

from memoryhub import execute_memory_hub, index_memory_sources
from memoryhub.memory_lifecycle import execute_memory_management, memory_lifecycle_rows


DAY = "2026-01-15"


def run_demo(base: Path) -> dict[str, Any]:
    """Run only inside a new, empty directory selected by main()."""
    if any(base.iterdir()):
        raise ValueError("The lifecycle demo requires an empty directory.")
    log = base / "data" / "logs" / "chat_20260115.jsonl"
    log.parent.mkdir(parents=True)
    steps: list[dict[str, Any]] = []

    def source(number: int, text: str) -> dict[str, Any]:
        # These are ordinary-format synthetic rows in an isolated fixture
        # workspace, allowing the normal source-verification path to be tried.
        event = {
            "role": "user", "message_id": f"demo-user-{number}",
            "time": f"{DAY}T10:{number:02d}:00+00:00", "content": text,
            "turn_id": f"demo-turn-{number}", "trace_id": f"demo-trace-{number}",
        }
        with log.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")
            stream.flush()
        return {
            "user_message": event["content"], "message_id": event["message_id"],
            "timestamp": event["time"], "turn_id": event["turn_id"],
            "trace_id": event["trace_id"], "actor": "assistant",
        }

    def manage(label: str, arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        receipt = execute_memory_management(base, arguments, turn_context=context)
        if not receipt.get("ok"):
            raise RuntimeError(f"{label}: {receipt.get('status')}: {receipt.get('detail', '')}")
        record = receipt.get("record", {})
        steps.append({
            "step": label, "action": arguments["action"], "status": receipt["status"],
            "memory_id": receipt.get("memory_id", record.get("memory_id")),
            "revision": receipt.get("revision", record.get("revision")),
            "mutation_applied": receipt.get("mutation_applied", False),
            "statement": record.get("statement"),
            "source_ids": [s["message_id"] for s in record.get("sources", [])],
        })
        return receipt

    first = source(1, "The blue notebook is in the left drawer.")
    remember_args = {
        "action": "remember", "memory_key": "blue-notebook-location",
        "title": "Blue notebook location", "quote": first["user_message"],
        "subject": "user", "source_kind": "user_statement",
    }
    remembered = manage("remember", remember_args, first)
    memory_id = remembered["memory_id"]

    sync = index_memory_sources(base, incremental=True)
    if not sync.get("ok"):
        raise RuntimeError(f"index: {sync.get('status')}")
    found = execute_memory_hub(
        base_dir=base, action="search", scope="episodic", query="blue notebook", limit=15,
    )
    retained = [r for r in found.get("results", []) if r["memory_id"] == "hub:lm:" + memory_id]
    if not found.get("ok") or not retained:
        raise RuntimeError("The retained memory did not appear in the Hub search.")
    steps.append({
        "step": "retrieve_through_hub", "action": "search", "status": found["status"],
        "memory_id": retained[0]["memory_id"], "retained_results": len(retained),
    })

    interpretation = manage("propose_interpretation", {
        "action": "propose", "memory_key": "notebook-interpretation",
        "title": "Possible meaning of the notebook request", "quote": first["user_message"],
        "source_kind": "assistant_interpretation", "subject": "user",
        "statement": "The notebook location may be useful to revisit later.",
    }, first)
    deferred = manage("defer_interpretation", {
        "action": "defer", "memory_id": interpretation["memory_id"],
        "expected_revision": interpretation["revision"], "reason": "Keep this interpretation pending.",
    }, first)

    correction = source(2, "Correction: the blue notebook is in the right drawer.")
    corrected = manage("correct", {
        "action": "correct", "memory_id": memory_id,
        "expected_revision": remembered["revision"], "quote": correction["user_message"],
    }, correction)
    corrected_rows, corrected_errors = memory_lifecycle_rows(base, memory_id=memory_id)
    corrected_text = "\n".join(r["primary_text"] for r in corrected_rows)
    if corrected_errors or "right drawer" not in corrected_text or "left drawer" in corrected_text:
        raise RuntimeError("The retained current version did not reflect the correction.")

    detail = source(3, "The blue notebook has a yellow ribbon.")
    candidate = manage("propose_detail", {
        "action": "propose", "memory_key": "blue-notebook-ribbon",
        "title": "Blue notebook ribbon", "quote": detail["user_message"],
        "subject": "user", "source_kind": "user_statement",
    }, detail)
    accepted = manage("accept_detail", {
        "action": "accept", "memory_id": candidate["memory_id"],
        "expected_revision": candidate["revision"],
    }, detail)
    merged = manage("merge", {
        "action": "merge", "memory_id": memory_id,
        "expected_revision": corrected["revision"],
        "merge_targets": [{"memory_id": accepted["memory_id"], "revision": accepted["revision"]}],
        "title": "Blue notebook: location and ribbon",
    }, detail)
    if len(merged["record"]["sources"]) != 2:
        raise RuntimeError("The merged memory did not retain both source references.")

    original_log_hash = hashlib.sha256(log.read_bytes()).hexdigest()
    forgotten = manage("forget_retained_memory", {
        "action": "forget", "memory_id": memory_id,
        "expected_revision": merged["revision"], "reason": "Stop reusing this retained memory.",
    }, detail)
    inspected = manage("inspect_forgotten", {"action": "inspect", "memory_id": memory_id}, detail)
    replay = manage("replay_original_request", remember_args, first)
    rows_after_forget, errors_after_forget = memory_lifecycle_rows(base)
    after_forget = execute_memory_hub(
        base_dir=base, action="inspect", memory_id="hub:lm:" + memory_id,
    )

    # Reindexing the unchanged source log demonstrates the boundary of forget:
    # original dialogue remains a separate searchable source.
    sync = index_memory_sources(base, incremental=True)
    if not sync.get("ok"):
        raise RuntimeError(f"final index: {sync.get('status')}")
    original = execute_memory_hub(
        base_dir=base, action="inspect", memory_id="msg:demo-user-1", date=DAY,
    )
    checks = {
        "retained_memory_was_searchable": bool(retained),
        "interpretation_is_deferred": deferred["status"] == "deferred",
        "correction_kept_same_id": corrected["memory_id"] == memory_id,
        "correction_advanced_revision": corrected["revision"] > remembered["revision"],
        "merged_two_sources": len(merged["record"]["sources"]) == 2,
        "forget_scope_is_retained_layer": forgotten["forget_scope"] == "retained_memory_retrieval_only",
        "no_active_retained_rows_after_forget": not rows_after_forget and not errors_after_forget,
        "forgotten_inspect_has_no_body": "statement" not in inspected["record"] and "sources" not in inspected["record"],
        "old_request_replay_has_no_body": replay.get("replayed") is True and replay["status"] == "forgotten" and "statement" not in replay["record"],
        "retained_hub_inspect_has_no_body": not after_forget.get("results"),
        "original_log_unchanged_by_forget": hashlib.sha256(log.read_bytes()).hexdigest() == original_log_hash,
        "original_message_still_retrievable": original.get("status") == "FOUND" and bool(original.get("results")),
    }
    if not all(checks.values()):
        raise RuntimeError("Demo checks failed: " + json.dumps(checks, sort_keys=True))
    return {
        "fixture": "invented_scripted_actions", "live_model": False,
        "notice": "Invented messages and predefined API actions; this is not a model-quality result.",
        "state_path": str(base / "data/memory/lifecycle.sqlite"), "steps": steps, "checks": checks,
        "forget_boundary": "Only retained-memory reuse is suppressed. Original dialogue and prior audit receipts remain.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, help="New directory for this invented demo; existing paths are refused.")
    args = parser.parse_args(argv)
    try:
        if args.base is None:
            with tempfile.TemporaryDirectory(prefix="memoryhub-phase2-demo-") as temporary:
                report = run_demo(Path(temporary))
                report["workspace_lifetime"] = "temporary; removed when this command exits"
                print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            base = args.base.expanduser().resolve()
            base.mkdir(parents=True, exist_ok=False)
            report = run_demo(base)
            report["workspace_lifetime"] = "preserved at the explicitly selected new directory"
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        parser.exit(1, f"Lifecycle demo failed: {type(exc).__name__}: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
