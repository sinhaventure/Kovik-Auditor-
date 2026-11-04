#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Kovik SEO Audit Engine — single file.

Crawls a website, runs 26 SEO checks, scores five categories and writes a
watermarked PDF report.

    pip install requests beautifulsoup4 lxml reportlab matplotlib
    python kovik_audit.py https://example.com --pages 10 --out report.pdf

Kovik Digital Solutions · kovik.online · powered by Kovik Intelligence AI
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import ipaddress
import json
import os
import sys
import tempfile
import time
import urllib.robotparser as robotparser
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (BaseDocTemplate, Frame, Image, PageBreak, PageTemplate,
                                Paragraph, Spacer, Table, TableStyle)



# ==========================================================================
# CRAWLER
# ==========================================================================

# -*- coding: utf-8 -*-
"""Kovik SEO Audit Engine — crawler.

Crawls a site politely, same host only, and returns raw page records.
"""

import time
import urllib.robotparser as robotparser
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup

UA = "KovikAuditBot/1.0 (+https://kovik.online; SEO audit engine)"
DEFAULT_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
}


@dataclass
class Page:
    url: str
    status: int = 0
    elapsed_ms: int = 0
    bytes: int = 0
    content_type: str = ""
    title: str = ""
    meta_description: str = ""
    h1: List[str] = field(default_factory=list)
    h2: List[str] = field(default_factory=list)
    images: int = 0
    images_no_alt: int = 0
    internal_links: int = 0
    external_links: int = 0
    canonical: str = ""
    robots_meta: str = ""
    og_tags: Dict[str, str] = field(default_factory=dict)
    jsonld_blocks: List[str] = field(default_factory=list)
    jsonld_valid: int = 0
    jsonld_types: List[str] = field(default_factory=list)
    words: int = 0
    viewport: str = ""
    lang: str = ""
    https: bool = False
    redirect_chain: List[str] = field(default_factory=list)
    links: List[str] = field(default_factory=list)
    error: str = ""
    headers: Dict[str, str] = field(default_factory=dict)
    script_count: int = 0
    js_only: bool = False


def norm(url: str) -> str:
    url, _ = urldefrag(url)
    p = urlparse(url)
    path = p.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    q = ("?" + p.query) if p.query else ""
    return f"{p.scheme}://{p.netloc}{path}{q}"


def same_host(a: str, b: str) -> bool:
    ha, hb = urlparse(a).netloc.lower(), urlparse(b).netloc.lower()
    return ha.replace("www.", "") == hb.replace("www.", "")


# Query parameters whose value must never be printed, stored or put in a report.
SECRET_PARAMS = ("token", "access_token", "id_token", "refresh_token", "key", "apikey",
                 "api_key", "secret", "client_secret", "password", "passwd", "pwd",
                 "auth", "authorization", "session", "sessionid", "sid", "sig",
                 "signature", "otp", "pin", "code", "hash", "email")
SECRET_HINTS = ("token", "secret", "passw", "apikey", "api_key", "signature",
                "session", "auth", "cookie", "private")


def redact_url(url: str) -> str:
    """Strip credentials and secret-looking query values out of a URL.

    Everything the tool prints, exports or prints in a PDF goes through here, so a
    login link, a signed download or a session token in a query string cannot leak
    into a client report or a JSON dump.
    """
    try:
        p = urlparse(str(url))
    except ValueError:
        return "[unparseable url]"
    if not p.scheme or not p.netloc:
        return str(url)
    netloc = p.netloc
    if "@" in netloc:
        netloc = "***@" + netloc.split("@", 1)[1]
    query = ""
    if p.query:
        kept = []
        for part in p.query.split("&"):
            if not part:
                continue
            k = part.split("=", 1)[0]
            kl = k.lower()
            if kl in SECRET_PARAMS or any(h in kl for h in SECRET_HINTS):
                kept.append(f"{k}=[redacted]")
            else:
                kept.append(part)
        if kept:
            query = "?" + "&".join(kept)
    return f"{p.scheme}://{netloc}{p.path or '/'}{query}"


