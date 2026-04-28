#!/usr/bin/env python3
"""
Generate HTML dashboard — Case Studies + Industry News + Archive pages.
"""

import os
import json
from datetime import datetime
from pathlib import Path

CASE_STUDIES_DIR = "case_studies"
NEWS_DIR = "logs"
OUTPUT_FILE = "site/index.html"
ARCHIVE_CS_FILE = "case_studies/archive.json"
ARCHIVE_NEWS_FILE = "logs/archive.json"


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


def score_bar(score, max_score=75):
    pct = int((score / max_score) * 100)
    color = "#00ff88" if pct >= 70 else "#ffaa00" if pct >= 40 else "#ff4444"
    return f'<div class="score-bar"><div class="score-fill" style="width:{pct}%;background:{color}"></div><span class="score-label">{score}/{max_score}</span></div>'


def tag_badge(tag, color="#00ff88"):
    return f'<span class="tag-badge" style="background:{color}22;color:{color};border:1px solid {color}44">{tag}</span>'


def relevance_badge(signal):
    colors = {"high": "#00ff88", "medium": "#ffaa00", "low": "#ff4444"}
    color = colors.get(signal, "#6b7280")
    return f'<span class="rel-badge" style="background:{color}22;color:{color};border:1px solid {color}44">{signal.upper()}</span>'


