# MemoryHub Core を自分のローカルAIに組み込む

このガイドは、初めて使う開発者が「架空データで起動 → 自分のローカル会話データで検索 → AIに参照させる」まで進むためのものです。実際に動くサンプルは [examples/agent_integration.py](../examples/agent_integration.py) にあります。

MemoryHub Coreは**検索・出典提示を行う部品**です。チャットUI、LLM、会話ログの保存処理、どのタイミングで記憶検索するかを決めるエージェントは利用側で実装します。デフォルトでクラウドへ会話を送信しません。

## 1. まず架空データで試す

Python 3.11以上と、SQLite FTS5を利用できるPythonが必要です。リポジトリのルートで実行してください。

~~~bash
python -m pip install -e .
python -m memoryhub --base examples/synthetic_workspace index
python -m memoryhub --base examples/synthetic_workspace search "図書館"
python examples/agent_integration.py --query "青い本はもう借りましたか？"
~~~

最後のコマンドはオフラインで、検索結果と**AIに渡すメッセージ**を表示します。LLMには接続しません。自分のパソコンでOllamaが動いていて、モデルを導入済みなら以下でも試せます。

~~~bash
python examples/agent_integration.py --query "青い本はもう借りましたか？" --model YOUR_INSTALLED_MODEL
~~~

この場合のみ、サンプルはローカルの Ollama API（127.0.0.1:11434）に質問と検索根拠を送ります。モデルは自動ダウンロードされません。架空データの「借りる予定」と「まだ借りていない」という訂正を区別できるかを観察してください。

## 2. 自分の会話ログを置く

明示的に指定した作業用ディレクトリの中へ、次の構成で用意します。

~~~text
my_agent/
  data/
    logs/
      chat_20261010.jsonl
      chat_20261011.jsonl
    memory/
      (検索インデックスが生成される)
~~~

1行に1件のJSONオブジェクトを書き、末尾に改行を付けます。以下は**完全に架空**のデータです。

~~~jsonl
{"role":"user","time":"2026-10-10T21:15:00+09:00","content":"明日は図書館で青い本を借りる予定です。","message_id":"example_u_001"}
{"role":"assistant","time":"2026-10-10T21:15:08+09:00","content":"予定を確認しました。","message_id":"example_a_001"}
~~~

各フィールドの意味：

- `role`：話者。通常は `user` または `assistant`。
- `time`：会話が記録された日時。タイムゾーン付きISO 8601形式を推奨。
- `content`：発話原文。後で修正しないよう、事実訂正は新しい発話として記録します。
- `message_id`：同じ発言を再識別するための一意なID。推奨。
- `is_test`：テスト会話の場合は `true`。通常のユーザー記憶へ混ぜないための出典情報です。

ファイル名の日付は実際の**記録日**と合わせてください。昔の予定は、その日の出来事が実行された証拠にはなりません。ログをGitHubにアップロードしないでください。

## 3. 検索インデックスを作成・更新する

~~~bash
python -m memoryhub --base my_agent index
python -m memoryhub --base my_agent search "図書館"
~~~

`index` は `my_agent/data/memory/memory_index.sqlite` という**派生SQLiteインデックス**を作ります。元の会話ログを書き換えません。

アプリ側で会話を書き込んだ後は、**ログへの追記とフラッシュが完了してから**インクリメンタル更新します。

~~~python
from pathlib import Path
from memoryhub import index_memory_sources

root = Path("my_agent")
sync = index_memory_sources(root, incremental=True)
if not sync["ok"]:
    raise RuntimeError(sync["status"])
~~~

1ターンごとでも、複数ターンまとめてでも構いません。常に全部再構築する必要はありません。APIでの `execute_memory_hub` 検索自体は、保存や索引更新を実行しません。更新に失敗した際は、結果を「記憶がない」と解釈せず、障害を確認してください。

## 4. 自分のAIから検索する

実装の基本は「AIが検索の必要性を判断 → アプリが検索APIを実行 → 結果を歴史的資料としてAIへ返す」です。通常の会話を毎回強制的に検索する必要はありません。

~~~python
from pathlib import Path
from memoryhub import HUB_TOOL, execute_memory_hub, render_memory_hub_context

root = Path("my_agent")
# HUB_TOOL を、利用するLLMの tool/function-calling 定義へ登録できる。
# モデルが返した引数をアプリ側で検証してから実行する。
result = execute_memory_hub(
    action="search",
    base_dir=root,
    query="青い本",
    scope="episodic",
    limit=5,
)
if result["ok"]:
    memory_evidence = render_memory_hub_context(result)
    # AIの次の応答に「システムが取得した歴史資料」として渡す。
else:
    memory_evidence = f"MemoryHub unavailable: {result['status']}"
~~~

`HUB_TOOL` は read-only な `memory.hub` ツールのスキーマです。モデルからの `action`、`query`、`scope`、`date`、`memory_id` などの入力は、利用側で型・許容値を検証した後に `execute_memory_hub` へ渡してください。得られた `render_memory_hub_context` は**モデルへの資料**です。過去の会話を新しい `role=user` の発言や権限のある指示として渡さないでください。

デモプログラムは、読みやすくするため検索を毎回実行します。実運用の「検索する・しない」判断はエージェントに持たせてください。

## 5. 挙動の理解とトラブル対応

- `search` は候補検索、`chronology` は候補の時系列、`day` は指定記録日のログ、`inspect` は返された正確なIDの原文確認です。
- 検索結果の `status`、`source_health`、`speaker`、`event_time`、`source_file`、`source_locator` を見て、根拠の有無を判断してください。
- `INDEX_UNAVAILABLE` / `INDEX_ERROR` は検索機能・索引の問題であり、「発言が存在しない」という意味ではありません。
- `candidate_count=0` は探索範囲内で一致が見つからなかった状態です。全過去を網羅的に否定する証明にはなりません。
- `recorded_at` や `event_time` は原則**発言記録時刻**です。「昨日」と話した日の前日が実際の行動日かどうかは、原文と追認の有無を比べてください。
- 過去の `assistant` の推測を本人の事実として確定しないでください。
- `--model` を使うと、指定したローカルモデルへ検索資料を渡します。自分の会話ログを使う場合は、ログ出力・端末表示・モデルへの入力のプライバシー管理を利用側で行ってください。

## ライセンス

[PolyForm Noncommercial 1.0.0](../LICENSE)。非商用の範囲と義務を確認してください。商用利用には[別途の許諾](../COMMERCIAL-LICENSING.md)が必要です。本サンプルはPRIVATEな会話やアランOSLの設定を含みません。
