"""Regression tests for the scoring and reporting guarantees.

Run from the repo root:

    python -m unittest discover -s tests -v

No network access is used. These cover the failure that shipped in the first
release: a crawl that fetched nothing still produced a score of 81.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import kovik_audit as k


class FakeCrawler:
    """Just enough of Crawler for the Analyzer, with no sockets."""

    def __init__(self, pages, start="https://example.in/"):
        self.pages = pages
        self.start = start
        self.robots_url = start.rstrip("/") + "/robots.txt"
        self.sitemap_urls = []
        self.max_depth = 2
        self.failed = []


def page(url="https://example.in/", **kw):
    p = k.Page(url=url)
    p.status = kw.pop("status", 200)
    p.elapsed_ms = kw.pop("elapsed_ms", 100)
    p.bytes = kw.pop("bytes", 20000)
    p.title = kw.pop("title", "A perfectly ordinary page title here")
    p.meta_description = kw.pop("meta_description", "d" * 120)
    p.h1 = kw.pop("h1", ["One"])
    p.h2 = kw.pop("h2", ["Two"])
    p.words = kw.pop("words", 500)
    p.https = kw.pop("https", True)
    p.lang = kw.pop("lang", "en-IN")
    p.viewport = kw.pop("viewport", "width=device-width")
    p.canonical = kw.pop("canonical", url)
    p.internal_links = kw.pop("internal_links", 8)
    p.headers = kw.pop("headers", {"content-encoding": "gzip", "cache-control": "max-age=60"})
    for key, value in kw.items():
        setattr(p, key, value)
    return p


class TestRefusesToScoreNothing(unittest.TestCase):
    def test_empty_crawl_raises_instead_of_scoring(self):
        a = k.Analyzer(FakeCrawler([]))
        with self.assertRaises(k.AuditError):
            a.run()

    def test_only_failed_pages_raises(self):
        pages = [page(status=502, error="HTTP 502"), page(url="https://example.in/b", status=0,
                                                          error="connection refused")]
        with self.assertRaises(k.AuditError):
            k.Analyzer(FakeCrawler(pages)).run()

    def test_all_pages_rendered_in_the_browser_raises(self):
        pages = [page(words=3, js_only=True, script_count=4),
                 page(url="https://example.in/b", words=2, js_only=True, script_count=3)]
        with self.assertRaises(k.AuditError):
            k.Analyzer(FakeCrawler(pages)).run()


class TestUnmeasuredChecksDoNotScore(unittest.TestCase):
    def test_skip_is_excluded_from_the_average(self):
        """A check nobody could run must not lift the score."""
        a = k.Analyzer(FakeCrawler([page()]))
        a.add("x", "Technical", "passes", "high", "pass", "", "", [], 1.0)
        a.add("y", "Technical", "fails", "high", "fail", "", "", [], 1.0)
        before = a.scores()["Technical"]
        a.add("z", "Technical", "never measured", "high", "skip", "", "", [], 1.0)
        after = a.scores()["Technical"]
        self.assertEqual(before, 50.0)
        self.assertEqual(after, 50.0)

    def test_category_with_no_measured_check_is_dropped_and_weights_renormalised(self):
        a = k.Analyzer(FakeCrawler([page()]))
        a.add("t", "Technical", "t", "high", "pass", "", "", [], 1.0)
        a.add("p", "Performance", "p", "high", "skip", "", "", [], 1.0)
        s = a.scores()
        self.assertEqual(s["Technical"], 100.0)
        self.assertIsNone(s["Performance"])
        self.assertEqual(s["Overall"], 100.0)  # not dragged toward 0 by the empty category

    def test_coverage_is_reported(self):
        a = k.Analyzer(FakeCrawler([page()]))
        a.add("t", "Technical", "t", "high", "pass", "", "", [], 1.0)
        a.add("o", "On-page", "o", "high", "skip", "", "", [], 1.0)
        a.scores()
        self.assertAlmostEqual(a.coverage, 0.30 / 0.55, places=3)

    def test_no_measured_check_at_all_gives_no_score(self):
        a = k.Analyzer(FakeCrawler([page()]))
        a.add("t", "Technical", "t", "high", "skip", "", "", [], 1.0)
        self.assertIsNone(a.scores()["Overall"])


class TestPageLevelChecksCannotPassOnAnEmptySet(unittest.TestCase):
    def test_onpage_checks_are_not_measured_when_content_pages_is_empty(self):
        a = k.Analyzer(FakeCrawler([page(words=2, js_only=True, script_count=5)]))
        a.check_titles()
        self.assertEqual(a.checks[0].status, "skip")
        self.assertIn("Not measured", a.checks[0].detail)

    def test_https_is_not_measured_on_a_local_mirror(self):
        a = k.Analyzer(FakeCrawler([page(url="http://127.0.0.1:8801/", https=False)],
                                   start="http://127.0.0.1:8801/"))
        a.check_https()
        self.assertEqual(a.checks[0].status, "skip")

    def test_https_still_fails_for_a_real_plain_http_site(self):
        a = k.Analyzer(FakeCrawler([page(url="http://example.in/", https=False)],
                                   start="http://example.in/"))
        a.check_https()
        self.assertEqual(a.checks[0].status, "fail")


class TestPageRowsKeepTheBadPages(unittest.TestCase):
    def test_failed_and_js_pages_appear_without_a_score(self):
        pages = [page(), page(url="https://example.in/gone", status=404, error="HTTP 404"),
                 page(url="https://example.in/app", words=2, js_only=True, script_count=3)]
        rows = k.Analyzer(FakeCrawler(pages)).page_rows()
        self.assertEqual(len(rows), 3)
        by_flag = {r["flag"]: r for r in rows}
        self.assertIsNotNone(by_flag[""]["score"])
        self.assertIsNone(by_flag["failed"]["score"])
        self.assertIsNone(by_flag["js"]["score"])


class TestRedaction(unittest.TestCase):
    def test_credentials_and_secret_query_values_are_stripped(self):
        out = k.redact_url("https://bob:hunter2@example.in/private?token=abc&page=2&api_key=Z")
        self.assertNotIn("hunter2", out)
        self.assertNotIn("abc", out)
        self.assertNotIn("Z", out)
        self.assertIn("page=2", out)

    def test_session_parameters_are_redacted(self):
        self.assertIn("[redacted]", k.redact_url("https://x.in/l?sessionid=deadbeef"))
        self.assertIn("[redacted]", k.redact_url("https://x.in/d?signature=9f8e"))

    def test_affected_lists_are_redacted_at_the_source(self):
        a = k.Analyzer(FakeCrawler([page()]))
        a.add("x", "Technical", "t", "high", "fail", "", "",
              ["https://x.in/a?token=supersecret"], 1.0)
        self.assertNotIn("supersecret", " ".join(a.checks[0].affected))

    def test_page_rows_are_redacted(self):
        rows = k.Analyzer(FakeCrawler([page(url="https://x.in/a?key=secret123")])).page_rows()
        self.assertNotIn("secret123", rows[0]["url"])


class TestPrivateOrigin(unittest.TestCase):
    def test_private_and_public(self):
        for url in ("http://localhost:8000/", "http://127.0.0.1:8801/",
                    "http://192.168.1.5/", "http://10.0.0.2/", "http://box.local/"):
            self.assertTrue(k.is_private_origin(url), url)
        for url in ("https://kovik.online/", "https://8.8.8.8/"):
            self.assertFalse(k.is_private_origin(url), url)


class TestReportSurvivesAnUnscoredAudit(unittest.TestCase):
    def test_disp_never_rewrites_the_host(self):
        a = k.Analyzer(FakeCrawler([page(url="http://127.0.0.1:8801/x.html")],
                                   start="http://127.0.0.1:8801/"))
        r = k.AuditReport(a, a.base, display_site="https://kovik.online")
        self.assertEqual(r.disp("http://127.0.0.1:8801/x.html"), "http://127.0.0.1:8801/x.html")


if __name__ == "__main__":
    unittest.main()
