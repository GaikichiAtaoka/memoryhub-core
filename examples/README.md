# Fully synthetic examples

The runnable [agent integration example](agent_integration.py) shows the full
source-index-sync -> read-only search -> model-facing evidence path.

~~~bash
python -m pip install -e .
python examples/agent_integration.py --query "青い本はもう借りましたか？"
~~~

It runs offline by default. See [the Japanese guide](../docs/INTEGRATION_JA.md)
for your own logs and optional localhost Ollama model usage.


All example source messages are invented for this repository.
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
