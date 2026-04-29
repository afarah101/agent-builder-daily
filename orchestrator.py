#!/usr/bin/env python3
"""
Orchestrator — runs both scouts sequentially, generates HTML, pushes to git.
Run via cron: 0 6,18 * * * cd /Users/ahmed.farah/scout-agent && python3 orchestrator.py
"""

import os
import subprocess
import sys
from datetime import datetime


def run(label, cmd):
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Running: {label}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(f"STDERR: {result.stderr}")
    if result.returncode != 0:
        print(f"✗ {label} failed with exit code {result.returncode}")
        return False
    print(f"✓ {label} complete")
    return True


def main():
    print(f"=== Scout Agent Orchestrator === {datetime.now().isoformat()}")

    steps = [
        ("Case Study Scout", "python3 case_study_scout_langgraph.py"),
        ("Industry News Scout", "python3 industry_news_scout_langgraph.py"),
        ("Generate HTML", "python3 generate_html.py"),
        ("Git Add", "git add -A"),
        ("Git Commit", f'git commit -m "Auto-update: {datetime.now().strftime("%Y-%m-%d %H:%M")}"'),
        ("Git Push", "git push origin main"),
    ]

    for label, cmd in steps:
        if not run(label, cmd):
            print(f"\n✗ Orchestrator stopped at: {label}")
            sys.exit(1)

    print(f"\n✓ All steps complete — {datetime.now().isoformat()}")


if __name__ == "__main__":
    main()
