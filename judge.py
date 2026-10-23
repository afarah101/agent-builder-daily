"""Gemini judge: is each item's summary faithful to its source? Items are judged in batches (one call per 25 items);
items that fail, or that the judge can't score, are held back."""
import json
import os
import time
from collections import Counter

# Tried in order; a 429 (daily quota) or persistent 5xx moves to the next. 3.8/3.7 Flash time out on this key (2026-10-23).
JUDGE_MODELS = ["gemini-2.5-flash", "gemini-3.6-flash", "gemini-3.5-flash"]
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

Each item's publisher and date are known facts: a summary may state them even if the source text does not.

ITEMS (JSON list; each has id, title, publisher, date, source, summary, action):
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


def _ask(prompt, model):
    global _client
    from google import genai
    from google.genai import types
    if _client is None:
        # SDK retries off: _judge_chunk owns the single retry, and pacing counts every real request.
        _client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"), http_options=types.HttpOptions(
            timeout=120_000, retry_options=types.HttpRetryOptions(attempts=1)))
    resp = _client.models.generate_content(model=model, contents=prompt, config=types.GenerateContentConfig(
        temperature=0, response_mime_type="application/json"))
    return resp.text


def _error(reason):
    return {"verdict": "error", "score": 0.0, "reason": reason}


def _code(e):
    code = getattr(e, "code", None)
    return code if isinstance(code, int) else None


def _parse_verdict(v, model):
    verdict, score = v.get("verdict"), float(v.get("score"))
    if verdict not in ("pass", "fail") or not 0.0 <= score <= 1.0:
        raise ValueError(f"bad verdict {v!r}")
    return {"verdict": verdict, "score": score, "reason": str(v.get("reason", "")), "model": model}


def _judge_chunk(chunk):
    """One Gemini call for up to BATCH_SIZE items. Returns {id: result}; anything unmatched or malformed is an error."""
    prompt = BATCH_PROMPT.format(rules=RULES, items=json.dumps(chunk, ensure_ascii=False, indent=1))
    raw, model, last = None, None, None
    for candidate in JUDGE_MODELS:
        for attempt in range(2):
            try:
                _pace()
                raw, model = _ask(prompt, candidate), candidate
                break
            except Exception as e:
                last, code = e, _code(e)
                if code == 429:  # quota or rate limit on this model: the next model has its own quota
                    print(f"[JUDGE] {candidate} quota hit (429), trying next model")
                    break
                if code is not None and code >= 500 and attempt == 0:
                    print(f"[JUDGE] {candidate} {code}, retrying in {RETRY_DELAY}s")
                    sleep(RETRY_DELAY)
                    continue
                if code is not None and code >= 500:
                    print(f"[JUDGE] {candidate} {code} again, trying next model")
                    break
                return {it["id"]: _error(f"judge call failed: {type(e).__name__}: {str(e)[:200]}") for it in chunk}
        if raw is not None:
            break
    if raw is None:
        return {it["id"]: _error(f"all judge models failed; last: {type(last).__name__}: {str(last)[:160]}") for it in chunk}
    print(f"[JUDGE] model used: {model}")
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
                out[vid] = _parse_verdict(v, model)
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
    # Full article text (news source_text) beats the scout's short source field when present.
    batch = [{"id": str(i), "title": it.get(title_key) or "",
              "publisher": it.get("source") or ("arXiv" if it.get("source_type") == "arxiv" else ""),
              "date": it.get("pub_date") or "", "source": it.get("source_text") or it.get(source_key) or "",
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
    models = sorted({r.get("model") for r in results.values() if r.get("model")})
    stats = {"total": total, "passed": counts["pass"], "failed": counts["fail"], "errors": counts["error"],
             "pass_rate": counts["pass"] / total if total else 1.0, "model": ",".join(models) or "none"}
    log(f"[JUDGE] {counts['pass']} passed, {counts['fail']} failed, {counts['error']} errors (of {total}); model: {stats['model']}")
    return kept, held, stats


def send_scores(client, stats, log=print):
    """judge_pass_rate, judge_fail and judge_error on the current Langfuse trace."""
    try:
        meta = {"judge_model": stats.get("model", "none")}
        client.score_current_trace(name="judge_pass_rate", value=round(stats["pass_rate"], 4),
                                   comment=f"{stats['passed']}/{stats['total']} items passed the judge", metadata=meta)
        client.score_current_trace(name="judge_fail", value=stats["failed"], metadata=meta)
        client.score_current_trace(name="judge_error", value=stats["errors"], metadata=meta)
    except Exception as e:
        log(f"[JUDGE] Langfuse score failed: {e}")
