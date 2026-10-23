"""Gemini judge: is each item's summary faithful to its source? Items are judged in batches (one call per 25 items);
items that fail, or that the judge can't score, are held back."""
import json
import os
import time
from collections import Counter

# Newest Flash that answers reliably on this key (2026-10-23): 3.8 and 3.7 hit 504 DEADLINE_EXCEEDED on every try.
JUDGE_MODEL = "gemini-3.6-flash"
PASS_SCORE = 0.7
BATCH_SIZE = 25
MAX_PER_MINUTE = 11  # stay under 12 calls a minute (free-tier headroom)
RETRY_DELAY = 10

RULES = """FAIL an item if any of these is true:
1. The summary states a fact that is not in the source (invented results, numbers, names, dates or claims).
2. The summary names different companies or products from the title, or swaps one company or product for another.
3. The action text invents features, settings or facts about the product or company in the source. The action MAY name
   the reader's own IT stack (Microsoft 365, Google Workspace, Okta, JumpCloud, AWS, Azure, SSO, endpoint management)
   as the place to act; that alone is not a fail.
4. Anything in the summary or action contradicts the title.

Otherwise PASS. Be strict: a single invented fact or a swapped name is a fail."""

BATCH_PROMPT = """You are a strict fact checker for a daily briefing. For EACH item below, judge whether its summary and
action are faithful to its source. Judge every item on its own.

{rules}

ITEMS (JSON list; each has id, title, source, summary, action):
{items}

Return JSON only, with exactly one verdict per item id:
{{"verdicts": [{{"id": "<item id>", "verdict": "pass" or "fail", "score": <0.0-1.0, how faithful>, "reason": "<one sentence naming the rule broken, or why it passes>"}}]}}"""

clock, sleep = time.monotonic, time.sleep  # patched in tests
_calls = []
_client = None


def _pace():
    """Sliding 60 s window: block until fewer than MAX_PER_MINUTE calls were made in the last minute."""
    while True:
        now = clock()
        _calls[:] = [t for t in _calls if now - t < 60]
        if len(_calls) < MAX_PER_MINUTE:
            _calls.append(now)
            return
        sleep(60 - (now - _calls[0]) + 0.01)


def _ask(prompt):
    global _client
    from google import genai
    from google.genai import types
    if _client is None:
        # SDK retries off: _judge_chunk owns the single retry, and pacing counts every real request.
        _client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"), http_options=types.HttpOptions(
            timeout=120_000, retry_options=types.HttpRetryOptions(attempts=1)))
    resp = _client.models.generate_content(model=JUDGE_MODEL, contents=prompt, config=types.GenerateContentConfig(
        temperature=0, response_mime_type="application/json"))
    return resp.text


def _error(reason):
    return {"verdict": "error", "score": 0.0, "reason": reason}


def _retryable(e):
    code = getattr(e, "code", None)
    return isinstance(code, int) and (code == 429 or code >= 500)


def _parse_verdict(v):
    verdict, score = v.get("verdict"), float(v.get("score"))
    if verdict not in ("pass", "fail") or not 0.0 <= score <= 1.0:
        raise ValueError(f"bad verdict {v!r}")
    return {"verdict": verdict, "score": score, "reason": str(v.get("reason", ""))}


def _judge_chunk(chunk):
    """One Gemini call for up to BATCH_SIZE items. Returns {id: result}; anything unmatched or malformed is an error."""
    prompt = BATCH_PROMPT.format(rules=RULES, items=json.dumps(chunk, ensure_ascii=False, indent=1))
    raw = None
    for attempt in range(2):
        try:
            _pace()
            raw = _ask(prompt)
            break
        except Exception as e:
            if attempt == 0 and _retryable(e):
                print(f"[JUDGE] call failed ({type(e).__name__}: {str(e)[:120]}), retrying in {RETRY_DELAY}s")
                sleep(RETRY_DELAY)
                continue
            return {it["id"]: _error(f"judge call failed: {type(e).__name__}: {str(e)[:200]}") for it in chunk}
    try:
        verdicts = json.loads(raw)["verdicts"]
        if not isinstance(verdicts, list):
            raise ValueError("verdicts is not a list")
    except Exception as e:
        return {it["id"]: _error(f"unparseable judge response: {type(e).__name__}: {str(e)[:120]}") for it in chunk}
    wanted = {it["id"] for it in chunk}
    counts = Counter(str(v.get("id")) for v in verdicts if isinstance(v, dict))
    out = {}
    for v in verdicts:
        vid = str(v.get("id")) if isinstance(v, dict) else None
        if vid in wanted and counts[vid] == 1:  # extra ids are ignored; duplicated ids count for nothing
            try:
                out[vid] = _parse_verdict(v)
            except (TypeError, ValueError):
                pass
    return {i: out.get(i, _error("missing, duplicate or malformed verdict")) for i in wanted}


def judge_batch(items):
    """items: [{"id", "title", "source", "summary", "action"}]. Returns {id: {"verdict", "score", "reason"}}."""
    results = {}
    for start in range(0, len(items), BATCH_SIZE):
        results.update(_judge_chunk(items[start:start + BATCH_SIZE]))
    return results


def passed(result):
    return result["verdict"] == "pass" and result["score"] >= PASS_SCORE


def gate(items, title_key, source_key, summary_key, action_key, log=print):
    """Judge every item in batches. Returns (kept, held_back, stats); only passes with score >= PASS_SCORE are kept."""
    batch = [{"id": str(i), "title": it.get(title_key) or "", "source": it.get(source_key) or "",
              "summary": it.get(summary_key) or "", "action": it.get(action_key) or ""} for i, it in enumerate(items)]
    results = judge_batch(batch) if batch else {}
    kept, held, counts = [], [], {"pass": 0, "fail": 0, "error": 0}
    for i, item in enumerate(items):
        r = results.get(str(i), _error("no verdict"))
        ok = passed(r)
        counts["pass" if ok else ("error" if r["verdict"] == "error" else "fail")] += 1
        if ok:
            kept.append(item)
        else:
            held.append(item)
            log(f"[JUDGE] HELD BACK {(item.get(title_key) or '?')[:70]}: {r['verdict']} {r['score']:.2f} - {r['reason']}")
    total = len(items)
    stats = {"total": total, "passed": counts["pass"], "failed": counts["fail"], "errors": counts["error"],
             "pass_rate": counts["pass"] / total if total else 1.0}
    log(f"[JUDGE] {counts['pass']} passed, {counts['fail']} failed, {counts['error']} errors (of {total})")
    return kept, held, stats


def send_scores(client, stats, log=print):
    """judge_pass_rate, judge_fail and judge_error on the current Langfuse trace."""
    try:
        client.score_current_trace(name="judge_pass_rate", value=round(stats["pass_rate"], 4),
                                   comment=f"{stats['passed']}/{stats['total']} items passed the judge")
        client.score_current_trace(name="judge_fail", value=stats["failed"])
        client.score_current_trace(name="judge_error", value=stats["errors"])
    except Exception as e:
        log(f"[JUDGE] Langfuse score failed: {e}")
