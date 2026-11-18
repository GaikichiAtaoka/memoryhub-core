# MemoryHub Core — local source-first memory retrieval

**Source-available / 非商用で利用可能。Commercial use requires separate written permission.**

MemoryHub is a read-only retrieval layer for long-running **local AI** projects.
It preserves source text, timestamps, source IDs and uncertainty markers while
searching dialogue, source-backed summaries and selected canonical facts.

This repository contains **adapted, real MemoryHub core modules** extracted from
a locally developed system, independently packaged and tested with invented
dialogue. It is **not** a complete AI companion, cloud memory service or
plug-and-play replacement for any other memory system.

## ライセンス / License

**PolyForm Noncommercial License 1.0.0** — see [LICENSE](LICENSE) and [NOTICE](NOTICE).

- ✅ 個人的な趣味・学習・非商用の研究での利用、改変、条件に従った再配布。
- ✅ 非商用のローカルAI開発の実験。
- ❗ 商用製品・有償サービス等への利用は、**事前に別途書面での許諾が必要**。
- ❗ 本プロジェクトはソース公開型であり、**OSI認定のオープンソースではありません**。

Commercial licensing inquiries: [COMMERCIAL-LICENSING.md](COMMERCIAL-LICENSING.md).
Contact via a repository issue with no private business details; permission
is **not** granted by merely opening an issue.

## Features

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

## What is and is not included

This package includes **11 adapted core source modules** implementing the
existing retrieval/indexing/temporal logic, with project-specific speaker
labels neutralized and local imports converted to a standalone Python package.

It does not include any conversation logs, real user records, personality or
policy state, credentials, model weights, face/voice biometrics, original
Git history, frontend UI, full OSL runtime, or model-owned decision prompt.
See [docs/source-boundary.md](docs/source-boundary.md).

## Verification

```bash
python -m unittest discover -s tests -v
```

All tests use synthetic data. The CI workflow verifies multiple Python versions.
This is **experimental**: the results are not evidence of production-ready
memory accuracy, perfect identity separation or general superiority to other
memory frameworks.
