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
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")
SITE_URL = "https://agent-builder-daily.vercel.app"
CS_ARCHIVE, NEWS_ARCHIVE = "case_studies/archive.json", "site/news_archive.json"
LAST_SENT, PREVIEW = "email/last_sent.txt", "email/preview.html"
STALE_AFTER = timedelta(hours=30)
SEND_HOUR = 11

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
    news_day = _load(NEWS_ARCHIVE)[0]
    cs_day = _load(CS_ARCHIVE)[0]
    news = sorted(news_day.get("news", []), key=lambda n: _score(n, "relevance_score"), reverse=True)
    papers = sorted(cs_day.get("case_studies", []), key=lambda c: _score(c, "total_score"), reverse=True)
    stamps = [datetime.strptime(news_day["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)]
    runs = sorted(glob.glob("case_studies/[0-9][0-9][0-9][0-9]-*.json"))
    if runs and _load(runs[-1]).get("timestamp"):
        ts = datetime.fromisoformat(_load(runs[-1])["timestamp"])
        stamps.append(ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc))  # scout runs on UTC runners
    return {"news": news, "papers": papers, "newest": max(stamps)}


def two_sentences(text):
    parts = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    return " ".join(parts[:2])


def safe_url(url):
    return url if isinstance(url, str) and url.startswith(("https://", "http://")) else SITE_URL


# ---------- render ----------

def build_email(data, now):
    news, papers = data["news"], data["papers"]
    top, hits, radar = (news[0] if news else {}), news[1:6], papers[:3]
    date = now.strftime("%a %d %b %Y")
    sources = sorted({n.get("source", "") for n in hits if n.get("source")})
    categories = [n.get("category") for n in news if n.get("category")]
    avg = sum(_score(n, "relevance_score") for n in news) / len(news) if news else 0
    bullets = [
        f"The Signal: {top.get('title', 'no top story today')}",
        f"Research Radar: {len(radar)} paper(s)" + (f", led by {radar[0]['title']} ({_score(radar[0], 'total_score'):.0f}/75)" if radar else ""),
        f"Quick Hits: {len(hits)} more stories" + (f" from {', '.join(sources[:3])}" if sources else ""),
        f"{len(news)} news items scored today, average relevance {avg:.1f}/10",
        f"Most common category: {max(set(categories), key=categories.count)}" if categories else "No categories today",
    ]
    sections = {
        "greeting": two_sentences(top.get("summary", "")),
        "bullets": bullets,
        "signal": top,
        "radar": radar,
        "hits": hits,
        "footer": f"{len(news)} news items, {len(papers)} case studies. Data as of {data['newest']:%Y-%m-%d %H:%M} UTC.",
    }
    subject = f"Scout Daily — {date} — {top.get('title', 'no top story')}"
    return subject, render_html(date, sections), render_text(date, sections), sections


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
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<meta name="color-scheme" content="dark light"><meta name="supported-color-schemes" content="dark light"></head>
<body style="margin:0;padding:0;background:{C["bg"]};" bgcolor="{C["bg"]}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="{C["bg"]}" style="background:{C["bg"]};"><tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:100%;max-width:600px;border:1px solid {C["border"]};border-radius:8px;">
<tr><td bgcolor="{C["surface2"]}" style="background:{C["surface2"]};padding:24px;">
<div style="font:700 24px {FONT};color:{C["text"]};">Scout Daily</div>
<div style="font:13px {FONT};color:{C["text2"]};margin-top:4px;">{_e(date)} · {_link(SITE_URL, "agent-builder-daily.vercel.app")}</div></td></tr>
{_block("Good morning, Ahmed", _p(_e(s["greeting"])))}
{_block("In today's Scout", f"<ul {ul}>{bullets}</ul>")}
{_block("The Signal", _p(_link(sig.get("url"), sig.get("title")), size=16) + _p(_e(sig.get("summary")), C["text2"], 14)
        + _p("<b>Enterprise use:</b> " + _e(sig.get("enterprise_use")), C["text"], 14)
        + _p(_link(sig.get("url"), "Read at " + (sig.get("source") or "source") + " →"), size=14))}
{_block("Research Radar", radar)}
{_block("Quick Hits", f"<ul {ul}>{hits}</ul>")}
<tr><td bgcolor="{C["bg"]}" style="background:{C["bg"]};padding:16px 24px;font:12px {FONT};color:{C["muted"]};">{_e(s["footer"])}</td></tr>
</table></td></tr></table></body></html>"""


def render_text(date, s):
    sig = s["signal"]
    lines = [f"SCOUT DAILY — {date}", SITE_URL, "", "Good morning, Ahmed", s["greeting"], "", "IN TODAY'S SCOUT"]
    lines += [f"- {b}" for b in s["bullets"]]
    lines += ["", "THE SIGNAL", sig.get("title", ""), sig.get("summary", ""), f"Enterprise use: {sig.get('enterprise_use', '')}",
              safe_url(sig.get("url")), "", "RESEARCH RADAR"]
    lines += [f"- {c.get('title')} ({_score(c, 'total_score'):.0f}/75)\n  {c.get('summary', '')}\n  {safe_url(c.get('url'))}"
              for c in s["radar"]] or ["- No papers passed today's checks."]
    lines += ["", "QUICK HITS"] + [f"- {n.get('title')} — {safe_url(n.get('url'))}" for n in s["hits"]]
    lines += ["", s["footer"]]
    return "\n".join(lines)


def build_stale(data, now):
    age = now - data["newest"]
    subject = f"Scout data is stale — {now:%a %d %b %Y}"
    text = (f"Scout data is stale: newest data is from {data['newest']:%Y-%m-%d %H:%M} UTC, "
            f"{age.total_seconds() / 3600:.0f} hours old (limit {STALE_AFTER.total_seconds() / 3600:.0f}h).\n"
            f"Check the Scout Daily workflow runs. No digest was sent.\n{SITE_URL}")
    body = _p(_e(text).replace("\n", "<br>"))
    page = f'<!doctype html><html><body bgcolor="{C["bg"]}" style="background:{C["bg"]};padding:24px;">{body}</body></html>'
    return subject, page, text


# ---------- send ----------

def smtp_send(subject, html_body, text_body):
    user, password = os.getenv("GMAIL_USER"), os.getenv("GMAIL_APP_PASSWORD")
    recipient = os.getenv("REPORT_EMAIL") or user
    if not user or not password:
        raise RuntimeError("GMAIL_USER / GMAIL_APP_PASSWORD not set")
    msg = MIMEMultipart("alternative")
    msg["From"], msg["To"], msg["Subject"] = user, recipient, subject
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(user, password)
        refused = server.sendmail(user, recipient, msg.as_string())
    return f"accepted by smtp.gmail.com for {recipient}; refused={refused or 'none'}"


def main(argv=None, now=None, sleep=time.sleep, send=smtp_send):
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--dry-run", action="store_true", help="write email/preview.html, send nothing")
    args.add_argument("--force", action="store_true", help="ignore the weekday and 11:00 rules")
    args = args.parse_args(argv)
    now = now or datetime.now(LONDON)
    today = now.date().isoformat()

    if not args.force and not args.dry_run:
        if now.weekday() >= 5:
            print(f"[EMAIL] {today} is a weekend in London, not sending.")
            return "weekend"
        if os.path.exists(LAST_SENT) and open(LAST_SENT).read().strip() == today:
            print(f"[EMAIL] Already sent for {today}, skipping.")
            return "already_sent"
        send_at = now.replace(hour=SEND_HOUR, minute=0, second=0, microsecond=0)
        if now < send_at:
            wait = (send_at - now).total_seconds()
            print(f"[EMAIL] Sleeping {wait:.0f}s until 11:00 London.")
            sleep(wait)
            now = send_at

    data = load_data()
    stale = now - data["newest"] > STALE_AFTER
    if stale:
        subject, html_body, text_body = build_stale(data, now)
    else:
        subject, html_body, text_body, sections = build_email(data, now)
    print(f"[EMAIL] Subject: {subject}")

    if args.dry_run:
        os.makedirs(os.path.dirname(PREVIEW), exist_ok=True)
        with open(PREVIEW, "w", encoding="utf-8") as f:
            f.write(html_body)
        if not stale:
            print(f"[EMAIL] Sections: bullets={len(sections['bullets'])} signal={1 if sections['signal'] else 0} "
                  f"radar={len(sections['radar'])} hits={len(sections['hits'])}")
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
