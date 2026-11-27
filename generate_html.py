#!/usr/bin/env python3
import os, re, json, html as html_lib
from urllib.parse import urlparse
from datetime import datetime, timedelta
from pathlib import Path

from send_daily_email import signal_points  # the email's S1/S2/S3 parser, so site and email agree
from generate_images import image_figure  # <figure> only when site/images/<slug>.webp exists

CASE_STUDIES_DIR = "case_studies"
NEWS_DIR = "data/news"
OUTPUT_FILE = "site/index.html"
ARCHIVE_CS_FILE = "case_studies/archive.json"
ARCHIVE_NEWS_FILE = "data/news_archive.json"
SLUG_MAP_FILE = "site/breakdowns/slug_map.json"

# ---------- One font set and one token block, the same on every page ----------
# Benchmark: therundown.ai, read live on 27 Nov 2026 (Satoshi headings and body, 16px / 24px body, hero 28px on a
# phone and 72px at -1.8px on a desktop, 672px column, 28.8px panel, #000 to #292929 hero).
# Satoshi is the Fontshare file, served from site/fonts (the site's CSP allows fonts from 'self' only).
SATOSHI_WEIGHTS = (400, 700)  # body and headings
MONO_CSS = "https://fonts.googleapis.com/css2?family=Geist+Mono:wght@400;700&display=swap"


def font_links(prefix=""):
    """<link> tags of the font set. prefix is the path to the site root: "" or "../"."""
    return f"""<link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link rel="preload" href="{prefix}fonts/satoshi-400.woff2" as="font" type="font/woff2" crossorigin>
  <link rel="preload" href="{prefix}fonts/satoshi-700.woff2" as="font" type="font/woff2" crossorigin>
  <link rel="stylesheet" href="{MONO_CSS}">"""


def font_faces(prefix=""):
    """The Satoshi @font-face rules, first rules of the page's <style>. font-display: swap, so text shows at once."""
    return "\n    ".join(f'@font-face {{ font-family: "Satoshi"; src: url("{prefix}fonts/satoshi-{w}.woff2") format("woff2"); '
                          f'font-weight: {w}; font-style: normal; font-display: swap; }}' for w in SATOSHI_WEIGHTS)


TOKENS_CSS = """:root {
      /* Daily Edge AI tokens (27 Nov 2026). This block is the same on every page. */
      --font-head: "Satoshi", ui-sans-serif, system-ui, sans-serif;  /* headings: 700 */
      --font: "Satoshi", ui-sans-serif, system-ui, sans-serif;       /* body: 400 */
      --font-mono: "Geist Mono", ui-monospace, SFMono-Regular, Menlo, monospace;  /* tags and dates */

      /* Type scale 14 / 16 / 18 / 22 / 28 / 40 / 56. Hero: 28 on a phone, 56 on a tablet, 72 on a desktop. Body: 16 / 24. */
      --fs-label: 14px;
      --fs-small: 14px;
      --fs-ui: 16px;
      --fs-body: 16px;
      --fs-lead: 18px;
      --fs-h4: 22px;
      --fs-h3: 28px;
      --fs-h2: 40px;
      --fs-h1: 56px;
      --fs-hero: 28px;
      --lh-hero: 1.15;
      --lh-head: 1.15;
      --lh-snug: 1.3;
      --lh-body: 1.5;       /* 24px at 16px */
      --lh-small: 1.5;
      --ls-hero: -0.025em;  /* -0.7px at 28px, -1.8px at 72px */
      --ls-head: -0.02em;
      --ls-label: 0.04em;

      /* Spacing: 4/8 scale */
      --s-4: 4px;
      --s-8: 8px;
      --s-12: 12px;
      --s-16: 16px;
      --s-24: 24px;
      --s-32: 32px;
      --s-48: 48px;
      --s-64: 64px;
      --s-80: 80px;

      --c-text: #171717;
      --c-text-soft: #404040;      /* long-form body: 10.4:1 on white */
      --c-text-muted: #737373;     /* 4.7:1 on white; never on --c-bg-alt */
      --c-bg: #ffffff;
      --c-bg-alt: #f5f5f5;
      --c-surface-dark: #171717;
      --c-surface-dark-2: #212121;
      --c-border: #e5e5e5;
      --c-border-dark: #333333;
      --c-hero-bottom: #292929;
      --g-hero: linear-gradient(#000, var(--c-hero-bottom));
      --c-accent: #10b981;         /* emerald: link underline, the one CTA, the accent word. Never text on white (2.5:1) */
      --c-accent-hover: #059669;
      --c-on-accent: #0a0a0a;      /* 7.8:1 on emerald */
      --c-on-dark-muted: #a3a3a3;  /* muted text on the hero: 5.8:1 on #292929 */
      --c-good: #15803d;
      --c-warn: #b45309;
      --c-bad: #b91c1c;

      --radius-sm: 4px;
      --radius: 8px;
      --radius-card: 12px;
      --radius-lg: 16px;
      --radius-panel: 28.8px;
      --radius-pill: 9999px;

      --content-max: 1152px;
      --reading-max: 672px;  /* article column */
      --measure: 68ch;       /* longest line of body text */
      --tap: 44px;           /* smallest tap target */
    }

    @media (min-width: 701px) {
      :root { --fs-hero: 56px; --lh-hero: 1.05; }
    }

    @media (min-width: 1024px) {
      :root { --fs-hero: 72px; }
    }"""


def load_latest_json(directory, prefix=""):
    path = Path(directory)
    pattern = f"{prefix}*.json" if prefix else "*.json"
    files = [f for f in sorted(path.glob(pattern), reverse=True) if "archive" not in f.name]
    if not files:
        return None
    with open(files[0]) as f:
        return json.load(f)

def load_json(filepath):
    if os.path.exists(filepath):
        with open(filepath) as f:
            return json.load(f)
    return []

STEP_FIELDS = ("try_this", "try_this_judge")

def without_steps(data):
    """data with the step fields removed at every depth, switch on or off: steps reach readers only through the email."""
    if isinstance(data, dict):
        return {k: without_steps(v) for k, v in data.items() if k not in STEP_FIELDS}
    return [without_steps(v) for v in data] if isinstance(data, list) else data

def load_slug_map():
    if os.path.exists(SLUG_MAP_FILE):
        with open(SLUG_MAP_FILE) as f:
            return json.load(f)
    return {"case_studies": {}, "news": {}}

