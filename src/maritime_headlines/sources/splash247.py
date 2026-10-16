from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import re
from urllib.parse import urlencode
import xml.etree.ElementTree as ET

from ..models import FetchResult, JST, make_article
from .common import get, soup, unique


def publication_time(value):
    # WordPress date_gmt and RFC 2822 -0000 both mean UTC even when their
    # parsers return a naive datetime. Never use the machine's local timezone.
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(JST)


def parse_posts(posts):
    articles = []
    for post in posts:
        title = soup(post["title"]["rendered"]).get_text(" ", strip=True)
        # `date` is in the site's unspecified local timezone. Use date_gmt so
        # the API and RSS routes agree on the date used for scoring/deduping.
        raw_date = post.get("date_gmt")
        published = publication_time(datetime.fromisoformat(raw_date)) if raw_date else None
        articles.append(make_article("s", "Dry Cargo", title, post["link"],
                                     published.date().isoformat() if published else None))
    return unique(articles)


def parse_rss(content, now, google=False):
    articles = []
    for item in ET.fromstring(content).iter("item"):
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        if not title or not url:
            continue
        if google:
            title = re.sub(r"\s*-\s*Splash247\s*$", "", title)
        raw_date = item.findtext("pubDate")
        published = publication_time(parsedate_to_datetime(raw_date)) if raw_date else None
        if google and published and published < now - timedelta(days=7):
            continue
        articles.append(make_article("s", "Dry Cargo", title, url,
                                     published.date().isoformat() if published else None))
    return unique(articles)


def fetch(now):
    warnings = []
    try:
        categories = get("https://splash247.com/wp-json/wp/v2/categories?slug=dry-cargo").json()
        if not categories:
            raise ValueError("Dry Cargoカテゴリなし")
        query = urlencode({"categories": categories[0]["id"], "per_page": 20, "_fields": "title,link,date_gmt"})
        articles = parse_posts(get("https://splash247.com/wp-json/wp/v2/posts?" + query).json())
        if articles:
            return FetchResult("s", articles, method="WordPress API")
        warnings.append("WordPress API: 0件")
    except Exception as exc:
        warnings.append(f"WordPress API: {exc}")
    routes = [
        ("RSS", "https://splash247.com/category/sector/dry-cargo/feed/", False),
        ("Google News RSS", "https://news.google.com/rss/search?" + urlencode({
            "q": "site:splash247.com dry cargo bulk when:7d", "hl": "en", "gl": "US", "ceid": "US:en"}), True),
    ]
    for method, url, google in routes:
        try:
            articles = parse_rss(get(url).content, now, google)
            if articles:
                return FetchResult("s", articles, method=method, warnings=warnings)
            warnings.append(method + ": 0件")
        except Exception as exc:
            warnings.append(f"{method}: {exc}")
    return FetchResult("s", error="全取得経路で記事を取得できませんでした", warnings=warnings)
