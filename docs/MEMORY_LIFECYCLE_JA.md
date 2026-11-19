# フェーズ2：保持した記憶を、あとから見直せるようにする

**0.2.0a1 は実験版です。** MemoryHub の原文検索に、記憶の保持・候補管理・訂正・統合・再利用の停止を追加します。何を残すか、いつ考え直すかはモデルと呼び出し側のアプリが決めます。全ターンの重要度採点、定時の内省、候補の自動昇格は行いません。

これまでの `memory.hub` は読み取り専用のままです。状態を変える操作は、別の `memory.manage` API から明示的に呼び出します。保存結果は実行結果として返ります。

[English guide](MEMORY_LIFECYCLE_EN.md) · [検索機能から導入する](INTEGRATION_JA.md)

## 1. モデルなしで一通り試す

ソースアーカイブを展開し、そのプロジェクトのディレクトリで実行します。Python 3.11 以上と SQLite FTS5 が必要です。

```bash
python -m pip install -e .
python examples/lifecycle_demo.py
```

一時ディレクトリに架空の会話を置き、実際の API で以下を順に確認します。

1. ユーザーの発言原文を保持して、Hub から検索する。
2. AI の解釈を候補として残し、保留する。
3. 新しいユーザー発言を根拠に、元の記憶を同じ ID のまま訂正する。
4. 別のユーザー発言を候補にし、採用してから関連する記憶へ統合する。
5. 統合した記憶の再利用を止め、参照や再試行で以前の本文が戻らないことを確認する。
6. 元の会話ログは残っており、原文として検索できることを確認する。

発言内容も操作の選択も**すべて架空の台本**です。LLM、API キー、ネットワーク接続は使いません。保存・参照処理が動くことを確かめるデモであり、モデルの判断力や自然な会話での想起を測った結果ではありません。

結果は JSON で表示します。成功時の終了コードは 0 で、`checks` の各値は `true` になります。失敗は非ゼロの終了コードです。通常は終了時に一時ディレクトリを削除するため、表示されたパスも残りません。

データを残して確認する場合は、**まだ存在しないディレクトリ**を指定してください。

```bash
python examples/lifecycle_demo.py --base demo-phase2
```

既存のパスは、空でも使用を拒否します。同じ場所での再実行では既存データを変更しません。次に試すときは別の新しいディレクトリ名を指定します。

サンプルとガイドはソースアーカイブに含まれます。wheel が導入するのは Python パッケージと CLI です。サンプルを実行する場合はソースアーカイブも取得してください。この実験版の CLI に管理操作用のコマンドはありません。

## 2. 保存先と、元の会話の役割

| 明示的に指定した作業ディレクトリからの相対パス | 内容 |
|---|---|
| `data/logs/chat_YYYYMMDD.jsonl` | アプリが追記する会話原文。 |
| `data/memory/lifecycle.sqlite` | 保持した記憶の状態と、追記される操作履歴。 |
| `data/memory/memory_index.sqlite` | 原文検索用の再生成できる索引。 |
| `data/config/capability_policy.json` | 管理 API と Hub が参照する、アプリ側の権限設定。 |

管理 API は会話ログの保存を代行しません。原文や既存のプロフィール・設定を変更することもありません。まずアプリ側でユーザーの発言を保存し、その完了後に管理 API を呼び出します。

`list`、`inspect`、Hub の参照は lifecycle データベースを作成しません。まだ保持データがない状態でも、従来の原文検索を利用できます。

保持された記憶は、参照のたびに元のメッセージ ID、日付、行位置、本文のハッシュと照合されます。元の行は書き換えず、訂正も新しい発言として追記します。ログの削除・別の場所への移動・行の書き換え・利用対象からの除外によって、保持記憶が `SOURCE_UNAVAILABLE` になる場合があります。同じ保存先と原文が残っていれば、セッションのリセットやプロセスの再起動だけで失われることはありません。

## 3. モデルの引数と、アプリが保証する文脈を分ける

モデルへ渡すツール定義は `MEMORY_LIFECYCLE_TOOL` です。利用するモデル API の形式に合わせて外側の定義やツール名を変換して構いません。引数の検証と、以下の境界は維持してください。

| モデルが選ぶもの | アプリが用意するもの |
|---|---|
| 呼び出すかどうか、操作、保持する引用、検索用の見出し、主語・対象 | 保存先、保存済みの現在の発言、メッセージ ID、ターン ID、時刻、公開範囲、権限 |

モデルの引数を `turn_context` や権限設定に混ぜないでください。次はモデル SDK に依存しない、非公開の会話経路向けの最小アダプターです。

