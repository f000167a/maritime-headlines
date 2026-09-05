from datetime import date
import re
from urllib.parse import urljoin, urlsplit

from ..models import FetchResult, fixed_tds_url, make_article
from .common import get, soup, unique

URL = "https://www.tramp.co.jp/"


def report_date(text, now):
    full = re.search(r"(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})日?", text)
    if full:
        return date(*map(int, full.groups())).isoformat()
    short = re.search(r"(\d{1,2})月(\d{1,2})日", text)
    if short:
        month, day = map(int, short.groups())
        candidates = []
        for year in range(now.year - 1, now.year + 2):
            try:
                candidates.append(date(year, month, day))
            except ValueError:
                pass
        if candidates:
            return min(candidates, key=lambda d: abs((d - now.date()).days)).isoformat()
    return None


def tds_article(title, href, now, published_date=None):
    path = urlsplit(href).path
    if "detail_by_order" in path or "目次" in title:
        return None
    if "daily" in path:
        category = "TDS Daily"
        if not any(word in title for word in ("トピック", "市場", "DAILY", "Daily")):
            return None
        kind = "topic" if "トピック" in title else "market"
    elif "weekly" in path:
        category, kind = "TDS Weekly", "weekly"
    else:
        return None
    date_text = published_date
    if not date_text and category == "TDS Daily":
        # Only the daily market heading is publication metadata. Dates quoted in
        # weekly excerpts describe events and must never become publication dates.
        heading = re.search(r"((?:\d{4}年)?\d{1,2}月\d{1,2}日)[：:]\s*(?:T/C|V/C|市場)", title)
        if heading:
            date_text = report_date(heading.group(1), now)
    issue = re.search(r"(\d+)号", title)
    edition = ("issue:" + issue.group(1)) if issue else date_text
    if fixed_tds_url(href) and category == "TDS Weekly" and not issue:
        return None  # generic links to the current issue are not headlines
    if len(title) < 5:
        return None
    return make_article("t", category, title, href, date_text, edition=edition, kind=kind)


def parse(text, now):
    document = soup(text)
    articles = []
    for li in document.find_all("li"):
        if li.find("li"):
            continue
        title = li.get_text(" ", strip=True)
        link = li.find("a", href=True)
        if link:
            href = urljoin(URL, link["href"])
        elif "トピック" in title or re.match(r"\d{1,2}月\d{1,2}日[：:]", title):
            href = urljoin(URL, "/fenet/daily_report")
        else:
            continue
        article = tds_article(title, href, now)
        if article:
            articles.append(article)
    for td in document.find_all("td"):
        link = td.find("a", href=True)
        if not link:
            continue
        previous = td.find_previous_sibling("td")
        published = report_date(previous.get_text(strip=True), now) if previous else None
        article = tds_article(link.get_text(" ", strip=True), urljoin(URL, link["href"]), now, published)
        if article:
            articles.append(article)
    return unique(articles)


def fetch(now):
    return FetchResult("t", parse(get(URL).text, now), method="HTML")