def is_private_origin(url: str) -> bool:
    """True for localhost, a private IP or an internal hostname.

    HTTPS, robots.txt and sitemap findings about a staging copy say nothing about
    the public domain, so they are reported as not measured instead of failed.
    """
    host = (urlparse(str(url)).hostname or "").strip().lower()
    if not host:
        return False
    if host == "localhost" or host.endswith((".local", ".internal", ".localhost")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved


class Crawler:
    def __init__(self, start_url: str, max_pages: int = 10, max_depth: int = 2,
                 delay: float = 0.4, timeout: int = 20, verbose: bool = True):
        self.start = norm(start_url)
        self.max_pages = max_pages
        self.max_depth = max_depth
        self.delay = delay
        self.timeout = timeout
        self.verbose = verbose
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        self.robots: Optional[robotparser.RobotFileParser] = None
        self.robots_url = ""
        self.sitemap_urls: List[str] = []
        self.pages: List[Page] = []
        self.seen: set = set()
        self.broken: List[dict] = []
        self.failed: List[dict] = []
        self.fetched_ok: int = 0

    # ---------------------------------------------------------------- robots
    def load_robots(self) -> None:
        parsed = urlparse(self.start)
        self.robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        rp = robotparser.RobotFileParser()
        try:
            r = self.session.get(self.robots_url, timeout=self.timeout)
            if r.status_code == 200:
                rp.parse(r.text.splitlines())
                for line in r.text.splitlines():
                    if line.lower().startswith("sitemap:"):
                        self.sitemap_urls.append(line.split(":", 1)[1].strip())
            else:
                rp.parse([])
        except requests.RequestException:
            rp.parse([])
        self.robots = rp

    def allowed(self, url: str) -> bool:
        if not self.robots:
            return True
        try:
            return self.robots.can_fetch(UA, url)
        except Exception:
            return True

    # ---------------------------------------------------------------- fetch
    def fetch(self, url: str) -> Page:
        page = Page(url=url)
        page.https = url.startswith("https://")
        try:
            t0 = time.time()
            resp = self.session.get(url, timeout=self.timeout, allow_redirects=True)
            page.elapsed_ms = int((time.time() - t0) * 1000)
            page.status = resp.status_code
            page.bytes = len(resp.content)
            page.content_type = resp.headers.get("Content-Type", "")
            # Cookies and auth headers are never stored: they end up in exports.
            page.headers = {k.lower(): v for k, v in resp.headers.items()
                            if k.lower() not in ("set-cookie", "cookie", "authorization",
                                                 "proxy-authorization", "www-authenticate")}
            page.redirect_chain = [h.url for h in resp.history] if resp.history else []
            if resp.status_code >= 400:
                page.error = f"HTTP {resp.status_code}"
                return page
            if "html" not in page.content_type.lower():
                page.error = "not HTML"
                return page
            self.parse(page, resp.text)
        except requests.RequestException as exc:
            page.error = str(exc)[:160]
        return page

    # ---------------------------------------------------------------- parse
    def parse(self, page: Page, html: str) -> None:
        soup = BeautifulSoup(html, "lxml")
        page.script_count = len(soup.find_all("script"))

        t = soup.find("title")
        page.title = t.get_text(strip=True) if t else ""

        md = soup.find("meta", attrs={"name": "description"})
        page.meta_description = (md.get("content") or "").strip() if md else ""

        page.h1 = [h.get_text(" ", strip=True) for h in soup.find_all("h1")]
        page.h2 = [h.get_text(" ", strip=True) for h in soup.find_all("h2")]

        imgs = soup.find_all("img")
        page.images = len(imgs)
        page.images_no_alt = sum(1 for i in imgs if not (i.get("alt") or "").strip())

        base_host = page.url
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.startswith(("mailto:", "tel:", "javascript:", "#")):
                continue
            target = urljoin(page.url, href)
            if urlparse(target).scheme not in ("http", "https"):
                continue
            if same_host(target, base_host):
                page.internal_links += 1
                page.links.append(norm(target))
            else:
                page.external_links += 1

        can = soup.find("link", rel=lambda v: v and "canonical" in v)
        page.canonical = (can.get("href") or "").strip() if can else ""

        rb = soup.find("meta", attrs={"name": "robots"})
        page.robots_meta = (rb.get("content") or "").strip() if rb else ""

        for prop in ("og:title", "og:description", "og:image", "og:type", "og:url"):
            tag = soup.find("meta", attrs={"property": prop})
            if tag and tag.get("content"):
                page.og_tags[prop] = tag["content"]

        for s in soup.find_all("script", attrs={"type": "application/ld+json"}):
            raw = s.string or s.get_text() or ""
            if raw.strip():
                page.jsonld_blocks.append(raw)
                try:
                    import json
                    data = json.loads(raw)
                    page.jsonld_valid += 1
                    items = data if isinstance(data, list) else [data]
                    for it in items:
                        if isinstance(it, dict) and it.get("@type"):
                            tp = it["@type"]
                            page.jsonld_types.extend(tp if isinstance(tp, list) else [tp])
                except Exception:
                    pass

        vp = soup.find("meta", attrs={"name": "viewport"})
        page.viewport = (vp.get("content") or "") if vp else ""

        html_tag = soup.find("html")
        page.lang = (html_tag.get("lang") or "") if html_tag else ""

        for junk in soup(["script", "style", "noscript", "template"]):
            junk.decompose()
        text = soup.get_text(" ", strip=True)
        page.words = len(text.split())

        # A page with no readable text, scripts on it and a framework mount point is
        # built in the browser. Marking it lets the checks say "cannot see this"
        # instead of scoring an empty shell as a bad page.
        html_l = html.lower()
        marker = any(m in html_l for m in FRAMEWORK_MARKERS)
        page.js_only = page.words < 40 and page.script_count > 0 and (marker or page.words < 12)

    # ---------------------------------------------------------------- crawl
    def run(self) -> List[Page]:
        self.load_robots()
        queue = deque([(self.start, 0)])
        self.seen.add(self.start)
        while queue and len(self.pages) < self.max_pages:
            url, depth = queue.popleft()
            if not self.allowed(url):
                if self.verbose:
                    print(f"  blocked by robots.txt: {redact_url(url)}")
                continue
            if self.verbose:
                print(f"  [{len(self.pages)+1}/{self.max_pages}] {redact_url(url)}")
            page = self.fetch(url)
            self.pages.append(page)
            if page.status >= 400 or page.error:
                self.failed.append({"url": url, "status": page.status, "error": page.error})
                self.broken.append({"url": url, "status": page.status, "error": page.error})
                time.sleep(self.delay)
                continue
            self.fetched_ok += 1
            if depth < self.max_depth:
                for target in page.links:
                    if target in self.seen:
                        continue
                    self.seen.add(target)
                    queue.append((target, depth + 1))
            time.sleep(self.delay)
        return self.pages

# ==========================================================================
# CHECKS AND SCORING
# ==========================================================================

# -*- coding: utf-8 -*-
"""Kovik SEO Audit Engine — checks and scoring."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional
from urllib.parse import urlparse

import requests


WEIGHTS = {
    "Technical": 0.30,
    "On-page": 0.25,
    "Content": 0.20,
    "Structured data": 0.15,
    "Performance": 0.10,
}

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

# A check that could not be measured contributes NOTHING to the score. It used to
# contribute a full pass, because an empty list of "pages with a problem" is also an
# empty list of "pages checked". That is how a site that never loaded scored 81.
STATUS_VALUE = {"pass": 1.0, "warn": 0.5, "fail": 0.0}
UNMEASURED = ("skip", "info")

# Below this share of the check weight we refuse to publish a number at all.
MIN_COVERAGE = 0.50

# Pages this thin, with scripts on them and a framework mount point, are almost
# certainly built in the browser. We cannot see their content, so we say so
# instead of scoring them as if they were empty pages.
FRAMEWORK_MARKERS = ("__next_data__", "ng-version", "data-reactroot", 'id="root"',
                     'id="app"', 'id="__nuxt"', 'id="svelte"', "svelte-", "data-v-app")


class AuditError(RuntimeError):
    """Raised when an audit cannot honestly be scored."""


@dataclass
class Check:
    id: str
    category: str
    title: str
    severity: str
    status: str
    detail: str
    fix: str
    affected: List[str] = field(default_factory=list)
    weight: float = 1.0


def short(url: str, n: int = 48) -> str:
    url = redact_url(url)
    p = urlparse(url)
    path = p.path or "/"
    s = p.netloc + path
    return s if len(s) <= n else s[: n - 1] + "\u2026"


def _content_check(id, category, title, severity, fix, weight):
    """Mark an on-page check 'not measured' when no page returned readable HTML.

    Without this, a site whose content is rendered in the browser produced an
    empty page list for every on-page check, and every one of them scored a pass.
    """
    def deco(fn):
        def wrapper(self):
            if not self.content_pages:
                return self._skip(
                    id, category, title, severity, fix,
                    "Not measured \u2014 no page returned readable HTML, so this check had "
                    "nothing to inspect. See 'Content visible without JavaScript'.",
                    weight)
            return fn(self)
        wrapper.__name__ = fn.__name__
        return wrapper
    return deco


class Analyzer:
    def __init__(self, crawler, timeout: int = 15):
        self.c = crawler
        self.pages: List[Page] = list(crawler.pages)
        self.ok_pages: List[Page] = [p for p in self.pages if p.status < 400 and not p.error]
        # Pages whose HTML actually carries the content. JS-built pages are excluded:
        # their empty body is a rendering limit of the crawler, not a content problem.
        self.content_pages: List[Page] = [p for p in self.ok_pages if not p.js_only]
        self.base = crawler.start
        self.timeout = timeout
        self.checks: List[Check] = []
        self.coverage = 0.0
        self._404_status: Optional[int] = None

    # ------------------------------------------------------------------ util
    def add(self, id, category, title, severity, status, detail, fix, affected=None, weight=1.0):
        # One choke point: nothing reaches the PDF, the JSON or the CSV carrying a
        # credential, a session token or a private query value.
        safe = [redact_url(str(a)) for a in (affected or [])]
        self.checks.append(Check(id, category, title, severity, status, detail, fix, safe, weight))

    def _skip(self, id, category, title, severity, fix, reason, weight=1.0):
        self.add(id, category, title, severity, "skip", reason, fix, [], weight)

    def _fetch_status(self, url: str) -> int:
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=self.timeout,
                             allow_redirects=True)
            return r.status_code
        except requests.RequestException:
            return 0

    # ---------------------------------------------------------------- checks
    def run(self) -> List[Check]:
        if not self.ok_pages:
            raise AuditError(
                "No page could be fetched, so there is nothing to measure. "
                "Refusing to produce a score \u2014 a number here would be invented.")
        if not self.content_pages:
            raise AuditError(
                "Every page returned almost no readable HTML: the content is built in the "
                "browser. A crawler cannot audit that, and a score here would describe the "
                "crawler rather than the site. Render or prerender the pages, or use a "
                "JavaScript-capable crawler. Nothing was scored.")
        self.check_https()
        self.check_robots()
        self.check_sitemap()
        self.check_404()
        self.check_status_codes()
        self.check_redirects()
        self.check_canonical()
        self.check_noindex()
        self.check_viewport()
        self.check_lang()
        self.check_titles()
        self.check_title_dupes()
        self.check_descriptions()
        self.check_desc_dupes()
        self.check_h1()
        self.check_headings()
        self.check_alt()
        self.check_jsrender()
        self.check_internal_links()
        self.check_thin_content()
        self.check_jsonld()
        self.check_og()
        self.check_local_schema()
        self.check_speed()
        self.check_weight()
        self.check_compression()
        self.check_cache()
        return self.checks

    def check_https(self):
        if is_private_origin(self.c.start):
            return self._skip(
                "https", "Technical", "HTTPS on every page", "critical",
                "Serve the whole site over HTTPS and 301-redirect http:// to https://.",
                f"Not measured \u2014 the crawl ran against {redact_url(self.c.start)}, a local "
                f"or private address. HTTPS on a staging copy says nothing about the public "
                f"domain, so this check cannot be graded.", 1.4)
        bad = [p.url for p in self.ok_pages if not p.https]
        self.add("https", "Technical", "HTTPS on every page", "critical",
                 "fail" if bad else "pass",
                 f"{len(bad)} of {len(self.ok_pages)} pages are served over plain HTTP." if bad
                 else "Every crawled page is served over HTTPS.",
                 "Serve the whole site over HTTPS and 301-redirect http:// to https://.",
                 bad, 1.4)

    def check_robots(self):
        exists = bool(self.c.robots_url) and self._fetch_status(self.c.robots_url) == 200
        self.add("robots", "Technical", "robots.txt reachable", "high",
                 "pass" if exists else "fail",
                 f"{redact_url(self.c.robots_url)} returns 200." if exists
                 else "No robots.txt found \u2014 search engines get no crawl guidance.",
                 "Publish a robots.txt that allows crawling and points to your sitemap.",
                 [] if exists else [self.c.robots_url], 1.0)

    def check_sitemap(self):
        parsed = urlparse(self.base)
        candidates = self.c.sitemap_urls or [f"{parsed.scheme}://{parsed.netloc}/sitemap.xml"]
        found, count = "", 0
        for url in candidates:
            try:
                r = requests.get(url, headers={"User-Agent": UA}, timeout=self.timeout)
                body = r.text.lower()
                if r.status_code == 200 and "<loc" in body:
                    found = url
                    count = body.count("<loc")
                    break
            except requests.RequestException:
                continue
        self.add("sitemap", "Technical", "XML sitemap present", "high",
                 "pass" if found else "fail",
                 f"{redact_url(found)} lists {count} URLs." if found
                 else "No usable sitemap.xml was found.",
                 "Publish an XML sitemap, list every indexable page, and submit it in Search Console.",
                 [] if found else [candidates[0]], 1.2)

    def check_404(self):
        parsed = urlparse(self.base)
        probe_id = int(hashlib.md5(self.base.encode("utf-8")).hexdigest()[:6], 16) % 99999
        probe = f"{parsed.scheme}://{parsed.netloc}/kovik-audit-missing-page-{probe_id}"
        code = self._fetch_status(probe)
        self._404_status = code
        ok = code == 404
        self.add("404", "Technical", "Broken URLs return 404", "medium",
                 "pass" if ok else ("warn" if code in (403, 410) else "fail"),
                 f"A non-existent URL returned HTTP {code}." + (" Correct." if ok else
                 " Search engines treat a 200 here as a soft 404 and waste crawl budget."),
                 "Serve a real 404 status for missing pages, with a helpful page body.",
                 [], 0.8)

    def check_status_codes(self):
        bad = [p for p in self.pages if p.status >= 400 or p.error]
        detail = ("All crawled pages returned a healthy status." if not bad else
                  f"{len(bad)} of {len(self.pages)} crawled pages could not be retrieved.")
        self.add("status", "Technical", "No broken pages in the crawl", "critical",
                 "pass" if not bad else "fail", detail,
                 "Fix or redirect every URL that returns 4xx or 5xx.",
                 [f"{short(p.url)} \u2192 {('HTTP ' + str(p.status)) if p.status else (p.error or 'no response')}"
                  for p in bad], 1.5)

    def check_redirects(self):
        red = [p for p in self.ok_pages if p.redirect_chain]
        self.add("redirects", "Technical", "Pages resolve without redirect chains", "low",
                 "pass" if not red else "warn",
                 "No redirect chains found." if not red
                 else f"{len(red)} pages reached their final URL through a redirect.",
                 "Link to the final URL directly; keep redirects to a single hop.",
                 [short(p.url) for p in red], 0.6)

    def check_canonical(self):
        missing = [p for p in self.ok_pages if not p.canonical]
        self.add("canonical", "Technical", "Canonical tag on every page", "high",
                 "pass" if not missing else "fail",
                 "Every page declares a canonical URL." if not missing
                 else f"{len(missing)} pages have no canonical tag.",
                 "Add a self-referencing canonical link to every indexable page.",
                 [short(p.url) for p in missing], 1.0)

    def check_noindex(self):
        blocked = [p for p in self.ok_pages if "noindex" in (p.robots_meta or "").lower()]
        self.add("noindex", "Technical", "No accidental noindex", "critical",
                 "pass" if not blocked else "warn",
                 "No crawled page is marked noindex." if not blocked
                 else f"{len(blocked)} pages are marked noindex and will not rank.",
                 "Remove the noindex meta from any page you want in search results.",
                 [short(p.url) for p in blocked], 1.2)

    def check_viewport(self):
        bad = [p for p in self.ok_pages if not p.viewport]
        self.add("viewport", "Technical", "Mobile viewport declared", "high",
                 "pass" if not bad else "fail",
                 "Every page declares a responsive viewport." if not bad
                 else f"{len(bad)} pages have no viewport meta tag.",
                 'Add <meta name="viewport" content="width=device-width, initial-scale=1">.',
                 [short(p.url) for p in bad], 1.0)

    def check_lang(self):
        bad = [p for p in self.ok_pages if not p.lang]
        self.add("lang", "Technical", "Page language declared", "low",
                 "pass" if not bad else "warn",
                 "Every page declares a language." if not bad
                 else f"{len(bad)} pages have no lang attribute.",
                 'Add lang="en-IN" (or the correct language) to the <html> element.',
                 [short(p.url) for p in bad], 0.5)

    # --------------------------------------------------------------- on-page
    @_content_check("title", "On-page", "Title tags present and well sized", "critical",
                    "Write a unique 30\u201360 character title per page: service + city + brand.", 1.6)
    def check_titles(self):
        missing = [p for p in self.content_pages if not p.title]
        short_t = [p for p in self.content_pages if 0 < len(p.title) < 25]
        long_t = [p for p in self.content_pages if len(p.title) > 62]
        status = "fail" if missing else ("warn" if (short_t or long_t) else "pass")
        detail = "Every page has a title of a sensible length." if status == "pass" else \
            f"{len(missing)} pages without a title, {len(short_t)} too short, {len(long_t)} too long."
        self.add("title", "On-page", "Title tags present and well sized", "critical", status, detail,
                 "Write a unique 30\u201360 character title per page: service + city + brand.",
                 [short(p.url) for p in (missing + short_t + long_t)], 1.6)

    @_content_check("title_dupes", "On-page", "No duplicate title tags", "high",
                    "Give every page its own title; duplicates compete with each other.", 1.2)
    def check_title_dupes(self):
        seen: Dict[str, List[str]] = {}
        for p in self.content_pages:
            if p.title:
                seen.setdefault(p.title.strip().lower(), []).append(short(p.url))
        dupes = {k: v for k, v in seen.items() if len(v) > 1}
        self.add("title_dupes", "On-page", "No duplicate title tags", "high",
                 "pass" if not dupes else "fail",
                 "All titles are unique." if not dupes
                 else f"{len(dupes)} titles are shared by more than one page.",
                 "Give every page its own title; duplicates compete with each other.",
                 [u for v in dupes.values() for u in v], 1.2)

    @_content_check("description", "On-page", "Meta descriptions written", "high",
                    "Write a 120\u2013160 character description that gives a reason to click.", 1.3)
    def check_descriptions(self):
        missing = [p for p in self.content_pages if not p.meta_description]
        short_d = [p for p in self.content_pages if 0 < len(p.meta_description) < 70]
        long_d = [p for p in self.content_pages if len(p.meta_description) > 165]
        status = "fail" if missing else ("warn" if (short_d or long_d) else "pass")
        self.add("description", "On-page", "Meta descriptions written", "high", status,
                 "Every page has a well-sized meta description." if status == "pass" else
                 f"{len(missing)} pages without one, {len(short_d)} too short, {len(long_d)} too long.",
                 "Write a 120\u2013160 character description that gives a reason to click.",
                 [short(p.url) for p in (missing + short_d + long_d)], 1.3)

    @_content_check("desc_dupes", "On-page", "No duplicate meta descriptions", "medium",
                    "Write a distinct description for each page.", 0.8)
    def check_desc_dupes(self):
        seen: Dict[str, List[str]] = {}
        for p in self.content_pages:
            if p.meta_description:
                seen.setdefault(p.meta_description.strip().lower(), []).append(short(p.url))
        dupes = {k: v for k, v in seen.items() if len(v) > 1}
        self.add("desc_dupes", "On-page", "No duplicate meta descriptions", "medium",
                 "pass" if not dupes else "warn",
                 "All descriptions are unique." if not dupes
                 else f"{len(dupes)} descriptions are repeated across pages.",
                 "Write a distinct description for each page.",
                 [u for v in dupes.values() for u in v], 0.8)

    @_content_check("h1", "On-page", "One clear H1 per page", "high",
                    "Use a single H1 that states the page's subject, then H2 for sections.", 1.3)
    def check_h1(self):
        none = [p for p in self.content_pages if not p.h1]
        many = [p for p in self.content_pages if len(p.h1) > 1]
        status = "fail" if none else ("warn" if many else "pass")
        self.add("h1", "On-page", "One clear H1 per page", "high", status,
                 "Every page has exactly one H1." if status == "pass" else
                 f"{len(none)} pages have no H1, {len(many)} have more than one.",
                 "Use a single H1 that states the page's subject, then H2 for sections.",
                 [short(p.url) for p in (none + many)], 1.3)

    @_content_check("headings", "On-page", "Sections marked with H2", "low",
                    "Break content into H2 sections so both readers and crawlers can scan it.", 0.6)
    def check_headings(self):
        no_h2 = [p for p in self.content_pages if not p.h2]
        self.add("headings", "On-page", "Sections marked with H2", "low",
                 "pass" if not no_h2 else "warn",
                 "Every page has section headings." if not no_h2
                 else f"{len(no_h2)} pages jump straight from H1 to body text.",
                 "Break content into H2 sections so both readers and crawlers can scan it.",
                 [short(p.url) for p in no_h2], 0.6)

    @_content_check("alt", "On-page", "Images have alt text", "medium",
                    "Describe what each image shows; alt text feeds image search and accessibility.", 1.0)
    def check_alt(self):
        total = sum(p.images for p in self.content_pages)
        missing = sum(p.images_no_alt for p in self.content_pages)
        if total == 0:
            return self.add("alt", "On-page", "Images have alt text", "medium", "info",
                            "No images on the measured pages, so there is nothing to check.",
                            "Describe what each image shows; alt text feeds image search.",
                            [], 1.0)
        ratio = missing / total
        status = "pass" if missing == 0 else ("warn" if ratio < 0.3 else "fail")
        affected = [f"{short(p.url)} \u2014 {p.images_no_alt} of {p.images} images"
                    for p in self.content_pages if p.images_no_alt]
        self.add("alt", "On-page", "Images have alt text", "medium", status,
                 f"{missing} of {total} images have no alt text.",
                 "Describe what each image shows; alt text feeds image search and accessibility.",
                 affected, 1.0)

    def check_jsrender(self):
        js = [p for p in self.ok_pages if p.js_only]
        if not js:
            return self.add("jsrender", "Content", "Content visible without JavaScript", "high",
                            "pass",
                            "Every page returned its content in the first HTML response.",
                            "Keep the content in the server response so crawlers see it too.",
                            [], 1.4)
        status = "fail" if len(js) == len(self.ok_pages) else "warn"
        self.add("jsrender", "Content", "Content visible without JavaScript", "high", status,
                 f"{len(js)} of {len(self.ok_pages)} pages returned almost no readable HTML \u2014 "
                 f"the content is assembled in the browser. Search engines may index an empty "
                 f"page, and this audit cannot measure those pages.",
                 "Render the page on the server (SSR or static generation) or prerender it, so "
                 "the HTML already contains the content.",
                 [short(p.url) for p in js], 1.4)

    @_content_check("internal_links", "Content", "Pages link to each other", "medium",
                    "Link related pages from the body copy, not just the menu.", 0.9)
    def check_internal_links(self):
        thin = [p for p in self.content_pages if p.internal_links < 5]
        self.add("internal_links", "Content", "Pages link to each other", "medium",
                 "pass" if not thin else "warn",
                 "Every page carries at least five internal links." if not thin
                 else f"{len(thin)} pages have fewer than five internal links.",
                 "Link related pages from the body copy, not just the menu.",
                 [short(p.url) for p in thin], 0.9)

    @_content_check("thin", "Content", "Enough substance on the page", "high",
                    "Add the detail a customer needs: what is included, prices, timings, proof.", 1.2)
    def check_thin_content(self):
        thin = [p for p in self.content_pages if p.words < 300]
        status = "pass" if not thin else ("warn" if len(thin) <= 2 else "fail")
        self.add("thin", "Content", "Enough substance on the page", "high", status,
                 "Every page carries at least 300 words." if not thin
                 else f"{len(thin)} pages carry under 300 words of visible text.",
                 "Add the detail a customer needs: what is included, prices, timings, proof.",
                 [f"{short(p.url)} \u2014 {p.words} words" for p in thin], 1.2)

    # ------------------------------------------------------- structured data
    @_content_check("jsonld", "Structured data", "Schema.org markup", "high",
                    "Add LocalBusiness/Organization plus page-type schema, and validate it.", 1.3)
    def check_jsonld(self):
        missing = [p for p in self.content_pages if not p.jsonld_blocks]
        invalid = [p for p in self.content_pages
                   if p.jsonld_blocks and p.jsonld_valid < len(p.jsonld_blocks)]
        status = "pass" if not missing and not invalid else ("warn" if not missing else "fail")
        types = sorted({t for p in self.content_pages for t in p.jsonld_types})
        self.add("jsonld", "Structured data", "Schema.org markup", "high", status,
                 (f"Valid JSON-LD on every page. Types found: {', '.join(types[:8])}." if status == "pass"
                  else f"{len(missing)} pages have no JSON-LD; {len(invalid)} have invalid blocks."),
                 "Add LocalBusiness/Organization plus page-type schema, and validate it.",
                 [short(p.url) for p in (missing + invalid)], 1.3)

    @_content_check("og", "Structured data", "Open Graph tags for sharing", "low",
                    "Add og:title, og:description and a 1200\u00d7630 og:image to every page.", 0.7)
    def check_og(self):
        need = ["og:title", "og:description", "og:image"]
        bad = [p for p in self.content_pages if any(k not in p.og_tags for k in need)]
        self.add("og", "Structured data", "Open Graph tags for sharing", "low",
                 "pass" if not bad else "warn",
                 "Every page is ready to share." if not bad
                 else f"{len(bad)} pages are missing some Open Graph tags.",
                 "Add og:title, og:description and a 1200\u00d7630 og:image to every page.",
                 [short(p.url) for p in bad], 0.7)

    @_content_check("local_schema", "Structured data", "Local business identity markup", "medium",
                    "Declare your business once \u2014 name, address, phone, hours, area served.", 0.9)
    def check_local_schema(self):
        local_types = {"LocalBusiness", "ProfessionalService", "Store", "MedicalBusiness",
                       "Restaurant", "Dentist", "Organization"}
        found = any(local_types & set(p.jsonld_types) for p in self.content_pages)
        self.add("local_schema", "Structured data", "Local business identity markup", "medium",
                 "pass" if found else "warn",
                 "A business entity type is declared." if found
                 else "No LocalBusiness or Organization entity was found in the markup.",
                 "Declare your business once \u2014 name, address, phone, hours, area served.",
                 [], 0.9)

    # ---------------------------------------------------------- performance
    def check_speed(self):
        times = [p.elapsed_ms for p in self.ok_pages if p.elapsed_ms]
        if not times:
            return self._skip("speed", "Performance", "Server responds quickly", "high",
                              "Move to a host close to your customers and enable server-side caching.",
                              "Not measured \u2014 no response times were recorded.", 1.3)
        avg = int(sum(times) / len(times))
        slow = [p for p in self.ok_pages if p.elapsed_ms > 1200]
        status = "pass" if avg < 800 and not slow else ("warn" if avg < 1500 else "fail")
        self.add("speed", "Performance", "Server responds quickly", "high", status,
                 f"Average response time {avg} ms across {len(times)} pages.",
                 "Move to a host close to your customers and enable server-side caching.",
                 [f"{short(p.url)} \u2014 {p.elapsed_ms} ms" for p in slow], 1.3)

    def check_weight(self):
        heavy = [p for p in self.ok_pages if p.bytes > 500_000]
        avg_kb = int(sum(p.bytes for p in self.ok_pages) / max(len(self.ok_pages), 1) / 1024)
        status = "pass" if not heavy else "warn"
        self.add("weight", "Performance", "Page weight under control", "medium", status,
                 f"Average HTML weight {avg_kb} KB; {len(heavy)} pages above 500 KB.",
                 "Compress images, drop unused scripts and defer what is not needed first.",
                 [f"{short(p.url)} \u2014 {p.bytes // 1024} KB" for p in heavy], 1.0)

    def check_compression(self):
        enc = [p.headers.get("content-encoding", "") for p in self.ok_pages]
        good = sum(1 for e in enc if any(k in e for k in ("gzip", "br", "zstd")))
        status = "pass" if good == len(self.ok_pages) else ("warn" if good else "fail")
        self.add("compression", "Performance", "Text compression enabled", "medium", status,
                 f"{good} of {len(self.ok_pages)} pages were served compressed.",
                 "Enable gzip or brotli on the server \u2014 it usually halves transfer size.",
                 [short(p.url) for p in self.ok_pages
                  if not any(k in p.headers.get("content-encoding", "") for k in ("gzip", "br", "zstd"))],
                 0.9)

    def check_cache(self):
        cached = [p for p in self.ok_pages if p.headers.get("cache-control")]
        status = "pass" if len(cached) == len(self.ok_pages) else "warn"
        self.add("cache", "Performance", "Cache headers set", "low", status,
                 f"{len(cached)} of {len(self.ok_pages)} pages send a Cache-Control header.",
                 "Set long cache lifetimes on assets and a short one on HTML.",
                 [short(p.url) for p in self.ok_pages if not p.headers.get("cache-control")], 0.6)

    # -------------------------------------------------------------- scoring
    def scores(self) -> Dict[str, float]:
        """Category and overall scores over MEASURED checks only.

        Checks that could not be measured are left out of both the numerator and
        the denominator. A category with nothing measured is dropped and the
        remaining weights are renormalised, so a missing check can never be
        mistaken for a good one.
        """
        out: Dict[str, Optional[float]] = {}
        measured_cat_weight = 0.0
        measured_total = 0.0
        possible_total = 0.0

        for cat, weight in WEIGHTS.items():
            all_items = [c for c in self.checks if c.category == cat]
            items = [c for c in all_items if c.status not in UNMEASURED]
            possible_total += weight * sum(c.weight for c in all_items)
            measured_total += weight * sum(c.weight for c in items)
            if not items:
                out[cat] = None
                continue
            num = sum(STATUS_VALUE[c.status] * c.weight for c in items)
            den = sum(c.weight for c in items)
            out[cat] = round(100 * num / den, 1)
            measured_cat_weight += weight

        self.coverage = round(measured_total / possible_total, 3) if possible_total else 0.0

        if measured_cat_weight <= 0:
            out["Overall"] = None
            return out

        overall = sum(out[c] * w for c, w in WEIGHTS.items() if out.get(c) is not None)
        out["Overall"] = round(overall / measured_cat_weight, 1)
        return out

    def grade(self, score: Optional[float]) -> str:
        if score is None:
            return "Not scored"
        if score >= 85:
            return "Strong"
        if score >= 70:
            return "Good, with gaps"
        if score >= 50:
            return "Needs work"
        return "Critical"

    def issues(self, only_failing: bool = True) -> List[Check]:
        items = [c for c in self.checks if c.status in ("fail", "warn")] if only_failing else list(self.checks)
        return sorted(items, key=lambda c: (SEVERITY_ORDER.get(c.severity, 9), c.status != "fail"))

    def unmeasured(self) -> List[Check]:
        return [c for c in self.checks if c.status in UNMEASURED]

    def page_rows(self) -> List[dict]:
        """One row per crawled URL \u2014 including the ones that failed or need JavaScript.

        Failed pages used to vanish from this table, so the report claimed more
        pages than it showed and every hidden page looked clean.
        """
        rows = []
        for p in self.pages:
            row = {
                "url": redact_url(p.url),
                "status": p.status or 0,
                "score": None,
                "title_len": len(p.title),
                "desc_len": len(p.meta_description),
                "h1": len(p.h1),
                "words": p.words,
                "imgs": p.images,
                "no_alt": p.images_no_alt,
                "ms": p.elapsed_ms,
                "kb": round(p.bytes / 1024),
                "jsonld": len(p.jsonld_blocks),
                "flag": "",
                "note": "",
            }
            if p.status >= 400 or p.error:
                row["flag"] = "failed"
                row["note"] = p.error or f"HTTP {p.status}"
                rows.append(row)
                continue
            if p.js_only:
                row["flag"] = "js"
                row["note"] = "content built in the browser \u2014 not measured"
                rows.append(row)
                continue
            problems = 0
            if not p.title:
                problems += 1
            if len(p.title) > 62:
                problems += 1
            if not p.meta_description:
                problems += 1
            if len(p.h1) != 1:
                problems += 1
            if p.images_no_alt:
                problems += 1
            if p.words < 300:
                problems += 1
            if not p.jsonld_blocks:
                problems += 1
            row["score"] = max(0, 100 - problems * 14)
            rows.append(row)
        return rows

# ==========================================================================
# REPORT
# ==========================================================================

# -*- coding: utf-8 -*-
"""Kovik SEO Audit Engine — PDF report."""

import os
import tempfile
from datetime import datetime
from typing import Dict, List

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Wedge

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import (BaseDocTemplate, Frame, Image, KeepTogether, PageBreak,
                                PageTemplate, Paragraph, Spacer, Table, TableStyle)

INK = colors.HexColor("#0b1210")
INK_SOFT = colors.HexColor("#16221e")
TEAL = colors.HexColor("#0e5e52")
MINT = colors.HexColor("#2fbfa4")
CLAY = colors.HexColor("#b4552f")
AMBER = colors.HexColor("#c08a2e")
GREY = colors.HexColor("#5c6a64")
LIGHT = colors.HexColor("#f2f5f3")
LINE = colors.HexColor("#dfe5e2")

SEV_COLOR = {
    "critical": colors.HexColor("#a3341f"),
    "high": colors.HexColor("#c0561f"),
    "medium": colors.HexColor("#b0821f"),
    "low": colors.HexColor("#4b6b62"),
    "info": GREY,
}
STATUS_LABEL = {"pass": "PASS", "warn": "IMPROVE", "fail": "FIX", "info": "INFO"}


class AuditReport:
    def __init__(self, analyzer, site_url: str, brand="Kovik Digital Solutions",
                 powered_by="Kovik Intelligence AI", site_name="kovik.online",
                 client: str = "", display_site: str = ""):
        self.a = analyzer
        self.measured = site_url
        self.label = (display_site or "").strip()
        self.site = self.label or site_url
        self._origin = analyzer.base
        self._mirrored = bool(self.label) and not same_host(self.label, analyzer.base)
        self.brand = brand
        self.powered = powered_by
        self.site_name = site_name
        self.client = client
        self.scores = analyzer.scores()
        self.issues = analyzer.issues()
        self.date = datetime.now().strftime("%d %B %Y")
        self.tmp: List[str] = []
        self.styles = self._styles()

    def disp(self, url: str) -> str:
        """Print the URL exactly as it was measured, minus anything secret.

        A --label used to rewrite the host of every finding, so a crawl of
        127.0.0.1 was reported as "kovik.online is served over plain HTTP" — a
        false critical finding about the live domain. The label is a caption now,
        and this only redacts.
        """
        return redact_url(url)

    def _mirror_note(self) -> str:
        if not self._mirrored:
            return ""
        return (f"<b>Measured on {redact_url(self.measured)}</b>, a copy of "
                f"{redact_url(self.label)}. Every URL below is the address that was actually "
                f"fetched. Findings about HTTPS, robots.txt and the sitemap describe that copy, "
                f"not the public domain.")

    # ---------------------------------------------------------------- styles
    def _styles(self):
        ss = getSampleStyleSheet()
        add = ss.add
        add(ParagraphStyle("KBody", parent=ss["BodyText"], fontName="Helvetica",
                           fontSize=9.5, leading=14, textColor=colors.HexColor("#243330"),
                           alignment=TA_LEFT))
        add(ParagraphStyle("KSmall", parent=ss["BodyText"], fontName="Helvetica",
                           fontSize=8, leading=11.5, textColor=GREY))
        add(ParagraphStyle("KH1", parent=ss["Heading1"], fontName="Helvetica-Bold",
                           fontSize=17, leading=21, textColor=INK, spaceAfter=10, spaceBefore=4))
        add(ParagraphStyle("KH2", parent=ss["Heading2"], fontName="Helvetica-Bold",
                           fontSize=11.5, leading=15, textColor=TEAL, spaceBefore=14, spaceAfter=6))
        add(ParagraphStyle("KCell", parent=ss["BodyText"], fontName="Helvetica",
                           fontSize=7.6, leading=10, textColor=colors.HexColor("#243330")))
        add(ParagraphStyle("KCellB", parent=ss["BodyText"], fontName="Helvetica-Bold",
                           fontSize=7.6, leading=10, textColor=INK))
        add(ParagraphStyle("KEyebrow", parent=ss["BodyText"], fontName="Helvetica-Bold",
                           fontSize=7.5, leading=10, textColor=MINT))
        return ss

    # --------------------------------------------------------------- charts
    def _chart_scores(self) -> str:
        cats = [c for c in WEIGHTS if self.scores.get(c) is not None]
        vals = [self.scores[c] for c in cats]
        if not cats:
            return ""
        fig, ax = plt.subplots(figsize=(6.4, 2.5), dpi=200)
        fig.patch.set_facecolor("white")
        bars = ax.barh(cats, vals, color="#2fbfa4", height=0.55)
        for b, v in zip(bars, vals):
            b.set_color("#0e5e52" if v >= 70 else ("#b0821f" if v >= 50 else "#a3341f"))
            if v >= 88:
                ax.text(v - 4, b.get_y() + b.get_height() / 2, f"{v:.0f}", ha="right",
                        va="center", fontsize=8, color="white", fontweight="bold")
            else:
                ax.text(v + 2, b.get_y() + b.get_height() / 2, f"{v:.0f}",
                        va="center", fontsize=8, color="#243330", fontweight="bold")
        ax.set_xlim(0, 100)
        ax.set_xticks([0, 25, 50, 75, 100])
        ax.tick_params(colors="#5c6a64", labelsize=8)
        for s in ("top", "right", "left"):
            ax.spines[s].set_visible(False)
        ax.spines["bottom"].set_color("#dfe5e2")
        ax.grid(axis="x", color="#eef2f0", linewidth=0.8)
        ax.set_axisbelow(True)
        path = os.path.join(tempfile.gettempdir(), "kovik_scores.png")
        fig.tight_layout()
        fig.savefig(path, facecolor="white")
        plt.close(fig)
        self.tmp.append(path)
        return path

    def _chart_severity(self) -> str:
        counts: Dict[str, int] = {}
        for c in self.issues:
            counts[c.severity] = counts.get(c.severity, 0) + 1
        order = [k for k in ("critical", "high", "medium", "low") if counts.get(k)]
        if not order:
            order, counts = ["low"], {"low": 1}
        sizes = [counts[k] for k in order]
        cols = {"critical": "#a3341f", "high": "#c0561f", "medium": "#b0821f", "low": "#4b6b62"}
        fig, ax = plt.subplots(figsize=(2.6, 2.6), dpi=200)
        ax.pie(sizes, colors=[cols[k] for k in order], startangle=90,
               wedgeprops=dict(width=0.38, edgecolor="white", linewidth=2))
        ax.text(0, 0.08, str(sum(sizes)), ha="center", va="center",
                fontsize=20, fontweight="bold", color="#0b1210")
        ax.text(0, -0.22, "issues", ha="center", va="center", fontsize=8, color="#5c6a64")
        path = os.path.join(tempfile.gettempdir(), "kovik_sev.png")
        fig.savefig(path, transparent=True, bbox_inches="tight")
        plt.close(fig)
        self.tmp.append(path)
        return path

    # ------------------------------------------------------------- decorate
    def _decorate(self, canv, doc):
        canv.saveState()
        w, h = A4
        if doc.page == 1:
            canv.setFillColor(INK)
            canv.rect(0, 0, w, h, stroke=0, fill=1)
            canv.setFillColor(colors.HexColor("#12211d"))
            canv.circle(w - 18 * mm, h - 26 * mm, 62 * mm, stroke=0, fill=1)
            canv.setFillColor(colors.HexColor("#0f1b18"))
            canv.circle(6 * mm, 34 * mm, 44 * mm, stroke=0, fill=1)
            self._cover_text(canv, w, h)
            canv.restoreState()
            return

        canv.setFillColor(colors.HexColor("#f7f9f8"))
        canv.rect(0, 0, w, h, stroke=0, fill=1)

        # diagonal watermark
        canv.saveState()
        canv.setFillColor(colors.Color(0.06, 0.16, 0.14, alpha=0.05))
        canv.setFont("Helvetica-Bold", 34)
        canv.translate(w / 2, h / 2)
        canv.rotate(38)
        canv.drawCentredString(0, 0, "KOVIK DIGITAL SOLUTIONS")
        canv.setFont("Helvetica-Bold", 13)
        canv.drawCentredString(0, -18 * mm, "POWERED BY KOVIK INTELLIGENCE AI")
        canv.restoreState()

        canv.setStrokeColor(LINE)
        canv.setLineWidth(0.6)
        canv.line(18 * mm, h - 16 * mm, w - 18 * mm, h - 16 * mm)
        canv.setFont("Helvetica-Bold", 7.5)
        canv.setFillColor(GREY)
        canv.drawString(18 * mm, h - 13.4 * mm, self.brand.upper())
        canv.drawRightString(w - 18 * mm, h - 13.4 * mm, self.site_name.upper())
        canv.line(18 * mm, 14 * mm, w - 18 * mm, 14 * mm)
        canv.setFont("Helvetica", 7)
        canv.setFillColor(GREY)
        canv.drawString(18 * mm, 10 * mm, f"Powered by {self.powered}  \u00b7  {self.site_name}")
        canv.drawRightString(w - 18 * mm, 10 * mm, f"Page {doc.page - 1}")
        canv.restoreState()

    def _cover_text(self, c, w, h):
        # brand mark
        c.setFillColor(TEAL)
        c.roundRect(20 * mm, h - 34 * mm, 13 * mm, 13 * mm, 3 * mm, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 15)
        c.drawCentredString(26.5 * mm, h - 30 * mm, "K")
        c.setFont("Helvetica-Bold", 11)
        c.drawString(37 * mm, h - 27 * mm, "KOVIK")
        c.setFont("Helvetica", 6.5)
        c.setFillColor(MINT)
        c.drawString(37 * mm, h - 30.6 * mm, "D I G I T A L   S O L U T I O N S")

        c.setFillColor(MINT)
        c.setFont("Helvetica-Bold", 8)
        c.drawString(20 * mm, h - 58 * mm, "S E O   A U D I T   R E P O R T")

        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 26)
        c.drawString(20 * mm, h - 74 * mm, "Website visibility audit")

        c.setFillColor(colors.HexColor("#c9d8d2"))
        c.setFont("Helvetica", 11)
        c.drawString(20 * mm, h - 86 * mm, self.site[:70])

        y = h - 100 * mm
        if self.client:
            c.setFillColor(MINT)
            c.setFont("Helvetica-Bold", 7)
            c.drawString(20 * mm, y, "PREPARED FOR")
            c.setFillColor(colors.white)
            c.setFont("Helvetica", 12)
            c.drawString(20 * mm, y - 6 * mm, self.client)
            y -= 16 * mm

        # score block
        cx, cy, r = w - 62 * mm, h - 96 * mm, 26 * mm
        c.setStrokeColor(colors.HexColor("#1e332e"))
        c.setLineWidth(7)
        c.circle(cx, cy, r, stroke=1, fill=0)
        overall = self.scores.get("Overall")
        c.setStrokeColor(MINT)
        frac = max(0.0, min(1.0, (overall or 0.0) / 100.0))
        c.setLineWidth(7)
        c.arc(cx - r, cy - r, cx + r, cy + r, startAng=90, extent=-360 * frac)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 30)
        c.drawCentredString(cx, cy + 2 * mm, f"{overall:.0f}" if overall is not None else "—")
        c.setFillColor(colors.HexColor("#9fb4ad"))
        c.setFont("Helvetica", 7)
        c.drawCentredString(cx, cy - 8 * mm, "OUT OF 100" if overall is not None else "NOT SCORED")
        c.setFillColor(MINT)
        c.setFont("Helvetica-Bold", 9)
        c.drawCentredString(cx, cy - r - 9 * mm, self.a.grade(overall).upper())

        # facts
        c.setFillColor(colors.HexColor("#8ba39b"))
        c.setFont("Helvetica", 9)
        measured = sum(1 for c in self.a.checks if c.status not in UNMEASURED)
        lines = [
            f"Date of audit      {self.date}",
            f"Pages fetched      {len(self.a.ok_pages)} of {len(self.a.pages)} tried",
            f"Checks measured    {measured} of {len(self.a.checks)}",
            f"Issues found       {len(self.issues)}",
        ]
        if self._mirrored:
            lines.append(f"Measured on        {redact_url(self.measured)[:44]}")
        yy = h - 132 * mm
        for ln in lines:
            c.drawString(20 * mm, yy, ln)
            yy -= 6 * mm

        # what's inside
        inside = [
            "Executive summary with a weighted score across five categories",
            f"{len(self.issues)} findings, each with the evidence and the fix",
            f"Page-by-page table for {len(self.a.pages)} crawled URLs",
            "Method, scoring weights and the limits of this audit",
        ]
        c.setFillColor(MINT)
        c.setFont("Helvetica-Bold", 7)
        c.drawString(20 * mm, 118 * mm, "I N S I D E   T H I S   R E P O R T")
        c.setFillColor(colors.HexColor("#c9d8d2"))
        c.setFont("Helvetica", 8.5)
        yy = 111 * mm
        for item in inside:
            c.setFillColor(MINT)
            c.drawString(20 * mm, yy, "\u2014")
            c.setFillColor(colors.HexColor("#c9d8d2"))
            c.drawString(25 * mm, yy, item)
            yy -= 5.6 * mm

        # footer band
        c.setFillColor(colors.HexColor("#0f1b18"))
        c.rect(0, 0, w, 34 * mm, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 8)
        c.drawString(20 * mm, 22 * mm, self.brand.upper())
        c.setFillColor(colors.HexColor("#8ba39b"))
        c.setFont("Helvetica", 7.5)
        c.drawString(20 * mm, 17 * mm, "Hajipur, Bihar  \u00b7  kovik.online  \u00b7  +91 91992 64615")
        c.setFillColor(MINT)
        c.setFont("Helvetica-Bold", 7.5)
        c.drawRightString(w - 20 * mm, 22 * mm, f"POWERED BY {self.powered.upper()}")
        c.setFillColor(colors.HexColor("#8ba39b"))
        c.setFont("Helvetica", 7)
        c.drawRightString(w - 20 * mm, 17 * mm, "Our own crawl, scoring and reporting engine")

    # ---------------------------------------------------------------- build
    def build(self, path: str) -> str:
        doc = BaseDocTemplate(path, pagesize=A4,
                              leftMargin=18 * mm, rightMargin=18 * mm,
                              topMargin=24 * mm, bottomMargin=20 * mm,
                              title=f"SEO Audit \u2014 {self.site}", author=self.brand,
                              subject="Technical and on-page SEO audit",
                              creator=f"{self.powered} \u00b7 {self.site_name}")
        frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
        doc.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=self._decorate)])

        story: List = [Spacer(1, 1), PageBreak()]
        story += self._summary()
        story += self._issues_section()
        story += self._pages_section()
        story += self._method_section()
        doc.build(story)
        for f in self.tmp:
            try:
                os.remove(f)
            except OSError:
                pass
        return path

    # ------------------------------------------------------------- summary
    def _summary(self) -> List:
        s = self.styles
        story: List = []
        story.append(Paragraph("Executive summary", s["KH1"]))
        story.append(Paragraph(
            f"A technical and on-page audit of <b>{redact_url(self.site)}</b>, covering "
            f"{len(self.a.ok_pages)} fetched pages. Every finding below was measured, not assumed — "
            f"each check records what was found on the page and what to change. Checks that could "
            f"not be measured are listed as such and left out of the score rather than counted as "
            f"passes.", s["KBody"]))
        if self._mirrored:
            story.append(Spacer(1, 3 * mm))
            story.append(Paragraph(self._mirror_note(), s["KSmall"]))
        story.append(Spacer(1, 6 * mm))

        overall = self.scores.get("Overall")
        score_txt = f"{overall:.0f}" if overall is not None else "—"
        head = Table(
            [[Paragraph(f'<font size="30" color="#0b1210"><b>{score_txt}</b></font>'
                        f'<font size="11" color="#5c6a64"> / 100</font>', s["KCell"]),
              Paragraph(f'<b>{self.a.grade(overall)}</b><br/>'
                        f'<font size="8" color="#5c6a64">Overall visibility score</font>', s["KCell"]),
              Paragraph(f'<b>{len(self.issues)}</b> issues found<br/>'
                        f'<font size="8" color="#5c6a64">'
                        f'{sum(1 for c in self.issues if c.status == "fail")} blocking, '
                        f'{sum(1 for c in self.issues if c.status == "warn")} to improve</font>', s["KCell"])]],
            colWidths=[42 * mm, 62 * mm, doc_width_rest()])
        head.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), LIGHT),
            ("BOX", (0, 0), (-1, -1), 0.5, LINE),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ]))
        story.append(head)
        story.append(Spacer(1, 7 * mm))

        chart = self._chart_scores()
        if chart:
            story.append(Image(chart, width=165 * mm, height=64 * mm))
            story.append(Spacer(1, 4 * mm))

        # priorities
        story.append(Paragraph("Fix these first", s["KH2"]))
        fails = [c for c in self.issues if c.status == "fail"]
        warns = [c for c in self.issues if c.status == "warn"]
        top = (fails + warns)[:5]
        if not top:
            story.append(Paragraph("Nothing critical was found in this crawl. "
                                   "Keep the monthly cadence and watch Core Web Vitals.", s["KBody"]))
        else:
            rows = [[Paragraph("#", s["KCellB"]), Paragraph("Issue", s["KCellB"]),
                     Paragraph("Why it matters", s["KCellB"]), Paragraph("Severity", s["KCellB"])]]
            for i, c in enumerate(top, 1):
                rows.append([Paragraph(str(i), s["KCell"]),
                             Paragraph(f"<b>{c.title}</b><br/><font size='7' color='#5c6a64'>{c.detail}</font>", s["KCell"]),
                             Paragraph(c.fix, s["KCell"]),
                             Paragraph(f'<font color="{sev_hex(c.severity)}"><b>{c.severity.upper()}</b></font>', s["KCell"])])
            t = Table(rows, colWidths=[8 * mm, 74 * mm, 68 * mm, 22 * mm], repeatRows=1)
            t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
                ("GRID", (0, 0), (-1, -1), 0.4, LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]))
            story.append(t)
        story.append(PageBreak())
        return story

    # -------------------------------------------------------------- issues
    def _issues_section(self) -> List:
        s = self.styles
        story: List = [Paragraph("Findings in detail", s["KH1"]),
                       Paragraph("Grouped by severity. Each row states what was measured, "
                                 "on how many pages, and what to do about it.", s["KBody"]),
                       Spacer(1, 5 * mm)]
        if not self.issues:
            story.append(Paragraph("No failing checks in this crawl.", s["KBody"]))
            story.append(PageBreak())
            return story

        sev_chart = self._chart_severity()
        row = Table([[Image(sev_chart, width=42 * mm, height=42 * mm),
                      Paragraph(self._severity_text(), s["KBody"])]],
                    colWidths=[48 * mm, doc_width_rest()])
        row.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                 ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
        story.append(row)
        story.append(Spacer(1, 5 * mm))

        for sev in ("critical", "high", "medium", "low"):
            group = [c for c in self.issues if c.severity == sev]
            if not group:
                continue
            story.append(Paragraph(f"{sev.upper()} — {len(group)} issue"
                                   f"{'s' if len(group) > 1 else ''}", s["KH2"]))
            for c in group:
                story.append(self._issue_block(c))
                story.append(Spacer(1, 2.5 * mm))
        story.append(PageBreak())
        return story

    def _severity_text(self) -> str:
        n_fail = sum(1 for c in self.issues if c.status == "fail")
        n_warn = sum(1 for c in self.issues if c.status == "warn")
        return (f"<b>{n_fail} checks failed</b> and <b>{n_warn} need improvement</b>. "
                f"Critical items cost you rankings or trust today; high items cost you clicks; "
                f"medium and low items are the difference between ranking and ranking well.")

    def _issue_block(self, c) -> Table:
        s = self.styles
        left = [
            Paragraph(f'<font color="{sev_hex(c.severity)}"><b>{c.severity.upper()}</b></font>'
                      f'  ·  {c.category}', s["KEyebrow"]),
            Spacer(1, 1.5 * mm),
            Paragraph(f"<b>{c.title}</b>", ParagraphStyle(
                "t", parent=s["KCell"], fontSize=9.5, leading=12.5, textColor=INK)),
            Spacer(1, 1.5 * mm),
            Paragraph(c.detail, s["KCell"]),
        ]
        if c.affected:
            shown = c.affected[:4]
            more = len(c.affected) - len(shown)
            left.append(Spacer(1, 1.5 * mm))
            left.append(Paragraph("Affected: " + "; ".join(self.disp(x) for x in shown) +
                                  (f" and {more} more" if more > 0 else ""), s["KSmall"]))
        right = [Paragraph('<font color="#0e5e52"><b>WHAT TO DO</b></font>', s["KEyebrow"]),
                 Spacer(1, 1.5 * mm),
                 Paragraph(c.fix, s["KCell"])]
        t = Table([[left, right]], colWidths=[112 * mm, doc_width_rest() - 6 * mm])
        t.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 0), (0, 0), colors.white),
            ("BACKGROUND", (1, 0), (1, 0), colors.HexColor("#f2f8f6")),
            ("BOX", (0, 0), (-1, -1), 0.5, LINE),
            ("LINEBEFORE", (1, 0), (1, 0), 1.4, MINT),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ]))
        return t

    # --------------------------------------------------------------- pages
    def _pages_section(self) -> List:
        s = self.styles
        story: List = [Paragraph("Page-by-page", s["KH1"]),
                       Paragraph("Measured per page. A page score below 70 has more than two "
                                 "structural problems.", s["KBody"]), Spacer(1, 4 * mm)]
        rows = [[Paragraph(h, s["KCellB"]) for h in
                 ["URL", "Status", "Score", "Title", "Desc", "H1", "Words", "Alt gaps", "ms", "KB"]]]
        flagged = []
        for r in self.a.page_rows():
            if r["flag"] == "js":
                status_txt, score_txt = "JS", "—"
            elif r["flag"] == "failed":
                status_txt, score_txt = (str(r["status"]) if r["status"] else "ERR"), "—"
            else:
                status_txt, score_txt = str(r["status"]), f"<b>{r['score']}</b>"
            if r["flag"]:
                flagged.append(r)
            rows.append([
                Paragraph(self.disp(r["url"]).replace("&", "&amp;")[:64], s["KCell"]),
                Paragraph(status_txt, s["KCell"]),
                Paragraph(score_txt, s["KCell"]),
                Paragraph(str(r["title_len"]), s["KCell"]),
                Paragraph(str(r["desc_len"]), s["KCell"]),
                Paragraph(str(r["h1"]), s["KCell"]),
                Paragraph(str(r["words"]), s["KCell"]),
                Paragraph(str(r["no_alt"]), s["KCell"]),
                Paragraph(str(r["ms"]), s["KCell"]),
                Paragraph(str(r["kb"]), s["KCell"]),
            ])
        t = Table(rows, colWidths=[74 * mm, 13 * mm, 13 * mm, 12 * mm, 12 * mm, 9 * mm,
                                   13 * mm, 15 * mm, 12 * mm, 11 * mm], repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
            ("GRID", (0, 0), (-1, -1), 0.35, LINE),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafbfa")]),
        ]))
        story.append(t)
        if flagged:
            kinds = {r["flag"] for r in flagged}
            note = []
            if "js" in kinds:
                note.append("<b>JS</b> means the content is assembled in the browser and could "
                            "not be measured.")
            if "failed" in kinds:
                note.append("A page that did not load keeps its error status and no score.")
            note.append("Neither is scored, and neither counts as a pass.")
            story.append(Spacer(1, 2 * mm))
            story.append(Paragraph(" ".join(note), s["KSmall"]))
        story.append(PageBreak())
        return story

    # -------------------------------------------------------------- method
    def _method_section(self) -> List:
        s = self.styles
        measured = sum(1 for c in self.a.checks if c.status not in UNMEASURED)
        skipped = len(self.a.checks) - measured
        story: List = [Paragraph("Method and limits", s["KH1"]),
                       Paragraph(
                           f"The engine fetched {len(self.a.ok_pages)} pages from "
                           f"{redact_url(self.measured)} (same host only, robots.txt respected, "
                           f"{self.a.c.max_depth} levels deep), parsed the HTML, and ran "
                           f"{len(self.a.checks)} checks. {measured} of them could be measured; "
                           f"{skipped} could not, and are excluded from the score. Response times "
                           f"are a single measurement from one location at one moment — treat "
                           f"them as a signal, not a benchmark.", s["KBody"]),
                       Spacer(1, 3 * mm)]
        if self.a.c.failed:
            failed = "; ".join(redact_url(f["url"]) + " (" +
                               (f["error"] or "HTTP " + str(f["status"])) + ")"
                               for f in self.a.c.failed[:6])
            story.append(Paragraph(
                f"<b>Pages that did not load:</b> {failed}. They are counted as broken, not "
                f"as clean.", s["KSmall"]))
            story.append(Spacer(1, 3 * mm))
        story += [
            Paragraph("<b>What this audit does not cover:</b> backlinks and authority, "
                      "search volume and keyword difficulty, competitor rankings, "
                      "Core Web Vitals field data from real users, paid search, or "
                      "Google Business Profile performance. Those need Search Console, "
                      "Analytics and a rank-tracking source.", s["KBody"]),
            Spacer(1, 3 * mm),
            Paragraph("<b>Scores</b> are a weighted average: Technical 30%, On-page 25%, "
                      "Content 20%, Structured data 15%, Performance 10%. A pass scores 1, "
                      "an improvement 0.5, a failure 0. A check that could not be measured "
                      "scores nothing and is removed from both sides of the average, so a "
                      "page that never loaded can never lift a score.", s["KBody"]),
            Spacer(1, 8 * mm)]
        box = Table([[Paragraph(
            f"<b>Prepared by {self.brand}</b><br/>"
            f"Hajipur, Bihar · kovik.online · +91 91992 64615<br/>"
            f"<font color='#5c6a64'>Powered by {self.powered} — our own crawl, scoring and reporting "
            f"engine, built in-house.</font>", s["KCell"])]],
            colWidths=[doc_width_rest()])
        box.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), LIGHT),
            ("BOX", (0, 0), (-1, -1), 0.5, LINE),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ]))
        story.append(box)
        return story


def doc_width_rest():
    return A4[0] - 36 * mm - 42 * mm - 62 * mm


def sev_hex(sev: str) -> str:
    return {"critical": "#a3341f", "high": "#c0561f", "medium": "#b0821f",
            "low": "#4b6b62", "info": "#5c6a64"}.get(sev, "#5c6a64")

# ==========================================================================
# COMMAND LINE
# ==========================================================================

C_BOLD, C_DIM, C_TEAL, C_CLAY, C_RESET = "\033[1m", "\033[2m", "\033[36m", "\033[31m", "\033[0m"


def color(txt, code):
    return txt if os.environ.get("NO_COLOR") else f"{code}{txt}{C_RESET}"


def banner():
    print()
    print(color("  KOVIK  ", C_TEAL) + color("· SEO AUDIT ENGINE", C_BOLD))
    print(color("  powered by Kovik Intelligence AI · kovik.online", C_DIM))
    print()


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="kovik_audit.py",
        description="Crawl a website and produce a scored SEO audit report (PDF, JSON, CSV). "
                    "Refuses to score a site it could not fetch.")
    ap.add_argument("url", help="Start URL, e.g. https://example.com")
    ap.add_argument("--pages", type=int, default=10, help="Maximum pages to crawl (default 10)")
    ap.add_argument("--depth", type=int, default=2, help="Link depth from the start URL (default 2)")
    ap.add_argument("--out", default=None, help="Output PDF path (default audit-<host>.pdf)")
    ap.add_argument("--json", dest="json_out", default=None, help="Also write raw results as JSON")
    ap.add_argument("--csv", dest="csv_out", default=None, help="Also write the issue list as CSV")
    ap.add_argument("--brand", default="Kovik Digital Solutions", help="Brand name on the report")
    ap.add_argument("--client", default="", help="Client name shown on the cover")
    ap.add_argument("--delay", type=float, default=0.4, help="Seconds between requests (be polite)")
    ap.add_argument("--quiet", action="store_true", help="Suppress crawl progress")
    ap.add_argument("--label", default="",
                    help="Public URL to caption the report with when you audit a staging copy. "
                         "Caption only: every measured URL is printed exactly as it was fetched.")
    args = ap.parse_args(argv)

    if not args.url.startswith(("http://", "https://")):
        args.url = "https://" + args.url

    banner()
    t0 = time.time()
    crawler = Crawler(args.url, max_pages=args.pages, max_depth=args.depth,
                      delay=args.delay, verbose=not args.quiet)
    print(color("Crawling", C_BOLD))
    crawler.run()

    # ---- gate 1: the page we were asked to audit has to have actually loaded.
    start_page = next((p for p in crawler.pages if p.url == crawler.start), None)
    if start_page is None or start_page.status >= 400 or start_page.error:
        reason = (start_page.error or f"HTTP {start_page.status}") if start_page \
            else "the start URL was never fetched"
        reason = reason.split(")")[0][:120] if "(" in reason[:60] else reason[:120]
        print(color(f"\n  Cannot audit {redact_url(args.url)} \u2014 {reason}", C_CLAY))
        print(color("  No page was measured, so no score and no report were written.", C_CLAY))
        if crawler.failed:
            for f in crawler.failed[:5]:
                print(color(f"    {redact_url(f['url'])} \u2014 "
                            f"{f['error'] or 'HTTP ' + str(f['status'])}", C_DIM))
        print()
        return 2

    ok_pages = [p for p in crawler.pages if p.status < 400 and not p.error]
    js_pages = [p for p in ok_pages if p.js_only]
    failed = len(crawler.pages) - len(ok_pages)
    line = f"\n  Fetched {len(ok_pages)} of {len(crawler.pages)} pages"
    if failed:
        line += f"  \u00b7  {failed} failed"
    if js_pages:
        line += f"  \u00b7  {len(js_pages)} rendered in the browser"
    print(color(line, C_BOLD))

    # ---- gate 2: the engine itself refuses to analyse an empty crawl.
    analyzer = Analyzer(crawler)
    try:
        analyzer.run()
    except AuditError as exc:
        print(color(f"\n  {exc}", C_CLAY))
        print()
        return 2

    scores = analyzer.scores()
    issues = analyzer.issues()
    overall = scores.get("Overall")

    # ---- gate 3: not enough of the checklist was measurable to justify a number.
    if overall is None or analyzer.coverage < MIN_COVERAGE:
        print(color(f"\n  Only {analyzer.coverage * 100:.0f}% of the checklist could be measured. "
                    f"That is not enough to publish a score.", C_CLAY))
        print(color("  Nothing was written. Fix the fetch problem and run it again.", C_DIM))
        print()
        return 2

    # terminal summary
    print()
    print(f"  Overall score  {color(f'{overall:.0f}/100', C_BOLD)}  ({analyzer.grade(overall)})")
    for cat in WEIGHTS:
        v = scores.get(cat)
        if v is None:
            print(f"  {cat:<16} {color('not measured', C_DIM)}")
            continue
        bar = "\u2588" * int(v / 5) + "\u2591" * (20 - int(v / 5))
        print(f"  {cat:<16} {color(bar, C_TEAL)} {v:.0f}")
    print(f"  {'coverage':<16} {analyzer.coverage * 100:.0f}% of the checklist measured")
    print()
    fails = [c for c in issues if c.status == "fail"]
    warns = [c for c in issues if c.status == "warn"]
    print(f"  {color(str(len(fails)), C_CLAY)} failing \u00b7 {len(warns)} to improve "
          f"\u00b7 {len(analyzer.unmeasured())} not measurable")
    for c in issues[:6]:
        tag = color("FIX ", C_CLAY) if c.status == "fail" else color("IMPR", C_DIM)
        print(f"    {tag} [{c.severity:<8}] {c.title}")
    print()

    host = urlparse(crawler.start).netloc.replace("www.", "") or "site"
    out = args.out or f"audit-{host}-{time.strftime('%Y%m%d')}.pdf"

    report = AuditReport(analyzer, crawler.start, brand=args.brand, client=args.client,
                         site_name=host, display_site=args.label.strip())
    report.build(out)
    print(f"  PDF  \u2192 {color(out, C_BOLD)}")

    if args.json_out:
        payload = {
            "tool": "Kovik SEO Audit Engine",
            "schema": 2,
            "measured_origin": redact_url(crawler.start),
            "label": args.label.strip(),
            "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "pages_requested": args.pages,
            "pages_fetched": len(ok_pages),
            "pages_failed": [{"url": redact_url(f["url"]), "status": f["status"],
                              "error": f["error"]} for f in crawler.failed],
            "pages_js_only": [redact_url(p.url) for p in js_pages],
            "coverage": analyzer.coverage,
            "score_valid": overall is not None,
            "scores": scores,
            "checks": [c.__dict__ for c in analyzer.checks],
            "pages": analyzer.page_rows(),
            "redaction": "URL credentials, session tokens and secret query values are removed; "
                         "page bodies, headers, cookies and raw JSON-LD are never exported.",
        }
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        print(f"  JSON \u2192 {args.json_out}")

    if args.csv_out:
        with open(args.csv_out, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["severity", "category", "check", "status", "detail", "fix", "affected"])
            for c in issues:
                w.writerow([c.severity, c.category, c.title, c.status, c.detail, c.fix,
                            " | ".join(redact_url(u) for u in c.affected[:10])])
        print(f"  CSV  \u2192 {args.csv_out}")

    print(color(f"\n  Done in {time.time() - t0:.1f}s\n", C_DIM))
    return 0


if __name__ == "__main__":
    sys.exit(main())
