#!/usr/bin/env python3
"""
Generate HTML dashboard from case studies + industry news JSON files.
Dark tech/AI aesthetic. Sorted by score descending.
"""

import os
import json
from datetime import datetime
from pathlib import Path

CASE_STUDIES_DIR = "case_studies"
NEWS_DIR = "logs"
OUTPUT_FILE = "site/index.html"


def load_latest_json(directory):
    path = Path(directory)
    files = sorted(path.glob("*.json"), reverse=True)
    if not files:
        return None
    with open(files[0]) as f:
        return json.load(f)


def score_bar(score, max_score=100):
    pct = int((score / max_score) * 100)
    color = "#00ff88" if pct >= 70 else "#ffaa00" if pct >= 40 else "#ff4444"
    return f'<div class="score-bar"><div class="score-fill" style="width:{pct}%;background:{color}"></div><span class="score-label">{score}/{max_score}</span></div>'


def tier_badge(tier):
    colors = {
        "universities": "#7c3aed",
        "consulting": "#0ea5e9",
        "ai_labs": "#10b981",
        "other": "#6b7280",
    }
    labels = {
        "universities": "University",
        "consulting": "Consulting",
        "ai_labs": "AI Lab",
        "other": "Other",
    }
    color = colors.get(tier, "#6b7280")
    label = labels.get(tier, tier.title())
    return f'<span class="badge" style="background:{color}22;color:{color};border:1px solid {color}44">{label}</span>'


def suitability_badge(suit):
    colors = {"high": "#10b981", "medium": "#f59e0b", "low": "#ef4444"}
    color = colors.get(suit, "#6b7280")
    return f'<span class="badge" style="background:{color}22;color:{color};border:1px solid {color}44">{suit.upper()}</span>'


def render_case_studies(data):
    if not data or not data.get("case_studies"):
        return "<p class='empty'>No case studies available yet.</p>"

    cards = []
    for cs in data["case_studies"]:
        authors = ", ".join(cs.get("authors", [])) or "Unknown authors"
        pub = cs.get("pub_date", "")[:10] or "Unknown date"
        url = cs.get("url", "#")
        cards.append(f"""
        <article class="card" onclick="window.open('{url}','_blank')">
          <div class="card-header">
            <div class="card-meta">
              {tier_badge(cs.get('tier','other'))}
              <span class="pub-date">{pub}</span>
            </div>
            {score_bar(cs.get('total_score', 0))}
          </div>
          <h3 class="card-title">{cs.get('title','Untitled')}</h3>
          <p class="card-authors">{authors} · {cs.get('venue','')}</p>
          <p class="card-summary">{cs.get('summary','')}</p>
          <div class="card-insights">
            <div class="insight">
              <span class="insight-label">HOW TO USE</span>
              <span class="insight-text">{cs.get('how_to_use','')}</span>
            </div>
            <div class="insight">
              <span class="insight-label">OPPORTUNITY</span>
              <span class="insight-text">{cs.get('monetisation','')}</span>
            </div>
          </div>
          <div class="score-breakdown">
            <span>Prestige {cs.get('prestige_score',0)}</span>
            <span>Recency {cs.get('recency_score',0)}</span>
            <span>Impact {cs.get('real_world_impact',0)}</span>
            <span>Maturity {cs.get('ai_maturity',0)}</span>
          </div>
        </article>""")

    return "\n".join(cards)


def render_news(data):
    if not data or not data.get("news"):
        return "<p class='empty'>No news available yet.</p>"

    cards = []
    for item in data["news"]:
        suit = item.get("suitability", "medium")
        cards.append(f"""
        <article class="news-card">
          <div class="news-header">
            <span class="news-source">{item.get('source','')}</span>
            {suitability_badge(suit)}
            <span class="relevance-score">Score: {item.get('relevance_score',0)}/10</span>
          </div>
          <h4 class="news-title">{item.get('article_title','')}</h4>
          <span class="news-category">{item.get('category','')}</span>
        </article>""")

    return "\n".join(cards)


