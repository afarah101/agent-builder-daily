#!/usr/bin/env python3
import os, json, html as html_lib
from datetime import datetime, timedelta
from pathlib import Path

CASE_STUDIES_DIR = "case_studies"
NEWS_DIR = "logs"
OUTPUT_FILE = "site/index.html"
ARCHIVE_CS_FILE = "case_studies/archive.json"
ARCHIVE_NEWS_FILE = "logs/archive.json"
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

def load_slug_map():
    if os.path.exists(SLUG_MAP_FILE):
        with open(SLUG_MAP_FILE) as f:
            return json.load(f)
    return {"case_studies": {}, "news": {}}

def esc(val):
    return html_lib.escape(str(val)) if val else ""

def safe_url(url):
    if not url or not str(url).startswith(("https://", "http://")):
        return "#"
    return url

def relevance_dot(signal):
    colors = {"high": "#22c55e", "medium": "#f59e0b", "low": "#ef4444"}
    labels = {"high": "High relevance", "medium": "Medium relevance", "low": "Low relevance"}
    c = colors.get(signal, "#94a3b8")
    l = labels.get(signal, signal)
    return f'<span class="rel-dot" style="background:{c}" title="{l}"></span>'

def tag_pill(tag):
    tag_colors = {
        "AI Agents": "#7c3aed", "LLMs": "#0284c7", "Automation": "#059669",
        "Safety": "#dc2626", "Computer Vision": "#d97706", "NLP": "#0891b2",
        "MLOps": "#7c3aed", "Healthcare AI": "#db2777", "Other": "#64748b",
        "Product Launch": "#0284c7", "Research": "#7c3aed", "Partnership": "#059669",
        "Market Move": "#d97706", "Tool": "#0891b2", "Policy": "#dc2626",
    }
    c = tag_colors.get(tag, "#64748b")
    return f'<span class="tag-pill" style="background:{c}1a;color:{c};border:1px solid {c}33">{tag}</span>'

def score_ring(score, max_score=100):
    pct = int((score / max_score) * 100)
    color = "#22c55e" if pct >= 70 else "#f59e0b" if pct >= 40 else "#ef4444"
    legend = "AI SCOUT SCORE: A 0-100 composite metric of Technical Novelty, Business Impact, and Market Maturity."
    return f'<div class="score-ring" style="--pct:{pct};--color:{color}" title="{legend}"><span>{score}</span></div>'

def tier_label(tier):
    labels = {"ai_labs": "AI Lab", "consulting": "Consulting", "news": "Press", "universities": "Academic"}
    colors = {"ai_labs": "#0284c7", "consulting": "#7c3aed", "news": "#64748b", "universities": "#059669"}
    label = labels.get(tier, tier)
    color = colors.get(tier, "#64748b")
    return f'<span class="tier-label" style="color:{color}">{label}</span>'

def score_bar(label, score, max_val=25):
    pct = min(100, int((score / max_val) * 100))
    return f"""<div class="score-bar-row">
      <span class="bar-label">{label}</span>
      <div class="bar-track"><div class="bar-fill" style="width: {pct}%"></div></div>
      <span class="bar-val">{score}</span>
    </div>"""

