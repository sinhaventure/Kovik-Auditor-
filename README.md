# Kovik SEO Audit Engine

A real crawler in one Python file. It fetches the pages, parses the HTML, runs 26 checks,
scores five categories, and writes a branded PDF report — Kovik watermark on every page,
"Powered by Kovik Intelligence AI" in the footer.

No API keys, no third-party SEO service, no dashboard.

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
| `--label https://client-site.in` | Public URL to show when auditing a staging copy |
| `--delay 0.5` | Seconds between requests — be polite on small servers |
| `--quiet` | No crawl progress in the terminal |

### Example with a client name

```bash
python kovik_audit.py https://sharmadental.in \
  --pages 12 --client "Sharma Dental Clinic, Hajipur" \
  --out sharma-dental-audit.pdf --csv sharma-dental-issues.csv
```

---

## What it checks (26 checks, five categories)

**Technical — 30% of the score**
HTTPS, robots.txt reachable, XML sitemap present, real 404 handling, broken pages,
redirect chains, canonical tags, accidental noindex, mobile viewport, page language.

**On-page — 25%**
Title tags present and well sized, duplicate titles, meta descriptions, duplicate
descriptions, single H1, H2 structure, image alt coverage.

**Content — 20%**
Word count per page (thin content), internal links per page.

**Structured data — 15%**
JSON-LD present and valid, schema types found, Open Graph tags, LocalBusiness /
Organization identity markup.

**Performance — 10%**
Server response time, HTML weight, gzip or brotli compression, cache headers.

Each check is weighted inside its category: pass = 1, improve = 0.5, fail = 0.
Categories are then weighted into the overall score out of 100.

---

## The report

* **Cover** — brand, site, date, score ring, what is inside.
* **Executive summary** — score card, category chart, the five things to fix first.
* **Findings in detail** — grouped critical → low. Each finding shows what was measured,
  which pages were affected, and what to do.
* **Page-by-page table** — status, page score, title and description length, H1 count,
  words, missing alt text, response time, page weight.
* **Method and limits** — what this audit does not cover, so nobody over-claims.

Every page carries a diagonal **KOVIK DIGITAL SOLUTIONS** watermark and a footer with
**Powered by Kovik Intelligence AI · kovik.online**.

---

## Honest limits

* One crawl, from one location, at one moment. Response times are a signal, not a
  benchmark — run it twice before quoting numbers.
* No backlink data, no search volume, no rank tracking, no Core Web Vitals field data.
  Those need Search Console, Analytics and a rank tracker.
* JavaScript-rendered content is not executed. If a site builds its content in the
  browser, the crawler sees an almost empty page — that is itself worth reporting.

---

Kovik Digital Solutions · Hajipur, Bihar · kovik.online · +91 91992 64615
