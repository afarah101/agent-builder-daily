import json
from pathlib import Path
from datetime import datetime
from flask import Flask, render_template_string, abort

app = Flask(__name__)
ISSUES_DIR = Path("issues")


def load_all_issues():
    """Load all issues, newest first."""
    if not ISSUES_DIR.exists():
        return []

    issues = []
    for file in ISSUES_DIR.glob("*.json"):
        with open(file, "r", encoding="utf-8") as f:
            issues.append(json.load(f))

    issues.sort(key=lambda i: i["issue_date"], reverse=True)
    return issues


def load_issue(date_str):
    """Load one specific issue by YYYY-MM-DD."""
    filepath = ISSUES_DIR / f"{date_str}.json"
    if not filepath.exists():
        return None
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def format_date(date_str):
    """Convert YYYY-MM-DD to 'Thursday, 16 April 2026'."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return dt.strftime("%A, %d %B %Y")


# ============================================================
# HTML TEMPLATES
# ============================================================

BASE_STYLE = """
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: Georgia, 'Times New Roman', serif;
    background: #f8f5ef;
    color: #1a1a1a;
    line-height: 1.6;
    padding: 40px 20px;
  }
  .container { max-width: 960px; margin: 0 auto; }
  .masthead {
    text-align: center;
    border-bottom: 3px double #1a1a1a;
    padding-bottom: 20px;
    margin-bottom: 30px;
  }
  .masthead h1 {
    font-size: 48px;
    font-weight: 900;
    letter-spacing: -1px;
    font-family: 'Playfair Display', Georgia, serif;
  }
  .masthead .date {
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 2px;
    color: #666;
    margin-top: 8px;
  }
  .nav {
    display: flex;
    justify-content: center;
    gap: 24px;
    margin-bottom: 40px;
    font-size: 13px;
    text-transform: uppercase;
    letter-spacing: 1px;
  }
  .nav a { color: #444; text-decoration: none; }
  .nav a:hover { color: #000; text-decoration: underline; }

  .hero {
    background: #fff;
    padding: 30px;
    border: 1px solid #ddd;
    margin-bottom: 30px;
  }
  .hero .badge {
    display: inline-block;
    background: #c0392b;
    color: white;
    font-size: 11px;
    padding: 4px 10px;
    text-transform: uppercase;
    letter-spacing: 1px;
    margin-bottom: 12px;
  }
  .hero h2 {
    font-size: 36px;
    line-height: 1.2;
    font-weight: 900;
    margin-bottom: 12px;
  }
  .hero .meta {
    font-size: 12px;
    color: #888;
    text-transform: uppercase;
    letter-spacing: 1px;
    margin-bottom: 20px;
  }
  .hero .section { margin-bottom: 16px; }
  .hero .section strong {
    display: block;
    font-size: 11px;
    color: #c0392b;
    text-transform: uppercase;
    letter-spacing: 1px;
    margin-bottom: 4px;
  }
  .hero .section p { font-size: 16px; }
  .hero a { color: #c0392b; font-size: 13px; }

  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 20px;
  }
  .card {
    background: #fff;
    padding: 20px;
    border: 1px solid #ddd;
  }
  .card .category {
    font-size: 11px;
    color: #888;
    text-transform: uppercase;
    letter-spacing: 1px;
    margin-bottom: 8px;
  }
  .card h3 {
    font-size: 20px;
    line-height: 1.25;
    margin-bottom: 12px;
  }
  .card .section { margin-bottom: 10px; font-size: 14px; }
  .card .section strong { color: #444; font-size: 11px; text-transform: uppercase; }
  .card .meta {
    font-size: 11px;
    color: #888;
    margin-top: 12px;
    padding-top: 12px;
    border-top: 1px solid #eee;
  }
  .card a { color: #c0392b; font-size: 12px; }

  .archive-list { list-style: none; }
  .archive-list li {
    padding: 16px 0;
    border-bottom: 1px solid #ddd;
  }
  .archive-list a {
    color: #1a1a1a;
    text-decoration: none;
    font-size: 18px;
  }
  .archive-list a:hover { text-decoration: underline; }
  .archive-list .count {
    font-size: 13px;
    color: #888;
    margin-left: 12px;
  }

  .empty {
    text-align: center;
    padding: 60px 20px;
    color: #888;
  }
</style>
"""

FRONT_PAGE = BASE_STYLE + """
<div class="container">
  <div class="masthead">
    <h1>The Agent Builder Daily</h1>
    <div class="date">{{ formatted_date }} · Issue {{ issue_number }}</div>
  </div>

  <div class="nav">
    <a href="/">Today</a>
    <a href="/archive">Archive</a>
  </div>

  {% if not issue %}
    <div class="empty">
      <h2>No issues yet</h2>
      <p>Run <code>python scout.py</code> to generate today's issue.</p>
    </div>
  {% else %}
    {% set top = issue.articles[0] %}
    <div class="hero">
      <span class="badge">Top Story · {{ top.category }} · {{ top.importance }}/10</span>
      <h2>{{ top.headline }}</h2>
      <div class="meta">{{ top.opportunity_type }} opportunity</div>
      <div class="section"><strong>Problem</strong><p>{{ top.problem }}</p></div>
      <div class="section"><strong>Solution</strong><p>{{ top.solution }}</p></div>
      <div class="section"><strong>Monetisation</strong><p>{{ top.monetisation }}</p></div>
      <div class="section"><strong>Trend</strong><p>{{ top.trend_signal }}</p></div>
      <a href="{{ top.source_url }}" target="_blank">Read source →</a>
    </div>

    <div class="grid">
      {% for a in issue.articles[1:] %}
      <div class="card">
        <div class="category">{{ a.category }} · {{ a.importance }}/10</div>
        <h3>{{ a.headline }}</h3>
        <div class="section"><strong>Problem</strong><p>{{ a.problem }}</p></div>
        <div class="section"><strong>Opportunity</strong><p>{{ a.monetisation }}</p></div>
        <div class="meta">
          {{ a.opportunity_type }} · <a href="{{ a.source_url }}" target="_blank">Source →</a>
        </div>
      </div>
      {% endfor %}
    </div>
  {% endif %}
</div>
"""

ARCHIVE_PAGE = BASE_STYLE + """
<div class="container">
  <div class="masthead">
    <h1>The Agent Builder Daily</h1>
    <div class="date">Archive · {{ issues|length }} issues</div>
  </div>

  <div class="nav">
    <a href="/">Today</a>
    <a href="/archive">Archive</a>
  </div>

  {% if not issues %}
    <div class="empty"><h2>No issues yet</h2></div>
  {% else %}
    <ul class="archive-list">
      {% for i in issues %}
      <li>
        <a href="/issue/{{ i.issue_date }}">{{ format_date(i.issue_date) }}</a>
        <span class="count">{{ i.article_count }} articles</span>
      </li>
      {% endfor %}
    </ul>
  {% endif %}
</div>
"""


# ============================================================
# ROUTES
# ============================================================

@app.route("/")
def home():
    issues = load_all_issues()
    latest = issues[0] if issues else None
    return render_template_string(
        FRONT_PAGE,
        issue=latest,
        formatted_date=format_date(latest["issue_date"]) if latest else "",
        issue_number=len(issues)
    )


@app.route("/archive")
def archive():
    issues = load_all_issues()
    return render_template_string(
        ARCHIVE_PAGE,
        issues=issues,
        format_date=format_date
    )


@app.route("/issue/<date_str>")
def issue_by_date(date_str):
    issue = load_issue(date_str)
    if not issue:
        abort(404)
    all_issues = load_all_issues()
    return render_template_string(
        FRONT_PAGE,
        issue=issue,
        formatted_date=format_date(issue["issue_date"]),
        issue_number=len(all_issues)
    )


if __name__ == "__main__":
    print("📰 Starting The Agent Builder Daily at http://localhost:5000")
    app.run(debug=True, port=5000)
