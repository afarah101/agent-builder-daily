#!/usr/bin/env python3
from typing_extensions import TypedDict
from typing import List
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
import os, json, smtplib
from pathlib import Path
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
from langfuse import Langfuse, observe
load_dotenv()
langfuse = Langfuse()


# ─────────────────────────────────────────────
# STATE
# ─────────────────────────────────────────────

class RemediationState(TypedDict):
    papers_saved: int
    site_fresh: bool
    site_remediated: bool
    key1_pct: float
    key2_pct: float
    token_warning: bool
    token_approval: str  # "approved", "skipped", "pending"
    langfuse_traces: int
    actions_taken: List[str]
    alerts: List[str]
    email_sent: bool
    run_timestamp: str


# ─────────────────────────────────────────────
# NODE 1 — check_papers
# ─────────────────────────────────────────────

@observe()
def check_papers(state: RemediationState) -> RemediationState:
    """Check if case studies were saved in today's pipeline run."""
    print("[REMEDIATION] Checking papers saved...")

    today = datetime.now().strftime("%Y-%m-%d")
    cs_path = Path("case_studies")

    today_file = cs_path / f"{today}.json"
    papers_saved = 0

    if today_file.exists():
        data = json.loads(today_file.read_text())
        papers_saved = len(data.get("case_studies", []))

    video_files = list(cs_path.glob(f"v_{today.replace('-','')}*.json"))
    for vf in video_files:
        data = json.loads(vf.read_text())
        papers_saved += len(data.get("case_studies", []))

    alerts = list(state.get("alerts", []))
    actions = list(state.get("actions_taken", []))

    if papers_saved == 0:
        alerts.append(
            "CRITICAL: 0 case studies saved today. "
            "Pipeline may have failed at arXiv fetch or Groq scoring."
        )
        print("[REMEDIATION] ALERT: No papers saved today")
    else:
        print(f"[REMEDIATION] Papers saved: {papers_saved}")

    return {
        **state,
        "papers_saved": papers_saved,
        "alerts": alerts,
        "actions_taken": actions,
        "run_timestamp": datetime.now(timezone.utc).isoformat()
    }


# ─────────────────────────────────────────────
# NODE 2 — check_site_freshness
# ─────────────────────────────────────────────

@observe()
def check_site_freshness(state: RemediationState) -> RemediationState:
    """
    Check if site/index.html was updated today.
    If stale, regenerate and push automatically.
    """
    print("[REMEDIATION] Checking site freshness...")

    index_path = Path("site/index.html")
    alerts = list(state.get("alerts", []))
    actions = list(state.get("actions_taken", []))
    site_fresh = False
    site_remediated = False

    if not index_path.exists():
        alerts.append("CRITICAL: site/index.html does not exist.")
        return {
            **state,
            "site_fresh": False,
            "site_remediated": False,
            "alerts": alerts,
            "actions_taken": actions
        }

    mtime = datetime.fromtimestamp(
        index_path.stat().st_mtime, tz=timezone.utc
    )
    now = datetime.now(timezone.utc)
    hours_since_update = (now - mtime).total_seconds() / 3600

    if hours_since_update > 25:
        print(f"[REMEDIATION] Site is stale ({hours_since_update:.1f}h old) - regenerating...")
        alerts.append(
            f"Site was stale ({hours_since_update:.1f}h since last update). "
            "Auto-remediated: regenerated HTML and pushed."
        )

        import subprocess
        result = subprocess.run(
            ["python3", "generate_html.py"],
            capture_output=True, text=True
        )

        if result.returncode == 0:
            subprocess.run(["git", "add", "site/"], capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "Fix: remediation agent regenerated stale HTML"],
                capture_output=True
            )
            subprocess.run(["git", "push"], capture_output=True)
            actions.append("Regenerated stale site/index.html and pushed to Vercel")
            site_remediated = True
            print("[REMEDIATION] Site regenerated and pushed successfully")
        else:
            alerts.append(f"FAILED to regenerate HTML: {result.stderr[:200]}")
    else:
        site_fresh = True
        print(f"[REMEDIATION] Site is fresh ({hours_since_update:.1f}h old)")

    return {
        **state,
        "site_fresh": site_fresh,
        "site_remediated": site_remediated,
        "alerts": alerts,
        "actions_taken": actions
    }


