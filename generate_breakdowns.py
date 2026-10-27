#!/usr/bin/env python3
from dotenv import load_dotenv
load_dotenv()
import os
import json
import re
import sys
import time
from html import escape, unescape
from pathlib import Path
from datetime import datetime
from groq import Groq

CASE_STUDIES_DIR = "case_studies"
NEWS_DIR = "data/news"
OUTPUT_DIR = "site/breakdowns"

# Dual API key setup — falls back to key 2 if key 1 hits daily limit
API_KEY_1 = os.environ.get("GROQ_API_KEY")
API_KEY_2 = os.environ.get("GROQ_API_KEY_2")
_current_key = API_KEY_1
_key1_exhausted = False

def get_client():
    return Groq(api_key=_current_key)


def load_latest_json(directory, prefix=""):
    path = Path(directory)
    pattern = f"{prefix}*.json" if prefix else "*.json"
    files = [f for f in sorted(path.glob(pattern), reverse=True) if "archive" not in f.name]
    if not files:
        return None
    with open(files[0]) as f:
        return json.load(f)


def slugify(title):
    slug = title.lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug.strip())
    slug = re.sub(r"-+", "-", slug)
    return slug[:80]


def call_groq(prompt, retries=3):
    global _current_key, _key1_exhausted

    for attempt in range(retries):
        try:
            client = get_client()
            response = client.chat.completions.create(
                model="openai/gpt-oss-20b",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1200,
                reasoning_effort="low",
                reasoning_format="hidden",
                temperature=0.4,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            err = str(e)
            print(f"  Groq error (attempt {attempt+1}): {e}")
            if "tokens per day" in err:
                if not _key1_exhausted and API_KEY_2:
                    print("  Key 1 daily limit hit — switching to Key 2.")
                    _current_key = API_KEY_2
                    _key1_exhausted = True
                    continue  # Retry immediately with key 2
                else:
                    print("  Both API keys exhausted — stopping.")
                    return "DAILY_LIMIT"
            if attempt < retries - 1:
                time.sleep(2)
    return None


def generate_content_for_case_study(cs):
    title = cs.get("title", "Untitled")
    summary = cs.get("summary", "")
    how_to = cs.get("how_to_apply", "")
    opportunity = cs.get("monetisation", "")
    authors = ", ".join(cs.get("authors", [])) or "Unknown"
    institution = cs.get("institution", "")

    prompt = f"""You are writing for an enterprise IT engineer audience. Be direct, technical, and actionable. No marketing language.

Case Study Title: {title}
Authors: {authors} | Institution: {institution}
Summary: {summary}
How to Apply: {how_to}
Business Opportunity: {opportunity}

Write two sections. Use plain text with no markdown formatting, no asterisks, no hash symbols. Use numbered lists where appropriate.

SECTION_SUMMARY:
Write a 300-350 word detailed summary of this case study. Cover: what was studied, what was found, why it matters for enterprise IT, and what the real-world implications are. Be specific.

SECTION_IT_GUIDE:
Write a practical IT engineer application guide with these 6 parts:
1. EVALUATE - What to audit or assess in your current environment before acting
2. PROPOSE - How to build a business case for leadership (include metrics or benchmarks if available)
3. TOOLS TO CONSIDER - Specific vendor names or platforms relevant to this
4. RISKS TO FLAG - Technical, compliance (UK GDPR where relevant), or operational risks
5. QUICK WIN - Something achievable in under 30 days
6. LONG-TERM PLAY - The 6-12 month strategic move

Separate the two sections with exactly this delimiter: ---SPLIT---"""

    return call_groq(prompt)


def generate_content_for_news(item):
    title = item.get("title", "Untitled")
    summary = item.get("summary", "")
    enterprise_use = item.get("enterprise_use", "")
    source = item.get("source", "")
    category = item.get("category", "")

    prompt = f"""You are writing for an enterprise IT engineer audience. Be direct, technical, and actionable. No marketing language.

News Title: {title}
Source: {source} | Category: {category}
Summary: {summary}
Enterprise Use: {enterprise_use}

Write two sections. Use plain text with no markdown formatting, no asterisks, no hash symbols. Use numbered lists where appropriate.

SECTION_SUMMARY:
Write a 300-350 word detailed summary of this news item. Cover: what happened, why it matters for enterprise IT teams, what vendors or technologies are involved, and what the broader industry implications are.

SECTION_IT_GUIDE:
Write a practical IT engineer application guide with these 6 parts:
1. EVALUATE - What to audit or assess in your current environment before acting
2. PROPOSE - How to build a business case for leadership (include metrics or benchmarks if available)
3. TOOLS TO CONSIDER - Specific vendor names or platforms relevant to this
4. RISKS TO FLAG - Technical, compliance (UK GDPR where relevant), or operational risks
5. QUICK WIN - Something achievable in under 30 days
6. LONG-TERM PLAY - The 6-12 month strategic move

Separate the two sections with exactly this delimiter: ---SPLIT---"""

    return call_groq(prompt)


def parse_groq_response(raw):
    if not raw or "---SPLIT---" not in raw:
        return None, None

    parts = raw.split("---SPLIT---", 1)
    summary_raw = parts[0].strip()
    guide_raw = parts[1].strip() if len(parts) > 1 else ""

    for prefix in ["SECTION_SUMMARY:", "SECTION_IT_GUIDE:"]:
        summary_raw = summary_raw.replace(prefix, "").strip()
        guide_raw = guide_raw.replace(prefix, "").strip()

    summary_paras = [p.strip() for p in summary_raw.split("\n\n") if p.strip()]
    summary_html = summary_block_html(summary_paras)

    guide_labels = [
        ("1.", "EVALUATE", ""),
        ("2.", "PROPOSE", ""),
        ("3.", "TOOLS TO CONSIDER", "green"),
        ("4.", "RISKS TO FLAG", "red"),
        ("5.", "QUICK WIN", "green"),
        ("6.", "LONG-TERM PLAY", "amber"),
    ]

    blocks = []
    lines = guide_raw.split("\n")
    current_label = None
    current_color = ""
    current_lines = []

    def flush_block():
        if current_label and current_lines:
            blocks.append(guide_block_html(current_label, current_color, "\n".join(current_lines).strip()))

    for line in lines:
        matched = False
        for num, label, color in guide_labels:
            if line.strip().startswith(num):
                flush_block()
                current_label = label
                current_color = color
                rest = line.strip()[len(num):].strip()
                for lbl in ["EVALUATE", "PROPOSE", "TOOLS TO CONSIDER", "RISKS TO FLAG", "QUICK WIN", "LONG-TERM PLAY"]:
                    if rest.upper().startswith(lbl):
                        rest = rest[len(lbl):].lstrip(" -:").strip()
                current_lines = [rest] if rest else []
                matched = True
                break
        if not matched and current_label is not None:
            current_lines.append(line)

    flush_block()
    guide_html = "\n".join(blocks)
    return summary_html, guide_html


def summary_block_html(paras):
    """Summary paragraphs, every one escaped: the text is AI-written."""
    return "".join(f"<p>{escape(p)}</p>" for p in paras)


def guide_block_html(label, color, text):
    """One guide block. Label, colour and text are all escaped; the colour only ever picks a CSS class."""
    color_class = f" {color}" if color in ("green", "amber", "red") else ""
    return (f'<div class="guide-block{color_class}">'
            f'<h3 class="guide-label">{escape(label.capitalize())}</h3>'
            f'<div class="guide-text">{escape(text)}</div>'
            f'</div>')


def source_link_html(url):
    """A link to the source only for an https URL; anything else (javascript:, data:, http:) is not a link."""
    url = str(url or "").strip()
    if not url.lower().startswith("https://"):
        return ""
    return f'<a href="{escape(url)}" target="_blank" rel="noopener noreferrer" class="source-link">View original source</a>'


def render_breakdown_page(title, slug, url, source_label, summary_html, guide_html, meta_line, generated_at):
    """The page frame. title, url, meta_line and generated_at are raw text and are escaped here; summary_html and
    guide_html come from summary_block_html / guide_block_html, which escape their text."""
    title, meta_line, generated_at = escape(str(title)), escape(str(meta_line)), escape(str(generated_at))
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title} - IT Breakdown - Scout Daily</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Syne:wght@800&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    *,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
    :root{{--bg:#0f1117;--surface:#1a1d27;--surface2:#22263a;--border:#2d3148;--text:#f1f5f9;--text-secondary:#94a3b8;--text-muted:#8592a8;--accent:#00ff88;--accent-ink:#0f1117;--accent-tint:rgba(0,255,136,0.1);--warn:#fbbf24;--danger:#f87171;--fs-xs:12px;--fs-sm:13px;--radius:8px;--tap:44px}}
    body{{background:var(--bg);color:var(--text);font-family:'Inter',sans-serif;font-size:15px;line-height:1.6;min-height:100vh}}
    ::selection{{background:var(--accent);color:var(--accent-ink)}}
    :focus-visible{{outline:2px solid var(--accent);outline-offset:2px}}
    .topbar{{border-bottom:1px solid var(--border);padding:8px 32px;display:flex;align-items:center;justify-content:space-between;gap:16px;position:sticky;top:0;background:var(--bg);z-index:100}}
    .brand-link,.back-btn,.source-link{{display:inline-flex;align-items:center;min-height:var(--tap);text-decoration:none}}
    .brand-link{{font-family:'Syne',sans-serif;font-size:20px;font-weight:800;color:var(--accent);white-space:nowrap}}
    .back-btn{{font-size:var(--fs-sm);font-weight:500;color:var(--text-secondary);padding:0 4px}}
    .back-btn:hover{{color:var(--text)}}
    .page-wrap{{max-width:860px;margin:0 auto;padding:48px 32px 96px}}
    .page-title{{font-family:'Georgia',serif;font-size:clamp(1.5rem,1.1rem + 1.6vw,2rem);font-weight:700;line-height:1.25;color:#ffffff;margin-bottom:12px;overflow-wrap:anywhere;text-wrap:balance}}
    .meta-line{{font-size:var(--fs-sm);color:var(--text-muted);margin-bottom:24px;overflow-wrap:anywhere}}
    .source-link{{font-size:var(--fs-sm);font-weight:600;color:var(--accent);background:var(--accent-tint);border:1px solid rgba(0,255,136,0.2);padding:0 16px;border-radius:6px;margin-bottom:40px}}
    .source-link:hover{{background:rgba(0,255,136,0.16)}}
    .section-title{{font-size:20px;font-weight:700;color:var(--text);margin-bottom:16px}}
    .summary-body{{font-size:15px;line-height:1.8;color:var(--text-secondary);max-width:60ch}}
    .summary-body p{{margin-bottom:16px}}
    .divider{{height:1px;background:var(--border);margin:40px 0}}
    .guide-grid{{display:flex;flex-direction:column;gap:16px}}
    .guide-block{{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:20px 24px}}
    .guide-label{{font-size:15px;font-weight:600;margin-bottom:8px;color:var(--text)}}
    .guide-block.green .guide-label{{color:var(--accent)}}
    .guide-block.amber .guide-label{{color:var(--warn)}}
    .guide-block.red .guide-label{{color:var(--danger)}}
    .guide-text{{font-size:14px;line-height:1.7;color:var(--text-secondary);white-space:pre-wrap;overflow-wrap:anywhere;max-width:60ch}}
    .generated-note{{margin-top:48px;font-size:var(--fs-xs);color:var(--text-muted);text-align:center}}
    @media(max-width:700px){{.page-wrap{{padding:32px 16px 64px}}.topbar{{padding:4px 16px;position:static}}}}
  </style>
</head>
<body>
<header class="topbar">
  <a href="../index.html" class="brand-link">Scout Daily</a>
  <a href="../index.html" class="back-btn">← Back to feed</a>
</header>
<main class="page-wrap">
  <h1 class="page-title">{title}</h1>
  <p class="meta-line">{meta_line} · Generated {generated_at}</p>
  {source_link_html(url)}
  <section>
    <h2 class="section-title">Detailed summary</h2>
    <div class="summary-body">{summary_html}</div>
  </section>
  <div class="divider"></div>
  <section>
    <h2 class="section-title">IT engineer application guide</h2>
    <div class="guide-grid">{guide_html}</div>
  </section>
  <p class="generated-note">AI-generated breakdown · Scout Daily · {generated_at}</p>
</main>
</body>
</html>"""


STORED = re.compile(
    r'<h1 class="page-title">(?P<title>.*?)</h1>.*?'
    r'<div class="meta-line">(?P<meta>.*?)</div>.*?'
    r'<a href="(?P<url>[^"]*)"[^>]*class="source-link">.*?'
    r'<div class="summary-body">(?P<summary>.*?)</div>\s*<div class="divider">.*?'
    r'<div class="guide-grid">(?P<guide>.*?)</div>\s*<div class="generated-note">', re.S)
STORED_BLOCK = re.compile(
    r'<div class="guide-block(?: (?P<color>\w+))?"><div class="guide-label">(?P<label>.*?)</div>'
    r'<div class="guide-text">(?P<text>.*?)</div></div>', re.S)


def stored_text(fragment):
    """What a browser shows for a stored fragment: tags dropped, entities decoded. The escape on re-render undoes it."""
    return unescape(re.sub(r"</?[a-zA-Z][^<>]*>", "", fragment))  # a tag starts with a letter: "<10 ms" is text


def restyle_page(old_html):
    """New frame and style around an old page's stored text, with no AI call. None when the page has a shape this
    does not recognise, so the caller leaves it as it is."""
    m = STORED.search(old_html)
    if not m:
        return None
    meta, sep, generated_at = stored_text(m["meta"]).rpartition(" · Generated ")
    blocks = list(STORED_BLOCK.finditer(m["guide"]))
    if not sep or len(blocks) != m["guide"].count('class="guide-block'):
        return None
    summary = summary_block_html(stored_text(p) for p in re.findall(r"<p>(.*?)</p>", m["summary"], re.S))
    guide = "\n".join(guide_block_html(stored_text(b["label"]), b["color"] or "", stored_text(b["text"])) for b in blocks)
    return render_breakdown_page(stored_text(m["title"]), "", unescape(m["url"]), "", summary, guide, meta, generated_at)


def restyle_existing_pages(directory=OUTPUT_DIR):
    """Rewrite every existing breakdown page in the current frame. Returns (restyled, skipped file names)."""
    restyled, skipped = 0, []
    for path in sorted(Path(directory).glob("*.html")):
        new_html = restyle_page(path.read_text(encoding="utf-8"))
        if new_html is None:
            skipped.append(path.name)
            continue
        path.write_text(new_html, encoding="utf-8")
        restyled += 1
    return restyled, skipped


def process_case_studies(cs_data, generated_at):
    if not cs_data or not cs_data.get("case_studies"):
        print("No case studies found.")
        return {}

    slug_map = {}
    studies = cs_data["case_studies"]
    print(f"Generating breakdowns for {len(studies)} case studies...")

    for i, cs in enumerate(studies):
        title = cs.get("title", "Untitled")
        slug = slugify(title)
        output_path = f"{OUTPUT_DIR}/{slug}.html"

        if os.path.exists(output_path):
            print(f"  [{i+1}/{len(studies)}] Skipping (exists): {slug}")
            slug_map[title] = slug
            continue

        print(f"  [{i+1}/{len(studies)}] Generating: {title[:60]}...")
        raw = generate_content_for_case_study(cs)

        if raw == "DAILY_LIMIT":
            return slug_map
        if not raw:
            print(f"    Failed to generate content.")
            continue

        summary_html, guide_html = parse_groq_response(raw)
        if not summary_html:
            print(f"    Failed to parse response.")
            continue

        authors = ", ".join(cs.get("authors", [])) or "Unknown"
        institution = cs.get("institution", "")
        pub = cs.get("pub_date", "")[:10]
        meta_line = " · ".join(filter(None, [authors, institution, pub]))
        url = cs.get("url", "#")

        html = render_breakdown_page(title, slug, url, "Read Paper →", summary_html, guide_html, meta_line, generated_at)

        with open(output_path, "w") as f:
            f.write(html)

        slug_map[title] = slug
        print(f"    Saved: {output_path}")
        time.sleep(0.5)

    return slug_map


def process_news(news_data, generated_at):
    if not news_data or not news_data.get("news"):
        print("No news found.")
        return {}

    slug_map = {}
    items = news_data["news"]
    print(f"Generating breakdowns for {len(items)} news items...")

    for i, item in enumerate(items):
        title = item.get("title", "Untitled")
        slug = slugify(title)
        output_path = f"{OUTPUT_DIR}/{slug}.html"

        if os.path.exists(output_path):
            print(f"  [{i+1}/{len(items)}] Skipping (exists): {slug}")
            slug_map[title] = slug
            continue

        print(f"  [{i+1}/{len(items)}] Generating: {title[:60]}...")
        raw = generate_content_for_news(item)

        if raw == "DAILY_LIMIT":
            return slug_map
        if not raw:
            print(f"    Failed to generate content.")
            continue

        summary_html, guide_html = parse_groq_response(raw)
        if not summary_html:
            print(f"    Failed to parse response.")
            continue

        source = item.get("source", "")
        pub = item.get("pub_date", "")[:10]
        meta_line = " · ".join(filter(None, [source, pub]))
        url = item.get("url", "#")

        html = render_breakdown_page(title, slug, url, "Read Article →", summary_html, guide_html, meta_line, generated_at)

        with open(output_path, "w") as f:
            f.write(html)

        slug_map[title] = slug
        print(f"    Saved: {output_path}")
        time.sleep(0.5)

    return slug_map


def save_slug_map(cs_slugs, news_slugs):
    slug_map = {"case_studies": cs_slugs, "news": news_slugs}
    with open("site/breakdowns/slug_map.json", "w") as f:
        json.dump(slug_map, f, indent=2)
    print(f"Slug map saved: site/breakdowns/slug_map.json")


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    generated_at = datetime.now().strftime("%d %b %Y, %H:%M")

    cs_data = load_latest_json(CASE_STUDIES_DIR)
    news_data = load_latest_json(NEWS_DIR, prefix="news_")

    cs_slugs = process_case_studies(cs_data, generated_at)
    news_slugs = process_news(news_data, generated_at)
    save_slug_map(cs_slugs, news_slugs)

    print(f"\nDone. {len(cs_slugs)} case study + {len(news_slugs)} news breakdowns generated.")


if __name__ == "__main__":
    if "--restyle" in sys.argv:  # re-frame existing pages only: no Groq call
        done, skipped = restyle_existing_pages()
        print(f"Restyled {done} pages; skipped {len(skipped)} with an unknown shape: {skipped}")
    else:
        main()