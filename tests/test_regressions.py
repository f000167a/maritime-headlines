from datetime import datetime, timedelta
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from maritime_headlines.models import FetchResult, JST, make_article
from maritime_headlines.pipeline import run
from maritime_headlines.render import render
from maritime_headlines.scoring import load_config, score_article
from maritime_headlines.sources import jmd, kaijipress, splash247, tds
from maritime_headlines.storage import empty_state, load_state, merge_results, save_state, visible_articles

NOW = datetime(2026, 9, 5, 12, tzinfo=JST)
BS4_AVAILABLE = importlib.util.find_spec("bs4") is not None
if os.environ.get("REQUIRE_PARSER_TESTS") and not BS4_AVAILABLE:
    raise RuntimeError("CIではBeautifulSoupをインストールしてパーサーテストを実行してください")


def article(src="j", title="ケープサイズの市況", url="https://example.com/article", date=None):
    return make_article(src, "不定期", title, url, date)


def seeded(articles, now=NOW):
    results = [FetchResult(a["src"], [a], method="fixture") for a in articles]
    return merge_results(empty_state(), results, now, 7)


class HistoryAndScoringTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config()

    def test_fresh_personnel_is_not_hot(self):
        value = make_article("j", "人事", "役員人事のお知らせ", "https://example.com/personnel")
        stored = next(iter(seeded([value])["articles"].values()))
        scored = score_article(stored, self.config, NOW)
        self.assertEqual(scored["content_score"], 15)
        self.assertEqual(scored["rank_score"], 65)
        self.assertFalse(scored["is_hot"])

    def test_synonyms_count_once(self):
        stored = next(iter(seeded([article(title="ケープサイズ Capesize")])["articles"].values()))
        self.assertEqual(score_article(stored, self.config, NOW)["content_score"], 125)

    def test_mol_does_not_match_demolition(self):
        stored = next(iter(seeded([article(title="demolition")])["articles"].values()))
        groups = dict(score_article(stored, self.config, NOW)["matched_groups"])
        self.assertNotIn("mol", groups)
        self.assertIn("demolition", groups)

    def test_fixed_url_editions_are_separate(self):
        url = "https://www.tramp.co.jp/fenet/daily_report"
        first = tds.tds_article("9月4日：T/C市場＝24隻", url, NOW)
        second = tds.tds_article("9月5日：T/C市場＝26隻", url, NOW)
        state = seeded([first], NOW - timedelta(days=1))
        state = merge_results(state, [FetchResult("t", [second])], NOW, 7)
        self.assertEqual(len(state["articles"]), 2)
        self.assertEqual(state["articles"][second["id"]]["first_seen_at"], NOW.isoformat())
        self.assertEqual(second["published_date"], "2026-09-05")

    def test_same_edition_correction_keeps_first_seen(self):
        url = "https://www.tramp.co.jp/fenet/daily_report"
        old = tds.tds_article("9月5日：T/C市場＝24隻", url, NOW)
        new = tds.tds_article("9月5日：T/C市場＝25隻", url, NOW)
        earlier = NOW - timedelta(hours=1)
        state = merge_results(seeded([old], earlier), [FetchResult("t", [new])], NOW, 7)
        value = state["articles"][new["id"]]
        self.assertEqual(len(state["articles"]), 1)
        self.assertEqual(value["first_seen_at"], earlier.isoformat())
        self.assertEqual(value["content_updated_at"], NOW.isoformat())

    def test_fixed_url_without_date_uses_content_identity(self):
        url = "https://www.tramp.co.jp/fenet/daily_report"
        old = tds.tds_article("【本日のトピック】旧見出し", url, NOW)
        new = tds.tds_article("【本日のトピック】新見出し", url, NOW)
        self.assertNotEqual(old["id"], new["id"])
        self.assertIsNone(new["published_date"])

    def test_unchanged_article_does_not_become_new(self):
        value = article()
        earlier = NOW - timedelta(days=1)
        state = merge_results(seeded([value], earlier), [FetchResult("j", [value])], NOW, 7)
        stored = state["articles"][value["id"]]
        self.assertEqual(stored["first_seen_at"], earlier.isoformat())
        self.assertEqual(stored["content_updated_at"], earlier.isoformat())
        self.assertEqual(stored["last_seen_at"], NOW.isoformat())

    def test_publication_date_updates_without_resetting_first_seen(self):
        value = article()
        state = seeded([value], NOW - timedelta(hours=1))
        dated = article(date="2026-09-04")
        state = merge_results(state, [FetchResult("j", [dated])], NOW, 7)
        self.assertEqual(state["articles"][value["id"]]["published_date"], "2026-09-04")
        self.assertNotEqual(state["articles"][value["id"]]["first_seen_at"], NOW.isoformat())

    def test_expired_still_listed_story_does_not_resurrect(self):
        value = article()
        state = seeded([value], NOW - timedelta(days=8))
        for day in range(3):
            later = NOW + timedelta(days=day)
            state = merge_results(state, [FetchResult("j", [value])], later, 7)
            self.assertEqual(visible_articles(state, later, 7), [])
            self.assertIn(value["id"], state["articles"])

    def test_expired_story_absent_from_html(self):
        value = article(url="https://example.com/expired")
        state = seeded([value], NOW - timedelta(days=8))
        self.assertNotIn(value["url"], render(state, self.config, NOW))

    def test_old_publication_is_not_boosted_as_fresh(self):
        value = article(date="2026-08-01")
        state = seeded([value])
        self.assertEqual(visible_articles(state, NOW, 7), [])
        self.assertEqual(score_article(state["articles"][value["id"]], self.config, NOW)["recency_bonus"], 0)

    def test_fallback_url_change_preserves_splash_identity(self):
        old = article("s", "Dry bulk news", "https://splash247.com/story/", "2026-09-05")
        new = article("s", "Dry bulk news", "https://news.google.com/rss/articles/abc", "2026-09-05")
        earlier = NOW - timedelta(hours=1)
        state = merge_results(seeded([old], earlier), [FetchResult("s", [new])], NOW, 7)
        self.assertEqual(len(state["articles"]), 1)
        self.assertEqual(next(iter(state["articles"].values()))["first_seen_at"], earlier.isoformat())

    def test_tds_navigation_excluded_and_year_boundary(self):
        self.assertIsNone(tds.tds_article("目次（計7部門）", "https://www.tramp.co.jp/fenet/weekly_report", NOW))
        self.assertIsNone(tds.tds_article("鉄鉱石部門", "https://www.tramp.co.jp/fenet/weekly_general/detail_by_order/1", NOW))
        jan = datetime(2026, 1, 1, tzinfo=JST)
        self.assertEqual(tds.report_date("12月31日：T/C市場", jan), "2025-12-31")

    def test_weekly_event_date_is_not_a_publication_date(self):
        value = tds.tds_article("石炭部門：8月18日、政府が市場の動向を発表",
                                "https://www.tramp.co.jp/fenet/weekly_general/detail/1233/7988/2", NOW)
        self.assertIsNone(value["published_date"])

    def test_render_escapes_external_headline_and_rejects_unsafe_url(self):
        state = seeded([article(title='<script>alert("x")</script>')])
        page = render(state, self.config, NOW)
        self.assertIn("&lt;script&gt;", page)
        self.assertNotIn('<script>alert("x")', page)
        with self.assertRaises(ValueError):
            article(url="javascript:alert(1)")


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.state_path, self.output, self.status = root / "state.json", root / "dist", root / "status.json"

    def execute(self, fetchers):
        return run(self.state_path, self.output, self.status, fetchers=fetchers, now=NOW)

    def test_all_failure_preserves_exact_state_and_page(self):
        state = seeded([article()], NOW - timedelta(hours=1))
        save_state(self.state_path, state)
        original = self.state_path.read_bytes()
        self.output.mkdir()
        (self.output / "index.html").write_text("last successful page")
        with self.assertLogs(level="ERROR"):
            self.assertEqual(self.execute({"j": lambda _: FetchResult("j", error="HTTP 503")}), 1)
        self.assertEqual(self.state_path.read_bytes(), original)
        self.assertEqual((self.output / "index.html").read_text(), "last successful page")
        self.assertFalse(json.loads(self.status.read_text())["ok"])

    def test_empty_fetch_is_failure(self):
        with self.assertLogs(level="ERROR"):
            self.assertEqual(self.execute({"j": lambda _: FetchResult("j", [])}), 1)
        self.assertFalse(self.state_path.exists())
        self.assertFalse((self.output / "index.html").exists())

    def test_partial_failure_retains_prior_success_time_and_articles(self):
        old = NOW - timedelta(hours=1)
        value = article("k", url="https://example.com/retained")
        save_state(self.state_path, seeded([value], old))
        code = self.execute({"j": lambda _: FetchResult("j", [article()]),
                             "k": lambda _: FetchResult("k", error="HTTP 503")})
        self.assertEqual(code, 0)
        state = load_state(self.state_path)
        self.assertEqual(state["sources"]["k"]["last_success_at"], old.isoformat())
        self.assertFalse(state["sources"]["k"]["ok"])
        page = (self.output / "index.html").read_text()
        self.assertIn(value["url"], page)
        self.assertIn("取得失敗・保存記事を表示", page)

    def test_corrupt_state_is_not_silently_overwritten(self):
        self.state_path.write_text("invalid JSON")
        with self.assertRaises(json.JSONDecodeError):
            self.execute({"j": lambda _: FetchResult("j", [article()])})
        self.assertEqual(self.state_path.read_text(), "invalid JSON")

    def test_render_failure_does_not_replace_state_or_page(self):
        save_state(self.state_path, seeded([article()]))
        original = self.state_path.read_bytes()
        self.output.mkdir()
        (self.output / "index.html").write_text("old page")
        with patch("maritime_headlines.pipeline.render", side_effect=ValueError("bad template")):
            with self.assertRaises(ValueError):
                self.execute({"j": lambda _: FetchResult("j", [article()])})
        self.assertEqual(self.state_path.read_bytes(), original)
        self.assertEqual((self.output / "index.html").read_text(), "old page")

    def test_legacy_migration_preserves_detection_but_not_false_dates(self):
        first = (NOW - timedelta(days=1)).isoformat()
        value = {"src": "k", "cat": "ニュース", "title": "海事ニュース見出し", "date": "2026/09/04", "first_seen_iso": first}
        self.state_path.write_text(json.dumps({"https://example.com/a": value}))
        state = load_state(self.state_path)
        migrated = next(iter(state["articles"].values()))
        self.assertEqual(migrated["first_seen_at"], first)
        self.assertIsNone(migrated["published_date"])
        self.assertEqual(state["sources"], {})

    def test_timestamp_only_legacy_migration_is_completed_on_fetch(self):
        value = article()
        first = (NOW - timedelta(days=1)).isoformat()
        self.state_path.write_text(json.dumps({value["url"]: first}))
        state = merge_results(load_state(self.state_path), [FetchResult("j", [value])], NOW, 7)
        self.assertEqual(state["articles"][value["id"]]["first_seen_at"], first)
        self.assertEqual(state["articles"][value["id"]]["title"], value["title"])

    def test_preview_never_claims_a_new_success(self):
        save_state(self.state_path, seeded([article()], NOW - timedelta(days=1)))
        original = self.state_path.read_bytes()
        run(self.state_path, self.output, self.status, render_only=True, now=NOW)
        self.assertEqual(self.state_path.read_bytes(), original)
        self.assertIn("実サイトの取得は行っていません", (self.output / "index.html").read_text())


