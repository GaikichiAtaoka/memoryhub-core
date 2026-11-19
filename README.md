# MemoryHub Core — Remember, Revisit, Refine

**Give your AI a memory it can revisit—with the original sources still attached.**

MemoryHub connects local AI to past conversations and provides an explicit way
to retain, review, correct, and combine memories. Keep the original words,
who said them, and when they were recorded alongside the AI's interpretation.

**v0.2.0a1 is an experimental preview of Phase 2 memory management.**
The established retrieval interface remains read-only. Applications opt into
changes through a separate `memory.manage` API; a search never decides to save
or rewrite a memory.

[日本語の導入手順](docs/INTEGRATION_JA.md) · [Phase 2 guide](docs/MEMORY_LIFECYCLE_EN.md) · [フェーズ2の使い方](docs/MEMORY_LIFECYCLE_JA.md)

**[Download the v0.2.0a1 preview](https://github.com/GaikichiAtaoka/memoryhub-core/releases/tag/v0.2.0a1)** — source archive, installable wheel, and checksums.

## A memory can change without losing its source

1. **Remember what matters.** Your model chooses a source passage worth retaining, or leaves it alone.
2. **Keep uncertainty visible.** Hold an idea as a candidate, defer it, and keep interpretations distinct from a user's words.
3. **Revisit and refine.** Correct a retained memory or combine related memories using their IDs, revisions, and original sources.
4. **Stop reusing a retained memory.** Exclude it from the retained layer while clearly reporting what still exists in original logs.

An application can reopen the same local workspace in a later process or chat
session and retrieve the saved records. The host decides which results to bring
into the conversation. MemoryHub supplies the storage, source checks, and
operation receipts; it does not choose the model's goals or reflection topics.

## Try the complete lifecycle without a model

Use **Python 3.11+ with SQLite FTS5**. Download and extract the source archive,
open a terminal in its project directory, and run:

```bash
python -m pip install -e .
python examples/lifecycle_demo.py
```

The demo runs in an isolated temporary workspace and removes it when finished.
It prints the actual results of **save → retrieve → defer → correct → accept →
merge → stop reuse**, including source IDs and revisions. Every message and
operation choice is **invented and scripted**. No model, API key, or network is
used, and the output is not a model-quality evaluation.

To inspect the files afterward, choose a **new directory**:

```bash
python examples/lifecycle_demo.py --base demo-phase2
```

An existing path is refused. Choose another new directory when rerunning.
The source archive contains the example scripts and guides. A wheel installs
the Python package and CLI; obtain the source archive as well to run these scripts.

**Source-available / 非商用で利用可能。Commercial use requires separate written permission.**

## ライセンス / License

**PolyForm Noncommercial License 1.0.0** — see [LICENSE](LICENSE) and [NOTICE](NOTICE).

- ✅ 個人的な趣味・学習・非商用の研究での利用、改変、条件に従った再配布。
- ✅ 非商用のローカルAI開発の実験。
- ❗ 商用製品・有償サービス等への利用は、**事前に別途書面での許諾が必要**。
- ❗ 本プロジェクトはソース公開型であり、**OSI認定のオープンソースではありません**。

Commercial licensing inquiries: [COMMERCIAL-LICENSING.md](COMMERCIAL-LICENSING.md).
Contact via a repository issue with no private business details; permission
is **not** granted by merely opening an issue.

## Search the original evidence

- **Episodic:** SQLite FTS5 sidecar index of original dialogue and related logs.
- **Semantic:** explicitly permitted current-fact JSON stores only; policy and
  credentials are excluded.
- **Reflective:** self-reports and agent interpretations remain separate from
  factual statements by the user.
- **Procedural:** explicitly *not connected* (never fabricate a capability).
- **Source-aware tools:** `memory.hub` read-only actions `search`,
  `chronology`, `inspect`, and `day`.
- **Staleness checks:** missing or stale indexes are not silently described as
  absence of memory.
- **Optional temporal-semantic retrieval:** use a local BGE-M3 embedding model
  via Ollama to search nearby diary dates while preserving source references.
  "Yesterday" is anchored to the time of the original utterance and remains
  a **candidate retrieval coordinate**, not evidence an event occurred.
- **Process-local embedding cache:** reduces repeated calls for unchanged
  source sentences; it does not cache personal conversations on disk.

## Manage retained memories explicitly

| Operation | What it does |
|---|---|
| `remember` | Retains an exact source-backed user passage. |
| `propose`, `defer`, `accept` | Keeps candidates and deferred records separate from active retained memories. Accepting an assistant interpretation requires a separate permission. |
| `correct` | Updates the same memory ID using a new source passage and its expected revision. |
| `merge` | Combines compatible user-statement records while preserving their sources. |
| `forget` | Stops reuse of this retained record. Original conversation logs and audit history remain. |
| `list`, `inspect` | Reads lifecycle state, IDs, and revisions without creating a database. |

Active, source-valid retained records can be read through the existing Hub.
The original conversation is still a separate source: correcting or forgetting
a retained record does not remove the earlier utterance from dialogue search.

See the [Phase 2 API and host integration guide](docs/MEMORY_LIFECYCLE_EN.md)
for required inputs, permissions, and the meaning of each state.

## AIへの導入ガイド（日本語）

**[MemoryHub Core を既存AIに接続する手順](docs/INTEGRATION_JA.md)** に、
自分の会話ログの形式、インデックス更新、AIへの検索結果の渡し方をまとめました。

動く最小統合サンプル: [examples/agent_integration.py](examples/agent_integration.py)

~~~bash
python -m pip install -e .
python examples/agent_integration.py --query "青い本はもう借りましたか？"
~~~

この例は**架空の会話データ**で検索して、LLMに渡す根拠付きメッセージをオフラインで表示します。
ローカルOllamaに接続する場合だけ `--model YOUR_INSTALLED_MODEL` を追加します。
自分のデータを使うときは明示的に `--base YOUR_WORKSPACE` を指定してください。
検索するかどうかの判断と、会話の保存処理は呼び出し側のエージェントが担当します。
保持・訂正・統合を接続する場合は、[フェーズ2ガイド](docs/MEMORY_LIFECYCLE_JA.md)へ進めます。

## Run the synthetic example

Python 3.11+ with SQLite FTS5:

```bash
python -m pip install -e .

# Indexes ONLY the explicitly given, fully fabricated sample workspace
python -m memoryhub --base examples/synthetic_workspace index

python -m memoryhub --base examples/synthetic_workspace search "図書館"
python -m memoryhub --base examples/synthetic_workspace chronology "青い本"
python -m memoryhub --base examples/synthetic_workspace day 2026-10-11
```

The index command creates a **disposable derived SQLite file** at
`<base>/data/memory/memory_index.sqlite`. It does not change source dialogue.
Search, inspect, chronology and day are read-only. A missing index requires
an explicit index build; no production memory is modified.

### Optional local BGE-M3

```bash
ollama pull bge-m3
python -m memoryhub --base examples/synthetic_workspace temporal "10月10日は休めた？"
```

BGE-M3 is **optional**, independently obtained and not redistributed here.
The optional embedding adapter contacts only
`http://127.0.0.1:11434/api/embed`. It is not used for the normal search.

## Python API

The existing retrieval API continues to work:

```python
from pathlib import Path
from memoryhub import rebuild_memory_index, execute_memory_hub

root = Path("examples/synthetic_workspace")
rebuild_memory_index(root)  # only derived SQLite cache written
result = execute_memory_hub(
    action="search", base_dir=root, scope="episodic",
    query="図書館", limit=5,
)
print(result["status"], result["results"])
```

The LLM should treat results as **source candidates**, not unquestioned facts.
An assistant's old reply is not automatically evidence of the user's claim.
Plans, uncertain timestamps and disagreements must remain explicitly qualified.

For management, register `MEMORY_LIFECYCLE_TOOL` with your model's tool adapter
and call `execute_memory_management` only when the model selects an operation.
Pass the persisted source message and turn IDs from your host application as
`turn_context`, separately from the model's arguments. The
[integration example](docs/MEMORY_LIFECYCLE_EN.md#connect-your-model-to-the-host)
shows that boundary.

## What is and is not included

This package contains adapted retrieval, indexing, temporal, provenance, and
memory-management modules with generic speaker labels and standalone imports.

It does not include private conversation logs, real user records, personality or
policy state, credentials, model weights, face/voice biometrics, original
Git history, frontend UI, a full assistant runtime, or a model-owned decision prompt.
See [docs/source-boundary.md](docs/source-boundary.md).

## Verification

```bash
python -m unittest discover -s tests -v
```

All tests use synthetic data. The CI workflow verifies multiple Python versions.
This is **experimental**: the results are not evidence of production-ready
memory accuracy, perfect identity separation or general superiority to other
memory frameworks.

The Phase 2 demo checks the mechanics of memory management. Whether a model
chooses a useful passage, preserves the meaning of a qualification, or recalls
the right experience in an everyday conversation needs separate evaluation.
