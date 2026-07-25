"""共通の分析ロジック（Gemini呼び出し・キーワードフォールバック・企業名抽出）。
fetch_news.py / reanalyze.py の両方から利用する。
"""
import html
import json
import os
import re
import time

import requests

# "gemini-2.5-flash" は一部キーで新規利用不可(404 "no longer available to new users")になっているため、
# 常に現行のflashモデルを指すエイリアス "gemini-flash-latest" を使用する。
GEMINI_MODEL = "gemini-flash-latest"
GEMINI_ENDPOINT = (
    f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
)

CATEGORIES = [
    "Blockchain",
    "DeFi",
    "障害・攻撃",
    "分析・レポート",
    "Stablecoin",
    "NFT",
    "Tokenized Deposit",
    "Security Token",
    "暗号資産ETF",
    "ビジネス",
    "マーケット",
    "規制・法律",
    "イベント・人事",
]

# カテゴリのキーワード辞書（Gemini失敗時のフォールバック用）
CATEGORY_KEYWORDS = {
    "障害・攻撃": ["ハッキング", "hack", "攻撃", "脆弱性", "流出", "exploit", "不正アクセス", "障害", "ダウン", "停止"],
    "Stablecoin": ["ステーブルコイン", "stablecoin", "USDT", "USDC", "DAI", "テザー"],
    "NFT": ["NFT", "非代替性トークン"],
    "Tokenized Deposit": ["トークン化預金", "tokenized deposit"],
    "Security Token": ["セキュリティトークン", "security token", "STO"],
    "暗号資産ETF": ["ETF", "上場投資信託", "現物ETF"],
    "DeFi": ["DeFi", "分散型金融", "レンディング", "DEX", "AMM", "流動性", "イールド"],
    "規制・法律": ["規制", "金融庁", "SEC", "訴訟", "法律", "規制当局", "法案", "ライセンス", "認可"],
    "イベント・人事": ["カンファレンス", "イベント", "就任", "退任", "CEO", "人事", "登壇", "サミット"],
    "分析・レポート": ["レポート", "調査", "分析", "report", "統計", "アンケート"],
    "マーケット": ["価格", "相場", "高騰", "急落", "急騰", "下落", "上昇", "market", "取引高", "チャート", "最高値"],
    "Blockchain": ["ブロックチェーン", "blockchain", "レイヤー2", "Layer2", "メインネット", "ハードフォーク"],
    "ビジネス": ["提携", "買収", "資金調達", "上場", "投資", "パートナーシップ", "子会社", "出資"],
}
DEFAULT_CATEGORY = "ビジネス"

