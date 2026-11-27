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

import quality_checks
# the one font set and token block, and the tag, meta and label rules the cards use
from generate_html import (DEFAULT_TAG, HOW_LABEL, HOW_LABELS, KIND_TAG, SKIP_CSS, SKIP_LINK, TOKENS_CSS, canonical_tag,
                           clean_meta, font_faces, font_links, meta_text, seo_tags)

OUTPUT_DIR = "site/breakdowns"
SHORT = " (short version)"  # ends the "generated" note of a page made without Groq; a later run replaces that page
HELD = " (source-checked short version)"  # the AI page failed the grounding checks twice: this page is not replaced
SOURCE_CAP = 4000  # characters of source text sent with a page prompt

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


# Sent with every page prompt. page_problems() and the judge enforce it: a page that breaks it is not published.
GROUNDING_RULES = """GROUNDING RULES. A page that breaks one is not published.
1. Use only facts that are in the SOURCE TEXT below. If the source does not say it, do not write it.
2. Every number, price, percentage, date, version and name of a product, company or person must appear in the
   SOURCE TEXT or the title. Write no other number.
3. Never write a price, cost, benchmark, metric, tier, feature or setting that the source does not state. Never write
   an example figure ("e.g. 30%").
4. State no prediction as fact. A forward view (a prediction, an implication, why it matters) goes in a paragraph
   that starts with "Our take:".
5. Never write a placeholder such as "xxxx", "TBD", "[insert" or "example.com".
6. If the source is thin, write less. A short accurate page is right. A long invented page is wrong."""

PAGE_TASK = """Write two sections. Use plain text with no markdown formatting, no asterisks, no hash symbols. Use numbered lists where appropriate.

SECTION_SUMMARY:
Write 120-250 words in short paragraphs on what the source says: {covers}. Facts from the source only.
Then write one last paragraph that starts with "Our take:" and says why it matters for enterprise IT teams.

SECTION_IT_GUIDE:
Write a practical IT engineer application guide with these 6 parts. This is advice for the reader's own environment.
A time frame is allowed here; every other number must come from the source.
1. EVALUATE - What to audit or assess in your current environment before acting
2. PROPOSE - How to build a business case for leadership. Use only metrics the source states. If it states none, name the metrics to measure and give no figures
3. TOOLS TO CONSIDER - Tools the source names, and the reader's own stack where it applies. State no feature the source does not state
4. RISKS TO FLAG - Technical, compliance (UK GDPR where relevant), or operational risks
5. QUICK WIN - Something achievable in under 30 days
6. LONG-TERM PLAY - The 6-12 month strategic move

Separate the two sections with exactly this delimiter: ---SPLIT---"""

REGENERATE_NOTE = """

Your last answer broke the GROUNDING RULES: {problems}.
Write both sections again. Remove every number, name and claim that is not in the SOURCE TEXT. Put a forward view in the "Our take:" paragraph."""


def source_body(item):
    """The stored source of a story: full article text, abstract or feed snippet, cut to SOURCE_CAP."""
    return str(item.get("source_text") or item.get("abstract") or item.get("summary_raw") or "").strip()[:SOURCE_CAP]


def page_prompt(head, item, covers, feedback=""):
    body = source_body(item) or "(no source text is stored: write only what the title and the summary state)"
    return (f"You are writing for an enterprise IT engineer audience. Be direct, technical, and actionable. No marketing language.\n\n"
            f"{GROUNDING_RULES}\n\n{head}\n\nSOURCE TEXT:\n<<<\n{body}\n>>>\n\n" + PAGE_TASK.format(covers=covers) + feedback)


def generate_content_for_case_study(cs, feedback=""):
    authors = ", ".join(cs.get("authors", [])) or "Unknown"
    head = (f"Case Study Title: {cs.get('title', 'Untitled')}\n"
            f"Authors: {authors} | Institution: {cs.get('institution', '')} | Published: {cs.get('pub_date', '')}\n"
            f"Summary: {cs.get('summary', '')}\nHow to Apply: {cs.get('it_breakdown') or cs.get('how_to_apply', '')}")
    return call_groq(page_prompt(head, cs, "what was studied, what was built and what was found", feedback))


def generate_content_for_news(item, feedback=""):
    head = (f"News Title: {item.get('title', 'Untitled')}\n"
            f"Source: {item.get('source', '')} | Category: {item.get('category', '')} | Published: {item.get('pub_date', '')}\n"
            f"Summary: {item.get('summary', '')}\nEnterprise Use: {item.get('enterprise_use', '')}")
    return call_groq(page_prompt(head, item, "what happened, who is involved and which vendors or technologies it names", feedback))


def split_sections(raw):
    """(summary text, guide text) of a Groq answer, or (None, None) when it has no ---SPLIT---."""
    if not raw or "---SPLIT---" not in raw:
        return None, None
    summary_raw, guide_raw = (part.strip() for part in raw.split("---SPLIT---", 1))
    for prefix in ["SECTION_SUMMARY:", "SECTION_IT_GUIDE:"]:
        summary_raw = summary_raw.replace(prefix, "").strip()
        guide_raw = guide_raw.replace(prefix, "").strip()
    return summary_raw, guide_raw


