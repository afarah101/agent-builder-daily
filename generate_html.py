#!/usr/bin/env python3
import os, json, html as html_lib
from datetime import datetime, timedelta
from pathlib import Path

from send_daily_email import signal_points  # the email's S1/S2/S3 parser, so site and email agree

CASE_STUDIES_DIR = "case_studies"
NEWS_DIR = "data/news"
OUTPUT_FILE = "site/index.html"
ARCHIVE_CS_FILE = "case_studies/archive.json"
ARCHIVE_NEWS_FILE = "data/news_archive.json"
SLUG_MAP_FILE = "site/breakdowns/slug_map.json"

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

def relevance_dot(signal):
    labels = {"high": "High", "medium": "Medium", "low": "Low"}
    word = labels.get(signal, esc(signal).capitalize())
    level = signal if signal in labels else "none"
    return f'<span class="rel-dot rel-{level}" aria-hidden="true"></span><span class="rel-word">{word} relevance</span>'

def tag_pill(tag):
    tag = str(tag or "Other")
    if "_" in tag:  # raw snake_case from the scorer
        tag = tag.replace("_", " ").capitalize()
    return f'<span class="tag-pill">{esc(tag)}</span>'

def score_ring(score, max_score=75):
    """Ring out of 75. A legacy score above 75 (old 0-100 scale) gets no ring rather than a ring past full."""
    if not isinstance(score, (int, float)) or not 0 <= score <= max_score:
        return ""
    pct = int((score / max_score) * 100)
    level = "high" if pct >= 70 else "mid" if pct >= 40 else "low"
    legend = "AI SCOUT SCORE: A 0-75 metric for Technical Novelty, Market Maturity, and Business Impact."
    return (f'<div class="score-ring ring-{level}" style="--pct:{pct};" title="{legend}" role="img" '
            f'aria-label="Scout score {score} of {max_score}"><span>{score}</span></div>')

def tier_label(tier):
    labels = {"ai_labs": "AI Lab", "consulting": "Consulting", "news": "Press", "universities": "Academic"}
    return f'<span class="tier-label">{esc(labels.get(tier, tier))}</span>'

def eval_status(cs):
    """eval_verdict (the judge) first, then the Gemini field; anything not pass/fail is still pending."""
    return next((s for s in (cs.get("eval_verdict"), cs.get("gemini_eval_status")) if s in ("pass", "fail")), "pending")

def status_badge(status):
    level, text = {"pass": ("pass", "Verified"), "fail": ("fail", "Flagged")}.get(status, ("pending", "Evaluating"))
    return f'<span class="status-badge status-{level}">{text}</span>'

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

def render_case_studies(data, slug_map):
    if not data or not data.get("case_studies"):
        return "<div class='empty-state'><p>No case studies available yet.</p></div>"
    cs_slugs = slug_map.get("case_studies", {})
    cards = []
    for cs in data["case_studies"]:
        authors = esc(", ".join(cs.get("authors", [])) or "Unknown authors")
        pub_raw = cs.get("pub_date", "")[:10]
        try:
            from datetime import datetime as dt
            pub = dt.strptime(pub_raw, "%Y-%m-%d").strftime("%d %b %Y")
        except:
            pub = esc(pub_raw)
        url = esc(safe_url(cs.get("url", "#")))
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
        breakdown_link = f'<a href="breakdowns/{slug}.html" class="breakdown-link">IT Breakdown</a>' if slug else ""
        total = cs.get("total_score", 0)

        cards.append(f"""<article class="card cs-card">
  <div class="card-header">
    <div class="card-tags">{tag_pill(cs.get("relevance_tag", "Other"))}{status_badge(eval_status(cs))}</div>
    {score_ring(total)}
  </div>
  <h2 class="card-title"><a href="{url}" target="_blank" rel="noopener noreferrer">{title}</a></h2>
  <div class="card-meta">{meta_html}</div>
  <p class="card-summary">{esc(cs.get('summary',''))}</p>
  {insight_html("How to apply:", cs.get('it_breakdown','') or cs.get('how_to_apply',''))}
  <details class="agent-trace">
    <summary class="trace-toggle">View Technical Reasoning</summary>
    <div class="trace-body">
      <div class="trace-row"><span class="trace-label">Impact</span><span class="trace-val">{esc(cs.get('real_world_impact',0))}/25</span></div>
      <div class="trace-row"><span class="trace-label">Maturity</span><span class="trace-val">{esc(cs.get('ai_maturity',0))}/25</span></div>
      <div class="trace-row"><span class="trace-label">Monetisation</span><span class="trace-val">{esc(cs.get('monetisation_potential',0))}/25</span></div>
      <p class="trace-reasoning">{esc(cs.get('score_reasoning',''))}</p>
    </div>
  </details>
  <div class="card-footer">
    {breakdown_link}
    <button type="button" class="md-export-btn"
      data-title="{title}"
      data-summary="{esc(cs.get('summary',''))}"
      data-it="{esc(cs.get('it_breakdown',''))}"
      data-monetisation="{esc(cs.get('monetisation',''))}"
      data-reasoning="{esc(cs.get('score_reasoning',''))}"
      data-score="{esc(total)}"
      data-url="{url}">Copy as Markdown</button>
  </div>
</article>""")
    return "\n".join(cards)

