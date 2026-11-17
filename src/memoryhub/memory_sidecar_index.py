# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SQLite FTS5 Non-Destructive Sidecar Index for local agent Large-Scale Memory (Phase 1).

ABSOLUTE BOUNDARY:
- memory_index.sqlite is NOT Canonical Memory. It is a derived search cache.
- Source of Truth remains the append-only raw stores (growth_log.txt, summary.txt,
  experience_context.jsonl, web_chat_*.jsonl).
- Sidecar index can be deleted or corrupted at any time and deterministically
  rebuilt 100% from raw stores without memory loss.
- Indexing is idempotent; re-indexing identical sources creates 0 duplicates.
- Index or search failure must NEVER be reported as 'memory absence'.
- Does not alter production chat paths, Thin Bridge, or memory writers.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from .dialogue_provenance import dialogue_exclusion_reason, memory_record_exclusion_reason

SCHEMA_VERSION = "1.0"
DEFAULT_DB_REL_PATH = "data/memory/memory_index.sqlite"

# Supported source categories
SOURCE_CLASSES = {"growth", "summary", "experience", "dialogue"}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def normalize_text(text: str) -> str:
    """Normalize text using NFKC and collapse internal whitespace."""
    if not text:
        return ""
    norm = unicodedata.normalize("NFKC", str(text))
    norm = re.sub(r"[\r\n\t]+", " ", norm)
    norm = re.sub(r" +", " ", norm)
    return norm.strip()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def compact_ts(ts: str) -> str:
    cleaned = re.sub(r"[^\d]", "", ts)
    if len(cleaned) >= 14:
        return f"{cleaned[:8]}T{cleaned[8:14]}"
    elif len(cleaned) >= 8:
        return f"{cleaned[:8]}T000000"
    return "00000000T000000"


def default_db_path(base_dir: Union[Path, str]) -> Path:
    return Path(base_dir) / DEFAULT_DB_REL_PATH


def logical_source_path(file_path: Union[Path, str], base_dir: Union[Path, str]) -> str:
    """Compute logical path relative to base_dir without resolving symlinks.

    Preserves lexical subpath (e.g. 'data/logs/chat_20260828.jsonl') even when
    directories like data/logs are symlinks pointing outside base_dir.
    """
    p = Path(file_path)
    base = Path(base_dir)
    try:
        return str(p.relative_to(base))
    except ValueError:
        p_abs = p if p.is_absolute() else (base / p)
        base_abs = base if base.is_absolute() else base.resolve()
        try:
            return str(p_abs.relative_to(base_abs))
        except ValueError:
            return str(p)


# ----------------------------------------------------------------------
# Schema & DB Connection
# ----------------------------------------------------------------------

