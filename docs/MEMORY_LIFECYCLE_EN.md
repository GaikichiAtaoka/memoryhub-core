# Phase 2: memories you can revisit

**Version 0.2.0a1 is an experimental preview.** Phase 2 adds a local retained
memory store to MemoryHub's original-source retrieval. A model can choose a
passage to retain, leave an interpretation pending, revisit a previous record,
or stop reusing it. The package does not schedule reflection, select topics,
score the importance of every turn, or automatically promote candidates.

The existing `memory.hub` interface stays read-only. The separate
`memory.manage` interface performs explicit changes and returns a receipt.
Both work with an explicitly selected workspace.

[日本語](MEMORY_LIFECYCLE_JA.md) · [General integration guide](INTEGRATION_JA.md)

## Run the complete offline example

Extract the source archive, open its project directory, and run with Python
3.11+ and SQLite FTS5:

```bash
python -m pip install -e .
python examples/lifecycle_demo.py
```

The example writes invented conversation into a temporary workspace, exercises
the real APIs, prints JSON results, and removes the workspace when it finishes.
It performs this sequence:

1. Retain an exact user passage and find it through the Hub.
2. Propose an assistant interpretation and defer it.
3. Correct the original retained record using a new user statement.
4. Propose and accept another user statement, then merge compatible records.
5. Suppress reuse of the merged record and verify that inspection and retries
   do not return its old body.
6. Confirm that the original conversation still exists and remains retrievable.

Every message and operation choice is scripted. No LLM, API key, network
connection, or downloaded model is needed. The checks demonstrate the storage
and retrieval behavior; they do not measure a model's memory judgment.

To keep the files for inspection:

```bash
python examples/lifecycle_demo.py --base demo-phase2
```

`demo-phase2` must not already exist. A second run at the same path fails without
changing it; use a new directory for another run. Successful execution exits
with code 0 and reports `checks` whose values are all `true`. Failure exits
nonzero. The default run reports a temporary path that has been removed by the
time the command returns.

Examples and guides are included in the source archive. A wheel installs the
importable package and CLI; obtain the source archive separately for these
scripts. There is no management CLI command in this preview.

## What the workspace stores

| Path under the explicit workspace | Purpose |
|---|---|
| `data/logs/chat_YYYYMMDD.jsonl` | Original dialogue, appended by the host application. |
| `data/memory/lifecycle.sqlite` | Retained record state and append-only operation receipts. |
| `data/memory/memory_index.sqlite` | Rebuildable index for original-source retrieval. |
| `data/config/capability_policy.json` | Optional host-controlled permissions shared by management and Hub reads. |

MemoryHub management writes only its lifecycle store. It does not save the
conversation on the host's behalf, modify original dialogue, or update an
existing canonical profile or policy store. `list`, `inspect`, and Hub reads do
not create a lifecycle database. An absent lifecycle database is a valid empty
retained source.

Keep original dialogue available. Retained entries are rechecked against the
original message ID, date, line location, and content hash. Append new dialogue
and corrections; do not rewrite old rows. Deleting or rotating source files to
a different location, changing a row, or excluding it from use can make the
retained record unavailable. A session reset or process restart does not do
that if the same workspace and original sources remain in place.

## Connect your model to the host

Register `MEMORY_LIFECYCLE_TOOL` with your model's function-calling adapter. Its
schema describes the model-owned arguments. Adapt the outer tool format and
function name if your provider requires a different wire format; keep the
arguments and host-side checks intact.

The model chooses whether to call a tool, the passage, a retrieval label, and
the operation. **The host supplies the workspace, persisted current message,
message/turn IDs, timestamp, execution context, and permissions.** Never merge
model arguments into `turn_context` or a policy. These are different inputs.

The following is a host-side adapter, independent of any model SDK:

