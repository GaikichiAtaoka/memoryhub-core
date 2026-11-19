# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Experimental source-backed lifecycle for privately retained dialogue.

The host model chooses which passage to retain, defer, correct, merge or forget.
This module validates provenance, bounds, permissions and atomic revisions. A
retained historical passage or interpretation is not a current-fact authority.
All paths are relative to an explicit, operator-selected workspace root.

SQLite commits the materialized record and an append-only receipt together.
Forget suppresses retrieval from this layer; original dialogue and audit history
remain. Merely importing this module or reading an empty workspace creates no DB.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone, timedelta
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from typing import Any
import uuid

from .dialogue_provenance import dialogue_exclusion_reason, memory_record_exclusion_reason
from .memory_source_inspection import inspect_message_source

SCHEMA = "memory-lifecycle.v1"
RELATIVE_DB = "data/memory/lifecycle.sqlite"
MAX_RECORDS = 5000
MAX_SOURCES = 8
MAX_SOURCE_BYTES_PER_READ = 25_000_000
_ACTIONS = {"remember", "propose", "accept", "defer", "correct", "merge", "forget", "list", "inspect"}
_STATES = {"candidate", "deferred", "active", "superseded", "forgotten"}
_SUBJECTS = {"user", "assistant", "third_party", "shared", "unknown"}
_KINDS = {"user_statement", "assistant_interpretation"}
_PROPERTIES = {
    "action": {"type": "string", "enum": sorted(_ACTIONS)},
    "memory_id": {"type": "string", "description": "Exact lm_ id from list/inspect/receipt; hub:lm:<id> is also accepted."},
    "expected_revision": {"type": "integer", "description": "Required for accept/defer/correct/merge/forget; use the revision returned by list/inspect."},
    "memory_key": {"type": "string", "description": "Stable short key chosen by the assistant for a new memory. Reuse existing id for corrections."},
    "title": {"type": "string", "description": "Short model-authored retrieval label, not a factual assertion."},
    "quote": {"type": "string", "description": "Exact passage of a user source; no paraphrasing. Required for remember/propose/correct."},
    "statement": {"type": "string", "description": "Optional interpretation text ONLY for assistant_interpretation. User statements must equal quote if supplied."},
    "source_kind": {"type": "string", "enum": sorted(_KINDS)},
    "subject": {"type": "string", "enum": sorted(_SUBJECTS), "description": "Whose experience this is; source speaker remains independently recorded."},
    "subject_name": {"type": "string", "description": "Optional explicit subject identity; keep distinct people separate."},
    "confidence": {"type": "number", "description": "The assistant's assessment in 0..1, never proof of a fact."},
    "reason": {"type": "string", "description": "Brief public reason for the chosen lifecycle action, not hidden reasoning."},
    "source_message_id": {"type": "string", "description": "Optional historical source message id, paired with source_date; defaults to current user message."},
    "source_date": {"type": "string", "description": "YYYY-MM-DD of that original dialogue log, required with historical source_message_id."},
    "privacy": {"type": "string", "enum": ["private"]},
    "merge_targets": {"type": "array", "maxItems": 7, "items": {
        "type": "object", "properties": {"memory_id": {"type": "string"}, "revision": {"type": "integer"}},
        "required": ["memory_id", "revision"], "additionalProperties": False,
    }, "description": "Other memory ids and their current revisions to merge into memory_id. Subjects and source kinds must match."},
    "state": {"type": "string", "enum": ["active", "candidate", "deferred", "all"], "description": "Optional list filter (default all). Forgotten content is never returned."},
    "limit": {"type": "integer", "description": "List page size 1..20 (default 10)."},
    "offset": {"type": "integer", "description": "List page offset (default 0)."},
}
MEMORY_LIFECYCLE_TOOL = {
    "type": "function", "function": {
        "name": "memory.manage",
        "description": (
            "Maintain private long-term episodic memory when YOU find a source worth retaining. "
            "remember retains an exact user source passage atomically; propose stores a candidate; "
            "accept/defer/correct/merge/forget operate on exact id+revision. Interpretations stay "
            "non-factual candidates unless separately permitted. Choose list/inspect before changing "
            "an older item. Current profile/policy facts remain in their existing canonical domains. "
            "A remembered statement does not prove that it is still true today. Forget suppresses "
            "this retained memory, not original chat logs. Claim saving only after an ok receipt."
        ),
        "parameters": {"type": "object", "properties": _PROPERTIES,
                       "required": ["action"], "additionalProperties": False},
    },
}


