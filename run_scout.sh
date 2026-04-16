#!/bin/bash

# Go to the project folder
cd "$HOME/scout-agent"

# Activate the virtual environment
source venv/bin/activate

# Load the API key from zshrc (launchd doesn't read shell configs by default)
export GROQ_API_KEY="$(grep GROQ_API_KEY ~/.zshrc | cut -d'"' -f2)"

# Run the scout and log output
python scout.py >> logs/scout.log 2>&1
echo "---" >> logs/scout.log
echo "Finished: $(date)" >> logs/scout.log
echo "" >> logs/scout.log
