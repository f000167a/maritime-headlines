"""Versioned state, safe migration, and atomic writes."""

from copy import deepcopy
from datetime import timedelta
import json
import os
from pathlib import Path
import tempfile

from .models import SOURCES, make_article, normalized_title, timestamp


def empty_state():
    return {"schema_version": 2, "articles": {}, "sources": {}, "legacy_seen": {}}


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def save_state(path, state):
    atomic_write(path, json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def load_state(path):
    path = Path(path)
    if not path.exists():
        return empty_state()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("履歴の形式が不正です。上書きせずに停止します")
    if "schema_version" in data:
        if data["schema_version"] != 2 or not isinstance(data.get("articles"), dict):
            raise ValueError("未対応の履歴形式です")
        for key, article in data["articles"].items():
            if article.get("id") != key or article.get("src") not in SOURCES:
                raise ValueError("記事の識別子が不正です")
            for field in ("first_seen_at", "content_updated_at", "last_seen_at"):
                timestamp(article[field])
        return data
    # Legacy URL-keyed files remain readable. Do not fabricate success times.
    state = empty_state()
    for url, value in data.items():
        if isinstance(value, str):
            timestamp(value)
            state["legacy_seen"][url] = value
            continue
        first = value["first_seen_iso"]
        timestamp(first)
        if not value.get("title"):
            state["legacy_seen"][url] = first
            continue
        if value.get("src") == "t":
            from .sources.tds import tds_article
            article = tds_article(value["title"], url, timestamp(first))
            if article is None:
                continue
        else:
            # Legacy KP dates were page-wide, not article-specific.
            date = value.get("date") if value["src"] in {"j", "s"} else None
            article = make_article(value["src"], value.get("cat", ""), value["title"], url, date or None)
        article.update(first_seen_at=first, content_updated_at=first, last_seen_at=first)
        state["articles"][article["id"]] = article
    return state


def merge_results(state, results, now, retain_days):
    state = deepcopy(state)
    articles = state["articles"]
    for result in results:
        previous = state["sources"].get(result.source, {})
        status = dict(previous, last_attempt_at=now.isoformat(), ok=result.ok,
                      fetched_count=len(result.articles), method=result.method,
                      error=result.error or (None if result.ok else "記事を取得できませんでした"),
                      warnings=result.warnings)
        if result.ok:
            status["last_success_at"] = now.isoformat()
            for incoming in result.articles:
                article = dict(incoming)
                old = articles.get(article["id"])
                # Splash API/RSS and Google News may use different URLs for the same story.
                if old is None and article["src"] == "s":
                    old = next((a for a in articles.values() if a["src"] == "s"
                                and normalized_title(a["title"]) == normalized_title(article["title"])
                                and a.get("published_date") == article.get("published_date")), None)
                    if old:
                        article["id"] = old["id"]
                if old:
                    changed = any(article.get(k) != old.get(k) for k in ("title", "cat"))
                    article["published_date"] = article.get("published_date") or old.get("published_date")
                    article["first_seen_at"] = old["first_seen_at"]
                    article["content_updated_at"] = now.isoformat() if changed else old["content_updated_at"]
                else:
                    first = state.get("legacy_seen", {}).pop(article["url"], now.isoformat())
                    article.update(first_seen_at=first, content_updated_at=first)
                article["last_seen_at"] = now.isoformat()
                articles[article["id"]] = article
        state["sources"][result.source] = status
    # Keep identities of still-listed articles to avoid cyclic "new" detection.
    cutoff = now - timedelta(days=retain_days)
    state["articles"] = {key: a for key, a in articles.items()
                         if timestamp(a["last_seen_at"]) >= cutoff}
    state["legacy_seen"] = {url: t for url, t in state.get("legacy_seen", {}).items()
                            if timestamp(t) >= cutoff}
    return state


def visible_articles(state, now, retain_days):
    cutoff = now - timedelta(days=retain_days)
    visible = []
    for article in state["articles"].values():
        date = article.get("published_date")
        if date:
            if not cutoff.date() <= timestamp(date + "T00:00:00+09:00").date() <= now.date():
                continue
        elif timestamp(article["content_updated_at"]) < cutoff:
            continue
        visible.append(article)
    return visible
