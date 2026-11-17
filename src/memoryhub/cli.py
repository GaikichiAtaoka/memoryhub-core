# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Explicit-workspace CLI for the released MemoryHub core."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .memory_hub import execute_memory_hub
from .memory_hub_temporal_bridge import search_temporal_memory
from .memory_sidecar_index import rebuild_memory_index


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="memoryhub",
        description="Read-only, source-backed local memory queries. Indexing writes only a derived SQLite cache.",
    )
    parser.add_argument("--base", type=Path, required=True,
                        help="Explicit workspace root. No private system root is inferred.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("index", help="Rebuild a disposable SQLite FTS5 search index from this workspace.")
    for action in ("search", "chronology"):
        p = sub.add_parser(action)
        p.add_argument("query")
        p.add_argument("--scope", default="episodic",
                       choices=("all", "episodic", "semantic", "reflective", "procedural"))
        p.add_argument("--limit", default=8, type=int)
    day = sub.add_parser("day")
    day.add_argument("date", help="YYYY-MM-DD")
    day.add_argument("--limit", default=8, type=int)
    inspect = sub.add_parser("inspect")
    inspect.add_argument("memory_id")
    inspect.add_argument("--date", default="")
    inspect.add_argument("--limit", default=8, type=int)
    temporal = sub.add_parser("temporal")
    temporal.add_argument("query")
    temporal.add_argument("--limit", default=8, type=int)

    args = parser.parse_args(argv)
    base = args.base.expanduser().resolve()
    if not base.is_dir():
        parser.error("--base must be an existing workspace directory")
    if args.command == "index":
        result = rebuild_memory_index(base)
    elif args.command == "temporal":
        result = search_temporal_memory(base_dir=base, query=args.query, limit=args.limit)
    else:
        kwargs = {"action": args.command, "base_dir": base, "limit": args.limit}
        for key in ("query", "scope", "date", "memory_id"):
            if hasattr(args, key):
                kwargs[key] = getattr(args, key)
        result = execute_memory_hub(**kwargs)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("ok", result.get("status") in ("FOUND", "EMPTY")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