@unittest.skipUnless(BS4_AVAILABLE, "BeautifulSoupが未導入（CIでは必須実行）")
class HtmlParserTests(unittest.TestCase):
    def test_jmd_extracts_article_date_and_category(self):
        values = jmd.parse('<h2>不定期 記事一覧へ</h2><h3><a href="article.php?id=1">2026/09/04 ケープ市況</a></h3>')
        self.assertEqual(values[0]["published_date"], "2026-09-04")
        self.assertEqual(values[0]["title"], "ケープ市況")

    def test_kaijipress_does_not_use_page_date(self):
        values = kaijipress.parse('<header>2026年9月5日</header><h2>不定期</h2><li><a href="/news/1/">日付のない記事見出し</a></li>')
        self.assertIsNone(values[0]["published_date"])

    def test_kaijipress_uses_article_time(self):
        values = kaijipress.parse('<li><time datetime="2026-09-04T12:00:00+09:00"></time><a href="/news/1/">日付のある記事見出し</a></li>')
        self.assertEqual(values[0]["published_date"], "2026-09-04")

    def test_tds_keeps_multiple_editions_and_excludes_navigation(self):
        values = tds.parse('''<ul>
          <li><a href="/fenet/daily_report">9月4日：T/C市場＝24隻</a></li>
          <li><a href="/fenet/daily_report">9月5日：T/C市場＝25隻</a></li>
          <li><a href="/fenet/weekly_report">目次（計7部門）</a></li>
          <li><a href="/fenet/weekly_general/detail_by_order/1">鉄鉱石部門</a></li>
        </ul>''', NOW)
        self.assertEqual(len(values), 2)
        self.assertNotEqual(values[0]["id"], values[1]["id"])

    def test_wordpress_decodes_headline(self):
        values = splash247.parse_posts([{"title": {"rendered": "Bulk &amp; grain"}, "link": "https://splash247.com/a/", "date": "2026-09-05T00:00:00"}])
        self.assertEqual(values[0]["title"], "Bulk & grain")


class RssTests(unittest.TestCase):
    def test_google_fallback_strips_source_and_parses_date(self):
        xml = '<rss><channel><item><title>Bulk news - Splash247</title><link>https://news.google.com/rss/articles/a</link><pubDate>Fri, 04 Sep 2026 23:30:00 GMT</pubDate></item></channel></rss>'
        values = splash247.parse_rss(xml, NOW, google=True)
        self.assertEqual(values[0]["published_date"], "2026-09-05")
        self.assertEqual(values[0]["title"], "Bulk news")

    def test_splash_fallback_reports_failed_methods(self):
        response = type("Response", (), {"content": '<rss><channel><item><title>Bulk news</title><link>https://splash247.com/a/</link></item></channel></rss>'})()
        with patch.object(splash247, "get", side_effect=[RuntimeError("HTTP 503"), response]):
            result = splash247.fetch(NOW)
        self.assertTrue(result.ok)
        self.assertEqual(result.method, "RSS")
        self.assertIn("HTTP 503", result.warnings[0])


if __name__ == "__main__":
    unittest.main()
