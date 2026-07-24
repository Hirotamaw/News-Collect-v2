"""暗号資産ニュースの自動収集・分析スクリプト。
RSSから記事を取得し、Gemini APIで要約・カテゴリ分類・企業名抽出を行い、
docs/data/news.json と docs/data/entities.json を更新する。

実行方法:
    python scripts/fetch_news.py
環境変数:
    GEMINI_API_KEY  Gemini APIキー（未設定の場合はキーワードフォールバックのみで動作）
    FETCH_MODE      "24h"（既定）または "today"
    NEWS_FILE       news.jsonのパスを上書き（既定: docs/data/news.json）
    ENTITIES_FILE   entities.jsonのパスを上書き（既定: docs/data/entities.json）
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import feedparser
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyzer import analyze_article, clean_text, extract_article_html  # noqa: E402

JST = timezone(timedelta(hours=9))

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "docs", "data")
NEWS_FILE = os.environ.get("NEWS_FILE") or os.path.join(DATA_DIR, "news.json")
ENTITIES_FILE = os.environ.get("ENTITIES_FILE") or os.path.join(DATA_DIR, "entities.json")

SOURCES = {
    "NADA NEWS": {"url": "https://www.nadanews.com/feed/", "color": "#0f6e56"},
    "CoinPost": {"url": "https://coinpost.jp/?feed=rss2", "color": "#1d4ed8"},
    "あたらしい経済": {"url": "https://www.neweconomy.jp/feed", "color": "#7c3aed"},
    # CoinTelegraph JP は現在サイトが 410 Gone を返しており取得できない。
    # URLが復活した場合はここを更新するだけで有効化できる。
    "CoinTelegraph JP": {"url": "https://jp.cointelegraph.com/rss/", "color": "#b45309"},
}

REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NewsCollectorBot/1.0)"}
MAX_DESCRIPTION_LEN = 1200


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


def parse_pub_date(entry):
    for key in ("published_parsed", "updated_parsed"):
        struct = entry.get(key)
        if struct:
            dt_utc = datetime(*struct[:6], tzinfo=timezone.utc)
            return dt_utc.astimezone(JST)
    return datetime.now(JST)


def fetch_source_entries(source_name, source_conf):
    try:
        resp = requests.get(source_conf["url"], headers=REQUEST_HEADERS, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"[warn] failed to fetch RSS for {source_name}: {type(exc).__name__}")
        return []

    parsed = feedparser.parse(resp.content)
    if parsed.bozo and not parsed.entries:
        print(f"[warn] failed to parse RSS for {source_name}: {type(parsed.bozo_exception).__name__}")
        return []

    entries = []
    for entry in parsed.entries:
        link = entry.get("link")
        title = clean_text(entry.get("title", ""))
        if not link or not title:
            continue
        description = clean_text(entry.get("summary", "") or entry.get("description", ""))[:MAX_DESCRIPTION_LEN]
        pub_date = parse_pub_date(entry)
        entries.append(
            {
                "title": title,
                "link": link,
                "pub_date": pub_date,
                "description": description,
                "source_name": source_name,
                "source_color": source_conf["color"],
            }
        )
    return entries


def within_window(pub_date, mode, now):
    if mode == "today":
        today_9am = now.replace(hour=9, minute=0, second=0, microsecond=0)
        if now < today_9am:
            today_9am -= timedelta(days=1)
        return pub_date >= today_9am
    return pub_date >= now - timedelta(hours=24)


def fetch_article_body(url):
    try:
        resp = requests.get(url, headers=REQUEST_HEADERS, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"[warn] failed to fetch article body: {type(exc).__name__}")
        return ""
    return extract_article_html(resp.text)


def update_entities_db(entities_db, article):
    now_iso = article["fetched_at"]
    for name in article.get("all_entities", []):
        record = entities_db["entities"].setdefault(
            name,
            {
                "article_count": 0,
                "as_main_count": 0,
                "as_related_count": 0,
                "recent_articles": [],
                "first_seen": now_iso,
                "last_seen": now_iso,
            },
        )
        record["article_count"] += 1
        if name in article.get("main_entities", []):
            record["as_main_count"] += 1
        else:
            record["as_related_count"] += 1
        record["last_seen"] = now_iso
        record["recent_articles"].insert(
            0, {"title": article["title"], "link": article["link"], "pub_date": article["pub_date"]}
        )
        record["recent_articles"] = record["recent_articles"][:5]


def main():
    mode = os.environ.get("FETCH_MODE", "24h")
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("[info] GEMINI_API_KEY not set; using keyword fallback for all articles")

    news_db = load_json(
        NEWS_FILE,
        {"articles": [], "last_updated": None, "total_count": 0, "sources": list(SOURCES.keys())},
    )
    entities_db = load_json(ENTITIES_FILE, {"entities": {}})

    existing_links = {a["link"] for a in news_db["articles"]}
    now = datetime.now(JST)

    new_articles = []
    seen_links = set()
    for source_name, source_conf in SOURCES.items():
        entries = fetch_source_entries(source_name, source_conf)
        print(f"[info] {source_name}: {len(entries)} entries fetched from RSS")
        for entry in entries:
            link = entry["link"]
            if link in existing_links or link in seen_links:
                continue
            if not within_window(entry["pub_date"], mode, now):
                continue
            seen_links.add(link)
            new_articles.append(entry)

    print(f"[info] {len(new_articles)} new article(s) to analyze")

    for entry in new_articles:
        body_text = fetch_article_body(entry["link"])
        analysis = analyze_article(entry["title"], body_text or entry["description"], api_key=api_key)
        fetched_at = datetime.now(JST).isoformat()

        article = {
            "title": entry["title"],
            "link": entry["link"],
            "pub_date": entry["pub_date"].isoformat(),
            "description": entry["description"],
            "summary": analysis["summary"],
            "summary_error": analysis["summary_error"],
            "category": analysis["category"],
            "all_entities": analysis["all_entities"],
            "main_entities": analysis["main_entities"],
            "manually_edited": False,
            "source_name": entry["source_name"],
            "source_color": entry["source_color"],
            "fetched_at": fetched_at,
        }
        news_db["articles"].insert(0, article)
        update_entities_db(entities_db, article)
        time.sleep(4)  # Gemini APIの無料枠レート制限（1分あたりのリクエスト数）に配慮

    news_db["articles"].sort(key=lambda a: a["pub_date"], reverse=True)
    news_db["total_count"] = len(news_db["articles"])
    news_db["last_updated"] = datetime.now(JST).isoformat()
    news_db["sources"] = list(SOURCES.keys())

    save_json(NEWS_FILE, news_db)
    save_json(ENTITIES_FILE, entities_db)
    print(f"[info] done. total articles: {news_db['total_count']} (+{len(new_articles)})")


if __name__ == "__main__":
    main()
