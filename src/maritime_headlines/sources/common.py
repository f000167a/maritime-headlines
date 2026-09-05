"""HTTP validation is shared; HTML parsing remains source-specific."""

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; MaritimeHeadlines/7.0)",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}


def get(url):
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    retries = Retry(total=2, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504],
                    allowed_methods=["GET"], respect_retry_after_header=False)
    with requests.Session() as session:
        session.mount("https://", HTTPAdapter(max_retries=retries))
        response = session.get(url, headers=HEADERS, timeout=(5, 20))
        response.raise_for_status()
        response.encoding = response.apparent_encoding
        return response


def soup(text):
    from bs4 import BeautifulSoup
    return BeautifulSoup(text, "html.parser")


def unique(articles):
    return list({a["id"]: a for a in articles}.values())
