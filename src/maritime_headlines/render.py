"""Static HTML rendering using escaped data and a separate template."""

from collections import defaultdict
from html import escape
from pathlib import Path
from string import Template

from .models import SOURCES, canonical_url, timestamp
from .scoring import score_article
from .storage import atomic_write, visible_articles

ROOT = Path(__file__).parent
SHORT = {"j": "海事新聞", "k": "海事プレス", "s": "Splash247", "t": "TDS"}


def fmt(value):
    return timestamp(value).strftime("%m/%d %H:%M") if value else "未確認"


def item(article):
    source = article["src"]
    published = article.get("published_date")
    date = published[5:].replace("-", "/") if published else "—"
    changed = article["content_updated_at"] != article["first_seen_at"]
    time_label = ("更新 " if changed else "検出 ") + fmt(article["content_updated_at"])
    detail = f'初検出: {fmt(article["first_seen_at"])} / 内容更新: {fmt(article["content_updated_at"])} JST'
    score = article["content_score"]
    cls = "hi" if score >= 80 else "md" if score >= 50 else "lo"
    return (f'<div class="li" data-s="{source}">'
            f'<span class="d" title="公開日。—は不明">{escape(date)}</span>'
            f'<span class="fs" title="{escape(detail, quote=True)}">{escape(time_label)}</span>'
            f'<span class="sc {cls}" title="関心度。鮮度加点は含みません">{score}</span>'
            f'<span class="st2 {source}">{SHORT[source]}</span>'
            f'<span class="ct2">{escape(article["cat"])}</span>'
            f'<a href="{escape(canonical_url(article["url"]), quote=True)}" target="_blank" rel="noopener noreferrer">'
            f'{escape(article["title"])}</a></div>\n')


def filters():
    buttons = '<button class="fb a" data-filter="all" aria-pressed="true">すべて</button>'
    for key, label in SHORT.items():
        buttons += f'<button class="fb" data-filter="{key}" aria-pressed="false">{label}</button>'
    return '<div class="fl"><span class="lb">ソース:</span>' + buttons + '</div>'


def render(state, config, now, preview=False):
    articles = [score_article(a, config, now) for a in visible_articles(state, now, config["retain_days"])]
    by_time = sorted(articles, key=lambda a: (a["content_updated_at"], a["id"]), reverse=True)
    hot = sorted((a for a in articles if a["is_hot"]),
                 key=lambda a: (a["rank_score"], a["content_updated_at"], a["id"]), reverse=True)
    statuses = state.get("sources", {})
    last_success = max((s.get("last_success_at", "") for s in statuses.values()), default="")
    cards = (f'<div class="st"><div class="n">{fmt(last_success)}</div>'
             '<div class="l">最終成功 (JST・いずれかの媒体)</div></div>')
    cards += f'<div class="st"><div class="n">{len(hot)}</div><div class="l">注目</div></div>'
    source_html = ""
    status_html = ""
    for key, label in SOURCES.items():
        group = [a for a in by_time if a["src"] == key]
        cards += f'<div class="st"><div class="n">{len(group)}</div><div class="l">{SHORT[key]}</div></div>'
        status = statuses.get(key, {})
        ok = status.get("ok")
        health = "取得成功" if ok else "取得失敗・保存記事を表示" if ok is False else "取得状況未確認"
        status_html += (f'<div class="source-status {"ok" if ok else "warn"}">'
                        f'<strong>{label}</strong>：{health} '
                        f'｜ 今回 {status.get("fetched_count", 0)}件 '
                        f'｜ 最終成功 {fmt(status.get("last_success_at"))}</div>')
        source_html += f'<div class="sh {key}">{label}</div>'
        categories = defaultdict(list)
        for article in group:
            categories[article["cat"]].append(article)
        for category, rows in categories.items():
            source_html += f'<div class="cn source-category">{escape(category)} ({len(rows)})</div>'
            source_html += "".join(item(a) for a in rows)
        if not group:
            source_html += '<p class="empty">表示できる記事はありません</p>'
    return Template((ROOT / "templates/index.html").read_text(encoding="utf-8")).substitute(
        css=(ROOT / "assets/style.css").read_text(encoding="utf-8"),
        js=(ROOT / "assets/app.js").read_text(encoding="utf-8"),
        generated=now.strftime("%Y年%m月%d日 %H:%M"), last_success=escape(last_success),
        status_html=status_html, cards=cards,
        preview='<p class="notice">保存データのプレビューです。実サイトの取得は行っていません。</p>' if preview else "",
        hot_count=len(hot), total=len(articles),
        hot_html=filters() + ("".join(item(a) for a in hot) or '<p class="empty">注目記事なし</p>'),
        all_html=filters() + ("".join(item(a) for a in by_time) or '<p class="empty">記事なし</p>'),
        source_html=source_html, threshold=config["threshold"], retain_days=config["retain_days"],
    )


def write_page(output_dir, page):
    output_dir = Path(output_dir)
    atomic_write(output_dir / "index.html", page)
    atomic_write(output_dir / ".nojekyll", "")
