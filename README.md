# 暗号資産ニュース自動収集・表示システム

RSSで暗号資産ニュースを自動収集し、Gemini APIで要約・カテゴリ分類・企業名抽出を行い、
Webページで閲覧できるシステム。ホスティングは [Render](https://render.com) の Static Site を使用する。

## 構成

```
scripts/
  fetch_news.py    # 毎日自動実行（RSS取得→分析→docs/data/*.json更新）
  reanalyze.py      # 既存記事の再分析（手動実行のみ）
  analyzer.py       # 共通ロジック（Gemini呼び出し・キーワードフォールバック・企業名抽出）
  test_data/
    news.sample.json  # API動作確認用の5記事サンプル（トークン節約用）
docs/
  index.html         # フロントエンド（Renderが配信する静的ファイル）
  data/
    news.json
    entities.json
.github/workflows/
  fetch-news.yml     # 毎日09:00 JSTに自動実行 → git push
  reanalyze.yml       # 手動実行のみ
render.yaml           # RenderのStatic Site定義（Blueprint）
```

## データパイプラインとホスティングの関係

1. GitHub Actions (`fetch-news.yml`) が毎朝 `scripts/fetch_news.py` を実行し、
   `docs/data/news.json` / `docs/data/entities.json` を更新して git push する。
2. Render の Static Site はこのリポジトリと連携し、`docs/` を公開ディレクトリとして配信する。
   push を検知すると自動的に再デプロイされるため、Render 側で追加のジョブ実行は不要。
3. フロントエンド（`docs/index.html`）の企業名インライン編集機能は、ブラウザから直接
   GitHub Contents API を叩いて `docs/data/news.json` を更新する（GitHub Personal Access Token使用）。
   これにより編集内容も同じ push → 自動デプロイの流れで反映される。

## セットアップ手順

### 1. GitHubリポジトリの準備

```bash
cd news-collector
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/<owner>/<repo>.git
git push -u origin main
```

`Settings > Secrets and variables > Actions` で `GEMINI_API_KEY` を登録する。

### 2. Renderでのホスティング設定

1. [Render](https://dashboard.render.com) にログインし、"New +" → "Blueprint" を選択。
2. 上記でpushしたGitHubリポジトリを連携する（リポジトリ直下の `render.yaml` が自動検出される）。
3. デプロイ後、`docs/index.html` が公開URL（例: `https://news-collector.onrender.com`）で閲覧できる。
4. Blueprintを使わない場合は "New +" → "Static Site" から手動作成し、
   Publish Directory に `docs` を指定、Build Command は空欄のままでよい。

GitHub Actionsが `docs/data/*.json` を更新してpushするたびに、Renderが自動で再デプロイする
（`render.yaml` の `autoDeploy: true` により有効化済み）。

### 3. フロントエンドのGitHub連携設定（企業名編集機能）

Renderで公開されたページ右上の ⚙ ボタンから、`repo` スコープのGitHub Personal Access Token と
`owner/repo` を設定する。トークンはブラウザのlocalStorageにのみ保存され、サーバーには送信されない。

## ローカルでのオフライン動作確認

```bash
pip install -r scripts/requirements.txt
python scripts/fetch_news.py          # GEMINI_API_KEY未設定ならキーワードフォールバックで動作
cd docs && python -m http.server 8899  # http://localhost:8899 で確認
```

## Gemini APIの動作確認（トークン節約）

全記事（数十件）をいきなり実APIで処理するとトークン消費が大きいため、まず
`scripts/test_data/news.sample.json` に選定した5記事だけで動作確認できるようにしている。

```bash
NEWS_FILE=scripts/test_data/news.sample.json GEMINI_API_KEY=xxxxx python scripts/reanalyze.py
```

サンプルの5記事は、ソース・カテゴリ・登場企業数（単一企業／複数企業）にばらつきを持たせて選定した:

| ソース | カテゴリ | タイトル |
|---|---|---|
| NADA NEWS | 暗号資産ETF | 日経新聞に掲載された筆者コメントを解説──日本版ビットコインETF「3兆円試算」の根拠 |
| CoinPost | Stablecoin | コインベース、AIエージェント向け決済機能を発表 x402採用 |
| あたらしい経済 | 障害・攻撃 | ヴェルスのイーサリアムブリッジで約754万ドル流出 |
| NADA NEWS | ビジネス | Strategyやブラックロックなど9社、ビットコイン開発支援へ1500万ドル拠出（複数企業が登場） |
| あたらしい経済 | DeFi | ユニスワップラボ、規制対象資産向け「パーミッションドプール」発表 |

結果を確認してから、本番の `docs/data/news.json` に対して同様に実行する（`NEWS_FILE` を指定しなければ既定パスが使われる）。

## RSSソースについて

`CoinTelegraph JP` (`jp.cointelegraph.com`) は現在サイトが「410 Gone」を返しており取得できないため、
`scripts/fetch_news.py` の `SOURCES` にURL設定は残したまま、実質的に3メディア（NADA NEWS / CoinPost / あたらしい経済）で運用している。
取得に失敗しても他のソースの処理は継続される。復旧時は `SOURCES["CoinTelegraph JP"]["url"]` を更新するだけでよい。