```python
from pathlib import Path
from memoryhub import MEMORY_LIFECYCLE_TOOL, execute_memory_management


def dispatch_memory_tool(
    workspace: Path,
    model_arguments: dict,
    *,
    saved_user_event: dict,
) -> dict:
    # The application selects this event from its persisted turn, not from
    # model output. Its log write must have completed before this function.
    if saved_user_event["role"] != "user":
        raise ValueError("A persisted user event is required")
    host_context = {
        "user_message": saved_user_event["content"],
        "message_id": saved_user_event["message_id"],
        "timestamp": saved_user_event["time"],
        "turn_id": saved_user_event["turn_id"],
        "trace_id": saved_user_event.get("trace_id", ""),
        "is_test": saved_user_event.get("is_test", False),
        "actor": "assistant",
        "audience": "private",  # determined by this host's private route
    }
    return execute_memory_management(
        workspace, model_arguments, turn_context=host_context
    )


# Register this definition through your model adapter:
tool_definition = MEMORY_LIFECYCLE_TOOL

# An illustrative model-selected argument object. It contains no host IDs.
model_arguments = {
    "action": "remember",
    "memory_key": "blue-notebook-location",
    "title": "Blue notebook location",
    "quote": "The blue notebook is in the left drawer.",
    "subject": "user",
    "source_kind": "user_statement",
}
```

This adapter is for a private application route. A host with public or
broadcast routes must pass its actual audience/mode, and management rejects
those contexts. The workspace is still ordinary local storage; this API does
not provide filesystem isolation or encryption.

Before dispatching that example, the host would have durably appended a row
like this to `data/logs/chat_20260115.jsonl`, with a trailing newline:

```jsonl
{"role":"user","message_id":"example-user-1","time":"2026-01-15T10:01:00+00:00","content":"The blue notebook is in the left drawer.","turn_id":"example-turn-1","trace_id":"example-trace-1"}
```