def status_badge(status):
    if status == 'pass':
        color = "#22c55e"
        text = "✅ Gemini Verified"
        bg = "#22c55e1a"
    elif status == 'fail':
        color = "#ef4444"
        text = "❌ Flagged"
        bg = "#ef44441a"
    else:
        color = "#f59e0b"
        text = "⏳ Evaluating"
        bg = "#f59e0b1a"
    return f'<span class="status-badge" style="background:{bg};color:{color};border:1px solid {color}33">{text}</span>'

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
            pub = pub_raw
        url = safe_url(cs.get("url", "#"))
        tag = cs.get("relevance_tag", "Other")
        institution = esc(cs.get("institution", "Academic Institution"))
        built = cs.get("author_built_product", False)
        built_badge = '<span class="built-badge">🚀 Author shipped product</span>' if built else ""
        title = esc(cs.get("title", "Untitled"))
        
        # Status badge
        status = cs.get("gemini_eval_status", cs.get("eval_verdict", "pending"))
        badge_html = status_badge(status)

        # Breakdown link
        slug = cs_slugs.get(title)
        breakdown_link = (
            f'<a href="breakdowns/{slug}.html" class="breakdown-link" onclick="event.stopPropagation()">IT Breakdown →</a>'
            if slug else
            '<span class="breakdown-pending">Breakdown pending</span>'
        )

        cards.append(f"""<article class="card cs-card" onclick="window.open('{url}','_blank')">
  <div class="card-header">
    <div class="card-tags">{tag_pill(tag)}{built_badge}{badge_html}</div>
    {score_ring(cs.get('total_score', 0))}
  </div>
  <h3 class="card-title">{title}</h3>
  <div class="card-meta">
    <span class="authors">{authors}</span>
    <span class="sep">·</span>
    <span class="institution">{institution}</span>
    <span class="sep">·</span>
    <span class="pub-date">{pub}</span>
  </div>
  <p class="card-summary">{esc(cs.get('summary',''))}</p>
  <div class="insight-block">
    <div class="insight-label">HOW TO APPLY THIS</div>
    <p class="insight-text">{esc(cs.get('how_to_apply',''))}</p>
  </div>
  <div class="insight-block monetisation">
    <div class="insight-label">BUSINESS OPPORTUNITY</div>
    <p class="insight-text">{esc(cs.get('monetisation',''))}</p>
  </div>
  <details class="agent-trace">
    <summary class="trace-toggle">▶ Agent Trace</summary>
    <div class="trace-body">
      <div class="trace-row"><span class="trace-label">IMPACT</span><span class="trace-val">{cs.get('real_world_impact',0)}/25</span></div>
      <div class="trace-row"><span class="trace-label">MATURITY</span><span class="trace-val">{cs.get('ai_maturity',0)}/25</span></div>
      <div class="trace-row"><span class="trace-label">MONETISATION</span><span class="trace-val">{cs.get('monetisation_potential',0)}/25</span></div>
      <p class="trace-reasoning">{esc(cs.get('score_reasoning',''))}</p>
    </div>
  </details>
  <div class="card-footer">
    <div class="score-bars">
      {score_bar('Recency', cs.get('recency_score',0), 25)}
      {score_bar('Impact', cs.get('real_world_impact',0), 25)}
      {score_bar('Maturity', cs.get('ai_maturity',0), 25)}
      {score_bar('Monetisation', cs.get('monetisation_potential',0), 25)}
    </div>
    <div class="footer-links">
      <a href="{url}" target="_blank" rel="noopener noreferrer" class="read-link" onclick="event.stopPropagation()">Read Source →</a>
      {breakdown_link}
      <button class="md-export-btn" onclick="event.stopPropagation();copyMarkdown(this)"
        data-title="{title}"
        data-summary="{esc(cs.get('summary',''))}"
        data-it="{esc(cs.get('it_breakdown',''))}"
        data-monetisation="{esc(cs.get('monetisation',''))}"
        data-reasoning="{esc(cs.get('score_reasoning',''))}"
        data-score="{cs.get('total_score',0)}"
        data-url="{url}">
        ⬇ MD
      </button>
    </div>
  </div>
</article>""")
    return "\n".join(cards)

def render_news(data, slug_map):
    if not data or not data.get("news"):
        return "<div class='empty-state'><p>No news available yet.</p></div>"
    news_slugs = slug_map.get("news", {})
    cards = []
    for item in data["news"]:
        signal = item.get("relevance_signal", "medium")
        url = safe_url(item.get("url", "#"))
        tier = item.get("tier", "news")
        category = item.get("category", "Other")
        title = esc(item.get("title", ""))

        slug = news_slugs.get(title)
        breakdown_link = (
            f'<a href="breakdowns/{slug}.html" class="breakdown-link" onclick="event.stopPropagation()">IT Breakdown →</a>'
            if slug else
            '<span class="breakdown-pending">Breakdown pending</span>'
        )

        pub = item.get('pub_date', '')

        cards.append(f"""<article class="card news-card" onclick="window.open('{url}','_blank')">
  <div class="card-header">
    <div class="card-tags">
      {tier_label(tier)}
      <span class="source-name">{item.get('source','')}</span>
      {tag_pill(category)}
    </div>
    <div class="news-score-area">
      {relevance_dot(signal)}
      <span class="score-num">{item.get('relevance_score',0)}/10</span>
    </div>
  </div>
  <h3 class="card-title">{title}</h3>
  <p class="card-summary">{esc(item.get('summary',''))}</p>
  <div class="insight-block">
    <div class="insight-label">HOW TO USE THIS</div>
    <p class="insight-text">{esc(item.get('enterprise_use',''))}</p>
  </div>
  <div class="card-footer">
    <span class="pub-date">{pub}</span>
    <div class="footer-links">
      <a href="{url}" target="_blank" rel="noopener noreferrer" class="read-link" onclick="event.stopPropagation()">Read Source →</a>
      {breakdown_link}
    </div>
  </div>
</article>""")
    return "\n".join(cards)

