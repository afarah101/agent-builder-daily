import os, json, re
from datetime import datetime

# Paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
QUEUE_FILE = os.path.join(PROJECT_ROOT, "video_queue.txt")
METRICS_FILE = os.path.join(SCRIPT_DIR, "metrics.json")
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")

def get_queued_count():
    if not os.path.exists(QUEUE_FILE):
        return 0
    try:
        with open(QUEUE_FILE, "r") as f:
            lines = [line.strip() for line in f if line.strip()]
            return len(lines)
    except Exception as e:
        print(f"[MONITOR] Error reading queue: {e}")
        return 0

def scrape_logs():
    key1_tokens = 0
    key2_tokens = 0
    total_requests = 0
    key1_exhausted = False
    key2_exhausted = False
    
    log_files = [
        os.path.join(LOG_DIR, "scout.log"),
        os.path.join(LOG_DIR, "cron.log")
    ]
    
    # Process logs
    for log_path in log_files:
        if not os.path.exists(log_path):
            continue
            
        try:
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    # Check for usage: [USAGE] Groq Tokens: 123 (Key 1)
                    usage_match = re.search(r"\[USAGE\] Groq Tokens(?::| \(Error Context\):) (\d+)(?: \(Key (\d+)\))?", line)
                    if usage_match:
                        tokens = int(usage_match.group(1))
                        key_num = usage_match.group(2)
                        if key_num == "2":
                            key2_tokens += tokens
                        else:
                            key1_tokens += tokens
                        total_requests += 1
                        
                    # Check for specific key exhaustion
                    if "Groq 429: Rate Limit Hit" in line:
                        if "Key 2" in line:
                            key2_exhausted = True
                        else:
                            key1_exhausted = True
                            
                    # [USAGE] Groq Tokens (Estimated): 500
                    est_match = re.search(r"\[USAGE\] Groq Tokens \(Estimated\): (\d+)", line)
                    if est_match:
                        key1_tokens += int(est_match.group(1))
                        total_requests += 1

                    # Count initiated requests
                    if "_initiated" in line:
                        total_requests += 1

        except Exception as e:
            print(f"[MONITOR] Error scraping {log_path}: {e}")

    # Exhaustion Override: 100k tokens if 429 hit
    if key1_exhausted: key1_tokens = max(key1_tokens, 100000)
    if key2_exhausted: key2_tokens = max(key2_tokens, 100000)

    return key1_tokens, key2_tokens, total_requests

def update_metrics(status="success", tokens=0, api_calls=0, errors=None, article_count=0, requests_initiated=0):
    # SCRAPE FIRST
    scraped_k1, scraped_k2, scraped_requests = scrape_logs()
    
    total_tokens = scraped_k1 + scraped_k2 + tokens
    final_requests = scraped_requests + requests_initiated
    
    metrics = []
    if os.path.exists(METRICS_FILE):
        try:
            with open(METRICS_FILE, "r") as f:
                metrics = json.load(f)
        except:
            pass
    
    entry = {
        "timestamp": datetime.now().isoformat(),
        "run_id": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "status": status,
        "latency": {},
        "tokens": total_tokens,
        "key_1_raw_tokens": scraped_k1,
        "key_2_raw_tokens": scraped_k2,
        "api_calls": final_requests,
        "total_requests": final_requests,
        "errors": errors or [],
        "article_count": article_count,
        "pending_queue": get_queued_count()
    }
    
    # If same run_id (second call in same second), merge
    if metrics and metrics[-1]["run_id"] == entry["run_id"]:
        metrics[-1].update(entry)
    else:
        metrics.append(entry)
        
    metrics = metrics[-100:]
    
    with open(METRICS_FILE, "w") as f:
        json.dump(metrics, f, indent=2)

if __name__ == "__main__":
    update_metrics(status="refresh")
    print(f"Monitor updated from logs. Pending: {get_queued_count()}")
