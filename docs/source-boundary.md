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

Import paths were adapted for packaging. Agent-specific speaker and
interpretation labels were replaced with generic `agent` labels; this public
snapshot is not guaranteed to be a drop-in replacement for existing private
data schemas. **Do not point a test instance at a production workspace.**

**Excluded deliberately:**
- private dialogue/audio/video/photos, personal profile, life history,
  raw traces, canonical memory, WORLD and policy state;
- authentication material, API keys, environment files, service configs;
- original Git history and all release worktree content outside the selected modules;
- the personal agent's system prompts and persona-specific memory decision layer;
- third-party pretrained weights, databases and model downloads.

Every included example record is fabricated. A separate Git history was
created for this publication, so no path or author metadata from the original
private repository is pushed.

## Retrieval status is not factual truth

The search engine reports original source utterances, not verified real-world
events. Source time, a mentioned date, and confirmed completion are independent
attributes. A planned event must not be described as completed solely because
the statement appears in an index.

## Legal boundaries

This repository distributes only code licensed by the owner under
PolyForm Noncommercial License 1.0.0. Future contributors must explicitly
agree to the licensing rules for their contributions.
External model licenses are separate. Commercial inquiries require a
separate written agreement; see `COMMERCIAL-LICENSING.md`.