```python
from pathlib import Path
from memoryhub import MEMORY_LIFECYCLE_TOOL, execute_memory_management


def dispatch_memory_tool(
    workspace: Path,
    model_arguments: dict,
    *,
    saved_user_event: dict,
) -> dict:
    # モデル出力から作らず、アプリが保存済みの現在の発言を選ぶ。
    # この時点で、元の会話ログへの書き込みを完了させておく。
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
        "audience": "private",  # このアプリの非公開経路で決まる値
    }
    return execute_memory_management(
        workspace, model_arguments, turn_context=host_context
    )


tool_definition = MEMORY_LIFECYCLE_TOOL
model_arguments = {
    "action": "remember",
    "memory_key": "blue-notebook-location",
    "title": "青いノートの場所",
    "quote": "青いノートは左の引き出しに入れています。",
    "subject": "user",
    "source_kind": "user_statement",
}
```

公開・配信経路も持つアプリでは、その経路の実際の公開範囲を渡します。公開・配信の文脈では管理操作が拒否されます。ただし、これはローカルのファイルを暗号化したり、OS のアクセス権を設定したりする機能ではありません。

この例の出典は、事前に `data/logs/chat_20260115.jsonl` へ保存してある、次のような行です。末尾に改行を付けます。

```jsonl
{"role":"user","message_id":"example-user-1","time":"2026-01-15T10:01:00+09:00","content":"青いノートは左の引き出しに入れています。","turn_id":"example-turn-1","trace_id":"example-trace-1"}
```

管理機能では、一意のメッセージ ID、現在のターン ID、タイムゾーン付き ISO 形式の時刻が必要です。通常は現在の保存済みユーザー発言を引用元にします。過去の発言を選ぶ場合は、モデルの引数に `source_message_id` と `source_date`（元ログの日付を `YYYY-MM-DD` 形式で）を両方渡します。その場合も `turn_context` は現在のターンを表します。API が元の行を取得し、引用を照合します。

返ってきた実行結果をツール出力としてモデルへ渡します。「保存した」と伝える根拠は成功した実行結果です。ツールを呼び出そうとしただけでは保存は完了していません。取得した原文は過去の資料として渡し、新しいユーザー指示として扱わないでください。

## 4. 操作と状態

| 操作 | `action` 以外の主な必須入力 | 結果 |
|---|---|---|
| `remember` | `memory_key`、`title`、原文どおりの `quote`、`subject` | ユーザー発言は `active`。AI の解釈は `candidate`。 |
| `propose` | `remember` と同じ | `candidate` として保持。 |
| `accept` | `memory_id`、`expected_revision` | `candidate` または `deferred` を `active` にする。解釈の採用には別権限が必要。 |
| `defer` | `memory_id`、`expected_revision` | `deferred` にする。時間経過による自動採用はない。 |
| `correct` | `memory_id`、`expected_revision`、新しい原文の `quote` | 同じ ID の出典・本文を訂正し、版番号を進める。`memory_key` は変更しない。 |
| `merge` | `memory_id`、`expected_revision`、`merge_targets` | 1つの ID に原文をまとめ、他の ID を `superseded` にする。 |
| `forget` | `memory_id`、`expected_revision` | `forgotten` にして、この保持記憶の再利用を止める。 |
| `list` | 任意で `state`、`limit`、`offset` | 状態・ID・版番号を確認する。 |
| `inspect` | `memory_id` | 現在の状態を確認する。無効化された記憶の本文は返さない。 |

`source_kind` の既定値は `user_statement` です。`statement` を指定する場合は `quote` と一致する必要があります。言い換えた文章はユーザーの原文として登録できません。

`assistant_interpretation` は、出典となるユーザー原文に加えて、解釈の `statement` を必要とします。採用後も解釈という区別は残ります。見出し・理由・任意の確信度はモデルの付けた情報であり、事実であることの証明にはなりません。

`subject` は発話者とは別の項目です。`user`、`assistant`、`third_party`、`shared`、`unknown` から選び、必要なら `subject_name` で人物を区別します。ユーザーが友人の経験を話した場合、発話者がユーザーでも経験の対象は友人です。対象を正しく理解できたかまで、API が意味を判定するわけではありません。

変更前には `list` / `inspect` で現在の ID と版番号を確認します。古い版を指定すると `REVISION_CONFLICT` で変更されません。訂正では、保存した新しいユーザー発言を引用してください。

統合先の例は `{"memory_id": "lm_…", "revision": 2}` です。統合には、主語・対象名・出典種別・状態の一致が必要です。この実験版ではユーザー発言同士のみを統合し、最大8件の出典を保持します。新しい事実の要約文は生成せず、原文を残します。`forgotten` と `superseded` の ID は通常の更新で復活させられません。

