#!/usr/bin/env python3
"""Scout Daily email: Mon-Fri at 11:00 London, built from committed Scout data. No LLM calls.

Usage: python send_daily_email.py [--dry-run] [--force]
"""
import argparse
import glob
import html
import json
import os
import re
import smtplib
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")
SITE_URL = "https://agent-builder-daily.vercel.app"
CS_ARCHIVE, NEWS_ARCHIVE = "case_studies/archive.json", "site/news_archive.json"
LAST_SENT, PREVIEW = "email/last_sent.txt", "email/preview.html"
STALE_AFTER = timedelta(hours=30)
SEND_HOUR = 11
CASE_LOOKBACK_DAYS = 7
CREATORS_GLOB = "data/creators/[0-9][0-9][0-9][0-9]-*.json"  # written by creator_watch.py
CREATOR_MAX = 3

# Site palette (generate_html.py :root). Dark backgrounds set on every cell so dark-mode clients don't invert them.
C = {"bg": "#0f1117", "surface": "#1a1d27", "surface2": "#22263a", "border": "#2d2d2d", "text": "#f1f5f9",
     "text2": "#94a3b8", "muted": "#64748b", "accent": "#6366f1", "green": "#00ff88"}
FONT = "Inter, -apple-system, Segoe UI, Arial, sans-serif"


# ---------- data ----------

