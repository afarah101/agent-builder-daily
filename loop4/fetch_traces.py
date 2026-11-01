#!/usr/bin/env python3
"""Fetch the last 7 days of Scout Daily traces from Langfuse and compact them into loop4/out/traces.json."""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from langfuse import Langfuse

load_dotenv()

OUT_DIR = "loop4/out"
TRACES_FILE = f"{OUT_DIR}/traces.json"
WINDOW = timedelta(days=7)
MAX_AGE = timedelta(hours=48)
TEXT_CAP = 500
PAGE_SIZE = 100
# Traces go to third-party readers and a build artifact, so anything shaped like a key is masked first.
KEY_SHAPED = re.compile(r"gsk_\w{8,}|AIza[\w-]{20,}|[ps]k-lf-[\w-]{8,}|sk-[\w-]{20,}")


def cut(value, cap=TEXT_CAP):
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, default=str, ensure_ascii=False)
    return KEY_SHAPED.sub("[REDACTED]", text)[:cap]


def tokens_used(observation):
    usage = observation.usage_details or {}
    return usage.get("total") or (usage.get("input", 0) + usage.get("output", 0))


def compact_observation(o):
    return {
        "name": o.name,
        "type": o.type,
        "level": str(getattr(o.level, "value", o.level)),
        "error": cut(o.status_message),
        "latency_s": o.latency,
        "tokens": tokens_used(o),
        "args": cut(o.input),
        "result": cut(o.output),
    }


def compact_score(s):
    return {"name": s.name, "value": getattr(s, "value", None), "comment": cut(getattr(s, "comment", None))}


def compact_trace(t):
    observations = [compact_observation(o) for o in sorted(t.observations, key=lambda o: o.start_time)]
    levels = {o["level"] for o in observations}
    errors = [o["error"] or o["name"] for o in observations if o["level"] == "ERROR"]
    return {
        "id": t.id,
        "name": t.name,
        "timestamp": t.timestamp.isoformat(),
        "status": "error" if "ERROR" in levels else "warning" if "WARNING" in levels else "ok",
        "error": errors[0] if errors else None,
        "latency_s": t.latency,
        "tokens": sum(o["tokens"] for o in observations),
        "scores": [compact_score(s) for s in t.scores],
        "input": cut(t.input),
        "output": cut(t.output),
        "observations": observations,
    }


def list_trace_ids(langfuse, since, until):
    ids, page = [], 1
    while True:
        response = langfuse.api.trace.list(page=page, limit=PAGE_SIZE, from_timestamp=since, to_timestamp=until,
                                           order_by="timestamp.desc", fields="core")
        ids += [t.id for t in response.data]
        if page >= response.meta.total_pages or not response.data:
            return ids
        page += 1


def stale_reason(traces, now):
    """Why this week's traces cannot be reviewed, or None when they can."""
    if not traces:
        return "no Scout Daily traces in the last 7 days"
    newest = max(datetime.fromisoformat(t["timestamp"]) for t in traces)
    if now - newest > MAX_AGE:
        return f"newest trace is {newest.isoformat()}, older than {MAX_AGE.total_seconds() / 3600:.0f} hours"
    return None


def main():
    now = datetime.now(timezone.utc)
    langfuse = Langfuse(tracing_enabled=False)  # read-only: this review must not add traces to what it reviews
    ids = list_trace_ids(langfuse, now - WINDOW, now)
    traces, failed = [], []
    for trace_id in ids:
        try:
            traces.append(compact_trace(langfuse.api.trace.get(trace_id)))
        except Exception as e:
            print(f"[LOOP4] trace {trace_id} not fetched: {type(e).__name__}")
            failed.append(trace_id)
    reason = stale_reason(traces, now)
    if reason:
        sys.exit(f"[LOOP4] Cannot review: {reason} (listed {len(ids)}, fetch failures {len(failed)})")
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(TRACES_FILE, "w", encoding="utf-8") as f:
        json.dump({"fetched_at": now.isoformat(), "fetch_failures": len(failed), "traces": traces}, f, ensure_ascii=False, indent=1)
    newest = max(t["timestamp"] for t in traces)
    print(f"[LOOP4] Wrote {len(traces)} traces ({len(failed)} failed), newest {newest}, {os.path.getsize(TRACES_FILE)} bytes")


if __name__ == "__main__":
    main()