# ─────────────────────────────────────────────
# NODE 3 — check_tokens (with human-in-the-loop)
# ─────────────────────────────────────────────

@observe()
def check_tokens(state: RemediationState) -> RemediationState:
    """
    Check Groq token budget.
    If Key 1 > 80%, set token_warning = True.
    Human-in-the-loop handles the approval via LangGraph interrupt.
    """
    print("[REMEDIATION] Checking token budget...")

    alerts = list(state.get("alerts", []))
    actions = list(state.get("actions_taken", []))
    token_warning = False
    key1_pct = 0.0
    key2_pct = 0.0

    stats_path = Path("site/groq_token_stats.json")
    if stats_path.exists():
        stats = json.loads(stats_path.read_text())
        key1_tokens = stats.get("key_1", {}).get("total_tokens", 0)
        key2_tokens = stats.get("key_2", {}).get("total_tokens", 0)
        DAILY_LIMIT = 100000
        key1_pct = round((key1_tokens / DAILY_LIMIT) * 100, 1)
        key2_pct = round((key2_tokens / DAILY_LIMIT) * 100, 1)

        print(f"[REMEDIATION] Key 1: {key1_pct}% | Key 2: {key2_pct}%")

        if key1_pct >= 50 and key1_pct < 80:
            alerts.append(
                f"TOKEN NOTICE: Key 1 at {key1_pct}%. "
                "Approaching limit - monitor closely."
            )
            print(f"[REMEDIATION] Token notice: Key 1 at {key1_pct}%")

        if key1_pct >= 80:
            token_warning = True
            alerts.append(
                f"TOKEN WARNING: Key 1 at {key1_pct}%. "
                f"Key 2 at {key2_pct}%. "
                "Video pipeline paused pending approval."
            )
            print(f"[REMEDIATION] Token warning triggered at {key1_pct}%")
    else:
        print("[REMEDIATION] groq_token_stats.json not found - skipping token check")

    return {
        **state,
        "key1_pct": key1_pct,
        "key2_pct": key2_pct,
        "token_warning": token_warning,
        "alerts": alerts,
        "actions_taken": actions
    }


# ─────────────────────────────────────────────
# NODE 4 — check_langfuse
# ─────────────────────────────────────────────

@observe()
def check_langfuse(state: RemediationState) -> RemediationState:
    """Check if LangFuse received traces from today's run."""
    print("[REMEDIATION] Checking LangFuse traces...")

    alerts = list(state.get("alerts", []))
    langfuse_traces = 0

    try:
        snapshot_path = Path("site/langfuse_snapshot.json")
        if snapshot_path.exists():
            snapshot = json.loads(snapshot_path.read_text())
            langfuse_traces = snapshot.get("total_traces", 0)
            if langfuse_traces == 0:
                alerts.append(
                    "WARNING: LangFuse snapshot shows 0 traces. "
                    "Pipeline may have crashed before flushing."
                )
            print(f"[REMEDIATION] LangFuse traces (snapshot): {langfuse_traces}")
        else:
            print("[REMEDIATION] No LangFuse snapshot found - skipping check")
            langfuse_traces = -1  # -1 means not checked
    except Exception as e:
        print(f"[REMEDIATION] LangFuse check failed: {e}")
        alerts.append(f"LangFuse check error: {str(e)[:100]}")

    return {
        **state,
        "langfuse_traces": langfuse_traces,
        "alerts": alerts
    }


# ─────────────────────────────────────────────
# NODE 5 — report
# ─────────────────────────────────────────────