def page_problems(summary, guide, item):
    """Why page text is not grounded in the story's stored source, as short strings. [] means it passes. No model call.
    summary: every number, %, currency amount, date, ID and numbered name must be in the source, no placeholder, and
    no forward view outside an "Our take:" paragraph. guide: the same, but a time frame there is advice."""
    source = quality_checks.source_text(item)
    summary, guide = str(summary or ""), str(guide or "")
    missing = list(dict.fromkeys(quality_checks.ungrounded(summary, source) + quality_checks.ungrounded(guide, source, action=True)))
    found = quality_checks.placeholders(summary + "\n" + guide)
    views = quality_checks.unlabelled_forward_views(summary, source)
    return ((["not in source: " + ", ".join(missing[:10])] if missing else [])
            + (["placeholder text: " + ", ".join(found)] if found else [])
            + ([f'forward view without "{quality_checks.TAKE_LABEL}": ' + ", ".join(views)] if views else []))


PAGE_JUDGE_RULES = """
The action of these items is a six-part how-to guide for the reader's own environment. General IT practice in it (audit,
pilot, monitor, roll back, train staff) is advice, not a claim about the source, and is not a fail. FAIL the item when the
guide states a fact about the product, company or study in the source that the source does not state."""


def judge_page(item, summary, guide):
    """The Gemini judge on one generated page: "pass", "fail" or "error", with the reason. Without GEMINI_API_KEY the
    judge is skipped ("pass"): the deterministic checks in page_problems have already run."""
    if not os.environ.get("GEMINI_API_KEY"):
        return "pass", "judge skipped: no GEMINI_API_KEY"
    try:
        import judge
        page = {"title": item.get("title") or "", "source_text": quality_checks.source_text(item), "source": item.get("source"),
                "source_type": item.get("source_type"), "pub_date": item.get("pub_date"), "page_summary": summary, "page_guide": guide}
        logs = []
        kept, _, stats = judge.gate([page], "title", "source_text", "page_summary", "page_guide", log=logs.append,
                                    extra_rules=judge.GROUNDING_RULES + PAGE_JUDGE_RULES)
        reason = next((m for m in logs if "HELD BACK" in m), "")
        return ("pass" if kept else "error" if stats.get("errors") else "fail"), reason
    except Exception as e:  # the judge must never stop the pages
        return "error", f"{type(e).__name__}: {str(e)[:120]}"


def parse_groq_response(raw):
    summary_raw, guide_raw = split_sections(raw)
    if summary_raw is None:
        return None, None

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
    """One guide block. Label, colour and text are all escaped; the colour only ever picks a CSS class.
    "How to use", "Do this week" and "What to do" all show as "How to apply", the label the cards use."""
    color_class = f" {color}" if color in ("green", "amber", "red") else ""
    label = HOW_LABEL if str(label).strip().rstrip(":").lower() in HOW_LABELS else label.capitalize()
    return (f'<div class="guide-block{color_class}">'
            f'<h3 class="guide-label">{escape(label)}</h3>'
            f'<div class="guide-text">{escape(text)}</div>'
            f'</div>')


def source_link_html(url):
    """A link to the source only for an https URL; anything else (javascript:, data:, http:) is not a link."""
    url = str(url or "").strip()
    if not url.lower().startswith("https://"):
        return ""
    return f'<a href="{escape(url)}" target="_blank" rel="noopener noreferrer" class="source-link">Read the original →</a>'


