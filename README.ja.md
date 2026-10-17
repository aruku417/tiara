# tiara

**GitHub に見せる前に、PR のブランチをぶつからない状態にする道具。**
機械で決められるぶつかりは AI を使わずに直し、残りだけを、使っているコーディング AI（Claude Code、Codex など）に短く正確に渡します。

[English](README.md)

> **初期版（0.1）で、完成品ではありません。** 自分のリポジトリや進め方に合わせて、あなたとコーディング AI で手を入れて使う「土台」です。[自分の環境に合わせる](#自分の環境に合わせる) を見てください。

AI を何人も並べて1つのリポジトリで動かすと、PR で失う時間の多くはコードを書く時間ではありません。
「PR を出す → "This branch has conflicts" → main を取り込む → AI がファイルごと読んで印を直す → push → CI をやり直す」の往復です。
しかも、そのぶつかりの多くは本当の食い違いではありません。2つの AI が同じファイルの末尾に関数を足した、同じ JSON のリストに別々の項目を足した、といったものです。

tiara は、GitHub と同じマージでぶつかりを予測し、機械的なものは AI を使わずに直します。残ったものだけを、行の範囲、両側のコミットの説明、「両方が同じ行を書き換えた」という注意を付けて AI に渡します。

```text
$ tiara check --prs
conflicts with origin/main in 3 file(s):
  auto    src/routes.ts  (insert-both)
  auto    config/features.json  (structured)
  AGENT   src/auth/session.ts  (same-lines-changed)
run `tiara sync` - the files marked AGENT will be handed to you
if #212 (Add rate limiting) merges first, you will also conflict in:
    auto       docs/api.md  (insert-both)
```

## 入れ方

Python 3.10 以上と git 2.38 以上。ほかに要るものはありません（YAML を構造で混ぜるときだけ PyYAML）。

```bash
pipx install git+https://github.com/aruku417/tiara
```

`tiara check --prs` には、ログイン済みの [GitHub CLI](https://cli.github.com/)（`gh`）も要ります。

## 使い方

```bash
tiara check            # 予測だけ（何も変えない）：GitHub で base とぶつかるか
tiara check --prs      # ほかの開いている PR が先に入ったら、ぶつかるか
tiara sync             # base を取り込み、直せるものを直す。全部直せたらコミット、残れば AI に渡す
tiara verify --commit  # AI が直したあと：印が残っていない、JSON/YAML が壊れていない、を確かめてコミット
tiara verify --cmd "pytest -q" --commit   # コミットの前にテストも流す
```

終了コード：`check` は 0（ぶつからない）・1（tiara が全部直せる）・2（AI が要る）、`sync` は 0 か 2、`verify` は 0 か 3、エラーは 4。

`sync` は、ぶつかるときだけ動きます。GitHub がそのままマージできるブランチには何もしません。コミットしていない変更があると動きません。`git merge --abort` でいつでも元に戻せます。

## tiara が自分で直すもの

git がぶつかりとしたものだけを扱い、どの決まりも、迷ったら推測せずに止めます。

| 決まり | いつ | 結果 |
|---|---|---|
| `line` | 慎重な行マージなら混ぜられる | 混ぜる |
| `insert-both` | 両方が同じ場所に**足しただけ**（関数・テスト・項目） | base 側、続けて自分の側。両方が同じ名前（`def`・`class`・`func`・キーなど）を定義していたら止める |
| `structured` | JSON／YAML で、別々のキー、または `id`・`name`・`key`・`slug` で見分けられるリストの要素 | 値として混ぜる。触っていない JSON は書き方をそのまま残す。コメント付きの YAML は書き直さない |
| `regenerate` | 設定したとき：`regenerate` の glob に合うファイル（生成物） | base 側を残し、作り直すよう伝える |

それ以外（同じ行を別々に変えた、片方が消した、バイナリ）は AI に渡します。

## AI への引き継ぎ

残ったファイルにだけ zdiff3 形式のぶつかりの印を残し、次のような引き継ぎを出します（`.git/tiara/handoff.md` にも保存）。

```markdown
## `src/auth/session.ts` (lines 120-141)
- why: both sides changed the same or adjacent lines
- `origin/main` changed it in: 3fa9c1e Shorten session lifetime to 1h
- this branch changed it in: 81d02b7 Refresh sessions on activity
- caution: in 1 place(s) both sides rewrote the same lines. ...
```

AI はその範囲だけを直し、`tiara verify --commit` を実行します。

## コーディング AI と使う

- **Claude Code**：[`integrations/claude-code/skills/tiara`](integrations/claude-code/skills/tiara) を、リポジトリの `.claude/skills/tiara/`（または `~/.claude/skills/tiara/`）に置きます。PR を出す前に `tiara check` を実行し、引き継ぎに沿って直すようになります。
- **Codex**（など `AGENTS.md` を読む AI）：[`integrations/codex/AGENTS.md`](integrations/codex/AGENTS.md) の中身を、自分の `AGENTS.md` に足します。

tiara 自身は AI を呼ばず、API キーも要らず、どこにも何も送りません。

## 設定

リポジトリの直下に `.tiara.json` を置けます（どのキーも省略できます）。

```json
{
  "base": "origin/main",
  "regenerate": ["dist/*", "*.generated.ts"],
  "id_rules": {"$.jobs.*.steps": ["id", "name"]},
  "insert_both": true
}
```

- `regenerate`：手で混ぜてはいけない生成物。base 側を残し、作り直すよう伝えます。
- `id_rules`：JSON／YAML のリストの要素を、場所ごとにどのキーで見分けるか（既定は `id`・`name`・`key`・`slug`）。
- `insert_both`：足した塊の順番に意味があるコードなら `false` にします。

## 自分の環境に合わせる

tiara は、わざと小さく作っています。リポジトリ・フォルダの構成・ブランチの名前・生成ファイル・テストの方法は、チームごとに違うので、そのまま使うより、合わせて手を入れる前提です。いちばん簡単なのは、AI に頼むことです。

> tiara の README を読んで、このリポジトリ向けに設定して。`.tiara.json`（base のブランチ、生成ファイル、JSON・YAML のリストの項目をどのキーで見分けるか）を書き、skill か AGENTS.md の指示を、あなたが読む場所に置き、`tiara verify --cmd` には実際のテストのコマンドを使って。

合わせることが多いもの：

- **base のブランチとリモート**（`base`）：`origin/main`、`upstream/develop` など
- **生成ファイル**（`regenerate`）：ロックファイル、ビルドの成果物、スナップショット、ツールが作り直す記録など
- **AI への指示を置く場所**：Claude Code ならリポジトリの `.claude/skills/` か `~/.claude/skills/`、Codex ならリポジトリの直下かサブフォルダの `AGENTS.md`。モノレポで決まりが違うなら、パッケージごとに置く
- **テストのコマンド**：AI が `tiara verify --cmd` に渡すもの
- **直す決まりそのもの**：コードは小さい（`src/tiara/resolve.py`）ので、チームで使う形式（ロックファイル、CHANGELOG の書き方、ビルド番号など）の決まりを、自分のフォークに足すのも自然な使い方です

tiara がうまく動かないとき（エラー、対応していない構成）は、AI への指示に従って、AI がふつうの `git merge` に切り替えて自分で直します。何かが失われることはありません。

## どれくらい効くか

2つのアプリのリポジトリで、`main` とぶつかって人が直した過去の PR 47本（ぶつかったファイル 149）を既定の設定で再現し、人が最後にマージした中身と比べました。

- tiara が自分で直したのは **14 ファイル**：8 は人の結果と同じ、3 は足した塊の順番だけが違う、3 は人がマージのときに新しい行も書いていて比べられない。
- **間違って混ぜたファイルは 0。** 残りの 135 ファイルは、短い引き継ぎを付けて AI に渡しました。

効き目は、AI どうしのぶつかり方によります。並んで関数・テスト・リストの項目を足す使い方ほど、よく効きます。

## 背景

Anthropic が、AI が主役の開発のために Git を Scion に置き換えた話（[Redesigning source control at Anthropic](https://www.anthropic.com/engineering/redesigning-source-control)）をきっかけに始めた実験から、ふつうの Git と GitHub の上で今日から使える部分（サーバー側の乗せ直しと構造マージ）を、新しいソース管理の中ではなく PR の前に使えるように取り出したものです。

## ライセンス

MIT
