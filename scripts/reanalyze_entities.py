"""企業名タグの品質問題がある記事だけを識別し、それらだけを再分析するスクリプト（手動実行のみ）。

reanalyze.py の対象条件（summary_error等）とは別に、以下のいずれかに該当する記事を対象とする
（manually_edited=false のみ。summary_errorがfalseの「一見成功済み」の記事も対象になる点が
reanalyze.pyとの違い）。

  - 表記重複・人物名/役職名混入の疑い:
    all_entities または main_entities を canonicalize_entities() で正規化すると件数が減る
    （例:「コインベース」と「Coinbase」が別々に列挙されている、役職名付きの人物名が
    紛れ込んでいる、など）
  - 主体企業が未抽出:
    all_entities はあるのに main_entities が空（記事の中心となる企業を特定できていない）

まず対象を識別して件数を表示し（--dry-run ならここで終了）、そのうえで reanalyze.py と同じ
バッチ処理・上限（既定10件・5件ずつ）でGemini再分析を行う。

実行方法:
    python scripts/reanalyze_entities.py --dry-run       # 識別のみ。API呼び出しなし
    python scripts/reanalyze_entities.py                 # 識別のうえ、既定の上限まで再分析
    python scripts/reanalyze_entities.py --limit 5 --batch-size 5
環境変数:
    GEMINI_API_KEY, NEWS_FILE, REANALYZE_LIMIT, REANALYZE_BATCH_SIZE (reanalyze.pyと共通)
"""
import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyzer import canonicalize_entities  # noqa: E402
from reanalyze import (  # noqa: E402
    DATA_FILE,
    DEFAULT_BATCH_SIZE,
    DEFAULT_LIMIT,
    JST,
    load_json,
    process_targets,
    save_json,
)


def _has_duplicate_or_polluted_names(names):
    """canonicalize_entities()で正規化すると件数が減る = 表記重複や人物名/役職名混入の疑いあり。"""
    names = names or []
    return len(canonicalize_entities(names)) < len(names)


def has_entity_quality_issue(article):
    if article.get("manually_edited"):
        return False
    all_entities = article.get("all_entities") or []
    main_entities = article.get("main_entities") or []
    if not all_entities:
        return False  # 企業名自体が無い記事は対象外（一般的な再分析はreanalyze.py側の役割）
    if _has_duplicate_or_polluted_names(all_entities):
        return True
    if _has_duplicate_or_polluted_names(main_entities):
        return True
    if not main_entities:
        return True  # 全企業は抽出できているのに主体企業が特定できていない
    return False


def classify(article):
    all_entities = article.get("all_entities") or []
    main_entities = article.get("main_entities") or []
    reasons = []
    if _has_duplicate_or_polluted_names(all_entities) or _has_duplicate_or_polluted_names(main_entities):
        reasons.append("dup")
    if all_entities and not main_entities:
        reasons.append("no_main")
    return reasons


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help=f"1回の実行で処理する件数の上限（既定: {DEFAULT_LIMIT}）")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help=f"バッチ1回あたりの記事数（既定: {DEFAULT_BATCH_SIZE}）")
    parser.add_argument("--dry-run", action="store_true", help="対象記事の識別・件数表示のみ行い、API呼び出しは行わない")
    args = parser.parse_args()

    news_db = load_json(DATA_FILE, {"articles": [], "last_updated": None, "total_count": 0, "sources": []})

    all_targets = [a for a in news_db["articles"] if has_entity_quality_issue(a)]
    dup_count = sum(1 for a in all_targets if "dup" in classify(a))
    no_main_count = sum(1 for a in all_targets if "no_main" in classify(a))
    print(
        f"[info] identified {len(all_targets)} article(s) with entity-quality issues "
        f"(表記重複/人物名混入の疑い: {dup_count}件, 主体企業未抽出: {no_main_count}件; 重複カウントあり得る)"
    )

    if args.dry_run:
        print("[info] --dry-run のためAPI呼び出しは行いません")
        return
    if not all_targets:
        print("[info] 対象記事がないため終了します")
        return

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("[info] GEMINI_API_KEY not set; using keyword fallback for all articles")

    updated_count = process_targets(
        all_targets, args.limit, args.batch_size, api_key, label="entity-quality issues"
    )

    news_db["last_updated"] = datetime.now(JST).isoformat()
    save_json(DATA_FILE, news_db)
    print(f"[info] done. reanalyzed {updated_count} article(s) for entity-quality issues")


if __name__ == "__main__":
    main()