# 企業名・団体名マスタ辞書（正式名称 -> エイリアス一覧）
# 英数字は \bXXX\b の単語境界マッチング、日本語は部分一致で判定する。
ENTITY_DB = {
    "Coinbase": ["Coinbase", "コインベース"],
    "Binance": ["Binance", "バイナンス"],
    "bitFlyer": ["bitFlyer", "ビットフライヤー"],
    "Coincheck": ["Coincheck", "コインチェック"],
    "GMOコイン": ["GMOコイン", "GMO Coin"],
    "SBI VCトレード": ["SBI VCトレード", "SBI VC Trade"],
    "Bitget": ["Bitget", "ビットゲット"],
    "OKX": ["OKX"],
    "Kraken": ["Kraken", "クラーケン"],
    "BitMEX": ["BitMEX"],
    "Bybit": ["Bybit", "バイビット"],
    "MEXC": ["MEXC"],
    "bitbank": ["bitbank", "ビットバンク"],
    "DMM Bitcoin": ["DMM Bitcoin", "DMMビットコイン"],
    "楽天ウォレット": ["楽天ウォレット", "Rakuten Wallet"],
    "auフィナンシャル": ["auフィナンシャル", "au Financial"],
    "Circle": ["Circle", "サークル"],
    "Tether": ["Tether", "テザー"],
    "Ripple": ["Ripple", "リップル"],
    "Cardano": ["Cardano", "カルダノ", "ADA"],
    "Ethereum Foundation": ["Ethereum Foundation", "イーサリアム財団"],
    "Solana": ["Solana", "ソラナ"],
    "Polygon": ["Polygon", "ポリゴン"],
    "Avalanche": ["Avalanche", "アバランチ"],
    "Chainlink": ["Chainlink", "チェーンリンク"],
    "Uniswap": ["Uniswap", "ユニスワップ"],
    "Aave": ["Aave", "アーベ"],
    "MakerDAO": ["MakerDAO", "メイカーダオ"],
    "Compound": ["Compound"],
    "Metaplanet": ["Metaplanet", "メタプラネット"],
    "Strategy": ["MicroStrategy", "Strategy"],
    "BlackRock": ["BlackRock", "ブラックロック"],
    "Fidelity": ["Fidelity", "フィデリティ"],
    "Grayscale": ["Grayscale", "グレイスケール"],
    "VanEck": ["VanEck", "ヴァンエック"],
    "ARK Invest": ["ARK Invest", "アーク・インベスト"],
    "JPMorgan": ["JPMorgan", "JPモルガン"],
    "Visa": ["Visa", "ビザ"],
    "Mastercard": ["Mastercard", "マスターカード"],
    "PayPal": ["PayPal", "ペイパル"],
    "IREN": ["IREN"],
    "Infura": ["Infura"],
    "ConsenSys": ["ConsenSys", "コンセンシス"],
    "Alchemy": ["Alchemy"],
    "OpenSea": ["OpenSea", "オープンシー"],
    "Worldcoin": ["Worldcoin", "ワールドコイン"],
    "Polymarket": ["Polymarket", "ポリマーケット"],
    "Robinhood": ["Robinhood", "ロビンフッド"],
    "Upbit": ["Upbit", "アップビット"],
    "Bithumb": ["Bithumb", "ビッサム"],
    "Marathon Digital": ["Marathon Digital", "MARA"],
    "Riot Platforms": ["Riot Platforms"],
    "CleanSpark": ["CleanSpark"],
    "Core Scientific": ["Core Scientific"],
    "SBI": ["SBI"],
    "楽天": ["楽天グループ", "楽天"],
    "ソニー": ["ソニー", "Sony"],
    "金融庁": ["金融庁", "FSA"],
    "日本銀行": ["日本銀行", "日銀"],
    "SEC": ["SEC"],
}

_JP_RE = re.compile(r"[぀-ヿ一-鿿]")


def _alias_pattern(alias):
    if _JP_RE.search(alias):
        return None  # 日本語は部分一致で扱う
    return re.compile(r"\b" + re.escape(alias) + r"\b", re.IGNORECASE)


_ALIAS_PATTERNS = {
    canonical: [(alias, _alias_pattern(alias)) for alias in aliases]
    for canonical, aliases in ENTITY_DB.items()
}