def generate_html():
    cs_data = load_latest_json(CASE_STUDIES_DIR)
    news_data = load_latest_json(NEWS_DIR)

    cs_count = len(cs_data.get("case_studies", [])) if cs_data else 0
    news_count = len(news_data.get("news", [])) if news_data else 0
    generated_at = datetime.now().strftime("%d %b %Y, %H:%M")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AI Intelligence Feed</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Syne:wght@400;600;800&display=swap" rel="stylesheet">
  <style>
    :root {{
      --bg: #080b12;
      --surface: #0d1117;
      --surface2: #161b25;
      --border: #1e2837;
      --accent: #00ff88;
      --accent2: #0ea5e9;
      --text: #e2e8f0;
      --muted: #64748b;
      --danger: #ff4444;
    }}

    * {{ margin: 0; padding: 0; box-sizing: border-box; }}

    body {{
      background: var(--bg);
      color: var(--text);
      font-family: 'Syne', sans-serif;
      min-height: 100vh;
      overflow-x: hidden;
    }}

    /* Grid background */
    body::before {{
      content: '';
      position: fixed;
      inset: 0;
      background-image: 
        linear-gradient(rgba(0,255,136,0.03) 1px, transparent 1px),
        linear-gradient(90deg, rgba(0,255,136,0.03) 1px, transparent 1px);
      background-size: 40px 40px;
      pointer-events: none;
      z-index: 0;
    }}

    .container {{
      max-width: 1200px;
      margin: 0 auto;
      padding: 0 24px;
      position: relative;
      z-index: 1;
    }}

    /* Header */
    header {{
      padding: 48px 0 32px;
      border-bottom: 1px solid var(--border);
    }}

    .header-inner {{
      display: flex;
      align-items: flex-end;
      justify-content: space-between;
      gap: 24px;
      flex-wrap: wrap;
    }}

    .logo-area {{}}

    .logo-tag {{
      font-family: 'Space Mono', monospace;
      font-size: 11px;
      color: var(--accent);
      letter-spacing: 3px;
      text-transform: uppercase;
      margin-bottom: 8px;
    }}

    h1 {{
      font-size: clamp(32px, 5vw, 56px);
      font-weight: 800;
      line-height: 1;
      background: linear-gradient(135deg, #fff 0%, var(--accent) 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }}

    .header-stats {{
      display: flex;
      gap: 24px;
      align-items: center;
    }}

    .stat {{
      text-align: right;
    }}

    .stat-value {{
      font-family: 'Space Mono', monospace;
      font-size: 28px;
      font-weight: 700;
      color: var(--accent);
    }}

    .stat-label {{
      font-size: 11px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 1px;
    }}

    .updated {{
      font-family: 'Space Mono', monospace;
      font-size: 11px;
      color: var(--muted);
      margin-top: 12px;
    }}

    .updated span {{
      color: var(--accent);
    }}

    /* Tabs */
    .tabs {{
      display: flex;
      gap: 0;
      margin: 32px 0 0;
      border-bottom: 1px solid var(--border);
    }}

    .tab {{
      padding: 12px 24px;
      font-family: 'Space Mono', monospace;
      font-size: 12px;
      letter-spacing: 1px;
      text-transform: uppercase;
      color: var(--muted);
      cursor: pointer;
      border-bottom: 2px solid transparent;
      transition: all 0.2s;
      background: none;
      border-top: none;
      border-left: none;
      border-right: none;
    }}

    .tab.active {{
      color: var(--accent);
      border-bottom-color: var(--accent);
    }}

    .tab:hover:not(.active) {{
      color: var(--text);
    }}

    /* Panels */
    .panel {{ display: none; padding: 32px 0; }}
    .panel.active {{ display: block; }}

    /* Case Study Cards */
    .cards-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(520px, 1fr));
      gap: 16px;
    }}

    .card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 24px;
      cursor: pointer;
      transition: border-color 0.2s, transform 0.2s;
    }}

    .card:hover {{
      border-color: var(--accent);
      transform: translateY(-2px);
    }}

    .card-header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 16px;
      gap: 12px;
      flex-wrap: wrap;
    }}

    .card-meta {{
      display: flex;
      align-items: center;
      gap: 8px;
    }}

    .pub-date {{
      font-family: 'Space Mono', monospace;
      font-size: 11px;
      color: var(--muted);
    }}

    .score-bar {{
      display: flex;
      align-items: center;
      gap: 8px;
      flex: 1;
      max-width: 160px;
    }}

    .score-fill {{
      height: 4px;
      border-radius: 2px;
      flex: 1;
      transition: width 0.5s ease;
    }}

    .score-label {{
      font-family: 'Space Mono', monospace;
      font-size: 10px;
      color: var(--muted);
      white-space: nowrap;
    }}

    .card-title {{
      font-size: 16px;
      font-weight: 600;
      line-height: 1.4;
      margin-bottom: 6px;
      color: #fff;
    }}

    .card-authors {{
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 12px;
      font-family: 'Space Mono', monospace;
    }}

    .card-summary {{
      font-size: 14px;
      line-height: 1.6;
      color: #94a3b8;
      margin-bottom: 16px;
    }}

    .card-insights {{
      display: flex;
      flex-direction: column;
      gap: 8px;
      margin-bottom: 16px;
      padding: 12px;
      background: var(--surface2);
      border-radius: 8px;
      border-left: 2px solid var(--accent);
    }}

    .insight {{
      display: flex;
      flex-direction: column;
      gap: 2px;
    }}

    .insight-label {{
      font-family: 'Space Mono', monospace;
      font-size: 9px;
      color: var(--accent);
      letter-spacing: 2px;
    }}

    .insight-text {{
      font-size: 13px;
      color: var(--text);
      line-height: 1.5;
    }}

    .score-breakdown {{
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
    }}

    .score-breakdown span {{
      font-family: 'Space Mono', monospace;
      font-size: 10px;
      color: var(--muted);
      background: var(--surface2);
      padding: 3px 8px;
      border-radius: 4px;
    }}

    /* News Cards */
    .news-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
      gap: 12px;
    }}

    .news-card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 18px;
      transition: border-color 0.2s;
    }}

    .news-card:hover {{ border-color: var(--accent2); }}

    .news-header {{
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 10px;
      flex-wrap: wrap;
    }}

    .news-source {{
      font-family: 'Space Mono', monospace;
      font-size: 10px;
      color: var(--accent2);
      text-transform: uppercase;
    }}

    .relevance-score {{
      font-family: 'Space Mono', monospace;
      font-size: 10px;
      color: var(--muted);
      margin-left: auto;
    }}

    .news-title {{
      font-size: 14px;
      font-weight: 600;
      line-height: 1.4;
      margin-bottom: 8px;
      color: #fff;
    }}

    .news-category {{
      font-family: 'Space Mono', monospace;
      font-size: 10px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 1px;
    }}

    /* Badges */
    .badge {{
      font-family: 'Space Mono', monospace;
      font-size: 9px;
      padding: 3px 8px;
      border-radius: 4px;
      text-transform: uppercase;
      letter-spacing: 1px;
      white-space: nowrap;
    }}

    .empty {{
      color: var(--muted);
      font-family: 'Space Mono', monospace;
      font-size: 13px;
      padding: 48px;
      text-align: center;
    }}

    /* Terminal pulse */
    @keyframes pulse {{
      0%, 100% {{ opacity: 1; }}
      50% {{ opacity: 0; }}
    }}

    .cursor {{
      display: inline-block;
      width: 8px;
      height: 16px;
      background: var(--accent);
      animation: pulse 1s infinite;
      vertical-align: middle;
      margin-left: 4px;
    }}

    footer {{
      border-top: 1px solid var(--border);
      padding: 24px 0;
      margin-top: 48px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 12px;
    }}

    footer span {{
      font-family: 'Space Mono', monospace;
      font-size: 11px;
      color: var(--muted);
    }}

    footer .accent {{ color: var(--accent); }}

    @media (max-width: 600px) {{
      .cards-grid, .news-grid {{ grid-template-columns: 1fr; }}
      .header-inner {{ flex-direction: column; align-items: flex-start; }}
      .header-stats {{ flex-direction: row; }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="header-inner">
        <div class="logo-area">
          <p class="logo-tag">// AI Intelligence Feed</p>
          <h1>Scout<span class="cursor"></span></h1>
        </div>
        <div class="header-stats">
          <div class="stat">
            <div class="stat-value">{cs_count}</div>
            <div class="stat-label">Case Studies</div>
          </div>
          <div class="stat">
            <div class="stat-value">{news_count}</div>
            <div class="stat-label">News Items</div>
          </div>
        </div>
      </div>
      <p class="updated">Last updated: <span>{generated_at}</span></p>
      <div class="tabs">
        <button class="tab active" onclick="showTab('case-studies', this)">Case Studies</button>
        <button class="tab" onclick="showTab('news', this)">Industry News</button>
      </div>
    </header>

    <div id="case-studies" class="panel active">
      <div class="cards-grid">
        {render_case_studies(cs_data)}
      </div>
    </div>

    <div id="news" class="panel">
      <div class="news-grid">
        {render_news(news_data)}
      </div>
    </div>

    <footer>
      <span>Scout Agent · LangGraph + Groq</span>
      <span class="accent">// Ranked by prestige · recency · impact · maturity</span>
    </footer>
  </div>

  <script>
    function showTab(id, btn) {{
      document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
      document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      document.getElementById(id).classList.add('active');
      btn.classList.add('active');
    }}
  </script>
</body>
</html>"""

    os.makedirs("site", exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        f.write(html)
    print(f"✓ HTML generated → {OUTPUT_FILE}")


if __name__ == "__main__":
    generate_html()