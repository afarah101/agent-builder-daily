#!/bin/bash

# Scout automation + monitoring + git push
# Runs scout.py, news_scout.py, monitoring, and commits to git

set -e  # Exit on error

PROJECT_DIR="$HOME/scout-agent"
VENV="$PROJECT_DIR/venv"
LOG_FILE="$PROJECT_DIR/run.log"

# Load environment (API keys, etc.)
source ~/.zshrc

# Activate venv
source "$VENV/bin/activate"

# Timestamp
echo "=== Scout Run Start: $(date) ===" >> "$LOG_FILE"

# Step 1: Reddit Scout
echo "Running scout.py..."
START=$(date +%s)
python "$PROJECT_DIR/scout.py" >> "$LOG_FILE" 2>&1
END=$(date +%s)
LATENCY_SCOUT=$((END - START))
echo "scout.py completed in ${LATENCY_SCOUT}s" >> "$LOG_FILE"

# Step 2: News Scout
echo "Running news_scout.py..."
START=$(date +%s)
python "$PROJECT_DIR/news_scout.py" >> "$LOG_FILE" 2>&1
END=$(date +%s)
LATENCY_NEWS=$((END - START))
echo "news_scout.py completed in ${LATENCY_NEWS}s" >> "$LOG_FILE"

# Step 3: Generate HTML
echo "Running generate_html.py..."
START=$(date +%s)
python "$PROJECT_DIR/generate_html.py" >> "$LOG_FILE" 2>&1
END=$(date +%s)
LATENCY_HTML=$((END - START))
echo "generate_html.py completed in ${LATENCY_HTML}s" >> "$LOG_FILE"

# Step 4: Run monitoring
echo "Running monitor.py..."
START=$(date +%s)
python "$PROJECT_DIR/monitor.py" >> "$LOG_FILE" 2>&1
MONITOR_EXIT=$?
END=$(date +%s)
LATENCY_MONITOR=$((END - START))
echo "monitor.py completed in ${LATENCY_MONITOR}s (exit code: $MONITOR_EXIT)" >> "$LOG_FILE"

# Step 5: Git push
echo "Pushing to GitHub..."
START=$(date +%s)
cd "$PROJECT_DIR"
git add -A
git commit -m "Scout run: $(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE" 2>&1 || true
git push origin main >> "$LOG_FILE" 2>&1
END=$(date +%s)
LATENCY_GIT=$((END - START))
echo "git push completed in ${LATENCY_GIT}s" >> "$LOG_FILE"

# Done
echo "=== Scout Run Complete: $(date) ===" >> "$LOG_FILE"
echo "Latencies - scout: ${LATENCY_SCOUT}s, news: ${LATENCY_NEWS}s, html: ${LATENCY_HTML}s, monitor: ${LATENCY_MONITOR}s, git: ${LATENCY_GIT}s" >> "$LOG_FILE"

exit 0
