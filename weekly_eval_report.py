#!/usr/bin/env python3
"""Weekly agent check: one-page Langfuse evaluation email for scout-daily and agent-starter. Mondays 08:00 UTC.

Reads only the Langfuse v2 API (/api/public/v2/metrics, /api/public/v2/observations). Sends through
send_daily_email.smtp_send (Gmail SMTP: GMAIL_USER, GMAIL_APP_PASSWORD, REPORT_EMAIL). No LLM calls.

Usage: python weekly_eval_report.py [--dry-run]
"""
import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from send_daily_email import C, FONT, _e, clean_header, smtp_send

PREVIEW = "email/weekly_eval_preview.html"
DEFAULT_HOST = "https://cloud.langfuse.com"
# (project, public key env, secret key env). Both projects live on the same Langfuse host (LANGFUSE_HOST).
PROJECTS = [("scout-daily", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"),
            ("agent-starter", "LANGFUSE_AS_PUBLIC_KEY", "LANGFUSE_AS_SECRET_KEY")]
RANK = {"FIX": 2, "WATCH": 1, "OK": 0, "IDLE": 0}
BADGE = {"FIX": "#ef4444", "WATCH": "#f59e0b", "OK": C["green"], "IDLE": C["muted"], "NOT CONNECTED": C["muted"]}


# ---------- Langfuse v2 ----------

def _get(host, auth, path, params):
    req = urllib.request.Request(f"{host}{path}?{urllib.parse.urlencode(params)}",
                                 headers={"Authorization": f"Basic {auth}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, json.load(r)


def _iso(t):
    return t.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def fetch_week(host, auth, frm, to, log):
    """Runs per trace name, avg of each numeric score, and ERROR observation count for [frm, to).
    The v2 Metrics API has no traces view; a run is a root observation (isRootObservation), as in langfuse_proxy.py."""
    def metrics(label, view, measure, agg, dims=(), filters=()):
        q = {"view": view, "dimensions": [{"field": d} for d in dims], "metrics": [{"measure": measure, "aggregation": agg}],
             "filters": list(filters), "fromTimestamp": _iso(frm), "toTimestamp": _iso(to), "config": {"row_limit": 1000}}
        status, body = _get(host, auth, "/api/public/v2/metrics", {"query": json.dumps(q)})
        log(f"{label} {frm:%Y-%m-%d}..{to:%Y-%m-%d}: HTTP {status}")
        return body.get("data", [])

    roots = [{"column": "isRootObservation", "operator": "=", "value": True, "type": "boolean"}]
    errors = [{"column": "level", "operator": "=", "value": "ERROR", "type": "string"}]
    runs = {r.get("traceName") or "(unnamed)": int(r["count_count"])
            for r in metrics("runs", "observations", "count", "count", ["traceName"], roots)}
    scores = {r["name"]: float(r["avg_value"]) for r in metrics("scores", "scores-numeric", "value", "avg", ["name"])
              if r.get("avg_value") is not None}
    err = metrics("errors", "observations", "count", "count", filters=errors)
    return {"runs": runs, "scores": scores, "errors": int(err[0]["count_count"]) if err else 0}


def project_link(host, auth):
    """Deep link to the project; the bare host when the project has no observations yet."""
    _, body = _get(host, auth, "/api/public/v2/observations", {"limit": 1})
    pid = (body.get("data") or [{}])[0].get("projectId")
    return f"{host}/project/{pid}" if pid else host


# ---------- verdict ----------

def _f(x):
    return "n/a" if x is None else f"{x:.2f}"


def verdict(project, cur, prev):
    """(verdict, reasons). Fixed rules: FIX, then WATCH, else OK; agent-starter with 0 runs is IDLE, not FIX."""
    runs, s, p = sum(cur["runs"].values()), cur["scores"], prev["scores"]
    if project == "agent-starter" and runs == 0:
        return "IDLE", ["agent-starter had 0 runs"]
    jpr, jerr = s.get("judge_pass_rate"), s.get("judge_error")
    rules = [
        ("FIX", project == "scout-daily" and runs < 5, f"scout-daily had {runs} runs (under 5)"),
        ("FIX", jpr is not None and jpr < 0.6, f"judge_pass_rate avg {_f(jpr)} is under 0.6"),
        ("FIX", jerr is not None and jerr > 0.2, f"judge_error avg {_f(jerr)} is over 0.2"),
        ("WATCH", jpr is not None and 0.6 <= jpr <= 0.8, f"judge_pass_rate avg {_f(jpr)} is between 0.6 and 0.8"),
        *(("WATCH", True, f"{k} dropped {p[k] - s[k]:.2f} vs last week ({_f(p[k])} to {_f(s[k])})")
          for k in sorted(s) if k in p and p[k] - s[k] > 0.10),
        ("WATCH", cur["errors"] > 0, f"{cur['errors']} observations logged level ERROR"),
    ]
    for level in ("FIX", "WATCH"):
        hits = [why for lv, fired, why in rules if lv == level and fired]
        if hits:
            return level, hits
    return "OK", ["no rule fired"]


def plain_line(r):
    v, reasons = r["verdict"], r.get("reasons", [])
    if v == "NOT CONNECTED":
        return f"Not connected: {r['why']}."
    if v == "IDLE":
        return "No runs this week, so there is nothing to score."
    if v == "OK":
        return f"All good: {r['total']} runs, no errors, no score dropped more than 0.10."
    lead = "Needs a fix" if v == "FIX" else "Worth watching"
    return f"{lead}: {'; '.join(reasons)}."


def build_report(project, pub_env, sec_env, host, now, log=print):
    pub, sec = os.getenv(pub_env, "").strip(), os.getenv(sec_env, "").strip()
    if not pub or not sec:
        r = {"project": project, "verdict": "NOT CONNECTED", "why": f"{pub_env} / {sec_env} not set", "link": host}
        log(f"[WEEKLY] {project}: Not connected ({r['why']})")
        return r
    auth = base64.b64encode(f"{pub}:{sec}".encode()).decode()
    plog = lambda m: log(f"[WEEKLY] {project} {m}")
    try:
        cur = fetch_week(host, auth, now - timedelta(days=7), now, plog)
        prev = fetch_week(host, auth, now - timedelta(days=14), now - timedelta(days=7), plog)
        link = project_link(host, auth)
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        why = f"Langfuse HTTP {e.code}" if isinstance(e, urllib.error.HTTPError) else f"Langfuse read failed ({type(e).__name__})"
        log(f"[WEEKLY] {project}: Not connected ({why})")
        return {"project": project, "verdict": "NOT CONNECTED", "why": why, "link": host}
    v, reasons = verdict(project, cur, prev)
    return {"project": project, "verdict": v, "reasons": reasons, "cur": cur, "prev": prev, "link": link,
            "total": sum(cur["runs"].values()), "prev_total": sum(prev["runs"].values())}


# ---------- render ----------

def arrow(cur, prev, digits=2):
    """'▲ +0.05' / '▼ -3' / '= 0' vs last week, or 'new' when last week had no value."""
    if prev is None:
        return "new"
    d = round(cur - prev, digits)
    num = f"{d:+.{digits}f}" if digits else f"{d:+.0f}"
    return f"▲ {num}" if d > 0 else f"▼ {num}" if d < 0 else "= 0"


def worst(reports):
    ranked = [r["verdict"] for r in reports if r["verdict"] in RANK]
    return max(ranked, key=RANK.get) if ranked else "NOT CONNECTED"


def _row(cells, head=False):
    tag, color = ("th", C["text2"]) if head else ("td", C["text"])
    style = f'style="padding:4px 8px;text-align:left;font:{"600 12px" if head else "13px"} {FONT};color:{color};"'
    return "<tr>" + "".join(f"<{tag} {style}>{_e(str(c))}</{tag}>" for c in cells) + "</tr>"  # str(): _e drops 0


def project_rows(r):
    """(label, this week, last week, change) rows: total runs, runs per trace name, errors, each score avg."""
    if "cur" not in r:
        return []
    cur, prev = r["cur"], r["prev"]
    rows = [("Runs (total)", r["total"], r["prev_total"], arrow(r["total"], r["prev_total"], 0))]
    rows += [(f"Runs: {n}", c, prev["runs"].get(n, 0), arrow(c, prev["runs"].get(n, 0), 0))
             for n, c in sorted(cur["runs"].items())]
    rows += [("Errors (level ERROR)", cur["errors"], prev["errors"], arrow(cur["errors"], prev["errors"], 0))]
    rows += [(f"Score avg: {k}", _f(v), _f(prev["scores"].get(k)), arrow(v, prev["scores"].get(k)))
             for k, v in sorted(cur["scores"].items())]
    return rows


def render_html(reports, subject, now):
    blocks = []
    for r in reports:
        badge = (f'<span style="background:{BADGE[r["verdict"]]};color:#0f1117;font:700 12px {FONT};'
                 f'padding:2px 8px;border-radius:4px;">{_e(r["verdict"])}</span>')
        rows = project_rows(r)
        table = (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:10px;">\n'
                 + _row(["", "This week", "Last week", "Change"], head=True) + "\n"
                 + "\n".join(_row(row) for row in rows) + "\n</table>") if rows else ""
        blocks.append(
            f'<tr><td bgcolor="{C["surface"]}" style="background:{C["surface"]};padding:18px 24px;border-top:1px solid {C["border"]};">\n'
            f'<div style="font:700 18px {FONT};color:{C["text"]};">{_e(r["project"])} {badge}</div>\n'
            f'<p style="margin:8px 0 0;font:14px/1.5 {FONT};color:{C["text2"]};">{_e(plain_line(r))}</p>\n'
            f'{table}\n'
            f'<p style="margin:10px 0 0;font:13px {FONT};"><a href="{_e(r["link"])}" style="color:{C["accent"]};'
            f'text-decoration:none;">Open {_e(r["project"])} in Langfuse →</a></p>\n</td></tr>')
    period = f"{now - timedelta(days=7):%d %b} to {now:%d %b %Y} (UTC), compared with the 7 days before"
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<meta name="color-scheme" content="dark light"><title>{_e(subject)}</title></head>
<body style="margin:0;padding:0;background:{C["bg"]};" bgcolor="{C["bg"]}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="{C["bg"]}" style="background:{C["bg"]};"><tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:100%;max-width:600px;border:1px solid {C["border"]};border-radius:8px;">
<tr><td bgcolor="{C["surface2"]}" style="background:{C["surface2"]};padding:24px;">
<div style="font:700 22px {FONT};color:{C["text"]};">Weekly agent check</div>
<div style="font:13px {FONT};color:{C["text2"]};margin-top:4px;">{_e(period)}</div></td></tr>
{chr(10).join(blocks)}
<tr><td bgcolor="{C["bg"]}" style="background:{C["bg"]};padding:16px 24px;font:12px/1.5 {FONT};color:{C["muted"]};">
FIX: scout-daily under 5 runs, judge_pass_rate under 0.6, or judge_error over 0.2. WATCH: judge_pass_rate 0.6 to 0.8,
any score down more than 0.10, or any ERROR observation. Source: Langfuse v2 Metrics API.</td></tr>
</table></td></tr></table></body></html>
"""


def render_text(reports, subject):
    lines = [subject, ""]
    for r in reports:
        lines += [f"{r['project'].upper()}: {r['verdict']}", plain_line(r)]
        lines += [f"- {label}: {a} (last week {b}, {chg})" for label, a, b, chg in project_rows(r)]
        lines += [r["link"], ""]
    return "\n".join(lines)


def build_email(reports, now):
    subject = f"Weekly agent check - {now:%Y-%m-%d}: {worst(reports)}"
    return subject, render_html(reports, subject, now), render_text(reports, subject)


def main(argv=None, now=None, send=smtp_send):
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--dry-run", action="store_true", help=f"write {PREVIEW}, send nothing")
    args = args.parse_args(argv)
    now = now or datetime.now(timezone.utc)
    host = (os.getenv("LANGFUSE_HOST") or DEFAULT_HOST).strip().rstrip("/")
    reports = [build_report(p, pub, sec, host, now) for p, pub, sec in PROJECTS]
    for r in reports:
        if "cur" in r:
            c = r["cur"]
            print(f"[WEEKLY] {r['project']}: {r['verdict']} runs={r['total']} {c['runs']} errors={c['errors']} "
                  f"scores={ {k: round(v, 4) for k, v in c['scores'].items()} }")
            print(f"[WEEKLY] {r['project']}: rules fired: {'; '.join(r['reasons'])}")
    subject, html_body, text_body = build_email(reports, now)
    print(f"[WEEKLY] Subject: {clean_header(subject)}")
    if args.dry_run:
        os.makedirs(os.path.dirname(PREVIEW), exist_ok=True)
        with open(PREVIEW, "w", encoding="utf-8") as f:
            f.write(html_body)
        print(f"[WEEKLY] Dry run: wrote {PREVIEW}, nothing sent.")
        return "preview"
    print(f"[WEEKLY] SMTP: {send(subject, html_body, text_body)}")
    return "sent"


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[WEEKLY] Failed: {e}", file=sys.stderr)
        sys.exit(1)