class _Rejected(ValueError):
    def __init__(self, code: str, detail: str = ""):
        self.code, self.detail = code, detail
        super().__init__(detail or code)


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_dump(value).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _string(value: Any, name: str, maximum: int, *, required: bool = False, trim: bool = True) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
        raise _Rejected("INVALID_ARGUMENT", name)
    result = value.strip() if trim else value
    if required and not result.strip():
        raise _Rejected("INVALID_ARGUMENT", name + " is required")
    return result


def _id(value: Any) -> str:
    item = _string(value, "memory_id", 100, required=True)
    if item.startswith("hub:lm:"):
        item = item[7:]
    if not re.fullmatch(r"lm_[a-f0-9]{24}", item):
        raise _Rejected("INVALID_MEMORY_ID")
    return item


def _aware(value: Any, name: str) -> datetime:
    text = _string(value, name, 80, required=True)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _Rejected("INVALID_TIMESTAMP", name) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _Rejected("INVALID_TIMESTAMP", name + " must include timezone")
    return parsed


def _allowed(policy: dict | None, permission: str, default: bool) -> bool:
    if policy is None:
        return default
    if not isinstance(policy, dict) or not isinstance(policy.get("permissions", {}), dict):
        return False
    values = policy.get("permissions", {})
    value = values.get(permission, "allow" if default else "deny")
    return value == "allow"


def _policy(base: Path, supplied: dict | None) -> dict:
    if supplied is not None:
        if not isinstance(supplied, dict) or not isinstance(supplied.get("permissions", {}), dict):
            raise _Rejected("INVALID_POLICY")
        return supplied
    path = base / "data/config/capability_policy.json"
    if not path.exists():
        return {}
    if path.stat().st_size > 1_000_000:
        raise _Rejected("INVALID_POLICY")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise _Rejected("INVALID_POLICY") from exc
    if not isinstance(value, dict) or not isinstance(value.get("permissions", {}), dict):
        raise _Rejected("INVALID_POLICY")
    return value


def _private(context: dict, policy: dict | None) -> bool:
    for source in (context, policy or {}):
        if source.get("public_mode") is True or source.get("is_public") is True:
            return False
        for field in ("mode", "scope", "execution_mode", "audience", "output_mode"):
            if str(source.get(field, "")).lower() in {"public", "broadcast"}:
                return False
    return True


