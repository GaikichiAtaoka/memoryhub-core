# Fully synthetic examples

Run commands from the project directory after extracting the source archive.
The archive contains these scripts; a wheel installs the package and CLI.
All example source messages are invented for this repository.

## Retain, revisit, correct, merge, and stop reuse

The [Phase 2 lifecycle demo](lifecycle_demo.py) uses the real management and Hub
APIs, with scripted choices and invented ordinary-format source events. It has
no model or network dependency:

```bash
python -m pip install -e .
python examples/lifecycle_demo.py
```

By default it creates an isolated temporary workspace and cleans it up after
printing the JSON report. To inspect the source log and SQLite files afterward,
choose a new path:

```bash
python examples/lifecycle_demo.py --base demo-phase2
```

An existing path, including an empty directory, is refused without changing it.
Run at another new path to repeat the example. Exit code 0 means the sequence
and its checks completed successfully; a failure exits nonzero.

The report identifies itself as `invented_scripted_actions`, with
`live_model: false`. It records source IDs, revisions, and checks for retrieval,
correction, candidate handling, merge, and suppression. A deferred interpretation
remains separate from active user statements. The final `forget` suppresses the
merged retained memory, while the original invented dialogue and previous
audit receipts remain. The final check demonstrates that original dialogue can
still be retrieved.

These are API behavior checks, not a measurement of a model choosing what to
remember or recalling it naturally. See the [English guide](../docs/MEMORY_LIFECYCLE_EN.md)
or [日本語ガイド](../docs/MEMORY_LIFECYCLE_JA.md) for host integration and the
exact persistence and deletion boundaries.

## Read-only retrieval and model-facing evidence

The runnable [agent integration example](agent_integration.py) shows the full
source-index-sync -> read-only search -> model-facing evidence path.

~~~bash
python -m pip install -e .
python examples/agent_integration.py --query "青い本はもう借りましたか？"
~~~

It runs offline by default. See [the Japanese guide](../docs/INTEGRATION_JA.md)
for your own logs and optional localhost Ollama model usage.


The example workspace is disposable; it is not compatible with or connected
to a live deployment.

To build an index, run from the project root:

```bash
python -m pip install -e .
python -m memoryhub --base examples/synthetic_workspace index
python -m memoryhub --base examples/synthetic_workspace search "library book"
python -m memoryhub --base examples/synthetic_workspace day 2026-10-10
```

The `index` command may create `data/memory/memory_index.sqlite` under
the explicitly specified workspace only. This file is derived data and must
not be committed. All read-only search actions leave source files unchanged.