# The article shell: dark hero (logo, tag, H1, meta), then a white panel with a 672px reading column.
# site/privacy.html carries the same rules, so both pages have one layout. Every value is a token.
ARTICLE_CSS = """*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      background: var(--c-bg);
      color: var(--c-text);
      font-family: var(--font);
      font-size: var(--fs-body);
      line-height: var(--lh-body);
      min-height: 100vh;
      overflow-wrap: anywhere;  /* long words and URLs wrap at 360px */
    }

    ::selection { background: var(--c-text); color: var(--c-bg); }
    :focus-visible { outline: 2px solid var(--c-text); outline-offset: 2px; }
    .hero :focus-visible { outline-color: var(--c-bg); }

    /* Hero: logo (links home), tag, H1, meta */
    .hero { background: var(--g-hero); color: var(--c-bg); padding: var(--s-24) 0 calc(var(--s-64) + var(--s-48)); }
    .header-inner { max-width: var(--content-max); margin: 0 auto; padding: 0 var(--s-32); }

    .brand-link {
      display: inline-flex;
      align-items: center;
      gap: var(--s-8);
      min-height: var(--tap);
      font-family: var(--font-head);
      font-size: var(--fs-h4);
      line-height: 1;
      font-weight: 700;
      letter-spacing: var(--ls-head);
      color: var(--c-bg);
      text-decoration: none;
    }

    .brand-logo { display: block; width: 28px; height: 22px; flex-shrink: 0; }
    .brand-name { white-space: nowrap; }
    .accent-word { color: var(--c-accent); }

    .article-head {
      max-width: calc(var(--reading-max) + 2 * var(--s-64));
      margin: var(--s-64) auto 0;
      padding: 0 var(--s-64);
    }

    .tag-pill {
      display: inline-block;
      margin-bottom: var(--s-16);
      padding: var(--s-4) var(--s-8);
      border: 1px solid var(--c-border-dark);
      border-radius: var(--radius-sm);
      background: var(--c-surface-dark-2);
      color: var(--c-bg);
      font-family: var(--font-mono);
      font-size: var(--fs-label);
      line-height: var(--lh-small);
      letter-spacing: var(--ls-label);
      font-weight: 700;
      text-transform: uppercase;
    }

    .page-title {
      margin-bottom: var(--s-16);
      font-family: var(--font-head);
      font-size: var(--fs-h3);
      line-height: var(--lh-head);
      font-weight: 700;
      letter-spacing: var(--ls-head);
      text-wrap: balance;
    }

    .meta-line {
      font-family: var(--font-mono);
      font-size: var(--fs-small);
      line-height: var(--lh-small);
      color: var(--c-on-dark-muted);
    }

    /* White panel: overlaps the hero bottom, as on the home page */
    .content {
      position: relative;
      width: min(calc(var(--reading-max) + 2 * var(--s-64)), calc(100% - var(--s-32)));
      margin: calc(-1 * var(--s-64)) auto 0;
      padding: var(--s-48) var(--s-64) var(--s-64);
      background: var(--c-bg);
      border-radius: var(--radius-panel) var(--radius-panel) 0 0;
    }

    .article { max-width: var(--reading-max); margin: 0 auto; }

    .article-figure { margin: 0 0 var(--s-32); }
    .article-figure img {
      display: block;
      width: 100%;
      height: auto;
      aspect-ratio: 16 / 9;  /* with width and height on the tag: the space is kept before the file loads */
      object-fit: cover;
      border-radius: var(--radius-card);
      background: var(--c-bg-alt);
    }
    .article-figure figcaption {
      margin-top: var(--s-8);
      font-family: var(--font-mono);
      font-size: var(--fs-label);
      line-height: var(--lh-small);
      color: var(--c-text-muted);
    }

    .section-title {
      margin-bottom: var(--s-16);
      font-family: var(--font-head);
      font-size: var(--fs-h4);
      line-height: var(--lh-snug);
      font-weight: 700;
      letter-spacing: var(--ls-head);
    }

    .summary-body { max-width: var(--measure); color: var(--c-text-soft); }
    .summary-body p { margin-bottom: var(--s-16); }
    .summary-body p:last-child { margin-bottom: 0; }
    .divider { height: var(--s-48); }

    /* "How to apply" panel */
    .apply-panel { padding: var(--s-32); border-radius: var(--radius-panel); background: var(--c-bg-alt); }
    .guide-grid { display: flex; flex-direction: column; gap: var(--s-12); }
    .guide-block {
      padding: var(--s-16) var(--s-24);
      border: 1px solid var(--c-border);
      border-radius: var(--radius-card);
      background: var(--c-bg);
    }
    .guide-label {
      margin-bottom: var(--s-8);
      font-family: var(--font-head);
      font-size: var(--fs-lead);
      line-height: var(--lh-snug);
      font-weight: 700;
      letter-spacing: var(--ls-head);
      color: var(--c-text);
    }
    .guide-text { color: var(--c-text-soft); white-space: pre-wrap; }

    .generated-note {
      margin-top: var(--s-16);
      font-family: var(--font-mono);
      font-size: var(--fs-label);
      line-height: var(--lh-small);
      color: var(--c-text-muted);
    }

    /* Actions: the one emerald button, then the back link */
    .article-actions { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s-8) var(--s-24); margin-top: var(--s-32); }

    .source-link, .cta {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: var(--tap);
      padding: 0 var(--s-24);
      border-radius: var(--radius);
      background: var(--c-accent);
      color: var(--c-on-accent);
      font-size: var(--fs-ui);
      font-weight: 700;
      text-decoration: none;
    }
    .source-link:hover, .cta:hover { background: var(--c-accent-hover); }

    /* Text links: the text keeps its colour, the underline is emerald */
    .back-link, .text-link, footer a {
      display: inline-flex;
      align-items: center;
      min-height: var(--tap);
      color: var(--c-text);
      text-decoration: underline;
      text-underline-offset: 4px;
      text-decoration-thickness: 2px;
      text-decoration-color: var(--c-accent);
    }
    .back-link, .text-link { font-size: var(--fs-ui); font-weight: 600; }
    .back-link:hover, .text-link:hover, footer a:hover { text-decoration-color: var(--c-accent-hover); }

    footer {
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: var(--s-8);
      max-width: var(--content-max);
      margin: 0 auto;
      padding: var(--s-16) var(--s-32);
      border-top: 1px solid var(--c-border);
    }
    footer span { font-size: var(--fs-small); color: var(--c-text-muted); }
    footer a { padding: 0 var(--s-4); }

    @media (min-width: 701px) {
      .page-title { font-size: var(--fs-h2); }
    }

    @media (max-width: 700px) {
      .hero { padding: var(--s-12) 0 var(--s-64); }
      .header-inner, .article-head { padding: 0 var(--s-16); }
      .article-head { margin-top: var(--s-32); }
      .content { width: 100%; margin-top: calc(-1 * var(--s-32)); padding: var(--s-24) var(--s-16) var(--s-48); }
      .article-figure { margin-bottom: var(--s-24); }
      .divider { height: var(--s-32); }
      .apply-panel { padding: var(--s-16); border-radius: var(--radius-lg); }
      .guide-block { padding: var(--s-16); }
      .source-link, .cta { width: 100%; }
      footer { padding: var(--s-16); }
    }"""

