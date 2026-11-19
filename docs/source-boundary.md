# Publication boundary and implementation scope

The public MemoryHub core was extracted from the existing local source code,
not reimagined from a benchmark chart. The following modules are included:

- `memory_hub.py`: read-only hub API, result scoping and rendering
- `memory_sidecar_index.py`: FTS5 index and source-aware candidate lookup
- `dialogue_provenance.py`: filtering of excluded, test and quarantined inputs
- `memory_hub_pages.py`, `memory_source_inspection.py`: source inspection
- `memory_source_origin.py`, `memory_source_observation.py`: provenance
- `memory_temporal_projection.py`, `memory_time_coordinates.py`, `temporal_continuity.py`: time coordinates
- `memory_hub_temporal_bridge.py`: optional BGE-M3, source-backed reranking and cache
- `memory_lifecycle.py`: explicit private retained-memory operations, source
  verification, revision checks, permissions and atomic receipts

Import paths were adapted for packaging. Agent-specific speaker and
interpretation labels were replaced with generic labels (`agent` in some
retrieval adapters and `assistant` in the Phase 2 API). This public snapshot is
not guaranteed to be a drop-in replacement for existing private data schemas.
Use a separate synthetic workspace when evaluating it.

**Excluded deliberately:**

- private dialogue/audio/video/photos, personal profile, life history,
  raw traces, canonical memory, private world-state and policy data;
- authentication material, API keys, environment files, service configs;
- original Git history and all release worktree content outside the selected modules;
- the personal agent's system prompts and persona-specific memory decision layer;
- third-party pretrained weights, databases and model downloads.

Every included example record is fabricated. A separate Git history was
created for this publication, so no path or author metadata from the original
private repository is pushed.

## Phase 2 write and host boundaries

Version 0.2.0a1 adds an experimental management interface without changing the
read-only contract of `memory.hub` or the retrieval CLI. The Python
`memory.manage` implementation writes `<base>/data/memory/lifecycle.sqlite`,
including current retained state and append-only operation receipts. Indexing
still writes only the derived original-source index. Neither operation rewrites
the original dialogue or an application's canonical profile.

The host chooses the base path and provides a persisted current user message,
message/turn identity, timezone-aware timestamp, execution audience, and
permissions. These are not model-supplied arguments. The model chooses whether
to retain a passage, an operation, a label, and the subject of the memory. The
package checks original source passages, revisions, state transitions, and
permissions; it does not choose the importance or meaning of an experience.
No timer, scheduled reflection, mandatory topic list, or automatic promotion
of a candidate is included.

The original source must remain available at its recorded message ID, date,
line, and content hash. Reads recheck that source and its eligibility. A
session reset can reuse the same workspace; source deletion, movement, or
rewriting can invalidate a retained entry. Connecting search results to the
new model session is the host's responsibility. Model weights and full session
context are not stored by this package.

The workspace policy file `data/config/capability_policy.json` controls shared
management and retained Hub reads. A `policy=` object supplied to a management
call applies to that call only. Public or broadcast management contexts are
rejected. Local files are not encrypted or isolated by this API.

`forget` suppresses the selected retained record from normal reuse, including
exact inspection and request replay. It does **not** delete original dialogue
or previous audit receipts. Original-dialogue search can still return the old
utterance, and independent retained records are unaffected. The operation is
not a promise of total erasure. See the [Phase 2 guide](MEMORY_LIFECYCLE_EN.md)
for the precise contract.

## Retrieval status is not factual truth

The search engine reports original source utterances, not verified real-world
events. Source time, a mentioned date, and confirmed completion are independent
attributes. A planned event must not be described as completed solely because
the statement appears in an index.

A retained user statement preserves an exact quote; an assistant interpretation
is separately labeled and starts as a candidate. Accepting an interpretation
requires an additional permission and does not turn it into established fact.
Titles, confidence values, and reasons are model-authored metadata. Even with
source checks, the model may select an unhelpful passage, omit a qualification,
assign the wrong subject, or misread an old statement as current truth.

The offline lifecycle demo has predefined choices and invented source events.
It exercises actual persistence, revision and retrieval behavior; it is not a
live-model benchmark or evidence of natural conversational recall after reset.

## Legal boundaries

This repository distributes only code licensed by the owner under
PolyForm Noncommercial License 1.0.0. Future contributors must explicitly
agree to the licensing rules for their contributions.
External model licenses are separate. Commercial inquiries require a
separate written agreement; see `COMMERCIAL-LICENSING.md`.