@observe()
def report(state: RemediationState) -> RemediationState:
    """Send a clean daily pipeline digest email."""
    print("[REMEDIATION] Sending daily digest email...")

    alerts = state.get("alerts", [])
    actions = state.get("actions_taken", [])
    timestamp = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")

    # Load pipeline metrics for richer report
    papers_saved = state.get("papers_saved", 0)
    key1_pct = state.get("key1_pct", 0)
    key2_pct = state.get("key2_pct", 0)
    site_fresh = state.get("site_fresh", False)
    site_remediated = state.get("site_remediated", False)
    langfuse_traces = state.get("langfuse_traces", 0)

    # Load groq_token_stats for detailed breakdown
    avg_tpc = 0
    try:
        stats_path = Path("site/groq_token_stats.json")
        if stats_path.exists():
            stats = json.loads(stats_path.read_text())
            tpc = stats.get("tokens_per_call", [])
            avg_tpc = sum(tpc) / len(tpc) if tpc else 0
    except Exception:
        pass

    # Load metrics.json for score and eval data
    avg_score = "N/A"
    score_health = "N/A"
    papers_fetched = "N/A"
    gemini_pass = "N/A"
    gemini_fail = "N/A"
    gemini_pending = "N/A"
    try:
        metrics_path = Path("site/metrics.json")
        if metrics_path.exists():
            metrics = json.loads(metrics_path.read_text())
            if metrics and isinstance(metrics, list):
                latest = metrics[-1]
                avg_score = latest.get("avg_score", "N/A")
                papers_fetched = latest.get("total_fetched", "N/A")
                gemini_pass = latest.get("gemini_pass", "N/A")
                gemini_fail = latest.get("gemini_fail", "N/A")
                gemini_pending = latest.get("gemini_pending", "N/A")
                if isinstance(avg_score, (int, float)):
                    if avg_score >= 70:
                        score_health = "GOOD"
                    elif avg_score >= 50:
                        score_health = "FAIR"
                    else:
                        score_health = "POOR - scoring prompts need review"
    except Exception:
        pass

    # Bucket alerts
    critical_alerts = [a for a in alerts if "CRITICAL" in a]
    token_warnings = [a for a in alerts if "TOKEN" in a]
    other_alerts = [a for a in alerts if "CRITICAL" not in a and "TOKEN" not in a]

    if critical_alerts:
        status = "ACTION REQUIRED"
        status_icon = "URGENT"
    elif token_warnings:
        status = "WARNING"
        status_icon = "WARNING"
    else:
        status = "All systems running"
        status_icon = "OK"

    # Build email body
    lines = [
        "SCOUT DAILY - Pipeline Digest",
        timestamp,
        f"Status: {status_icon} {status}",
        "",
        "=" * 50,
        "PIPELINE HEALTH",
        "=" * 50,
        f"Papers fetched today:     {papers_fetched}",
        f"Papers saved today:       {papers_saved}",
        f"Average score today:      {avg_score}/100",
        f"Score health:             {score_health}",
        "",
        "=" * 50,
        "GEMINI QUALITY CONTROL",
        "=" * 50,
        f"Verified (pass):          {gemini_pass}",
        f"Flagged (fail):           {gemini_fail}",
        f"Pending evaluation:       {gemini_pending}",
        "",
        "=" * 50,
        "GROQ TOKEN USAGE",
        "=" * 50,
        f"Key 1:                    {key1_pct}% of 100,000 daily limit",
        f"Key 2:                    {key2_pct}% of 100,000 daily limit",
        f"Avg tokens per call:      {avg_tpc:.0f}",
        "",
        "=" * 50,
        "SITE STATUS",
        "=" * 50,
        f"Site fresh:               {'Yes' if site_fresh else 'Stale - auto-remediated' if site_remediated else 'STALE - check manually'}",
        f"LangFuse traces:          {langfuse_traces if langfuse_traces >= 0 else 'Not checked'}",
        f"Dashboard:                https://scout-monitor-lemon.vercel.app/dashboard.html",
        f"Live site:                https://agent-builder-daily.vercel.app",
    ]

    if actions:
        lines += [
            "",
            "=" * 50,
            "ACTIONS TAKEN AUTOMATICALLY",
            "=" * 50,
        ]
        for a in actions:
            lines.append(f"  - {a}")

    if critical_alerts:
        lines += [
            "",
            "=" * 50,
            "CRITICAL ALERTS - ACTION REQUIRED",
            "=" * 50,
        ]
        for a in critical_alerts:
            lines.append(f"  ! {a}")

    if token_warnings:
        lines += [
            "",
            "=" * 50,
            "TOKEN WARNINGS",
            "=" * 50,
        ]
        for a in token_warnings:
            lines.append(f"  ! {a}")

    if other_alerts:
        lines += [
            "",
            "=" * 50,
            "OTHER ALERTS",
            "=" * 50,
        ]
        for a in other_alerts:
            lines.append(f"  - {a}")

    if score_health and "POOR" in str(score_health):
        lines += [
            "",
            "=" * 50,
            "SCORE QUALITY NOTICE",
            "=" * 50,
            "Average score is below 50/100. This means papers are scoring",
            "too low or the scoring rubric needs tuning. Suggested actions:",
            "  1. Check if blocklist is filtering too aggressively",
            "  2. Review the scoring prompt in case_study_scout_langgraph.py",
            "  3. Check if arXiv queries are returning relevant papers",
        ]

    lines += [
        "",
        "-" * 50,
        "Scout Daily - Automated Pipeline Digest",
        "-" * 50,
    ]

    body = "\n".join(lines)

    # Send via Gmail
    try:
        gmail_user = os.getenv("GMAIL_USER")
        gmail_password = os.getenv("GMAIL_APP_PASSWORD")
        recipient = os.getenv("REPORT_EMAIL", gmail_user)

        if not gmail_user or not gmail_password:
            print("[REMEDIATION] Gmail credentials not set - printing report")
            print(body)
            return {**state, "email_sent": False}

        msg = MIMEMultipart()
        msg["From"] = gmail_user
        msg["To"] = recipient
        msg["Subject"] = f"Scout Daily: {status_icon} {status} - {timestamp}"
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_password)
            server.sendmail(gmail_user, recipient, msg.as_string())

        print(f"[REMEDIATION] Digest email sent to {recipient}")
        return {**state, "email_sent": True}

    except Exception as e:
        print(f"[REMEDIATION] Email failed: {e}")
        print(body)
        return {**state, "email_sent": False}


