#!/usr/bin/env python3
"""Deterministic quality checks for Scout Daily items. Each check returns (PASS|FAIL|FLAG, reason).

CLI: python quality_checks.py [--days N] [--no-network] [files...]
"""
import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import requests

PASS, FAIL, FLAG = "PASS", "FAIL", "FLAG"
CS_SCORES = ("real_world_impact", "ai_maturity", "monetisation_potential")
CS_MAX, NEWS_MAX = 25, 10
CS_WINDOW, NEWS_WINDOW = timedelta(days=120), timedelta(weeks=8)  # match the scouts' fetch windows
UA = {"User-Agent": "Mozilla/5.0 (compatible; ScoutDailyQualityCheck/1.0)"}


def kind(item):
    return "news" if "relevance_score" in item else "case_study"


def _score_fields(item):
    return ("relevance_score",) if kind(item) == "news" else CS_SCORES


def _empty(v):
    return v is None or (isinstance(v, str) and not v.strip())


def c1_score_range(item):
    hi = NEWS_MAX if kind(item) == "news" else CS_MAX
    bad = []
    for f in _score_fields(item):
        v = item.get(f)
        if v is None:
            continue  # missing scores are C3's job
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= hi:
            bad.append(f"{f}={v!r}")
    return (FAIL, f"out of 0-{hi}: " + ", ".join(bad)) if bad else (PASS, "")


def c2_not_all_zero(item):
    if kind(item) == "news":
        return PASS, "n/a for news"
    if all(item.get(f) == 0 for f in CS_SCORES):
        return FAIL, "all scores are 0"
    return PASS, ""


def c3_required_fields(item):
    fields = ("title", "summary", "url", "pub_date") + _score_fields(item)
    missing = [f for f in fields if _empty(item.get(f))]
    return (FAIL, "missing/empty: " + ", ".join(missing)) if missing else (PASS, "")


def parse_date(s):
    s = (s or "").strip()
    for fmt, n in (("%Y-%m-%d", 10), ("%d %b %Y", None), ("%d %B %Y", None)):
        try:
            return datetime.strptime(s[:n] if n else s, fmt)
        except ValueError:
            pass
    return None


def c4_date_window(item, now=None):
    now = now or datetime.now()
    dt = parse_date(item.get("pub_date"))
    if dt is None:
        return FAIL, f"unparseable date {item.get('pub_date')!r}"
    window = NEWS_WINDOW if kind(item) == "news" else CS_WINDOW
    if dt < now - window:
        return FAIL, f"{dt:%Y-%m-%d} older than {window.days} days"
    if dt > now + timedelta(days=1):
        return FAIL, f"{dt:%Y-%m-%d} is in the future"
    return PASS, ""


def c5_url_live(item, timeout=10):
    url = item.get("url")
    if _empty(url):
        return FAIL, "no url"
    try:
        r = requests.get(url, headers=UA, timeout=timeout, allow_redirects=False, stream=True)
        r.close()
    except requests.RequestException as e:
        return FLAG, f"network error: {type(e).__name__}"
    if 200 <= r.status_code < 400:
        return PASS, str(r.status_code)
    return FAIL, f"HTTP {r.status_code}"


_NOT_NAMES = {"AI", "LLM", "LLMs", "AGI", "API", "APIs", "GPU", "GPUs", "CEO", "US", "UK", "EU", "The", "A", "An",
              "And", "Or", "Of", "For", "In", "On", "To", "With", "From", "By", "At", "As", "Is", "Are", "How", "Why",
              "What", "When", "New", "Its", "It", "Your", "Our", "This", "That", "Via", "Vs", "Not"}


def c6_company_in_summary(item):
    # shortcut: capitalisation heuristic, not entity recognition; upgrade to NER if FLAG noise matters.
    title, summary = item.get("title") or "", (item.get("summary") or "").lower()
    # Clauses after ':' or a dash start with a capital; hyphen parts ("Multi-Layer") are not names.
    clauses = [re.findall(r"[A-Za-z][A-Za-z0-9&']*", c) for c in re.split(r"[:|?!–—]", title)]
    words = [w for c in clauses for w in c]
    if not words:
        return PASS, ""
    internal_cap = lambda w: any(c.isupper() for c in w[1:]) and not w.isupper()
    title_case = sum(w[0].isupper() for w in words) > 0.6 * len(words)
    names = []
    for clause in clauses:
        for i, w in enumerate(clause):
            if w in _NOT_NAMES or len(w) < 2:
                continue
            if (internal_cap(w) or (not title_case and i > 0 and w[0].isupper())) and w not in names:
                names.append(w)
    missing = [w for w in names if w.lower() not in summary]
    return (FLAG, "not in summary: " + ", ".join(missing)) if missing else (PASS, "")


