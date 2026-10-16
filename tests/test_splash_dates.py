"""Splash API and fallback feeds must agree on publication dates and identity."""

from datetime import datetime, timedelta
import importlib.util
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from maritime_headlines.models import FetchResult, JST
from maritime_headlines.scoring import load_config, score_article
from maritime_headlines.sources import splash247
from maritime_headlines.storage import empty_state, merge_results

NOW = datetime(2026, 9, 5, 12, tzinfo=JST)
BS4_AVAILABLE = importlib.util.find_spec("bs4") is not None
if os.environ.get("REQUIRE_PARSER_TESTS") and not BS4_AVAILABLE:
    raise RuntimeError("CIではBeautifulSoupをインストールしてパーサーテストを実行してください")


def post(**dates):
    return {"title": {"rendered": "Dry bulk news"},
            "link": "https://splash247.com/story/", **dates}


def feed(date, google=False):
    url = "https://news.google.com/rss/articles/abc" if google else "https://splash247.com/story/"
    suffix = " - Splash247" if google else ""
    return (f"<rss><channel><item><title>Dry bulk news{suffix}</title><link>{url}</link>"
            f"<pubDate>{date}</pubDate></item></channel></rss>")


@unittest.skipUnless(BS4_AVAILABLE, "BeautifulSoupが未導入（CIでは必須実行）")
class WordpressPublicationTests(unittest.TestCase):
    def test_api_converts_gmt_to_jst_instead_of_slicing_site_local_date(self):
        value = post(date="2026-09-04T19:30:00", date_gmt="2026-09-04T23:30:00")
        self.assertEqual(splash247.parse_posts([value])[0]["published_date"], "2026-09-05")

    def test_api_accepts_explicit_offsets_and_year_rollover(self):
        for raw in ("2026-12-31T23:30:00", "2026-12-31T23:30:00Z",
                    "2026-12-31T23:30:00+00:00", "2027-01-01T02:30:00+03:00"):
            with self.subTest(raw=raw):
                value = post(date="2026-12-31T19:30:00", date_gmt=raw)
                self.assertEqual(splash247.parse_posts([value])[0]["published_date"], "2027-01-01")

    def test_missing_gmt_does_not_guess_the_site_timezone(self):
        for fields in ({}, {"date_gmt": None}, {"date_gmt": ""}):
            with self.subTest(fields=fields):
                value = post(date="2026-09-04T19:30:00", **fields)
                self.assertIsNone(splash247.parse_posts([value])[0]["published_date"])

    def test_missing_gmt_does_not_erase_a_previously_confirmed_date(self):
        known = splash247.parse_posts([post(date_gmt="2026-09-04T23:30:00")])
        unknown = splash247.parse_posts([post(date_gmt=None)])
        earlier = NOW - timedelta(hours=1)
        state = merge_results(empty_state(), [FetchResult("s", known)], earlier, 7)
        state = merge_results(state, [FetchResult("s", unknown)], NOW, 7)
        stored = next(iter(state["articles"].values()))
        self.assertEqual(stored["published_date"], "2026-09-05")
        self.assertEqual(stored["first_seen_at"], earlier.isoformat())

    def test_invalid_gmt_uses_the_existing_rss_fallback(self):
        responses = [Mock(json=lambda: [{"id": 7}]),
                     Mock(json=lambda: [post(date="2026-09-04T19:30:00", date_gmt="invalid")]),
                     Mock(content=feed("Fri, 04 Sep 2026 23:30:00 GMT"))]
        with patch.object(splash247, "get", side_effect=responses):
            result = splash247.fetch(NOW)
        self.assertTrue(result.ok)
        self.assertEqual(result.method, "RSS")
        self.assertEqual(result.articles[0]["published_date"], "2026-09-05")
        self.assertTrue(any("WordPress API:" in warning for warning in result.warnings))

    def test_api_fetch_requests_gmt_field(self):
        value = post(date_gmt="2026-09-04T23:30:00")
        with patch.object(splash247, "get", side_effect=[
                Mock(json=lambda: [{"id": 7}]), Mock(json=lambda: [value])]) as get:
            result = splash247.fetch(NOW)
        query = parse_qs(urlsplit(get.call_args_list[1].args[0]).query)
        self.assertIn("date_gmt", query["_fields"][0].split(","))
        self.assertTrue(result.ok)
        self.assertEqual(result.method, "WordPress API")

    def test_api_google_and_api_roundtrip_keeps_one_article_and_original_times(self):
        api = splash247.parse_posts([post(date="2026-09-04T19:30:00", date_gmt="2026-09-04T23:30:00")])
        rss = splash247.parse_rss(feed("Fri, 04 Sep 2026 23:30:00 GMT", google=True), NOW, google=True)
        earlier = NOW - timedelta(hours=1)
        state = merge_results(empty_state(), [FetchResult("s", api)], earlier, 7)
        state = merge_results(state, [FetchResult("s", rss)], NOW, 7)
        self.assertEqual(len(state["articles"]), 1)
        later = NOW + timedelta(hours=1)
        state = merge_results(state, [FetchResult("s", api)], later, 7)
        self.assertEqual(len(state["articles"]), 1)
        stored = next(iter(state["articles"].values()))
        self.assertEqual(stored["first_seen_at"], earlier.isoformat())
        self.assertEqual(stored["content_updated_at"], earlier.isoformat())
        self.assertEqual(stored["last_seen_at"], later.isoformat())
        self.assertEqual(stored["published_date"], "2026-09-05")

    def test_api_and_rss_have_identical_freshness_scores(self):
        api = splash247.parse_posts([post(date="2026-09-04T19:30:00", date_gmt="2026-09-04T23:30:00")])
        rss = splash247.parse_rss(feed("Fri, 04 Sep 2026 23:30:00 GMT"), NOW)
        scores = []
        for articles in (api, rss):
            state = merge_results(empty_state(), [FetchResult("s", articles)], NOW, 7)
            value = next(iter(state["articles"].values()))
            scores.append(score_article(value, load_config(), NOW)["rank_score"])
        self.assertEqual(scores[0], scores[1])


class RssPublicationTests(unittest.TestCase):
    @unittest.skipUnless(hasattr(time, "tzset"), "host timezone changes require tzset")
    def test_negative_zero_offset_is_utc_independent_of_host_timezone(self):
        try:
            for zone in ("UTC0", "JST-9", "EST5"):
                with self.subTest(zone=zone), patch.dict(os.environ, TZ=zone):
                    time.tzset()
                    articles = splash247.parse_rss(feed("Fri, 04 Sep 2026 23:30:00 -0000"), NOW)
                    self.assertEqual(articles[0]["published_date"], "2026-09-05")
        finally:
            time.tzset()

    @unittest.skipUnless(hasattr(time, "tzset"), "host timezone changes require tzset")
    def test_google_retention_boundary_uses_utc_for_negative_zero_offset(self):
        try:
            with patch.dict(os.environ, TZ="JST-9"):
                time.tzset()
                recent = splash247.parse_rss(feed("Sat, 29 Aug 2026 03:30:00 -0000", google=True), NOW, google=True)
                expired = splash247.parse_rss(feed("Sat, 29 Aug 2026 02:30:00 -0000", google=True), NOW, google=True)
                self.assertEqual(len(recent), 1)
                self.assertEqual(expired, [])
        finally:
            time.tzset()

    def test_rss_preserves_nonzero_timezone_offset(self):
        articles = splash247.parse_rss(feed("Fri, 04 Sep 2026 19:30:00 -0400"), NOW)
        self.assertEqual(articles[0]["published_date"], "2026-09-05")