def _path(base_dir: Path, *, write: bool = False) -> Path:
    base = Path(base_dir).resolve()
    path = base / RELATIVE_DB
    # data/memory may be an operator-managed workspace directory.
    # The database itself must never be redirected through a leaf symlink.
    if path.is_symlink():
        raise _Rejected("STORE_PATH_UNSAFE")
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _open_write(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(str(path), timeout=4.0)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA synchronous=FULL")
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version not in (0, 1):
        db.close()
        raise _Rejected("STORE_SCHEMA_UNSUPPORTED")
    db.execute("BEGIN IMMEDIATE")
    db.execute("CREATE TABLE IF NOT EXISTS memories (memory_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, status TEXT NOT NULL, memory_key TEXT NOT NULL, body TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT UNIQUE NOT NULL, operation TEXT NOT NULL, recorded_at TEXT NOT NULL, receipt TEXT NOT NULL)")
    db.execute("CREATE INDEX IF NOT EXISTS memories_key ON memories(memory_key)")
    db.execute("PRAGMA user_version=1")
    return db


def _read_connection(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2.0)
    db.row_factory = sqlite3.Row
    if db.execute("PRAGMA user_version").fetchone()[0] != 1:
        db.close()
        raise _Rejected("STORE_SCHEMA_UNSUPPORTED")
    return db


def _load(db: sqlite3.Connection, memory_id: str) -> dict:
    row = db.execute("SELECT body FROM memories WHERE memory_id=?", (memory_id,)).fetchone()
    if row is None:
        raise _Rejected("NOT_FOUND")
    item = json.loads(row["body"])
    if not isinstance(item, dict) or item.get("memory_id") != memory_id:
        raise _Rejected("STORE_CORRUPT")
    return item


def _revision(item: dict, expected: Any) -> None:
    if type(expected) is not int or expected < 1:
        raise _Rejected("EXPECTED_REVISION_REQUIRED")
    if item["revision"] != expected:
        raise _Rejected("REVISION_CONFLICT", f"current revision={item['revision']}")
    if item["status"] in {"forgotten", "superseded"}:
        raise _Rejected("MEMORY_INACTIVE", item["status"])


def _write(db: sqlite3.Connection, item: dict) -> None:
    db.execute("INSERT INTO memories(memory_id,revision,status,memory_key,body) VALUES (?,?,?,?,?) ON CONFLICT(memory_id) DO UPDATE SET revision=excluded.revision,status=excluded.status,memory_key=excluded.memory_key,body=excluded.body",
               (item["memory_id"], item["revision"], item["status"], item["memory_key"], _dump(item)))


def _source(base: Path, args: dict, context: dict) -> dict:
    historical_id = _string(args.get("source_message_id"), "source_message_id", 150)
    source_date = _string(args.get("source_date"), "source_date", 10)
    current_id = _string(context.get("message_id"), "message_id", 150, required=True)
    if source_date and not historical_id:
        raise _Rejected("SOURCE_MESSAGE_ID_REQUIRED")
    if historical_id:
        if not source_date:
            raise _Rejected("SOURCE_DATE_REQUIRED")
        source_id = historical_id.removeprefix("msg:")
    else:
        source_id = current_id
        stamp = context.get("timestamp") or context.get("time")
        source_date = _aware(stamp, "timestamp").date().isoformat()
    rows, status = inspect_message_source(base, source_date, source_id)
    if not historical_id:
        if status == "SOURCE_AMBIGUOUS":
            raise _Rejected("SOURCE_UNAVAILABLE", status)
        # The host timestamp and save_jsonl timestamp can straddle midnight.
        # Only the exact host-supplied id is searched in the adjacent dates.
        source_day = datetime.fromisoformat(source_date).date()
        matches = [(source_date, rows[0])] if status == "FOUND" and len(rows) == 1 else []
        for delta in (-1, 1):
            alternate = (source_day + timedelta(days=delta)).isoformat()
            more, more_status = inspect_message_source(base, alternate, source_id)
            if more_status == "SOURCE_AMBIGUOUS":
                raise _Rejected("SOURCE_UNAVAILABLE", more_status)
            if more_status == "FOUND" and len(more) == 1:
                matches.append((alternate, more[0]))
        if len(matches) > 1:
            raise _Rejected("SOURCE_UNAVAILABLE", "SOURCE_AMBIGUOUS")
        if matches:
            source_date, row = matches[0]
            rows, status = [row], "FOUND"
    if status != "FOUND" or len(rows) != 1:
        raise _Rejected("SOURCE_UNAVAILABLE", status)
    row = rows[0]
    visibility_probe = {**row, **(row.get("_source_observation") or {})}
    if dialogue_exclusion_reason(visibility_probe, base) or memory_record_exclusion_reason(visibility_probe, base):
        raise _Rejected("SOURCE_UNAVAILABLE", "SOURCE_QUARANTINED")
    if row.get("speaker") != "user":
        raise _Rejected("USER_SOURCE_REQUIRED")
    _aware(row.get("event_time"), "source timestamp")
    quote = _string(args.get("quote"), "quote", 2000, required=True, trim=False)
    if quote not in row["primary_text"]:
        raise _Rejected("QUOTE_NOT_IN_SOURCE")
    if source_id == current_id:
        raw_current = _string(context.get("user_message"), "user_message", 200000, required=True, trim=False)
        if quote not in raw_current:
            raise _Rejected("CURRENT_SOURCE_MISMATCH")
    meta = row.get("_source_observation") or {}
    source = {
        "message_id": source_id, "source_ref": "msg:" + source_id,
        "source_date": source_date, "source_file": row["source_file"],
        "source_locator": row["source_locator"], "timestamp": row["event_time"],
        "role": "user", "quote": quote,
        "source_sha256": row["source_content_sha256"],
        "quote_sha256": hashlib.sha256(quote.encode("utf-8")).hexdigest(),
        "turn_id": meta.get("turn_id", ""), "trace_id": meta.get("trace_id", ""),
        "is_test": bool(meta.get("is_test", False)),
        "actor": meta.get("conversation_actor") or meta.get("actor") or "user",
    }
    return source


class _SourceVerifier:
    """Per-read cache: each bounded original day is read once, not per memory."""
    def __init__(self, base: Path):
        self.base = base
        self.days: dict[str, dict[str, tuple[str, str, str, str] | None] | None] = {}
        self.read_bytes = 0

    def valid(self, source: dict) -> bool:
        if not isinstance(source, dict):
            return False
        probe = {**source, "source_class": "dialogue"}
        if dialogue_exclusion_reason(probe, self.base) or memory_record_exclusion_reason(probe, self.base):
            return False
        date_iso = source.get("source_date", "")
        if date_iso not in self.days:
            self.days[date_iso] = self._read_day(date_iso)
        day = self.days[date_iso]
        found = day.get(source.get("message_id", "")) if day is not None else None
        quote = source.get("quote")
        return bool(found and isinstance(quote, str) and quote
                    and found[0] == source.get("source_sha256") and quote in found[1]
                    and found[2] == source.get("source_locator") and found[3] == source.get("timestamp")
                    and source.get("source_file") == "data/logs/chat_" + date_iso.replace("-", "") + ".jsonl"
                    and hashlib.sha256(quote.encode("utf-8")).hexdigest() == source.get("quote_sha256"))

    def _read_day(self, date_iso: str) -> dict[str, tuple[str, str, str, str] | None] | None:
        if not isinstance(date_iso, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_iso):
            return None
        try:
            datetime.fromisoformat(date_iso)
        except ValueError:
            return None
        relative = "data/logs/chat_" + date_iso.replace("-", "") + ".jsonl"
        path = self.base / relative
        logs = (self.base / "data/logs").resolve()
        try:
            if path.is_symlink() or not path.resolve().is_relative_to(logs):
                return None
            size = path.stat().st_size
            if size > 5_000_000 or self.read_bytes + size > MAX_SOURCE_BYTES_PER_READ:
                return None
            raw = path.read_bytes()
            if len(raw) > 5_000_000 or self.read_bytes + len(raw) > MAX_SOURCE_BYTES_PER_READ:
                return None
            self.read_bytes += len(raw)
            rows: dict[str, tuple[str, str, str, str] | None] = {}
            for line_no, line in enumerate(raw.decode("utf-8").splitlines(), 1):
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict) or row.get("role") != "user":
                    continue
                message_id, text = row.get("message_id"), row.get("content")
                if not isinstance(message_id, str) or not isinstance(text, str):
                    continue
                row.update(source_class="dialogue", source_file=relative, source_locator=f"line:{line_no}")
                if dialogue_exclusion_reason(row, self.base) or memory_record_exclusion_reason(row, self.base):
                    continue
                if message_id in rows:
                    rows[message_id] = None
                else:
                    rows[message_id] = (hashlib.sha256(text.encode("utf-8")).hexdigest(), text,
                                        f"line:{line_no}", str(row.get("time") or ""))
            return rows
        except (OSError, UnicodeError):
            return None


