#!/usr/bin/env python3
from dotenv import load_dotenv
load_dotenv()
import os
import json
import re
import time
from pathlib import Path
from datetime import datetime
from groq import Groq

CASE_STUDIES_DIR = "case_studies"
NEWS_DIR = "logs"
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
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1200,
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
    summary_html = "".join(f"<p>{p}</p>" for p in summary_paras)

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
            text = "\n".join(current_lines).strip()
            color_class = f" {current_color}" if current_color else ""
            blocks.append(
                f'<div class="guide-block{color_class}">'
                f'<div class="guide-label">{current_label}</div>'
                f'<div class="guide-text">{text}</div>'
                f'</div>'
            )

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


def render_breakdown_page(title, slug, url, source_label, summary_html, guide_html, meta_line, generated_at):
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title} - IT Breakdown - Scout Daily</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Syne:wght@400;600;800&family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
  <style>
    *,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
    :root{{--bg:#0f1117;--surface:#1a1d27;--surface2:#22263a;--border:#2d3148;--text:#f1f5f9;--text-secondary:#94a3b8;--text-muted:#64748b;--accent:#6366f1;--accent-green:#22c55e;--radius:12px}}
    body{{background:var(--bg);color:var(--text);font-family:'Inter',sans-serif;font-size:15px;line-height:1.6;min-height:100vh}}
    .topbar{{border-bottom:1px solid var(--border);padding:16px 32px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;background:var(--bg);z-index:100}}
    .brand-link{{font-family:'Syne',sans-serif;font-size:20px;font-weight:800;background:linear-gradient(135deg,#ffffff 0%,#00ff88 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;text-decoration:none}}
    .back-btn{{font-size:13px;color:var(--accent);text-decoration:none;font-weight:500}}
    .page-wrap{{max-width:860px;margin:0 auto;padding:48px 32px 96px}}
    .badge{{display:inline-block;font-family:'JetBrains Mono',monospace;font-size:10px;font-weight:600;letter-spacing:1.5px;text-transform:uppercase;color:var(--accent);background:#6366f11a;border:1px solid #6366f133;padding:4px 12px;border-radius:20px;margin-bottom:20px}}
    .page-title{{font-family:'Syne',sans-serif;font-size:32px;font-weight:800;line-height:1.25;color:var(--text);margin-bottom:16px;letter-spacing:-0.5px}}
    .meta-line{{font-size:13px;color:var(--text-muted);font-family:'JetBrains Mono',monospace;margin-bottom:32px}}
    .source-link{{display:inline-flex;align-items:center;gap:6px;font-size:13px;font-weight:600;color:var(--accent-green);text-decoration:none;background:#22c55e1a;border:1px solid #22c55e33;padding:8px 16px;border-radius:8px;margin-bottom:40px}}
    .divider{{height:1px;background:var(--border);margin:40px 0}}
    .section-header{{display:flex;align-items:center;gap:12px;margin-bottom:24px}}
    .section-num{{font-family:'JetBrains Mono',monospace;font-size:11px;font-weight:600;color:var(--accent);background:#6366f11a;border:1px solid #6366f133;width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;flex-shrink:0}}
    .section-title{{font-family:'Syne',sans-serif;font-size:20px;font-weight:700;color:var(--text)}}
    .summary-body{{font-size:15px;line-height:1.8;color:var(--text-secondary)}}
    .summary-body p{{margin-bottom:16px}}
    .guide-grid{{display:flex;flex-direction:column;gap:16px}}
    .guide-block{{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:20px 24px;border-left:3px solid var(--accent)}}
    .guide-block.green{{border-left-color:var(--accent-green)}}
    .guide-block.amber{{border-left-color:#f59e0b}}
    .guide-block.red{{border-left-color:#ef4444}}
    .guide-label{{font-family:'JetBrains Mono',monospace;font-size:10px;font-weight:600;letter-spacing:1.5px;text-transform:uppercase;margin-bottom:10px;color:var(--accent)}}
    .guide-block.green .guide-label{{color:var(--accent-green)}}
    .guide-block.amber .guide-label{{color:#f59e0b}}
    .guide-block.red .guide-label{{color:#ef4444}}
    .guide-text{{font-size:14px;line-height:1.7;color:var(--text-secondary);white-space:pre-wrap}}
    .generated-note{{margin-top:48px;font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--text-muted);text-align:center}}
    @media(max-width:700px){{.page-wrap{{padding:32px 16px 64px}}.page-title{{font-size:24px}}.topbar{{padding:14px 16px}}}}
  </style>
</head>
<body>
<div class="topbar">
  <a href="../index.html" class="brand-link">Scout Daily</a>
  <a href="../index.html" class="back-btn">← Back to feed</a>
</div>
<div class="page-wrap">
  <div class="badge">IT Engineer Breakdown</div>
  <h1 class="page-title">{title}</h1>
  <div class="meta-line">{meta_line} · Generated {generated_at}</div>
  <a href="{url}" target="_blank" class="source-link">View Original Source →</a>
  <div class="section-header">
    <div class="section-num">1</div>
    <div class="section-title">Detailed Summary</div>
  </div>
  <div class="summary-body">{summary_html}</div>
  <div class="divider"></div>
  <div class="section-header">
    <div class="section-num">2</div>
    <div class="section-title">IT Engineer Application Guide</div>
  </div>
  <div class="guide-grid">{guide_html}</div>
  <div class="generated-note">AI-generated breakdown · Scout Daily · {generated_at}</div>
</div>
</body>
</html>"""


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
    main()