def tag_for(item, kind):
    """The page's tag: the canonical tag on the story's card (the kind of story when it has no known topic)."""
    return canonical_tag(item.get("relevance_tag" if kind == "case_studies" else "category"), kind)


def article_figure(slug, directory=OUTPUT_DIR):
    """The page's <figure> when site/images/<slug>.webp exists, else "". width and height keep the space (no layout
    shift); the alt is the scene text generate_images.py stored."""
    from generate_images import FALLBACK_ALT, HEIGHT, WIDTH, load_alts
    images = Path(directory).parent / "images"
    if not slug or not (images / f"{slug}.webp").is_file():
        return ""
    alt = str((load_alts(images).get(slug) or {}).get("alt") or "").strip() or FALLBACK_ALT
    return (f'<figure class="article-figure"><img src="../images/{escape(slug)}.webp" width="{WIDTH}" height="{HEIGHT}" '
            f'alt="{escape(alt)}" fetchpriority="high" decoding="async"><figcaption>AI illustration</figcaption></figure>')


# Summaries that are the page's own words, not the story's: the same on many pages
STOCK_NOTES = ("No source-checked summary is stored for this story. Read the original for the detail.",
               "This breakdown was withdrawn. It was written without the source text, and it did not pass the source check. "
               "Read the original for the detail.", "No summary is stored for this story.")
CANONICAL = re.compile(r'<link rel="canonical" href="[^"]*/breakdowns/(?P<slug>[^"/]+)\.html">')


def page_seo(title, slug, summary_html):
    """The SEO block of a breakdown page (generate_html.seo_tags), or "" when the page has no slug and so no address.
    The description is the start of the page's own summary. A page with no summary of its own (the stock notes) gets
    its title in the description, so no two pages share one."""
    if not slug:
        return ""
    plain = lambda text: " ".join(re.sub(r"</?[a-zA-Z][^<>]*>", "", str(text)).split())  # a search or share card shows plain text
    title, summary = plain(title), plain(stored_text(re.sub(r"</p>\s*<p>", " ", str(summary_html))))
    if not summary or summary in STOCK_NOTES:
        summary = f"{title}: the source link and the notes of Daily Edge AI for IT engineers."
    return "\n  " + seo_tags(f"{title} — IT Breakdown — Daily Edge AI", summary, f"breakdowns/{slug}.html", "article")


def render_breakdown_page(title, slug, url, source_label, summary_html, guide_html, meta_line, generated_at,
                          tag="", figure=""):
    """The page frame, in magazine order: tag, H1, meta, image, summary, "How to apply" panel, source button, back link.
    title, url, meta_line, generated_at and tag are raw text and are escaped here; summary_html and guide_html come
    from summary_block_html / guide_block_html, which escape their text; figure comes from article_figure.
    The tag is one of the canonical tags and the meta line is "who · source type · date", as on the story's card."""
    seo = page_seo(title, slug, summary_html)
    title, meta_line, generated_at = escape(str(title)), escape(clean_meta(meta_line)), escape(str(generated_at))
    tag = escape(canonical_tag(tag))
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title} — IT Breakdown — Daily Edge AI</title>{seo}
  <link rel="icon" href="../favicon.svg" type="image/svg+xml">
  <style>
    {font_faces("../")}

    {TOKENS_CSS}

    {ARTICLE_CSS}

    {SKIP_CSS}

    .brand-logo {{ background: url(../logo.svg) center/contain no-repeat; }}
  </style>
</head>
<body>
{SKIP_LINK}
<header class="hero">
  <div class="header-inner">
    <a class="brand-link" href="../index.html"><span class="brand-logo" aria-hidden="true"></span><span class="brand-name">Daily Edge <span class="accent-word">AI</span></span></a>
  </div>
  <div class="article-head">
    <p class="tag-pill">{tag}</p>
    <h1 class="page-title">{title}</h1>
    <p class="meta-line">{meta_line}</p>
  </div>