def _eligible_source(base: Path, source: dict, verifier: _SourceVerifier | None = None) -> bool:
    return (verifier or _SourceVerifier(base)).valid(source)


def _item_visible(base: Path, item: dict, verifier: _SourceVerifier | None = None) -> bool:
    if (item.get("privacy") != "private" or item.get("is_test")
            or str(item.get("actor", "")).startswith("test_fixture:")
            or not isinstance(item.get("sources"), list) or not item["sources"]):
        return False
    checker = verifier or _SourceVerifier(base)
    return all(_eligible_source(base, src, checker) for src in item["sources"])


def _view(item: dict, *, source_available: bool | None = None) -> dict:
    fields = ("memory_id", "revision", "status", "memory_key", "title", "subject", "subject_name",
              "source_kind", "statement", "confidence", "privacy", "sources", "created_at", "updated_at",
              "superseded_by", "reason")
    if item["status"] in {"forgotten", "superseded"}:
        fields = ("memory_id", "revision", "status", "superseded_by", "updated_at")
    result = {k: copy.deepcopy(item[k]) for k in fields if k in item}
    result.update(fact_authority="none", instruction_authority="none")
    if source_available is not None:
        result["source_available"] = source_available
    return result


def _new_fields(base: Path, args: dict, context: dict, *, existing: dict | None = None) -> dict:
    existing = existing or {}
    kind = args.get("source_kind", existing.get("source_kind", "user_statement"))
    subject = args.get("subject", existing.get("subject"))
    if kind not in _KINDS or subject not in _SUBJECTS:
        raise _Rejected("INVALID_ARGUMENT", "source_kind or subject")
    source = _source(base, args, context)
    confidence = args.get("confidence", existing.get("confidence", 1.0))
    if type(confidence) not in (int, float) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise _Rejected("INVALID_ARGUMENT", "confidence")
    statement = _string(args.get("statement"), "statement", 2000, trim=False)
    if kind == "user_statement":
        if statement and statement != source["quote"]:
            raise _Rejected("USER_STATEMENT_MUST_BE_EXACT_QUOTE")
        statement = source["quote"]
    elif not statement.strip():
        raise _Rejected("INTERPRETATION_REQUIRED")
    return {
        "memory_key": _string(args.get("memory_key", existing.get("memory_key")), "memory_key", 120, required=True),
        "title": _string(args.get("title", existing.get("title")), "title", 160, required=True),
        "source_kind": kind, "subject": subject,
        "subject_name": _string(args.get("subject_name", existing.get("subject_name", "")), "subject_name", 120),
        "statement": statement, "sources": [source], "confidence": float(confidence),
        "privacy": "private", "reason": _string(args.get("reason"), "reason", 300),
    }


