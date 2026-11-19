# Changelog

## 0.2.0a1 — 2026-11-20

Experimental preview of source-backed memory lifecycle management.

- Add explicit `memory.manage` operations to retain, propose, accept, defer,
  correct, merge, suppress, list and inspect retained memories.
- Keep user quotations, assistant interpretations, subjects and source times
  distinct, with source validation and versioned mutation receipts.
- Expose active retained memories through existing Hub search, chronology and
  `hub:lm:` inspection. Read-only queries do not create the optional store.
- Apply current source visibility and read permission to replayed requests;
  corrected or forgotten records cannot reappear as stale successful responses.
- Add a fully invented offline lifecycle demonstration, public API regressions,
  and English/Japanese integration documentation.
- Provide wheel, source distribution and source archive packages with checksums.

`forget` suppresses reuse from the retained-memory layer. Original conversation
logs and operation history remain; other source-search paths can still find
the original statements. Natural conversation selection and long-term behavior
remain experimental. See [release verification](docs/RELEASE_VERIFICATION.md).
