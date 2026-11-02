import subprocess
import sys
import os

if sys.stdout.encoding != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

EXIT_ALL_GROQ_CALLS_FAILED = 3  # set by the news scout and generate_breakdowns.py

def run_script(script_name, allow_failure=False):
    print(f"--- Starting: {script_name} ---")
    result = subprocess.run(
        [sys.executable, script_name],
        capture_output=True,
        text=True,
        encoding='utf-8'
    )
    if result.returncode != 0:
        if allow_failure and result.returncode != EXIT_ALL_GROQ_CALLS_FAILED:
            print(f"WARNING: {script_name} failed but continuing pipeline:")
            print(result.stderr)
        else:
            print(f"CRITICAL ERROR in {script_name}:")
            print(result.stdout)  # failed Groq calls are logged here
            print(result.stderr)
            sys.exit(1)
    print(result.stdout)
    print(f"--- Finished: {script_name} ---\n")

if __name__ == "__main__":
    run_script("case_study_scout_langgraph.py")
    run_script("industry_news_scout_langgraph.py")
    run_script("generate_breakdowns.py", allow_failure=True)
    run_script("generate_html.py")
    run_script("langfuse_proxy.py", allow_failure=True)
    run_script("remediation_agent.py", allow_failure=True)
    run_script("creator_watch.py", allow_failure=True)
    print("Scout Daily Update: Success!")
