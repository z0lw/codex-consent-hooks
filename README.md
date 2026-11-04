# codex-consent-hooks

[OpenAI Codex](https://github.com/openai/codex) のフック（hooks）で、エージェントが「同意してよろしいですか」と止まる手間を減らし、依頼が終わっていないのに止まった時は作業を続けさせるためのフック集です。日本語の応答を前提にしています。

English summary: Codex lifecycle hooks that (1) answer routine consent stops (terms of service, checkboxes, bot checks, "may I proceed?") with a standing consent you configure, (2) hold back `request_user_input` questions you have already answered, and (3) send the agent back when it ends a turn with your request unfinished. Money, personal information, physical steps, and your explicit holds always reach you. Detection rules are written for Japanese.

## 3つのフックと目的

| フック | イベント | 目的 |
| --- | --- | --- |
| `consent_stop_hook.py` | Stop | 利用規約への同意・チェック欄・ロボット確認やパズル・操作前の確認だけで止まった時に、事前に設定した常設の同意を返し、エージェント自身に操作を続けさせる |
| `consent_question_hook.py` | PreToolUse（`request_user_input`） | 上と同じ種類の確認や、本人の発言ですでに許可した操作を質問しようとした時に、質問を送らせずに実行させる |
| `completion_guard.py` | Stop | 依頼が終わっていないのに止まった時（成果物がない、「確認します」と宣言しただけ、障害で止まって復旧を試していない、不要な確認待ち、ツール呼び出しが文字として出力された）に、足りないものと次の操作を示して差し戻す |

どのフックも、次の場合は何もせず本人に判断を残します。

- 金銭を伴う契約・支払い（注文、有料の予約や申込、課金など）
- 個人情報の送信（氏名、住所、電話、メールアドレス、送信元アカウントの選択など）
- ケーブルの接続や指紋認証など、本人しかできない物理的な操作
- 本人が「待って」「送信しないで」「確認させて」などと止めた操作
- 会話記録を読めず、本人の発言を確認できない時

自動の同意と差し戻しは、合わせて1ターンにつき既定で3回までです。同意フックが送る文には `[consent-hooks]`、完了確認の差し戻しには `[completion-check]` の目印が付き、本人の入力と区別できます。

## 必要なもの

- Codex CLI またはアプリ（フック機能が有効なもの。`codex features list` で `hooks` が `true`）
- Python 3.9 以上（標準ライブラリのみ）
- macOS または Linux
- 完了確認フックと任意の判定AIは `codex exec` でモデルを呼ぶため、そのぶんの利用量と数秒〜数十秒の待ち時間がかかります

## 導入

```sh
git clone https://github.com/z0lw/codex-consent-hooks.git
cd codex-consent-hooks
python3 install.py --dry-run   # 書き込む内容を確認
python3 install.py
```

`install.py` は `~/.codex/hooks.json`（`CODEX_HOME` があればその下）に3つのフックを追加します。既存のフックは残し、変更前にバックアップを作ります。設定ファイル `~/.codex/consent-hooks.json` がなければ `config.example.json` から作ります。

その後 Codex を起動し直し、新しいフックの確認が表示されたら内容を見て有効にしてください。Codex は承認されていないフックを実行しません。

- 完了確認フックを入れない: `python3 install.py --without-completion-check`
- 取り外す: `python3 install.py --uninstall`
- 手で書く場合は `hooks.example.json` を参考にしてください

## 設定

`~/.codex/consent-hooks.json`（環境変数 `CODEX_CONSENT_HOOKS_CONFIG` で場所を変更可能）:

| 項目 | 既定 | 内容 |
| --- | --- | --- |
| `max_resumes_per_turn` | 3 | 1ターンで自動の同意・差し戻しを行う上限 |
| `consent_message` | 丁寧な同意文 | 同意フックが送る文。`{asked}` は止まった確認文に置き換わる |
| `judge.enabled` | false | 同意フック・質問フックでも判定AIを使う。言い換えた金銭・個人情報の確認を意味で見分け、本人の発言による許可（引用が本人の発言と一致する時だけ）を認める |
| `judge.model` | null | 判定に使うモデル。null なら Codex の既定 |
| `judge.codex_path` | `codex` | `codex` コマンドの場所 |
| `judge.ignore_user_config` | true | 判定時に `config.toml` を読まない。独自のプロバイダ設定が必要な場合は false |
| `judge.extra_args` | [] | `codex exec` に追加する引数 |
| `completion_check.enabled` | true | 完了確認フックを動かす |
| `completion_check.resume_on_indeterminate` | true | 完了したか判定できない時も一度確認を促す |

判定AIを呼ぶ `codex exec` には環境変数 `CODEX_CONSENT_HOOKS_ACTIVE=1` を渡し、その中でフックが再び動かないようにしています。判定に送る文面からは、メールアドレス・電話番号・郵便番号・トークン類を伏せ字にしています。

## 記録

`~/.codex/consent-hooks/` に、1ターンの回数を数えるファイルと、イベント名だけの記録（`events.log`）を置きます。会話の本文は保存しません。

## 試験

```sh
python3 -m unittest discover -s tests -v
```

実機では、Codex CLI 0.153 で次を確認しています。

- 「利用規約に同意していただけますか。」で止まった応答に同意文が届き、エージェントが続行した
- 「行数をこれから確認します。」とだけ書いて止まった応答が差し戻され、エージェントが実際に数えて報告した。完了後の停止では何もしなかった

## 注意

- 確認文の検出は日本語の言い回しに合わせた正規表現です。英語の応答では同意フックはほぼ反応しません
- 同意フックは、あなたの代わりに一般的な利用規約へ同意する操作を進めさせます。自動で同意してよい範囲を `consent_message` と運用で決めてから使ってください
- Stop フックの続行は Codex の標準の仕組み（`decision: block`）で届くため、アプリではフックからのメッセージとして表示されます

## License

MIT
