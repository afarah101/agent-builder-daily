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

        cards.append(f"""<article class="card news-card">
  <div class="card-header">
    <div class="card-tags">
      {tier_label(item.get("tier", "news"))}
      <span class="source-name">{esc(item.get('source',''))}</span>
      {tag_pill(item.get("category", "Other"))}
    </div>
    <div class="news-score-area">
      {relevance_dot(item.get("relevance_signal", "medium"))}
      <span class="score-num">{esc(item.get('relevance_score',0))}/10</span>
    </div>
  </div>
  <h2 class="card-title"><a href="{url}" target="_blank" rel="noopener noreferrer">{title}</a></h2>
  <p class="card-summary">{esc(item.get('summary',''))}</p>
  {signal_html(item.get('enterprise_use',''))}
  <div class="card-footer">
    <span class="pub-date">{esc(item.get('pub_date', ''))}</span>
    {breakdown_link}
  </div>
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
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Syne:wght@800&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    :root {
      --bg: #0f1117;
      --surface: #1a1d27;
      --surface2: #22263a;
      --border: #2d3148;
      --text: #f1f5f9;
      --text-secondary: #94a3b8;
      --text-muted: #8592a8;
      --accent: #00ff88;
      --accent-ink: #0f1117;
      --accent-tint: rgba(0, 255, 136, 0.1);
      --warn: #fbbf24;
      --danger: #f87171;
      --fs-xs: 12px;
      --fs-sm: 13px;
      --radius: 8px;
      --tap: 44px;
    }

    body {
      background: var(--bg);
      color: var(--text);
      font-family: 'Inter', sans-serif;
      font-size: 15px;
      line-height: 1.6;
      min-height: 100vh;
    }

    ::selection { background: var(--accent); color: var(--accent-ink); }
    :focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

    .visually-hidden {
      position: absolute;
      width: 1px;
      height: 1px;
      overflow: hidden;
      clip: rect(0 0 0 0);
      white-space: nowrap;
    }

    /* Header */
    .header {
      border-bottom: 1px solid var(--border);
      padding: 32px 0 0;
      position: sticky;
      top: 0;
      background: var(--bg);
      z-index: 100;
    }

    .header-inner {
      max-width: 1200px;
      margin: 0 auto;
      padding: 0 32px;
    }

    .header-top {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 24px;
      flex-wrap: wrap;
      gap: 16px;
    }

    .brand {
      display: flex;
      align-items: center;
      font-family: 'Syne', sans-serif;
      font-size: clamp(32px, 9vw, 48px);
      font-weight: 800;
      letter-spacing: -1px;
      line-height: 1;
    }

    .brand-name { color: var(--accent); white-space: nowrap; }

    .brand-cursor {
      display: inline-block;
      width: 0.2em;
      height: 0.8em;
      background: var(--accent);
      margin-left: 4px;
      animation: blink 1s 3;
      border-radius: 2px;
    }

    @keyframes blink { 0%,100%{opacity:1} 50%{opacity:0.3} }

    .header-stats { display: flex; gap: 32px; }

    .stat { text-align: right; }

    .stat-num {
      font-size: 24px;
      font-weight: 600;
      color: var(--accent);
      line-height: 1;
      font-variant-numeric: tabular-nums;
    }

    .stat-label {
      font-size: var(--fs-xs);
      color: var(--text-muted);
      margin-top: 4px;
    }

    .updated {
      font-size: var(--fs-xs);
      color: var(--text-muted);
      margin-bottom: 20px;
    }

    .toolbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 16px;
    }

    /* Tabs */
    .tabs { display: flex; flex-wrap: wrap; gap: 0; }

    .tab {
      padding: 10px 20px;
      font: inherit;
      font-size: var(--fs-sm);
      font-weight: 500;
      color: var(--text-muted);
      cursor: pointer;
      border: none;
      background: none;
      border-bottom: 2px solid transparent;
      transition: color 0.15s, border-color 0.15s;
      white-space: nowrap;
    }

    .tab:hover { color: var(--text); }
    .tab.active { color: var(--text); border-bottom-color: var(--accent); }

    .search {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 7px 14px;
      font: inherit;
      font-size: var(--fs-sm);
      color: var(--text);
      width: 220px;
      transition: border-color 0.15s;
    }

    .search::placeholder, .email-input::placeholder { color: var(--text-muted); }
    .search:focus { border-color: var(--accent); }

    /* Content */
    .content {
      max-width: 1200px;
      margin: 0 auto;
      padding: 32px 32px 64px;
    }

    .panel { display: none; }
    .panel.active { display: block; }

    /* Cards Grid */
    .cards-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(min(540px, 100%), 1fr));
      gap: 20px;
    }

    .card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
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

    .card-tags { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; flex-grow: 1; }

    .tag-pill, .status-badge {
      font-size: var(--fs-xs);
      font-weight: 600;
      padding: 3px 8px;
      border-radius: 4px;
      line-height: 1.5;
    }

    .tag-pill { color: var(--text-secondary); background: var(--surface2); }
    .status-pass { color: var(--accent); background: var(--accent-tint); }
    .status-fail { color: var(--danger); background: rgba(248, 113, 113, 0.1); }
    .status-pending { color: var(--warn); background: rgba(251, 191, 36, 0.1); }

    .tier-label, .source-name {
      font-size: var(--fs-xs);
      font-weight: 600;
      color: var(--text-secondary);
    }

    /* Score ring: out of 75 */
    .score-ring {
      --ring: var(--danger);
      width: 48px;
      height: 48px;
      border-radius: 50%;
      background: conic-gradient(var(--ring) calc(var(--pct) * 1%), var(--surface2) 0);
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
      position: relative;
    }

    .ring-high { --ring: var(--accent); }
    .ring-mid { --ring: var(--warn); }

    .score-ring::before {
      content: '';
      position: absolute;
      width: 38px;
      height: 38px;
      background: var(--surface);
      border-radius: 50%;
    }

    .score-ring span {
      position: relative;
      font-size: var(--fs-xs);
      font-weight: 700;
      color: var(--text);
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
      border-radius: 50%;
      flex-shrink: 0;
      background: var(--text-muted);
    }

    .rel-high { background: var(--accent); }
    .rel-medium { background: var(--warn); }
    .rel-low { background: var(--danger); }

    .rel-word, .score-num {
      font-size: var(--fs-xs);
      color: var(--text-secondary);
      white-space: nowrap;
    }

    .score-num { font-weight: 700; font-variant-numeric: tabular-nums; }

    /* Card content */
    .card-title {
      font-family: 'Georgia', serif;
      font-size: clamp(1.25rem, 1rem + 1.2vw, 1.6rem);
      font-weight: 700;
      line-height: 1.3;
      margin-bottom: 12px;
      overflow-wrap: anywhere;
      text-wrap: balance;
    }

    .card-title a { color: #ffffff; text-decoration: none; }
    .card-title a:hover { text-decoration: underline; text-underline-offset: 4px; text-decoration-color: var(--accent); }

    .card-meta {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
      margin-bottom: 18px;
      font-size: var(--fs-sm);
      color: var(--text-secondary);
    }

    .card-meta > span:first-child { color: var(--accent); font-weight: 600; }
    .pub-date { font-size: var(--fs-xs); color: var(--text-muted); white-space: nowrap; font-variant-numeric: tabular-nums; }
    .sep { color: var(--text-muted); }

    .card-summary {
      font-size: 15px;
      line-height: 1.6;
      color: var(--text-secondary);
      margin-bottom: 16px;
    }

    .insight {
      font-size: 14px;
      line-height: 1.6;
      color: var(--text);
      margin-bottom: 16px;
    }

    .insight strong { font-weight: 600; color: var(--accent); }

    .signal-points {
      padding-left: 20px;
      margin-bottom: 16px;
      font-size: 14px;
      line-height: 1.6;
      color: var(--text);
    }

    .signal-points li { margin-bottom: 6px; }
    .signal-points strong { font-weight: 600; color: var(--accent); }

    .agent-trace { margin-bottom: 4px; }

    .trace-toggle {
      display: inline-flex;
      align-items: center;
      border: 1px solid var(--border);
      color: var(--text-secondary);
      font-size: var(--fs-sm);
      padding: 6px 12px;
      border-radius: 6px;
      cursor: pointer;
      transition: color 0.15s, border-color 0.15s;
      list-style: none;
    }

    .trace-toggle::-webkit-details-marker { display: none; }
    .trace-toggle:hover, .agent-trace[open] .trace-toggle { color: var(--text); border-color: var(--accent); }

    .trace-body { padding: 12px 0 4px; }

    .trace-row {
      display: flex;
      justify-content: space-between;
      padding: 4px 0;
      border-bottom: 1px solid var(--border);
      margin-bottom: 4px;
    }

    .trace-label { font-size: var(--fs-xs); color: var(--text-muted); }

    .trace-val {
      font-size: var(--fs-xs);
      color: var(--accent);
      font-weight: 600;
      font-variant-numeric: tabular-nums;
    }

    .trace-reasoning {
      font-size: var(--fs-sm);
      line-height: 1.6;
      color: var(--text-secondary);
      margin-top: 10px;
      font-style: italic;
    }

    /* Card footer */
    .card-footer {
      display: flex;
      align-items: center;
      margin-top: 16px;
      padding-top: 14px;
      border-top: 1px solid var(--border);
      gap: 10px;
      flex-wrap: wrap;
    }

    .breakdown-link, .md-export-btn {
      display: inline-flex;
      align-items: center;
      font: inherit;
      font-size: var(--fs-sm);
      font-weight: 600;
      white-space: nowrap;
      padding: 6px 12px;
      border-radius: 6px;
      transition: background 0.15s, border-color 0.15s, color 0.15s;
    }

    .breakdown-link {
      color: var(--accent);
      text-decoration: none;
      background: var(--accent-tint);
      border: 1px solid rgba(0, 255, 136, 0.2);
    }

    .breakdown-link:hover { background: rgba(0, 255, 136, 0.16); border-color: rgba(0, 255, 136, 0.35); }

    .md-export-btn {
      color: var(--text-secondary);
      background: none;
      border: 1px solid var(--border);
      cursor: pointer;
    }

    .md-export-btn:hover { color: var(--text); border-color: var(--text-muted); }
    .md-export-btn.copied { color: var(--accent); border-color: var(--accent); }

    /* Today's signal */
    .signal {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 16px 20px;
      margin-bottom: 24px;
    }

    .signal-title { font-size: 15px; font-weight: 600; color: var(--accent); margin-bottom: 4px; }
    .signal p { font-size: 15px; color: var(--text); }

    /* Archive */
    .archive-list { display: flex; flex-direction: column; gap: 8px; }

    .archive-row {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 14px 20px;
      display: grid;
      grid-template-columns: 110px 80px minmax(0, 1fr);
      gap: 20px;
      align-items: start;
    }

    .archive-date, .archive-count { font-size: var(--fs-xs); font-variant-numeric: tabular-nums; }
    .archive-date { color: var(--accent); }
    .archive-count { color: var(--text-muted); }

    .archive-previews { display: flex; flex-direction: column; gap: 4px; min-width: 0; }

    .archive-title {
      font-size: var(--fs-sm);
      color: var(--text-secondary);
      text-decoration: none;
      overflow-wrap: anywhere;
    }

    .archive-title:hover { color: var(--text); text-decoration: underline; }

    .empty-state {
      text-align: center;
      padding: 64px 32px;
      color: var(--text-muted);
      font-size: 14px;
    }

    footer {
      border-top: 1px solid var(--border);
      padding: 20px 32px;
      max-width: 1200px;
      margin: 0 auto;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 8px;
    }

    footer span { font-size: var(--fs-xs); color: var(--text-muted); }

    /* Email capture */
    .email-banner {
      background: var(--surface);
      border-bottom: 1px solid var(--border);
      padding: 8px 32px;
    }

    .email-inner {
      max-width: 1200px;
      margin: 0 auto;
      display: flex;
      align-items: center;
      justify-content: center;
      flex-wrap: wrap;
      gap: 12px;
    }

    .email-title {
      font-size: 14px;
      font-weight: 600;
      color: var(--text-secondary);
    }

    .email-form {
      display: flex;
      gap: 6px;
      align-items: center;
    }

    .email-input {
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 5px 10px;
      font: inherit;
      font-size: var(--fs-sm);
      color: var(--text);
      width: 200px;
      transition: border-color 0.15s;
    }

    .email-input:focus { border-color: var(--accent); }

    .email-btn {
      background: var(--accent);
      color: var(--accent-ink);
      border: none;
      border-radius: 4px;
      padding: 5px 14px;
      font: inherit;
      font-size: var(--fs-sm);
      font-weight: 600;
      cursor: pointer;
      transition: opacity 0.15s;
      white-space: nowrap;
    }

    .email-btn:hover { opacity: 0.9; }

    .email-confirm { font-size: var(--fs-sm); color: var(--accent); }

    @media (max-width: 700px) {
      .header { position: static; padding-top: 20px; }
      .header-inner { padding: 0 16px; }
      .header-top { margin-bottom: 12px; }
      .updated { margin-bottom: 12px; }
      .toolbar { flex-direction: column; align-items: stretch; gap: 8px; padding-bottom: 8px; }
      .tabs { flex-wrap: wrap; }
      .tab { flex: 1 1 40%; min-height: var(--tap); padding: 10px 12px; }
      .search { width: 100%; min-height: var(--tap); font-size: 16px; }
      .email-banner { padding: 12px 16px; }
      .email-inner { flex-direction: column; align-items: stretch; gap: 8px; }
      .email-title { text-align: center; }
      .email-form { width: 100%; }
      .email-input { flex: 1; width: auto; min-width: 0; min-height: var(--tap); font-size: 16px; }
      .email-btn { min-height: var(--tap); padding: 0 18px; }
      .content { padding: 24px 16px 48px; }
      .card { padding: 18px; }
      .card-title a { display: block; min-height: var(--tap); }
      .trace-toggle, .breakdown-link, .md-export-btn { min-height: var(--tap); }
      .archive-row { grid-template-columns: 1fr; gap: 6px; padding: 14px 16px; }
      .archive-title { display: flex; align-items: center; min-height: var(--tap); }
    }

    @media (prefers-reduced-motion: reduce) {
      .brand-cursor { animation: none; }
      * { transition: none !important; }
    }
  </style>
</head>
<body>

<header class="header">
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
    <p class="updated">Updated """ + generated_at + """</p>
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
  </div>
</header>

""" + generate_email_capture() + """

<main class="content">
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
