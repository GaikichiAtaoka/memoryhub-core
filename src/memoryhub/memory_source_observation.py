# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""PRIVATE, optional observation of MemoryHub results; never an inference actor.

Captures the exact retrieved page and its rendering/transport status for a turn.
It does not alter prompts, rerun searches, expose thinking, rank evidence, or
claim which candidate caused a sentence. Source snapshots are NOT new memory.
"""
from __future__ import annotations
from contextlib import contextmanager
from contextvars import ContextVar
import copy
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from typing import Any

from .dialogue_provenance import dialogue_exclusion_reason, memory_record_exclusion_reason

SCHEMA = "private-memory-sources.v1"
MAX_EVENTS = 32
MAX_RECORDS = 15
MAX_TEXT = 1600
MAX_DOCUMENT_BYTES = 4_000_000
JST = timezone(timedelta(hours=9))
_ACTIVE: ContextVar[Any] = ContextVar("memoryhub_source_observer", default=None)
_TYPES = {
    "dialogue": "対話ログ", "USER_EXPLICIT": "内省（発言の振り返り）",
    "AGENT_INTERPRETATION": "内省（エージェントの解釈）", "SYSTEM_FACT": "内省（システム記録）",
    "UNRESOLVED": "内省（未解決の問い）", "growth": "内省・成長記録",
    "summary": "会話要約", "experience": "経験記録",
    "canonical_current": "現在の保存情報", "world": "世界状態の記録",
}
_REFLECTIVE = {"USER_EXPLICIT", "AGENT_INTERPRETATION", "SYSTEM_FACT", "UNRESOLVED"}


def _now() -> str:
    return datetime.now(JST).isoformat(timespec="milliseconds")


def validate_trace_id(trace_id: str) -> str:
    if not isinstance(trace_id, str) or not re.fullmatch(r"trace_[A-Za-z0-9_-]{1,100}", trace_id):
        raise ValueError("Invalid source observation trace ID")
    return trace_id


def _text(value: Any, maximum: int = 256) -> str:
    return str(value)[:maximum] if value is not None else ""


def _relative_source(value: Any) -> str:
    text = _text(value, 500)
    path = PurePosixPath(text)
    if not text or path.is_absolute() or ".." in path.parts or "\\" in text:
        return "source_path_unavailable"
    return text


def _refs(value: Any) -> list[str]:
    values = [value] if isinstance(value, str) else value if isinstance(value, list) else []
    return [_text(x, 256) for x in values[:32] if isinstance(x, str)]


def source_visibility_metadata(record: dict) -> dict:
    """Carry existing exclusion identities without interpreting record meaning."""
    metadata = record.get("metadata")
    if not isinstance(metadata, dict):
        try:
            metadata = json.loads(record.get("metadata_json") or "{}")
        except (ValueError, TypeError):
            metadata = {}
    metadata = metadata if isinstance(metadata, dict) else {}
    keys = ("is_test", "conversation_actor", "actor", "message_id", "turn_id", "trace_id")
    return {key: record.get(key, metadata.get(key)) for key in keys
            if record.get(key, metadata.get(key)) is not None}


def _visible(record: dict, base_dir: Path) -> bool:
    probe = {**record, "metadata": record.get("_visibility") or {}}
    return (dialogue_exclusion_reason(probe, base_dir) is None
            and memory_record_exclusion_reason(probe, base_dir) is None)


def _source_snapshot(row: dict, slot: int, body_rendered: bool) -> dict:
    source_class = _text(row.get("source_class"), 80)
    reflected = source_class in _REFLECTIVE or str(row.get("source_file", "")).endswith("introspection_structured.jsonl")
    private = row.get("_source_observation") or {}
    origin = private.get("origin") if isinstance(private, dict) else None
    origin = origin if isinstance(origin, dict) else {}
    text = str(row.get("text") or "")
    snapshot = {
        "slot": slot, "memory_id": _text(row.get("memory_id"), 200),
        "source_class": source_class, "kind": "reflection" if reflected else source_class,
        "kind_label": _TYPES.get(source_class, "記憶（種類不明）"),
        "source_file": _relative_source(row.get("source_file")),
        "source_locator": _text(row.get("source_locator"), 256),
        "recorded_at": _text(row.get("recorded_at") or row.get("event_time"), 80),
        "recorded_at_label": "内省の作成日時" if reflected else "発言日時" if source_class == "dialogue" else "記録日時",
        "event_time": None, "event_time_status": "not_established",
        "speaker": _text(row.get("speaker"), 50),
        "excerpt": text[:MAX_TEXT], "excerpt_chars": min(len(text), MAX_TEXT),
        "range_start": row.get("range_start") if type(row.get("range_start")) is int else None,
        "range_end": row.get("range_end") if type(row.get("range_end")) is int else None,
        "total_chars": row.get("source_total_chars") if type(row.get("source_total_chars")) is int else None,
        "partial": bool(row.get("source_text_truncated")) or len(text) > MAX_TEXT,
        "record_revision_sha256": _text(row.get("record_revision_sha256"), 64),
        "source_read_verified": bool(row.get("source_verified")),
        "body_in_rendered_evidence": bool(body_rendered),
        "source_refs": _refs(row.get("source_refs")),
        "origin_available": reflected and bool(origin.get("window_path")) and bool(_refs(origin.get("refs"))),
        "_origin": {"window_path": _text(origin.get("window_path"), 1000),
                    "reflection_id": _text(origin.get("reflection_id"), 150),
                    "refs": _refs(origin.get("refs"))} if reflected else {},
        "_visibility": {k: private.get(k) for k in ("is_test", "conversation_actor", "actor", "message_id", "turn_id", "trace_id")
                        if isinstance(private, dict) and private.get(k) is not None},
    }
    return snapshot


class MemorySourceCapture:
    def __init__(self, base_dir: Path, is_test: bool):
        self.base_dir = Path(base_dir)
        self.is_test = bool(is_test)
        self.started_at = _now()
        self.events: list[dict] = []
        self.omitted_events = 0
        self.capture_errors = 0
        self.transport_count = 0
        self._rendered: list[str] = []  # ephemeral only, never persisted

    def record(self, result: dict, rendered: str, rendered_ids: list[str]) -> None:
        if len(self.events) >= MAX_EVENTS:
            self.omitted_events += 1
            return
        rows = result.get("results") or []
        rows = rows if isinstance(rows, list) else []
        event = {
            "ordinal": len(self.events) + 1, "observed_at": _now(),
            "operation": _text(result.get("action"), 40), "scope": _text(result.get("scope"), 40),
            "query": _text(result.get("query"), 4096),
            "query_truncated": len(str(result.get("query") or "")) > 4096,
            "requested_memory_id": _text(result.get("memory_id"), 200),
            "requested_day": _text(result.get("date"), 40),
            "status": _text(result.get("status"), 100), "ok": bool(result.get("ok")),
            "returned_count": len(rows), "sources": [], "withheld_sources": 0,
            "omitted_sources": max(0, len(rows) - MAX_RECORDS),
            "rendered_sha256": hashlib.sha256(rendered.encode()).hexdigest(),
            "transport_calls": [], "transport_inclusion": "not_observed",
        }
        for slot, row in enumerate(rows[:MAX_RECORDS]):
            if not isinstance(row, dict):
                event["withheld_sources"] += 1
                continue
            snapshot = _source_snapshot(row, slot, row.get("memory_id") in rendered_ids)
            if not _visible(snapshot, self.base_dir):
                event["withheld_sources"] += 1
                continue
            event["sources"].append(snapshot)
        self.events.append(event)
        self._rendered.append(rendered)

    def transport(self, messages: list[dict]) -> None:
        self.transport_count += 1
        # Only actual system/tool evidence content, never thinking or answers.
        bodies = [str(x.get("content") or "") for x in messages if x.get("role") in {"system", "tool"}]
        for event, rendered in zip(self.events, self._rendered):
            if rendered and any(rendered in content for content in bodies):
                event["transport_inclusion"] = "submitted_with_evidence"
                event["transport_calls"].append(self.transport_count)

    def document(self, trace_id: str) -> dict:
        return {
            "schema": SCHEMA, "trace_id": validate_trace_id(trace_id), "is_test": self.is_test,
            "state": "partial" if self.omitted_events or self.capture_errors else "recorded",
            "started_at": self.started_at, "finished_at": _now(),
            "scope": "PRIVATE MemoryHub retrieval observations only",
            "reasoning_or_sentence_attribution": False,
            "events": copy.deepcopy(self.events), "omitted_events": self.omitted_events,
            "capture_errors": self.capture_errors, "transport_call_count": self.transport_count,
        }


@contextmanager
def capture_memory_sources(base_dir: Path, *, is_test: bool = False):
    capture = MemorySourceCapture(base_dir, is_test)
    token = _ACTIVE.set(capture)
    try:
        yield capture
    finally:
        _ACTIVE.reset(token)


def observe_memory_render(result: dict, rendered: str, rendered_ids: list[str]) -> None:
    capture = _ACTIVE.get()
    if capture is None:
        return
    try:
        capture.record(result, rendered, rendered_ids)
    except Exception:
        capture.capture_errors += 1  # observation failure must not replace a reply


def observe_memory_transport(messages: list[dict]) -> None:
    capture = _ACTIVE.get()
    if capture is None:
        return
    try:
        capture.transport(messages)
    except Exception:
        capture.capture_errors += 1


def _directory(base_dir: Path) -> Path:
    return Path(base_dir) / "data/trace/memory_sources"


def save_memory_sources(capture: MemorySourceCapture, trace_id: str) -> dict:
    doc = capture.document(trace_id)
    raw = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode()
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise ValueError("Source observation exceeds storage bound")
    folder = _directory(capture.base_dir)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = folder / (trace_id + ".json")
    fd, temporary = tempfile.mkstemp(prefix=".source-", dir=folder)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        # Atomic exclusive publication: never rewrite a turn's historical evidence.
        os.link(temporary, target)
    finally:
        os.unlink(temporary)
    return {"schema": SCHEMA, "state": doc["state"], "event_count": len(doc["events"])}


def _load(base_dir: Path, trace_id: str, *, is_test: bool = False) -> dict | None:
    validate_trace_id(trace_id)
    target = _directory(base_dir) / (trace_id + ".json")
    if not target.exists():
        return None
    if target.is_symlink() or target.stat().st_size > MAX_DOCUMENT_BYTES:
        raise ValueError("Invalid source observation file")
    doc = json.loads(target.read_text(encoding="utf-8"))
    if (not isinstance(doc, dict) or doc.get("schema") != SCHEMA or doc.get("trace_id") != trace_id):
        raise ValueError("Invalid source observation document")
    if bool(doc.get("is_test")) != bool(is_test):
        raise PermissionError("Source observation scope mismatch")
    if dialogue_exclusion_reason({"trace_id": trace_id}, base_dir):
        raise PermissionError("Source observation is quarantined")
    return doc


def read_memory_sources(base_dir: Path, trace_id: str, *, is_test: bool = False) -> dict:
    doc = _load(base_dir, trace_id, is_test=is_test)
    if doc is None:
        return {"schema": SCHEMA, "trace_id": trace_id, "state": "not_recorded", "events": [],
                "note": "この返答の情報源は記録されていません。導入前の返答などを再検索して補完はしません。"}
    view = {k: v for k, v in doc.items() if k != "is_test"}
    view["note"] = "取得した記憶と参照順の記録です。実際の思考や、回答の各文で採用した根拠の断定ではありません。"
    view["context_note"] = "会話履歴や人格・内部状態の全情報は含みません。追加取得なしは、記憶を使っていないという意味ではありません。"
    events = []
    for event in doc.get("events") or []:
        clean = {k: v for k, v in event.items() if k != "sources"}
        clean["sources"] = []
        for record in event.get("sources") or []:
            if not _visible(record, Path(base_dir)):
                clean["withheld_sources"] = clean.get("withheld_sources", 0) + 1
                continue
            source = {k: v for k, v in record.items() if not k.startswith("_")}
            source["body_submitted_to_model"] = bool(record.get("body_in_rendered_evidence")) and event.get("transport_inclusion") == "submitted_with_evidence"
            clean["sources"].append(source)
        events.append(clean)
    view["events"] = events
    return view


def read_memory_origin(base_dir: Path, trace_id: str, ordinal: int, slot: int, *, is_test: bool = False) -> dict:
    if type(ordinal) is not int or type(slot) is not int or ordinal < 1 or slot < 0:
        raise ValueError("Invalid source coordinates")
    doc = _load(base_dir, trace_id, is_test=is_test)
    if doc is None:
        return {"state": "not_recorded", "records": []}
    event = next((x for x in doc.get("events", []) if x.get("ordinal") == ordinal), None)
    row = next((x for x in (event or {}).get("sources", []) if x.get("slot") == slot), None)
    if row is None or not _visible(row, Path(base_dir)):
        raise PermissionError("Source is not available")
    from memory_source_origin import resolve_display_origin
    return resolve_display_origin(Path(base_dir), row.get("_origin") or {})