def esc(val):
    return html_lib.escape(str(val)) if val or val == 0 else ""  # a score of 0 still shows

def https_url(url):
    """The URL when it is https, else "". Nothing else (http:, javascript:, data:) ever becomes a link."""
    url = str(url or "").strip()
    return url if url.lower().startswith("https://") else ""

def safe_url(url):
    return https_url(url) or "#"

def item_key(item):
    return item.get("url") or item.get("title")

MIN_PER_TAB = 8

def top_up(active, archive, kind, minimum=MIN_PER_TAB):
    """active plus items from the newest archive days until there are at least `minimum`, no repeats.
    A run that finds few or no new items (e.g. an extra manual run) then still shows a full page."""
    out, seen = list(active), {item_key(i) for i in active}
    for day in archive:
        if len(out) >= minimum:
            break
        for item in day.get(kind) or []:
            if item_key(item) not in seen:
                out.append(item); seen.add(item_key(item))
    if len(out) > len(active):
        print(f"Top-up: {kind} {len(active)} -> {len(out)} from archive.")
    return out

def archive_day(archive, kind, date_str, items):
    """Save items under date_str in archive (newest day first: the email reads archive[0]). A day that already exists
    keeps its items and gains the new ones, matched by url then title, so archiving a file never drops what it holds."""
    day = next((e for e in archive if e.get("date") == date_str), None)
    if day is None:
        at = next((n for n, e in enumerate(archive) if e.get("date", "") < date_str), len(archive))
        archive.insert(at, {"date": date_str, "total": len(items), kind: list(items)})
        return
    have = day.setdefault(kind, [])
    seen = {item_key(x) for x in have}
    new = [x for x in items if item_key(x) not in seen]
    if new:
        have.extend(new)
        day["total"] = len(have)

CASE_STUDY_WINDOW_DAYS = 120  # the scout's FOUR_MONTHS_AGO window; applies to every item, video or not

def in_window(cs, cutoff):
    """True when the item was published inside the 4-month window. No pub_date: kept, as before."""
    pub = str(cs.get("pub_date") or "")[:10]
    return not pub or pub >= cutoff

def by_score(item, field):
    """Sort key: highest score first, newest first within a score. A non-numeric score counts as 0."""
    score = item.get(field)
    return (score if isinstance(score, (int, float)) else 0, str(item.get("pub_date") or ""))

# ---------- Honest content, checked at render time ----------
YEAR_RE = re.compile(r"\b(20\d\d)\b")
SENTENCE_END = re.compile(r"[.!?][\"'\u2019\u201d)\]]*(?=\s|$)")


def domain_of(url):
    host = urlparse(https_url(url) or "").hostname or ""
    return host[4:] if host.startswith("www.") else host


def source_matches(source, url):
    """True when the source name and the URL's domain name the same outlet ("The Verge" ~ theverge.com)."""
    name = re.sub(r"[^a-z0-9]", "", str(source or "").lower())
    host = domain_of(url)
    core = re.sub(r"[^a-z0-9]", "", host.rsplit(".", 2)[-2] if host.count(".") >= 1 else host)
    return bool(name and core) and (core in name or name in core)


def full_sentences(text):
    """The text cut after its last full sentence; never ends on an ellipsis."""
    text = str(text or "").strip()
    if not text:
        return text
    trimmed = re.sub(r"\s*(\u2026|\.\.\.)\s*$", "", text)
    if trimmed == text and SENTENCE_END.search(text[-3:] + " "):
        return text
    ends = [m.end() for m in SENTENCE_END.finditer(trimmed)]
    return trimmed[:ends[-1]].strip() if ends else trimmed.rstrip(" ,;:-")


DATE_FORMATS = ("%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%a, %d %b %Y", "%b %d, %Y")


def parse_date(value):
    """The item's date as YYYY-MM-DD, or "" when no known format matches (an unknown date is never hidden)."""
    text = str(value or "").strip()
    for fmt in DATE_FORMATS:
        for cut in (len(text), 10, 11, 16):
            try:
                return datetime.strptime(text[:cut].strip(), fmt).strftime("%Y-%m-%d")
            except ValueError:
                pass
    return ""


def honest(items, now, changes, kind):
    """Items fit to show: no future date, no summary naming a later year; summaries end on a full sentence;
    a news source that does not match its URL shows the domain. Every change is appended to changes."""
    today, year = now.strftime("%Y-%m-%d"), now.year
    kept = []
    for item in items:
        title = str(item.get("title", ""))
        pub = parse_date(item.get("pub_date"))
        if pub and pub > today:
            changes.append((kind, title, "hidden", f"date {pub} is in the future"))
            continue
        later = [y for y in YEAR_RE.findall(str(item.get("summary") or "")) if int(y) > year]
        if later:
            changes.append((kind, title, "hidden", f"summary names {later[0]}, after {year}"))
            continue
        item = dict(item)
        summary = full_sentences(item.get("summary"))
        if summary != str(item.get("summary") or "").strip():
            changes.append((kind, title, "summary cut", "cut to the last full sentence"))
            item["summary"] = summary
        if kind == "news" and item.get("source") and not source_matches(item["source"], item.get("url")):
            dom = domain_of(item.get("url"))
            if dom:
                changes.append((kind, title, "source changed", f"{item['source']!r} does not match {dom}"))
                item["source"] = dom
        kept.append(item)
    return kept


# A scorer tag that grades the story ("Low relevance", "high", "IT_relevance_moderate") is not a topic
GRADE_WORD = re.compile(r"relevan|score|novelty|impact|signal", re.I)
GRADE_ONLY = re.compile(r"(very |highly )?(low|medium|moderate|high|n/?a|none)", re.I)

def topic_tag(tag):
    """The topic to show on a card. A relevance or score label never shows: it becomes "Other"."""
    tag = str(tag or "Other").strip()
    if "_" in tag:  # raw snake_case from the scorer
        tag = tag.replace("_", " ").capitalize()
    return "Other" if not tag or GRADE_WORD.search(tag) or GRADE_ONLY.fullmatch(tag) else tag

def tag_pill(tag):
    return f'<span class="tag-pill">{esc(topic_tag(tag))}</span>'

