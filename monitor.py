#!/usr/bin/env python3
"""
Monitor metrics from scout.py and news_scout.py runs.
Reads JSON output, parses logs, captures token usage, and writes metrics.json
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Project paths
PROJECT_DIR = Path.home() / "scout-agent"
ISSUES_DIR = PROJECT_DIR / "issues"
MONITORING_DIR = PROJECT_DIR / "monitoring"
METRICS_FILE = MONITORING_DIR / "metrics.json"

# Ensure monitoring dir exists
MONITORING_DIR.mkdir(exist_ok=True)


def get_latest_issue_file():
    """Get the most recent issues/*.json file"""
    if not ISSUES_DIR.exists():
        return None
    files = sorted(ISSUES_DIR.glob("*.json"), reverse=True)
    return files[0] if files else None


def parse_issue_file(filepath):
    """Extract metrics from an issue JSON file"""
    try:
        with open(filepath, "r") as f:
            data = json.load(f)
        
        articles = data.get("articles", [])
        article_count = len(articles)
        
        return {
            "article_count": article_count,
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        print(f"Error parsing {filepath}: {e}")
        return {"article_count": 0, "error": str(e)}


def estimate_tokens(text):
    """Rough estimate: ~1 token per 4 chars (Groq/LLM average)"""
    return max(1, len(text) // 4)


def get_current_metrics():
    """Build current metrics snapshot"""
    latest_issue = get_latest_issue_file()
    
    metrics = {
        "timestamp": datetime.now().isoformat(),
        "run_id": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "status": "success",
        "latency": {},
        "tokens": 0,
        "api_calls": 0,
        "errors": [],
    }
    
    if latest_issue:
        issue_data = parse_issue_file(latest_issue)
        metrics["article_count"] = issue_data.get("article_count", 0)
        if "error" in issue_data:
            metrics["status"] = "error"
            metrics["errors"].append(issue_data["error"])
    
    return metrics


def append_to_metrics_log(current_metrics):
    """Append current run metrics to historical log"""
    history = []
    
    # Load existing metrics if file exists
    if METRICS_FILE.exists():
        try:
            with open(METRICS_FILE, "r") as f:
                history = json.load(f)
        except json.JSONDecodeError:
            history = []
    
    # Append new metrics
    history.append(current_metrics)
    
    # Keep last 100 runs to avoid bloat
    if len(history) > 100:
        history = history[-100:]
    
    # Write back
    with open(METRICS_FILE, "w") as f:
        json.dump(history, f, indent=2)
    
    print(f"Metrics logged to {METRICS_FILE}")


if __name__ == "__main__":
    metrics = get_current_metrics()
    append_to_metrics_log(metrics)
    
    # Exit with status 0 if success, 1 if error
    sys.exit(0 if metrics["status"] == "success" else 1)