# ─────────────────────────────────────────────
# ROUTING
# ─────────────────────────────────────────────

def should_interrupt_for_tokens(state: RemediationState) -> str:
    """Route to interrupt if token warning triggered."""
    if state.get("token_warning"):
        return "interrupt"
    return "continue"


# ─────────────────────────────────────────────
# GRAPH
# ─────────────────────────────────────────────

def build_remediation_graph():
    memory = MemorySaver()
    builder = StateGraph(RemediationState)

    builder.add_node("check_papers", check_papers)
    builder.add_node("check_site_freshness", check_site_freshness)
    builder.add_node("check_tokens", check_tokens)
    builder.add_node("check_langfuse", check_langfuse)
    builder.add_node("report", report)

    builder.add_edge(START, "check_papers")
    builder.add_edge("check_papers", "check_site_freshness")
    builder.add_edge("check_site_freshness", "check_tokens")

    builder.add_conditional_edges(
        "check_tokens",
        should_interrupt_for_tokens,
        {
            "interrupt": "report",   # emails you immediately for approval
            "continue": "check_langfuse"
        }
    )

    builder.add_edge("check_langfuse", "report")
    builder.add_edge("report", END)

    return builder.compile(checkpointer=memory)


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

@observe(name="remediation-agent")
def run_remediation():
    print("\n[REMEDIATION] Starting remediation agent...")

    graph = build_remediation_graph()

    initial_state = {
        "papers_saved": 0,
        "site_fresh": False,
        "site_remediated": False,
        "key1_pct": 0.0,
        "key2_pct": 0.0,
        "token_warning": False,
        "token_approval": "pending",
        "langfuse_traces": 0,
        "actions_taken": [],
        "alerts": [],
        "email_sent": False,
        "run_timestamp": ""
    }

    config = {"configurable": {"thread_id": "remediation-1"}}
    result = graph.invoke(initial_state, config=config)

    print(f"\n[REMEDIATION] Complete.")
    print(f"  Papers: {result['papers_saved']}")
    print(f"  Site fresh: {result['site_fresh']}")
    print(f"  Token warning: {result['token_warning']}")
    print(f"  Email sent: {result['email_sent']}")
    print(f"  Alerts: {len(result['alerts'])}")

    langfuse.flush()
    return result


if __name__ == "__main__":
    run_remediation()