def _record_receipt(db: sqlite3.Connection, request_id: str, operation: str, item: dict,
                    context: dict, *, before: dict | None = None, merged: list[dict] | None = None) -> dict:
    receipt = {
        "schema": SCHEMA, "ok": True, "status": item["status"], "action": operation, "mutation_applied": True,
        "receipt_id": "mlr_" + uuid.uuid4().hex[:24], "request_id": request_id,
        "memory_id": item["memory_id"], "revision": item["revision"],
        "record": _view(item), "recorded_at": _now(),
        "trace_id": context.get("trace_id", ""), "turn_id": context.get("turn_id", ""),
        "before_sha256": _hash(before) if before else None, "after_sha256": _hash(item),
        "merged_ids": [x["memory_id"] for x in (merged or [])],
        "canonical_current_fact_changed": False,
        "raw_conversation_modified": False,
        "forget_scope": "retained_memory_retrieval_only" if operation == "forget" else None,
    }
    db.execute("INSERT INTO events(request_id,operation,recorded_at,receipt) VALUES (?,?,?,?)",
               (request_id, operation, receipt["recorded_at"], _dump(receipt)))
    return receipt


def _read_management(base: Path, args: dict) -> dict:
    path = _path(base)
    action = args["action"]
    limit, offset = args.get("limit", 10), args.get("offset", 0)
    if type(limit) is not int or not 1 <= limit <= 20 or type(offset) is not int or offset < 0:
        raise _Rejected("INVALID_PAGE")
    state = args.get("state", "all")
    if state not in {"all", "candidate", "deferred", "active"}:
        raise _Rejected("INVALID_STATE")
    if not path.exists():
        return {"schema": SCHEMA, "ok": action == "list", "status": "empty" if action == "list" else "NOT_FOUND", "records": [], "mutation_applied": False}
    verifier = _SourceVerifier(base)
    with closing(_read_connection(path)) as db:
        if action == "inspect":
            item = _load(db, _id(args.get("memory_id")))
            available = _item_visible(base, item, verifier)
            if not available and item["status"] not in {"forgotten", "superseded"}:
                return {"schema": SCHEMA, "ok": False, "status": "SOURCE_UNAVAILABLE",
                        "memory_id": item["memory_id"], "revision": item["revision"], "mutation_applied": False}
            return {"schema": SCHEMA, "ok": True, "status": item["status"],
                    "record": _view(item, source_available=available), "mutation_applied": False}
        # Read at most one bounded page plus the following record. The caller
        # gets a cursor even when source visibility removes a row from this page.
        params: tuple = () if state == "all" else (state,)
        where = "status IN ('candidate','deferred','active')" if state == "all" else "status=?"
        rows = db.execute(f"SELECT body FROM memories WHERE {where} ORDER BY rowid LIMIT ? OFFSET ?", (*params, limit+1, offset)).fetchall()
        records = []
        unavailable = 0
        for row in rows[:limit]:
            item = json.loads(row["body"])
            if not _item_visible(base, item, verifier):
                unavailable += 1
                continue
            records.append(_view(item, source_available=True))
        return {"schema": SCHEMA, "ok": True, "status": "listed", "records": records,
                "source_unavailable_count": unavailable,
                "next_offset": offset + limit if len(rows) > limit else None, "mutation_applied": False}


