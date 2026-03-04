#!/usr/bin/env python3
"""
海事ニュース見出しスクレイパー v4
==================================
日本海事新聞 + 海事プレスONLINE の公開見出しを取得し、
ドライバルク特化スコアリング + 初検出時刻付きHTMLを生成する。

初検出時刻: seen_articles.json に記事URLと初回取得日時を永続化。
15分間隔で実行すれば、おおよその公開タイミングが分かる。

使い方:
  pip install requests beautifulsoup4
  python maritime_headlines.py

出力:
  index.html           … ブラウザで見る見出しページ
  seen_articles.json   … 初検出時刻の記録（自動生成・更新）
"""

import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
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
}

SCORE_THRESHOLD = 60


# ============================================================
# 初検出時刻の永続化
# ============================================================
def load_seen():
    """seen_articles.json を読み込む。なければ空dictを返す。"""
    if os.path.exists(SEEN_PATH):
        try:
            with open(SEEN_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_seen(seen):
    """seen_articles.json を保存。古いエントリは自動削除。"""
    cutoff = (datetime.now(JST) - timedelta(days=SEEN_RETAIN_DAYS)).isoformat()
    cleaned = {url: ts for url, ts in seen.items() if ts >= cutoff}
    with open(SEEN_PATH, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, ensure_ascii=False, indent=1)
    return cleaned


def stamp_articles(articles, seen):
    """各記事に初検出時刻を付与。新規記事はseenに追加。"""
    now_iso = datetime.now(JST).isoformat()
    for a in articles:
        url = a["url"]
        if url not in seen:
            seen[url] = now_iso
        first = seen[url]
        a["first_seen_iso"] = first
        # JST HH:MM を取り出す
        try:
            dt = datetime.fromisoformat(first)
            a["first_seen"] = dt.strftime("%H:%M")
            a["first_seen_full"] = dt.strftime("%m/%d %H:%M")
        except ValueError:
            a["first_seen"] = "--:--"
            a["first_seen_full"] = ""
            a["first_seen_iso"] = ""
    return seen


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


# ============================================================
# HTML生成
# ============================================================
CSS = """
:root{--bg:#f5f5f0;--card:#fff;--jmd:#1b3a4b;--jmd-l:#e8eff3;--kp:#6b2737;--kp-l:#f5e8eb;
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
.sh{font-size:1em;font-weight:700;padding:10px 16px;color:#fff}.sh.j{background:var(--jmd)}.sh.k{background:var(--kp)}
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
    sl = '海事新聞' if sc == 'j' else '海事プレス'
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
            '<button class="fb" onclick="filterSrc(\'j\',this)">日本海事新聞</button>'
            '<button class="fb" onclick="filterSrc(\'k\',this)">海事プレスONLINE</button>'
            '</div>\n')


def render_source_tab(jmd, kp):
    s = '<div class="sh j">&#9875; 日本海事新聞</div>\n'
    cats = {}
    for a in jmd: cats.setdefault(a["cat"], []).append(a)
    for cat, items in cats.items():
        items.sort(key=lambda x: -x["score"])
        s += f'<div class="ct"><div class="cn">{h.escape(cat)} ({len(items)})</div><ul class="al">\n'
        for a in items: s += render_source_item(a)
        s += '</ul></div>\n'
    s += '<div class="sh k" style="margin-top:2px">&#9875; 海事プレスONLINE</div>\n'
    cats = {}
    for a in kp: cats.setdefault(a["cat"], []).append(a)
    for cat, items in cats.items():
        items.sort(key=lambda x: -x["score"])
        s += f'<div class="ct"><div class="cn">{h.escape(cat)} ({len(items)})</div><ul class="al">\n'
        for a in items: s += render_source_item(a)
        s += '</ul></div>\n'
    return s


def generate_html(jmd, kp, output_path):
    now_jst = datetime.now(JST)
    now = now_jst.strftime("%Y年%m月%d日 %H:%M")
    now_short = now_jst.strftime("%H:%M")
    total = len(jmd) + len(kp)

    # 注目: スコア閾値以上、スコア降順
    hot = sorted([a for a in jmd + kp if a["score"] >= SCORE_THRESHOLD],
                 key=lambda x: -x["score"])

    # 全記事: 初検出の新しい順
    by_time = sorted(jmd + kp,
                     key=lambda x: x.get("first_seen_iso", ""), reverse=True)

    hot_html = filter_buttons()
    if hot:
        for a in hot: hot_html += render_item(a)
    else:
        hot_html += '<div class="empty">注目記事なし（閾値を調整してください）</div>\n'

    all_html = filter_buttons()
    for a in by_time: all_html += render_item(a)

    src_html = render_source_tab(jmd, kp)

    page = f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>海事ニュース - {now}</title>
<style>{CSS}</style></head><body>
<div class="hd"><h1>&#9875; 海事ニュース 見出し一覧</h1><div class="m">ドライバルク特化スコアリング ｜ 15分間隔自動更新</div></div>
<div class="sts">
<div class="st"><div class="n tm">{now_short}</div><div class="l">最終取得 (JST)</div></div>
<div class="st"><div class="n h">{len(hot)}</div><div class="l">注目</div></div>
<div class="st"><div class="n j">{len(jmd)}</div><div class="l">海事新聞</div></div>
<div class="st"><div class="n k">{len(kp)}</div><div class="l">海事プレス</div></div>
<div class="st"><div class="n t">{total}</div><div class="l">合計</div></div>
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
公開トップページから見出しのみを取得。記事本文は各サイトの会員登録が必要。<br>
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
    print("  海事ニュース見出しスクレイパー v4")
    print("=" * 55)

    # 既存の初検出記録を読み込み
    seen = load_seen()
    print(f"  履歴: {len(seen)} 件の既知記事")

    print("\n[1/4] 日本海事新聞を取得中...")
    jmd = fetch_jmd()
    print(f"      → {len(jmd)} 件")

    print("[2/4] 海事プレスONLINEを取得中...")
    kp = fetch_kaijipress()
    print(f"      → {len(kp)} 件")

    print("[3/4] スコアリング + 初検出時刻付与...")
    for a in jmd: score_article(a)
    for a in kp:  score_article(a)

    new_before = len(seen)
    seen = stamp_articles(jmd, seen)
    seen = stamp_articles(kp, seen)
    new_count = len(seen) - new_before
    print(f"      → 新規: {new_count} 件 / 全件: {len(jmd)+len(kp)} 件")

    # コンソール出力（注目度上位）
    all_sorted = sorted(jmd + kp, key=lambda x: -x["score"])
    print(f"\n{'='*55}")
    print(f"  【注目度順 TOP15】")
    print(f"{'='*55}")
    for a in all_sorted[:15]:
        s = "海事新聞" if a["src"] == "j" else "海事ﾌﾟﾚｽ"
        fs = a.get("first_seen", "--:--")
        print(f"  [{a['score']:3d}] {a['sd']} {fs}  [{s}] {a['title']}")

    # 保存
    print(f"\n[4/4] 保存中...")
    seen = save_seen(seen)
    print(f"      → 履歴: {len(seen)} 件（{SEEN_RETAIN_DAYS}日以内）")

    out = os.path.join(SCRIPT_DIR, "index.html")
    generate_html(jmd, kp, out)
    print(f"      → HTML: {out}")
    print(f"\n{'='*55}")
    print(f"  完了: {datetime.now(JST).strftime('%Y-%m-%d %H:%M JST')}")
    print(f"{'='*55}")


if __name__ == "__main__":
    main()
