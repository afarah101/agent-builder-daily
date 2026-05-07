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
load_dotenv()


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
        print(f"[REMEDIATION] Site is stale ({hours_since_update:.1f}h old) — regenerating...")
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

        if key1_pct >= 80:
            token_warning = True
            alerts.append(
                f"TOKEN WARNING: Key 1 at {key1_pct}%. "
                f"Key 2 at {key2_pct}%. "
                "Video pipeline paused pending approval."
            )
            print(f"[REMEDIATION] Token warning triggered at {key1_pct}%")
    else:
        print("[REMEDIATION] groq_token_stats.json not found — skipping token check")

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
            print("[REMEDIATION] No LangFuse snapshot found — skipping check")
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

def report(state: RemediationState) -> RemediationState:
    """Send summary email via Gmail SMTP."""
    print("[REMEDIATION] Sending report email...")

    alerts = state.get("alerts", [])
    actions = state.get("actions_taken", [])
    is_clean = len(alerts) == 0

    status = "✅ Clean Run" if is_clean else "⚠️ Action Required"
    timestamp = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")

    body_lines = [
        f"Scout Daily Pipeline Report — {timestamp}",
        f"Status: {status}",
        "",
        f"Papers saved today: {state.get('papers_saved', 0)}",
        f"Site fresh: {'Yes' if state.get('site_fresh') else 'No (auto-remediated)' if state.get('site_remediated') else 'No'}",
        f"Key 1 usage: {state.get('key1_pct', 0)}%",
        f"Key 2 usage: {state.get('key2_pct', 0)}%",
        f"LangFuse traces: {state.get('langfuse_traces', 0)}",
        "",
    ]

    if actions:
        body_lines.append("ACTIONS TAKEN AUTOMATICALLY:")
        for a in actions:
            body_lines.append(f"  • {a}")
        body_lines.append("")

    if alerts:
        body_lines.append("ALERTS:")
        for alert in alerts:
            body_lines.append(f"  • {alert}")
        body_lines.append("")

    if state.get("token_warning"):
        body_lines.append(
            "TOKEN APPROVAL REQUIRED:\n"
            "Key 1 is above 80%. Reply to this email with "
            "'APPROVE' to run the video pipeline, or "
            "'SKIP' to defer until tomorrow."
        )

    body = "\n".join(body_lines)

    try:
        gmail_user = os.getenv("GMAIL_USER")
        gmail_password = os.getenv("GMAIL_APP_PASSWORD")
        recipient = os.getenv("REPORT_EMAIL", gmail_user)

        if not gmail_user or not gmail_password:
            print("[REMEDIATION] Gmail credentials not set — skipping email")
            return {**state, "email_sent": False}

        msg = MIMEMultipart()
        msg["From"] = gmail_user
        msg["To"] = recipient
        msg["Subject"] = f"Scout Daily: {status} — {timestamp}"
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_password)
            server.sendmail(gmail_user, recipient, msg.as_string())

        print(f"[REMEDIATION] Email sent to {recipient}")
        return {**state, "email_sent": True}

    except Exception as e:
        print(f"[REMEDIATION] Email failed: {e}")
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

    return result


if __name__ == "__main__":
    run_remediation()
