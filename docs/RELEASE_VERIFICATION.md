# Release verification: 0.2.0a1

This is an experimental preview of explicit, source-backed memory management.
The checks below exercise invented data and deterministic operations. They do
not establish live-model memory quality or production readiness.

## Source checks

- Python 3.12.14: all 63 unit and subprocess tests passed.
- Python 3.13: all 63 unit and subprocess tests passed.
- The suite includes 40 lifecycle cases, two offline-demo subprocess cases,
  and 21 existing retrieval/integration cases.
- A separate Python process reopens saved memory, demonstrating persistence
  when the workspace and original source files remain available.
- Tests cover exact source validation, revisions, atomic merge behavior,
  concurrent/repeated requests, source quarantine, current read permission,
  and suppression of forgotten retained bodies.
- The offline lifecycle demo reports 12 successful mechanical checks. It uses
  invented conversation and scripted decisions, with no model or network.
- Markdown links and Python documentation snippets were checked locally.

## Distribution checks

- Built a platform-independent Python wheel and a source distribution with
  Python 3.12.14 and setuptools 84.2.1, without fetching runtime dependencies.
- Installed the wheel with `--no-index --no-deps` into a fresh virtual
  environment. Imports resolved to that environment's `site-packages`, not
  to the source checkout.
- All 63 tests passed against the installed wheel. The lifecycle demo passed
  all 12 checks, and the existing offline agent-integration example ran.
- The installed `memoryhub` CLI rebuilt the synthetic index, found search and
  chronology results, and returned a complete day.
- Extracted the source distribution and verified every `SOURCE_SHA256SUMS`
  entry. Rebuilt its wheel, installed it into another fresh environment, and
  ran the lifecycle demo successfully.
- The final release includes a source ZIP for the examples and documentation.
  Its manifest covers the selected source files. `SHA256SUMS` accompanies the
  release assets; `SOURCE_SHA256SUMS` covers source files inside the archive.
- Distribution content was checked for unintended databases, bytecode, private
  deployment names/paths, and non-synthetic runtime data. Only the explicitly
  included invented JSON/JSONL fixtures are distributed.

The wheel contains the package, CLI entry point, and license notices. Examples,
guides, and tests are in the source distribution and source ZIP. This release
is published on GitHub; the package version is not claimed to be on PyPI.

## Scope and limits

The tests do not evaluate how a live model chooses what to retain or what to
retrieve, full context restoration after a session reset, long-term usefulness,
or semantic correctness of a subject label. The host must still save original
dialogue and bring selected evidence into each model session.

`forget` suppresses a retained record. It does not erase original conversations
or audit history, and source search can still find those original statements.

GitHub Actions is configured for Python 3.11, 3.12, and 3.13. Check the
[actual workflow runs](https://github.com/GaikichiAtaoka/memoryhub-core/actions)
for the result associated with the release commit; the local results above do
not stand in for that CI result.