def render_archive(archive, kind="cs"):
    if not archive:
        return "<div class='empty-state'><p>No archive yet.</p></div>"
    rows = []
    key = "case_studies" if kind == "cs" else "news"
    for entry in archive[:30]:
        date = entry.get("date","")
        total = entry.get("total", 0)
        items = entry.get(key, [])[:3]
        previews = [i.get("title","")[:60] for i in items]
        preview_html = "".join([f"<span class='archive-title'>{t}</span>" for t in previews])
        rows.append(f"""<div class="archive-row">
  <div class="archive-date">{date}</div>
  <div class="archive-count">{total} items</div>
  <div class="archive-previews">{preview_html}</div>
</div>""")
    return "\n".join(rows)

def generate_rss(cs_data, news_data):
    base_url = "https://agent-builder-daily.vercel.app"
    now_rfc = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")
    items = []

    if cs_data:
        for cs in cs_data.get("case_studies", [])[:10]:
            title = esc(cs.get("title", "Untitled"))
            url = cs.get("url", base_url)
            summary = esc(cs.get("summary", ""))
            it_breakdown = esc(cs.get("it_breakdown", ""))
            pub = cs.get("pub_date", "")
            try:
                pub_rfc = datetime.strptime(pub, "%Y-%m-%d").strftime("%a, %d %b %Y 07:00:00 +0000")
            except:
                pub_rfc = now_rfc
            items.append(f"""  <item>
    <title>{title}</title>
    <link>{url}</link>
    <guid>{url}</guid>
    <pubDate>{pub_rfc}</pubDate>
    <description><![CDATA[<p>{summary}</p><p><strong>IT Breakdown:</strong> {it_breakdown}</p>]]></description>
    <category>Case Study</category>
  </item>""")

    if news_data:
        for item in news_data.get("news", [])[:5]:
            title = esc(item.get("title", "Untitled"))
            url = item.get("url", base_url)
            summary = esc(item.get("summary", ""))
            pub = item.get("pub_date", "")
            try:
                pub_rfc = datetime.strptime(pub, "%Y-%m-%d").strftime("%a, %d %b %Y 07:00:00 +0000")
            except:
                pub_rfc = now_rfc
            items.append(f"""  <item>
    <title>[News] {title}</title>
    <link>{url}</link>
    <guid>{url}</guid>
    <pubDate>{pub_rfc}</pubDate>
    <description><![CDATA[<p>{summary}</p>]]></description>
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
      <h4 class="email-title">Get Scout Daily in your inbox</h4>
      <div class="email-form">
        <input type="email" class="email-input" placeholder="your@email.com" required onkeydown="if(event.key==='Enter') submitEmail(event)" />
        <button type="button" class="email-btn" onclick="submitEmail(event)">Subscribe</button>
      </div>
      <span class="email-confirm" id="email-confirm"></span>
    </div>
  </div>"""


def generate_html():
    # --- Rotation Logic ---
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    
    # --- Case Studies Archiving & Loading ---
    path_cs = Path(CASE_STUDIES_DIR)
    all_cs_files = [f for f in path_cs.glob("*.json") if "archive" not in f.name]
    cs_archive = load_json(ARCHIVE_CS_FILE)
    active_cs = []
    
    for f in sorted(all_cs_files):
        if f.stem < today_str:
            data = load_json(str(f))
            if data and "case_studies" in data:
                date_str = f.stem
                if not any(item["date"] == date_str for item in cs_archive):
                    cs_archive.insert(0, {
                        "date": date_str,
                        "total": len(data["case_studies"]),
                        "case_studies": data["case_studies"]
                    })
                os.remove(f)
                print(f"Archived and removed {f}")
        else:
            data = load_json(str(f))
            if data and "case_studies" in data:
                active_cs.extend(data["case_studies"])
                
    with open(ARCHIVE_CS_FILE, 'w') as f_out:
        json.dump(cs_archive, f_out, indent=2)
    
    # Sort active articles by pub_date descending
    active_cs.sort(key=lambda x: x.get("pub_date", ""), reverse=True)
    cs_data = {"case_studies": active_cs}

    # --- News Archiving & Loading ---
    path_news = Path(NEWS_DIR)
    all_news_files = [f for f in path_news.glob("news_*.json") if "archive" not in f.name]
    news_archive = load_json(ARCHIVE_NEWS_FILE)
    active_news = []
    
    for f in sorted(all_news_files):
        date_str = f.stem.replace("news_", "")
        if date_str < today_str:
            data = load_json(str(f))
            if data and "news" in data:
                if not any(item["date"] == date_str for item in news_archive):
                    news_archive.insert(0, {
                        "date": date_str,
                        "total": len(data["news"]),
                        "news": data["news"]
                    })
                os.remove(f)
                print(f"Archived and removed {f}")
        else:
            data = load_json(str(f))
            if data and "news" in data:
                active_news.extend(data["news"])
                
    with open(ARCHIVE_NEWS_FILE, 'w') as f_out:
        json.dump(news_archive, f_out, indent=2)

    active_news.sort(key=lambda x: x.get("pub_date", ""), reverse=True)
    news_data = {"news": active_news}

    # --- Metadata ---
    slug_map = load_slug_map()
    cs_count = len(active_cs)
    news_count = len(active_news)
    generated_at = datetime.now().strftime("%d %b %Y, %H:%M")

    html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Scout — AI Intelligence Feed</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Syne:wght@400;600;800&family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    :root {
      --bg: #0f1117;
      --surface: #1a1d27;
      --surface2: #22263a;
      --border: #2d2d2d;
      --text: #f1f5f9;
      --text-secondary: #94a3b8;
      --text-muted: #64748b;
      --accent: #6366f1;
      --accent-green: #00ff88;
      --radius: 8px;
    }

    body {
      background: var(--bg);
      color: var(--text);
      font-family: 'Inter', sans-serif;
      font-size: 15px;
      line-height: 1.6;
      min-height: 100vh;
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

    .brand { display: flex; align-items: center; gap: 0; }

    .brand-name {
      font-family: 'Syne', sans-serif;
      font-size: 48px;
      font-weight: 800;
      letter-spacing: -1px;
      line-height: 1;
      background: linear-gradient(135deg, #ffffff 0%, #00ff88 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }

    .brand-cursor {
      display: inline-block;
      width: 10px;
      height: 40px;
      background: #00ff88;
      margin-left: 4px;
      vertical-align: middle;
      animation: blink 1s infinite;
      border-radius: 2px;
    }

    .brand-dot {
      width: 8px;
      height: 8px;
      background: var(--accent-green);
      border-radius: 50%;
      animation: blink 2s infinite;
    }

    @keyframes blink { 0%,100%{opacity:1} 50%{opacity:0.3} }

    .header-stats { display: flex; gap: 32px; }

    .stat { text-align: right; }

    .stat-num {
      font-family: 'JetBrains Mono', monospace;
      font-size: 24px;
      font-weight: 600;
      color: var(--accent-green);
      line-height: 1;
    }

    .stat-label {
      font-size: 11px;
      color: var(--text-muted);
      text-transform: uppercase;
      letter-spacing: 0.5px;
      margin-top: 2px;
    }

    .updated {
      font-family: 'JetBrains Mono', monospace;
      font-size: 11px;
      color: var(--text-muted);
      margin-bottom: 20px;
    }

    /* Tabs */
    .tabs { display: flex; gap: 0; }

    .tab {
      padding: 10px 20px;
      font-size: 13px;
      font-weight: 500;
      color: var(--text-muted);
      cursor: pointer;
      border: none;
      background: none;
      border-bottom: 2px solid transparent;
      transition: all 0.15s;
      white-space: nowrap;
    }

    .tab:hover { color: var(--text); }
    .tab.active { color: var(--text); border-bottom-color: var(--accent); }

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
      grid-template-columns: repeat(auto-fill, minmax(540px, 1fr));
      gap: 20px;
    }

    .card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 24px;
      cursor: pointer;
      transition: border-color 0.15s, box-shadow 0.15s;
      position: relative;
    }

    .status-badge {
      font-size: 10px;
      font-weight: 700;
      padding: 4px 10px;
      border-radius: 6px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }

    .card:hover {
      border-color: var(--accent);
      box-shadow: 0 0 0 1px var(--accent)22;
    }

    .card-header {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 14px;
    }

    .card-tags { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; flex-grow: 1; }

    .tag-pill {
      font-size: 11px;
      font-weight: 600;
      padding: 3px 10px;
      border-radius: 4px;
      letter-spacing: 0.3px;
    }

    .tier-label {
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }

    .source-name {
      font-size: 12px;
      font-weight: 600;
      color: var(--text-secondary);
    }

    .built-badge {
      font-size: 11px;
      color: var(--accent-green);
      background: #22c55e1a;
      padding: 3px 8px;
      border-radius: 4px;
    }

    /* Score ring */
    .score-ring {
      width: 48px;
      height: 48px;
      border-radius: 50%;
      background: conic-gradient(var(--color) calc(var(--pct) * 1%), var(--surface2) 0);
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
      position: absolute;
      top: 24px;
      right: 24px;
    }

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
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
      font-weight: 700;
      color: var(--text);
      z-index: 1;
    }

    /* News score */
    .news-score-area {
      display: flex;
      align-items: center;
      gap: 6px;
      flex-shrink: 0;
      position: absolute;
      top: 24px;
      right: 24px;
    }

    .rel-dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      flex-shrink: 0;
    }

    .score-num {
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
      color: var(--text-secondary);
      white-space: nowrap;
      font-weight: 700;
    }

    /* Card content */
    .card-title {
      font-family: 'Georgia', serif;
      font-size: 1.8rem;
      font-weight: 700;
      line-height: 1.3;
      color: #ffffff;
      margin-bottom: 12px;
      max-width: calc(100% - 60px);
    }

    .card-meta {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
      margin-bottom: 18px;
    }

    .authors { font-size: 13px; color: #00ff88; font-weight: 600; }
    .institution { font-size: 13px; color: var(--text-secondary); }
    .pub-date { font-family: 'JetBrains Mono', monospace; font-size: 12px; color: var(--text-muted); white-space: nowrap; }
    .sep { color: var(--border); }

    .card-summary {
      font-size: 15px;
      line-height: 1.6;
      color: var(--text-secondary);
      margin-bottom: 20px;
    }

    .insight-block {
      background: var(--surface2);
      border-radius: 8px;
      padding: 16px;
      margin-bottom: 12px;
      border-left: 4px solid var(--accent);
    }

    .insight-block.monetisation {
      border-left-color: #00ff88;
      background: rgba(0, 255, 136, 0.05);
    }

    .insight-label {
      font-family: 'JetBrains Mono', monospace;
      font-size: 10px;
      font-weight: 700;
      color: var(--accent);
      letter-spacing: 1.5px;
      margin-bottom: 8px;
    }

    .monetisation .insight-label { color: #00ff88; }

    .insight-text {
      font-size: 13px;
      line-height: 1.6;
      color: var(--text);
    }

    /* Card footer */
    .card-footer {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-top: 16px;
      padding-top: 14px;
      border-top: 1px solid var(--border);
      gap: 12px;
      flex-wrap: wrap;
    }

    .score-pills { display: flex; gap: 6px; flex-wrap: wrap; }

    .score-pills span {
      font-family: 'JetBrains Mono', monospace;
      font-size: 10px;
      color: var(--text-muted);
      background: var(--bg);
      padding: 3px 8px;
      border-radius: 6px;
      border: 1px solid var(--border);
    }

    /* Footer links — two distinct CTAs */
    .footer-links {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-shrink: 0;
    }

    .read-link {
      font-size: 13px;
      font-weight: 500;
      color: var(--text-muted);
      text-decoration: none;
      white-space: nowrap;
      transition: color 0.15s;
    }

    .read-link:hover { color: var(--text-secondary); }

    .breakdown-link {
      font-size: 13px;
      font-weight: 600;
      color: var(--accent-green);
      text-decoration: none;
      white-space: nowrap;
      background: #22c55e1a;
      border: 1px solid #22c55e33;
      padding: 4px 12px;
      border-radius: 6px;
      transition: all 0.15s;
    }

    .breakdown-link:hover {
      background: #22c55e2a;
      border-color: #22c55e55;
    }

    .breakdown-pending {
      font-size: 11px;
      color: var(--text-muted);
      font-family: 'JetBrains Mono', monospace;
    }

    /* Archive */
    .archive-list { display: flex; flex-direction: column; gap: 8px; }

    .archive-row {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 14px 20px;
      display: grid;
      grid-template-columns: 110px 80px 1fr;
      gap: 20px;
      align-items: start;
    }

    .archive-date {
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
      color: var(--accent-green);
    }

    .archive-count {
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
      color: var(--text-muted);
    }

    .archive-previews { display: flex; flex-direction: column; gap: 4px; }

    .archive-title {
      font-size: 12px;
      color: var(--text-secondary);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

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

    footer span {
      font-family: 'JetBrains Mono', monospace;
      font-size: 11px;
      color: var(--text-muted);
    }

    .agent-trace { margin-bottom: 12px; }

    .trace-toggle {
      border: 1px solid var(--border);
      color: var(--text-muted);
      font-family: 'JetBrains Mono', monospace;
      font-size: 11px;
      padding: 6px 12px;
      border-radius: 6px;
      cursor: pointer;
      transition: all 0.15s;
      list-style: none;
    }

    .trace-toggle::-webkit-details-marker { display: none; }

    .trace-toggle:hover { color: var(--text); border-color: var(--accent); }

    .trace-body { padding: 12px 0 4px; }

    .agent-trace[open] .trace-toggle { color: var(--accent); border-color: var(--accent); }

    .trace-row {
      display: flex;
      justify-content: space-between;
      padding: 4px 0;
      border-bottom: 1px solid var(--border);
      margin-bottom: 4px;
    }

    .trace-label {
      font-family: 'JetBrains Mono', monospace;
      font-size: 10px;
      color: var(--text-muted);
      letter-spacing: 1px;
    }

    .trace-val {
      font-family: 'JetBrains Mono', monospace;
      font-size: 11px;
      color: var(--accent-green);
      font-weight: 600;
    }

    .trace-reasoning {
      font-size: 12px;
      line-height: 1.6;
      color: var(--text-secondary);
      margin-top: 10px;
      font-style: italic;
    }

    /* Score bars */
    .score-bars {
      display: flex;
      flex-direction: column;
      gap: 8px;
      min-width: 180px;
    }

    .score-bar-row {
      display: flex;
      align-items: center;
      gap: 10px;
    }

    .bar-label {
      font-family: 'JetBrains Mono', monospace;
      font-size: 10px;
      color: var(--text-muted);
      width: 80px;
      text-transform: uppercase;
    }

    .bar-track {
      flex-grow: 1;
      height: 6px;
      background: #333;
      border-radius: 3px;
      overflow: hidden;
    }

    .bar-fill {
      height: 100%;
      background: #00ff88;
      border-radius: 3px;
    }

    .bar-val {
      font-family: 'JetBrains Mono', monospace;
      font-size: 10px;
      color: var(--text-secondary);
      width: 20px;
      text-align: right;
    }

    .md-export-btn {
      font-size: 11px;
      font-weight: 600;
      font-family: 'JetBrains Mono', monospace;
      color: var(--text-muted);
      background: none;
      border: 1px solid var(--border);
      padding: 4px 10px;
      border-radius: 6px;
      cursor: pointer;
      transition: all 0.15s;
      white-space: nowrap;
    }

    .md-export-btn:hover { color: var(--text); border-color: var(--accent); }
    .md-export-btn.copied { color: var(--accent-green); border-color: var(--accent-green); }

    .email-banner {
      background: var(--surface);
      border-bottom: 1px solid var(--border);
      padding: 6px 32px;
    }

    .email-inner {
      max-width: 1200px;
      margin: 0 auto;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 12px;
    }

    .email-title {
      font-size: 0.85rem;
      font-weight: 600;
      color: var(--text-secondary);
      white-space: nowrap;
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
      padding: 3px 10px;
      font-size: 11px;
      color: var(--text);
      width: 160px;
      outline: none;
      transition: border-color 0.15s;
    }

    .email-input:focus { border-color: var(--accent); }

    .email-btn {
      background: var(--accent);
      color: white;
      border: none;
      border-radius: 4px;
      padding: 3px 12px;
      font-size: 11px;
      font-weight: 600;
      cursor: pointer;
      transition: opacity 0.15s;
      white-space: nowrap;
    }

    .email-btn:hover { opacity: 0.85; }

    .email-confirm {
      font-size: 12px;
      color: var(--accent-green);
      font-family: 'JetBrains Mono', monospace;
    }

    @media (max-width: 700px) {
      .email-banner { padding: 16px; }
      .email-form { width: 100%; }
      .email-input { flex: 1; width: auto; }
    }

    @media (max-width: 700px) {
      .cards-grid { grid-template-columns: 1fr; }
      .header-inner { padding: 0 16px; }
      .content { padding: 24px 16px 48px; }
      .archive-row { grid-template-columns: 1fr; gap: 6px; }
      .header-top { flex-direction: column; align-items: flex-start; }
      .header-stats { flex-direction: row; }
      .card-footer { flex-direction: column; align-items: flex-start; }
    }
  </style>
</head>
<body>

<div class="header">
  <div class="header-inner">
    <div class="header-top">
      <div class="brand">
        <span class="brand-name">Scout Daily</span><span class="brand-cursor"></span>
      </div>
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
    <div class="updated">Updated """ + generated_at + """</div>
    <div class="tabs">
      <button class="tab active" onclick="showTab('case-studies',this)">Case Studies</button>
      <button class="tab" onclick="showTab('industry-news',this)">Industry News</button>
      <button class="tab" onclick="showTab('archive-cs',this)">Archive: Studies</button>
      <button class="tab" onclick="showTab('archive-news',this)">Archive: News</button>
    </div>
  </div>
</div>

""" + generate_email_capture() + """

<div class="content">
  <div id="case-studies" class="panel active">
    <div class="cards-grid">""" + render_case_studies(cs_data, slug_map) + """</div>
  </div>

  <div id="industry-news" class="panel">
    <div class="cards-grid">""" + render_news(news_data, slug_map) + """</div>
  </div>

  <div id="archive-cs" class="panel">
    <div class="archive-list">""" + render_archive(cs_archive, 'cs') + """</div>
  </div>

  <div id="archive-news" class="panel">
    <div class="archive-list">""" + render_archive(news_archive, 'news') + """</div>
  </div>
</div>

<footer>
  <span>Scout · LangGraph + Groq + arXiv</span>
  <span>Case Studies: last 4 months · News: last 4–8 weeks</span>
</footer>

<script src="js/subscribe.js"></script>
<script>
  function showTab(id, btn) {
    document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.getElementById(id).classList.add('active');
    btn.classList.add('active');
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
      "**Score:** " + score + "/100",
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
      btn.textContent = "✓ Copied";
      setTimeout(() => {
        btn.classList.remove("copied");
        btn.textContent = "⬇ MD";
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
    import shutil
    if os.path.exists("case_studies/archive.json"):
        shutil.copy("case_studies/archive.json", "site/case_studies_archive.json")
        print("Copied -> site/case_studies_archive.json")
    if os.path.exists("logs/archive.json"):
        shutil.copy("logs/archive.json", "site/news_archive.json")
        print("Copied -> site/news_archive.json")
    print("HTML generated -> " + OUTPUT_FILE)

if __name__ == "__main__":
    generate_html()