def clean_text(raw):
    """HTMLエンティティのデコードとゴミ文字列の除去。"""
    if not raw:
        return ""
    text = html.unescape(raw)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("[…]", "").replace("…", "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


# 本文コンテナ候補のクラス名（優先度順）。サイトごとにテーマが異なるため複数用意する。
CONTENT_CLASS_HINTS = [
    "article-body",
    "entry-content",
    "articleContent",
    "post-content",
    "article_body",
    "articleBody",
    "post__content",
    "article-content",
]

# 広告・関連記事・SNS共有など、本文と無関係なウィジェットを除去するためのクラス名判定。
# トークン単位で完全一致させるもの（"ad" が "header" 等に部分一致して誤爆しないように）。
_BLACKLIST_TOKENS = {
    "ad", "ads", "promo", "widget", "widgets", "banner", "cta", "sponsor",
    "share", "sns", "popup", "pr",
}
# フレーズとして部分一致で判定するもの。
_BLACKLIST_PHRASES = [
    "entity-placement", "newsletter", "recommend", "related", "trending",
    "pr-box", "advertorial", "advertisement", "sidefeature", "side-feature",
    "breaking-area", "pressrelease",
]


# 本文末尾に付く「関連記事」等の見出しが現れたら、それ以降を本文から切り捨てる。
# （見出し要素にクラス名が付いていないサイトが多く、DOM除去だけでは対処できないため）
_BOILERPLATE_MARKERS = [
    "関連ニュース", "関連記事", "関連ガイド", "関連するキーワード", "Related Posts",
    "Trending", "この記事をシェアする", "Follow Us", "あわせて読みたい",
    "こちらもおすすめ", "Read More About", "Recommend",
]


def _truncate_at_boilerplate(text):
    positions = [text.find(m) for m in _BOILERPLATE_MARKERS if m in text]
    if positions:
        return text[: min(positions)].strip()
    return text


def _find_content_container(soup):
    candidates = {}
    for tag in soup.find_all(True):
        classes = tag.get("class")
        if not classes:
            continue
        joined = " ".join(classes).lower()
        for hint in CONTENT_CLASS_HINTS:
            if hint.lower() in joined and hint not in candidates:
                candidates[hint] = tag
    for hint in CONTENT_CLASS_HINTS:
        if hint in candidates:
            return candidates[hint]
    return None


def _strip_boilerplate(container):
    for el in container.find_all(True):
        if el.parent is None:
            continue  # 親要素ごとdecompose済み
        classes = el.get("class") or []
        idv = el.get("id") or ""
        tokens = set()
        for c in classes:
            tokens.update(re.split(r"[-_]", c.lower()))
        joined = (" ".join(classes) + " " + idv).lower()
        if tokens & _BLACKLIST_TOKENS or any(p in joined for p in _BLACKLIST_PHRASES):
            el.decompose()


def extract_article_html(html_text):
    """記事HTMLから本文らしきテキストを抽出する。

    サイトごとに本文用のクラス名（entry-content等）を優先的に探し、
    見つからない場合は<article>タグ、それも無ければ<body>にフォールバックする。
    広告・関連記事・SNS共有ウィジェットなどの本文と無関係な要素は除去する。
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "aside", "form", "iframe", "noscript"]):
        tag.decompose()

    container = _find_content_container(soup) or soup.find("article") or soup.body or soup
    _strip_boilerplate(container)

    text = container.get_text(separator=" ", strip=True)
    return _truncate_at_boilerplate(clean_text(text))


def match_entities(title, body_text):
    """本文・タイトルから登場企業を抽出し (all_entities, main_entities) を返す。"""
    all_entities = []
    title_hits = []
    haystack = f"{title} {body_text}"

    for canonical, aliases in _ALIAS_PATTERNS.items():
        matched = False
        matched_in_title = False
        for alias, pattern in aliases:
            if pattern is not None:
                if pattern.search(haystack):
                    matched = True
                if pattern.search(title):
                    matched_in_title = True
            else:
                if alias in haystack:
                    matched = True
                if alias in title:
                    matched_in_title = True
        if matched:
            all_entities.append(canonical)
            if matched_in_title:
                title_hits.append(canonical)

    main_entities = title_hits[:3] if title_hits else all_entities[:1]
    return all_entities, main_entities


# Gemini出力（自由記述）の正規化用。ENTITY_DBのエイリアスをすべて正式名称へのマップにしておき、
# 「コインベース」「Coinbase」のような表記ゆれをまとめて重複を防ぐ。
_ALIAS_TO_CANONICAL = {
    alias.lower(): canonical for canonical, aliases in ENTITY_DB.items() for alias in aliases
}

# 人物名・役職名がentityとして紛れ込むのを防ぐためのキーワード（部分一致で除外）。
_TITLE_KEYWORDS = [
    "CEO", "CFO", "COO", "CTO", "会長", "社長", "代表取締役", "代表理事", "理事長",
    "総裁", "会頭", "副社長", "専務", "常務", "取締役", "議員", "大臣", "長官",
    "委員長", "局長", "頭取", "創業者", "founder", "Founder",
]


def canonicalize_entities(names):
    """企業名リストを正規化する: 表記ゆれの統合・重複排除・人物名/役職名の除去。"""
    result = []
    seen = set()
    for raw in names or []:
        name = (raw or "").strip()
        if not name:
            continue
        if any(kw in name for kw in _TITLE_KEYWORDS):
            continue
        canonical = _ALIAS_TO_CANONICAL.get(name.lower(), name)
        key = canonical.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(canonical)
    return result


def keyword_category(title, body_text):
    haystack = f"{title} {body_text}"
    scores = {}
    for category, keywords in CATEGORY_KEYWORDS.items():
        score = sum(haystack.count(kw) for kw in keywords)
        if score:
            scores[category] = score
    if not scores:
        return DEFAULT_CATEGORY
    return max(scores.items(), key=lambda kv: kv[1])[0]


def fallback_analysis(title, body_text):
    """Gemini失敗時のキーワードベース分析。"""
    summary_source = body_text if body_text else title
    summary = summary_source[:400].strip()
    all_entities, main_entities = match_entities(title, body_text)
    return {
        "summary": summary,
        "summary_error": True,
        "category": keyword_category(title, body_text),
        "all_entities": all_entities,
        "main_entities": main_entities,
    }


def _build_prompt(title, body_text):
    categories_list = "\n".join(f"- {c}" for c in CATEGORIES)
    return f"""あなたは暗号資産(仮想通貨)ニュースの編集者です。以下の記事を分析し、指定のJSON形式で出力してください。

# 記事タイトル
{title}

# 記事本文
{body_text[:6000]}

# 出力項目
- summary: 350〜400字の日本語要約。省略記号（…や"[…]"など）は使わず、必ず完結した文章にすること。
- category: 以下のカテゴリの中から最も適切なものを1つだけ選ぶこと（このリストの文字列をそのまま出力し、番号は使わないこと）。
{categories_list}
- all_entities: 記事本文中に登場する全ての企業名・団体名・プロジェクト名のリスト。以下のルールを厳守すること。
  - 同一の企業・団体がカタカナ表記と英語表記の両方で記事中に登場する場合（例:「コインベース」と「Coinbase」）、重複して列挙せず、どちらか一方（より正式・一般的な表記）に統一すること。
  - 人物名（例:「デービッド・ソロモン」）や役職名（CEO、会長、社長、代表取締役など）は企業名ではないため含めないこと。その人物が所属する企業名のみを含めること。
- main_entities: all_entitiesのうち、タイトルにおいて主役となっている企業・団体を1〜3件選んだリスト。
  - カテゴリが「分析・レポート」の場合は、分析・調査を行っている企業や機関（分析主体）を優先すること。分析対象となっている企業やプロトコルそのものではない点に注意すること。
"""


def gemini_analyze(title, body_text, api_key, timeout=30):
    """Gemini APIで記事を分析する。失敗時は例外を送出する。"""
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")

    payload = {
        "contents": [{"parts": [{"text": _build_prompt(title, body_text)}]}],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 2048,
            # thinkingBudgetでトークン消費を抑える（0は一部モデルで拒否されるため小さめの正の値にする）
            "thinkingConfig": {"thinkingBudget": 512},
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "summary": {"type": "STRING"},
                    "category": {"type": "STRING", "enum": CATEGORIES},
                    "all_entities": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "main_entities": {"type": "ARRAY", "items": {"type": "STRING"}},
                },
                "required": ["summary", "category", "all_entities", "main_entities"],
            },
        },
    }
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key,
    }
    resp = requests.post(GEMINI_ENDPOINT, headers=headers, json=payload, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    result = json.loads(text)

    summary = (result.get("summary") or "").strip()
    if not summary:
        raise ValueError("empty summary from Gemini")

    # プロンプトで表記統一・人物名除外を指示しているが、確実性のためコード側でも正規化する
    return {
        "summary": summary,
        "summary_error": False,
        "category": result.get("category") or DEFAULT_CATEGORY,
        "all_entities": canonicalize_entities(result.get("all_entities")),
        "main_entities": canonicalize_entities(result.get("main_entities")),
    }


def _describe_error(exc):
    """例外からHTTPステータスコードと理由を人間可読な形にする（キー・URLは含めない）。"""
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        status = exc.response.status_code
        try:
            body = exc.response.json().get("error", {})
            reason = body.get("status") or body.get("message", "")
        except ValueError:
            reason = exc.response.text[:200]
        return f"HTTP {status} {reason}".strip()
    return f"{type(exc).__name__}: {exc}"


def _status_code(exc):
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return exc.response.status_code
    return None


def analyze_article(title, body_text, api_key=None, retries=3):
    """Gemini分析を試み、失敗時はキーワードフォールバックに切り替える。
    APIキー・URLはログに出力しない。

    429(クォータ超過)は数秒〜数十秒待っても回復しないため、リトライを重ねず即座に
    フォールバックする。戻り値のquota_exhaustedがTrueの場合、呼び出し側はこの実行内で
    以降の記事のGemini呼び出しを打ち切り、無駄なリクエストを避けるべき。
    """
    if api_key:
        last_err = None
        for attempt in range(retries):
            try:
                return {**gemini_analyze(title, body_text, api_key), "quota_exhausted": False}
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                if _status_code(exc) == 429:
                    break
                time.sleep(2.0 * (attempt + 1))
        print(f"[warn] Gemini analysis failed ({_describe_error(last_err)}); using keyword fallback")
        result = fallback_analysis(title, body_text)
        result["quota_exhausted"] = _status_code(last_err) == 429
        return result
    result = fallback_analysis(title, body_text)
    result["quota_exhausted"] = False
    return result