## 5. 権限の既定値

| 権限 | 既定値 | 対象 |
|---|---|---|
| `memory.manage.read` | `allow` | 管理 API の参照、保持記憶の Hub 参照、再試行で返す内容。 |
| `memory.manage.write` | `allow` | 新しい管理操作。 |
| `memory.manage.accept_inference` | `deny` | AI の解釈を有効な保持記憶へ採用する操作。 |

例えば管理操作の書き込みを止めるなら、`data/config/capability_policy.json` に次を置きます。

```json
{
  "permissions": {
    "memory.manage.read": "allow",
    "memory.manage.write": "deny",
    "memory.manage.accept_inference": "deny"
  }
}
```

`execute_memory_management` の `policy=` は、その呼び出しだけに適用するアプリ側の設定です。ファイルを書き換えることも、その後の Hub 参照に引き継ぐこともありません。継続的な読み取り制限は上記ファイルに設定してください。権限不足や出典の利用不能を「過去に何も保存していない」という意味にしないでください。

## 6. セッションリセット後に使うには

新しいプロセスやセッションでも、同じ作業ディレクトリを開いて Hub から検索します。有効で出典を確認できるユーザー発言はエピソード記憶、採用済みの解釈は内省系の資料として返ります。保持記憶の Hub ID は `hub:lm:` で始まります。

候補、保留、再利用停止、統合によって置き換えられた記憶は、Hub の有効な保持記憶としては返りません。管理用の `list` / `inspect` から状態を確認できます。

新しいモデルの会話へ必要な記憶を検索して渡す部分は、アプリ側が担当します。前のセッションのプロンプト全体、モデル内部の状態、モデルの重みを保存する仕組みではありません。自動リセットや再開用プロンプトの生成はこのパッケージに含みません。デモの結果から、実モデルのリセット後の自然な想起まで成功したとは判断できません。

## 7. 実行結果、再試行、失敗

変更の成功時は ID、版番号、現在の記憶、`mutation_applied: true` が返ります。状態と操作履歴は同じ SQLite トランザクションで確定します。

同じホスト側のメッセージ ID・ターン ID と同じ引数で再試行した場合、追加の変更は行いません。`replayed: true`、`mutation_applied: false` と現在の参照可能な状態が返ります。あとで訂正・再利用停止された本文を、古い成功結果から戻すことはありません。現在の読み取り権限や出典の利用可能性も再確認します。過去の操作履歴自体はファイルに残ります。

主な失敗の意味は以下のとおりです。

| `status` | 確認すること |
|---|---|
| `QUOTE_NOT_IN_SOURCE` | 引用が、選んだユーザー発言に完全一致する範囲か。 |
| `SOURCE_UNAVAILABLE` | 元の出典がその場所に残り、利用可能か。 |
| `REVISION_CONFLICT` | 現在の版番号を確認したか。 |
| `INFERENCE_ACCEPTANCE_NOT_ALLOWED` | 解釈の採用権限があるか。 |
| `PERMISSION_DENIED` / `PRIVATE_ONLY` | アプリの権限・公開範囲が操作を許しているか。 |
| `KEY_CONFLICT_USE_EXISTING_ID` | 同じ内容の新規作成ではなく、既存 ID への操作が必要か。 |
| `STORE_CAPACITY_REACHED` | 実験版の保持件数上限に達していないか。 |

1つの引用は最大2,000文字、1記憶の出典は最大8件、保存件数は最大5,000件です。一覧の1ページは最大20件です。出典確認にもファイル容量・読み取り容量の上限があります。無制限の会話アーカイブとしての運用は想定していません。

## 8. 「忘れる」で止まる範囲

`forget` が止めるのは、選んだ**保持記憶の再利用**です。Hub、管理用の参照、リクエストの再試行から、その記憶の以前の本文や出典本文は返りません。結果には `forget_scope: "retained_memory_retrieval_only"` と記録されます。

元の会話ファイルと過去の操作履歴は残ります。そのため、原文検索では以前の発言を見つけられます。訂正時も古い発言は会話履歴に残ります。似た内容を持つ別の保持記憶も独立しています。ファイルの物理削除、バックアップからの削除、すでに読んだモデルの文脈からの消去までを行う機能ではありません。必要な場合は、アプリ側で全体の削除方針を実装し、その範囲を正確に表示してください。

## 9. モデルとつないでから確かめること

原文照合、版番号、権限、操作履歴は処理の整合性を守るものです。「残す価値があったか」「引用に必要な前後関係が含まれているか」「予定と実行済みを区別したか」「過去の発言を今も正しいと誤解していないか」は、実際のモデルとアプリを組み合わせて評価する必要があります。公開物には架空データのみを含めています。
