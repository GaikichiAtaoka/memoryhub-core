# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Exact, bounded, read-only original dialogue lookup for temporal rechecking."""
from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path
import re

from .dialogue_provenance import dialogue_record_visible_to_model
from .memory_source_observation import source_visibility_metadata


def inspect_message_source(base_dir: Path, date_iso: str, message_id: str) -> tuple[list[dict], str]:
    """Only date-scoped daily dialogue files, never model-supplied paths.

    Duplicate IDs, quarantined records, oversized sources and invalid dates fail
    explicitly. Files, memory index, canonical stores and policies are unchanged.
    """
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(date_iso)):
        return [], "INVALID_DATE"
    try:
        date.fromisoformat(date_iso)
    except ValueError:
        return [], "INVALID_DATE"
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,150}", str(message_id)):
        return [], "INVALID_MESSAGE_ID"
    base = Path(base_dir).resolve()
    relative = "data/logs/chat_" + date_iso.replace("-", "") + ".jsonl"
    path = base / relative
    if not path.exists():
        return [], "NOT_FOUND"
    # data/logs is an existing trusted runtime-store symlink in production.
    # Resolve that store once; never allow a model-controlled file/path escape.
    logs_root = (base / "data/logs").resolve()
    if path.is_symlink() or not path.resolve().is_relative_to(logs_root):
        return [], "SOURCE_UNAVAILABLE"
    try:
        if not path.is_file() or path.stat().st_size > 5_000_000:
            return [], "SOURCE_TOO_LARGE"
        matches = []
        with path.open(encoding="utf-8", errors="strict") as stream:
            for line_no, line in enumerate(stream, 1):
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict) or rec.get("message_id") != message_id:
                    continue
                if (not dialogue_record_visible_to_model(rec, base)
                        or rec.get("role") not in {"user", "assistant"}):
                    continue
                matches.append((line_no, rec))
                if len(matches) > 1:
                    return [], "SOURCE_AMBIGUOUS"
    except (OSError, UnicodeError):
        return [], "SOURCE_UNAVAILABLE"
    if not matches:
        return [], "NOT_FOUND"
    line_no, record = matches[0]
    text = str(record.get("content") or "")
    if not text:
        return [], "SOURCE_EMPTY"
    role = record["role"]
    row = {
        "memory_id": "msg:" + message_id, "message_id": message_id,
        "memory_type": "episodic", "source_class": "dialogue",
        "source_file": relative, "source_locator": f"line:{line_no}",
        "event_time": record.get("time"), "recorded_at": record.get("time"),
        "speaker": role, "title": "exact_original_dialogue",
        "primary_text": text, "source_verified": True,
        "source_content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "source_text_truncated": False,  # the delivery layer applies explicit windows
        "source_authority": ("primary_user_statement" if role == "user"
                             else "historical_assistant_utterance_not_verified_fact"),
        "evidence_level": "exact_primary_source_read",
        "source_refs": [f"{relative}#line:{line_no}"],
        "index_status": "original_source_not_index",
        "_source_observation": source_visibility_metadata(record),
    }
    return [row], "FOUND"