def score_ring(score, max_score=75):
    """Not shown on the page (scores are hidden); kept because tests/test_phase0e.py checks the 0-75 scale.
    A legacy score above 75 (old 0-100 scale) gets no ring rather than a ring past full."""
    if not isinstance(score, (int, float)) or not 0 <= score <= max_score:
        return ""
    pct = int((score / max_score) * 100)
    level = "high" if pct >= 70 else "mid" if pct >= 40 else "low"
    legend = "AI SCOUT SCORE: A 0-75 metric for Technical Novelty, Market Maturity, and Business Impact."
    return (f'<span class="score-ring score-{level}" style="--pct:{pct};" title="{legend}" '
            f'aria-label="Scout score {score} of {max_score}">{score}/{max_score}</span>')

def tier_label(tier):
    labels = {"ai_labs": "AI Lab", "consulting": "Consulting", "news": "Press", "universities": "Academic"}
    return f'<span class="tier-label">{esc(labels.get(tier, tier))}</span>'

def eval_status(cs):
    """eval_verdict (the judge) first, then the Gemini field; anything not pass/fail is still pending."""
    return next((s for s in (cs.get("eval_verdict"), cs.get("gemini_eval_status")) if s in ("pass", "fail")), "pending")

NOT_APPLICABLE = ("not applicable", "not directly applicable")

def insight_html(label, text):
    """'How to apply' line, left out when empty or when the model said it does not apply."""
    if not text or any(p in str(text).lower() for p in NOT_APPLICABLE):
        return ""
    return f'<p class="insight"><strong>{label}</strong> {esc(text)}</p>'

def signal_html(text):
    """News 'How to use': 'S1: … S2: … S3: …' becomes three headed bullets, as in the email; no S-label survives."""
    if not text or any(p in str(text).lower() for p in NOT_APPLICABLE):
        return ""
    points = signal_points(str(text))
    if not points:
        return ""
    if points[0][0] is None:  # labels missing or out of order: one plain line
        return insight_html("How to use:", points[0][1])
    lis = "".join(f"<li><strong>{esc(h)}:</strong> {esc(t)}</li>" for h, t in points)
    return f'<ul class="signal-points">{lis}</ul>'

def card_title(title, slug):
    """The card's one link: the title, stretched over the whole card by .card-link::after, opens the breakdown page.
    The source link is on that page. No slug means no page, so the title is plain text, never a dead link."""
    if not slug:
        return f'<h2 class="card-title">{title}</h2>'
    return f'<h2 class="card-title"><a class="card-link" href="breakdowns/{esc(slug)}.html">{title}</a></h2>'

def render_case_studies(data, slug_map):
    if not data or not data.get("case_studies"):
        return "<div class='empty-state'><p>No case studies available yet.</p></div>"
    cs_slugs = slug_map.get("case_studies", {})
    cards = []
    for i, cs in enumerate(data["case_studies"]):
        authors = esc(", ".join(cs.get("authors", [])) or "Unknown authors")
        pub_raw = cs.get("pub_date", "")[:10]
        try:
            from datetime import datetime as dt
            pub = dt.strptime(pub_raw, "%Y-%m-%d").strftime("%d %b %Y")
        except:
            pub = esc(pub_raw)
        title = esc(cs.get("title", "Untitled"))
        # Meta: drop the "Academic Institution" placeholder and a name repeated as its own institution
        institution = esc(cs.get("institution", ""))
        meta = [authors]
        if institution and institution != "Academic Institution" and institution != authors:
            meta.append(institution)
        meta.append(f'<span class="pub-date">{pub}</span>')
        if cs.get("source_type", "") == "youtube_agent":
            meta.append("Video")
        if cs.get("author_built_product", False):
            meta.append("Author shipped a product")
        meta_html = '<span class="sep" aria-hidden="true">·</span>'.join(f"<span>{m}</span>" for m in meta)

        slug = cs_slugs.get(cs.get("title", "Untitled"))  # slug_map is keyed by the raw title

        cards.append(f"""<article class="card cs-card">
  {image_figure(cs.get("title", "Untitled"))}<div class="card-header">
    <div class="card-tags">{tag_pill(cs.get("relevance_tag", "Other"))}</div>
  </div>
  {card_title(title, slug)}
  <div class="card-meta">{meta_html}</div>
  <p class="card-summary">{esc(cs.get('summary',''))}</p>
  {insight_html("How to apply:", cs.get('it_breakdown','') or cs.get('how_to_apply',''))}
</article>""")
    return "\n".join(cards)

def render_news(data, slug_map):
    if not data or not data.get("news"):
        return "<div class='empty-state'><p>No news available yet.</p></div>"
    news_slugs = slug_map.get("news", {})
    cards = []
    for i, item in enumerate(data["news"]):
        title = esc(item.get("title", ""))
        slug = news_slugs.get(item.get("title", ""))  # slug_map is keyed by the raw title
        meta = [esc(v) for v in (item.get('source', ''), item.get('pub_date', '')) if v]
        meta_html = '<span class="sep" aria-hidden="true">•</span>'.join(f"<span>{m}</span>" for m in meta)

        cards.append(f"""<article class="card news-card">
  {image_figure(item.get("title", ""))}<div class="card-header">
    <div class="card-tags">
      {tier_label(item.get("tier", "news"))}
      {tag_pill(item.get("category", "Other"))}
    </div>
  </div>
  {card_title(title, slug)}
  <div class="card-meta">{meta_html}</div>
  <p class="card-summary">{esc(item.get('summary',''))}</p>
  {signal_html(item.get('enterprise_use',''))}
</article>""")
    return "\n".join(cards)

def render_headlines(items):
    if not items:
        return ""
    rows = "".join(
        f'<p><a class="archive-title" href="{esc(safe_url(i.get("url")))}" target="_blank" rel="noopener noreferrer">{esc(i.get("title", ""))}</a> '
        f'<span class="pub-date">{esc(i.get("pub_date", ""))}</span></p>'
        for i in items[:3])
    return f'<section class="signal">\n        <h2 class="signal-title">From McKinsey</h2>\n        {rows}\n    </section>'

GROUP_MAX = 7  # Miller's law: at most 7 items in one group


