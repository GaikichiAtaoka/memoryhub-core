# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""User-opened inspection of explicit reflection references, not model recall.

No semantic search or guessed yesterday mapping. A deleted reflection window is
unavailable. Results are checked at UI-open time, never labeled model-read data.
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import re
from .dialogue_provenance import dialogue_exclusion_reason, memory_record_exclusion_reason


def resolve_display_origin(base_dir: Path, origin: dict) -> dict:
    response = {"state": "unresolved", "records": [],
                "checked_at": datetime.now(timezone(timedelta(hours=9))).isoformat(timespec="seconds"),
                "model_read_proven": False,
                "note": "表示を開いた時点で出典リンクを照合したものです。この返答でモデルが原文を読んだ証明ではありません。"}
    raw = origin.get("window_path")
    refs = origin.get("refs")
    reflection_id = str(origin.get("reflection_id") or "")
    if not raw or not isinstance(raw, str) or not isinstance(refs, list) or not refs:
        return {**response, "state": "no_link"}
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,150}", reflection_id):
        return {**response, "state": "invalid_link"}
    path = Path(raw)
    if ".." in path.parts or "\\" in raw:
        return {**response, "state": "invalid_link"}
    logs = (Path(base_dir) / "data/logs").resolve()
    windows = logs / "reflection_windows"
    path = path if path.is_absolute() else Path(base_dir) / path
    if (path.name != "reflection_" + reflection_id + ".jsonl" or windows.is_symlink()
            or path.is_symlink() or path.resolve().parent != windows.resolve()):
        return {**response, "state": "invalid_link"}
    if not path.is_file():
        return {**response, "state": "source_missing"}
    if path.stat().st_size > 5_000_000:
        return {**response, "state": "source_too_large"}
    try:
        raw_data = path.read_bytes()
        if len(raw_data) > 5_000_000:
            return {**response, "state": "source_too_large"}
        # Do not skip malformed rows and silently shift turn:N coordinates.
        rows = [json.loads(line) for line in raw_data.decode("utf-8").splitlines() if line.strip()]
        if any(not isinstance(row, dict) for row in rows):
            raise ValueError("Invalid window")
    except (OSError, UnicodeError, ValueError):
        return {**response, "state": "source_unreadable"}
    response["window_sha256_at_display"] = hashlib.sha256(raw_data).hexdigest()
    resolved = []
    unresolved = []
    for ref in refs[:16]:
        if not isinstance(ref, str):
            continue
        matches = []
        for ordinal, row in enumerate(rows):
            keys = {str(row.get("source_id") or f"turn:{ordinal}"),
                    str(row.get("message_id") or row.get("_reflection_message_id") or "")}
            if ref in keys:
                matches.append(row)
        if len(matches) != 1:
            unresolved.append(ref)
            continue
        row = matches[0]
        probe = {**row, "message_id": row.get("message_id") or row.get("_reflection_message_id")}
        source_key = str(row.get("_reflection_source_log") or "")
        if Path(source_key).is_absolute():
            try:
                source_key = "data/logs/" + str(Path(source_key).resolve().relative_to(logs))
            except ValueError:
                source_key = ""
        source_line = row.get("_reflection_source_line_number")
        probe.update(source_class="dialogue", source_file=source_key,
                     source_locator=f"line:{source_line}" if type(source_line) is int else "")
        if (row.get("role") not in {"user", "assistant"}
                or dialogue_exclusion_reason(probe, base_dir)
                or memory_record_exclusion_reason(probe, base_dir)):
            unresolved.append(ref)
            continue
        content = str(row.get("content") or "")
        resolved.append({"source_ref": ref, "role": row["role"],
                         "uttered_at": str(row.get("time") or row.get("timestamp") or ""),
                         "excerpt": content[:1600], "partial": len(content) > 1600,
                         "message_id": str(probe.get("message_id") or ""),
                         "source_name": Path(source_key).name if source_key else path.name,
                         "evidence_kind": "explicit_reference_in_reflection_window",
                         "event_time": None})
    response.update(state="resolved" if resolved and not unresolved else "partial" if resolved else "unresolved",
                    records=resolved, unresolved_refs=unresolved, omitted_refs=max(0, len(refs)-16))
    return response