</header>
<main class="content" id="main">
  <article class="article">
    {figure}
    <section>
      <h2 class="section-title">Summary</h2>
      <div class="summary-body">{summary_html}</div>
    </section>
    <div class="divider"></div>
    <section class="apply-panel">
      <h2 class="section-title">How to apply</h2>
      <div class="guide-grid">{guide_html}</div>
    </section>
    <p class="generated-note">AI-generated breakdown&nbsp;· Daily Edge AI&nbsp;· {generated_at}</p>
    <div class="article-actions">
      {source_link_html(url)}
      <a class="back-link" href="../index.html">← Back to today</a>
    </div>
  </article>
</main>
<footer>
  <span>Daily Edge AI&nbsp;· LangGraph + Groq + arXiv&nbsp;· <a href="../privacy.html">Privacy</a></span>
  <span>Case Studies: last 4 months&nbsp;· News: last 4–8 weeks&nbsp;· Images are AI-generated.</span>
</footer>
{font_links("../")}
</body>
</html>"""


# Old and current page shapes: meta line and generated note as <div> or <p>, guide labels as <div> or <h3>; the
# "Generated" stamp at the end of the meta line (old) or only in the generated note (current).
STORED = re.compile(
    r'<h1 class="page-title">(?P<title>.*?)</h1>.*?'
    r'<(?:div|p) class="meta-line">(?P<meta>.*?)</(?:div|p)>(?P<between>.*?)'
    r'<div class="summary-body">(?P<summary>.*?)</div>\s*(?:</section>\s*)?<div class="divider">.*?'
    r'<div class="guide-grid">(?P<guide>.*?)</div>\s*(?:</section>\s*)?<(?:div|p) class="generated-note">'
    r'(?P<note>.*?)</(?:div|p)>', re.S)
SOURCE = re.compile(r'<a href="(?P<url>[^"]*)"[^>]*class="source-link">')
TAG = re.compile(r'<p class="tag-pill">(?P<tag>.*?)</p>')
FIGURE = re.compile(r'<figure class="article-figure">.*?</figure>', re.S)
STORED_BLOCK = re.compile(
    r'<div class="guide-block(?: (?P<color>\w+))?"><(?:div|h3) class="guide-label">(?P<label>.*?)</(?:div|h3)>'
    r'<div class="guide-text">(?P<text>.*?)</div></div>', re.S)


def stored_text(fragment):
    """What a browser shows for a stored fragment: tags dropped, entities decoded. The escape on re-render undoes it."""
    return unescape(re.sub(r"</?[a-zA-Z][^<>]*>", "", fragment))  # a tag starts with a letter: "<10 ms" is text


def restyle_page(old_html, tag="", figure=None, slug=""):
    """New frame and style around an old page's stored text, with no AI call. None when the page has a shape this
    does not recognise, so the caller leaves it as it is. tag: the story's tag when the caller knows it, else the
    page keeps the tag it has. figure: the <figure> to show ("" for none); None keeps the one the page has.
    slug: the page's file name without ".html", for its canonical URL; "" keeps the address the page already states."""
    m = STORED.search(old_html)
    if not m:
        return None
    meta, sep, generated_at = stored_text(m["meta"]).rpartition(" · Generated ")
    if not sep:  # the current shape: the stamp is in the generated note only
        meta, (_, sep, generated_at) = stored_text(m["meta"]), stored_text(m["note"]).replace("\u00a0", " ").rpartition(" · ")
    blocks = list(STORED_BLOCK.finditer(m["guide"]))
    if not sep or len(blocks) != m["guide"].count('class="guide-block'):
        return None
    source = SOURCE.search(old_html)  # one page in the archive has no source link
    stored_tag, stored_figure = TAG.search(old_html), FIGURE.search(old_html)
    summary = summary_block_html(stored_text(p) for p in re.findall(r"<p>(.*?)</p>", m["summary"], re.S))
    guide = "\n".join(guide_block_html(stored_text(b["label"]), b["color"] or "", stored_text(b["text"])) for b in blocks)
    url = unescape(source["url"]) if source else ""
    if figure is None:
        figure = stored_figure[0] if stored_figure else ""
    stated = CANONICAL.search(old_html)
    return render_breakdown_page(stored_text(m["title"]), slug or (stated["slug"] if stated else ""), url, "", summary, guide,
                                 meta, generated_at, tag=tag or (stored_text(stored_tag["tag"]) if stored_tag else ""), figure=figure)


def known_tags():
    """{slug: tag} for every story the data files still hold (active days and both archives). Reads only."""
    import generate_html as gh
    tags = {}
    files = [(f, "case_studies") for f in sorted(Path(gh.CASE_STUDIES_DIR).glob("*.json"))]
    files += [(f, "news") for f in [*sorted(Path(gh.NEWS_DIR).glob("*.json")), Path(gh.ARCHIVE_NEWS_FILE)]]
    for path, kind in files:
        data = gh.load_json(str(path))
        for day in data if isinstance(data, list) else [data]:
            for item in (day.get(kind) or []) if isinstance(day, dict) else []:
                tags.setdefault(page_slug(item.get("title")), tag_for(item, kind))
    return tags


