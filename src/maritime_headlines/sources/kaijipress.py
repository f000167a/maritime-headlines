import re
from urllib.parse import urljoin, urlsplit

from ..models import FetchResult, make_article
from .common import get, soup, unique

URL = "https://www.kaijipress.com/"


def parse(text):
    articles = []
    category = "トップ"
    for tag in soup(text).find_all(["h2", "a"]):
        if tag.name == "h2":
            label = tag.get_text(strip=True)
            if label and len(label) < 30:
                category = label
            continue
        href = urljoin(URL, tag.get("href", ""))
        parts = urlsplit(href)
        if parts.hostname != "www.kaijipress.com" or not parts.path.startswith(
                ("/news/", "/markets/", "/person/", "/column/", "/feature/")):
            continue
        title = tag.get_text(" ", strip=True)
        if len(title) <= 5 or "一覧" in title:
            continue
        # Only a date inside this article's container is considered publication metadata.
        container = tag.find_parent(["li", "article"])
        time = container.find("time") if container else None
        date = None
        if time:
            raw = time.get("datetime", "") or time.get_text(strip=True)
            match = re.search(r"\d{4}[-/]\d{2}[-/]\d{2}", raw)
            if match:
                date = match.group(0)
        articles.append(make_article("k", category, title, href, date))
    return unique(articles)


def fetch(now):
    return FetchResult("k", parse(get(URL).text), method="HTML")
