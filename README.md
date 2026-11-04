# Kovik SEO Audit Engine

A real crawler in one Python file. It fetches the pages, parses the HTML, runs 27 checks,
scores five categories, and writes a branded PDF report — Kovik watermark on every page,
"Powered by Kovik Intelligence AI" in the footer.

No API keys, no third-party SEO service, no dashboard.

**It refuses to score a site it could not fetch.** If no page loads, or the content is
built entirely in the browser, the run stops with an explanation and writes nothing.
A number that was never measured is worse than no number.

---

## Install

```bash
pip install -r requirements.txt
```

Python 3.9 or newer.

## Run

```bash
python kovik_audit.py https://client-site.in --pages 10 --out client-audit.pdf
```

Crawls up to 10 pages, prints the score in the terminal, writes `client-audit.pdf`.

### Options

| Flag | What it does |
|---|---|
| `--pages 12` | How many pages to crawl (8–12 suits a small business site) |
| `--depth 2` | Link hops from the home page |
| `--out report.pdf` | Output PDF path |
| `--json data.json` | Also dump every measurement as JSON |
| `--csv issues.csv` | Also dump the issue list as CSV |
| `--client "Sharma Dental"` | Client name printed on the cover |
| `--label https://client-site.in` | Caption for a staging audit — see below |
| `--delay 0.5` | Seconds between requests — be polite on small servers |
| `--quiet` | No crawl progress in the terminal |

### Example with a client name

```bash
python kovik_audit.py https://sharmadental.in \
  --pages 12 --client "Sharma Dental Clinic, Hajipur" \
  --out sharma-dental-audit.pdf --csv sharma-dental-issues.csv
```

### Auditing a staging copy

```bash
python kovik_audit.py http://127.0.0.1:8801 --label https://sharmadental.in
```

`--label` is a **caption only**. The report cover carries the public name, and a note says
plainly which address was measured. Every URL inside the report is printed exactly as it was
fetched, because the alternative was a report that told a client their live HTTPS site was
"served over plain HTTP" when all that had been tested was a local mirror.

Checks that cannot be judged from a mirror — HTTPS in particular — are reported as
**not measured** rather than failed.

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Report written |
| `2` | Refused: nothing fetched, content is client-side only, or too little of the checklist could be measured. No PDF, no score. |

---

## What it checks (27 checks, five categories)

**Technical — 30% of the score**
HTTPS, robots.txt reachable, XML sitemap present, real 404 handling, broken pages,
redirect chains, canonical tags, accidental noindex, mobile viewport, page language.

**On-page — 25%**
Title tags present and well sized, duplicate titles, meta descriptions, duplicate
descriptions, single H1, H2 structure, image alt coverage.

**Content — 20%**
Word count per page (thin content), internal links per page, content visible without
JavaScript.

**Structured data — 15%**
JSON-LD present and valid, schema types found, Open Graph tags, LocalBusiness /
Organization identity markup.

**Performance — 10%**
Server response time, HTML weight, gzip or brotli compression, cache headers.

Each check is weighted inside its category: pass = 1, improve = 0.5, fail = 0.
Categories are then weighted into the overall score out of 100.

### How "not measured" works

A check with nothing to inspect — no pages, no images, a JS-only page — is recorded as
**not measured** and removed from *both* sides of the average. It does not score 1. It
does not score 0. It is not part of the number.

This matters: the first version counted an empty list of problems as a pass, so a crawl
that fetched nothing at all scored 81/100 with "Good, with gaps" and "every page has a
title". Now:

* no page fetched → `AuditError`, exit 2, nothing written;
* every page JS-rendered → `AuditError`, exit 2, nothing written;
* some checks unmeasurable → they are excluded, and the report prints the coverage
  ("25 of 27 checks could be measured") next to the score.

If coverage falls below half of the checklist weight, the run refuses to publish a number.

---

## The report

* **Cover** — brand, site, date, score ring, what is inside.
* **Executive summary** — score card, category chart, the five things to fix first.
* **Findings in detail** — grouped critical → low. Each finding shows what was measured,
  which pages were affected, and what to do.
* **Page-by-page table** — status, page score, title and description length, H1 count,
  words, missing alt text, response time, page weight. Pages that failed or need
  JavaScript stay in the table with no score, instead of disappearing.
* **Method and limits** — what this audit does not cover, the pages that did not load,
  and how the score is built.

Every page carries a diagonal **KOVIK DIGITAL SOLUTIONS** watermark and a footer with
**Powered by Kovik Intelligence AI · kovik.online**.

---

## Privacy and secrets

Audit reports get forwarded, so nothing sensitive is allowed into one.

* URL credentials (`user:pass@`) are stripped.
* Query values for tokens, keys, signatures, sessions, OTPs and passwords are replaced
  with `[redacted]` before anything is printed, stored or rendered.
* Response headers are stored without `Set-Cookie`, `Cookie`, `Authorization` or
  `WWW-Authenticate`.
* The JSON export carries measurements only — never page bodies, headers, cookies or raw
  JSON-LD blocks, which can contain contact details and form payloads.

Redaction happens in one place (`Analyzer.add`), so every finding, the PDF, the CSV and the
JSON get the same treatment.

---

## Tests

```bash
python -m unittest discover -s tests -v
```

17 tests, no network. They pin the guarantees above: an empty crawl raises instead of
scoring, a skipped check cannot raise an average, a failed page keeps its row, and
credentials never survive into an affected-URL list.

---

## Honest limits

* One crawl, from one location, at one moment. Response times are a signal, not a
  benchmark — run it twice before quoting numbers.
* No backlink data, no search volume, no rank tracking, no Core Web Vitals field data.
  Those need Search Console, Analytics and a rank tracker.
* JavaScript-rendered content is not executed. If a site builds its content in the
  browser, the crawler says so and stops rather than grading an empty shell. Render the
  pages on the server, prerender them, or use a JavaScript-capable crawler for those sites.
* The checks cover on-page and technical SEO. They do not judge whether the copy is any
  good, and they do not replace looking at the site.

---

Kovik Digital Solutions · Hajipur, Bihar · kovik.online · +91 91992 64615