CHECKS = [("C1", c1_score_range), ("C2", c2_not_all_zero), ("C3", c3_required_fields),
          ("C4", c4_date_window), ("C5", c5_url_live), ("C6", c6_company_in_summary)]


def run_checks(item, network=True):
    out = []
    for name, fn in CHECKS:
        if name == "C5" and not network:
            out.append((name, FLAG, "network check skipped"))
            continue
        result, reason = fn(item)
        out.append((name, result, reason))
    return out


def gate(items, network=True, log=print):
    """Run all checks. Returns (kept, held_back, results); results is a list of (item, [(check, result, reason)])."""
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(zip(items, pool.map(lambda it: run_checks(it, network), items)))
    kept, held = [], []
    for item, checks in results:
        title = (item.get("title") or "?")[:70]
        fails = [c for c in checks if c[1] == FAIL]
        for check, result, reason in checks:
            if result != PASS:
                log(f"[QUALITY] {result} {check} {title}: {reason}")
        (held if fails else kept).append(item)
        if fails:
            log(f"[QUALITY] HELD BACK {title}: " + "; ".join(f"{c} {r}" for c, _, r in fails))
    return kept, held, results


def summarize(results):
    total = len(results)
    passed = sum(1 for _, checks in results if all(r != FAIL for _, r, _ in checks))
    fails = {}
    for item, checks in results:
        for check, result, reason in checks:
            if result == FAIL:
                fails.setdefault(check, []).append(f"{(item.get('title') or '?')[:50]}: {reason}")
    return {"total": total, "passed": passed, "pass_rate": passed / total if total else 1.0, "fails": fails}


def send_scores(client, summary, log=print):
    """Attach quality_pass_rate and one score per failed check to the current Langfuse trace."""
    try:
        client.score_current_trace(name="quality_pass_rate", value=round(summary["pass_rate"], 4),
                                   comment=f"{summary['passed']}/{summary['total']} items passed")
        for check, reasons in summary["fails"].items():
            client.score_current_trace(name=f"quality_fail_{check}", value=len(reasons),
                                       comment="; ".join(reasons)[:1000])
    except Exception as e:
        log(f"[QUALITY] Langfuse score failed: {e}")


def load_items(path, days):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):  # single-run output file
        data = [data]
    items, seen = [], set()
    for day in data[:days]:
        for item in day.get("case_studies") or day.get("news") or []:
            key = item.get("url") or item.get("id") or item.get("title")
            if key not in seen:
                seen.add(key)
                items.append(item)
    return items


def default_files():
    news = "logs/archive.json" if os.path.exists("logs/archive.json") else "site/news_archive.json"
    return ["case_studies/archive.json", news]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run Scout Daily quality checks on output data files.")
    ap.add_argument("files", nargs="*", help="archive or output JSON files (default: the scouts' archives)")
    ap.add_argument("--days", type=int, default=7, help="most recent archive days to check (default 7)")
    ap.add_argument("--no-network", action="store_true", help="skip C5 URL checks")
    ap.add_argument("--all", action="store_true", help="also print PASS rows")
    args = ap.parse_args(argv)
    items = []
    for path in args.files or default_files():
        loaded = load_items(path, args.days)
        print(f"{path}: {len(loaded)} items (last {args.days} days)")
        items += loaded
    _, _, results = gate(items, network=not args.no_network, log=lambda *_: None)
    print(f"\n{'ITEM':<52} {'CHECK':<5} {'RESULT':<6} REASON")
    counts = {PASS: 0, FAIL: 0, FLAG: 0}
    for item, checks in results:
        for check, result, reason in checks:
            counts[result] += 1
            if result != PASS or args.all:
                print(f"{(item.get('title') or '?')[:52]:<52} {check:<5} {result:<6} {reason}")
    s = summarize(results)
    print(f"\nChecks: {counts[PASS]} PASS, {counts[FAIL]} FAIL, {counts[FLAG]} FLAG")
    print(f"Items: {s['passed']}/{s['total']} passed (no FAIL), pass rate {s['pass_rate']:.2f}")
    for check, reasons in sorted(s["fails"].items()):
        print(f"  {check}: {len(reasons)} failed")
    return 1 if s["fails"] else 0


if __name__ == "__main__":
    sys.exit(main())