def render_news(data, slug_map):
    if not data or not data.get("news"):
        return "<div class='empty-state'><p>No news available yet.</p></div>"
    news_slugs = slug_map.get("news", {})
    cards = []
    for item in data["news"]:
        url = esc(safe_url(item.get("url", "#")))
        title = esc(item.get("title", ""))

        slug = news_slugs.get(item.get("title", ""))  # slug_map is keyed by the raw title
        breakdown_link = f'<a href="breakdowns/{slug}.html" class="breakdown-link">IT Breakdown</a>' if slug else ""
        meta = [esc(v) for v in (item.get('source', ''), item.get('pub_date', '')) if v]
        meta_html = '<span class="sep" aria-hidden="true">•</span>'.join(f"<span>{m}</span>" for m in meta)

        cards.append(f"""<article class="card news-card">
  <div class="card-header">
    <div class="card-tags">
      {tier_label(item.get("tier", "news"))}
      {tag_pill(item.get("category", "Other"))}
    </div>
    <div class="news-score-area">
      {relevance_dot(item.get("relevance_signal", "medium"))}
      <span class="score-num">{esc(item.get('relevance_score',0))}/10</span>
    </div>
  </div>
  <h2 class="card-title"><a href="{url}" target="_blank" rel="noopener noreferrer">{title}</a></h2>
  <div class="card-meta">{meta_html}</div>
  <p class="card-summary">{esc(item.get('summary',''))}</p>
  {signal_html(item.get('enterprise_use',''))}
  {f'<div class="card-footer">{breakdown_link}</div>' if breakdown_link else ""}
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

def render_archive(archive, kind="cs"):
    if not archive:
        return "<div class='empty-state'><p>No archive yet.</p></div>"
    rows = []
    key = "case_studies" if kind == "cs" else "news"
    for entry in [e for e in archive if e.get("total", 0)][:30]:
        preview_html = "".join(
            f'<a class="archive-title" href="{esc(safe_url(i.get("url")))}" target="_blank" rel="noopener noreferrer">{esc(i.get("title", ""))}</a>'
            for i in entry.get(key, [])[:3])
        rows.append(f"""<div class="archive-row">
  <div class="archive-date">{esc(entry.get("date", ""))}</div>
  <div class="archive-count">{esc(entry.get("total", 0))} items</div>
  <div class="archive-previews">{preview_html}</div>
</div>""")
    return "\n".join(rows) or "<div class='empty-state'><p>No archive yet.</p></div>"

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
    <title>Scout Daily — AI Intelligence Feed</title>
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


def generate_email_capture():
    return """
  <div class="email-banner">
    <div class="email-inner">
      <p class="email-title" id="email-title">Get Scout Daily in your inbox</p>
      <form class="email-form" aria-labelledby="email-title">
        <label class="visually-hidden" for="email-input">Email address</label>
        <input type="email" id="email-input" class="email-input" placeholder="your@email.com" autocomplete="email" required />
        <button type="submit" class="email-btn">Subscribe</button>
      </form>
      <span class="email-confirm" id="email-confirm" role="status"></span>
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

    # Fallback: If no active items (all archived), load latest from archive
    if not active_cs and cs_archive:
        active_cs = cs_archive[0].get("case_studies", [])
        print(f"Fallback: Loaded {len(active_cs)} case studies from archive.")
    active_cs = [cs for cs in active_cs if in_window(cs, cutoff)]
    active_cs.sort(key=lambda x: by_score(x, "total_score"), reverse=True)
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

    # Fallback: If no active news (all archived), load latest from archive
    if not active_news and news_archive:
        active_news = news_archive[0].get("news", [])
        print(f"Fallback: Loaded {len(active_news)} news items from archive.")
    if not active_headlines and news_archive:
        active_headlines = news_archive[0].get("headlines", [])
    active_headlines.sort(key=lambda x: by_score(x, "relevance_score"), reverse=True)
    news_data = {"news": active_news}

    # --- Metadata ---
    slug_map = load_slug_map()
    daily_hook = ""
    hook_path = Path("data/daily_hook.txt")
    if hook_path.exists():
        with open(hook_path) as f:
            daily_hook = f.read().strip()
    cs_count = len(active_cs)
    news_count = len(active_news)
    generated_at = datetime.utcnow().strftime("%d %b %Y, %H:%M UTC")

    html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Scout — AI Intelligence Feed</title>
  <style>
    :root {
      /* Tokens from scout-daily-tokens.css (24 Nov 2026) */
      --font: "Satoshi", ui-sans-serif, system-ui, sans-serif;

      --fs-hero: 72px;  --lh-hero: 1.08;  --ls-hero: -1.8px;
      --fs-h2: 48px;    --lh-h2: 1;       --ls-h2: -1.2px;
      --fs-h3: 30px;    --lh-h3: 1.375;
      --fs-lead: 21px;  --lh-lead: 1.625;
      --fs-body: 16px;  --lh-body: 24px;
      --fs-small: 14px; --lh-small: 20px;
      --fs-label: 12px; --lh-label: 16px; --ls-label: 0.6px;

      --c-text: #171717;
      --c-text-muted: #737373;
      --c-bg: #ffffff;
      --c-bg-alt: #f5f5f5;
      --c-surface-dark: #171717;
      --c-surface-dark-2: #212121;
      --c-border: #e5e5e5;
      --c-border-dark: #333333;
      --c-hero-bottom: #292929;
      --g-hero: linear-gradient(#000, var(--c-hero-bottom));
      --c-accent: #22d3ee;

      --radius-sm: 6px;
      --radius: 8px;
      --radius-card: 12px;
      --radius-lg: 16px;
      --radius-panel: 28.8px;
      --radius-pill: 9999px;

      --content-max: 1152px;
      --reading-max: 672px;
      --gap: 8px;
      --section-pad: 80px;

      /* Added for Scout, not in the token file */
      --c-on-dark-muted: #a3a3a3;  /* muted text on the hero: 5.8:1 on #292929 */
      --c-good: #15803d;           /* pass / high score: 5.0:1 on white */
      --c-warn: #b45309;           /* pending / mid score: 5.0:1 on white */
      --c-bad: #b91c1c;            /* fail / low score: 6.5:1 on white */
      --fs-hero-mobile: 44px;
      --tap: 44px;
    }

    @font-face { font-family: "Satoshi"; src: url("fonts/satoshi-400.woff2") format("woff2"); font-weight: 400; font-style: normal; font-display: swap; }
    @font-face { font-family: "Satoshi"; src: url("fonts/satoshi-500.woff2") format("woff2"); font-weight: 500; font-style: normal; font-display: swap; }
    @font-face { font-family: "Satoshi"; src: url("fonts/satoshi-700.woff2") format("woff2"); font-weight: 700; font-style: normal; font-display: swap; }

    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      background: var(--c-bg);
      color: var(--c-text);
      font-family: var(--font);
      font-size: var(--fs-body);
      line-height: var(--lh-body);
      min-height: 100vh;
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
      padding: 24px 0 calc(64px + var(--section-pad));
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

    .brand {
      display: flex;
      align-items: center;
      font-size: var(--fs-lead);
      font-weight: 700;
      line-height: 1;
    }

    .brand-name { white-space: nowrap; }

    .brand-cursor {
      display: inline-block;
      width: 0.2em;
      height: 0.8em;
      background: var(--c-accent);
      margin-left: 4px;
      animation: blink 1s 3;
      border-radius: var(--radius-sm);
    }

    @keyframes blink { 0%,100%{opacity:1} 50%{opacity:0.3} }

    .header-stats { display: flex; gap: 32px; }

    .stat { text-align: right; }

    .stat-num {
      font-size: var(--fs-h3);
      font-weight: 700;
      line-height: 1;
      font-variant-numeric: tabular-nums;
    }

    .stat-label, .tag-pill, .status-badge, .signal-title {
      font-size: var(--fs-label);
      line-height: var(--lh-label);
      letter-spacing: var(--ls-label);
      font-weight: 600;
      text-transform: uppercase;
    }

    .stat-label { color: var(--c-on-dark-muted); margin-top: 4px; }

    .hero-title {
      max-width: 900px;
      margin: var(--section-pad) 0 16px;
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
      max-width: var(--reading-max);
      background: var(--c-surface-dark-2);
      border: 1px solid var(--c-border-dark);
      border-radius: var(--radius-card);
      padding: 20px;
    }

    .email-inner { display: flex; flex-direction: column; gap: 12px; }

    .email-title { font-size: var(--fs-body); font-weight: 600; }

    .email-form { display: flex; gap: var(--gap); }

    .email-input {
      flex: 1;
      min-width: 0;
      background: var(--c-surface-dark);
      border: 1px solid var(--c-border-dark);
      border-radius: var(--radius);
      padding: 10px 14px;
      font: inherit;
      color: var(--c-bg);
      transition: border-color 0.15s;
    }

    .email-input::placeholder { color: var(--c-on-dark-muted); }
    .email-input:focus { border-color: var(--c-bg); }

    .email-confirm { font-size: var(--fs-small); color: var(--c-on-dark-muted); }

    /* Buttons: flat, no shadow */
    .email-btn, .breakdown-link, .md-export-btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      font: inherit;
      font-weight: 600;
      line-height: 1;
      border: 1px solid transparent;
      border-radius: var(--radius);
      white-space: nowrap;
      cursor: pointer;
      text-decoration: none;
      transition: background 0.15s, border-color 0.15s, color 0.15s;
    }

    .email-btn { padding: 10px 20px; font-size: var(--fs-body); background: var(--c-bg); color: var(--c-text); }
    .email-btn:hover { background: var(--c-border); }

    .breakdown-link, .md-export-btn { padding: 8px 14px; font-size: var(--fs-small); }
    .breakdown-link { background: var(--c-surface-dark); color: var(--c-bg); }
    .breakdown-link:hover { background: var(--c-border-dark); }
    .md-export-btn { background: var(--c-bg); color: var(--c-text); border-color: var(--c-border); }
    .md-export-btn:hover { border-color: var(--c-text); }
    .md-export-btn.copied { color: var(--c-good); border-color: var(--c-good); }

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
      flex-wrap: wrap;
      gap: 4px;
      padding: 4px;
      margin-bottom: 8px;
      border: 1px solid var(--c-border);
      border-radius: var(--radius-pill);
    }

    .tab {
      padding: 8px 16px;
      font: inherit;
      font-size: var(--fs-small);
      font-weight: 600;
      color: var(--c-text-muted);
      cursor: pointer;
      border: none;
      background: none;
      border-radius: var(--radius-pill);
      transition: color 0.15s, background-color 0.15s;
      white-space: nowrap;
    }

    .tab:hover { color: var(--c-text); }
    .tab.active { color: var(--c-bg); background: var(--c-surface-dark); }

    .search {
      background: var(--c-bg);
      border: 1px solid var(--c-border);
      border-radius: var(--radius);
      padding: 8px 14px;
      margin-bottom: 8px;
      font: inherit;
      font-size: var(--fs-small);
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
      gap: 24px;
    }

    /* Lead story + grid: the first card spans 2 columns (and 2 rows above 1024px) */
    @media (min-width: 641px) {
      .cards-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .cards-grid > .card:first-child { grid-column: span 2; }
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

    .card {
      background: var(--c-bg);
      border: 1px solid var(--c-border);
      border-radius: var(--radius-card);
      padding: 24px;
      min-width: 0;
    }

    .card-header {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 14px;
    }

    .card-tags { display: flex; align-items: center; gap: var(--gap); flex-wrap: wrap; flex-grow: 1; }

    .tag-pill, .status-badge { padding: 4px 8px; border-radius: var(--radius-sm); }

    .tag-pill { color: var(--c-text); background: var(--c-bg-alt); }
    .status-badge { background: color-mix(in srgb, currentColor 6%, var(--c-bg)); }  /* 8% put pending at 4.49:1 */
    .status-pass { color: var(--c-good); }
    .status-fail { color: var(--c-bad); }
    .status-pending { color: var(--c-warn); }

    .tier-label, .source-name {
      font-size: var(--fs-label);
      line-height: var(--lh-label);
      font-weight: 600;
      color: var(--c-text-muted);
    }

    /* Score ring: out of 75 */
    .score-ring {
      --ring: var(--c-bad);
      width: 48px;
      height: 48px;
      border-radius: var(--radius-pill);
      background: conic-gradient(var(--ring) calc(var(--pct) * 1%), var(--c-border) 0);
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
      position: relative;
    }

    .ring-high { --ring: var(--c-good); }
    .ring-mid { --ring: var(--c-warn); }

    .score-ring::before {
      content: '';
      position: absolute;
      width: 38px;
      height: 38px;
      background: var(--c-bg);
      border-radius: var(--radius-pill);
    }

    .score-ring span {
      position: relative;
      font-size: var(--fs-label);
      font-weight: 700;
      color: var(--c-text);
      font-variant-numeric: tabular-nums;
    }

    /* News relevance: a word beside the dot, never colour alone */
    .news-score-area {
      display: flex;
      align-items: center;
      gap: 6px;
      flex-shrink: 0;
    }

    .rel-dot {
      width: 10px;
      height: 10px;
      border-radius: var(--radius-pill);
      flex-shrink: 0;
      background: var(--c-text-muted);
    }

    .rel-high { background: var(--c-good); }
    .rel-medium { background: var(--c-warn); }
    .rel-low { background: var(--c-bad); }

    .rel-word, .score-num {
      font-size: var(--fs-label);
      color: var(--c-text-muted);
      white-space: nowrap;
    }

    .score-num { font-weight: 700; font-variant-numeric: tabular-nums; }

    /* Card content */
    .card-title {
      font-size: var(--fs-lead);
      font-weight: 700;
      line-height: var(--lh-h3);
      margin-bottom: 12px;
      overflow-wrap: anywhere;
      text-wrap: balance;
    }

    .card-title a { color: var(--c-text); text-decoration: none; }
    .card-title a:hover { text-decoration: underline; text-underline-offset: 4px; text-decoration-color: var(--c-text-muted); }

    .card-meta {
      display: flex;
      align-items: center;
      gap: var(--gap);
      flex-wrap: wrap;
      margin-bottom: 18px;
      font-size: var(--fs-small);
      line-height: var(--lh-small);
      color: var(--c-text-muted);
    }

    .card-meta > span:first-child { color: var(--c-text); font-weight: 600; }
    .pub-date { font-size: var(--fs-label); color: var(--c-text-muted); white-space: nowrap; font-variant-numeric: tabular-nums; }
    .sep { color: var(--c-text-muted); }

    .card-summary {
      font-size: var(--fs-body);
      line-height: var(--lh-body);
      color: var(--c-text-muted);
      margin-bottom: 16px;
    }

    .insight, .signal-points {
      font-size: var(--fs-small);
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
    .signal-points li { margin-bottom: 6px; }

    .agent-trace { margin-bottom: 4px; }

    .trace-toggle {
      display: inline-flex;
      align-items: center;
      border: 1px solid var(--c-border);
      color: var(--c-text-muted);
      font-size: var(--fs-small);
      padding: 6px 12px;
      border-radius: var(--radius);
      cursor: pointer;
      transition: color 0.15s, border-color 0.15s;
      list-style: none;
    }

    .trace-toggle::-webkit-details-marker { display: none; }
    .trace-toggle:hover, .agent-trace[open] .trace-toggle { color: var(--c-text); border-color: var(--c-text); }

    .trace-body { padding: 12px 0 4px; }

    .trace-row {
      display: flex;
      justify-content: space-between;
      padding: 4px 0;
      border-bottom: 1px solid var(--c-border);
      margin-bottom: 4px;
    }

    .trace-label { font-size: var(--fs-label); color: var(--c-text-muted); }

    .trace-val {
      font-size: var(--fs-label);
      color: var(--c-text);
      font-weight: 600;
      font-variant-numeric: tabular-nums;
    }

    .trace-reasoning {
      font-size: var(--fs-small);
      line-height: var(--lh-body);
      color: var(--c-text-muted);
      margin-top: 10px;
      font-style: italic;
    }

    /* Card footer */
    .card-footer {
      display: flex;
      align-items: center;
      margin-top: 16px;
      padding-top: 14px;
      border-top: 1px solid var(--c-border);
      gap: 10px;
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

    .signal-title { color: var(--c-text); margin-bottom: var(--gap); }
    .signal p { font-size: var(--fs-body); color: var(--c-text); }

    /* Archive */
    .archive-list { display: flex; flex-direction: column; gap: var(--gap); }

    .archive-row {
      background: var(--c-bg);
      border: 1px solid var(--c-border);
      border-radius: var(--radius-card);
      padding: 14px 20px;
      display: grid;
      grid-template-columns: 110px 80px minmax(0, 1fr);
      gap: 20px;
      align-items: start;
    }

    .archive-date, .archive-count { font-size: var(--fs-label); font-variant-numeric: tabular-nums; }
    .archive-date { color: var(--c-text); font-weight: 600; }
    .archive-count { color: var(--c-text-muted); }

    .archive-previews { display: flex; flex-direction: column; gap: 4px; min-width: 0; }

    .archive-title {
      font-size: var(--fs-small);
      color: var(--c-text);
      text-decoration: none;
      overflow-wrap: anywhere;
    }

    .archive-title:hover { text-decoration: underline; }

    .empty-state {
      text-align: center;
      padding: 64px 32px;
      color: var(--c-text-muted);
      font-size: var(--fs-small);
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
      gap: var(--gap);
    }

    footer span { font-size: var(--fs-label); color: var(--c-text-muted); }

    @media (max-width: 700px) {
      .hero { padding-bottom: calc(64px + 48px); }
      .header-inner { padding: 0 16px; }
      .header-stats { gap: 20px; }
      .hero-title { margin-top: 48px; }
      .email-input { min-height: var(--tap); }
      .email-btn { min-height: var(--tap); padding: 0 18px; }
      .content { width: 100%; padding: 24px 16px 48px; }
      .toolbar { flex-direction: column; align-items: stretch; gap: var(--gap); position: static; }
      .tab { flex: 1 1 40%; min-height: var(--tap); padding: 10px 12px; }
      .search { width: 100%; min-height: var(--tap); font-size: var(--fs-body); }
      .card { padding: 18px; }
      .card-title { font-size: var(--fs-lead); }
      .card-title a { display: block; min-height: var(--tap); }
      .trace-toggle, .breakdown-link, .md-export-btn { min-height: var(--tap); }
      .archive-row { grid-template-columns: 1fr; gap: 6px; padding: 14px 16px; }
      .archive-title { display: flex; align-items: center; min-height: var(--tap); }
    }

    @media (max-width: 639px) {
      .hero-title { font-size: var(--fs-hero-mobile); }
    }

    @media (prefers-reduced-motion: reduce) {
      .brand-cursor { animation: none; }
      * { transition: none !important; }
    }
  </style>
</head>
<body>

<header class="header hero">
  <div class="header-inner">
    <div class="header-top">
      <h1 class="brand">
        <span class="brand-name">Scout Daily</span><span class="brand-cursor" aria-hidden="true"></span>
      </h1>
      <div class="header-stats">
        <div class="stat">
          <div class="stat-num">""" + str(cs_count) + """</div>
          <div class="stat-label">Case Studies</div>
        </div>
        <div class="stat">
          <div class="stat-num">""" + str(news_count) + """</div>
          <div class="stat-label">News Items</div>
        </div>
      </div>
    </div>
    <p class="hero-title">AI agent case studies and news, <span class="accent-word">scored every day</span>.</p>
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
    <div class="cards-grid">""" + render_case_studies(cs_data, slug_map) + """</div>
  </div>

  <div id="industry-news" class="panel" role="tabpanel" aria-labelledby="tab-industry-news">
    """ + (f'''
    <section class="signal">
        <h2 class="signal-title">Today\'s signal</h2>
        <p>{esc(daily_hook)}</p>
    </section>
    ''' if daily_hook else "") + render_headlines(active_headlines) + """
    <div class="cards-grid">""" + render_news(news_data, slug_map) + """</div>
  </div>

  <div id="archive-cs" class="panel" role="tabpanel" aria-labelledby="tab-archive-cs">
    <div class="archive-list">""" + render_archive(cs_archive, 'cs') + """</div>
  </div>

  <div id="archive-news" class="panel" role="tabpanel" aria-labelledby="tab-archive-news">
    <div class="archive-list">""" + render_archive(news_archive, 'news') + """</div>
  </div>
</main>

<footer>
  <span>Scout · LangGraph + Groq + arXiv</span>
  <span>Case Studies: last 4 months · News: last 4–8 weeks</span>
</footer>

<script src="js/subscribe.js"></script>
<script>
  // Handlers are attached here, not as on*= attributes, so the page carries no inline event handlers
  document.querySelectorAll('.tab').forEach(t => t.addEventListener('click', () => showTab(t.getAttribute('aria-controls'), t)));
  document.getElementById('scout-search').addEventListener('input', e => filterCards(e.target.value));
  document.querySelector('.email-form').addEventListener('submit', e => submitEmail(e));
  document.addEventListener('click', e => { const b = e.target.closest('.md-export-btn'); if (b) copyMarkdown(b); });

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

  function copyMarkdown(btn) {
    const title = btn.dataset.title;
    const summary = btn.dataset.summary;
    const it = btn.dataset.it;
    const monetisation = btn.dataset.monetisation;
    const reasoning = btn.dataset.reasoning;
    const score = btn.dataset.score;
    const url = btn.dataset.url;
    const md = [
      "# " + title,
      "",
      "**Score:** " + score + "/75",
      "",
      "## Summary",
      summary,
      "",
      "## IT Engineer Breakdown",
      it,
      "",
      "## Business Opportunity",
      monetisation,
      "",
      "## Agent Reasoning",
      reasoning,
      "",
      "**Source:** " + url,
    ].join("\\n");
    navigator.clipboard.writeText(md).then(() => {
      btn.classList.add("copied");
      btn.textContent = "Copied";
      setTimeout(() => {
        btn.classList.remove("copied");
        btn.textContent = "Copy as Markdown";
      }, 2000);
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
    print("HTML generated -> " + OUTPUT_FILE)

if __name__ == "__main__":
    generate_html()
