# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Separate record time from described-event time; no semantic event invention.

Calendar words are mechanically resolved only for primary dialogue, relative to
that utterance (not the current clock). Each match remains a candidate tied to
its literal source span, never an asserted event, completed plan, or new memory.
"""
from __future__ import annotations

from datetime import datetime, date, timedelta
import json
import re
from typing import Any

from .memory_temporal_projection import project_memory_event_time
from .temporal_continuity import parse_timestamp

_RELATIVE = {"一昨日": -2, "おととい": -2, "昨日": -1, "きのう": -1,
             "今日": 0, "きょう": 0, "明後日": 2, "あさって": 2,
             "明日": 1, "あした": 1}
_WORDS = re.compile("|".join(sorted(_RELATIVE, key=len, reverse=True)) + "|数日前|先日|さっき|先ほど")
_DATE = re.compile(r"(?<!\d)(\d{4})(?:-|/|年)(\d{1,2})(?:-|/|月)(\d{1,2})(?:日)?(?!\d)")
_CLOCK = re.compile(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)")


def record_time_coordinates(record: dict[str, Any], *, reference_now: datetime | str | None = None) -> dict[str, Any]:
    raw = record.get("recorded_at") or record.get("time") or record.get("event_time")
    primary = (record.get("source_class") == "dialogue"
               or record.get("role") in {"user", "assistant"})
    text = str(record.get("text") or record.get("primary_text") or record.get("content") or "")
    projection = project_memory_event_time(raw, reference_now=reference_now)
    at = parse_timestamp(raw)
    refs = []
    # These are literal coordinates; quoted speech and multiple events can have
    # other anchors. Do not collapse several references into one event timestamp.
    for match in _WORDS.finditer(text[:8000]):
        word = match.group(0)
        offset = _RELATIVE.get(word)
        day = ((at.date() + timedelta(days=offset)).isoformat()
               if primary and at is not None and offset is not None else None)
        refs.append({"literal": word, "span": [match.start(), match.end()],
                     "civil_date_candidate": day, "precision": "day" if day else "unknown",
                     "basis": "utterance_date_candidate" if day else "unresolved",
                     "event_assignment": "not_established"})
        if len(refs) >= 8:
            break
    for match in _DATE.finditer(text[:8000]):
        try:
            day = date(*map(int, match.groups())).isoformat()
        except ValueError:
            continue
        refs.append({"literal": match.group(0), "span": [match.start(), match.end()],
                     "civil_date_candidate": day, "precision": "day",
                     "basis": "explicit_literal", "event_assignment": "not_established"})
        if len(refs) >= 12:
            break
    for match in _CLOCK.finditer(text[:8000]):
        refs.append({"literal": match.group(0), "span": [match.start(), match.end()],
                     "local_clock": f"{int(match[1]):02}:{match[2]}",
                     "civil_date_candidate": None, "precision": "clock_without_date",
                     "basis": "explicit_clock_only", "event_assignment": "not_established"})
        if len(refs) >= 16:
            break
    return {
        "recorded_at": str(raw or ""),
        "record_time_status": projection["status"],
        "record_time_projection": projection,
        "described_event_time": None,
        "event_time_status": "requires_source_interpretation",
        "reference_now": projection["reference_now_jst"],
        "relative_word_anchor": "utterance_timestamp" if primary else "unknown_for_derived_record",
        "time_references": refs,
        "source_file": record.get("source_file", ""),
        "source_locator": record.get("source_locator", ""),
        "speaker": record.get("speaker") or record.get("role") or "unknown",
        "source_verified": bool(record.get("source_verified", False)),
        "raw_records_modified": False,
    }


def render_record_time_coordinates(record: dict[str, Any], *, reference_now: datetime | str | None = None) -> str:
    coords = record_time_coordinates(record, reference_now=reference_now)
    p = coords["record_time_projection"]
    parts = ["Record / Event Time: recorded_at=" + (coords["recorded_at"] or "unknown"),
             "described_event_time=unresolved (recorded_at does not prove occurrence time)"]
    if p["status"] == "known":
        parts.append(f"record_age_calendar_days={p['calendar_offset_days']}")
    parts.append("relative_anchor=" + coords["relative_word_anchor"])
    if coords["time_references"]:
        parts.append("literal_time_candidates=" + json.dumps(
            [{k: v for k, v in item.items() if k not in {"span", "event_assignment"}}
             for item in coords["time_references"]], ensure_ascii=False, separators=(",", ":")))
        parts.append("候補は引用・予定・複数の出来事のいずれか未確定。元発言の意味で照合する")
    return "; ".join(parts)