def get_connection(db_path: Union[Path, str], *, read_only: bool = False) -> sqlite3.Connection:
    path = Path(db_path)
    if read_only:
        from urllib.parse import quote
        con = sqlite3.connect("file:" + quote(str(path.resolve()), safe="/") + "?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        return con
    path.parent.mkdir(parents=True, exist_ok=True)
    con=sqlite3.connect(str(path),timeout=5)
    con.row_factory=sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def init_schema(con: sqlite3.Connection) -> None:
    """Initialize metadata, source tracking, memory_records, and FTS5 tables."""
    cur = con.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS sources (
        source_path TEXT PRIMARY KEY,
        source_class TEXT NOT NULL,
        file_mtime REAL NOT NULL,
        file_size INTEGER NOT NULL,
        file_sha256 TEXT NOT NULL,
        record_count INTEGER NOT NULL,
        indexed_at TEXT NOT NULL
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS memory_records (
        memory_id TEXT PRIMARY KEY,
        source_class TEXT NOT NULL,
        source_file TEXT NOT NULL,
        source_locator TEXT NOT NULL,
        event_time TEXT,
        speaker TEXT,
        title TEXT,
        primary_text TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        metadata_json TEXT,
        indexed_at TEXT NOT NULL
    );
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_records_class ON memory_records(source_class);
    """)
    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_records_event_time ON memory_records(event_time);
    """)
    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_records_source_file ON memory_records(source_file);
    """)

    # FTS5 virtual table with trigram tokenizer indexing title & primary_text
    cur.execute("""
    CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
        memory_id UNINDEXED,
        title,
        primary_text,
        tokenize="trigram"
    );
    """)

    cur.execute("""
    INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?);
    """, (SCHEMA_VERSION,))
    con.commit()


# ----------------------------------------------------------------------
# Deterministic Source Parsers
# ----------------------------------------------------------------------

def parse_growth_log(file_path: Path, base_dir: Path) -> List[Dict[str, Any]]:
    """Parse growth_log.txt into structured memory records.

    Extracts individual growth bullet points and composite growth blocks with
    stable, deterministic IDs.
    """
    if not file_path.exists() or not file_path.is_file():
        return []

    text = file_path.read_text(encoding="utf-8", errors="ignore").strip()
    if not text:
        return []

    rel_file = logical_source_path(file_path, base_dir)

    pattern = re.compile(r"(?m)^\[成長記録\s*-\s*(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}:\d{2})?)\]")
    matches = list(pattern.finditer(text))
    if not matches:
        return []

    records: List[Dict[str, Any]] = []

    for i, m in enumerate(matches):
        ts = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        cts = compact_ts(ts)

        # 1. Parse individual growth bullet points in '1. 昨日との違い・成長点：'
        section_match = re.search(r"(?m)^1\.\s*[^\n]+$", body)
        if section_match:
            after_section = body[section_match.end():]
            next_section = re.search(r"(?m)^\d+\.\s*[^\n]+$", after_section)
            growth_text = after_section[:next_section.start()] if next_section else after_section

            item_idx = 1
            for line in growth_text.splitlines():
                line_str = line.strip()
                if line_str.startswith("・"):
                    statement = line_str[1:].strip()
                    if statement and statement != "該当なし":
                        content_hash = sha256_text(normalize_text(statement))[:12]
                        memory_id = f"mem:growth:{cts}:s1_{item_idx}:{content_hash}"
                        records.append({
                            "memory_id": memory_id,
                            "source_class": "growth",
                            "source_file": rel_file,
                            "source_locator": f"block:{ts}:section:1:item:{item_idx}",
                            "event_time": ts,
                            "speaker": "agent",
                            "title": f"成長記録・変化点 ({ts})",
                            "primary_text": statement,
                            "content_hash": content_hash,
                            "metadata_json": json.dumps({"block_ts": ts, "section": 1, "item_idx": item_idx}, ensure_ascii=False),
                        })
                        item_idx += 1

        # 2. Parse individual items in '2. 新たな気づき・課題：'
        section2_match = re.search(r"(?m)^2\.\s*[^\n]+$", body)
        if section2_match:
            after_section2 = body[section2_match.end():]
            next_section2 = re.search(r"(?m)^\d+\.\s*[^\n]+$", after_section2)
            growth_text2 = after_section2[:next_section2.start()] if next_section2 else after_section2

            item_idx2 = 1
            for line in growth_text2.splitlines():
                line_str = line.strip()
                if line_str.startswith("・"):
                    statement2 = line_str[1:].strip()
                    if statement2 and statement2 != "該当なし":
                        content_hash2 = sha256_text(normalize_text(statement2))[:12]
                        memory_id2 = f"mem:growth:{cts}:s2_{item_idx2}:{content_hash2}"
                        records.append({
                            "memory_id": memory_id2,
                            "source_class": "growth",
                            "source_file": rel_file,
                            "source_locator": f"block:{ts}:section:2:item:{item_idx2}",
                            "event_time": ts,
                            "speaker": "agent",
                            "title": f"成長記録・気づき課題 ({ts})",
                            "primary_text": statement2,
                            "content_hash": content_hash2,
                            "metadata_json": json.dumps({"block_ts": ts, "section": 2, "item_idx": item_idx2}, ensure_ascii=False),
                        })
                        item_idx2 += 1

        # 3. Composite growth block
        composite_body = f"[成長記録 - {ts}]\n{body}"
        block_norm = normalize_text(composite_body)
        if block_norm:
            block_hash = sha256_text(block_norm)[:12]
            block_id = f"mem:growth_block:{cts}:{block_hash}"
            records.append({
                "memory_id": block_id,
                "source_class": "growth",
                "source_file": rel_file,
                "source_locator": f"block:{ts}:composite",
                "event_time": ts,
                "speaker": "agent",
                "title": f"成長記録 ({ts})",
                "primary_text": composite_body,
                "content_hash": block_hash,
                "metadata_json": json.dumps({"block_ts": ts, "type": "composite_block"}, ensure_ascii=False),
            })

    return records


def parse_summary_txt(file_path: Path, base_dir: Path) -> List[Dict[str, Any]]:
    """Parse summary.txt into structured memory records."""
    if not file_path.exists() or not file_path.is_file():
        return []

    text = file_path.read_text(encoding="utf-8", errors="ignore").strip()
    if not text:
        return []

    rel_file = logical_source_path(file_path, base_dir)

    pattern = re.compile(r"(?m)^【更新】\s*(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}(?::\d{2})?)?)")
    matches = list(pattern.finditer(text))
    if not matches:
        return []

    records: List[Dict[str, Any]] = []

    for i, m in enumerate(matches):
        ts = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        cts = compact_ts(ts)

        composite_body = f"【更新】{ts}\n{body}"
        norm_body = normalize_text(composite_body)
        if not norm_body:
            continue

        block_hash = sha256_text(norm_body)[:12]
        memory_id = f"mem:summary:{cts}:{block_hash}"

        records.append({
            "memory_id": memory_id,
            "source_class": "summary",
            "source_file": rel_file,
            "source_locator": f"block:{ts}",
            "event_time": ts,
            "speaker": "system",
            "title": f"対話要約 ({ts})",
            "primary_text": composite_body,
            "content_hash": block_hash,
            "metadata_json": json.dumps({"block_ts": ts}, ensure_ascii=False),
        })

    return records


def parse_experience_jsonl(file_path: Path, base_dir: Path) -> List[Dict[str, Any]]:
    """Parse experience_context.jsonl into structured memory records."""
    if not file_path.exists() or not file_path.is_file():
        return []

    rel_file = logical_source_path(file_path, base_dir)

    records: List[Dict[str, Any]] = []
    lines = file_path.read_text(encoding="utf-8", errors="ignore").splitlines()

    for line_idx, line in enumerate(lines, start=1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue

        if not isinstance(obj, dict):
            continue

        primary_text = str(obj.get("primary_text") or obj.get("text") or obj.get("statement") or "").strip()
        if not primary_text:
            continue

        created_at = str(obj.get("created_at") or "").strip()
        cts = compact_ts(created_at) if created_at else f"line{line_idx}"
        content_hash = sha256_text(normalize_text(primary_text))[:12]

        raw_id = obj.get("experience_id") or obj.get("id")
        if raw_id and isinstance(raw_id, str) and raw_id.strip():
            memory_id = f"mem:exp:{raw_id.strip()}"
        else:
            memory_id = f"mem:exp:{cts}:{content_hash}"

        tags = obj.get("tags") if isinstance(obj.get("tags"), list) else []

        records.append({
            "memory_id": memory_id,
            "source_class": "experience",
            "source_file": rel_file,
            "source_locator": f"line:{line_idx}",
            "event_time": created_at or None,
            "speaker": "agent",
            "title": "一次体験記憶",
            "primary_text": primary_text,
            "content_hash": content_hash,
            "metadata_json": json.dumps({"tags": tags, "raw_id": raw_id}, ensure_ascii=False),
        })

    return records


def parse_dialogue_jsonl(file_path: Path, base_dir: Path) -> List[Dict[str, Any]]:
    """Parse dialogue logs (web_chat_*.jsonl, chat_*.jsonl, web_*.jsonl) into structured memory records."""
    if not file_path.exists() or not file_path.is_file():
        return []

    rel_file = logical_source_path(file_path, base_dir)

    file_stem = file_path.stem
    records: List[Dict[str, Any]] = []

    lines = file_path.read_text(encoding="utf-8", errors="ignore").splitlines()

    for line_idx, line in enumerate(lines, start=1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue

        if not isinstance(obj, dict):
            continue

        exclusion_reason = dialogue_exclusion_reason(obj, base_dir)
        if exclusion_reason is not None:
            continue

        content = str(obj.get("content") or obj.get("text") or obj.get("message") or "").strip()
        if not content:
            continue

        role = str(obj.get("role") or obj.get("speaker") or "unknown").strip().lower()
        if role not in ["user", "assistant", "system", "agent"]:
            role = "unknown"

        event_time = str(obj.get("time") or obj.get("timestamp") or obj.get("created_at") or "").strip()
        content_hash = sha256_text(normalize_text(content))[:12]
        memory_id = f"mem:dialogue:{file_stem}:{line_idx}:{content_hash}"

        records.append({
            "memory_id": memory_id,
            "source_class": "dialogue",
            "source_file": rel_file,
            "source_locator": f"line:{line_idx}",
            "event_time": event_time or None,
            "speaker": role,
            "title": f"対話ターン ({role})",
            "primary_text": content,
            "content_hash": content_hash,
            "metadata_json": json.dumps({
                "role": role,
                "file_stem": file_stem,
                "line_idx": line_idx,
                "is_test": bool(obj.get("is_test")),
                "actor": str(obj.get("actor") or ""),
                "conversation_actor": str(obj.get("conversation_actor") or obj.get("actor_context") or ""),
                "turn_id": str(obj.get("turn_id") or ""),
                "message_id": str(obj.get("message_id") or ""),
                "trace_id": str(obj.get("trace_id") or ""),
            }, ensure_ascii=False),
        })

    return records


# ----------------------------------------------------------------------
# Source Discovery & Indexing Orchestration
# ----------------------------------------------------------------------

def discover_memory_sources(base_dir: Union[Path, str]) -> List[Tuple[Path, str]]:
    """Discover all 4 target source files under base_dir."""
    base = Path(base_dir).resolve()
    sources: List[Tuple[Path, str]] = []

    # 1. growth_log.txt
    growth_path = base / "data" / "memory" / "growth_log.txt"
    if growth_path.exists() and growth_path.is_file():
        sources.append((growth_path, "growth"))

    # 2. summary.txt
    summary_path = base / "data" / "memory" / "summary.txt"
    if summary_path.exists() and summary_path.is_file():
        sources.append((summary_path, "summary"))

    # 3. experience_context.jsonl
    exp_path = base / "data" / "memory" / "experience_context.jsonl"
    if exp_path.exists() and exp_path.is_file():
        sources.append((exp_path, "experience"))

    # 4. Dialogue JSONL logs
    log_dir = base / "data" / "logs"
    if log_dir.exists() and log_dir.is_dir():
        for p in sorted(log_dir.glob("*.jsonl")):
            if p.is_file() and (p.name.startswith("chat_") or p.name.startswith("web_") or p.name.startswith("web_chat_")):
                sources.append((p, "dialogue"))

    return sources


def parse_source(file_path: Path, source_class: str, base_dir: Path) -> List[Dict[str, Any]]:
    if source_class == "growth":
        records = parse_growth_log(file_path, base_dir)
    elif source_class == "summary":
        records = parse_summary_txt(file_path, base_dir)
    elif source_class == "experience":
        records = parse_experience_jsonl(file_path, base_dir)
    elif source_class == "dialogue":
        records = parse_dialogue_jsonl(file_path, base_dir)
    else:
        records = []
    return [
        record for record in records
        if memory_record_exclusion_reason(record, base_dir) is None
    ]


def rebuild_memory_index(base_dir: Union[Path,str], db_path: Optional[Union[Path,str]]=None,
                         force: bool=True) -> Dict[str,Any]:
    """Rebuild the derived view transactionally, never unlink a usable index."""
    result=index_memory_sources(base_dir,db_path,incremental=False)
    return {**result, "status":"rebuilt" if result['ok'] else result['status'],
            "indexed_sources":result['updated_sources'], "rebuilt_at":result['updated_at']}


def index_memory_sources(base_dir: Union[Path,str], db_path: Optional[Union[Path,str]]=None,
                         incremental: bool=True) -> Dict[str,Any]:
    """Transactional derived-only sync of complete, stable source revisions.

    A changed/torn source never advances the indexed fingerprint. No model calls,
    source edits or canonical mutations. Query-time live-delta stays available.
    """
    base=Path(base_dir).resolve(); target=Path(db_path) if db_path else default_db_path(base)
    con=get_connection(target)
    deferred=[]; updated=0; skipped=0
    def filter_stamp():
        p=base/'data/memory/dialogue_quarantine.json'
        return sha256_file(p) if p.exists() else 'absent'
    try:
        init_schema(con)
        con.execute('BEGIN IMMEDIATE')
        cur=con.cursor()
        known={r['source_path']:dict(r) for r in cur.execute('SELECT * FROM sources')}
        previous=cur.execute("SELECT value FROM meta WHERE key='quarantine_sha256'").fetchone()
        quarantine=filter_stamp()
        quarantine_changed=not previous or previous[0]!=quarantine
        discovered=discover_memory_sources(base); seen=set(); now_str=now_iso()
        for path,sclass in discovered:
            rel=logical_source_path(path,base);seen.add(rel)
            try:
                stat1=path.stat(); digest1=sha256_file(path)
                old=known.get(rel)
                # Validate physical revision; no meaning-based record filtering.
                if (incremental and not quarantine_changed and old
                        and old['file_sha256']==digest1 and old['file_size']==stat1.st_size):
                    skipped+=1;continue
                if path.suffix=='.jsonl' and stat1.st_size:
                    with path.open('rb') as f:
                        f.seek(-1,2)
                        if f.read(1)!=b'\n':
                            deferred.append({'source':rel,'reason':'incomplete_final_line'});continue
                records=parse_source(path,sclass,base)
                stat2=path.stat();digest2=sha256_file(path)
                if ((stat1.st_ino,stat1.st_size,stat1.st_mtime_ns)!=(stat2.st_ino,stat2.st_size,stat2.st_mtime_ns)
                        or digest1!=digest2):
                    deferred.append({'source':rel,'reason':'source_changed_during_read'});continue
            except (OSError,UnicodeError,ValueError) as exc:
                deferred.append({'source':rel,'reason':type(exc).__name__});continue
            old_ids=[r[0] for r in cur.execute('SELECT memory_id FROM memory_records WHERE source_file=?',(rel,))]
            cur.executemany('DELETE FROM memory_fts WHERE memory_id=?',[(i,) for i in old_ids])
            cur.execute('DELETE FROM memory_records WHERE source_file=?',(rel,))
            for r in records:
                cur.execute('INSERT OR REPLACE INTO memory_records (memory_id,source_class,source_file,source_locator,event_time,speaker,title,primary_text,content_hash,metadata_json,indexed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                            (r['memory_id'],r['source_class'],r['source_file'],r['source_locator'],r['event_time'],r['speaker'],r.get('title',''),r['primary_text'],r['content_hash'],r.get('metadata_json','{}'),now_str))
                cur.execute('INSERT INTO memory_fts (memory_id,title,primary_text) VALUES (?,?,?)',(r['memory_id'],r.get('title',''),r['primary_text']))
            cur.execute('INSERT OR REPLACE INTO sources (source_path,source_class,file_mtime,file_size,file_sha256,record_count,indexed_at) VALUES (?,?,?,?,?,?,?)',
                        (rel,sclass,stat2.st_mtime,stat2.st_size,digest2,len(records),now_str))
            updated+=1
        # A missing file is distinguished from a concurrently reappeared file.
        for rel in set(known)-seen:
            if (base/rel).exists():
                deferred.append({'source':rel,'reason':'source_reappeared'});continue
            ids=[r[0] for r in cur.execute('SELECT memory_id FROM memory_records WHERE source_file=?',(rel,))]
            cur.executemany('DELETE FROM memory_fts WHERE memory_id=?',[(i,) for i in ids])
            cur.execute('DELETE FROM memory_records WHERE source_file=?',(rel,))
            cur.execute('DELETE FROM sources WHERE source_path=?',(rel,));updated+=1
        if filter_stamp()!=quarantine:
            raise RuntimeError('Quarantine changed during sync; transaction rolled back')
        total=cur.execute('SELECT COUNT(*) FROM memory_records').fetchone()[0]
        cur.execute("INSERT OR REPLACE INTO meta VALUES ('total_records',?)",(str(total),))
        cur.execute("INSERT OR REPLACE INTO meta VALUES ('last_sync_attempt_at',?)",(now_str,))
        if not deferred:
            cur.execute("INSERT OR REPLACE INTO meta VALUES ('updated_at',?)",(now_str,))
            cur.execute("INSERT OR REPLACE INTO meta VALUES ('quarantine_sha256',?)",(quarantine,))
            if not incremental:
                cur.execute("INSERT OR REPLACE INTO meta VALUES ('last_rebuild_at',?)",(now_str,))
        con.commit()
        return {'ok':not deferred,'status':'partial_deferred' if deferred else 'updated' if updated else 'unchanged',
                'db_path':str(target),'updated_sources':updated,'skipped_sources':skipped,
                'total_records':total,'updated_at':now_str,'deferred_sources':deferred}
    except Exception:
        con.rollback();raise
    finally:
        con.close()


# ----------------------------------------------------------------------
# Candidate Search Primitive (Phase 1 Internal Primitive)
# ----------------------------------------------------------------------

def _extract_mechanical_tokens(text: str) -> List[str]:
    """Extract multi-grain subphrase, script cluster, and compound tokens mechanically.

    Operates without hardcoded dictionaries or semantic classification:
    - Quoted terms: 「...」, 『...』, "...", '...'
    - Kanji compounds (length >= 2)
    - Katakana runs (length >= 2)
    - Alphanumeric / code identifiers (length >= 2)
    - Kanji + Hiragana inflection compounds (verbs/adjectives, length >= 2)
    - Standard whitespace tokens (length >= 2)
    """
    if not text:
        return []

    norm = unicodedata.normalize("NFKC", str(text))
    cleaned = re.sub(r'[\'\"「」『』\(\)\{\}\[\]\?\!？！、。・…\~\:\;\s]', " ", norm)

    tokens: set[str] = set()

    # 1. Quoted terms from original text
    quoted = re.findall(r'[「『\"\'\(]([^」』\"\'\)]+)[」』\"\'\)]', norm)
    for q in quoted:
        q_str = q.strip()
        if len(q_str) >= 2:
            tokens.add(q_str)

    # 2. Script-cluster runs
    kanji_runs = re.findall(r'[\u4e00-\u9fff]{2,}', cleaned)
    katakana_runs = re.findall(r'[\u30a0-\u30ff]{2,}', cleaned)
    alnum_runs = re.findall(r'[A-Za-z0-9_]{2,}', cleaned)
    mixed_runs = re.findall(r'[\u4e00-\u9fff]+[\u3040-\u309f]{1,4}', cleaned)

    for t in kanji_runs + katakana_runs + alnum_runs + mixed_runs:
        t_str = t.strip()
        if len(t_str) >= 2:
            tokens.add(t_str)

    # 3. Whitespace-separated words
    for w in norm.split():
        cleaned_w = re.sub(r'[\'\"「」『』\(\)\{\}\[\]\?\!？！、。・…\~\:\;]', "", w).strip()
        if len(cleaned_w) >= 2:
            tokens.add(cleaned_w)

    return sorted(tokens, key=lambda x: -len(x))


def _sanitize_fts5_tokens(query: str) -> List[str]:
    """Clean query terms and extract search tokens (delegates to _extract_mechanical_tokens)."""
    return _extract_mechanical_tokens(query)


def search_candidates(
    query: str,
    *,
    base_dir: Optional[Union[Path, str]] = None,
    db_path: Optional[Union[Path, str]] = None,
    memory_type: Optional[str] = None,
    after_date: Optional[str] = None,
    before_date: Optional[str] = None,
    limit: int = 10,
    max_chars: int = 4000,
) -> Dict[str, Any]:
    """Retrieve candidate memory records via SQLite FTS5 with multi-path trigram & exact fallback.

    Returns Candidate Records with IDs, timestamps, and Provenance.
    Does NOT perform final semantic filtering or truth judgment (reserved for the agent).
    If index is missing or corrupted, returns status='INDEX_UNAVAILABLE' (never 'MEMORY_NOT_FOUND').
    """
    if db_path:
        target_db = Path(db_path)
    elif base_dir:
        target_db = default_db_path(base_dir)
    else:
        target_db = Path("data/memory/memory_index.sqlite")

    if not target_db.exists() or not target_db.is_file():
        return {
            "ok": False,
            "status": "INDEX_UNAVAILABLE",
            "error": f"sidecar index not found at {target_db}",
            "candidates": [],
            "total_matches": 0,
            "candidate_count": 0,
        }

    raw_query = str(query or "").strip()
    if not raw_query:
        return {
            "ok": True,
            "status": "INDEX_SEARCH_EMPTY",
            "query": "",
            "candidates": [],
            "total_matches": 0,
            "candidate_count": 0,
        }

    safe_limit = max(1, min(int(limit), 100))
    safe_max_chars = max(200, min(int(max_chars), 50000))
    overfetch_limit = max(safe_limit * 5, 50)

    try:
        con = get_connection(target_db, read_only=True)
    except Exception as e:
        return {
            "ok": False,
            "status": "INDEX_UNAVAILABLE",
            "error": f"failed to connect to sidecar index: {e}",
            "candidates": [],
            "total_matches": 0,
            "candidate_count": 0,
        }

    try:
        cur = con.cursor()
        tokens = _extract_mechanical_tokens(raw_query)
        trigram_tokens = [t for t in tokens if len(t) >= 3]
        short_tokens = [t for t in tokens if len(t) == 2]

        where_clauses: List[str] = []
        filter_params: List[Any] = []

        if memory_type and str(memory_type).strip() in SOURCE_CLASSES:
            where_clauses.append("r.source_class = ?")
            filter_params.append(str(memory_type).strip())

        if after_date and str(after_date).strip():
            where_clauses.append("r.event_time >= ?")
            filter_params.append(str(after_date).strip())

        if before_date and str(before_date).strip():
            where_clauses.append("r.event_time <= ?")
            filter_params.append(str(before_date).strip())

        extra_filter_sql = (" AND " + " AND ".join(where_clauses)) if where_clauses else ""

        results: List[Dict[str, Any]] = []

        # Path 1: Exact full phrase match (if long enough)
        clean_full = re.sub(r'[\'\"\(\)\{\}\[\]\*\^\:\;\~]', " ", raw_query).strip()
        if len(clean_full) >= 3:
            fts_sql = f"""
            SELECT
                r.memory_id, r.source_class, r.source_file, r.source_locator,
                r.event_time, r.speaker, r.title, r.primary_text, r.metadata_json, r.content_hash,
                bm25(memory_fts) as score,
                snippet(memory_fts, 2, '<b>', '</b>', '...', 16) as snippet
            FROM memory_fts
            JOIN memory_records r ON r.memory_id = memory_fts.memory_id
            WHERE memory_fts MATCH ? {extra_filter_sql}
            ORDER BY score ASC, r.event_time DESC
            LIMIT ?;
            """
            try:
                cur.execute(fts_sql, [f'"{clean_full}"', *filter_params, overfetch_limit])
                for row in cur.fetchall():
                    results.append(dict(row))
            except sqlite3.OperationalError as e:
                if "syntax error" in str(e).lower() or "fts5" in str(e).lower():
                    pass
                else:
                    raise

        # Path 2: Subphrase / token disjunction OR match (trigrams)
        if trigram_tokens:
            top_tokens = trigram_tokens[:8]
            or_expr = " OR ".join(f'"{t}"' for t in top_tokens)
            fts_or_sql = f"""
            SELECT
                r.memory_id, r.source_class, r.source_file, r.source_locator,
                r.event_time, r.speaker, r.title, r.primary_text, r.metadata_json, r.content_hash,
                bm25(memory_fts) as score,
                snippet(memory_fts, 2, '<b>', '</b>', '...', 16) as snippet
            FROM memory_fts
            JOIN memory_records r ON r.memory_id = memory_fts.memory_id
            WHERE memory_fts MATCH ? {extra_filter_sql}
            ORDER BY score ASC, r.event_time DESC
            LIMIT ?;
            """
            try:
                cur.execute(fts_or_sql, [or_expr, *filter_params, overfetch_limit])
                for row in cur.fetchall():
                    results.append(dict(row))
            except sqlite3.OperationalError as e:
                if "syntax error" in str(e).lower() or "fts5" in str(e).lower():
                    pass
                else:
                    raise

        # Path 3: 2-char token substring fallback (when trigram yields < safe_limit or for 2-char keywords)
        if len(results) < safe_limit or not results:
            like_terms = short_tokens[:4] if short_tokens else [raw_query]
            like_clauses = ["(r.primary_text LIKE ? OR r.title LIKE ?)" for _ in like_terms]
            like_params: List[Any] = []
            for t in like_terms:
                like_params.extend([f"%{t}%", f"%{t}%"])
            combined_where = " AND ".join(["(" + " OR ".join(like_clauses) + ")"] + where_clauses) if where_clauses else " OR ".join(like_clauses)

            fallback_sql = f"""
            SELECT
                r.memory_id, r.source_class, r.source_file, r.source_locator,
                r.event_time, r.speaker, r.title, r.primary_text, r.metadata_json, r.content_hash,
                0.0 as score,
                substr(r.primary_text, 1, 120) as snippet
            FROM memory_records r
            WHERE {combined_where}
            ORDER BY r.event_time DESC
            LIMIT ?;
            """
            cur.execute(fallback_sql, [*like_params, *filter_params, overfetch_limit])
            for row in cur.fetchall():
                results.append(dict(row))

        # Defensive Deduplication: collapse identical memory_ids and mechanical mirror logs
        seen_memory_ids: set[str] = set()
        seen_event_keys: set[tuple[str, str, str, str]] = set()
        deduped_raw: List[Dict[str, Any]] = []

        for item in results:
            mid = str(item.get("memory_id") or "").strip()
            if not mid or mid in seen_memory_ids:
                continue

            etime = str(item.get("event_time") or "").strip()
            sclass = str(item.get("source_class") or "").strip()
            speaker = str(item.get("speaker") or "").strip()
            chash = str(item.get("content_hash") or "").strip()
            ptext = str(item.get("primary_text") or "").strip()

            event_key = (sclass, etime, speaker, chash or ptext)
            if etime and event_key in seen_event_keys:
                continue
            seen_event_keys.add(event_key)

            seen_memory_ids.add(mid)
            deduped_raw.append(item)

        # Quarantine is enforced at read time too, so manifest changes take
        # effect immediately even before the derived index is rebuilt.
        quarantine_base = Path(base_dir).resolve() if base_dir else target_db.resolve().parents[2]
        visible_deduped_raw = [
            item for item in deduped_raw
            if memory_record_exclusion_reason(item, quarantine_base) is None
        ]

        # Mechanical Diversification: anti-monopoly across short conversation sessions
        session_counts: Dict[str, int] = defaultdict(int)
        pass1_candidates: List[Dict[str, Any]] = []
        pass2_deferred: List[Dict[str, Any]] = []

        for item in visible_deduped_raw:
            etime = str(item.get("event_time") or "").strip()
            hour = int(etime[11:13]) if len(etime) >= 13 and etime[11:13].isdigit() else 0
            skey = f"{etime[:10]}:{hour // 2}" if len(etime) >= 10 else "generic"
            if session_counts[skey] < 1:
                session_counts[skey] += 1
                pass1_candidates.append(item)
            else:
                pass2_deferred.append(item)

        unique_results = pass1_candidates + pass2_deferred
        total_matches = len(unique_results)
        candidates: List[Dict[str, Any]] = []
        accumulated_chars = 0

        for item in unique_results:
            text = str(item.get("primary_text") or "")
            meta_raw = str(item.get("metadata_json") or "{}")
            try:
                meta = json.loads(meta_raw)
            except Exception:
                meta = {}

            item_chars = len(text)
            if accumulated_chars + item_chars > safe_max_chars and candidates:
                break

            candidates.append({
                "memory_id": item["memory_id"],
                "source_class": item["source_class"],
                "source_file": item["source_file"],
                "source_locator": item["source_locator"],
                "event_time": item["event_time"],
                "speaker": item["speaker"],
                "title": item.get("title", ""),
                "primary_text": text,
                "snippet": item.get("snippet", text[:100]),
                "bm25_score": float(item.get("score", 0.0)),
                "metadata": meta,
            })
            accumulated_chars += item_chars

            if len(candidates) >= safe_limit:
                break

        status = "INDEX_SEARCH_SUCCESS" if candidates else "INDEX_SEARCH_EMPTY"

        return {
            "ok": True,
            "status": status,
            "query": raw_query,
            "total_matches": total_matches,
            "candidate_count": len(candidates),
            "candidates": candidates,
        }

    except Exception as e:
        return {
            "ok": False,
            "status": "INDEX_ERROR",
            "error": f"search query execution failed: {e}",
            "candidates": [],
            "total_matches": 0,
            "candidate_count": 0,
        }
    finally:
        con.close()


# ----------------------------------------------------------------------
# Provenance & Inspection Primitives
# ----------------------------------------------------------------------

def get_memory_by_id(
    memory_id: str,
    *,
    base_dir: Optional[Union[Path, str]] = None,
    db_path: Optional[Union[Path, str]] = None,
) -> Optional[Dict[str, Any]]:
    """Retrieve full memory record by its stable memory_id."""
    if db_path:
        target_db = Path(db_path)
    elif base_dir:
        target_db = default_db_path(base_dir)
    else:
        target_db = Path("data/memory/memory_index.sqlite")

    if not target_db.exists():
        return None

    con = get_connection(target_db, read_only=True)
    try:
        cur = con.cursor()
        cur.execute("""
        SELECT memory_id, source_class, source_file, source_locator, event_time,
               speaker, title, primary_text, content_hash, metadata_json, indexed_at
        FROM memory_records
        WHERE memory_id = ?;
        """, (str(memory_id).strip(),))
        row = cur.fetchone()
        if not row:
            return None

        data = dict(row)
        try:
            data["metadata"] = json.loads(data.get("metadata_json") or "{}")
        except Exception:
            data["metadata"] = {}
        quarantine_base = Path(base_dir).resolve() if base_dir else target_db.resolve().parents[2]
        if memory_record_exclusion_reason(data, quarantine_base) is not None:
            return None
        return data
    finally:
        con.close()


def get_record_provenance(
    memory_id: str,
    *,
    base_dir: Optional[Union[Path, str]] = None,
    db_path: Optional[Union[Path, str]] = None,
) -> Optional[Dict[str, Any]]:
    """Retrieve exact provenance locator for audit."""
    record = get_memory_by_id(memory_id, base_dir=base_dir, db_path=db_path)
    if not record:
        return None
    return {
        "memory_id": record["memory_id"],
        "source_class": record["source_class"],
        "source_file": record["source_file"],
        "source_locator": record["source_locator"],
        "event_time": record["event_time"],
        "speaker": record["speaker"],
        "content_hash": record["content_hash"],
    }