def render_case_studies(data):
    if not data or not data.get("case_studies"):
        return "<p class='empty'>No case studies available yet. Run the scout to populate.</p>"
    cards = []
    for cs in data["case_studies"]:
        authors = ", ".join(cs.get("authors", [])) or "Unknown authors"
        pub = cs.get("pub_date", "")[:10]
        url = cs.get("url", "#")
        tag = cs.get("relevance_tag", "Other")
        tag_colors = {
            "AI Agents": "#7c3aed", "LLMs": "#0ea5e9", "Automation": "#10b981",
            "Safety": "#ef4444", "Computer Vision": "#f59e0b", "NLP": "#06b6d4",
            "MLOps": "#8b5cf6", "Other": "#6b7280",
        }
        tag_color = tag_colors.get(tag, "#6b7280")
        cards.append(f"""
        <article class="cs-card" onclick="window.open('{url}','_blank')">
          <div class="cs-header">
            <div class="cs-meta">
              {tag_badge(tag, tag_color)}
              <span class="pub-date">{pub}</span>
            </div>
            {score_bar(cs.get('total_score', 0))}
          </div>
          <h3 class="cs-title">{cs.get('title','Untitled')}</h3>
          <p class="cs-authors">{authors}</p>
          <p class="cs-institution">📍 {cs.get('institution','')}</p>
          <div class="cs-divider"></div>
          <div class="cs-section">
            <span class="section-label">SUMMARY</span>
            <p class="section-text">{cs.get('summary','')}</p>
          </div>
          <div class="cs-section highlight">
            <span class="section-label">HOW TO APPLY THIS</span>
            <p class="section-text">{cs.get('how_to_apply','')}</p>
          </div>
          <div class="score-breakdown">
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
        signal = item.get("relevance_signal", "medium")
        url = item.get("url", "#")
        tier_colors = {"ai_labs": "#00ff88", "consulting": "#0ea5e9", "news": "#f59e0b"}
        tier_color = tier_colors.get(item.get("tier", "news"), "#6b7280")
        cards.append(f"""
        <article class="news-card" onclick="window.open('{url}','_blank')">
          <div class="news-header">
            <span class="news-source" style="color:{tier_color}">{item.get('source','')}</span>
            {relevance_badge(signal)}
            <span class="news-score">{item.get('relevance_score',0)}/10</span>
            <span class="news-category">{item.get('category','')}</span>
          </div>
          <h4 class="news-title">{item.get('title','')}</h4>
          <p class="news-summary">{item.get('summary','')}</p>
          <div class="news-section highlight">
            <span class="section-label">HOW TO USE THIS</span>
            <p class="section-text">{item.get('enterprise_use','')}</p>
          </div>
          <div class="news-footer">
            <span class="news-date">{item.get('pub_date','')}</span>
            <span class="news-link">Read article →</span>
          </div>
        </article>""")
    return "\n".join(cards)


def render_archive_rows(archive, kind="cs"):
    if not archive:
        return "<p class='empty'>No archive yet.</p>"
    rows = []
    for entry in archive[:30]:
        date = entry.get("date", "")
        total = entry.get("total", 0)
        key = "case_studies" if kind == "cs" else "news"
        items = entry.get(key, [])[:3]
        previews = " · ".join([i.get("title","")[:50] for i in items])
        rows.append(f"""
        <div class="archive-row">
          <div class="archive-date">{date}</div>
          <div class="archive-count">{total} items</div>
          <div class="archive-preview">{previews}</div>
        </div>""")
    return "\n".join(rows)


def generate_html():
    cs_data = load_latest_json(CASE_STUDIES_DIR)
    news_data = load_latest_json(NEWS_DIR, prefix="news_")
    cs_archive = load_json(ARCHIVE_CS_FILE)
    news_archive = load_json(ARCHIVE_NEWS_FILE)

    cs_count = len(cs_data.get("case_studies", [])) if cs_data else 0
    news_count = len(news_data.get("news", [])) if news_data else 0
    generated_at = datetime.now().strftime("%d %b %Y, %H:%M")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AI Scout — Intelligence Feed</title>
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
    }}
    * {{ margin:0; padding:0; box-sizing:border-box; }}
    body {{ background:var(--bg); color:var(--text); font-family:'Syne',sans-serif; min-height:100vh; overflow-x:hidden; }}
    body::before {{ content:''; position:fixed; inset:0; background-image:linear-gradient(rgba(0,255,136,0.03) 1px,transparent 1px),linear-gradient(90deg,rgba(0,255,136,0.03) 1px,transparent 1px); background-size:40px 40px; pointer-events:none; z-index:0; }}
    .container {{ max-width:1280px; margin:0 auto; padding:0 24px; position:relative; z-index:1; }}

    header {{ padding:48px 0 0; border-bottom:1px solid var(--border); }}
    .header-inner {{ display:flex; align-items:flex-end; justify-content:space-between; gap:24px; flex-wrap:wrap; margin-bottom:24px; }}
    .logo-tag {{ font-family:'Space Mono',monospace; font-size:11px; color:var(--accent); letter-spacing:3px; text-transform:uppercase; margin-bottom:8px; }}
    h1 {{ font-size:clamp(36px,5vw,64px); font-weight:800; line-height:1; background:linear-gradient(135deg,#fff 0%,var(--accent) 100%); -webkit-background-clip:text; -webkit-text-fill-color:transparent; }}
    .header-stats {{ display:flex; gap:32px; }}
    .stat-value {{ font-family:'Space Mono',monospace; font-size:32px; font-weight:700; color:var(--accent); }}
    .stat-label {{ font-size:11px; color:var(--muted); text-transform:uppercase; letter-spacing:1px; }}
    .updated {{ font-family:'Space Mono',monospace; font-size:11px; color:var(--muted); margin-bottom:24px; }}
    .updated span {{ color:var(--accent); }}
    @keyframes pulse {{ 0%,100% {{ opacity:1; }} 50% {{ opacity:0; }} }}
    .cursor {{ display:inline-block; width:8px; height:20px; background:var(--accent); animation:pulse 1s infinite; vertical-align:middle; margin-left:4px; }}

    .tabs {{ display:flex; gap:0; border-bottom:1px solid var(--border); }}
    .tab {{ padding:12px 24px; font-family:'Space Mono',monospace; font-size:11px; letter-spacing:1px; text-transform:uppercase; color:var(--muted); cursor:pointer; border-bottom:2px solid transparent; transition:all 0.2s; background:none; border-top:none; border-left:none; border-right:none; }}
    .tab.active {{ color:var(--accent); border-bottom-color:var(--accent); }}
    .tab:hover:not(.active) {{ color:var(--text); }}

    .panel {{ display:none; padding:32px 0; }}
    .panel.active {{ display:block; }}

    /* Case Study Cards */
    .cs-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(500px,1fr)); gap:20px; }}
    .cs-card {{ background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:24px; cursor:pointer; transition:border-color 0.2s,transform 0.2s; }}
    .cs-card:hover {{ border-color:var(--accent); transform:translateY(-2px); }}
    .cs-header {{ display:flex; align-items:center; justify-content:space-between; margin-bottom:14px; gap:12px; flex-wrap:wrap; }}
    .cs-meta {{ display:flex; align-items:center; gap:8px; }}
    .pub-date {{ font-family:'Space Mono',monospace; font-size:11px; color:var(--muted); }}
    .score-bar {{ display:flex; align-items:center; gap:8px; flex:1; max-width:160px; }}
    .score-fill {{ height:4px; border-radius:2px; flex:1; }}
    .score-label {{ font-family:'Space Mono',monospace; font-size:10px; color:var(--muted); white-space:nowrap; }}
    .cs-title {{ font-size:16px; font-weight:600; line-height:1.4; margin-bottom:6px; color:#fff; }}
    .cs-authors {{ font-family:'Space Mono',monospace; font-size:11px; color:var(--accent2); margin-bottom:4px; }}
    .cs-institution {{ font-size:12px; color:var(--muted); margin-bottom:14px; }}
    .cs-divider {{ height:1px; background:var(--border); margin-bottom:14px; }}
    .cs-section {{ margin-bottom:12px; }}
    .cs-section.highlight {{ background:var(--surface2); border-left:2px solid var(--accent); border-radius:0 8px 8px 0; padding:12px; }}
    .section-label {{ font-family:'Space Mono',monospace; font-size:9px; color:var(--accent); letter-spacing:2px; display:block; margin-bottom:4px; }}
    .section-text {{ font-size:13px; line-height:1.6; color:#94a3b8; }}
    .cs-section.highlight .section-text {{ color:var(--text); }}
    .score-breakdown {{ display:flex; gap:8px; flex-wrap:wrap; margin-top:14px; }}
    .score-breakdown span {{ font-family:'Space Mono',monospace; font-size:10px; color:var(--muted); background:var(--surface2); padding:3px 8px; border-radius:4px; }}
    .tag-badge {{ font-family:'Space Mono',monospace; font-size:9px; padding:3px 8px; border-radius:4px; text-transform:uppercase; letter-spacing:1px; }}

    /* News Cards */
    .news-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(420px,1fr)); gap:16px; }}
    .news-card {{ background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:20px; cursor:pointer; transition:border-color 0.2s,transform 0.2s; }}
    .news-card:hover {{ border-color:var(--accent2); transform:translateY(-2px); }}
    .news-header {{ display:flex; align-items:center; gap:8px; margin-bottom:10px; flex-wrap:wrap; }}
    .news-source {{ font-family:'Space Mono',monospace; font-size:11px; font-weight:700; text-transform:uppercase; }}
    .news-score {{ font-family:'Space Mono',monospace; font-size:10px; color:var(--muted); margin-left:auto; }}
    .news-category {{ font-family:'Space Mono',monospace; font-size:9px; color:var(--muted); padding:2px 6px; border:1px solid var(--border); border-radius:3px; }}
    .rel-badge {{ font-family:'Space Mono',monospace; font-size:9px; padding:3px 8px; border-radius:4px; text-transform:uppercase; letter-spacing:1px; }}
    .news-title {{ font-size:15px; font-weight:600; line-height:1.4; margin-bottom:10px; color:#fff; }}
    .news-summary {{ font-size:13px; line-height:1.6; color:#94a3b8; margin-bottom:12px; }}
    .news-section {{ background:var(--surface2); border-left:2px solid var(--accent2); border-radius:0 8px 8px 0; padding:12px; margin-bottom:12px; }}
    .news-section .section-label {{ color:var(--accent2); }}
    .news-footer {{ display:flex; justify-content:space-between; align-items:center; }}
    .news-date {{ font-family:'Space Mono',monospace; font-size:10px; color:var(--muted); }}
    .news-link {{ font-family:'Space Mono',monospace; font-size:11px; color:var(--accent2); }}

    /* Archive */
    .archive-grid {{ display:flex; flex-direction:column; gap:8px; }}
    .archive-row {{ background:var(--surface); border:1px solid var(--border); border-radius:8px; padding:16px; display:grid; grid-template-columns:120px 80px 1fr; gap:16px; align-items:center; }}
    .archive-date {{ font-family:'Space Mono',monospace; font-size:12px; color:var(--accent); }}
    .archive-count {{ font-family:'Space Mono',monospace; font-size:11px; color:var(--muted); }}
    .archive-preview {{ font-size:12px; color:var(--muted); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}

    .empty {{ color:var(--muted); font-family:'Space Mono',monospace; font-size:13px; padding:48px; text-align:center; }}
    footer {{ border-top:1px solid var(--border); padding:24px 0; margin-top:48px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px; }}
    footer span {{ font-family:'Space Mono',monospace; font-size:11px; color:var(--muted); }}
    footer .accent {{ color:var(--accent); }}
    @media(max-width:600px) {{ .cs-grid,.news-grid {{ grid-template-columns:1fr; }} .header-inner {{ flex-direction:column; align-items:flex-start; }} .archive-row {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="header-inner">
        <div>
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
        <button class="tab active" onclick="showTab('case-studies',this)">Case Studies</button>
        <button class="tab" onclick="showTab('industry-news',this)">Industry News</button>
        <button class="tab" onclick="showTab('archive-cs',this)">Case Study Archive</button>
        <button class="tab" onclick="showTab('archive-news',this)">News Archive</button>
      </div>
    </header>

    <div id="case-studies" class="panel active">
      <div class="cs-grid">
        {render_case_studies(cs_data)}
      </div>
    </div>

    <div id="industry-news" class="panel">
      <div class="news-grid">
        {render_news(news_data)}
      </div>
    </div>

    <div id="archive-cs" class="panel">
      <div class="archive-grid">
        {render_archive_rows(cs_archive, 'cs')}
      </div>
    </div>

    <div id="archive-news" class="panel">
      <div class="archive-grid">
        {render_archive_rows(news_archive, 'news')}
      </div>
    </div>

    <footer>
      <span>Scout Agent · LangGraph + Groq</span>
      <span class="accent">// Case Studies: last 4 months · News: last 4 weeks</span>
    </footer>
  </div>
  <script>
    function showTab(id,btn) {{
      document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
      document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
      document.getElementById(id).classList.add('active');
      btn.classList.add('active');
    }}
  </script>
</body>
</html>"""

    os.makedirs("site", exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        f.write(html)
    print(f"HTML generated → {OUTPUT_FILE}")


if __name__ == "__main__":
    generate_html()