def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _score(item, key):
    try:
        return float(item.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


def load_data():
    """Read the latest news and case-study day. Any missing, unreadable or malformed data (missing file, bad JSON,
    empty archive, non-object entry, missing or badly formatted date, corrupt day file) returns newest=None with a
    reason, and main() sends the stale alert instead of crashing."""
    try:
        news_day = _load(NEWS_ARCHIVE)[0]
        cs_days = _load(CS_ARCHIVE)
        cs_day = cs_days[0]
        news = sorted(news_day.get("news", []), key=lambda n: _score(n, "relevance_score"), reverse=True)
        papers = sorted(cs_day.get("case_studies", []), key=lambda c: _score(c, "total_score"), reverse=True)
        stamps = [datetime.strptime(news_day["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)]
        runs = sorted(glob.glob("case_studies/[0-9][0-9][0-9][0-9]-*.json"))
        if runs and _load(runs[-1]).get("timestamp"):
            ts = datetime.fromisoformat(_load(runs[-1])["timestamp"])
            stamps.append(ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc))  # scout runs on UTC runners
    except (OSError, ValueError, IndexError, KeyError, TypeError, AttributeError) as e:
        print(f"[EMAIL] Scout data missing or malformed: {e!r}")
        return {"news": [], "papers": [], "newest": None, "reason": "a data file is missing or empty, or malformed"}
    return {"news": news, "papers": papers, "cs_days": cs_days, "newest": max(stamps), "reason": None,
            "creators": load_creators(), "headlines": news_day.get("headlines", [])[:3]}


def load_creators():
    """Creator Watch rows from the newest 8 day files (one per run day covers the 7-day window). An unreadable file is
    skipped: Creator Watch never stops the digest."""
    rows = []
    for path in sorted(glob.glob(CREATORS_GLOB))[-8:]:
        try:
            items = _load(path).get("items")
        except (OSError, ValueError, AttributeError) as e:
            print(f"[EMAIL] Skipping {path}: {e!r}")
            continue
        rows += [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []
    return rows


def creator_watch(rows, now):
    """Up to CREATOR_MAX items published in the last 7 days, newest first, one per URL. [] omits the section.
    The link must be https on youtube.com or on the creator's own blog host in data/creators.json. Title, summary and
    key idea get creator_watch.py's checks again, so a day file written before a check existed cannot bypass it."""
    from urllib.parse import urlsplit
    import creator_watch as cw  # stdlib-only at import
    blogs = {c.get("name"): urlsplit(str(c.get("feed_url"))).hostname
             for c in cw._read_json(cw.CREATORS_FILE, []) if isinstance(c, dict) and c.get("type") == "blog"}
    picked = {}
    for it in rows or []:
        try:
            published = datetime.fromisoformat(it["published"])
            fresh = now - timedelta(days=CASE_LOOKBACK_DAYS) <= published <= now
        except (KeyError, TypeError, ValueError):  # naive or missing date: TypeError on the compare
            continue
        if fresh and cw.item_url_ok(it.get("url"), cw.YOUTUBE_HOSTS | {blogs.get(it.get("creator"))}):
            picked.setdefault(it["url"], {**it, "published_at": published,
                                          "title": cw.safe_title(it.get("title"), it.get("creator"), it.get("type")),
                                          "summary": cw.clean_summary(it.get("summary")),
                                          "key_idea": cw.clean_summary(it.get("key_idea"), 1)})
    return sorted(picked.values(), key=lambda i: i["published_at"], reverse=True)[:CREATOR_MAX]


def case_of_the_day(cs_days, today):
    """Top case study by total score from the newest archive day in the last 7 days (today plus the 6 before).
    shown_date is None for today and the day's date otherwise. None when no day qualifies: the section is omitted."""
    dated = []
    for day in cs_days if isinstance(cs_days, list) else []:
        try:
            d = datetime.strptime(day["date"], "%Y-%m-%d").date()
        except (KeyError, TypeError, ValueError):
            continue
        items = [c for c in day.get("case_studies") or [] if isinstance(c, dict) and c.get("title")]
        if items and 0 <= (today - d).days < CASE_LOOKBACK_DAYS:
            dated.append((d, items))
    if not dated:
        return None
    d, items = max(dated, key=lambda x: x[0])
    return {**max(items, key=lambda c: _score(c, "total_score")), "shown_date": None if d == today else d}


def try_this_enabled():
    """The one switch rule, also used by the scout: ON only when TRY_THIS_ENABLED is exactly "1" (no strip, no case change).
    Off by default: the N1 audit (F1-F4) found invented steps that still pass the checks."""
    return os.getenv("TRY_THIS_ENABLED") == "1"


def try_this(case):
    """The 3 steps, or [] when the switch is off, the case study predates try_this or the list is malformed."""
    if not try_this_enabled():
        return []
    steps = case.get("try_this") if case else None
    ok = isinstance(steps, list) and len(steps) == 3 and all(isinstance(s, str) and s.strip() for s in steps)
    return [s.strip() for s in steps] if ok else []


def _count(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}" if n else ""


def teaser(top, case, radar, hits):
    """One line of counts, e.g. 'Today: 1 security story, 1 case study, 3 papers, 5 quick hits.' No titles, so it never
    repeats The Signal or the bullets below it."""
    cat = (top.get("category") or "").strip().lower()
    parts = [f"1 {cat if cat and cat != 'other' else 'top'} story" if top else "", "1 case study" if case else "",
             _count(len(radar), "paper"), _count(len(hits), "quick hit")]
    parts = [p for p in parts if p]
    return "Today: " + ", ".join(parts) + "." if parts else "Here is today's Scout."


SIGNAL_HEADINGS = ("Do this week", "Risk if ignored", "Tools affected")
_S_PARTS = re.compile(r"^\s*S1\s*:\s*(.*?)\s*S2\s*:\s*(.*?)\s*S3\s*:\s*(.*?)\s*$", re.S)
_S_LABEL = re.compile(r"\bS[1-3]\s*:\s*")


def signal_points(text):
    """enterprise_use arrives as 'S1: … S2: … S3: …'. Returns (heading, text) pairs: three headed points, or one
    unheaded point when the labels are missing or out of order. No 'S1:' label survives either way."""
    m = _S_PARTS.match(text or "")
    if m and all(m.groups()):
        return list(zip(SIGNAL_HEADINGS, m.groups()))
    rest = " ".join(_S_LABEL.sub("", text or "").split())
    return [(None, rest)] if rest else []


def two_sentences(text):
    parts = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    return " ".join(parts[:2])


def safe_url(url):
    return url if isinstance(url, str) and url.startswith(("https://", "http://")) else SITE_URL


# ---------- H11 BLUF block (House rule H11, checked by evals/email_check.py) ----------
# Rules: (project name in docs/PROJECTS.md, repo, keyword pattern, file or metric it changes, why). First match wins, so
# the most specific rules come first. An item with no match is dropped (House rule H11).
SD = ("Scout Daily", "afarah101/agent-builder-daily")
H11_RULES = (
    ("Aurora website", "afarah101/Aurora-Electrical-Website", r"third-party script|compromised cdn|served malware|malicious (code|script)|magecart",
     "external scripts loaded by app/layout.tsx", "risk: a bad third-party script can reach site visitors"),
    (*SD, r"jailbreak|backdoor|prompt[- ]injection|hijack|poison",
     "judge.py INJECTION_PATTERNS (the unsafe-text guard)", "risk: hostile text in a story can pass the judge"),
    (*SD, r"agentic workflow|agent workflows?|langgraph|static (safety )?analy|workflow bugs?",
     "orchestrator.py and the LangGraph scouts", "risk: a hidden logic bug can stop or skew the daily run"),
    (*SD, r"inference serving|rate.?limit|\bgroq\b|llama|prefill|token budget|ai budget|context and reasoning|reasoning effort",
     "groq_complete in case_study_scout_langgraph.py (model calls)", "tokens: fewer wasted Groq calls per run"),
    (*SD, r"code review|review, fix|ai coding tool|claude code|claude ship|pull request|github actions|release pipeline",
     ".github/workflows/ (build and email jobs)", "time: an agent can check each PR before you merge"),
    (*SD, r"langfuse|observability|llm judge|llm-as-a-judge|\bevals?\b",
     "judge.py scores in Langfuse (trace log tool)", "time: faster fault finding in the daily run"),
    ("DUS App", "afarah101/dus-app", r"firestore|firebase|\bexpo\b|react native|push notification|crashlytics|app store|mobile app",
     "Firestore reads and app releases", "money: the Firestore bill is 8x the limit"),
    ("Aurora website", "afarah101/Aurora-Electrical-Website",
     r"next\.js|vercel|core web vitals|google business|local seo|tradespe|electrician|small business website",
     "site build and lead capture (app/, content/site.md)", "money: more calls and WhatsApp leads"),
    ("Football Twin", "afarah101/football-twin", r"football|soccer|premier league|understat|expected goals|sports data",
     "player data and Claim Check", "time: better data for the twin model"),
    ("Optomate lead signals", "afarah101/optomate-lead-signals", r"sales lead|lead gen|lead scoring|duckdb|(hvac|plumbing) (contractor|business|compan)",
     "lead ranking (src/ranking)", "money: better leads for Optomate"),
)
H11_MAX_ITEMS = 3


def _h11_link(item):
    text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    for name, repo, pattern, target, why in H11_RULES:
        if re.search(pattern, text):
            return name, repo, target, why
    return None


def _first_words(text, n=14):
    words = two_sentences(text).split()
    return " ".join(words[:n]) + ("…" if len(words) > n else "")


def h11_items(news, case, radar):
    """Up to 3 items that link to a named project. Security stories are ACT NOW; the rest are LATER (test on a branch)."""
    picked, seen = [], set()
    for it in [*news[:6], *([case] if case else []), *radar]:
        link = _h11_link(it) if isinstance(it, dict) else None
        key = (it or {}).get("url") or (it or {}).get("title")
        if not link or key in seen:
            continue
        seen.add(key)
        name, repo, target, why = link
        security = (it.get("category") or "").lower() == "security"
        do = f"{'ACT NOW' if security else 'LATER'} — {why}."
        slug = re.sub(r"[^a-z0-9]+", "-", (it.get("title") or "item").lower()).strip("-")[:30]
        prompt = (f"In {repo}, spike on a new branch spike/{slug}. Read-only: find the files that {target} uses and list "
                  f"what \"{(it.get('title') or '')[:80]}\" would change, with file:line. Change no file. "
                  f"PASS if 3 or more file:line places are listed; FAIL if fewer.")
        picked.append({"title": _first_words(it.get("title"), 9), "found": _first_words(it.get("summary")), "project": name,
                       "target": target, "do": do, "source": safe_url(it.get("url")), "prompt": prompt[:600]})
        if len(picked) == H11_MAX_ITEMS:
            break
    return picked


def _mailto(n, word, date):
    from urllib.parse import quote
    to = clean_header(os.getenv("REPORT_EMAIL") or os.getenv("GMAIL_USER") or "")
    return f"mailto:{quote(to)}?subject={quote(f'Re: Scout Daily {date}')}&body={quote(f'{word} {n}')}"


def h11_top_line(items):
    if not items:
        return "No action today. No story links to your projects. Read the digest below DETAIL if you have time."
    first = items[0]
    return f"{len(items)} item(s) for your projects. Start with item 1 ({first['project']})."


# ---------- render ----------

def build_email(data, now):
    news, papers = data["news"], data["papers"]
    case = case_of_the_day(data.get("cs_days"), now.astimezone(LONDON).date())
    shown = case and (case.get("url") or case.get("title"))  # Radar skips the paper already shown as Case study of the day
    top, hits = (news[0] if news else {}), news[1:6]
    radar = [p for p in papers if not shown or (p.get("url") or p.get("title")) != shown][:3]
    date = now.strftime("%a %d %b %Y")
    sources = sorted({n.get("source", "") for n in hits if n.get("source")})
    bullets = [
        f"The Signal: {top.get('title', 'no top story today')}",
        *([f"Case study of the day: {case['title']}" + (f" (from {case['shown_date']:%a %d %b})" if case["shown_date"] else "")]
          if case else []),
        f"Research Radar: {len(radar)} paper(s)" + (f", led by {radar[0]['title']} ({_score(radar[0], 'total_score'):.0f}/75)" if radar else ""),
        f"Quick Hits: {len(hits)} more stories" + (f" from {', '.join(sources[:3])}" if sources else ""),
    ]
    h11 = h11_items(news, case, radar)
    sections = {
        "h11": h11,
        "top_line": h11_top_line(h11),
        "greeting": teaser(top, case, radar, hits),
        "bullets": bullets,
        "signal": top,
        "signal_points": signal_points(top.get("enterprise_use")),
        "case": case,
        "creators": creator_watch(data.get("creators"), now),
        "radar": radar,
        "hits": hits,
        "headlines": data.get("headlines", []),
        "footer": f"{len(news)} news items, {len(papers)} case studies. Data as of {data['newest']:%Y-%m-%d %H:%M} UTC.",
    }
    subject = f"Scout Daily — {date} — {top.get('title', 'no top story')}"
    return subject, render_html(date, sections), render_text(date, sections), sections


UNSUBSCRIBE_URL = "mailto:admin@optomate.co.uk?subject=Unsubscribe%20Scout%20Daily"
PRIVACY_URL = "https://agent-builder-daily.vercel.app/privacy.html"


def _e(s):
    return html.escape(str(s or ""))


def _link(url, label):
    return f'<a href="{_e(safe_url(url))}" style="color:{C["accent"]};text-decoration:none;">{_e(label)}</a>'


def _block(title, inner):
    return (f'<tr><td bgcolor="{C["surface"]}" style="background:{C["surface"]};padding:20px 24px;border-top:1px solid {C["border"]};">'
            f'<div style="font:600 12px {FONT};letter-spacing:1px;text-transform:uppercase;color:{C["green"]};margin-bottom:10px;">{_e(title)}</div>'
            f'{inner}</td></tr>')


def _p(text, color=None, size=15):
    return f'<p style="margin:0 0 10px;font:{size}px/1.5 {FONT};color:{color or C["text"]};">{text}</p>'


def render_html(date, s):
    sig = s["signal"]
    radar = "".join(
        _p(_link(c.get("url"), c.get("title")) + f' <span style="color:{C["green"]};">{_score(c, "total_score"):.0f}/75</span>')
        + _p(_e(c.get("summary")), C["text2"], 14) for c in s["radar"]) or _p("No papers passed today's checks.", C["text2"])
    hits = "".join(f'<li style="margin:0 0 8px;">{_link(n.get("url"), n.get("title"))} '
                   f'<span style="color:{C["muted"]};">· {_e(n.get("source"))}</span></li>' for n in s["hits"])
    bullets = "".join(f'<li style="margin:0 0 6px;">{_e(b)}</li>' for b in s["bullets"])
    ul = f'style="margin:0;padding-left:18px;font:14px/1.5 {FONT};color:{C["text"]};"'
    points = "".join(f'<li style="margin:0 0 6px;">' + (f"<b>{_e(h)}:</b> " if h else "") + f"{_e(t)}</li>"
                     for h, t in s["signal_points"])
    case = s["case"]
    case_block = ""
    if case:
        steps = "".join(f'<li style="margin:0 0 6px;">{_e(t)}</li>' for t in try_this(case))
        case_block = _block("Case study of the day", _p(_link(case.get("url"), case.get("title")), size=16)
                            + (_p(f"From {case['shown_date']:%a %d %b %Y}", C["muted"], 13) if case["shown_date"] else "")
                            + _p(_e(two_sentences(case.get("summary"))), C["text2"], 14)
                            + (_p("<b>Try this today</b>", C["text"], 14) + f"<ol {ul}>{steps}</ol>" if steps else ""))
    creators = "".join(
        _p(_link(c["url"], c.get("title")), size=15)
        + _p(f"{_e(c.get('creator'))} · {c['published_at']:%a %d %b}", C["muted"], 13)
        + (_p(f"<b>AI summary:</b> {_e(c.get('summary'))}", C["text2"], 14) if c.get("summary") else "")
        + (_p(f"<b>Key idea:</b> {_e(c.get('key_idea'))}", C["text2"], 14) if c.get("key_idea") else "")
        for c in s["creators"])
    creator_block = _block("Creator Watch", creators) if creators else ""
    headlines = "".join(f'<li style="margin:0 0 8px;">{_link(n.get("url"), n.get("title"))} '
                        f'<span style="color:{C["muted"]};">· {_e(n.get("pub_date"))}</span></li>' for n in s["headlines"])
    headline_block = _block("From McKinsey", f"<ul {ul}>{headlines}</ul>") if headlines else ""
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<meta name="color-scheme" content="dark light"><meta name="supported-color-schemes" content="dark light"></head>
<body style="margin:0;padding:0;background:{C["bg"]};" bgcolor="{C["bg"]}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="{C["bg"]}" style="background:{C["bg"]};"><tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:100%;max-width:600px;border:1px solid {C["border"]};border-radius:8px;">
<tr><td bgcolor="{C["surface2"]}" style="background:{C["surface2"]};padding:24px;">
<div style="font:700 24px {FONT};color:{C["text"]};">Scout Daily</div>
<div style="font:13px {FONT};color:{C["text2"]};margin-top:4px;">{_e(date)} · {_link(SITE_URL, "agent-builder-daily.vercel.app")}</div></td></tr>
{h11_html(date, s)}
{_block("Good morning", _p(_e(s["greeting"])))}
{_block("In today's Scout", f"<ul {ul}>{bullets}</ul>")}
{_block("The Signal", _p(_link(sig.get("url"), sig.get("title")), size=16) + _p(_e(sig.get("summary")), C["text2"], 14)
        + (f'<ul {ul.replace("margin:0;", "margin:0 0 10px;")}>{points}</ul>' if points else "")
        + _p(_link(sig.get("url"), "Read at " + (sig.get("source") or "source") + " →"), size=14))}
{case_block}{creator_block}
{_block("Research Radar", radar)}
{_block("Quick Hits", f"<ul {ul}>{hits}</ul>")}
{headline_block}
<tr><td bgcolor="{C["bg"]}" style="background:{C["bg"]};padding:16px 24px;font:12px {FONT};color:{C["muted"]};">{_e(s["footer"])}<br><a href="{_e(UNSUBSCRIBE_URL)}" style="color:{C["accent"]};">Unsubscribe</a> · <a href="{_e(PRIVACY_URL)}" style="color:{C["accent"]};">Privacy</a></td></tr>
</table></td></tr></table></body></html>"""


def h11_text(date, s):
    """TOP LINE and items above DETAIL; the copy-box Test prompts go just below DETAIL (evals/email_check.py)."""
    lines = ["TOP LINE", s["top_line"], ""]
    for i, it in enumerate(s["h11"], 1):
        lines += [f"{i}. {it['title']}", f"Found: {it['found']}", f"Your link: {it['project']} — {it['target']}.",
                  f"Do: {it['do']}", f"Test prompt: T{i}", f"Source: {it['source']}",
                  f"[YES {i}]({_mailto(i, 'YES', date)}) [NO {i}]({_mailto(i, 'NO', date)})", ""]
    lines += ["DETAIL", ""]
    for i, it in enumerate(s["h11"], 1):
        lines += [f"T{i} · copy into Claude Code", "```text", it["prompt"], "```", ""]
    return lines


def h11_html(date, s):
    items = "".join(
        _p(f"<b>{i}. {_e(it['title'])}</b>", size=15)
        + _p(f"<b>Found:</b> {_e(it['found'])}<br><b>Your link:</b> {_e(it['project'])} — {_e(it['target'])}.<br>"
             f"<b>Do:</b> {_e(it['do'])}<br><b>Source:</b> {_link(it['source'], it['source'])}", C["text2"], 14)
        + f'<pre style="white-space:pre-wrap;margin:0 0 10px;padding:10px;background:{C["bg"]};color:{C["text"]};'
          f'font:12px/1.4 Menlo,Consolas,monospace;border:1px solid {C["border"]};">{_e(it["prompt"])}</pre>'
        + _p(f'<a href="{_e(_mailto(i, "YES", date))}" style="color:{C["green"]};text-decoration:none;">✓ YES {i}</a>'
             f' &nbsp; <a href="{_e(_mailto(i, "NO", date))}" style="color:{C["muted"]};text-decoration:none;">✗ NO {i}</a>', size=14)
        for i, it in enumerate(s["h11"], 1))
    return _block("Top line", _p(_e(s["top_line"])) + items)


def render_text(date, s):
    sig = s["signal"]
    lines = [f"SCOUT DAILY — {date}", SITE_URL, ""] + h11_text(date, s)
    lines += ["Good morning", s["greeting"], "", "IN TODAY'S SCOUT"]
    lines += [f"- {b}" for b in s["bullets"]]
    lines += ["", "THE SIGNAL", sig.get("title", ""), sig.get("summary", "")]
    lines += [f"- {h}: {t}" if h else f"- {t}" for h, t in s["signal_points"]]
    lines += [safe_url(sig.get("url"))]
    case = s["case"]
    if case:
        lines += ["", "CASE STUDY OF THE DAY", case.get("title", "")]
        lines += [f"From {case['shown_date']:%a %d %b %Y}"] if case["shown_date"] else []
        lines += [two_sentences(case.get("summary")), safe_url(case.get("url"))]
        steps = try_this(case)
        lines += ["Try this today:"] + [f"{i}. {t}" for i, t in enumerate(steps, 1)] if steps else []
    if s["creators"]:
        lines += ["", "CREATOR WATCH"]
        for c in s["creators"]:
            lines += [c.get("title", ""), f"{c.get('creator', '')} · {c['published_at']:%a %d %b}"]
            lines += [f"AI summary: {c['summary']}"] if c.get("summary") else []
            lines += [f"Key idea: {c['key_idea']}"] if c.get("key_idea") else []
            lines += [c["url"]]
    lines += ["", "RESEARCH RADAR"]
    lines += [f"- {c.get('title')} ({_score(c, 'total_score'):.0f}/75)\n  {c.get('summary', '')}\n  {safe_url(c.get('url'))}"
              for c in s["radar"]] or ["- No papers passed today's checks."]
    lines += ["", "QUICK HITS"] + [f"- {n.get('title')} — {safe_url(n.get('url'))}" for n in s["hits"]]
    if s["headlines"]:
        lines += ["", "FROM MCKINSEY"] + [f"- {n.get('title')} ({n.get('pub_date')}) — {safe_url(n.get('url'))}"
                                          for n in s["headlines"]]
    lines += ["", s["footer"], f"Unsubscribe: {UNSUBSCRIBE_URL}", f"Privacy: {PRIVACY_URL}"]
    return "\n".join(lines)


def stale_reason(data, now):
    """Why no digest can be sent, or None when the data is fit for a digest."""
    if data["newest"] is None:
        return data.get("reason") or "a data file is missing or empty, or malformed"
    if not data["news"]:
        return "no news items today"
    if now - data["newest"] > STALE_AFTER:
        return f"data is {(now - data['newest']).total_seconds() / 3600:.0f} hours old"
    return None


def build_stale(data, now, why):
    subject = f"Scout data is stale: {why} — {now:%a %d %b %Y}"
    if data["newest"] is None or not data["news"]:
        reason = f"Scout data is stale: {why}."
    else:
        age = now - data["newest"]
        reason = (f"Scout data is stale: newest data is from {data['newest']:%Y-%m-%d %H:%M} UTC, "
                  f"{age.total_seconds() / 3600:.0f} hours old (limit {STALE_AFTER.total_seconds() / 3600:.0f}h).")
    text = f"{reason}\nCheck the Scout Daily workflow runs. No digest was sent.\n{SITE_URL}"
    body = _p(_e(text).replace("\n", "<br>"))
    page = f'<!doctype html><html><body bgcolor="{C["bg"]}" style="background:{C["bg"]};padding:24px;">{body}</body></html>'
    return subject, page, text


# ---------- send ----------

_BAD_HEADER_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")  # C0, DEL, and the C1 block smuggled in by cp1252 paste


def clean_header(s):
    """Scraped headlines carry NBSP and control characters; CR/LF would also allow header injection.
    \\s+ is Unicode-aware, so it folds NBSP into a plain space."""
    return re.sub(r"\s+", " ", _BAD_HEADER_CHARS.sub(" ", str(s or ""))).strip()


def clean_secret(s):
    """Google shows app passwords in four groups; the spaces are display only and are often pasted as NBSP,
    which smtplib's AUTH PLAIN cannot ascii-encode (run 39970000707). Zero-width separators (U+200B) and a
    leading U+FEFF BOM survive the same paste but are category Cf, not \\s, so they need their own pass."""
    stripped = re.sub(r"\s+", "", str(s or ""))
    return "".join(c for c in stripped if unicodedata.category(c) != "Cf")


def build_message(subject, html_body, text_body, sender, recipient):
    msg = MIMEMultipart("alternative")
    msg["From"], msg["To"] = sender, recipient
    # Explicit, rather than compat32's implicit fallback. An explicit charset also RFC 2047-encodes a
    # pure-ASCII subject (=?utf-8?q?...?=) instead of leaving it literal: one code path for every subject,
    # so the encoding cannot differ between a quiet day and a day with an em dash in the headline.
    msg["Subject"] = Header(clean_header(subject), "utf-8")
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    return msg


def smtp_send(subject, html_body, text_body, *, smtp_factory=smtplib.SMTP_SSL, host="smtp.gmail.com", port=465):
    """smtp_factory/host/port are seams for the tests; the defaults are the production path."""
    user, password = clean_secret(os.getenv("GMAIL_USER")), clean_secret(os.getenv("GMAIL_APP_PASSWORD"))
    recipient = clean_secret(os.getenv("REPORT_EMAIL")) or user
    if not user or not password:
        raise RuntimeError("GMAIL_USER / GMAIL_APP_PASSWORD not set")
    msg = build_message(subject, html_body, text_body, user, recipient)
    with smtp_factory(host, port) as server:
        server.login(user, password)
        refused = server.send_message(msg)
    return f"accepted by {host} for {recipient}; refused={refused or 'none'}"


def main(argv=None, now=None, sleep=time.sleep, send=smtp_send):
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--dry-run", action="store_true", help="write email/preview.html, send nothing")
    args.add_argument("--force", action="store_true", help="ignore the weekday and 11:00 rules (still once per day)")
    args = args.parse_args(argv)
    now = now or datetime.now(LONDON)
    today = now.date().isoformat()

    if not args.dry_run and os.path.exists(LAST_SENT) and open(LAST_SENT).read().strip() == today:
        print(f"[EMAIL] Already sent for {today}, skipping.")
        return "already_sent"
    if not args.force and not args.dry_run:
        if now.weekday() >= 5:
            print(f"[EMAIL] {today} is a weekend in London, not sending.")
            return "weekend"
        send_at = now.replace(hour=SEND_HOUR, minute=0, second=0, microsecond=0)
        if now < send_at:
            wait = (send_at - now).total_seconds()
            print(f"[EMAIL] Sleeping {wait:.0f}s until 11:00 London.")
            sleep(wait)
            now = send_at

    data = load_data()
    why = stale_reason(data, now)
    stale = why is not None
    if stale:
        subject, html_body, text_body = build_stale(data, now, why)
    else:
        subject, html_body, text_body, sections = build_email(data, now)
    print(f"[EMAIL] Subject: {clean_header(subject)}")  # what build_message will actually send

    if args.dry_run:
        os.makedirs(os.path.dirname(PREVIEW), exist_ok=True)
        with open(PREVIEW, "w", encoding="utf-8") as f:
            f.write(html_body)
        if not stale:
            print(f"[EMAIL] Sections: bullets={len(sections['bullets'])} signal={1 if sections['signal'] else 0} "
                  f"case={1 if sections['case'] else 0} radar={len(sections['radar'])} hits={len(sections['hits'])}")
        print(f"[EMAIL] Dry run: wrote {PREVIEW}, nothing sent.")
        return "stale_preview" if stale else "preview"

    print(f"[EMAIL] SMTP: {send(subject, html_body, text_body)}")
    os.makedirs(os.path.dirname(LAST_SENT), exist_ok=True)
    with open(LAST_SENT, "w") as f:
        f.write(today + "\n")
    return "stale_sent" if stale else "sent"


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[EMAIL] Failed: {e}", file=sys.stderr)
        sys.exit(1)
