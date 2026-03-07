#!/usr/bin/env python3
"""
海事ニュース見出しスクレイパー v6 (5ソース)
============================================
日本海事新聞 + 海事プレスONLINE + Splash247 + Hellenic Shipping News
+ TDS (Tramp Data Service) の公開見出しを取得。
ドライバルク特化スコアリング + 初検出時刻付きHTMLを生成。

英語ソース: WordPress REST API → RSS フィード の二段フォールバック。
TDS: トップページの Daily/Weekly 見出し（認証不要部分のみ）。

使い方:
  pip install requests beautifulsoup4
  python maritime_headlines.py

出力:
  index.html           … ブラウザで見る見出しページ
  seen_articles.json   … 記事データの記録（自動生成・更新）
"""

import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
import html as h
import re, os, json

JST = timezone(timedelta(hours=9))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}

# seen_articles.json のパス（スクリプトと同じディレクトリ）
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SEEN_PATH = os.path.join(SCRIPT_DIR, "seen_articles.json")

# 何日分の履歴を保持するか（古い記事は自動削除）
SEEN_RETAIN_DAYS = 7


# ============================================================
# ★ スコアリング設定（ここを編集して優先度を調整）
# ============================================================

CATEGORY_PRIORITY = {
    # --- 高関心 ---
    "不定期":               95,
    "海運＜不定期専用船＞・海洋": 95,
    "不定期・海洋":          95,
    "マーケット":            90,
    "激震 イラン軍事衝突":    85,
    "イラン軍事衝突":        85,
    # --- 中関心 ---
    "最新速報":             70,
    "速報":                70,
    "コンテナ":             60,
    "海運＜コンテナ・物流＞":  60,
    "外航全般":             55,
    "海運＜経営・全般＞":     55,
    "経営・全般":            55,
    "造船/舶用":            50,
    "造船・舶用":            50,
    "国内船主の今":          40,
    # --- 低関心 ---
    "物流/港運":            35,
    "港湾":                30,
    "内航/フェリー":         25,
    "航空貨物":             20,
    "コラム":               20,
    "ｺﾗﾑ･ｵﾋﾟﾆｵﾝ":          20,
    "Information":          15,
    "ひと":                15,
    "人事":                15,
    # --- Splash247 ---
    "Dry Cargo":            90,
    "Shipyards":            50,
    "Containers":           55,
    "Tankers":              30,
    "Gas":                  35,
    "Offshore":             35,
    "Renewables":           40,
    "Regulatory":           40,
    "Environment":          30,
    "Finance and Insurance": 40,
    "Bunkering":            30,
    "Operations":           35,
    "Ports and Logistics":  30,
    "Contributions":        25,
    "Press Releases":       15,
    # --- Hellenic Shipping News ---
    "Dry Bulk Market":      90,
    "International Shipping News": 55,
    "Hellenic Shipping News": 50,
    "Commodities":          60,
    "Stock Market":         25,
    "Global Economy":       30,
    "Weekly Shipbrokers Reports": 70,
    # --- TDS (Tramp Data Service) ---
    "TDS Daily":            85,
    "TDS Weekly":           80,
}
DEFAULT_CATEGORY_SCORE = 30

BOOST_KEYWORDS = {
    # ドライバルク（主軸）
    "ケープ":     30, "ケープサイズ": 30, "Capesize": 30,
    "パナマックス": 25, "Panamax":    25,
    "ハンディ":    20, "ハンディマックス": 20,
    "スープラマックス": 20, "スープラ": 20, "Supramax": 20,
    "ドライバルク": 30, "バルカー":    25, "Bulker": 25,
    "BDI":        25, "BCI":        25, "BPI": 20, "BSI": 20,
    "鉄鉱石":     25, "石炭":       25, "穀物": 20,
    "電力炭":     25, "原料炭":     25, "コークス": 20,
    "製紙原燃料":  20, "チップ船":   20, "木材チップ": 20,
    # 市況・レート（ドライ中心）
    "市況":       15, "運賃":       15, "用船":  15,
    "スポット":    10, "FFA":       15,
    "船価":       10, "中古船":     10,
    # 組織・キープレイヤー
    "MOL":        20, "商船三井":   20,
    "日本郵船":    15, "川崎汽船":   15, "NSU":  15,
    # 地政学・需給インパクト
    "ホルムズ":    20, "イラン":     15, "中東":  10,
    "封鎖":       15, "LNG":       15,
    # 洋上風力
    "洋上風力":    15,
    # --- English keywords (Splash247 / Hellenic) ---
    "dry bulk":   25, "iron ore":   25, "coal trade": 20,
    "Kamsarmax":  20, "Ultramax":   20, "Handysize":  15,
    "Newcastlemax": 25, "freight rate": 15, "tonne-mile": 15,
    "bauxite":    15, "grain":      15, "secondhand":  10,
    "newbuilding": 10, "demolition": 10,
}

