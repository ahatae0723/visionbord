# GitHub 設定ガイド（非公開リポジトリ・Secrets・自動実行）

このシステムはGitHubの「**Actions**」という無料の自動実行サービスで毎日動きます。
PCの電源が入っていなくても、GitHubのコンピュータが代わりに実行してくれます。

## 全体の流れ

1. データ保管用の**非公開リポジトリ**を用意する（Claudeが作成済みなら飛ばしてOK）
2. アクセストークンを**Secrets**（金庫）に入れる ← 一番大事
3. Actions（自動実行）が有効になっているか確認する
4. 手動で1回実行して動作確認する
5. （おすすめ）GH_PAT を設定して、トークン延長まで完全自動にする

---

## 1. 非公開リポジトリの確認

- GitHub にログインし、データ用リポジトリ（例: `threads-insights`）を開く
- リポジトリ名の横に「**Private**」と表示されていればOK（自分以外は見られません）
- もし「Public」になっていたら: 「Settings」→ 一番下の「Danger Zone」→「Change repository visibility」→「Make private」

## 2. アクセストークンを Secrets に入れる

**Secrets とは？** GitHubの中にある金庫です。ここに入れた文字列は、自動実行のプログラムからは使えますが、画面には表示されず、コードにも残りません。

1. リポジトリの「**Settings**」タブを開く
2. 左メニューの「**Secrets and variables**」→「**Actions**」
3. 緑の「**New repository secret**」ボタンをクリック
4. 次のとおり入力:
   - Name: `THREADS_ACCESS_TOKEN` （←この名前ぴったりで。コピペ推奨）
   - Secret: docs/token_guide.md で取得した長いトークンを貼り付け
5. 「**Add secret**」をクリック

## 3. Actions が有効か確認する

1. リポジトリの「**Actions**」タブを開く
2. 「日次インサイト取得」「週次レポート生成」「トークン自動延長」の3つが左側に見えていればOK
3. もし「Workflows aren't being run on this repository」のような表示が出ていたら、緑のボタンで有効化する

> 📌 **スケジュール実行の注意（GitHubの仕様）**
> - GitHub Actionsの時刻指定はUTC（世界標準時）です。設定ファイルでは日本時間から9時間ずらして指定済みなので、あなたが直すことはありません。
> - 混雑状況により、指定時刻から数分〜数十分遅れることがあります（朝6時が6時20分になる等）。データの中身には影響ありません。
> - リポジトリに**60日間なにも操作がない**と、GitHubが自動実行を一時停止することがあります。その場合はActionsタブに出る「Enable」ボタンを押せば再開します（このシステムは毎日データをコミットするので通常は止まりません）。

## 4. 手動で1回実行して動作確認

1. 「**Actions**」タブ → 左の「**日次インサイト取得**」をクリック
2. 右側の「**Run workflow**」→ 緑の「**Run workflow**」ボタン
3. 1〜2分待って、緑のチェック✅が付けば成功！
4. リポジトリの `data/` フォルダに `account_daily.csv` と `posts.csv` ができているのを確認
5. ❌になった場合は、実行結果をクリックしてログ（記録）を見る。だいたいはトークンの貼り間違いです（Secretsを入れ直して再実行）

## 5. （おすすめ）GH_PAT の設定 — トークン延長まで完全自動にする

Threadsのトークンは約60日で切れますが、毎週の「トークン自動延長」が**新しいトークンをSecretsに自動で書き戻す**ことで、実質無期限になります。この書き戻しには、GitHubを操作するための鍵「**PAT**（Personal Access Token）」が1つ必要です。

1. GitHub右上の自分のアイコン →「**Settings**」（リポジトリのSettingsではなく自分のSettings）
2. 左メニュー一番下の「**Developer settings**」
3. 「**Personal access tokens**」→「**Fine-grained tokens**」→「**Generate new token**」
4. 次のとおり設定:
   - Token name: `threads-insights-refresh` など分かる名前
   - Expiration: 「**Custom**」で1年後の日付（最長）を選ぶ
   - Repository access: 「**Only select repositories**」→ データ用リポジトリ（threads-insights）だけを選ぶ
   - Permissions → Repository permissions →「**Secrets**」を「**Read and write**」にする（他はNo accessのままでOK）
5. 「Generate token」→ 表示された文字列をコピー
6. データ用リポジトリの「Settings」→「Secrets and variables」→「Actions」→「New repository secret」
   - Name: `GH_PAT`
   - Secret: いまコピーした文字列
7. 完了！ 以降は毎週金曜の朝にトークンが自動延長されます

> ⚠️ PAT自体は最長1年で切れます。切れる前にGitHubからメールが届くので、そのときは上の手順でもう一度作って `GH_PAT` を入れ直してください（年1回のメンテナンスです）。

## Macで手元実行したい場合（おまけ）

GitHub Actionsだけで完結するので必須ではありませんが、手元で試したいときは:

```bash
# ターミナル.app で、プロジェクトのフォルダに移動してから
python3 -m venv .venv                 # 初回だけ: Python の作業場所を作る
source .venv/bin/activate             # 作業場所に入る
pip install -r requirements.txt      # 初回だけ: 必要な部品を入れる

cp .env.example .env                  # 初回だけ: 設定ファイルを作る
open -e .env                          # メモ帳で開いてトークンを貼り付けて保存

python scripts/fetch_daily.py         # 日次取得を実行
python scripts/weekly_report.py       # レポートを生成
```
