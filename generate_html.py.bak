import json
import shutil
from pathlib import Path
from datetime import datetime
from jinja2 import Template

ISSUES_DIR = Path("issues")
NEWS_DIR = Path("news")
SITE_DIR = Path("site")


# ============================================================
# HELPERS
# ============================================================

def format_date(date_str):
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return dt.strftime("%A, %d %B %Y")


def load_all(folder):
    """Load all JSON issues from a folder, newest first."""
    if not folder.exists():
        return []
    items = []
    for file in folder.glob("*.json"):
        with open(file, "r", encoding="utf-8") as f:
            items.append(json.load(f))
    items.sort(key=lambda i: i["issue_date"], reverse=True)
    return items


# ============================================================
# CSS (shared across all pages)
# ============================================================

CSS = """
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
.nav a.active { color: #c0392b; font-weight: bold; }

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
  margin-bottom: 30px;
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

.footer {
  text-align: center;
  margin-top: 60px;
  padding-top: 20px;
  border-top: 1px solid #ddd;
  font-size: 12px;
  color: #888;
}
"""


# ============================================================
# TEMPLATES
# ============================================================

BASE_TEMPLATE = Template("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ page_title }} · The Agent Builder Daily</title>
<style>{{ css }}</style>
</head>
<body>
<div class="container">
  <div class="masthead">
    <h1>The Agent Builder Daily</h1>
    <div class="date">{{ subtitle }}</div>
  </div>
  <div class="nav">
    <a href="/" class="{% if page == 'home' %}active{% endif %}">Pain Points</a>
    <a href="/news.html" class="{% if page == 'news' %}active{% endif %}">Industry News</a>
    <a href="/archive.html" class="{% if page == 'archive' %}active{% endif %}">Archive</a>
  </div>
  {{ body }}
  <div class="footer">
    Generated {{ generated_at }} · Data from Reddit, HackerNews, and official AI blogs
  </div>
</div>
</body>
</html>""")


PAIN_POINT_BODY = Template("""
{% if not issue %}
  <div class="empty"><h2>No issues yet</h2></div>
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
      <div class="meta">{{ a.opportunity_type }} · <a href="{{ a.source_url }}" target="_blank">Source →</a></div>
    </div>
    {% endfor %}
  </div>
{% endif %}
""")


NEWS_BODY = Template("""
{% if not issue %}
  <div class="empty"><h2>No news yet</h2></div>
{% else %}
  {% set top = issue.articles[0] %}
  <div class="hero">
    <span class="badge">Top News · {{ top.category }} · {{ top.importance }}/10</span>
    <h2>{{ top.headline }}</h2>
    <div class="meta">{{ top.news_type }} · {{ top.source }}</div>
    <div class="section"><strong>Summary</strong><p>{{ top.summary }}</p></div>
    <div class="section"><strong>Impact on agent builders</strong><p>{{ top.impact }}</p></div>
    <a href="{{ top.source_url }}" target="_blank">Read source →</a>
  </div>
  <div class="grid">
    {% for a in issue.articles[1:] %}
    <div class="card">
      <div class="category">{{ a.category }} · {{ a.news_type }} · {{ a.importance }}/10</div>
      <h3>{{ a.headline }}</h3>
      <div class="section"><strong>Summary</strong><p>{{ a.summary }}</p></div>
      <div class="section"><strong>Impact</strong><p>{{ a.impact }}</p></div>
      <div class="meta">{{ a.source }} · <a href="{{ a.source_url }}" target="_blank">Source →</a></div>
    </div>
    {% endfor %}
  </div>
{% endif %}
""")


ARCHIVE_BODY = Template("""
<h2 style="margin-bottom: 20px; font-size: 24px;">Pain Point Issues</h2>
{% if not issues %}
  <div class="empty"><p>No issues yet</p></div>
{% else %}
  <ul class="archive-list">
    {% for i in issues %}
    <li>
      <a href="/issues/{{ i.issue_date }}.html">{{ format_date(i.issue_date) }}</a>
      <span class="count">{{ i.article_count }} articles</span>
    </li>
    {% endfor %}
  </ul>
{% endif %}

<h2 style="margin: 40px 0 20px; font-size: 24px;">Industry News Issues</h2>
{% if not news %}
  <div class="empty"><p>No news yet</p></div>
{% else %}
  <ul class="archive-list">
    {% for i in news %}
    <li>
      <a href="/news/{{ i.issue_date }}.html">{{ format_date(i.issue_date) }}</a>
      <span class="count">{{ i.article_count }} stories</span>
    </li>
    {% endfor %}
  </ul>
{% endif %}
""")


# ============================================================
# PAGE GENERATORS
# ============================================================

def render_page(page_key, page_title, subtitle, body_html):
    return BASE_TEMPLATE.render(
        page_title=page_title,
        page=page_key,
        subtitle=subtitle,
        body=body_html,
        css=CSS,
        generated_at=datetime.now().strftime("%d %b %Y at %H:%M")
    )


def generate_home(issues):
    """The / (pain points front page)."""
    latest = issues[0] if issues else None
    subtitle = format_date(latest["issue_date"]) + " · Pain Points" if latest else "No issues yet"
    body = PAIN_POINT_BODY.render(issue=latest)
    return render_page("home", "Pain Points", subtitle, body)


def generate_news(news_issues):
    """The /news page."""
    latest = news_issues[0] if news_issues else None
    subtitle = format_date(latest["issue_date"]) + " · Industry News" if latest else "No news yet"
    body = NEWS_BODY.render(issue=latest)
    return render_page("news", "Industry News", subtitle, body)


def generate_archive(issues, news_issues):
    """The /archive page."""
    body = ARCHIVE_BODY.render(issues=issues, news=news_issues, format_date=format_date)
    return render_page("archive", "Archive",
                       f"{len(issues)} pain-point issues · {len(news_issues)} news issues", body)


def generate_issue_page(issue):
    """Individual past issue page."""
    subtitle = format_date(issue["issue_date"]) + " · Pain Points"
    body = PAIN_POINT_BODY.render(issue=issue)
    return render_page("home", issue["issue_date"], subtitle, body)


def generate_news_page(issue):
    """Individual past news page."""
    subtitle = format_date(issue["issue_date"]) + " · Industry News"
    body = NEWS_BODY.render(issue=issue)
    return render_page("news", issue["issue_date"], subtitle, body)


# ============================================================
# MAIN BUILD
# ============================================================

def build():
    print("🏗️  Building static site...")

    # Clean and recreate site folder
    if SITE_DIR.exists():
        shutil.rmtree(SITE_DIR)
    SITE_DIR.mkdir()
    (SITE_DIR / "issues").mkdir()
    (SITE_DIR / "news").mkdir()

    # Load all data
    issues = load_all(ISSUES_DIR)
    news_issues = load_all(NEWS_DIR)

    print(f"  Loaded {len(issues)} pain-point issues")
    print(f"  Loaded {len(news_issues)} news issues")

    # Homepage
    (SITE_DIR / "index.html").write_text(generate_home(issues), encoding="utf-8")
    print("  ✓ index.html")

    # News page
    (SITE_DIR / "news.html").write_text(generate_news(news_issues), encoding="utf-8")
    print("  ✓ news.html")

    # Archive
    (SITE_DIR / "archive.html").write_text(generate_archive(issues, news_issues), encoding="utf-8")
    print("  ✓ archive.html")

    # Individual issue pages
    for issue in issues:
        filepath = SITE_DIR / "issues" / f"{issue['issue_date']}.html"
        filepath.write_text(generate_issue_page(issue), encoding="utf-8")
    print(f"  ✓ {len(issues)} pain-point issue pages")

    # Individual news pages
    for issue in news_issues:
        filepath = SITE_DIR / "news" / f"{issue['issue_date']}.html"
        filepath.write_text(generate_news_page(issue), encoding="utf-8")
    print(f"  ✓ {len(news_issues)} news pages")

    print(f"\n✅ Site built to {SITE_DIR}/")
    print(f"   Open: file://{SITE_DIR.resolve()}/index.html")


if __name__ == "__main__":
    build()