SCORE_THRESHOLD = 60


# ============================================================
# 記事データの永続化（トップページ消滅後も保持）
# ============================================================
def load_seen():
    """seen_articles.json を読み込む。
    新形式: {url: {src, cat, title, url, date, sd, first_seen_iso}}
    旧形式: {url: timestamp} → 自動マイグレーション"""
    if os.path.exists(SEEN_PATH):
        try:
            with open(SEEN_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not data:
                return {}
            # 旧形式チェック: 値が文字列ならタイムスタンプのみ（旧形式）
            sample = next(iter(data.values()))
            if isinstance(sample, str):
                # 旧→新マイグレーション（時刻だけ引き継ぎ、記事データなし）
                return {url: {"first_seen_iso": ts} for url, ts in data.items()}
            return data
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_seen(seen):
    """seen_articles.json を保存。保持期間外のエントリは自動削除。"""
    cutoff = (datetime.now(JST) - timedelta(days=SEEN_RETAIN_DAYS)).isoformat()
    cleaned = {}
    for url, entry in seen.items():
        ts = entry.get("first_seen_iso", "")
        if ts >= cutoff:
            cleaned[url] = entry
    with open(SEEN_PATH, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, ensure_ascii=False, indent=1)
    return cleaned


def stamp_articles(articles, seen):
    """各記事に初検出時刻を付与し、seenに記事データを保存。"""
    now_iso = datetime.now(JST).isoformat()
    for a in articles:
        url = a["url"]
        if url not in seen:
            # 新規記事: 記事データ丸ごと保存
            seen[url] = {
                "src": a["src"], "cat": a["cat"], "title": a["title"],
                "url": a["url"], "date": a["date"], "sd": a["sd"],
                "first_seen_iso": now_iso,
            }
        else:
            # 既知記事: タイトル等が更新されていれば上書き（時刻は保持）
            seen[url]["title"] = a["title"]
            seen[url]["cat"] = a["cat"]
        first = seen[url]["first_seen_iso"]
        a["first_seen_iso"] = first
        try:
            dt = datetime.fromisoformat(first)
            a["first_seen"] = dt.strftime("%H:%M")
            a["first_seen_full"] = dt.strftime("%m/%d %H:%M")
        except ValueError:
            a["first_seen"] = "--:--"
            a["first_seen_full"] = ""
            a["first_seen_iso"] = ""
    return seen


def recover_past_articles(seen, current_urls):
    """seenに残っているが今回取得されなかった記事を復元する。"""
    recovered = []
    for url, entry in seen.items():
        if url in current_urls:
            continue  # 今回取得済み → スキップ
        # 必須フィールドがあるか確認（旧形式マイグレーション分は不完全）
        if not entry.get("title"):
            continue
        a = {
            "src": entry.get("src", "?"),
            "cat": entry.get("cat", ""),
            "title": entry.get("title", ""),
            "url": url,
            "date": entry.get("date", ""),
            "sd": entry.get("sd", ""),
            "first_seen_iso": entry.get("first_seen_iso", ""),
        }
        try:
            dt = datetime.fromisoformat(a["first_seen_iso"])
            a["first_seen"] = dt.strftime("%H:%M")
            a["first_seen_full"] = dt.strftime("%m/%d %H:%M")
        except (ValueError, TypeError):
            a["first_seen"] = "--:--"
            a["first_seen_full"] = ""
        recovered.append(a)
    return recovered


# ============================================================
# スクレイピング
# ============================================================
def fetch_jmd():
    url = "https://www.jmd.co.jp/"
    results = []
    date_re = re.compile(r'^(\d{4}/\d{2}/\d{2})\s*')
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.encoding = resp.apparent_encoding
        soup = BeautifulSoup(resp.text, "html.parser")
        cat = "トップ"
        for tag in soup.find_all(["h2", "h3"]):
            if tag.name == "h2":
                t = tag.get_text(strip=True).replace("記事一覧へ", "").strip()
                if t: cat = t
            elif tag.name == "h3":
                a = tag.find("a")
                if not a or not a.get("href"): continue
                raw = a.get_text(strip=True)
                href = a["href"]
                if not href.startswith("http"):
                    href = "https://www.jmd.co.jp/" + href.lstrip("/")
                if "article.php" not in href: continue
                m = date_re.match(raw)
                date_str = m.group(1) if m else ""
                title = raw[m.end():].strip() if m else raw
                if not title: continue
                sd = date_str[5:] if date_str else ""
                results.append({"src":"j","cat":cat,"title":title,"url":href,"date":date_str,"sd":sd})
    except Exception as e:
        print(f"  [ERROR] JMD: {e}")
    return results


def fetch_kaijipress():
    url = "https://www.kaijipress.com/"
    results = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.encoding = resp.apparent_encoding
        soup = BeautifulSoup(resp.text, "html.parser")
        page_date = ""
        dm = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', soup.get_text())
        if dm:
            page_date = f"{dm.group(1)}/{int(dm.group(2)):02d}/{int(dm.group(3)):02d}"
        cat = "トップ"
        for tag in soup.find_all(["h2", "a"]):
            if tag.name == "h2":
                t = tag.get_text(strip=True)
                if t and len(t) < 30: cat = t
            elif tag.name == "a":
                href = tag.get("href", "")
                if not any(href.startswith(p) for p in ["/news/","/markets/","/person/","/column/","/feature/"]): continue
                title = tag.get_text(strip=True)
                if not title or len(title) <= 5 or "一覧" in title: continue
                full = "https://www.kaijipress.com" + href
                if any(r["url"] == full for r in results): continue
                date_str = page_date or ""
                sd = date_str[5:] if len(date_str) >= 10 else ""
                results.append({"src":"k","cat":cat,"title":title,"url":full,"date":date_str,"sd":sd})
    except Exception as e:
        print(f"  [ERROR] KP: {e}")
    return results


def _fetch_wp_api(domain, category_slug, src_key, default_cat):
    """WordPress REST API から記事を取得（Cloudflare回避率が高い）"""
    results = []
    try:
        # カテゴリIDを取得
        cat_url = f"https://{domain}/wp-json/wp/v2/categories?slug={category_slug}"
        cat_resp = requests.get(cat_url, headers=HEADERS, timeout=15)
        cat_resp.raise_for_status()
        cats = cat_resp.json()
        if not cats:
            print(f"    [WP-API] カテゴリ '{category_slug}' 見つからず")
            return results
        cat_id = cats[0]["id"]
        cat_name = cats[0].get("name", default_cat)

        # 記事を取得
        api_url = (f"https://{domain}/wp-json/wp/v2/posts?"
                   f"categories={cat_id}&per_page=20"
                   f"&_fields=title,link,date,categories")
        resp = requests.get(api_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        posts = resp.json()
        for post in posts:
            raw = post["title"]["rendered"]
            title = BeautifulSoup(raw, "html.parser").get_text().strip()
            if not title:
                continue
            href = post["link"]
            dt_str = post["date"][:10]
            date_str = dt_str.replace("-", "/")
            sd = date_str[5:]
            if any(r["url"] == href for r in results):
                continue
            results.append({
                "src": src_key, "cat": cat_name,
                "title": title, "url": href,
                "date": date_str, "sd": sd
            })
        print(f"    [WP-API] {len(results)} 件取得")
    except Exception as e:
        print(f"    [WP-API] 失敗: {e}")
    return results


def _fetch_rss(rss_url, src_key, default_cat):
    """RSS フィードから記事を取得（フォールバック用）"""
    results = []
    try:
        resp = requests.get(rss_url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        for item in root.iter("item"):
            title_el = item.find("title")
            link_el = item.find("link")
            pub_el = item.find("pubDate")
            if title_el is None or link_el is None:
                continue
            title = (title_el.text or "").strip()
            href = (link_el.text or "").strip()
            if not title or not href:
                continue
            # カテゴリ
            cats = [c.text for c in item.findall("category") if c.text]
            cat = default_cat
            for c in cats:
                if c in CATEGORY_PRIORITY:
                    cat = c
                    break
            # 日付
            date_str, sd = "", ""
            if pub_el is not None and pub_el.text:
                try:
                    dt = parsedate_to_datetime(pub_el.text).astimezone(JST)
                    date_str = dt.strftime("%Y/%m/%d")
                    sd = dt.strftime("%m/%d")
                except (ValueError, TypeError):
                    pass
            if any(r["url"] == href for r in results):
                continue
            results.append({
                "src": src_key, "cat": cat,
                "title": title, "url": href,
                "date": date_str, "sd": sd
            })
        print(f"    [RSS] {len(results)} 件取得")
    except Exception as e:
        print(f"    [RSS] 失敗: {e}")
    return results


def _fetch_google_news_rss(site_domain, search_terms, src_key, default_cat):
    """Google News RSS で特定サイトの記事を取得（Cloudflare完全回避）"""
    results = []
    try:
        query = f"site:{site_domain} {search_terms}"
        gnews_url = (
            f"https://news.google.com/rss/search?"
            f"q={requests.utils.quote(query)}&hl=en&gl=US&ceid=US:en"
        )
        resp = requests.get(gnews_url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; NewsAggregator/1.0)"
        }, timeout=20)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        for item in root.iter("item"):
            title_el = item.find("title")
            link_el = item.find("link")
            pub_el = item.find("pubDate")
            if title_el is None or link_el is None:
                continue
            title = (title_el.text or "").strip()
            href = (link_el.text or "").strip()
            if not title or not href:
                continue
            # Google News のリンクはリダイレクトURLの場合がある
            # source タグからオリジナルURLを取得できる場合もある
            source_el = item.find("source")
            # 日付
            date_str, sd = "", ""
            if pub_el is not None and pub_el.text:
                try:
                    dt = parsedate_to_datetime(pub_el.text).astimezone(JST)
                    date_str = dt.strftime("%Y/%m/%d")
                    sd = dt.strftime("%m/%d")
                except (ValueError, TypeError):
                    pass
            if any(r["title"] == title for r in results):
                continue
            results.append({
                "src": src_key, "cat": default_cat,
                "title": title, "url": href,
                "date": date_str, "sd": sd
            })
        print(f"    [Google News] {len(results)} 件取得")
    except Exception as e:
        print(f"    [Google News] 失敗: {e}")
    return results


def fetch_splash247():
    """Splash247 Dry Cargo: WP REST API → RSS → Google News フォールバック"""
    print("  [Splash247] WP API...")
    results = _fetch_wp_api("splash247.com", "dry-cargo", "s", "Dry Cargo")
    if not results:
        print("  [Splash247] → RSS フォールバック...")
        results = _fetch_rss(
            "https://splash247.com/category/sector/dry-cargo/feed/",
            "s", "Dry Cargo"
        )
    if not results:
        print("  [Splash247] → Google News フォールバック...")
        results = _fetch_google_news_rss(
            "splash247.com", "dry cargo bulk", "s", "Dry Cargo"
        )
    return results


def fetch_hellenic():
    """Hellenic Shipping News: WP REST API → RSS → Google News フォールバック"""
    print("  [Hellenic] WP API...")
    results = _fetch_wp_api(
        "www.hellenicshippingnews.com", "dry-bulk-market", "h", "Dry Bulk Market"
    )
    if not results:
        print("  [Hellenic] → RSS フォールバック...")
        results = _fetch_rss(
            "https://www.hellenicshippingnews.com/category/dry-bulk-market/feed/",
            "h", "Dry Bulk Market"
        )
    if not results:
        print("  [Hellenic] → Google News フォールバック...")
        results = _fetch_google_news_rss(
            "hellenicshippingnews.com", "dry bulk shipping", "h", "Dry Bulk Market"
        )
    return results


def fetch_tds():
    """TDS (Tramp Data Service) トップページから Daily/Weekly 見出しを取得"""
    url = "https://www.tramp.co.jp/"
    results = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.encoding = resp.apparent_encoding
        soup = BeautifulSoup(resp.text, "html.parser")

        today_str = datetime.now(JST).strftime("%Y/%m/%d")
        today_sd = datetime.now(JST).strftime("%m/%d")

        # Daily 見出し: 「【本日のトピック】...」のパターン
        for li in soup.find_all("li"):
            li_text = li.get_text(strip=True)
            if "【本日のトピック】" in li_text or "【トピック】" in li_text:
                a = li.find("a")
                href = a["href"] if a and a.get("href") else "https://www.tramp.co.jp/fenet/daily_report"
                if href.startswith("/"):
                    href = "https://www.tramp.co.jp" + href
                title = li_text
                if len(title) > 150:
                    title = title[:150] + "…"
                results.append({
                    "src": "t", "cat": "TDS Daily",
                    "title": title, "url": href,
                    "date": today_str, "sd": today_sd
                })

        # Daily 成約概要行: 「○月○日：T/C市場＝...」
        for li in soup.find_all("li"):
            li_text = li.get_text(strip=True)
            if re.match(r'\d{1,2}月\d{1,2}日[：:]', li_text):
                a = li.find("a")
                href = a["href"] if a and a.get("href") else "https://www.tramp.co.jp/fenet/daily_report"
                if href.startswith("/"):
                    href = "https://www.tramp.co.jp" + href
                if any(r["url"] == href and r["title"] == li_text for r in results):
                    continue
                results.append({
                    "src": "t", "cat": "TDS Daily",
                    "title": li_text, "url": href,
                    "date": today_str, "sd": today_sd
                })

        # Weekly 各部門記事: weekly_general/detail リンクを探す
        for li in soup.find_all("li"):
            a = li.find("a")
            if not a or not a.get("href"):
                continue
            href = a["href"]
            if "weekly_general/detail" not in href and "weekly_report" not in href:
                continue
            if href.startswith("/"):
                href = "https://www.tramp.co.jp" + href
            li_text = li.get_text(strip=True)
            # 「部門名：タイトル」から部門名を抽出
            dept_match = re.match(r'(鉄鉱石|石炭|穀物|マイナーバルク|新造・中古船|証券|資源・食糧)部門[：:]\s*', li_text)
            if dept_match:
                title = li_text
            else:
                title = a.get_text(strip=True)
            if not title or len(title) < 5:
                continue
            if len(title) > 150:
                title = title[:150] + "…"
            if any(r["url"] == href for r in results):
                continue
            results.append({
                "src": "t", "cat": "TDS Weekly",
                "title": title, "url": href,
                "date": today_str, "sd": today_sd
            })

        # News Release テーブル内の更新情報
        for td in soup.find_all("td"):
            a = td.find("a")
            if not a or not a.get("href"):
                continue
            href = a["href"]
            if href.startswith("/"):
                href = "https://www.tramp.co.jp" + href
            link_text = a.get_text(strip=True)
            if any(r["url"] == href for r in results):
                continue
            if "weekly" in href.lower() or "柴田明夫" in link_text or "資源・食糧" in link_text:
                cat = "TDS Weekly"
            elif "daily" in href.lower():
                cat = "TDS Daily"
            else:
                continue
            # 日付を検出（テーブルの前のtdに日付がある）
            prev_td = td.find_previous_sibling("td")
            date_str = today_str
            sd = today_sd
            if prev_td:
                dm = re.match(r'(\d{4}-\d{2}-\d{2})', prev_td.get_text(strip=True))
                if dm:
                    date_str = dm.group(1).replace("-", "/")
                    sd = date_str[5:]
            results.append({
                "src": "t", "cat": cat,
                "title": link_text, "url": href,
                "date": date_str, "sd": sd
            })

    except Exception as e:
        print(f"  [ERROR] TDS: {e}")
    return results


# ============================================================
# スコアリング
# ============================================================
def score_article(article):
    base = CATEGORY_PRIORITY.get(article["cat"], DEFAULT_CATEGORY_SCORE)
    boost = 0
    title = article["title"]
    for kw, pts in BOOST_KEYWORDS.items():
        if kw.lower() in title.lower():
            boost += pts
    article["score"] = min(base + boost, 200)
    return article


# 速報ボーナス（初検出日の鮮度で加点）
RECENCY_BONUS = {
    0: 50,   # 当日
    1: 25,   # 昨日
    2: 10,   # 2日前
}

def apply_recency_bonus(articles):
    """初検出日に応じてスコアに速報ボーナスを加算"""
    today = datetime.now(JST).date()
    for a in articles:
        iso = a.get("first_seen_iso", "")
        if not iso:
            continue
        try:
            seen_date = datetime.fromisoformat(iso).date()
            days_ago = (today - seen_date).days
            bonus = RECENCY_BONUS.get(days_ago, 0)
            if bonus:
                a["score"] = min(a["score"] + bonus, 250)  # content 200 + recency 50
        except (ValueError, TypeError):
            pass


# ============================================================
# HTML生成
# ============================================================
CSS = """
:root{--bg:#f5f5f0;--card:#fff;--jmd:#1b3a4b;--jmd-l:#e8eff3;--kp:#6b2737;--kp-l:#f5e8eb;
--sp:#0066aa;--sp-l:#e5f0fa;--hl:#2e7d32;--hl-l:#e8f5e9;--td:#d35400;--td-l:#fdf2e9;
--text:#1a1a1a;--tm:#555;--tl:#888;--bdr:#ddd;--acc:#c4841d;--acc-bg:#fdf6ec;--hov:#f0f4f8;--r:6px;
--hi:#e74c3c;--hi-bg:#fdedec;--md:#f39c12;--md-bg:#fef9e7;--lo:#95a5a6;--lo-bg:#f2f3f4}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:"Noto Sans JP","Hiragino Kaku Gothic ProN","Meiryo",sans-serif;background:var(--bg);color:var(--text);line-height:1.7}
.hd{background:var(--jmd);color:#fff;padding:24px 24px 16px;text-align:center;border-bottom:4px solid var(--acc)}
.hd h1{font-size:1.4em;font-weight:700;letter-spacing:.08em;margin-bottom:3px}
.hd .m{font-size:.8em;opacity:.75}
.sts{display:flex;justify-content:center;gap:10px;padding:14px 20px;flex-wrap:wrap}
.st{background:var(--card);border:1px solid var(--bdr);border-radius:var(--r);padding:8px 20px;text-align:center;min-width:100px}
.st .n{font-size:1.6em;font-weight:700}
.st .n.j{color:var(--jmd)}.st .n.k{color:var(--kp)}.st .n.t{color:var(--acc)}.st .n.h{color:var(--hi)}
.st .n.tm{color:var(--jmd);font-size:1.4em;letter-spacing:.02em}
.st .l{font-size:.72em;color:var(--tl)}
.tb{display:flex;max-width:1100px;margin:0 auto;padding:0 20px;gap:2px}
.tbtn{padding:9px 22px;font-size:.9em;font-weight:500;border:1px solid var(--bdr);border-bottom:none;
border-radius:var(--r) var(--r) 0 0;background:#e8e8e3;color:var(--tm);cursor:pointer;transition:all .15s;font-family:inherit}
.tbtn:hover{background:#ddd}.tbtn.a{background:var(--card);color:var(--text);font-weight:700;position:relative;z-index:1}
.tc{display:none;max-width:1100px;margin:0 auto;padding:0 20px 30px}.tc.a{display:block}
.tp{background:var(--card);border:1px solid var(--bdr);border-radius:0 var(--r) var(--r) var(--r);overflow:hidden}
.sh{font-size:1em;font-weight:700;padding:10px 16px;color:#fff}.sh.j{background:var(--jmd)}.sh.k{background:var(--kp)}.sh.s{background:var(--sp)}.sh.h{background:var(--hl)}.sh.t{background:var(--td)}
.ct{border-bottom:1px solid var(--bdr);padding:12px 16px}.ct:last-child{border-bottom:none}
.cn{font-weight:700;font-size:.8em;color:var(--tl);letter-spacing:.04em;margin-bottom:6px;padding-bottom:4px;border-bottom:2px solid var(--bdr)}
.al{list-style:none}
.al li{display:flex;align-items:baseline;gap:8px;padding:4px 0;border-bottom:1px dotted #e5e5e5}
.al li:last-child{border-bottom:none}
.al .d{flex-shrink:0;font-size:.76em;color:var(--tl);font-weight:500;min-width:40px;font-variant-numeric:tabular-nums}
.al .fs{flex-shrink:0;font-size:.7em;color:var(--acc);font-weight:500;min-width:38px;font-variant-numeric:tabular-nums}
.al a{color:var(--text);text-decoration:none;font-size:.9em;padding:2px 4px;border-radius:3px;transition:all .12s;flex:1}
.al a:hover{background:var(--hov);color:var(--jmd)}
.li{display:flex;align-items:baseline;gap:8px;padding:7px 16px;border-bottom:1px solid #f0f0f0;transition:background .12s}
.li:hover{background:var(--hov)}.li:last-child{border-bottom:none}
.li .d{flex-shrink:0;font-size:.76em;color:var(--tl);font-weight:500;min-width:40px;font-variant-numeric:tabular-nums}
.li .fs{flex-shrink:0;font-size:.7em;color:var(--acc);font-weight:500;min-width:38px;font-variant-numeric:tabular-nums}
.li .st2{flex-shrink:0;font-size:.66em;font-weight:700;padding:2px 6px;border-radius:3px}
.li .st2.j{background:var(--jmd-l);color:var(--jmd)}.li .st2.k{background:var(--kp-l);color:var(--kp)}
.li .st2.s{background:var(--sp-l);color:var(--sp)}.li .st2.h{background:var(--hl-l);color:var(--hl)}.li .st2.t{background:var(--td-l);color:var(--td)}
.li .ct2{flex-shrink:0;font-size:.66em;font-weight:500;padding:2px 5px;border-radius:3px;background:var(--acc-bg);color:var(--acc)}
.li .sc{flex-shrink:0;font-size:.62em;font-weight:700;padding:2px 5px;border-radius:3px;min-width:28px;text-align:center}
.li .sc.hi{background:var(--hi-bg);color:var(--hi)}.li .sc.md{background:var(--md-bg);color:var(--md)}.li .sc.lo{background:var(--lo-bg);color:var(--lo)}
.li a{color:var(--text);text-decoration:none;font-size:.9em;flex:1;padding:2px 4px;border-radius:3px}
.li a:hover{color:var(--jmd)}
.fl{padding:12px 16px;border-bottom:1px solid var(--bdr);display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.fl .lb{font-size:.76em;color:var(--tl);margin-right:4px;font-weight:500}
.fb{font-size:.74em;padding:4px 11px;border:1px solid var(--bdr);border-radius:20px;background:var(--card);
color:var(--tm);cursor:pointer;transition:all .12s;font-family:inherit}
.fb:hover{border-color:var(--jmd);color:var(--jmd)}.fb.a{background:var(--jmd);color:#fff;border-color:var(--jmd)}
.ft{text-align:center;padding:18px;font-size:.75em;color:var(--tl);max-width:1100px;margin:0 auto}
.empty{padding:30px;text-align:center;color:var(--tl);font-size:.9em}
@media(max-width:600px){.li{flex-wrap:wrap;gap:3px}.li .ct2,.li .sc{display:none}.tbtn{padding:7px 12px;font-size:.82em}}
"""

JS = """
function switchTab(name){
  document.querySelectorAll('.tbtn').forEach(b=>b.classList.remove('a'));
  document.querySelectorAll('.tc').forEach(c=>c.classList.remove('a'));
  document.getElementById('tab-'+name).classList.add('a');
  event.target.classList.add('a');
}
function filterSrc(src,btn){
  document.querySelectorAll('.fl .fb').forEach(b=>b.classList.remove('a'));
  btn.classList.add('a');
  const container=btn.closest('.tp');
  container.querySelectorAll('.li').forEach(i=>{
    i.style.display=src==='all'?'':i.dataset.s===src?'':'none';
  });
}
"""


def score_badge(score):
    if score >= 80: return f'<span class="sc hi">{score}</span>'
    if score >= 50: return f'<span class="sc md">{score}</span>'
    return f'<span class="sc lo">{score}</span>'


def render_item(a):
    sc = a["src"]
    SRC_LABELS = {"j":"海事新聞","k":"海事プレス","s":"Splash","h":"Hellenic","t":"TDS"}
    sl = SRC_LABELS.get(sc, sc)
    fs = a.get("first_seen", "")
    return (f'<div class="li" data-s="{sc}">'
            f'<span class="d">{h.escape(a["sd"])}</span>'
            f'<span class="fs" title="初検出 JST">{h.escape(fs)}</span>'
            f'{score_badge(a["score"])}'
            f'<span class="st2 {sc}">{sl}</span>'
            f'<span class="ct2">{h.escape(a["cat"])}</span>'
            f'<a href="{h.escape(a["url"])}" target="_blank">{h.escape(a["title"])}</a>'
            f'</div>\n')


def render_source_item(a):
    fs = a.get("first_seen", "")
    return (f'<li><span class="d">{h.escape(a["sd"])}</span>'
            f'<span class="fs" title="初検出">{h.escape(fs)}</span>'
            f'<a href="{h.escape(a["url"])}" target="_blank">{h.escape(a["title"])}</a></li>\n')


def filter_buttons():
    return ('<div class="fl"><span class="lb">ソース:</span>'
            '<button class="fb a" onclick="filterSrc(\'all\',this)">すべて</button>'
            '<button class="fb" onclick="filterSrc(\'j\',this)">海事新聞</button>'
            '<button class="fb" onclick="filterSrc(\'k\',this)">海事プレス</button>'
            '<button class="fb" onclick="filterSrc(\'s\',this)">Splash247</button>'
            '<button class="fb" onclick="filterSrc(\'h\',this)">Hellenic</button>'
            '<button class="fb" onclick="filterSrc(\'t\',this)">TDS</button>'
            '</div>\n')


def render_source_tab(sources):
    """sources: list of (key, label, articles)"""
    s = ""
    for key, label, articles in sources:
        if not articles: continue
        s += f'<div class="sh {key}">&#9875; {h.escape(label)}</div>\n'
        cats = {}
        for a in articles: cats.setdefault(a["cat"], []).append(a)
        for cat, items in cats.items():
            items.sort(key=lambda x: -x["score"])
            s += f'<div class="ct"><div class="cn">{h.escape(cat)} ({len(items)})</div><ul class="al">\n'
            for a in items: s += render_source_item(a)
            s += '</ul></div>\n'
    return s


def generate_html(all_sources, output_path):
    """all_sources: list of (key, label, articles)"""
    now_jst = datetime.now(JST)
    now = now_jst.strftime("%Y年%m月%d日 %H:%M")
    now_short = now_jst.strftime("%H:%M")

    all_articles = []
    for _, _, arts in all_sources:
        all_articles.extend(arts)
    total = len(all_articles)

    hot = sorted([a for a in all_articles if a["score"] >= SCORE_THRESHOLD],
                 key=lambda x: -x["score"])
    by_time = sorted(all_articles,
                     key=lambda x: x.get("first_seen_iso", ""), reverse=True)

    hot_html = filter_buttons()
    if hot:
        for a in hot: hot_html += render_item(a)
    else:
        hot_html += '<div class="empty">注目記事なし（閾値を調整してください）</div>\n'

    all_html = filter_buttons()
    for a in by_time: all_html += render_item(a)

    src_html = render_source_tab(all_sources)

    # ソース別カウント表示
    CSS_VARS = {"j":"jmd","k":"kp","s":"sp","h":"hl","t":"td"}
    stat_cards = f'<div class="st"><div class="n tm">{now_short}</div><div class="l">最終取得 (JST)</div></div>\n'
    stat_cards += f'<div class="st"><div class="n" style="color:var(--hi)">{len(hot)}</div><div class="l">注目</div></div>\n'
    for key, label, arts in all_sources:
        short = label[:6]
        cv = CSS_VARS.get(key, "acc")
        stat_cards += f'<div class="st"><div class="n" style="color:var(--{cv})">{len(arts)}</div><div class="l">{h.escape(short)}</div></div>\n'
    stat_cards += f'<div class="st"><div class="n" style="color:var(--acc)">{total}</div><div class="l">合計</div></div>\n'

    page = f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>海事ニュース - {now}</title>
<style>{CSS}</style></head><body>
<div class="hd"><h1>&#9875; 海事ニュース 見出し一覧</h1><div class="m">ドライバルク特化スコアリング ｜ 15分間隔自動更新 ｜ 5ソース</div></div>
<div class="sts">
{stat_cards}
</div>
<div class="tb">
<button class="tbtn a" onclick="switchTab('hot')">&#128293; 注目 ({len(hot)})</button>
<button class="tbtn" onclick="switchTab('all')">&#128340; 全記事 ({total})</button>
<button class="tbtn" onclick="switchTab('source')">ソース別</button>
</div>
<div class="tc a" id="tab-hot"><div class="tp">
{hot_html}
</div></div>
<div class="tc" id="tab-all"><div class="tp">
{all_html}
</div></div>
<div class="tc" id="tab-source"><div class="tp">
{src_html}
</div></div>
<div class="ft">
公開ページから見出しのみを取得。記事本文は各サイトの会員登録が必要な場合あり。<br>
最終取得: {now} JST ｜ 時刻は初検出タイミング (JST) ｜ 注目 = スコア{SCORE_THRESHOLD}点以上
</div>
<script>{JS}</script>
</body></html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(page)


# ============================================================
# メイン
# ============================================================
def main():
    print("=" * 55)
    print("  海事ニュース見出しスクレイパー v6 (5ソース)")
    print("=" * 55)

    seen = load_seen()
    print(f"  履歴: {len(seen)} 件の既知記事")

    # 取得
    SOURCES = [
        ("j", "日本海事新聞",      fetch_jmd),
        ("k", "海事プレスONLINE",  fetch_kaijipress),
        ("s", "Splash247",         fetch_splash247),
        ("h", "Hellenic Shipping", fetch_hellenic),
        ("t", "TDS",               fetch_tds),
    ]

    all_sources = []
    for i, (key, label, fetcher) in enumerate(SOURCES, 1):
        print(f"\n[{i}/{len(SOURCES)}] {label} を取得中...")
        articles = fetcher()
        print(f"      → {len(articles)} 件")
        for a in articles: score_article(a)
        all_sources.append((key, label, articles))

    # 初検出時刻付与
    print(f"\n[スコアリング + タイムスタンプ]")
    new_before = len(seen)
    current_urls = set()
    all_articles = []
    for key, label, articles in all_sources:
        seen = stamp_articles(articles, seen)
        apply_recency_bonus(articles)
        all_articles.extend(articles)
        for a in articles:
            current_urls.add(a["url"])
    new_count = len(seen) - new_before
    print(f"  新規: {new_count} 件 / 今回取得: {len(all_articles)} 件")

    # トップページから消えた記事を復元
    recovered = recover_past_articles(seen, current_urls)
    if recovered:
        for a in recovered:
            score_article(a)
        apply_recency_bonus(recovered)
        # ソース別に振り分け
        SRC_KEYS = {key for key, _, _ in all_sources}
        for a in recovered:
            for i, (key, label, articles) in enumerate(all_sources):
                if a["src"] == key:
                    articles.append(a)
                    break
            else:
                # 未知ソース（通常ないが安全策）
                if all_sources:
                    all_sources[0][2].append(a)
            all_articles.append(a)
        print(f"  復元: {len(recovered)} 件（トップページ消滅後も保持）")
    print(f"  表示合計: {len(all_articles)} 件")

    # コンソール出力
    SRC_LABELS = {"j":"海事新聞","k":"海事ﾌﾟﾚｽ","s":"Splash ","h":"Hellenic","t":"TDS    "}
    all_sorted = sorted(all_articles, key=lambda x: -x["score"])
    print(f"\n{'='*55}")
    print(f"  【注目度順 TOP15】")
    print(f"{'='*55}")
    for a in all_sorted[:15]:
        s = SRC_LABELS.get(a["src"], "???")
        fs = a.get("first_seen", "--:--")
        print(f"  [{a['score']:3d}] {a['sd']} {fs}  [{s}] {a['title'][:50]}")

    # 保存
    print(f"\n[保存中...]")
    seen = save_seen(seen)
    print(f"  履歴: {len(seen)} 件（{SEEN_RETAIN_DAYS}日以内）")

    out = os.path.join(SCRIPT_DIR, "index.html")
    generate_html(all_sources, out)
    print(f"  HTML: {out}")
    print(f"\n{'='*55}")
    print(f"  完了: {datetime.now(JST).strftime('%Y-%m-%d %H:%M JST')}")
    print(f"{'='*55}")


if __name__ == "__main__":
    main()
