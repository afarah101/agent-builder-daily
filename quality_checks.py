#!/usr/bin/env python3
"""Deterministic quality checks for Scout Daily items. Each check returns (PASS|FAIL|FLAG, reason).
C7-C10 are the grounding checks: generated text states only what its source states.

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
    if r.status_code in (404, 410):
        return FAIL, f"HTTP {r.status_code}"
    # 401/403/429/5xx: bot walls and outages, not proof the link is dead.
    return FLAG, f"HTTP {r.status_code}"


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


# ---------- Grounding: generated text states only what its source states ----------
# A claim is a number, a percentage, a currency amount, a date, an ID (CVE and the like) or a product name with a
# number in it. Every claim in generated text must be in the item's source text. Nothing here calls a model.

_CHARS = {**dict.fromkeys(map(ord, "‐‑‒–—−"), "-"), **dict.fromkeys(map(ord, "    "), " "),
          ord("’"): "'", ord("‘"): "'"}
_NUM = r"\d[\d,]*(?:\.\d+)?"
_MON = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
_MONTHS = {m: n for n, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}
_SCALE = {"k": "thousand", "thousand": "thousand", "m": "million", "mn": "million", "million": "million",
          "b": "billion", "bn": "billion", "billion": "billion", "trillion": "trillion", "tn": "trillion"}
_SCALE_VALUE = {"thousand": 10 ** 3, "million": 10 ** 6, "billion": 10 ** 9, "trillion": 10 ** 12}
_CURRENCY = {"£": "GBP", "gbp": "GBP", "pound": "GBP", "pounds": "GBP", "$": "USD", "usd": "USD", "dollar": "USD",
             "dollars": "USD", "€": "EUR", "eur": "EUR", "euro": "EUR", "euros": "EUR"}
_WORD_NUM = {"three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
             "twelve": 12}
_ALL_WORD_NUM = {"one": 1, "two": 2, **_WORD_NUM}
_SCALE_WORDS = ("hundred", "thousand", "million", "billion", "trillion")
_TIME_UNIT = r"(?:seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?|months?|quarters?|years?|yrs?)"

_ID = re.compile(r"\b(?:CVE-\d{4}-[0-9A-Za-z]+|CWE-\d+|GHSA(?:-[0-9a-z]{4}){3}|RFC ?\d{3,5}|KB\d{6,8}|arXiv: ?\d{4}\.\d{4,5})\b", re.I)
# Names the reader already knows; the digits in them are not claims about the source.
_KNOWN_NAME = re.compile(
    r"\b(?:(?:Microsoft|Office|Dynamics|M|O) ?365|24/7|ISO(?:/IEC)? ?(?:27001|27002|27701|42001|9001|22301)|SOC ?[12]|NIST (?:SP )?800-\d+|OAuth ?2(?:\.0)?"
    r"|TLS ?1\.[0-3]|HTTP/?[123](?:\.\d)?|Windows 1[01]|Web ?[23](?:\.0)?|OWASP Top 10|802\.1[1x]\w*|FIDO2|WPA[23]"
    r"|SHA-?(?:1|256|512)|AES-?(?:128|256)|x86(?:[-_]64)?|x64|arm64|IPv[46]|S3|EC2|K8s|Log4j|UTF-?8|[2-5]G|[23]D|[48]K"
    r"|2FA|B2B|B2C|P2P|E2E|Q[1-4]|L[1-3] support|H[1-6]|F1(?: score)?|[45]xx)\b", re.I)
_DATE_PATTERNS = (  # each returns groups read by _date_key
    (re.compile(r"\b(?P<y>(?:19|20)\d\d)-(?P<m>\d\d)-(?P<d>\d\d)\b"), None),
    (re.compile(rf"\b(?P<d>\d{{1,2}})(?:st|nd|rd|th)? (?P<mon>{_MON})\b\.?,?(?: (?P<y>(?:19|20)\d\d)\b)?"), None),
    (re.compile(rf"\b(?P<mon>{_MON})\b\.? (?P<d>\d{{1,2}})(?:st|nd|rd|th)?\b(?:,? (?P<y>(?:19|20)\d\d)\b)?"), None),
    (re.compile(rf"\b(?P<mon>{_MON})\b\.?,? (?P<y>(?:19|20)\d\d)\b"), None),
)
_MONEY = (
    re.compile(rf"(?P<cur>[£$€]) ?(?P<num>{_NUM})(?: ?(?P<scale>k|m|mn|bn|b|tn|thousand|million|billion|trillion)\b)?", re.I),
    re.compile(rf"(?P<num>{_NUM})(?: ?(?P<scale>k|m|mn|bn|b|tn|thousand|million|billion|trillion))? ?"
               r"(?P<cur>£|\$|€|\b(?:USD|GBP|EUR|dollars?|pounds?|euros?)\b)", re.I),
)
_PCT = re.compile(rf"(?:(?P<lo>{_NUM}) ?(?:-|to) ?)?(?P<num>{_NUM}) ?(?:%|percent\b|per cent\b|percentage points?\b)", re.I)
_DURATION = re.compile(rf"\b\d+(?:\.\d+)?(?: ?(?:-|to|/) ?\d+(?:\.\d+)?)*[ -]?{_TIME_UNIT}\b", re.I)
# "1.", "2)", "(3)", "S1:", "step 2", and a line that starts "3 Identify ..." or "1.4 Assess ..."
_LIST_MARK = re.compile(r"(?m)^[ \t]*(?:[-•*] ?)?\(?\d{1,2}(?:\.\d{1,2})?(?:[.)](?=\s)|(?= [A-Z][a-z]))|\(\d{1,2}\)|\bS[1-3]:|\b[Ss]tep \d{1,2}\b")
# A letter comes first: GPT-5.5, FP32, Llama-3, v2.1, gpt-oss-20b. Checked as a name, not as a number.
_ALNUM_NAME = re.compile(r"\b[A-Za-z]+(?:[-./][A-Za-z]+)*[-.]?\d\w*(?:[-.]\d+\w*)*")
_PLAIN = re.compile(rf"(?<![\w.])(?P<num>{_NUM})(?P<scale>k|m|bn|b)?(?![A-Za-z]*\d)", re.I)
_NUMBER_WORD = re.compile(r"\b(?:" + "|".join([*_WORD_NUM, *(w + "s?" for w in _SCALE_WORDS), "dozens?"]) + r")\b", re.I)


def _norm(text):
    return str(text or "").translate(_CHARS)


def _value(num, scale=None):
    """A number as a canonical string: '10,000' -> '10000', '5.50' -> '5.5', ('10', 'k') -> '10000'."""
    num = num.replace(",", "").rstrip(".")
    try:
        v = float(num) * _SCALE_VALUE.get(_SCALE.get((scale or "").lower(), ""), 1)
    except ValueError:
        return num
    return str(int(v)) if v == int(v) else repr(round(v, 6))


def _date_key(m):
    g = m.groupdict()
    month = _MONTHS[g["mon"][:3].lower()] if g.get("mon") else int(g["m"])
    return (int(g["y"]) if g.get("y") else None, month, int(g["d"]) if g.get("d") else None)


def _blank(text, m):
    return text[:m.start()] + " " * (m.end() - m.start()) + text[m.end():]


def _compact(text):
    return re.sub(r"[\s\-./]", "", text.lower())


def _money_key(m):
    return (_CURRENCY[m["cur"].lower()], _value(m["num"]), _SCALE.get((m["scale"] or "").lower(), ""))


def claims(text, action=False):
    """Every claim in text as (kind, key, as written). kind: id, date, money, pct, name, number, word.
    action=True is "how to apply" text: a time frame there ("within 30 days") is advice, not a claim, and so is a
    spelled-out number. List markers ("1.", "S1:") and names the reader knows ("Microsoft 365") are never claims."""
    work, out = _norm(text), []
    for m in _ID.finditer(work):
        out.append(("id", _compact(m[0]), m[0]))
    work = _ID.sub(lambda m: " " * len(m[0]), work)
    work = _KNOWN_NAME.sub(lambda m: " " * len(m[0]), work)
    for pattern, _ in _DATE_PATTERNS:
        for m in list(pattern.finditer(work)):
            out.append(("date", _date_key(m), m[0]))
            work = _blank(work, m)
    for pattern in _MONEY:
        for m in list(pattern.finditer(work)):
            out.append(("money", _money_key(m), m[0].strip()))
            work = _blank(work, m)
    for m in list(_PCT.finditer(work)):
        for num in filter(None, (m["lo"], m["num"])):
            out.append(("pct", _value(num), m[0].strip()))
        work = _blank(work, m)
    if action:
        work = _DURATION.sub(lambda m: " " * len(m[0]), work)
    work = _LIST_MARK.sub(lambda m: " " * len(m[0]), work)
    for m in list(_ALNUM_NAME.finditer(work)):
        out.append(("name", _compact(m[0]), m[0]))
        work = _blank(work, m)
    for m in _PLAIN.finditer(work):
        out.append(("number", _value(m["num"], m["scale"]), m[0]))
    if not action:
        for m in _NUMBER_WORD.finditer(work):
            out.append(("word", m[0].lower().rstrip("s") if m[0].lower().rstrip("s") in _SCALE_WORDS + ("dozen",) else m[0].lower(), m[0]))
    return out


def _source_facts(source):
    """What a source states, in the forms claims() keys use."""
    src = _norm(source)
    facts = {"compact": _compact(src), "words": set(re.findall(r"[a-z]+", src.lower())), "dates": [], "money": set(),
             "pct": set(), "numbers": set()}
    for pattern, _ in _DATE_PATTERNS:
        facts["dates"] += [_date_key(m) for m in pattern.finditer(src)]
    for pattern in _MONEY:
        facts["money"] |= {_money_key(m) for m in pattern.finditer(src)}
    for m in _PCT.finditer(src):
        facts["pct"] |= {_value(n) for n in (m["lo"], m["num"]) if n}
    for m in re.finditer(rf"(?P<num>{_NUM})(?: ?(?P<scale>k|m|mn|bn|b|tn|thousand|million|billion|trillion)\b)?", src, re.I):
        facts["numbers"] |= {_value(m["num"]), _value(m["num"], m["scale"])}
    facts["numbers"] |= {str(n) for w, n in _ALL_WORD_NUM.items() if w in facts["words"]}
    return facts


def _in_source(kind, key, facts):
    if kind == "id":
        return key in facts["compact"]
    if kind == "date":  # every part the claim gives must match one date in the source
        return any(all(c is None or c == s for c, s in zip(key, src)) for src in facts["dates"])
    if kind == "money":
        return key in facts["money"]
    if kind == "pct":
        return key in facts["pct"]
    if kind == "name":
        return len(key) >= 2 and key in facts["compact"]
    if kind == "number":
        return key in facts["numbers"]
    if key in _WORD_NUM:  # "three" is in the source as the word or as the digit
        return key in facts["words"] or str(_WORD_NUM[key]) in facts["numbers"]
    return key in facts["words"] or key + "s" in facts["words"]  # hundred, thousand, million, dozen


def ungrounded(text, source, action=False):
    """Claims in text that the source does not state, as written. [] means the text is grounded."""
    facts, missing = _source_facts(source), []
    for kind, key, shown in claims(text, action):
        if not _in_source(kind, key, facts) and shown not in missing:
            missing.append(shown)
    return missing


PLACEHOLDER = re.compile(r"x{4,}|\bTB[DAC]\b|lorem|\[insert|\[placeholder|\[your |\[link\]|\[date\]|\[name\]|example\.(?:com|org|net)"
                         r"|\bto be (?:determined|announced|confirmed)\b|\{\{|<placeholder", re.I)


def placeholders(text):
    """Placeholder text left in generated copy: xxxx, TBD, lorem, [insert, example.com. [] means none."""
    found = []
    for m in PLACEHOLDER.finditer(_norm(text)):
        if m[0] not in found:
            found.append(m[0])
    return found


# Words that mark a forward view. One the source never uses is the writer's own guess, so it needs the "Our take:" label.
_FORWARD = re.compile(r"\b(?:[Ll]ikely|[Uu]nlikely|[Cc]ould|may|[Mm]ight|[Ee]xpected to|[Pp]oised|[Pp]otentially|[Pp]ossibly|[Pp]robably"
                      r"|[Aa]nticipate[sd]?|[Ss]ignals?|[Ss]uggests?|(?:is|are) set to|in the coming (?:weeks|months|years))\b")
TAKE_LABEL = "Our take:"


def unlabelled_forward_views(text, source):
    """Forward-view words in text that the source never uses and that are not inside an "Our take:" passage.
    A passage runs from the label to the end of its paragraph. [] means none."""
    src, found = _norm(source).lower(), []
    for para in _norm(text).split("\n"):
        at = para.lower().find(TAKE_LABEL.lower())
        for m in _FORWARD.finditer(para if at < 0 else para[:at]):
            word = m[0].lower()
            if not re.search(rf"\b{re.escape(word)}\b", src) and word not in found:
                found.append(word)
    return found


_LISTING_WORDS = {"news", "announcements", "blog", "blogs", "index", "press", "newsroom", "insights", "articles", "posts",
                  "updates", "stories", "research", "resources", "events", "feed", "rss", "home", "latest", "archive",
                  "press-releases", "category", "categories", "tag", "tags", "topics"}


def is_listing_url(url):
    """True when the URL is a listing page (/news, /announcements, /blog, /index), not one article."""
    from urllib.parse import urlsplit
    try:
        parts = [p for p in urlsplit(str(url or "").strip()).path.lower().split("/") if p]
    except ValueError:
        return False
    if not parts:
        return False  # a bare domain is a home page: C9 flags it and does not hold it back
    last = re.sub(r"\.(?:html?|php|aspx?)$", "", parts[-1])
    if last in _LISTING_WORDS:
        return True
    if len(parts) >= 2 and parts[-2] in ("category", "categories", "tag", "tags", "topics", "author", "page"):
        return True  # /category/ai, /tag/security, /page/2
    return False


def source_text(item):
    """The text an item's claims are checked against: title, stored source (full article, abstract or feed snippet),
    its own date, publisher and authors."""
    body = item.get("source_text") or item.get("abstract") or item.get("summary_raw") or ""
    authors = item.get("authors") if isinstance(item.get("authors"), list) else []
    return "\n".join(str(p) for p in (item.get("title"), body, item.get("pub_date"), item.get("source"), *authors) if p)


def _summary_and_actions(item):
    """(summary, [action text]). "Try this" steps are not here: a bad step drops the steps, not the story (see
    step_problems and judge.gate)."""
    action = item.get("enterprise_use") if kind(item) == "news" else item.get("it_breakdown")
    return str(item.get("summary") or ""), [str(action)] if action else []


def step_problems(steps, source):
    """Why "try this" steps are not grounded: claims the source does not state, or placeholder text. [] means none."""
    text = "\n".join(str(s) for s in steps or [])
    return ungrounded(text, source, action=True) + placeholders(text)


def grounding_problems(item):
    """Why an item's generated text is not grounded in its source, as short strings. [] means it passes C7, C8, C10."""
    return [f"{name}: {reason}" for name, fn in (("C7", c7_grounded), ("C8", c8_placeholders), ("C10", c10_forward_view))
            for result, reason in [fn(item)] if result == FAIL]


def c7_grounded(item):
    summary, actions = _summary_and_actions(item)
    source = source_text(item)
    missing = ungrounded(summary, source) + [m for a in actions for m in ungrounded(a, source, action=True)]
    missing = list(dict.fromkeys(missing))
    return (FAIL, "not in source: " + ", ".join(missing[:8])) if missing else (PASS, "")


def c8_placeholders(item):
    summary, actions = _summary_and_actions(item)
    found = placeholders("\n".join([str(item.get("title") or ""), summary, *actions]))
    return (FAIL, "placeholder text: " + ", ".join(found)) if found else (PASS, "")


def c9_source_url(item):
    from urllib.parse import urlsplit
    url = str(item.get("url") or "")
    if is_listing_url(url):
        return FAIL, f"listing page, not an article: {url}"
    try:
        bare = bool(url) and not urlsplit(url).path.strip("/")
    except ValueError:
        bare = False
    return (FLAG, f"home page, not an article: {url}") if bare else (PASS, "")


def c10_forward_view(item):
    found = unlabelled_forward_views(str(item.get("summary") or ""), source_text(item))
    return (FAIL, f'forward view without "{TAKE_LABEL}": ' + ", ".join(found)) if found else (PASS, "")


CHECKS = [("C1", c1_score_range), ("C2", c2_not_all_zero), ("C3", c3_required_fields),
          ("C4", c4_date_window), ("C5", c5_url_live), ("C6", c6_company_in_summary),
          ("C7", c7_grounded), ("C8", c8_placeholders), ("C9", c9_source_url), ("C10", c10_forward_view)]


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
