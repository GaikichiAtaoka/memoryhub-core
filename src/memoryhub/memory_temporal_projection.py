# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mechanical JST temporal coordinates for *retrieved* OSL memory.

The actor remains the agent. This module does not decide whether to search, which
memory matters, what an event means, or how to phrase a personal reflection.
It annotates a retrieved event's verifiable clock relation to this chat turn.

No mutation of original event timestamps, memory index or canonical records.
"""
from __future__ import annotations

import re
from datetime import datetime, date
from typing import Any

from .temporal_continuity import JST, daypart, daypart_ja, parse_timestamp


def _reference_jst(reference: datetime | str | None = None) -> datetime:
    if reference is None:
        return datetime.now(JST)
    parsed = parse_timestamp(reference)
    if parsed is None:
        raise ValueError("reference_now must be a valid ISO datetime")
    return parsed


def _calendar_label(delta: int) -> str:
    if delta == 0:
        return "今日"
    if delta == 1:
        return "昨日"
    if delta == 2:
        return "一昨日"
    if delta > 2:
        return f"{delta}暦日前"
    if delta == -1:
        return "明日"
    if delta == -2:
        return "明後日"
    return f"{-delta}暦日後"


def project_memory_event_time(
    event_time: Any,
    *,
    reference_now: datetime | str | None = None,
) -> dict[str, Any]:
    """Return mechanics only; never assume missing event times or sleep times.

    An event timestamp without timezone uses OSL's documented JST assumption
    and marks that assumption. A date-only timestamp carries no clock/daypart.
    """
    ref = _reference_jst(reference_now)
    raw = str(event_time or "").strip()
    common = {"reference_now_jst": ref.isoformat(timespec="seconds"), "timezone": "Asia/Tokyo"}
    if not raw or raw in {"unknown", "unknown_time", "None"}:
        return {**common, "status": "unknown_event_time",
                "source_event_time": raw or "", "calendar_label_ja": "不明"}

    date_only = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw))
    if date_only:
        try:
            event_date = date.fromisoformat(raw)
        except ValueError:
            return {**common, "status": "invalid_event_time",
                    "source_event_time": raw, "calendar_label_ja": "不明"}
        clock_at = None
        time_basis = "date_only"
    else:
        clock_at = parse_timestamp(raw)
        if clock_at is None:
            return {**common, "status": "invalid_event_time",
                    "source_event_time": raw, "calendar_label_ja": "不明"}
        event_date = clock_at.date()
        has_timezone = bool(re.search(r"(?:Z|[+-]\d{2}:\d{2})$", raw))
        time_basis = "explicit_timezone" if has_timezone else "naive_assumed_jst"

    delta = (ref.date() - event_date).days
    result = {
        **common,
        "status": "known",
        "source_event_time": raw,
        "event_civil_date_jst": event_date.isoformat(),
        "calendar_offset_days": delta,
        "calendar_label_ja": _calendar_label(delta),
        "timestamp_basis": time_basis,
        "clock_known": clock_at is not None,
    }
    if clock_at is not None:
        result["event_at_jst"] = clock_at.isoformat(timespec="seconds")
        result["event_daypart"] = daypart(clock_at)
        result["event_daypart_ja"] = daypart_ja(clock_at)
        result["elapsed_minutes"] = round((ref - clock_at).total_seconds() / 60)
    return result


def render_memory_temporal_projection(
    event_time: Any, *, reference_now: datetime | str | None = None,
) -> str:
    obj = project_memory_event_time(event_time, reference_now=reference_now)
    if obj["status"] != "known":
        return (
            f"Time Projection (JST): status={obj['status']}; "
            "日時不明のため『昨日』『昨夜』『一昨日』等の相対日付を根拠なく決めない"
        )
    label = obj["calendar_label_ja"]
    delta = obj["calendar_offset_days"]
    line = (
        f"Time Projection (JST): reference_now={obj['reference_now_jst']}; "
        f"event_civil_date={obj['event_civil_date_jst']}; "
        f"calendar_offset_days={delta}; calendar_label={label}; "
        f"time_basis={obj['timestamp_basis']}"
    )
    if obj["clock_known"]:
        line += (
            f"; event_at={obj['event_at_jst']}; "
            f"daypart={obj['event_daypart']}（{obj['event_daypart_ja']}）; "
            f"elapsed_minutes={obj['elapsed_minutes']}"
        )
    else:
        line += "; daypart=unknown"
    return line