def restyle_existing_pages(directory=OUTPUT_DIR, tags=None):
    """Rewrite every existing breakdown page in the current frame, each with its tag and its image when the file is
    there. Returns (restyled, skipped file names)."""
    restyled, skipped = 0, []
    tags = tags or {}
    for path in sorted(Path(directory).glob("*.html")):
        new_html = restyle_page(path.read_text(encoding="utf-8"), tags.get(path.stem, ""),
                                article_figure(path.stem, directory), slug=path.stem)
        if new_html is None:
            skipped.append(path.name)
            continue
        path.write_text(new_html, encoding="utf-8")
        restyled += 1
    return restyled, skipped


def sync_figures(directory=OUTPUT_DIR):
    """Each page shows its card's AI image, under the title, exactly when the image file exists, with the scene of that
    image as the alt: an image is made after its page, a redrawn image has a new scene, and generate_images.py deletes
    an image after 4 months. Returns the number of pages changed."""
    have = {p.stem for p in (Path(directory).parent / "images").glob("*.webp")}
    changed = 0
    for path in Path(directory).glob("*.html"):
        html = path.read_text(encoding="utf-8")
        stored = FIGURE.search(html)
        figure = article_figure(path.stem, directory) if path.stem in have else ""
        if (stored[0] if stored else "") != figure:
            new_html = restyle_page(html, figure=figure, slug=path.stem)
            if new_html:
                path.write_text(new_html, encoding="utf-8")
                changed += 1
    return changed


NOT_APPLICABLE = ("not applicable", "not directly applicable")
# News sources read from a listing page. Before the scout read the date from the article page (it marks those items
# "scraped"), their pub_date was the day of the scrape. That is not the source's date, so it is not shown.
SCRAPED_SOURCES = ("Anthropic", "Deloitte")


def source_dated(item, kind):
    """False when the item's pub_date is known not to be the source's own publish date."""
    return kind != "news" or item.get("source") not in SCRAPED_SOURCES or bool(item.get("scraped"))


def meta_line_for(item, kind):
    """The card's meta line: the first 2 authors + "et al." or the source, the source type, the date. The date is the
    source's own or none (source_dated)."""
    return meta_text(item if source_dated(item, kind) else {**item, "pub_date": ""}, kind)


NO_SUMMARY = STOCK_NOTES[0]
NO_HOW_TO = "No how-to notes are stored for this story. Read the original for the detail."


def short_content(item, kind):
    """(summary_html, guide_html) from the text the scout already stored for the item. No AI call. Stored text that
    fails the grounding checks against the item's own source is left out: a page never shows an unchecked claim."""
    listing = quality_checks.is_listing_url(item.get("url"))  # a list of stories is not a source: none of its text is shown
    summary = str(item.get("summary") or "").strip()
    if not summary or listing or page_problems(summary, "", item):
        summary = NO_SUMMARY
    summary_html = summary_block_html([p.strip() for p in summary.split("\n\n") if p.strip()])
    if kind == "case_studies":
        how = str(item.get("it_breakdown") or item.get("how_to_apply") or "").strip()
        points = [("What to do", how)] if how else []
    else:
        from send_daily_email import signal_points  # the parser the home page and the email use for S1/S2/S3
        points = [(head or "What to do", text) for head, text in signal_points(str(item.get("enterprise_use") or ""))]
    points = [(head, text) for head, text in points if text and not any(p in text.lower() for p in NOT_APPLICABLE)
              and not listing and not page_problems("", text, item)]
    if not points:
        points = [("What to do", NO_HOW_TO)]
    return summary_html, "\n".join(guide_block_html(head, "", text) for head, text in points)


def is_short_page(path):
    """True for a page made by short_content: its "generated" stamp ends with SHORT (in the generated note; in the
    meta line on a page of the old shape)."""
    return bool(re.search(r'class="(?:meta-line|generated-note)">[^<]*' + re.escape(escape(SHORT)) + "</(?:p|div)>",
                          Path(path).read_text(encoding="utf-8")))


HELD_BACK = []  # (title, reason) for every AI page held back in this process