def execute_memory_management(base_dir: Path, arguments: dict, *, turn_context: dict,
                              policy: dict | None = None) -> dict:
    """Execute one mechanically validated private lifecycle request.

    Trusted context fields: user_message, message_id, timestamp/time (aware ISO),
    turn_id, trace_id, is_test and actor. They must come from the host turn, never
    model arguments. The host must persist the source before invoking a write;
    current and historical sources resolve to the existing daily dialogue logs.

    A supplied policy applies to this call. Shared policy for management and Hub
    reads belongs in <base_dir>/data/config/capability_policy.json. Retained data
    is private-only; this does not encrypt the operator-controlled workspace.
    """
    action = arguments.get("action") if isinstance(arguments, dict) else None
    try:
        if not isinstance(arguments, dict) or action not in _ACTIONS:
            raise _Rejected("INVALID_ACTION")
        if set(arguments) - set(_PROPERTIES):
            raise _Rejected("UNKNOWN_ARGUMENT")
        if (not isinstance(turn_context, dict) or (policy is not None and not isinstance(policy, dict))
                or not _private(turn_context, policy)):
            raise _Rejected("PRIVATE_ONLY")
        if arguments.get("privacy", "private") != "private":
            raise _Rejected("PRIVATE_ONLY")
        base = Path(base_dir).resolve()
        policy = _policy(base, policy)
        if not _private(turn_context, policy):
            raise _Rejected("PRIVATE_ONLY")
        if action in {"list", "inspect"}:
            if not _allowed(policy, "memory.manage.read", True):
                raise _Rejected("PERMISSION_DENIED")
            return _read_management(base, arguments)
        if not _allowed(policy, "memory.manage.write", True):
            raise _Rejected("PERMISSION_DENIED")
        if dialogue_exclusion_reason(turn_context, base):
            raise _Rejected("TURN_NOT_ELIGIBLE")
        # Context identity, not model-authored approval flags, binds receipts.
        turn_id = _string(turn_context.get("turn_id"), "turn_id", 200, required=True)
        message_id = _string(turn_context.get("message_id"), "message_id", 150, required=True)
        _string(turn_context.get("user_message"), "user_message", 200000, required=True, trim=False)
        _aware(turn_context.get("timestamp") or turn_context.get("time"), "timestamp")
        request_id = _hash({"turn_id": turn_id, "message_id": message_id, "arguments": arguments})
        path = _path(base, write=True)
        with closing(_open_write(path)) as db:
            try:
                old = db.execute("SELECT receipt FROM events WHERE request_id=?", (request_id,)).fetchone()
                if old:
                    # A retry reads existing state rather than applying a new
                    # mutation. A later read revocation also covers this path.
                    if not _allowed(policy, "memory.manage.read", True):
                        db.rollback()
                        return {"schema": SCHEMA, "ok": False, "status": "PERMISSION_DENIED",
                                "action": action, "replayed": True, "mutation_applied": False,
                                "canonical_current_fact_changed": False,
                                "raw_conversation_modified": False}
                    prior = json.loads(old["receipt"])
                    # Never return an old active body after a later forget or
                    # correction, even if a transport retry replays its request.
                    current = _load(db, prior["memory_id"])
                    inactive = current["status"] in {"forgotten", "superseded"}
                    source_available = inactive or _item_visible(base, current)
                    # Source revocation/change also applies to retries; never
                    # expose the immutable old receipt's quote through replay.
                    view = (_view(current) if source_available else {
                        "memory_id": current["memory_id"],
                        "revision": current["revision"],
                        "status": current["status"],
                        "source_available": False,
                    })
                    prior.update(replayed=True, mutation_applied=False, record=view,
                                 original_status=prior["status"],
                                 ok=source_available,
                                 status=current["status"] if source_available else "SOURCE_UNAVAILABLE",
                                 revision=current["revision"],
                                 current_status=current["status"], current_revision=current["revision"])
                    db.rollback()
                    return prior
                before = None
                merged = []
                if action in {"remember", "propose"}:
                    fields = _new_fields(base, arguments, turn_context)
                    collisions = db.execute("SELECT body FROM memories WHERE memory_key=?", (fields["memory_key"],)).fetchall()
                    for row in collisions:
                        other = json.loads(row["body"])
                        if other["subject"] == fields["subject"] and other.get("subject_name", "") == fields["subject_name"]:
                            raise _Rejected("KEY_CONFLICT_USE_EXISTING_ID", other["memory_id"])
                    if db.execute("SELECT COUNT(*) FROM memories").fetchone()[0] >= MAX_RECORDS:
                        raise _Rejected("STORE_CAPACITY_REACHED")
                    status = "active" if action == "remember" and fields["source_kind"] == "user_statement" else "candidate"
                    item = {"schema": SCHEMA, "memory_id": "lm_" + uuid.uuid4().hex[:24],
                            "revision": 1, "status": status, **fields,
                            "created_at": _now(), "updated_at": _now(),
                            "is_test": bool(turn_context.get("is_test", False)),
                            "actor": str(turn_context.get("actor") or "assistant")}
                else:
                    item = _load(db, _id(arguments.get("memory_id")))
                    _revision(item, arguments.get("expected_revision"))
                    before = copy.deepcopy(item)
                    if action != "forget" and not _item_visible(base, item):
                        raise _Rejected("SOURCE_UNAVAILABLE")
                    if action == "accept":
                        if item["status"] not in {"candidate", "deferred"}:
                            raise _Rejected("INVALID_TRANSITION", "accept requires candidate/deferred")
                        if item["source_kind"] != "user_statement" and not _allowed(policy, "memory.manage.accept_inference", False):
                            raise _Rejected("INFERENCE_ACCEPTANCE_NOT_ALLOWED")
                        item["status"] = "active"
                    elif action == "defer":
                        item["status"] = "deferred"
                    elif action == "correct":
                        fields = _new_fields(base, arguments, turn_context, existing=item)
                        if fields["memory_key"] != item["memory_key"]:
                            raise _Rejected("CORRECTION_KEY_CHANGE_DENIED")
                        others = db.execute("SELECT body FROM memories WHERE memory_key=? AND memory_id<>?", (fields["memory_key"], item["memory_id"])).fetchall()
                        for row in others:
                            other = json.loads(row["body"])
                            if other["subject"] == fields["subject"] and other.get("subject_name", "") == fields["subject_name"]:
                                raise _Rejected("KEY_CONFLICT_USE_EXISTING_ID", other["memory_id"])
                        item.update(fields)
                        item["status"] = "active" if fields["source_kind"] == "user_statement" else "candidate"
                    elif action == "forget":
                        item["status"] = "forgotten"
                    elif action == "merge":
                        raw_targets = arguments.get("merge_targets")
                        if not isinstance(raw_targets, list) or not 1 <= len(raw_targets) <= MAX_SOURCES-1:
                            raise _Rejected("INVALID_MERGE_TARGETS")
                        seen = {item["memory_id"]}
                        sources = list(item["sources"])
                        for target in raw_targets:
                            if not isinstance(target, dict) or set(target) != {"memory_id", "revision"}:
                                raise _Rejected("INVALID_MERGE_TARGETS")
                            target_id = _id(target["memory_id"])
                            if target_id in seen:
                                raise _Rejected("DUPLICATE_MERGE_TARGET")
                            seen.add(target_id)
                            other = _load(db, target_id)
                            _revision(other, target["revision"])
                            if not _item_visible(base, other):
                                raise _Rejected("SOURCE_UNAVAILABLE")
                            for key in ("subject", "subject_name", "source_kind", "status"):
                                if other.get(key) != item.get(key):
                                    raise _Rejected("MERGE_DOMAIN_MISMATCH", key)
                            if item["source_kind"] != "user_statement":
                                raise _Rejected("MERGE_REQUIRES_USER_STATEMENTS")
                            for source in other["sources"]:
                                if _hash(source) not in {_hash(x) for x in sources}:
                                    sources.append(source)
                            merged.append(other)
                        if len(sources) > MAX_SOURCES:
                            raise _Rejected("MERGE_SOURCE_LIMIT")
                        item["sources"] = sources
                        item["statement"] = "\n".join(src["quote"] for src in sources)
                        if "title" in arguments:
                            item["title"] = _string(arguments["title"], "title", 160, required=True)
                        for other in merged:
                            other.update(status="superseded", superseded_by=item["memory_id"],
                                         revision=other["revision"]+1, updated_at=_now())
                            _write(db, other)
                    item["revision"] += 1
                    item["updated_at"] = _now()
                    item["reason"] = _string(arguments.get("reason"), "reason", 300)
                _write(db, item)
                receipt = _record_receipt(db, request_id, action, item, turn_context, before=before, merged=merged)
                db.commit()
                return receipt
            except Exception:
                db.rollback()
                raise
    except _Rejected as exc:
        return {"schema": SCHEMA, "ok": False, "status": exc.code, "action": action,
                "detail": exc.detail, "canonical_current_fact_changed": False,
                "raw_conversation_modified": False, "mutation_applied": False}
    except (sqlite3.Error, OSError, ValueError, TypeError, KeyError) as exc:
        return {"schema": SCHEMA, "ok": False, "status": "STORE_UNAVAILABLE", "action": action,
                "error_type": type(exc).__name__, "canonical_current_fact_changed": False,
                "raw_conversation_modified": False, "mutation_applied": False}


