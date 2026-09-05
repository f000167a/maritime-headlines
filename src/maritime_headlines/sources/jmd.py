import re
from urllib.parse import urljoin

from ..models import FetchResult, make_article
from .common import get, soup, unique

URL = "https://www.jmd.co.jp/"


def parse(text):
    category = "トップ"
    articles = []
    for tag in soup(text).find_all(["h2", "h3"]):
        if tag.name == "h2":
            category = tag.get_text(strip=True).replace("記事一覧へ", "").strip() or category
            continue
        link = tag.find("a", href=True)
        if not link or "article.php" not in link["href"]:
            continue
        title = link.get_text(" ", strip=True)
        match = re.match(r"^(\d{4}/\d{2}/\d{2})\s*", title)
        date = match.group(1) if match else None
        title = title[match.end():].strip() if match else title
        if title:
            articles.append(make_article("j", category, title, urljoin(URL, link["href"]), date))
    return unique(articles)


def fetch(now):
    return FetchResult("j", parse(get(URL).text), method="HTML")