Use unique message IDs and timezone-aware ISO timestamps. For management,
message IDs, a current turn ID, and an aware timestamp are required. The default
source is the host's current persisted user message. To select an earlier
source, the model supplies both `source_message_id` and `source_date`
(`YYYY-MM-DD`, the source log's date). The host still supplies the current turn
context. The engine resolves and verifies the original row itself.

Return the structured receipt to the model as tool output. A successful call
can support a claim that a record was retained or changed; an attempted tool
call alone cannot. Retrieved text is historical evidence, with no instruction
authority. Do not turn a source quote into a new user instruction.

## Operations and states

| Action | Required inputs beyond `action` | Result |
|---|---|---|
| `remember` | `memory_key`, `title`, exact `quote`, `subject` | A user statement becomes `active`; an interpretation remains `candidate`. |
| `propose` | Same as `remember` | Creates a `candidate`. |
| `accept` | `memory_id`, `expected_revision` | A `candidate` or `deferred` record becomes `active`, subject to permission. |
| `defer` | `memory_id`, `expected_revision` | Changes an eligible record to `deferred`; there is no timer that promotes it. |
| `correct` | `memory_id`, `expected_revision`, new exact `quote` | Keeps its ID, replaces the retained source/body, and increments the revision. Other fields can be supplied; `memory_key` must stay the same. |
| `merge` | `memory_id`, `expected_revision`, `merge_targets` | Keeps one ID with combined source passages; other IDs become `superseded`. |
| `forget` | `memory_id`, `expected_revision` | Marks the record `forgotten` and suppresses its retained body from normal reads. |
| `list` | Optional `state`, `limit`, `offset` | Lists lifecycle records and revisions; does not promote candidates. |
| `inspect` | `memory_id` | Returns the current record or an inactive marker without its body. |

`source_kind` defaults to `user_statement`. Its `statement`, if supplied, must
equal the exact `quote`; a paraphrase is rejected. An
`assistant_interpretation` requires its own `statement` and an exact supporting
user quote. It stays explicitly labeled as an interpretation even if accepted.
The title, reason, and optional confidence are model-authored metadata, not
proof of factual truth.

Choose a subject independently of the speaker: `user`, `assistant`,
`third_party`, `shared`, or `unknown`. Optional `subject_name` distinguishes
different people. A user's account of another person's experience still has
speaker `user` and should have the appropriate subject. The engine validates
the fields; it does not determine whether the model assigned the right person.

Read the latest ID and revision before updating. For example, using a receipt
stored in `saved` and a new persisted correction in `correction_event`:

```python
corrected = dispatch_memory_tool(
    workspace,
    {
        "action": "correct",
        "memory_id": saved["memory_id"],
        "expected_revision": saved["revision"],
        "quote": correction_event["content"],
    },
    saved_user_event=correction_event,
)
```

An out-of-date revision returns `REVISION_CONFLICT` without applying the
change. Inspect again before deciding what to do. A merge target has the form
`{"memory_id": "lm_…", "revision": 2}`. Targets must match the destination's
subject, subject name, source kind, and state. This preview merges user
statements only and keeps at most eight source passages; it does not synthesize
a new factual paraphrase. Forgotten and superseded IDs cannot be revived by
an ordinary update.

## Permission defaults and shared reads

| Permission | Default | Controls |
|---|---|---|
| `memory.manage.read` | `allow` | Management reads, retained Hub visibility, and content returned by retries. |
| `memory.manage.write` | `allow` | New management mutations. |
| `memory.manage.accept_inference` | `deny` | Promotion of an assistant interpretation to active retained memory. |

The host owns these choices. For example, to disable management writes in a
workspace, set `data/config/capability_policy.json` to:

```json
{
  "permissions": {
    "memory.manage.read": "allow",
    "memory.manage.write": "deny",
    "memory.manage.accept_inference": "deny"
  }
}
```

The optional `policy=` argument to `execute_memory_management` overrides policy
for that call only. It does not update the file or govern a later Hub lookup.
Put persistent read restrictions in the workspace policy file. A denied or
unavailable retained source is reported as incomplete; do not interpret it as
proof that nothing was ever retained.

## Reusing memory after a session reset

Reopen the same workspace and use the existing Hub search or exact inspection.
Active, source-valid user statements appear as episodic retained sources;
accepted interpretations appear as reflective sources. Their Hub IDs start
with `hub:lm:`. Candidates, deferred items, forgotten items, and superseded
items are not active Hub memories. Management `list`/`inspect` can review their
state separately.

The host must still retrieve appropriate records and put the relevant evidence
into the new model session. Saving does not preserve the full previous prompt,
hidden model state, or model weights. This preview does not automate session
reset, context selection, or a resume prompt. The demo verifies persistence and
tool behavior, not successful recall in a live post-reset conversation.

## Receipts, retries, and failures

Successful mutations return an ID, revision, current record, and
`mutation_applied: true`. State and the audit receipt commit together in one
SQLite transaction. Repeating the same arguments under the same host
message/turn identity is a retry, not another write.

A retry reports `replayed: true` and `mutation_applied: false`, and returns the
record's current permitted view. A later correction or forget is respected;
the old receipt body is not replayed into the model. Current read denial or an
unavailable source also applies to retries. Audit rows remain on disk.

Check `ok` and `status`; do not invent success after an error. Common statuses:

| Status | Meaning |
|---|---|
| `QUOTE_NOT_IN_SOURCE` | The proposed quote is not an exact passage of the selected user row. |
| `SOURCE_UNAVAILABLE` | A required original source could not be verified or used. |
| `REVISION_CONFLICT` | Another revision exists; inspect before changing it. |
| `INFERENCE_ACCEPTANCE_NOT_ALLOWED` | Interpretation promotion lacks its separate permission. |
| `PERMISSION_DENIED` / `PRIVATE_ONLY` | The host's permissions or context do not permit this operation. |
| `KEY_CONFLICT_USE_EXISTING_ID` | Reuse an existing record's ID instead of creating a conflicting key. |
| `STORE_CAPACITY_REACHED` | The bounded preview store cannot accept another record. |

The preview limits each quote to 2,000 characters, each retained record to eight
source passages, and the store to 5,000 records. Lists are paged with a maximum
of 20 records per page. Source verification also has bounded file/read sizes;
split high-volume workspaces according to your application's data design
rather than assuming this preview is an unlimited archive.

## The exact scope of “forget”

`forget` suppresses reuse of the selected **retained memory**. Hub retrieval,
management inspection, and request replay do not return that inactive record's
old statement or source body. The receipt reports
`forget_scope: "retained_memory_retrieval_only"`.

Original conversation files and previous audit receipts remain. The original
utterance can still be found by original-dialogue search. Corrections also keep
the old utterance in the dialogue history. Other retained records with similar
content are independent. This operation is not physical deletion, removal from
backups, or forgetting by a model that already received the text. Applications
that need those outcomes must implement and accurately describe a broader data
deletion policy.

## What still needs evaluation

Exact sources, revisions, permission checks, and atomic receipts constrain the
mechanics. They do not prove that a passage was worth remembering, that a quote
preserved enough context, that a plan happened, or that a remembered statement
is still true today. Test those questions with the actual model and host, using
separate synthetic or consented evaluation data. No private conversation is
included in this distribution.
