#!/usr/bin/env python3
import os, json, base64, requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
load_dotenv()

LANGFUSE_HOST = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")
PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY") or os.getenv("LANGFUSE_PUBLICKEY")
SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY")
OUTPUT_FILE = "site/langfuse_snapshot.json"

if not PUBLIC_KEY or not SECRET_KEY:
    print("[Langfuse Proxy] Missing credentials — skipping.")
    exit(0)

token = base64.b64encode(f"{PUBLIC_KEY}:{SECRET_KEY}".encode()).decode()
headers = {"Authorization": f"Basic {token}", "Content-Type": "application/json"}

now = datetime.now(timezone.utc)
seven_days_ago = (now - timedelta(days=7)).isoformat()
now_iso = now.isoformat()

def query_metrics(query, endpoint="/api/public/metrics"):
    try:
        url = LANGFUSE_HOST.rstrip("/") + endpoint
        resp = requests.get(url, headers=headers, params={"query": json.dumps(query)}, timeout=15)
        resp.raise_for_status()
        return resp.json().get("data", [])
    except Exception as e:
        print(f"[Langfuse Proxy] Error querying {endpoint}: {e}")
        return []

trace_counts = query_metrics({
    "view": "traces",
    "metrics": [{"measure": "count", "aggregation": "count"}],
    "dimensions": [{"field": "name"}],
    "filters": [],
    "fromTimestamp": seven_days_ago,
    "toTimestamp": now_iso,
    "timeDimension": {"granularity": "day"}
})

latency = query_metrics({
    "view": "observations",
    "metrics": [
        {"measure": "latency", "aggregation": "p50"},
        {"measure": "latency", "aggregation": "p95"}
    ],
    "dimensions": [{"field": "name"}],
    "filters": [],
    "fromTimestamp": seven_days_ago,
    "toTimestamp": now_iso
})

token_usage = query_metrics({
    "view": "observations",
    "metrics": [
        {"measure": "inputTokens", "aggregation": "sum"},
        {"measure": "outputTokens", "aggregation": "sum"}
    ],
    "dimensions": [],
    "filters": [],
    "fromTimestamp": seven_days_ago,
    "toTimestamp": now_iso
})

scores = query_metrics({
    "view": "scores-numeric",
    "metrics": [
        {"measure": "value", "aggregation": "avg"},
        {"measure": "value", "aggregation": "count"}
    ],
    "dimensions": [{"field": "name"}],
    "filters": [],
    "fromTimestamp": seven_days_ago,
    "toTimestamp": now_iso
})

snapshot = {
    "generated_at": now_iso,
    "trace_counts": trace_counts,
    "latency": latency,
    "token_usage": token_usage,
    "scores": scores
}

os.makedirs("site", exist_ok=True)
with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(snapshot, f, indent=2, ensure_ascii=False)

print(f"[Langfuse Proxy] Snapshot written to {OUTPUT_FILE}")
