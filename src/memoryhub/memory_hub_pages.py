# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Mechanical, revision-bound windows over already authorized memory records."""
from __future__ import annotations
import hashlib
import json
import re


def record_revision(row: dict) -> str:
    value = {k: row.get(k) for k in ('memory_id','source_file','source_locator','event_time','speaker','primary_text')}
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()


def project_record_page(row: dict, *, offset: int = 0, length: int = 1600, date: str = '') -> dict:
    text = str(row.get('primary_text') or '')
    end = min(len(text), offset + length)
    revision = record_revision(row)
    source_match = re.fullmatch(r"data/logs/chat_(\d{4})(\d{2})(\d{2})\.jsonl", str(row.get('source_file') or ''))
    source_day = '-'.join(source_match.groups()) if source_match else ''
    day = date or source_day or str(row.get('recorded_at') or row.get('event_time') or '')[:10]
    query = {'action':'inspect','memory_id':row['memory_id'],'date':day,
             'offset':end,'length':length,'expected_sha256':revision}
    return {
        'memory_id':row['memory_id'], 'memory_type':row['memory_type'],
        'source_class':row['source_class'], 'source_file':row['source_file'],
        'source_locator':row['source_locator'], 'source_date':day, 'event_time':row.get('event_time'),
        'recorded_at':row.get('recorded_at') or row.get('event_time'),
        'event_time_semantics':'record_timestamp_not_event_occurrence',
        'described_event_time':None, 'speaker':row.get('speaker'),
        'title':str(row.get('title') or '')[:180],
        'text':text[offset:end], 'source_verified':bool(row.get('source_verified',False)),
        'source_content_sha256':row.get('source_content_sha256') or hashlib.sha256(text.encode()).hexdigest(),
        'record_revision_sha256':revision,
        'source_total_chars':len(text), 'range_start':offset, 'range_end':end,
        'source_text_truncated':offset>0 or end<len(text),
        'next_query':query if end<len(text) else None,
        'source_authority':row['source_authority'], 'evidence_level':row['evidence_level'],
        'source_refs':row.get('source_refs') or [],
        'context_neighbors':row.get('context_neighbors') or [],
        'delivery_scope':'shown_range_only_not_full_source_comprehension',
        '_source_observation': {**(row.get('metadata') or {}),
            **{k:row[k] for k in ('message_id','turn_id','trace_id','is_test','conversation_actor','actor') if k in row},
            **(row.get('_source_observation') or {})},
    }


def page_label(row: dict) -> str:
    result = (f"shown_chars=[{row.get('range_start',0)},{row.get('range_end',len(str(row.get('text') or '')))}); "
              f"total_chars={row.get('source_total_chars','unknown')}; "
              f"partial={bool(row.get('source_text_truncated'))}; "
              f"record_revision={row.get('record_revision_sha256','unknown')}")
    if row.get('next_query'):
        result += '; continue_with_memory.hub=' + json.dumps(row['next_query'],ensure_ascii=False,separators=(',',':'))
    return result
