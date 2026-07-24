"""既存記事の再分析スクリプト（手動実行のみ）。

対象記事: manually_edited=false のうち、
  - summary_error=true
  - all_entities が空
  - summary が200字未満
のいずれかに該当する記事。

実行方法:
    python scripts/reanalyze.py
    # 少量データでのAPI動作確認（トークン節約用）:
    NEWS_FILE=scripts/test_data/news.sample.json GEMINI_API_KEY=xxx python scripts/reanalyze.py
環境変数:
    GEMINI_API_KEY  Gemini APIキー（未設定の場合はキーワードフォールバックのみで動作）
    NEWS_FILE       news.jsonのパスを上書き（既定: docs/data/news.json）
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyzer import analyze_article, extract_article_html  # noqa: E402

JST = timezone(timedelta(hours=9))

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_FILE = os.environ.get("NEWS_FILE") or os.path.join(BASE_DIR, "docs", "data", "news.json")
REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NewsCollectorBot/1.0)"}


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


def main():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("[info] GEMINI_API_KEY not set; using keyword fallback for all articles")

    news_db = load_json(DATA_FILE, {"articles": [], "last_updated": None, "total_count": 0, "sources": []})
    targets = [a for a in news_db["articles"] if needs_reanalysis(a)]
    print(f"[info] {len(targets)} article(s) need reanalysis")

    updated_count = 0
    for article in targets:
        body_text = fetch_article_body(article["link"])
        analysis = analyze_article(article["title"], body_text or article.get("description", ""), api_key=api_key)

        article["summary"] = analysis["summary"]
        article["summary_error"] = analysis["summary_error"]
        article["category"] = analysis["category"]
        article["all_entities"] = analysis["all_entities"]
        article["main_entities"] = analysis["main_entities"]
        updated_count += 1
        time.sleep(4)  # Gemini APIの無料枠レート制限（1分あたりのリクエスト数）に配慮

    news_db["last_updated"] = datetime.now(JST).isoformat()
    save_json(DATA_FILE, news_db)
    print(f"[info] done. reanalyzed {updated_count} article(s)")


if __name__ == "__main__":
    main()