def process_items(items, kind, generated_at, directory=OUTPUT_DIR, use_ai=True):
    """A page on disk and a slug_map entry for EVERY item; returns {raw title: slug}.
    Groq writes the full page from the story's stored source text. The page is published only when page_problems finds
    nothing (one regenerate is allowed) and the judge passes it; otherwise the AI page is held back and the item's own
    source-checked summary and how-to text make a short page (stamp HELD: never replaced). When Groq gives nothing (no
    key, error, daily limit, bad shape, or use_ai off) the same short page is made with stamp SHORT, so no card is left
    without one; a later run with Groq replaces that page. A full page is never rewritten."""
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
        held = ""
        if use_ai and quality_checks.is_listing_url(item.get("url")):
            held = "the source URL is a listing page, not an article"
        elif use_ai:
            print(f"  [{i+1}/{len(items)}] Generating: {str(title)[:60]}...")
            raw, problems = generate(item), []
            for attempt in (1, 2):  # the grounding check: one regenerate, then the AI page is held back
                if raw == "DAILY_LIMIT" or not raw or split_sections(raw)[0] is None:
                    break
                problems = page_problems(*split_sections(raw), item)
                if not problems:
                    break
                print(f"    [GROUNDING] attempt {attempt} failed: {'; '.join(problems)}")
                if attempt == 1:
                    time.sleep(0.5)
                    raw = generate(item, REGENERATE_NOTE.format(problems="; ".join(problems)))
            if raw == "DAILY_LIMIT":
                use_ai = False  # no more Groq calls this run; the items left still get a page below
            elif raw and split_sections(raw)[0] is not None:
                if problems:
                    held = "; ".join(problems)
                else:
                    verdict, why = judge_page(item, *split_sections(raw))
                    if verdict == "pass":
                        summary_html, guide_html = parse_groq_response(raw)
                    elif verdict == "fail":
                        held = f"judge: {why}"
                    else:  # the judge gave no verdict: keep the short page, a later run tries again
                        print(f"    [GROUNDING] judge error, AI page not published this run: {why}")
            time.sleep(0.5)
        if held:
            HELD_BACK.append((str(title), held))
            print(f"    [GROUNDING] HELD BACK AI page for {str(title)[:60]}: {held}")

        stamp = generated_at
        if not summary_html:
            if path.exists() and not held:
                continue  # keep the short page that is already there
            summary_html, guide_html = short_content(item, kind)
            stamp += HELD if held else SHORT
        url = "" if quality_checks.is_listing_url(item.get("url")) else item.get("url", "")  # no link to a listing page
        path.write_text(render_breakdown_page(title or "Untitled", slug, url, "", summary_html,
                                              guide_html, meta_line_for(item, kind), stamp, tag=tag_for(item, kind),
                                              figure=article_figure(slug, directory)), encoding="utf-8")
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
    slug_map = save_slug_map(process_items(studies, "case_studies", generated_at, directory, use_ai=False),
                             process_items(news, "news", generated_at, directory, use_ai=False), directory)
    sync_figures(directory)
    return slug_map


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


def story_index():
    """{slug: (item, kind)} for every story the data files hold (active days and all archives). Of two copies of a
    story, the one with the longer stored source wins. Reads only."""
    import generate_html as gh
    index = {}
    files = [(f, "case_studies") for f in [*sorted(Path(gh.CASE_STUDIES_DIR).glob("*.json")), Path("site/case_studies_archive.json")]]
    files += [(f, "news") for f in [*sorted(Path(gh.NEWS_DIR).glob("*.json")), Path(gh.ARCHIVE_NEWS_FILE), Path("site/news_archive.json")]]
    for path, kind in files:
        data = gh.load_json(str(path)) if path.exists() else []
        for day in data if isinstance(data, list) else [data]:
            for item in (day.get(kind) or []) if isinstance(day, dict) else []:
                slug = page_slug(item.get("title"))
                if slug not in index or len(source_body(item)) > len(source_body(index[slug][0])):
                    index[slug] = (item, kind)
    return index


def page_text(html):
    """(title, summary text, guide text, source URL, meta line) of a stored page, or None for an unknown shape."""
    m = STORED.search(html)
    if not m:
        return None
    summary = "\n".join(stored_text(p) for p in re.findall(r"<p>(.*?)</p>", m["summary"], re.S))
    guide = "\n".join(stored_text(b["text"]) for b in STORED_BLOCK.finditer(m["guide"]))
    source = SOURCE.search(html)
    return stored_text(m["title"]), summary, guide, unescape(source["url"]) if source else "", stored_text(m["meta"])


def scan_page(path, index, strict=True):
    """Why one live page fails the grounding checks; [] means it passes. The page is checked against the stored source
    of its story. A page whose story is in no data file (the archives keep 60 to 90 days) has only its own title as
    source: strict fails every claim on it; strict=False runs only the checks that need no source on such a page."""
    parts = page_text(Path(path).read_text(encoding="utf-8"))
    if parts is None:
        return ["page shape not recognised: cannot be checked"]
    title, summary, guide, url, meta = parts
    item, kind = index.get(Path(path).stem, ({"title": title}, "news"))
    if strict or Path(path).stem in index:
        problems = page_problems(summary, guide, item)
    else:
        found = quality_checks.placeholders(summary + "\n" + guide)
        problems = ["placeholder text: " + ", ".join(found)] if found else []
    if quality_checks.is_listing_url(url):
        problems.append(f"source URL is a listing page: {url}")
    shown = meta.rpartition(" · Generated ")[0] or meta  # an old page has the stamp in its meta line
    if not source_dated(item, kind) and re.search(r"\d", shown.replace("\u00a0", " ").rpartition(" · ")[2]):
        problems.append("the date shown is the day of the scrape, not the source's publish date")
    return problems