def card_grids(cards_html, more_title):
    """The cards in grids of at most GROUP_MAX, in equal parts (10 cards: 5 + 5). Each grid after the first has a heading."""
    cards = re.split(r'\n(?=<article class="card )', cards_html)
    size = -(-len(cards) // -(-len(cards) // GROUP_MAX))
    grids = ['<div class="cards-grid">' + "\n".join(cards[n:n + size]) + "</div>" for n in range(0, len(cards), size)]
    return f'\n    <h2 class="group-title">{esc(more_title)}</h2>\n    '.join(grids)


def week_title(date_str):
    """"Week of 23 Nov 2026" (the Monday) for a YYYY-MM-DD date; "Other dates" when the date does not parse."""
    try:
        day = datetime.strptime(str(date_str)[:10], "%Y-%m-%d")
    except ValueError:
        return "Other dates"
    return "Week of " + (day - timedelta(days=day.weekday())).strftime("%d %b %Y")


def render_archive(archive, kind="cs"):
    """Archive days, newest first, in one group for each week: a week has at most 7 days (GROUP_MAX)."""
    if not archive:
        return "<div class='empty-state'><p>No archive yet.</p></div>"
    weeks = []  # [title, rows]
    key = "case_studies" if kind == "cs" else "news"
    for entry in [e for e in archive if e.get("total", 0)][:30]:
        preview_html = "".join(
            f'<a class="archive-title" href="{esc(safe_url(i.get("url")))}" target="_blank" rel="noopener noreferrer">{esc(i.get("title", ""))}</a>'
            for i in entry.get(key, [])[:3])
        title = week_title(entry.get("date", ""))
        if not weeks or weeks[-1][0] != title or len(weeks[-1][1]) >= GROUP_MAX:
            weeks.append([title, []])
        weeks[-1][1].append(f"""<div class="archive-row">
  <div class="archive-date">{esc(entry.get("date", ""))}</div>
  <div class="archive-count">{esc(entry.get("total", 0))} items</div>
  <div class="archive-previews">{preview_html}</div>
</div>""")
    return "\n".join(f'<section class="archive-week">\n<h2 class="group-title">{esc(title)}</h2>\n'
                     f'<div class="archive-list">{chr(10).join(rows)}</div>\n</section>' for title, rows in weeks) \
        or "<div class='empty-state'><p>No archive yet.</p></div>"

def rss_links(url):
    """<link> and <guid> for an https URL, escaped for XML; nothing for any other URL."""
    url = https_url(url)
    return f"\n    <link>{esc(url)}</link>\n    <guid>{esc(url)}</guid>" if url else ""

def generate_rss(cs_data, news_data):
    base_url = "https://agent-builder-daily.vercel.app"
    now_rfc = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")
    items = []

    if cs_data:
        for cs in cs_data.get("case_studies", [])[:10]:
            title = esc(cs.get("title", "Untitled"))
            links = rss_links(cs.get("url"))
            description = esc(f"<p>{esc(cs.get('summary', ''))}</p><p><strong>IT Breakdown:</strong> {esc(cs.get('it_breakdown', ''))}</p>")
            pub = cs.get("pub_date", "")
            try:
                pub_rfc = datetime.strptime(pub, "%Y-%m-%d").strftime("%a, %d %b %Y 07:00:00 +0000")
            except:
                pub_rfc = now_rfc
            items.append(f"""  <item>
    <title>{title}</title>{links}
    <pubDate>{pub_rfc}</pubDate>
    <description>{description}</description>
    <category>Case Study</category>
  </item>""")

    if news_data:
        for item in news_data.get("news", [])[:5]:
            title = esc(item.get("title", "Untitled"))
            links = rss_links(item.get("url"))
            description = esc(f"<p>{esc(item.get('summary', ''))}</p>")
            pub = item.get("pub_date", "")
            try:
                pub_rfc = datetime.strptime(pub, "%Y-%m-%d").strftime("%a, %d %b %Y 07:00:00 +0000")
            except:
                pub_rfc = now_rfc
            items.append(f"""  <item>
    <title>[News] {title}</title>{links}
    <pubDate>{pub_rfc}</pubDate>
    <description>{description}</description>
    <category>Industry News</category>
  </item>""")

    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>Daily Edge AI: AI news and real case studies</title>
    <link>{base_url}</link>
    <description>Daily AI case studies and industry news for IT engineers and AI consultants.</description>
    <language>en-gb</language>
    <lastBuildDate>{now_rfc}</lastBuildDate>
    <atom:link href="{base_url}/feed.xml" rel="self" type="application/rss+xml"/>
{chr(10).join(items)}
  </channel>
</rss>"""

    os.makedirs("site", exist_ok=True)
    with open("site/feed.xml", "w", encoding="utf-8") as f:
        f.write(rss)
    print("RSS feed generated -> site/feed.xml")


def generate_email_capture(n=""):
    """Sign-up block. n="" is the hero form that js/subscribe.js drives; n="-2" is the page-end copy, which the
    page script forwards to the first form so subscribe.js stays unchanged."""
    return f"""
  <div class="email-banner{' email-banner-end' if n else ''}">
    <div class="email-inner{n}">
      <p class="email-title" id="email-title{n}">Get Daily Edge AI in your inbox</p>
      <form class="email-form{n}" aria-labelledby="email-title{n}">
        <label class="visually-hidden" for="email-input{n}">Email address</label>
        <input type="email" id="email-input{n}" class="email-input" placeholder="your@email.com" autocomplete="email" required />
        <button type="submit" class="email-btn">Subscribe</button>
      </form>
      <span class="email-confirm" id="email-confirm{n}" role="status"></span>
      <a class="privacy-link" href="privacy.html">How we use your email</a>
    </div>
  </div>"""


def generate_html():
    # --- Rotation Logic ---
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    cutoff = (now - timedelta(days=CASE_STUDY_WINDOW_DAYS)).strftime("%Y-%m-%d")

    # --- Case Studies Archiving & Loading ---
    path_cs = Path(CASE_STUDIES_DIR)
    all_cs_files = [f for f in path_cs.glob("*.json") if "archive" not in f.name]
    cs_archive = load_json(ARCHIVE_CS_FILE)
    active_cs = []
    archived_files = []  # removed only after the archive holding their items is written

    sorted_cs = sorted(all_cs_files)
    for i, f in enumerate(sorted_cs):
        data = load_json(str(f))
        studies = data.get("case_studies") if isinstance(data, dict) else None
        is_latest = (i == len(sorted_cs) - 1)
        # 4-month window for every file: a file whose items all fall outside it is archived too (video files included)
        out_of_window = bool(studies) and not any(in_window(cs, cutoff) for cs in studies)
        if (f.stem < today_str and not is_latest) or out_of_window:
            if studies is not None:
                # video files are named v_YYYYMMDD_<id>, so date them by their newest item instead
                date_str = f.stem if f.stem[:4].isdigit() else max((str(cs.get("pub_date") or "")[:10] for cs in studies), default=f.stem)
                archive_day(cs_archive, "case_studies", date_str, studies)
                archived_files.append(f)
        else:
            if studies:
                active_cs.extend(studies)

    with open(ARCHIVE_CS_FILE, 'w') as f_out:
        json.dump(cs_archive, f_out, indent=2)
    for f in archived_files:
        os.remove(f)
        print(f"Archived and removed {f}")

    active_cs = top_up(active_cs, cs_archive, "case_studies")  # a thin or empty run must not blank the page
    active_cs = [cs for cs in active_cs if in_window(cs, cutoff)]
    active_cs.sort(key=lambda x: by_score(x, "total_score"), reverse=True)
    changes = []  # what the honesty checks hid or changed, printed at the end
    active_cs = honest(active_cs, now, changes, "case study")
    cs_data = {"case_studies": active_cs}

    # --- News Archiving & Loading ---
    path_news = Path(NEWS_DIR)
    all_news_files = [f for f in path_news.glob("news_*.json") if "archive" not in f.name]
    news_archive = load_json(ARCHIVE_NEWS_FILE)
    active_news, active_headlines = [], []
    archived_files = []

    sorted_news = sorted(all_news_files)
    for i, f in enumerate(sorted_news):
        date_str = f.stem.replace("news_", "")
        is_latest = (i == len(sorted_news) - 1)
        if date_str < today_str and not is_latest:
            data = load_json(str(f))
            if data and "news" in data:
                archive_day(news_archive, "news", date_str, data["news"])
                archived_files.append(f)
        else:
            data = load_json(str(f))
            if data and "news" in data:
                active_news.extend(data["news"])
                active_headlines.extend(data.get("headlines", []))
                
    with open(ARCHIVE_NEWS_FILE, 'w') as f_out:
        json.dump(news_archive, f_out, indent=2)
    for f in archived_files:
        os.remove(f)
        print(f"Archived and removed {f}")

    active_news.sort(key=lambda x: by_score(x, "relevance_score"), reverse=True)

    active_news = top_up(active_news, news_archive, "news")
    if not active_headlines and news_archive:
        active_headlines = news_archive[0].get("headlines", [])
    active_headlines.sort(key=lambda x: by_score(x, "relevance_score"), reverse=True)
    active_news = honest(active_news, now, changes, "news")
    news_data = {"news": active_news}

    # --- Metadata ---
    try:  # a page and a slug for exactly the cards rendered below (top_up archive cards included); no AI call
        from generate_breakdowns import ensure_pages
        slug_map = ensure_pages(active_cs, active_news, os.path.dirname(SLUG_MAP_FILE))
    except Exception as e:  # the home page must still build
        print(f"Breakdown pages not checked ({e}); using the saved slug map.")
        slug_map = load_slug_map()
    daily_hook = ""
    hook_path = Path("data/daily_hook.txt")
    if hook_path.exists():
        with open(hook_path) as f:
            daily_hook = f.read().strip()
    generated_at = datetime.utcnow().strftime("%d %b %Y, %H:%M UTC")

    html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Daily Edge AI: AI news and real case studies</title>
  <meta name="description" content="AI news and real case studies, every day.">
  <meta property="og:site_name" content="Daily Edge AI">
  <meta property="og:title" content="Daily Edge AI: AI news and real case studies">
  <meta property="og:description" content="AI news and real case studies, every day.">
  <meta property="og:type" content="website">
  <meta property="og:url" content="https://agent-builder-daily.vercel.app/">
  <link rel="icon" href="favicon.svg" type="image/svg+xml">
  """ + font_links() + """
  <style>
    """ + font_faces() + """

    """ + TOKENS_CSS + """

    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      background: var(--c-bg);
      color: var(--c-text);
      font-family: var(--font);
      font-size: var(--fs-body);
      line-height: var(--lh-body);
      min-height: 100vh;
      overflow-wrap: anywhere;  /* long words and URLs wrap at 360px */
    }

    ::selection { background: var(--c-text); color: var(--c-bg); }
    :focus-visible { outline: 2px solid var(--c-text); outline-offset: 2px; }
    .hero :focus-visible { outline-color: var(--c-bg); }

    .visually-hidden {
      position: absolute;
      width: 1px;
      height: 1px;
      overflow: hidden;
      clip: rect(0 0 0 0);
      white-space: nowrap;
    }

    /* Hero */
    .hero {
      background: var(--g-hero);
      color: var(--c-bg);
      padding: 24px 0 calc(64px + var(--s-80));
    }

    .header-inner {
      max-width: var(--content-max);
      margin: 0 auto;
      padding: 0 32px;
    }

    .header-top {
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 16px;
    }

    .brand { font-size: var(--fs-h4); line-height: 1; }

    /* Logo top-left, links home */
    .brand-link {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      min-height: var(--tap);
      color: var(--c-bg);
      text-decoration: none;
      font-family: var(--font-head);
      font-weight: 600;
      letter-spacing: var(--ls-head);
    }

    .brand-logo { display: block; width: 28px; height: 22px; flex-shrink: 0; }
    .brand-name { white-space: nowrap; }

    /* Headings: Satoshi 700, tight tracking. Body: Satoshi 400 */
    .hero-title, .card-title, .email-title, .group-title { font-family: var(--font-head); font-weight: 700; letter-spacing: var(--ls-head); }

    /* Tags and dates: Geist Mono */
    .tag-pill, .tier-label, .pub-date, .archive-date, .archive-count, .updated,
    .news-card .card-meta > span:last-child:not(:first-child) {
      font-family: var(--font-mono);
    }

    .tag-pill, .signal-title {
      font-size: var(--fs-label);
      line-height: var(--lh-small);
      letter-spacing: var(--ls-label);
      font-weight: 700;
      text-transform: uppercase;
    }

    .hero-title {
      max-width: 900px;
      margin: var(--s-80) 0 16px;
      font-size: var(--fs-hero);
      line-height: var(--lh-hero);
      letter-spacing: var(--ls-hero);
      font-weight: 700;
      text-wrap: balance;
    }

    /* Key words: one solid colour, never a gradient */
    .accent-word { color: var(--c-accent); }

    .updated {
      font-size: var(--fs-small);
      line-height: var(--lh-small);
      color: var(--c-on-dark-muted);
      margin-bottom: 32px;
    }

    /* Email capture, inside the hero */
    .email-banner {
      color: var(--c-bg);  /* light text on the dark block, in the hero and at the page end */
      max-width: var(--reading-max);
      background: var(--c-surface-dark-2);
      border: 1px solid var(--c-border-dark);
      border-radius: var(--radius-card);
      padding: 20px;
    }

    .email-inner { display: flex; flex-direction: column; gap: 12px; }

    .email-title { font-size: var(--fs-lead); line-height: var(--lh-snug); font-weight: 700; }

    .email-form { display: flex; gap: var(--s-8); }

    .email-input {
      flex: 1;
      min-width: 0;
      background: var(--c-surface-dark);
      border: 1px solid var(--c-border-dark);
      border-radius: var(--radius);
      min-height: var(--tap);
      padding: 8px 16px;
      font: inherit;
      font-size: var(--fs-ui);
      color: var(--c-bg);
      transition: border-color 0.15s;
    }

    .email-input::placeholder { color: var(--c-on-dark-muted); }
    .email-input:focus { border-color: var(--c-bg); }

    .email-confirm { font-size: var(--fs-small); color: var(--c-on-dark-muted); }

    /* Buttons: flat, no shadow */
    .email-btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      font: inherit;
      font-weight: 700;
      line-height: 1;
      border: 1px solid transparent;
      border-radius: var(--radius);
      white-space: nowrap;
      cursor: pointer;
      text-decoration: none;
      transition: background 0.15s, border-color 0.15s, color 0.15s;
    }

    /* The one emerald CTA on each screen */
    .email-btn { min-height: var(--tap); padding: 0 24px; font-size: var(--fs-ui); background: var(--c-accent); color: var(--c-on-accent); }
    .email-btn:hover { background: var(--c-accent-hover); }

    /* Text links, never filled: the text keeps its colour, the underline is emerald */
    .breakdown-link, .privacy-link {
      display: inline-flex;
      align-items: center;
      min-height: var(--tap);
      font-size: var(--fs-small);
      font-weight: 700;
      color: var(--c-text);
      text-decoration: underline;
      text-underline-offset: 4px;
      text-decoration-thickness: 2px;
      text-decoration-color: var(--c-accent);
    }
    .breakdown-link:hover, .privacy-link:hover { text-decoration-color: var(--c-accent-hover); }
    .privacy-link { align-self: flex-start; font-weight: 400; color: var(--c-on-dark-muted); }
    .email-banner-end { margin: 48px auto 0; }

    /* White panel: overlaps the hero bottom by 64px */
    .content {
      position: relative;
      width: min(var(--content-max), calc(100% - 32px));
      margin: -64px auto 0;
      padding: 32px 32px 64px;
      background: var(--c-bg);
      border-radius: var(--radius-panel) var(--radius-panel) 0 0;
    }

    .toolbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 16px;
      border-bottom: 1px solid var(--c-border);
      margin-bottom: 32px;
      position: sticky;  /* tabs and search stay in view, as the old header did */
      top: 0;
      z-index: 1;
      background: var(--c-bg);
    }

    /* Tabs */
    .tabs {
      display: flex;
      flex-wrap: nowrap;
      overflow-x: auto;
      scrollbar-width: none;
      min-width: 0;
      max-width: 100%;
      gap: 4px;
      padding: 4px;
      margin-bottom: 8px;
    }

    .tabs::-webkit-scrollbar { display: none; }

    .tab {
      min-height: var(--tap);
      padding: 8px 16px;
      font: inherit;
      font-size: var(--fs-small);
      font-weight: 700;
      color: var(--c-text-muted);
      cursor: pointer;
      border: none;
      background: none;
      border-radius: var(--radius-pill);
      transition: color 0.15s, background-color 0.15s;
      white-space: nowrap;
    }

    .tab:hover { color: var(--c-text); }
    .tab.active { color: var(--c-text); box-shadow: inset 0 -2px 0 var(--c-text); border-radius: 0; }

    .search {
      background: var(--c-bg);
      border: 1px solid var(--c-border);
      border-radius: var(--radius);
      min-height: var(--tap);
      padding: 8px 16px;
      margin-bottom: 8px;
      font: inherit;
      font-size: var(--fs-ui);
      color: var(--c-text);
      width: 240px;
      transition: border-color 0.15s;
    }

    .search::placeholder { color: var(--c-text-muted); }
    .search:focus { border-color: var(--c-text); }

    .panel { display: none; }
    .panel.active { display: block; }

    /* Cards Grid */
    .cards-grid {
      display: grid;
      grid-template-columns: minmax(0, 1fr);
      gap: 32px 24px;
    }

    /* Lead story + grid: the first card spans 2 columns (and 2 rows above 1024px) */
    @media (min-width: 641px) {
      .cards-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .cards-grid > .card:first-child { grid-column: span 2; }
      .cards-grid > .card:first-child .card-title { font-size: var(--fs-h4); }
    }

    @media (min-width: 1025px) {
      .cards-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }
      .cards-grid > .card:first-child { grid-row: span 2; }
      .cards-grid > .card:first-child .card-title { font-size: var(--fs-h3); }
      /* No empty cell: widen the last card(s) to fill the row */
      .cards-grid > .card:first-child:last-child,
      .cards-grid > .card:first-child:nth-last-child(2),
      .cards-grid > .card:first-child:nth-last-child(2) + .card { grid-column: 1 / -1; grid-row: auto; }
      .cards-grid > .card:first-child:nth-last-child(3) ~ .card,
      .cards-grid > .card:first-child:nth-last-child(4) ~ .card:last-child,
      .cards-grid > .card:nth-child(4n+6):nth-last-child(2),
      .cards-grid > .card:nth-child(4n+7):last-child,
      .cards-grid > .card:nth-child(4n+8):last-child { grid-column: span 2; }
      .cards-grid > .card:nth-child(4n+6):last-child { grid-column: 1 / -1; }
    }

    /* Cards follow the benchmark (therundown.ai, read live): no box. No border, no radius, no padding;
       the grid gap separates them. Only the image has a radius. */
    .card { min-width: 0; }

    /* The whole card is one tap target: the title link stretches over it */
    .card { position: relative; }
    .card-link::after { content: ""; position: absolute; inset: 0; border-radius: var(--radius-card); }
    .card-link:focus-visible { outline: none; }
    .card-link:focus-visible::after { outline: 2px solid var(--c-text); outline-offset: 2px; }

    .card-figure { margin: 0 0 16px; }
    .card-figure img { display: block; width: 100%; height: auto; border-radius: var(--radius-card); }
    .card-figure figcaption { margin-top: 4px; font-family: var(--font-mono); font-size: var(--fs-label); line-height: var(--lh-small); color: var(--c-text-muted); }

    .card-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 8px 12px;
      margin-bottom: 12px;
    }

    .card-tags { display: flex; align-items: center; gap: var(--s-8); flex-wrap: nowrap; min-width: 0; overflow: hidden; }
    .tag-pill, .tier-label { white-space: nowrap; flex-shrink: 0; }
    .tag-pill { flex-shrink: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; }  /* a long tag is cut inside its card */

    .tag-pill { padding: 4px 8px; border-radius: var(--radius-sm); color: var(--c-text); background: var(--c-bg-alt); }

    .tier-label, .source-name {
      font-size: var(--fs-label);
      line-height: var(--lh-small);
      font-weight: 400;
      color: var(--c-text-muted);
    }

    /* Card content */
    .card-title {
      font-size: var(--fs-lead);
      font-weight: 700;
      letter-spacing: var(--ls-head);
      line-height: var(--lh-snug);
      margin-bottom: 12px;
      overflow-wrap: anywhere;
      text-wrap: balance;
    }

    .card-title a { display: block; min-height: var(--tap); color: var(--c-text); text-decoration: none; }
    .card-title a:hover { text-decoration: underline; text-underline-offset: 4px; text-decoration-thickness: 2px; text-decoration-color: var(--c-accent); }

    .card-meta {
      display: flex;
      align-items: center;
      gap: var(--s-8);
      flex-wrap: wrap;
      margin-bottom: 16px;
      font-size: var(--fs-small);
      line-height: var(--lh-small);
      color: var(--c-text-muted);
    }

    .card-meta > span:first-child { color: var(--c-text); font-weight: 700; }
    .pub-date { font-size: var(--fs-label); color: var(--c-text-muted); white-space: nowrap; font-variant-numeric: tabular-nums; }
    .sep { color: var(--c-text-muted); }

    .card-summary {
      font-size: var(--fs-ui);
      line-height: var(--lh-body);
      color: var(--c-text-muted);
      margin-bottom: 16px;
    }

    .insight, .signal-points {
      font-size: var(--fs-ui);
      line-height: var(--lh-body);
      color: var(--c-text);
      margin-bottom: 16px;
    }

    .insight strong, .signal-points strong { font-weight: 700; }

    /* Every card except the lead: summary and "How to apply" show 3 lines */
    .cards-grid > .card:not(:first-child) .card-summary,
    .cards-grid > .card:not(:first-child) .insight {
      display: -webkit-box;
      -webkit-box-orient: vertical;
      -webkit-line-clamp: 3;
      line-clamp: 3;
      overflow: hidden;
    }

    .signal-points { padding-left: 20px; }
    .signal-points li { margin-bottom: 8px; }

    /* Card footer */
    .card-footer {
      display: flex;
      align-items: center;
      margin-top: 16px;
      padding-top: 12px;
      border-top: 1px solid var(--c-border);
      gap: 8px;
      flex-wrap: wrap;
    }

    /* Today's signal */
    .signal {
      background: var(--c-bg);
      border: 1px solid var(--c-border);
      border-radius: var(--radius-card);
      padding: 16px 20px;
      margin-bottom: 24px;
    }

    .signal-title { font-family: var(--font-mono); color: var(--c-text); margin-bottom: var(--s-8); }
    .signal p { font-size: var(--fs-body); max-width: var(--measure); color: var(--c-text); }

    /* Archive */
    /* Groups: at most 7 cards in a grid and 7 days in an archive week */
    .group-title { font-size: var(--fs-h3); line-height: var(--lh-head); margin: var(--s-48) 0 var(--s-24); }
    .archive-week:first-child .group-title { margin-top: 0; }

    .archive-list { display: flex; flex-direction: column; gap: var(--s-8); }

    .archive-row {
      background: var(--c-bg);
      border: 1px solid var(--c-border);
      border-radius: var(--radius-card);
      padding: 12px 20px;
      display: grid;
      grid-template-columns: 112px 88px minmax(0, 1fr);
      gap: 20px;
      align-items: start;
    }

    .archive-date, .archive-count { font-size: var(--fs-label); line-height: var(--tap); font-variant-numeric: tabular-nums; }
    .archive-date { color: var(--c-text); font-weight: 700; }
    .archive-count { color: var(--c-text-muted); }

    .archive-previews { display: flex; flex-direction: column; gap: 4px; min-width: 0; }

    .archive-title {
      display: flex;
      align-items: center;
      min-height: var(--tap);
      font-size: var(--fs-ui);
      line-height: var(--lh-snug);
      color: var(--c-text);
      text-decoration: none;
      overflow-wrap: anywhere;
    }

    .archive-title:hover { text-decoration: underline; text-underline-offset: 4px; text-decoration-thickness: 2px; text-decoration-color: var(--c-accent); }

    .empty-state {
      text-align: center;
      padding: 64px 32px;
      color: var(--c-text-muted);
      font-size: var(--fs-ui);
    }

    footer {
      border-top: 1px solid var(--c-border);
      padding: 20px 32px;
      max-width: var(--content-max);
      margin: 0 auto;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: var(--s-8);
    }

    footer span { font-size: var(--fs-small); color: var(--c-text-muted); }
    footer a { display: inline-flex; align-items: center; min-height: var(--tap); padding: 0 4px; color: var(--c-text); text-underline-offset: 4px; text-decoration-thickness: 2px; text-decoration-color: var(--c-accent); }

    @media (max-width: 700px) {
      .hero { padding: 12px 0 calc(32px + 12px); }
      .header-inner { padding: 0 16px; }
      .hero-title { margin: 24px 0 16px; }
      .updated { margin-bottom: 16px; }
      .email-banner { padding: 16px; }
      .email-inner { gap: 8px; }
      .content { margin-top: -32px; }
      .email-btn { padding: 0 16px; }
      .content { width: 100%; padding: 24px 16px 48px; }
      .toolbar { flex-direction: column; align-items: stretch; gap: var(--s-8); position: static; }
      .tabs { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); overflow: visible; }  /* all 4 tabs in view */
      .tab { padding: 8px 12px; }
      .tab.active { box-shadow: inset 0 -2px 0 var(--c-text); }
      .search { width: 100%; }
      .archive-row { grid-template-columns: 1fr; gap: 0; padding: 12px 16px; }
      .archive-date, .archive-count { line-height: var(--lh-small); }
      .group-title { margin: var(--s-32) 0 var(--s-16); }
    }

    @media (max-width: 640px) {
      /* Phone cards: tag, title, source • date, 2-line summary, one link. Desktop keeps the full text. */
      .card .insight, .card .signal-points, .card .tier-label { display: none; }
      .cards-grid > .card .card-summary {
        display: -webkit-box;
        -webkit-box-orient: vertical;
        -webkit-line-clamp: 2;
        line-clamp: 2;
        overflow: hidden;
      }
      .card-meta { margin-bottom: 8px; }
      .card-footer { margin-top: 8px; padding-top: 4px; }
    }

    @media (prefers-reduced-motion: reduce) {
      * { transition: none !important; }
    }
  </style>
</head>
<body>

<header class="header hero">
  <div class="header-inner">
    <div class="header-top">
      <h1 class="brand">
        <a class="brand-link" href="./"><img class="brand-logo" src="logo.svg" width="28" height="22" alt=""><span class="brand-name">Daily Edge <span class="accent-word">AI</span></span></a>
      </h1>
    </div>
    <p class="hero-title">AI news and real case studies, every day.</p>
    <p class="updated">Updated """ + generated_at + """</p>
""" + generate_email_capture() + """
  </div>
</header>

<main class="content">
  <div class="toolbar">
      <div class="tabs" role="tablist" aria-label="Sections">
          <button type="button" class="tab active" role="tab" id="tab-case-studies" aria-controls="case-studies" aria-selected="true">Case Studies</button>
          <button type="button" class="tab" role="tab" id="tab-industry-news" aria-controls="industry-news" aria-selected="false">Industry News</button>
          <button type="button" class="tab" role="tab" id="tab-archive-cs" aria-controls="archive-cs" aria-selected="false">Archive: Studies</button>
          <button type="button" class="tab" role="tab" id="tab-archive-news" aria-controls="archive-news" aria-selected="false">Archive: News</button>
      </div>
      <label class="visually-hidden" for="scout-search">Search stories</label>
      <input type="search" id="scout-search" class="search" placeholder="Search stories" />
  </div>
  <div id="case-studies" class="panel active" role="tabpanel" aria-labelledby="tab-case-studies">
    """ + card_grids(render_case_studies(cs_data, slug_map), "More case studies") + """
  </div>

  <div id="industry-news" class="panel" role="tabpanel" aria-labelledby="tab-industry-news">
    """ + (f'''
    <section class="signal">
        <h2 class="signal-title">Today\'s signal</h2>
        <p>{esc(daily_hook)}</p>
    </section>
    ''' if daily_hook else "") + render_headlines(active_headlines) + """
    """ + card_grids(render_news(news_data, slug_map), "More industry news") + """
  </div>

  <div id="archive-cs" class="panel" role="tabpanel" aria-labelledby="tab-archive-cs">
    """ + render_archive(cs_archive, 'cs') + """
  </div>

  <div id="archive-news" class="panel" role="tabpanel" aria-labelledby="tab-archive-news">
    """ + render_archive(news_archive, 'news') + """
  </div>
""" + generate_email_capture("-2") + """
</main>

<footer>
  <span>Daily Edge AI · LangGraph + Groq + arXiv · <a href="privacy.html">Privacy</a></span>
  <span>Case Studies: last 4 months · News: last 4–8 weeks</span>
</footer>

<script src="js/subscribe.js"></script>
<script>
  // Handlers are attached here, not as on*= attributes, so the page carries no inline event handlers
  document.querySelectorAll('.tab').forEach(t => t.addEventListener('click', () => showTab(t.getAttribute('aria-controls'), t)));
  document.getElementById('scout-search').addEventListener('input', e => filterCards(e.target.value));
  document.querySelector('.email-form').addEventListener('submit', e => submitEmail(e));
  // The page-end form reuses submitEmail: copy its email into the hero form, submit, and mirror the status
  const endForm = document.querySelector('.email-form-2');
  if (endForm) endForm.addEventListener('submit', e => {
    e.preventDefault();
    const top = document.getElementById('email-input'), end = document.getElementById('email-input-2');
    const status = document.getElementById('email-confirm'), endStatus = document.getElementById('email-confirm-2');
    top.value = end.value;
    endStatus.textContent = 'Submitting…';
    Promise.resolve(submitEmail(e)).then(() => { endStatus.textContent = status.textContent; if (!top.value) end.value = ''; });
  });

  function showTab(id, btn) {
    document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.tab').forEach(t => { t.classList.remove('active'); t.setAttribute('aria-selected', 'false'); });
    document.getElementById(id).classList.add('active');
    btn.classList.add('active');
    btn.setAttribute('aria-selected', 'true');
    const search = document.getElementById('scout-search');
    if (search) { search.value = ''; filterCards(''); }
  }

  function filterCards(query) {
    const q = query.toLowerCase().trim();
    const activePanel = document.querySelector('.panel.active');
    if (!activePanel) return;
    const cards = activePanel.querySelectorAll('.card, .archive-row');
    cards.forEach(card => {
      card.style.display = (!q || card.innerText.toLowerCase().includes(q)) ? '' : 'none';
    });
  }

</script>
</body>
</html>"""

    os.makedirs("site", exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        f.write(html)
    generate_rss(cs_data, news_data)
    # Copy data files to site/ so Vercel can serve them for the dashboard
    for src, dst in ((ARCHIVE_CS_FILE, "site/case_studies_archive.json"), (ARCHIVE_NEWS_FILE, "site/news_archive.json")):
        if os.path.exists(src):  # same json.dump as the archive writes above, so only the step fields differ
            with open(dst, "w") as f_out:
                json.dump(without_steps(load_json(src)), f_out, indent=2)
            print(f"Wrote -> {dst} (try_this removed)")
    if os.path.exists("site/groq_token_stats.json"):
        print("groq_token_stats.json already in site/ — no copy needed")
    for kind, title, action, why in changes:
        print(f"Content check: {kind} | {action} | {why} | {title}")
    print("HTML generated -> " + OUTPUT_FILE)

if __name__ == "__main__":
    generate_html()