def memory_lifecycle_rows(base_dir: Path, *, memory_id: str = "") -> tuple[list[dict], list[str]]:
    """Read-only MemoryHub projection of active source-valid retained memories.

    Missing storage is an empty connected source. Broken/unavailable sources are
    explicit incompleteness; no database is created or rebuilt by a lookup.
    Existing storage uses the workspace capability policy and original sources
    on every read, so a later revocation/change also reaches exact-ID inspection.
    """
    base = Path(base_dir).resolve()
    rows, errors = [], []
    verifier = _SourceVerifier(base)
    try:
        path = _path(base)
        if not path.exists():
            return [], []
        policy = _policy(base, None)
        if not _private({}, policy) or not _allowed(policy, "memory.manage.read", True):
            return [], ["memory_lifecycle:PERMISSION_DENIED"]
        with closing(_read_connection(path)) as db:
            if memory_id:
                requested = _id(memory_id)
                raw = db.execute("SELECT body FROM memories WHERE memory_id=? AND status='active'", (requested,)).fetchall()
            else:
                raw = db.execute("SELECT body FROM memories WHERE status='active' ORDER BY rowid LIMIT ?", (MAX_RECORDS+1,)).fetchall()
            if len(raw) > MAX_RECORDS:
                errors.append("memory_lifecycle:CAPACITY_BOUND")
            for entry in raw[:MAX_RECORDS]:
                item = json.loads(entry["body"])
                if not _item_visible(base, item, verifier):
                    errors.append("memory_lifecycle:SOURCE_UNAVAILABLE:" + str(item.get("memory_id", "")))
                    continue
                sources = item["sources"]
                first = sources[0]
                # Labels remain metadata; only exact source quotes are treated
                # as the user's historical words. Interpretations are explicit.
                identity = f"memory_id={item['memory_id']}; revision={item['revision']}; subject={item['subject']}; subject_name={item['subject_name']}"
                chunks = [identity, "retained_at=" + item["updated_at"] + "; fact_authority=none; current_truth_not_established"]
                if item["source_kind"] == "assistant_interpretation":
                    chunks.append("assistant_interpretation=" + item["statement"])
                for source in sources:
                    chunks.append(f"user_source={source['source_ref']}; uttered_at={source['timestamp']}; quote={source['quote']}")
                rows.append({
                    "memory_id": "hub:lm:" + item["memory_id"],
                    "memory_type": "episodic" if item["source_kind"] == "user_statement" else "reflective",
                    "source_class": "retained_user_statement" if item["source_kind"] == "user_statement" else "retained_interpretation",
                    "source_file": RELATIVE_DB,
                    "source_locator": f"memory:{item['memory_id']}:revision:{item['revision']}",
                    "event_time": first["timestamp"], "recorded_at": first["timestamp"],
                    "speaker": "user" if item["source_kind"] == "user_statement" else "assistant",
                    "title": item["title"], "primary_text": "\n".join(chunks),
                    "source_verified": True,
                    "source_authority": "retained_user_statement_not_current_fact" if item["source_kind"] == "user_statement" else "subjective_non_fact",
                    "evidence_level": "exact_source_backed_retention_not_current_fact",
                    "source_refs": [f"{s['source_file']}#{s['source_locator']}" for s in sources],
                    "message_id": first["message_id"], "turn_id": first["turn_id"], "trace_id": first["trace_id"],
                    "is_test": item.get("is_test", False), "actor": item.get("actor", "assistant"),
                    "_source_observation": {"message_id": first["message_id"], "turn_id": first["turn_id"],
                                            "trace_id": first["trace_id"], "is_test": item.get("is_test", False)},
                })
    except (_Rejected, sqlite3.Error, OSError, ValueError, TypeError, KeyError):
        errors.append("memory_lifecycle:STORE_UNAVAILABLE")
    return rows, errors


__all__ = ["MEMORY_LIFECYCLE_TOOL", "execute_memory_management", "memory_lifecycle_rows"]
