import subprocess
import sys
import os

# 1. Force UTF-8 encoding for GitHub Actions environment
if sys.stdout.encoding != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def run_script(script_name):
    """Utility to run a script and catch errors."""
    print(f"--- Starting: {script_name} ---")
    # We use 'python3' for the Ubuntu runner in GitHub Actions
    result = subprocess.run(["python3", script_name], capture_output=True, text=True, encoding='utf-8')
    
    if result.returncode != 0:
        print(f"CRITICAL ERROR in {script_name}:")
        print(result.stderr)
        sys.exit(1) # Stop the whole automation if a script fails
    
    print(result.stdout)
    print(f"--- Finished: {script_name} ---\n")

if __name__ == "__main__":
    # Ensure we are in the project root (GitHub Actions does this by default)
    # No hardcoded Mac paths here!
    
    # 2. Execute the Pipeline in order
    run_script("case_study_scout_langgraph.py")
    run_script("industry_news_scout_langgraph.py")
    run_script("generate_html.py")
    
    print("Scout Daily Update: Success!")