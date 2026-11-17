# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared provenance/quarantine boundary for model-visible dialogue and derived memory."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

QUARANTINE_REL_PATH = Path("data/memory/dialogue_quarantine.json")


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


@lru_cache(maxsize=16)
def _load_quarantine_cached(path_text: str, mtime_ns: int, size: int) -> dict[str, Any]:
    del mtime_ns, size
    path = Path(path_text)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"incidents": []}
    if not isinstance(data, dict):
        return {"incidents": []}
    incidents = data.get("incidents")
    return {"incidents": incidents if isinstance(incidents, list) else []}


def load_quarantine_manifest(base_dir: Path | str) -> dict[str, Any]:
    path = Path(base_dir) / QUARANTINE_REL_PATH
    try:
        st = path.stat()
    except Exception:
        return {"incidents": []}
    return _load_quarantine_cached(str(path.resolve()), st.st_mtime_ns, st.st_size)


def load_dialogue_quarantine(base_dir: Path | str) -> dict[str, set[str]]:
    result = {"turn_ids": set(), "message_ids": set(), "trace_ids": set()}
    for incident in load_quarantine_manifest(base_dir).get("incidents") or []:
        if not isinstance(incident, dict) or incident.get("active") is False:
            continue
        for key in result:
            values = incident.get(key) or []
            if isinstance(values, list):
                result[key].update(str(v) for v in values if str(v).strip())
    return result


def _active_derived_rules(base_dir: Path | str) -> list[dict[str, str]]:
    rules: list[dict[str, str]] = []
    for incident in load_quarantine_manifest(base_dir).get("incidents") or []:
        if not isinstance(incident, dict) or incident.get("active") is False:
            continue
        for raw in incident.get("derived_source_prefixes") or []:
            if not isinstance(raw, dict):
                continue
            source_file = str(raw.get("source_file") or "").strip()
            locator_prefix = str(raw.get("source_locator_prefix") or "").strip()
            if source_file and locator_prefix:
                rules.append({
                    "source_file": source_file,
                    "source_locator_prefix": locator_prefix,
                    "incident_id": str(incident.get("incident_id") or ""),
                })
    return rules


def _record_metadata(record: dict[str, Any]) -> dict[str, Any]:
    meta = record.get("metadata")
    if isinstance(meta, dict):
        return meta
    raw = record.get("metadata_json")
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
    return {}


def _record_value(record: dict[str, Any], key: str) -> Any:
    value = record.get(key)
    if value not in (None, ""):
        return value
    return _record_metadata(record).get(key)


def dialogue_exclusion_reason(record: dict[str, Any], base_dir: Path | str) -> str | None:
    if _as_bool(_record_value(record, "is_test")):
        return "is_test"
    conversation_actor = str(
        _record_value(record, "conversation_actor")
        or _record_value(record, "actor_context")
        or _record_value(record, "actor")
        or ""
    ).strip()
    if conversation_actor.startswith("test_fixture:"):
        return "test_fixture_actor"
    quarantine = load_dialogue_quarantine(base_dir)
    for key, record_key in (
        ("turn_ids", "turn_id"),
        ("message_ids", "message_id"),
        ("trace_ids", "trace_id"),
    ):
        value = str(_record_value(record, record_key) or "").strip()
        if value and value in quarantine[key]:
            return f"quarantine_{record_key}"
    return None


def memory_record_exclusion_reason(record: dict[str, Any], base_dir: Path | str) -> str | None:
    """Return a quarantine reason for an indexed record; raw source remains untouched."""
    if str(record.get("source_class") or "") == "dialogue":
        reason = dialogue_exclusion_reason(record, base_dir)
        if reason is not None:
            return reason
    source_file = str(record.get("source_file") or "").strip()
    source_locator = str(record.get("source_locator") or "").strip()
    for rule in _active_derived_rules(base_dir):
        if source_file == rule["source_file"] and source_locator.startswith(rule["source_locator_prefix"]):
            incident = rule.get("incident_id") or "unknown"
            return f"quarantine_derived:{incident}"
    return None


def dialogue_record_visible_to_model(record: dict[str, Any], base_dir: Path | str) -> bool:
    return dialogue_exclusion_reason(record, base_dir) is None
