"""記事本文取得・分析（要約 / 主要企業抽出 / 企業名列挙）を段階ごとに検証するための診断スクリプト。

fetch_news.py / reanalyze.py はパイプライン全体を一括実行して結果をnews.jsonに書き込むだけなので、
どの段階（本文取得 / Gemini呼び出し / フォールバック）で問題が起きているかが分かりにくい。
このスクリプトは記事ごとに各段階の結果を個別に表示し、失敗時は例外の種類とメッセージを明示する。
（APIキーやURLはログに出力しない）

実行方法:
    python scripts/test_analyze.py                     # test_data/news.sample.json の5記事で検証
    python scripts/test_analyze.py --limit 1            # 先頭1件だけ
    python scripts/test_analyze.py --file docs/data/news.json --limit 3
環境変数:
    GEMINI_API_KEY  設定時は実際にGemini APIを呼び出して検証する。未設定ならキーワードフォールバックのみ検証する。
"""
import argparse
import json
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyzer import analyze_article, extract_article_html  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_FILE = os.path.join(BASE_DIR, "scripts", "test_data", "news.sample.json")
REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NewsCollectorBot/1.0)"}


def fetch_body(url):
    resp = requests.get(url, headers=REQUEST_HEADERS, timeout=20)
    resp.raise_for_status()
    return extract_article_html(resp.text)


def run_one(article, api_key):
    print(f"URL: {article['link']}")

    # 1. 本文取得
    body = ""
    try:
        body = fetch_body(article["link"])
        print(f"[1/2 本文取得] OK ({len(body)}字)")
        print(f"  冒頭: {body[:150]}{'...' if len(body) > 150 else ''}")
        if len(body) < 80:
            print("  [警告] 本文が極端に短い。抽出ロジックがこのサイト構造に対応できていない可能性があります。")
    except requests.RequestException as exc:
        print(f"[1/2 本文取得] 失敗: {type(exc).__name__}: {exc}")

    fallback_source = body or article.get("description", "")

    # 2. 分析（analyze_article内でGemini呼び出し・リトライ・失敗時のキーワードフォールバックまで行う。
    #    本番のfetch_news.py/reanalyze.pyと全く同じ経路を通すことで、診断結果が実運用と一致するようにする）
    result = analyze_article(article["title"], fallback_source, api_key=api_key)
    if api_key:
        print(f"[2/2 分析] {'Gemini API 成功' if not result['summary_error'] else 'Gemini API 失敗 → キーワードフォールバック使用（詳細は直前の[warn]行を参照）'}")
    else:
        print("[2/2 分析] GEMINI_API_KEY未設定のためキーワードフォールバックのみ実行")

    print(f"  要約 summary_error={result['summary_error']} ({len(result['summary'])}字):")
    print(f"    {result['summary'][:200]}{'...' if len(result['summary']) > 200 else ''}")
    print(f"  カテゴリ: {result['category']}")
    print(f"  主要企業 main_entities: {result['main_entities']}")
    print(f"  登場企業 all_entities : {result['all_entities']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", default=DEFAULT_FILE, help="検証対象のnews.json（既定: test_data/news.sample.json）")
    parser.add_argument("--limit", type=int, default=None, help="検証する記事数の上限")
    args = parser.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY")
    with open(args.file, encoding="utf-8") as f:
        db = json.load(f)
    articles = db["articles"]
    if args.limit:
        articles = articles[: args.limit]

    print(f"=== {len(articles)}件の記事を検証します (GEMINI_API_KEY: {'設定済み' if api_key else '未設定'}) ===\n")
    for i, article in enumerate(articles, 1):
        print(f"--- [{i}/{len(articles)}] {article['title']} ---")
        run_one(article, api_key)
        print()


if __name__ == "__main__":
    main()
