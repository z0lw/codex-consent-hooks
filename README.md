# codex-consent-hooks

[OpenAI Codex](https://github.com/openai/codex) 用のフック集です。日本語で使う前提で作っています。エージェントが確認待ちで止まる手間を減らし、依頼が終わっていないのに止まった時は作業を続けさせます。あわせて、応答の表現、自動実行の時刻、サイドバーの整理、AGENTS.md の履歴を見守ります。

English summary: Codex lifecycle hooks (Japanese-oriented) that answer routine consent stops with a standing consent, hold back questions you already answered, send unfinished turns back, flag phrases you chose not to use, show the saved next run of scheduled automations, keep desktop sidebar sections up to date (with /p1-/p5 commands), and auto-commit AGENTS.md.

## Codex に導入を頼む場合

下の文をコピーして Codex に貼り付けてください。下の「導入手順（Codex 向け）」に沿って導入できます。

```text
https://github.com/z0lw/codex-consent-hooks のフックを、READMEの導入手順に沿って導入してください。
```

## フックと目的

| 名前 | ファイル | イベント | 目的 |
| --- | --- | --- | --- |
| consent | `consent_stop_hook.py` | Stop | 利用規約への同意・チェック欄・ロボット確認やパズル・操作前の確認だけで止まった時に、事前に設定した常設の同意を返し、エージェント自身に操作を続けさせる |
| question | `consent_question_hook.py` | PreToolUse（`request_user_input`） | 上と同じ種類の確認や、本人の発言ですでに許可した操作を質問しようとした時に、質問を送らせずに実行させる |
| completion | `completion_guard.py` | Stop | 依頼が終わっていないのに止まった時（成果物がない、「確認します」と宣言しただけ、障害で止まって復旧を試していない、不要な確認待ち、ツール呼び出しが文字として出力された）に、足りないものと次の操作を示して差し戻す |
| language | `language_check.py` | Stop | 使わないと決めた表現（設定で指定）が最終応答にあれば、その文だけ言い換えて回答全体を出し直させる。「〜べきでした」のように文脈で判断が要る規則は判定AIが読む |
| schedule | `schedule_check.py` | PostToolUse・Stop | 自動実行を作成・変更した時に、スケジューラーが保存した次回の実行時刻を指定のタイムゾーンで示し、意図と一致するか確かめさせる（時差で数時間ずれる失敗を防ぐ） |
| sidebar | `sidebar_sections.py` と `prompts/p*.md` | UserPromptSubmit・Stop | デスクトップアプリのサイドバーで、会話を「進行中」「待ち」へ自動で振り分ける。`/p1`〜`/p5` で手動移動もできる（下記） |
| autocommit | `autocommit_agents_md.py` | PostToolUse | AGENTS.md（エージェントへの指示・メモリ）の変更を自動で Git にコミットし、意図しない変更を見直して戻せるようにする |

consent・question・completion は、次の場合は何もせず本人に判断を残します。

- 金銭を伴う契約・支払い（注文、有料の予約や申込、課金など）
- 個人情報の送信（氏名、住所、電話、メールアドレス、送信元アカウントの選択など）
- ケーブルの接続や指紋認証など、本人しかできない物理的な操作
- 本人が「待って」「送信しないで」「確認させて」などと止めた操作
- 会話記録を読めず、本人の発言を確認できない時

自動の同意と差し戻しは、種類ごとに1ターンにつき既定で3回までです。フックが送る文には `[consent-hooks]`、`[completion-check]`、`[language-check]`、`[schedule-check]` の目印が付き、本人の入力と区別できます。

### サイドバーのコマンド

| 入力 | 動作 |
| --- | --- |
| `/p1` | 進行中へ移動 |
| `/p2` | 待ちへ移動 |
| `/p3` | 棚上げへ移動 |
| `/p4` | 完了へ移動 |
| `/p5`、`/p` | セクションから外す |
| `/pinn セクション名` | 名前で指定して移動 |

手で移した会話は、その後の自動の振り分けで上書きされません。ピン留めした会話は動かしません。「棚上げ」「完了」の会話に書き込むと「進行中」へ戻ります。番号の割り当てとセクション名は設定で変えられます。

## 必要なもの

- Codex（フック機能が有効なもの。`codex features list` で `hooks` が `true`）
- Python 3.9 以上（標準ライブラリのみ）、macOS または Linux
- completion・language の文脈判定・sidebar の自動振り分け・任意の判定AIは `codex exec` でモデルを呼ぶため、そのぶんの利用量と数秒〜数十秒の待ち時間がかかります
- schedule と sidebar は Codex デスクトップアプリ（自動実行・カスタムセクションのある版）の内部データを読みます。アプリの版によって形式が変わると、何もしなくなります

## 導入手順（Codex 向け）

1. このリポジトリを利用者が指定した場所（指定がなければ `~/codex-consent-hooks`）へ clone する。
2. `python3 install.py --dry-run` で、既存の `hooks.json` に追加される内容を確認する。不要なフックは `--skip 名前` で外す（名前は上の表のとおり）。
3. `python3 install.py` を実行する。既存のフックは残り、変更前に `hooks.json` のバックアップが作られる。設定ファイル `~/.codex/consent-hooks.json` と、サイドバー用のコマンド `~/.codex/prompts/p.md`〜`p5.md`（同名のファイルがない時だけ）も作られる。
4. サイドバーのセクションを設定する。セクションの ID はアプリごとに違う。次で一覧を取得する。
   ```sh
   sqlite3 ~/.codex/state_5.sqlite "SELECT id, name FROM thread_sections"
   ```
   「進行中」「待ち」という名前のセクションがあれば設定は不要（名前から自動で引く）。名前が違う場合は、利用者に対応を確かめてから `consent-hooks.json` の `sidebar.section_ids.ongoing` と `sidebar.section_ids.waiting` にその ID を書き、`sidebar.numbers` の名前も合わせる。セクションがなければ、利用者にアプリでセクションを作ってもらうか、`--skip sidebar` で外す。
5. ほかの設定も、利用者の希望に合わせて `consent-hooks.json` を直す（下の「設定」）。特に `language_check.rules` は利用者が使わないと決めた表現に置き換える。`autocommit.repos` は、AGENTS.md を Git で管理しているリポジトリを指定する（未指定なら `~/.codex` だけ）。
6. Codex を再起動してもらい、新しいフックの確認が表示されたら内容を見て有効にしてもらう。Codex は承認されていないフックを実行しない。
7. `python3 -m unittest discover -s tests` が通ることを確認する。

取り外しは `python3 install.py --uninstall` です。

## 設定

`~/.codex/consent-hooks.json`（環境変数 `CODEX_CONSENT_HOOKS_CONFIG` で場所を変更可能）。`config.example.json` が全項目の例です。

| 項目 | 既定 | 内容 |
| --- | --- | --- |
| `max_resumes_per_turn` | 3 | 1ターンで自動の同意・差し戻しを行う上限（種類ごと） |
| `delivery` | `block` | 同意文の届け方。下の「同意文の届け方」を参照 |
| `codex_path` | `codex` | `delivery: queue` とサイドバーの移動で使う `codex` コマンドの場所 |
| `consent_message` | 丁寧な同意文 | 同意フックが送る文。`{asked}` は止まった確認文に置き換わる |
| `judge.enabled` | false | consent・question でも判定AIを使う。言い換えた金銭・個人情報の確認を意味で見分け、本人の発言による許可（引用が本人の発言と一致する時だけ）を認める |
| `judge.model` | null | 判定に使うモデル（completion・language・sidebar でも使う）。null なら Codex の既定 |
| `judge.codex_path` | `codex` | 判定に使う `codex` コマンドの場所 |
| `judge.ignore_user_config` | true | 判定時に `config.toml` を読まない。独自のプロバイダ設定が必要な場合は false |
| `judge.extra_args` | [] | `codex exec` に追加する引数 |
| `completion_check.enabled` | true | 完了確認フックを動かす |
| `completion_check.resume_on_indeterminate` | true | 完了したか判定できない時も一度確認を促す |
| `language_check.rules` | 例あり | `pattern`（正規表現）、`label`、`skip_quoted`（引用内を数えない）、`contextual`（判定AIが文脈で判断） |
| `language_check.extra_instruction` | 例あり | 差し戻しの文の末尾に足す指示 |
| `schedule_check.timezone` | `Asia/Tokyo` | 時刻を示すタイムゾーン。null ならシステムの設定 |
| `sidebar.section_ids` | 空 | 自動振り分け先の `ongoing`・`waiting` のセクション ID。空なら `ongoing_name`・`waiting_name` の名前から引く |
| `sidebar.numbers` | 1=進行中 … 5=外す | `/p1`〜`/p5` の番号とセクション名 |
| `autocommit.repos` | `~/.codex` | `{"path": ..., "file": "AGENTS.md", "message": ...}` の一覧 |

各フックは `enabled: false` で止められます。判定AIを呼ぶ `codex exec` には環境変数 `CODEX_CONSENT_HOOKS_ACTIVE=1` を渡し、その中でフックが再び動かないようにしています。判定に送る文面からは、メールアドレス・電話番号・郵便番号・トークン類を伏せ字にしています。

### 同意文の届け方

- `"delivery": "block"`（既定）: Stop フックの標準の仕組み（`decision: block`）で、そのターンを続行させます。どの環境でも動きますが、アプリではフックからのメッセージとして表示されます。
- `"delivery": "queue"`: 同意文を**本人の通常のメッセージとして**会話に届けます。エージェントがフックの枠の文より本人の発言として受け取る方が確実に動く場合や、Codex アプリで開いている会話に届けたい場合に使います。
  1. `codex queue --thread <会話ID> --resume --message <同意文>` で送ります（`--resume` に対応した Codex が必要です。対応していなければ `block` で届けます）。
  2. 開いている会話や常駐サーバーで再開できなかった時は、送った分を待ち行列から外し、会話記録にある作業フォルダ・権限設定・承認設定・モデルを引き継いで `codex exec resume` で会話を再開して届けます。会話の権限設定が読めない時は、より広い既定の権限で再開せず、待ち行列に残して次に会話を開いた時に届くようにします。
  3. 会話記録に同意文が本人のメッセージとして現れたことを確かめてから、届いたと記録します。失敗した時は外した分を待ち行列へ戻します。

## 記録

`~/.codex/consent-hooks/` に、1ターンの回数を数えるファイル、イベント名だけの記録（`events.log`）、サイドバーと自動コミットの記録を置きます。会話の本文は保存しません。

## 試験

```sh
python3 -m unittest discover -s tests -v
```

実機では次を確認しています。

- consent: 「利用規約に同意していただけますか。」で止まった応答に同意文が届き、エージェントが続行した（`block`、Codex CLI 0.153）。`delivery: queue` では、常駐サーバーのない環境でも同意文が本人のメッセージとして会話に届いて会話が再開し、待ち行列に重複が残らなかった（Codex 0.166）
- completion: 「行数をこれから確認します。」とだけ書いて止まった応答が差し戻され、エージェントが実際に数えて報告した。完了後の停止では何もしなかった
- language: 設定例の謝罪句を含む応答が差し戻され、言い換えた応答で終わった
- schedule: Codex アプリの自動実行の保存済み次回時刻を読み取り、同じ変更は繰り返し表示しなかった
- sidebar: `/p3`・`/p`・`/pinn 待ち` で会話がアプリのセクション間を移動した

## 注意

- 確認文の検出は日本語の言い回しに合わせた正規表現です。英語の応答では consent はほぼ反応しません
- consent は、あなたの代わりに一般的な利用規約へ同意する操作を進めさせます。自動で同意してよい範囲を `consent_message` と運用で決めてから使ってください
- `delivery: queue` の代替経路は、Codex の待ち行列ファイル（`queue_1.sqlite`）から自分が送った1件だけを外します。Codex の内部形式が変わると代替経路は使われず、メッセージは待ち行列に残ります

## License

MIT
