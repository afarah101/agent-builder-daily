#!/bin/bash

# Go to the project folder
cd "$HOME/scout-agent"

# Activate the virtual environment
source venv/bin/activate

# Load the API key
export GROQ_API_KEY="$(grep GROQ_API_KEY ~/.zshrc | cut -d'"' -f2)"

# Run both scouts
python scout.py >> logs/scout.log 2>&1
python news_scout.py >> logs/scout.log 2>&1

# Rebuild the static site
python generate_html.py >> logs/scout.log 2>&1

# Push to GitHub (triggers Vercel auto-deploy)
git add .
git commit -m "Update: $(date '+%Y-%m-%d %H:%M')" >> logs/scout.log 2>&1
git push origin main >> logs/scout.log 2>&1

echo "---" >> logs/scout.log
echo "Finished: $(date)" >> logs/scout.log
echo "" >> logs/scout.log