def scan_pages(directory=OUTPUT_DIR, index=None, strict=True):
    """Every breakdown page checked with the grounding checks. Returns (pages checked, {file name: [problems]})."""
    index = story_index() if index is None else index
    pages = sorted(Path(directory).glob("*.html"))
    failures = {p.name: found for p in pages if (found := scan_page(p, index, strict))}
    return len(pages), failures


WITHDRAWN = " (withdrawn)"  # ends the "generated" note of a page whose text was taken down by fix_pages
WITHDRAWN_NOTE = STOCK_NOTES[1]


def fix_pages(failures, directory=OUTPUT_DIR, index=None, delete=False):
    """Each failed page is re-rendered, withdrawn or removed. Returns (re-rendered, withdrawn, removed) file names.
    Removed: the source URL is a listing page (a list of stories is not a story), or the page has an unknown shape.
    Re-rendered: the story is in a data file, so the page becomes the short version of its stored text, and stored text
    that fails the checks is left out (short_content). A later run with Groq replaces a short page of a shown card.
    Withdrawn: the story is in no data file, so no claim on the page can be checked. The page keeps its address, its
    title and its link to the original, and WITHDRAWN_NOTE takes the place of its text. delete=True removes it."""
    index = story_index() if index is None else index
    generated_at = datetime.now().strftime("%d %b %Y, %H:%M")
    rendered, withdrawn, removed = [], [], []
    for name in sorted(failures):
        path, slug = Path(directory) / name, name[:-5]
        html = path.read_text(encoding="utf-8")
        parts, (item, kind) = page_text(html), index.get(slug, (None, None))
        listing = quality_checks.is_listing_url(item.get("url") if item else parts[3] if parts else "")
        if parts is None or listing or (delete and not item):
            path.unlink()
            removed.append(name)
        elif item:
            summary_html, guide_html = short_content(item, kind)
            path.write_text(render_breakdown_page(item.get("title") or "Untitled", slug, item.get("url", ""), "", summary_html,
                                                  guide_html, meta_line_for(item, kind), generated_at + SHORT,
                                                  tag=tag_for(item, kind), figure=article_figure(slug, directory)), encoding="utf-8")
            rendered.append(name)
        else:
            title, _, _, url, meta = parts
            meta = (meta.rpartition(" · Generated ")[0] or meta).replace("\u00a0", " ")
            if meta.split(" · ")[0] in SCRAPED_SOURCES:  # that date was the day of the scrape
                meta = " · ".join(p for p in meta.split(" · ") if not re.search(r"\d{4}", p))
            tag = TAG.search(html)
            path.write_text(render_breakdown_page(title, slug, url, "", summary_block_html([WITHDRAWN_NOTE]),
                                                  guide_block_html(HOW_LABEL, "", NO_HOW_TO), meta, generated_at + WITHDRAWN,
                                                  tag=stored_text(tag["tag"]) if tag else "", figure=article_figure(slug, directory)),
                            encoding="utf-8")
            withdrawn.append(name)
    return rendered, withdrawn, removed


def scan_main(fix=False, delete=False):
    """--scan: list every page that fails. --scan --fix: also re-render, withdraw or remove each one, then scan again.
    --scan --fix --delete: remove a page that --fix alone withdraws."""
    index = story_index()
    checked, failures = scan_pages(index=index)
    print(f"Scanned {checked} pages: {len(failures)} fail the grounding checks.")
    for name, problems in failures.items():
        print(f"FAIL {name}: {' | '.join(problems)[:300]}")
    if fix and failures:
        rendered, withdrawn, removed = fix_pages(failures, index=index, delete=delete)
        print(f"Re-rendered {len(rendered)} pages as the source-checked short version; withdrew the text of {len(withdrawn)} pages; "
              f"removed {len(removed)} pages.")
        for name in withdrawn:
            print(f"WITHDRAWN {name}")
        for name in removed:
            print(f"REMOVED {name}")
        checked, failures = scan_pages(index=index)
        print(f"Scanned {checked} pages again: {len(failures)} fail.")
    return checked, failures


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
    print(f"[GROUNDING] breakdown pages: {len(HELD_BACK)} AI pages held back (the source-checked short version is shown).")


if __name__ == "__main__":
    if "--scan" in sys.argv:  # check every live page against its stored source: no Groq call
        sys.exit(1 if scan_main(fix="--fix" in sys.argv, delete="--delete" in sys.argv)[1] else 0)
    elif "--restyle" in sys.argv:  # re-frame existing pages only: no Groq call
        done, skipped = restyle_existing_pages(tags=known_tags())
        print(f"Restyled {done} pages; skipped {len(skipped)} with an unknown shape: {skipped}")
    else:
        main()