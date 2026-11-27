#!/usr/bin/env python3
from dotenv import load_dotenv
load_dotenv()
import os
import json
import re
import sys
import time
import hashlib
from html import escape, unescape
from pathlib import Path
from datetime import datetime, timedelta
from groq import Groq

OUTPUT_DIR = "site/breakdowns"
SHORT = " (short version)"  # ends the "Generated" stamp of a page made without Groq; a later run replaces that page

# Dual API key setup — falls back to key 2 if key 1 hits daily limit
API_KEY_1 = os.environ.get("GROQ_API_KEY")
API_KEY_2 = os.environ.get("GROQ_API_KEY_2")
_current_key = API_KEY_1
_key1_exhausted = False

def get_client():
    return Groq(api_key=_current_key)


def slugify(title):
    slug = title.lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug.strip())
    slug = re.sub(r"-+", "-", slug)
    return slug[:80]


def page_slug(title):
    """The page's file name. A title with no ASCII letter or digit still gets one, from a hash."""
    title = str(title or "Untitled")
    return slugify(title) or "story-" + hashlib.sha1(title.encode("utf-8")).hexdigest()[:10]


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
    return f'<a href="{escape(url)}" target="_blank" rel="noopener noreferrer" class="source-link">Read the original →</a>'


def render_breakdown_page(title, slug, url, source_label, summary_html, guide_html, meta_line, generated_at):
    """The page frame. title, url, meta_line and generated_at are raw text and are escaped here; summary_html and
    guide_html come from summary_block_html / guide_block_html, which escape their text."""
    title, meta_line, generated_at = escape(str(title)), escape(str(meta_line)), escape(str(generated_at))
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title} — IT Breakdown — Daily Edge AI</title>
  <link rel="icon" href="../favicon.svg" type="image/svg+xml">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Geist:wght@400;600;700&family=Geist+Mono:wght@400;700&display=swap">
  <style>
    *,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
    :root{{--font:"Geist",ui-sans-serif,system-ui,sans-serif;--font-mono:"Geist Mono",ui-monospace,SFMono-Regular,Menlo,monospace;--ls-head:-0.02em;--c-bg:#000000;--c-surface:#171717;--c-border:#333333;--c-text:#ffffff;--c-text-2:#d4d4d4;--c-text-muted:#a3a3a3;--c-accent:#10b981;--c-accent-hover:#059669;--c-on-accent:#0a0a0a;--c-warn:#fbbf24;--c-bad:#f87171;--fs-body:16px;--lh-body:1.625;--fs-small:14px;--fs-label:12px;--radius:8px;--radius-card:12px;--tap:44px;--reading:65ch;--content-max:1152px}}
    body{{background:var(--c-bg);color:var(--c-text);font-family:var(--font);font-size:var(--fs-body);line-height:var(--lh-body);min-height:100vh;overflow-wrap:anywhere}}
    ::selection{{background:var(--c-accent);color:var(--c-on-accent)}}
    :focus-visible{{outline:2px solid var(--c-accent);outline-offset:2px}}
    a{{color:inherit}}
    .site-header{{border-bottom:1px solid var(--c-border)}}
    .header-inner,.page-wrap,footer{{max-width:var(--content-max);margin:0 auto;padding-left:32px;padding-right:32px}}
    .header-inner{{display:flex;align-items:center;padding-top:12px;padding-bottom:12px}}
    .brand-link,.back-link,.source-link,footer a{{display:inline-flex;align-items:center;min-height:var(--tap);text-decoration:none}}
    .brand-link{{gap:10px;font-size:21px;line-height:1;font-weight:600;letter-spacing:var(--ls-head);color:var(--c-text)}}
    .brand-logo{{display:block;width:28px;height:22px;flex-shrink:0;background:url(../logo.svg) center/contain no-repeat}}
    .brand-name{{white-space:nowrap}}
    .accent-word{{color:var(--c-accent)}}
    .page-wrap{{padding-top:24px;padding-bottom:80px}}
    .back-link{{gap:8px;padding-right:8px;font-size:var(--fs-body);font-weight:600;color:var(--c-accent);margin-bottom:24px}}
    .back-link:hover{{color:var(--c-accent-hover)}}
    .page-title{{max-width:var(--reading);font-size:clamp(28px,22px + 1.6vw,40px);line-height:1.15;font-weight:700;letter-spacing:var(--ls-head);text-wrap:balance;margin-bottom:12px}}
    .meta-line{{font-family:var(--font-mono);font-size:var(--fs-small);line-height:20px;color:var(--c-text-muted);margin-bottom:24px}}
    .source-link{{padding:0 20px;border-radius:var(--radius);background:var(--c-accent);color:var(--c-on-accent);font-weight:600;margin-bottom:48px}}
    .source-link:hover{{background:var(--c-accent-hover)}}
    .section-title{{font-size:24px;line-height:1.3;font-weight:700;letter-spacing:var(--ls-head);margin-bottom:16px}}
    .summary-body{{max-width:var(--reading);color:var(--c-text-2)}}
    .summary-body p{{margin-bottom:16px}}
    .divider{{height:1px;background:var(--c-border);margin:48px 0}}
    .guide-grid{{display:flex;flex-direction:column;gap:16px}}
    .guide-block{{max-width:calc(var(--reading) + 48px);background:var(--c-surface);border:1px solid var(--c-border);border-radius:var(--radius-card);padding:20px 24px}}
    .guide-label{{font-size:var(--fs-body);font-weight:700;margin-bottom:8px;color:var(--c-text)}}
    .guide-block.green .guide-label{{color:var(--c-accent)}}
    .guide-block.amber .guide-label{{color:var(--c-warn)}}
    .guide-block.red .guide-label{{color:var(--c-bad)}}
    .guide-text{{color:var(--c-text-2);white-space:pre-wrap}}
    .generated-note{{margin-top:48px;font-family:var(--font-mono);font-size:var(--fs-label);color:var(--c-text-muted)}}
    footer{{border-top:1px solid var(--c-border);padding-top:20px;padding-bottom:20px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px}}
    footer span{{font-size:var(--fs-label);color:var(--c-text-muted)}}
    footer a{{padding:0 4px;color:var(--c-text);text-decoration:underline}}
    footer a:hover{{color:var(--c-accent)}}
    @media(max-width:700px){{.header-inner,.page-wrap,footer{{padding-left:16px;padding-right:16px}}.page-wrap{{padding-top:16px;padding-bottom:48px}}.guide-block{{padding:16px}}.divider{{margin:32px 0}}.source-link{{margin-bottom:32px}}}}
  </style>
</head>
<body>
<header class="site-header">
  <div class="header-inner">
    <a class="brand-link" href="../index.html"><span class="brand-logo" aria-hidden="true"></span><span class="brand-name">Daily Edge <span class="accent-word">AI</span></span></a>
  </div>
</header>
<main class="page-wrap">
  <a class="back-link" href="../index.html">← Back to today</a>
  <h1 class="page-title">{title}</h1>
  <p class="meta-line">{meta_line} · Generated {generated_at}</p>
  {source_link_html(url)}
  <section>
    <h2 class="section-title">Summary</h2>
    <div class="summary-body">{summary_html}</div>
  </section>
  <div class="divider"></div>
  <section>
    <h2 class="section-title">How to apply</h2>
    <div class="guide-grid">{guide_html}</div>
  </section>
  <p class="generated-note">AI-generated breakdown · Daily Edge AI · {generated_at}</p>
</main>
<footer>
  <span>Daily Edge AI · LangGraph + Groq + arXiv · <a href="../privacy.html">Privacy</a></span>
  <span>Case Studies: last 4 months · News: last 4–8 weeks</span>
</footer>
</body>
</html>"""


# Old and current page shapes: meta line and generated note as <div> or <p>, guide labels as <div> or <h3>.
STORED = re.compile(
    r'<h1 class="page-title">(?P<title>.*?)</h1>.*?'
    r'<(?:div|p) class="meta-line">(?P<meta>.*?)</(?:div|p)>(?P<between>.*?)'
    r'<div class="summary-body">(?P<summary>.*?)</div>\s*(?:</section>\s*)?<div class="divider">.*?'
    r'<div class="guide-grid">(?P<guide>.*?)</div>\s*(?:</section>\s*)?<(?:div|p) class="generated-note">', re.S)
SOURCE = re.compile(r'<a href="(?P<url>[^"]*)"[^>]*class="source-link">')
STORED_BLOCK = re.compile(
    r'<div class="guide-block(?: (?P<color>\w+))?"><(?:div|h3) class="guide-label">(?P<label>.*?)</(?:div|h3)>'
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
    source = SOURCE.search(m["between"])  # one page in the archive has no source link
    summary = summary_block_html(stored_text(p) for p in re.findall(r"<p>(.*?)</p>", m["summary"], re.S))
    guide = "\n".join(guide_block_html(stored_text(b["label"]), b["color"] or "", stored_text(b["text"])) for b in blocks)
    url = unescape(source["url"]) if source else ""
    return render_breakdown_page(stored_text(m["title"]), "", url, "", summary, guide, meta, generated_at)


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


NOT_APPLICABLE = ("not applicable", "not directly applicable")


def meta_line_for(item, kind):
    """Authors · institution · date for a case study; source · date for news."""
    pub = str(item.get("pub_date") or "")
    pub = pub[:10] if re.match(r"\d{4}-\d\d-\d\d", pub) else pub.strip()  # news dates ("26 Nov 2026") stay whole
    if kind == "case_studies":
        parts = [", ".join(item.get("authors") or []) or "Unknown", item.get("institution", ""), pub]
    else:
        parts = [item.get("source", ""), pub]
    return " · ".join(str(p) for p in parts if p)


def short_content(item, kind):
    """(summary_html, guide_html) from the text the scout already stored for the item. No AI call."""
    summary = str(item.get("summary") or "").strip() or "No summary is stored for this story."
    summary_html = summary_block_html([p.strip() for p in summary.split("\n\n") if p.strip()])
    if kind == "case_studies":
        how = str(item.get("it_breakdown") or item.get("how_to_apply") or "").strip()
        points = [("What to do", how)] if how else []
    else:
        from send_daily_email import signal_points  # the parser the home page and the email use for S1/S2/S3
        points = [(head or "What to do", text) for head, text in signal_points(str(item.get("enterprise_use") or ""))]
    points = [(head, text) for head, text in points if text and not any(p in text.lower() for p in NOT_APPLICABLE)]
    if not points:
        points = [("What to do", "No how-to notes are stored for this story. Read the original for the detail.")]
    return summary_html, "\n".join(guide_block_html(head, "", text) for head, text in points)


def is_short_page(path):
    """True for a page made by short_content: its "Generated" stamp ends with SHORT."""
    return bool(re.search(r'class="meta-line">[^<]*' + re.escape(escape(SHORT)) + "</p>", Path(path).read_text(encoding="utf-8")))


def process_items(items, kind, generated_at, directory=OUTPUT_DIR, use_ai=True):
    """A page on disk and a slug_map entry for EVERY item; returns {raw title: slug}.
    Groq writes the full page. When Groq gives nothing (no key, error, daily limit, bad shape, or use_ai off), the
    item's own summary and how-to text make a short page, so no card is left without one. A later run with Groq
    replaces a short page with the full one; a full page is never rewritten."""
    slug_map = {}
    generate = generate_content_for_case_study if kind == "case_studies" else generate_content_for_news
    for i, item in enumerate(items or []):
        title = item.get("title", "Untitled" if kind == "case_studies" else "")  # the key the card renderer looks up
        slug = page_slug(title)
        path = Path(directory) / f"{slug}.html"
        slug_map[title] = slug
        if path.exists() and not (use_ai and is_short_page(path)):
            continue

        summary_html = guide_html = None
        if use_ai:
            print(f"  [{i+1}/{len(items)}] Generating: {str(title)[:60]}...")
            raw = generate(item)
            if raw == "DAILY_LIMIT":
                use_ai = False  # no more Groq calls this run; the items left still get a page below
            elif raw:
                summary_html, guide_html = parse_groq_response(raw)
            time.sleep(0.5)

        stamp = generated_at
        if not summary_html:
            if path.exists():
                continue  # keep the short page that is already there
            summary_html, guide_html = short_content(item, kind)
            stamp += SHORT
        path.write_text(render_breakdown_page(title or "Untitled", slug, item.get("url", ""), "", summary_html,
                                              guide_html, meta_line_for(item, kind), stamp), encoding="utf-8")
        print(f"    Saved: {path}")
    return slug_map


def save_slug_map(cs_slugs, news_slugs, directory=OUTPUT_DIR):
    slug_map = {"case_studies": cs_slugs, "news": news_slugs}
    path = Path(directory) / "slug_map.json"
    with open(path, "w") as f:
        json.dump(slug_map, f, indent=2)
    print(f"Slug map saved: {path}")
    return slug_map


def ensure_pages(studies, news, directory=OUTPUT_DIR):
    """The slug_map for exactly these items, each with a page on disk. Never calls Groq.
    generate_html calls this with the cards it is about to render, so a card cannot link to a missing page."""
    os.makedirs(directory, exist_ok=True)
    generated_at = datetime.now().strftime("%d %b %Y, %H:%M")
    return save_slug_map(process_items(studies, "case_studies", generated_at, directory, use_ai=False),
                         process_items(news, "news", generated_at, directory, use_ai=False), directory)


def shown_items(now=None):
    """(case studies, news) the home page tabs show, built with generate_html's own helpers as it builds them: every
    active file, top_up from the archive, the 4-month window, score order, honesty checks. Reads only: archives nothing."""
    import generate_html as gh
    now = now or datetime.now()
    cutoff = (now - timedelta(days=gh.CASE_STUDY_WINDOW_DAYS)).strftime("%Y-%m-%d")
    studies = []
    for f in sorted(Path(gh.CASE_STUDIES_DIR).glob("*.json")):
        if "archive" not in f.name:
            data = gh.load_json(str(f))
            studies += (data.get("case_studies") or []) if isinstance(data, dict) else []
    studies = gh.top_up(studies, gh.load_json(gh.ARCHIVE_CS_FILE), "case_studies")
    studies = sorted((s for s in studies if gh.in_window(s, cutoff)), key=lambda x: gh.by_score(x, "total_score"), reverse=True)
    news_files = sorted(f for f in Path(gh.NEWS_DIR).glob("news_*.json") if "archive" not in f.name)
    data = gh.load_json(str(news_files[-1])) if news_files else {}
    news = sorted((data.get("news") or []) if isinstance(data, dict) else [], key=lambda x: gh.by_score(x, "relevance_score"), reverse=True)
    news = gh.top_up(news, gh.load_json(gh.ARCHIVE_NEWS_FILE), "news")
    changes = []
    return gh.honest(studies, now, changes, "case study"), gh.honest(news, now, changes, "news")


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    generated_at = datetime.now().strftime("%d %b %Y, %H:%M")

    studies, news = shown_items()  # every card the page shows, archive top-up cards included
    use_ai = bool(_current_key)
    if not use_ai:
        print("No GROQ_API_KEY: every missing page gets the short version.")
    print(f"Breakdowns for {len(studies)} case studies and {len(news)} news items...")
    cs_slugs = process_items(studies, "case_studies", generated_at, use_ai=use_ai)
    news_slugs = process_items(news, "news", generated_at, use_ai=use_ai)
    save_slug_map(cs_slugs, news_slugs)

    print(f"\nDone. {len(cs_slugs)} case study + {len(news_slugs)} news breakdowns on disk.")


if __name__ == "__main__":
    if "--restyle" in sys.argv:  # re-frame existing pages only: no Groq call
        done, skipped = restyle_existing_pages()
        print(f"Restyled {done} pages; skipped {len(skipped)} with an unknown shape: {skipped}")
    else:
        main()