"""既存記事の再分析スクリプト（手動実行のみ）。

対象記事: manually_edited=false のうち、
  - summary_error=true
  - all_entities が空
  - summary が200字未満
のいずれかに該当する記事。

トークン節約のため、以下の2点を行う。
  - 1回の実行で処理する件数に上限（既定10件）を設け、対象が多くても一部だけ処理する。
    残りは次回の実行で処理される（manually_edited=falseかつ条件に合致する限り対象であり続ける）。
  - 記事を複数件（既定5件）まとめて1回のGemini API呼び出しで分析する（バッチ処理）。
    記事ごとに呼び出す場合に比べ、カテゴリ一覧などの指示文オーバーヘッドを大きく削減できる。

実行方法:
    python scripts/reanalyze.py                      # 既定: 上限10件・5件ずつバッチ処理
    python scripts/reanalyze.py --limit 5             # 1回の実行で処理する件数の上限
    python scripts/reanalyze.py --batch-size 3        # バッチ1回あたりの記事数
    # 少量データでのAPI動作確認（トークン節約用）:
    NEWS_FILE=scripts/test_data/news.sample.json GEMINI_API_KEY=xxx python scripts/reanalyze.py
環境変数:
    GEMINI_API_KEY      Gemini APIキー（未設定の場合はキーワードフォールバックのみで動作）
    NEWS_FILE           news.jsonのパスを上書き（既定: docs/data/news.json）
    REANALYZE_LIMIT     --limit の既定値を上書き（GitHub Actionsのworkflow_dispatch入力用）
    REANALYZE_BATCH_SIZE --batch-size の既定値を上書き
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyzer import analyze_batch, extract_article_html  # noqa: E402

JST = timezone(timedelta(hours=9))

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_FILE = os.environ.get("NEWS_FILE") or os.path.join(BASE_DIR, "docs", "data", "news.json")
REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NewsCollectorBot/1.0)"}

DEFAULT_LIMIT = int(os.environ.get("REANALYZE_LIMIT", "10"))
DEFAULT_BATCH_SIZE = int(os.environ.get("REANALYZE_BATCH_SIZE", "5"))


def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                return default
            return json.loads(content)
    except json.JSONDecodeError:
        print(f"[warn] failed to parse {os.path.basename(path)}; using default")
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def needs_reanalysis(article):
    if article.get("manually_edited"):
        return False
    if article.get("summary_error"):
        return True
    if not article.get("all_entities"):
        return True
    if len(article.get("summary", "")) < 200:
        return True
    return False


def fetch_article_body(url):
    try:
        resp = requests.get(url, headers=REQUEST_HEADERS, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"[warn] failed to fetch article body: {type(exc).__name__}")
        return ""
    return extract_article_html(resp.text)


def chunked(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help=f"1回の実行で処理する件数の上限（既定: {DEFAULT_LIMIT}）")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help=f"バッチ1回あたりの記事数（既定: {DEFAULT_BATCH_SIZE}）")
    args = parser.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("[info] GEMINI_API_KEY not set; using keyword fallback for all articles")

    news_db = load_json(DATA_FILE, {"articles": [], "last_updated": None, "total_count": 0, "sources": []})
    all_targets = [a for a in news_db["articles"] if needs_reanalysis(a)]
    targets = all_targets[: args.limit] if args.limit else all_targets
    remaining = len(all_targets) - len(targets)
    print(
        f"[info] {len(all_targets)} article(s) match reanalysis criteria; "
        f"processing {len(targets)} this run (limit={args.limit}, batch_size={args.batch_size})"
        + (f"; {remaining} will remain for a future run" if remaining > 0 else "")
    )

    updated_count = 0
    for chunk in chunked(targets, args.batch_size):
        items = []
        for i, article in enumerate(chunk):
            body_text = fetch_article_body(article["link"])
            items.append({"id": str(i), "title": article["title"], "body": body_text or article.get("description", "")})

        results = analyze_batch(items, api_key=api_key)

        quota_hit_this_batch = False
        for i, article in enumerate(chunk):
            analysis = results[str(i)]
            if analysis.pop("quota_exhausted", False):
                quota_hit_this_batch = True
            article["summary"] = analysis["summary"]
            article["summary_error"] = analysis["summary_error"]
            article["category"] = analysis["category"]
            article["all_entities"] = analysis["all_entities"]
            article["main_entities"] = analysis["main_entities"]
            updated_count += 1

        if quota_hit_this_batch and api_key:
            print("[warn] Gemini quota exhausted; skipping Gemini for the rest of this run (fallback only)")
            api_key = None

        if api_key:
            time.sleep(4)  # バッチ間でGemini APIの無料枠レート制限に配慮

    news_db["last_updated"] = datetime.now(JST).isoformat()
    save_json(DATA_FILE, news_db)
    print(f"[info] done. reanalyzed {updated_count} article(s)")


if __name__ == "__main__":
    main()
