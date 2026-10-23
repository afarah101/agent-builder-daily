"""Gemini judge: is an item's summary faithful to its source? Items that fail, or that the judge can't score, are held back."""
import json
import os
import time

# Newest Flash that answers reliably on this key (2026-10-23): 3.8 and 3.7 hit 504 DEADLINE_EXCEEDED on every try.
JUDGE_MODEL = "gemini-3.6-flash"
PASS_SCORE = 0.7
MAX_PER_MINUTE = 11  # stay under 12 calls a minute (free-tier headroom)
RETRY_DELAY = 10

RUBRIC = """You are a strict fact checker for a daily briefing. Judge whether the SUMMARY and ACTION are faithful to the SOURCE.

FAIL if any of these is true:
1. The summary states a fact that is not in the source (invented results, numbers, names, dates or claims).
2. The summary names different companies or products from the title, or swaps one company or product for another.
3. The action text invents features, settings or facts about the product or company in the source. The action MAY name
   the reader's own IT stack (Microsoft 365, Google Workspace, Okta, JumpCloud, AWS, Azure, SSO, endpoint management)
   as the place to act; that alone is not a fail.
4. Anything in the summary or action contradicts the title.

Otherwise PASS. Be strict: a single invented fact or a swapped name is a fail.

TITLE: {title}

SOURCE:
{source}

SUMMARY:
{summary}

ACTION:
{action}

Return JSON only: {{"verdict": "pass" or "fail", "score": <0.0-1.0, how faithful>, "reason": "<one sentence naming the rule broken, or why it passes>"}}"""

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
        # SDK retries off: judge_item owns the single retry, and pacing counts every real request.
        _client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"), http_options=types.HttpOptions(
            timeout=60_000, retry_options=types.HttpRetryOptions(attempts=1)))
    resp = _client.models.generate_content(model=JUDGE_MODEL, contents=prompt, config=types.GenerateContentConfig(
        temperature=0, response_mime_type="application/json"))
    return resp.text


def judge_item(title, source_text, summary, action_text):
    """Returns {"verdict": "pass"|"fail"|"error", "score": 0.0-1.0, "reason": str}. Errors are retried once after 10 s."""
    prompt = RUBRIC.format(title=title or "", source=source_text or "", summary=summary or "", action=action_text or "")
    last = None
    for attempt in range(2):
        try:
            _pace()
            out = json.loads(_ask(prompt))
            verdict, score = out.get("verdict"), float(out.get("score"))
            if verdict not in ("pass", "fail") or not 0.0 <= score <= 1.0:
                raise ValueError(f"bad judge output: {out!r}")
            return {"verdict": verdict, "score": score, "reason": str(out.get("reason", ""))}
        except Exception as e:
            last = e
            if attempt == 0:
                print(f"[JUDGE] call failed ({type(e).__name__}: {str(e)[:120]}), retrying in {RETRY_DELAY}s")
                sleep(RETRY_DELAY)
    return {"verdict": "error", "score": 0.0, "reason": f"judge failed twice: {type(last).__name__}: {str(last)[:200]}"}


def passed(result):
    return result["verdict"] == "pass" and result["score"] >= PASS_SCORE


def gate(items, title_key, source_key, summary_key, action_key, log=print):
    """Judge every item. Returns (kept, held_back, stats); only items that pass with score >= PASS_SCORE are kept."""
    kept, held, counts = [], [], {"pass": 0, "fail": 0, "error": 0}
    for item in items:
        r = judge_item(item.get(title_key), item.get(source_key), item.get(summary_key), item.get(action_key))
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
