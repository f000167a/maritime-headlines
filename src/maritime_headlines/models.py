"""記事の識別と時刻。取得日を記事の公開日として補完しない。"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

JST = timezone(timedelta(hours=9))
SOURCES = {"j": "日本海事新聞", "k": "海事プレスONLINE", "s": "Splash247", "t": "TDS"}


def timestamp(value):
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError("日時にはタイムゾーンが必要です")
    return dt.astimezone(JST)


def normalized_title(title):
    return " ".join(unicodedata.normalize("NFKC", title).casefold().split())


def canonical_url(url):
    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"} or not parts.netloc or parts.username:
        raise ValueError("記事URLはhttp(s)である必要があります")
    scheme = parts.scheme
    if parts.hostname in {"www.tramp.co.jp", "www.jmd.co.jp", "www.kaijipress.com", "splash247.com"}:
        scheme = "https"
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                             if not k.lower().startswith("utm_") and k not in {"fbclid", "gclid"}))
    return urlunsplit((scheme, parts.netloc.lower(), parts.path, query, ""))


def fixed_tds_url(url):
    return urlsplit(url).path.rstrip("/") in {"/fenet/daily_report", "/fenet/weekly_report"}


def article_id(article):
    url = canonical_url(article["url"])
    identity = url
    if article["src"] == "t" and fixed_tds_url(url):
        # Fixed URLs are destinations, not identifiers for individual editions.
        edition = article.get("edition")
        kind = article.get("kind", "report")
        identity += "|" + (f"{edition}|{kind}" if edition else normalized_title(article["title"]))
    return article["src"] + ":" + hashlib.sha256(identity.encode()).hexdigest()[:24]


def make_article(src, cat, title, url, published_date=None, **extra):
    if src not in SOURCES or not title.strip():
        raise ValueError("媒体または見出しが不正です")
    if published_date:
        published_date = datetime.strptime(published_date.replace("/", "-"), "%Y-%m-%d").date().isoformat()
    article = dict(src=src, cat=cat, title=" ".join(title.split()), url=canonical_url(url),
                   published_date=published_date, **extra)
    article["id"] = article_id(article)
    return article


@dataclass
class FetchResult:
    source: str
    articles: list = field(default_factory=list)
    method: str = ""
    error: str | None = None
    warnings: list = field(default_factory=list)

    @property
    def ok(self):
        # Empty pages often mean a broken selector or an upstream error page.
        return bool(self.articles) and self.error is None
