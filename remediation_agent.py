#!/usr/bin/env python3
from typing_extensions import TypedDict
from typing import List, Optional
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
import os, json, smtplib
from pathlib import Path
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
from langfuse import Langfuse, observe, propagate_attributes
load_dotenv()
langfuse = Langfuse()

NOT_CHECKED = "Not checked"
ALERT_STATE_FILE = Path("data/remediation_last_alert.json")
# case_study_scout_langgraph.py scoring prompt: total_score (0-75) below 30 is low_signal
LOW_SIGNAL_SCORE = 30


def is_dry_run() -> bool:
    return os.getenv("REMEDIATION_DRY_RUN", "").strip().lower() in ("1", "true", "yes")


def fmt(value, suffix=""):
    """None means the source file was missing or unreadable: show that, never 0."""
    return NOT_CHECKED if value is None else f"{value}{suffix}"


def load_json(path):
    """Parsed JSON, or None when the file is missing or unreadable."""
    try:
        return json.loads(Path(path).read_text())
    except Exception as e:
        print(f"[REMEDIATION] {path}: {NOT_CHECKED} ({type(e).__name__})")
        return None


def load_today_metrics(today):
    """Latest monitoring/metrics.json row, only if the case study scout wrote it today."""
    metrics = load_json("monitoring/metrics.json")
    if isinstance(metrics, list) and metrics and isinstance(metrics[-1], dict) and metrics[-1].get("date") == today:
        return metrics[-1]
    return None


def as_count(value):
    """value if it is an int >= 0, else None (missing or wrong type is Not checked, never 0)."""
    return value if type(value) is int and value >= 0 else None


def groq_stats_usable(stats):
    """True when every field check_tokens reads is present with the right type."""
    if not isinstance(stats, dict):
        return False
    keys = [stats.get("key_1"), stats.get("key_2")]
    fields_ok = all(isinstance(k, dict) and as_count(k.get(f)) is not None for k in keys for f in ("total_tokens", "calls"))
    return fields_ok and bool(as_count(stats.get("daily_limit_per_key", 100000)))


# ─────────────────────────────────────────────
# STATE
# ─────────────────────────────────────────────

class RemediationState(TypedDict):
    papers_saved: Optional[int]
    site_fresh: bool
    site_remediated: bool
    key1_pct: Optional[float]
    key2_pct: Optional[float]
    token_warning: bool
    token_approval: str  # "approved", "skipped", "pending"
    langfuse_traces: Optional[int]  # None = not checked
    groq_calls: Optional[int]
    actions_taken: List[str]
    alerts: List[str]   # real faults: these trigger the email
    faults: List[str]   # stable fault codes, used to send each fault set once per day
    info: List[str]     # reported in the digest, never trigger an email
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
    alerts = list(state.get("alerts", []))
    actions = list(state.get("actions_taken", []))
    faults = list(state.get("faults", []))
    info = list(state.get("info", []))

    data = load_json(today_file)
    if isinstance(data, dict) and not isinstance(data.get("case_studies"), list):
        print(f"[REMEDIATION] {today_file} has no case_studies list - {NOT_CHECKED}")
        data = None
    papers_saved = len(data["case_studies"]) if isinstance(data, dict) else None

    if papers_saved is not None:
        for vf in cs_path.glob(f"v_{today.replace('-','')}*.json"):
            vdata = load_json(vf)
            if isinstance(vdata, dict) and isinstance(vdata.get("case_studies"), list):
                papers_saved += len(vdata["case_studies"])

    if papers_saved is None:
        faults.append("papers_file_missing")
        alerts.append(
            f"CRITICAL: {today_file} is missing or unreadable. "
            "The case study scout did not save today."
        )
        print("[REMEDIATION] ALERT: today's case study file missing")
    elif papers_saved == 0:
        latest = load_today_metrics(today)
        judge_passed = as_count(latest.get("judge_passed")) if latest else None
        if judge_passed:
            below = as_count(latest.get("rank_cut_below_min"))
            beyond = as_count(latest.get("rank_cut_beyond_top"))
            cut = below + beyond if below is not None and beyond is not None else None
            info.append(
                f"INFO: 0 case studies saved. The judge passed {judge_passed}, "
                f"ranking cut {fmt(cut)} ({fmt(below)} below the minimum score, {fmt(beyond)} beyond the top 5)."
            )
            print(f"[REMEDIATION] 0 papers saved: ranking cut {fmt(cut)} judge-passed items (INFO)")
        else:
            faults.append("papers_zero")
            reason = "the judge passed 0 items" if judge_passed == 0 else "judge result not recorded today"
            alerts.append(
                f"CRITICAL: 0 case studies saved today ({reason}). "
                "Pipeline may have failed at arXiv fetch, Groq scoring or the judge."
            )
            print(f"[REMEDIATION] ALERT: No papers saved today ({reason})")
    else:
        print(f"[REMEDIATION] Papers saved: {papers_saved}")

    return {
        **state,
        "papers_saved": papers_saved,
        "alerts": alerts,
        "faults": faults,
        "info": info,
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
    faults = list(state.get("faults", []))
    info = list(state.get("info", []))
    site_fresh = False
    site_remediated = False

    if not index_path.exists():
        faults.append("site_missing")
        alerts.append("CRITICAL: site/index.html does not exist.")
        return {
            **state,
            "site_fresh": False,
            "site_remediated": False,
            "alerts": alerts,
            "faults": faults,
            "actions_taken": actions
        }

    mtime = datetime.fromtimestamp(
        index_path.stat().st_mtime, tz=timezone.utc
    )
    now = datetime.now(timezone.utc)
    hours_since_update = (now - mtime).total_seconds() / 3600

    if hours_since_update > 25 and is_dry_run():
        print(f"[REMEDIATION] Site is stale ({hours_since_update:.1f}h old) - DRY RUN, not regenerating or pushing")
        faults.append("site_stale")
        alerts.append(f"WARNING: Site is stale ({hours_since_update:.1f}h since last update). Dry run: not regenerated.")
    elif hours_since_update > 25:
        print(f"[REMEDIATION] Site is stale ({hours_since_update:.1f}h old) - regenerating...")

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
            info.append(f"INFO: Site was stale ({hours_since_update:.1f}h since last update). Auto-remediated.")
            site_remediated = True
            print("[REMEDIATION] Site regenerated and pushed successfully")
        else:
            faults.append("site_regen_failed")
            alerts.append(f"CRITICAL: FAILED to regenerate stale HTML: {result.stderr[:200]}")
    else:
        site_fresh = True
        print(f"[REMEDIATION] Site is fresh ({hours_since_update:.1f}h old)")

    return {
        **state,
        "site_fresh": site_fresh,
        "site_remediated": site_remediated,
        "alerts": alerts,
        "faults": faults,
        "info": info,
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
    faults = list(state.get("faults", []))
    info = list(state.get("info", []))
    token_warning = False
    key1_pct = None
    key2_pct = None
    groq_calls = None

    today = datetime.now().strftime("%Y-%m-%d")
    stats = load_json("site/groq_token_stats.json")
    if stats is not None and not groq_stats_usable(stats):
        print(f"[REMEDIATION] groq_token_stats.json has missing or wrong-type fields - {NOT_CHECKED}")
        stats = None
    if isinstance(stats, dict) and not str(stats.get("generated_at", "")).startswith(today):
        print(f"[REMEDIATION] groq_token_stats.json is not from today ({stats.get('generated_at')}) - {NOT_CHECKED}")
        stats = None
    if isinstance(stats, dict):
        key1_tokens = stats["key_1"]["total_tokens"]
        key2_tokens = stats["key_2"]["total_tokens"]
        groq_calls = stats["key_1"]["calls"] + stats["key_2"]["calls"]
        DAILY_LIMIT = stats.get("daily_limit_per_key", 100000)
        key1_pct = round((key1_tokens / DAILY_LIMIT) * 100, 1)
        key2_pct = round((key2_tokens / DAILY_LIMIT) * 100, 1)

        print(f"[REMEDIATION] Key 1: {key1_pct}% | Key 2: {key2_pct}% | live Groq calls: {groq_calls}")

        if key1_pct >= 50 and key1_pct < 80:
            info.append(
                f"TOKEN NOTICE: Key 1 at {key1_pct}%. "
                "Approaching limit - monitor closely."
            )
            print(f"[REMEDIATION] Token notice: Key 1 at {key1_pct}%")

        if key1_pct >= 80:
            token_warning = True
            faults.append("token_warning")
            alerts.append(
                f"TOKEN WARNING: Key 1 at {key1_pct}%. "
                f"Key 2 at {key2_pct}%. "
                "Video pipeline paused pending approval."
            )
            print(f"[REMEDIATION] Token warning triggered at {key1_pct}%")
    else:
        print("[REMEDIATION] groq_token_stats.json not usable - token check not done")

    return {
        **state,
        "key1_pct": key1_pct,
        "key2_pct": key2_pct,
        "groq_calls": groq_calls,
        "token_warning": token_warning,
        "alerts": alerts,
        "faults": faults,
        "info": info,
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
    faults = list(state.get("faults", []))
    langfuse_traces = None

    # langfuse_proxy.py runs after the scout and overwrites this file with
    # {generated_at, trace_counts: [{traceName, time_dimension, count_count}], ...};
    # the scout's own {total_traces} shape only survives if the proxy skipped.
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    snapshot = load_json("site/langfuse_snapshot.json")
    proxy_no_rows = False
    try:
        if isinstance(snapshot, dict) and isinstance(snapshot.get("trace_counts"), list):
            if str(snapshot.get("generated_at", "")).startswith(today):
                rows = snapshot["trace_counts"]
                if not rows:
                    # langfuse_proxy.py writes [] when its LangFuse query fails, so this is not a 0
                    proxy_no_rows = True
                    print(f"[REMEDIATION] LangFuse snapshot has no trace rows (proxy query failed?) - {NOT_CHECKED}")
                elif not any(t.get("time_dimension") == today for t in rows) and datetime.now(timezone.utc).hour < 2:
                    # shortcut: assumes a scout run lasts under 2h; a run that crossed UTC midnight traced into yesterday's bucket
                    print(f"[REMEDIATION] No LangFuse rows for {today} yet, just after UTC midnight - {NOT_CHECKED}")
                else:
                    langfuse_traces = sum(as_count(t["count_count"]) for t in rows
                                          if t.get("time_dimension") == today)
            else:
                print(f"[REMEDIATION] LangFuse snapshot is not from today ({snapshot.get('generated_at')})")
        elif isinstance(snapshot, dict) and "total_traces" in snapshot:
            # The scout writes total_traces as an all-time count, so it says nothing about today
            print(f"[REMEDIATION] LangFuse snapshot has only the all-time total_traces ({snapshot['total_traces']}) - {NOT_CHECKED}")
    except (TypeError, ValueError, AttributeError, KeyError) as e:
        print(f"[REMEDIATION] LangFuse snapshot unreadable: {e}")
        langfuse_traces = None

    if proxy_no_rows:
        faults.append("langfuse_no_rows")
        alerts.append(
            "WARNING: LangFuse proxy returned no trace rows today (query error or timeout). "
            "Traces not checked; the LangFuse dashboard data is empty."
        )
    if langfuse_traces == 0:
        faults.append("langfuse_zero")
        alerts.append(
            "WARNING: LangFuse snapshot shows 0 traces today. "
            "Pipeline may have crashed before flushing."
        )
    print(f"[REMEDIATION] LangFuse traces today (snapshot): {fmt(langfuse_traces)}")

    return {
        **state,
        "langfuse_traces": langfuse_traces,
        "alerts": alerts,
        "faults": faults
    }


# ─────────────────────────────────────────────
# NODE 5 — report
# ─────────────────────────────────────────────

@observe()
def report(state: RemediationState) -> RemediationState:
    """Build the daily digest; email it only when a real fault exists, once per day per fault set."""
    print("[REMEDIATION] Building daily digest...")

    alerts = state.get("alerts", [])
    actions = state.get("actions_taken", [])
    info = state.get("info", [])
    timestamp = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")

    # Load pipeline metrics for richer report
    papers_saved = state.get("papers_saved")
    key1_pct = state.get("key1_pct")
    key2_pct = state.get("key2_pct")
    groq_calls = state.get("groq_calls")
    site_fresh = state.get("site_fresh", False)
    site_remediated = state.get("site_remediated", False)
    langfuse_traces = state.get("langfuse_traces")

    # Token breakdown only counts when check_tokens could read today's stats
    avg_tpc = None
    if groq_calls is not None:
        stats = load_json("site/groq_token_stats.json") or {}
        tpc = stats.get("tokens_per_call", []) if isinstance(stats, dict) else None
        if isinstance(tpc, list) and all(as_count(t) is not None for t in tpc):
            avg_tpc = round(sum(tpc) / len(tpc)) if tpc else (0 if groq_calls == 0 else None)

    # Load metrics.json for score and eval data
    avg_score = "N/A"
    score_health = "N/A"
    gemini_pass = "N/A"
    gemini_fail = "N/A"
    gemini_pending = "N/A"
    from datetime import date
    today = date.today().strftime("%Y-%m-%d")
    latest = load_today_metrics(today) or {}
    # article_count is the number published; total_fetched is the arXiv fetch count
    papers_fetched = as_count(latest.get("total_fetched"))
    judge_passed = as_count(latest.get("judge_passed"))
    try:
        # Load real score and gemini data from today's case study file
        cs_path = Path(f"case_studies/{today}.json")
        if cs_path.exists():
            cs_data = json.loads(cs_path.read_text())
            papers_fetched = as_count(cs_data.get("total_fetched", papers_fetched))
            studies = cs_data.get("case_studies", [])
            if studies:
                scores = [s.get("total_score", 0) for s in studies if s.get("total_score")]
                avg_score = round(sum(scores) / len(scores)) if scores else "N/A"
                gemini_pass = sum(1 for s in studies if s.get("gemini_eval_status") == "pass")
                gemini_fail = sum(1 for s in studies if s.get("gemini_eval_status") == "fail")
                gemini_pending = sum(1 for s in studies if s.get("gemini_eval_status") == "pending")

                if isinstance(avg_score, (int, float)):
                    if avg_score >= LOW_SIGNAL_SCORE:
                        score_health = f"OK (at or above the {LOW_SIGNAL_SCORE}/75 low-signal line)"
                    else:
                        score_health = f"POOR - average is below the {LOW_SIGNAL_SCORE}/75 low-signal line"
    except Exception as e:
        print(f"[REMEDIATION] Metrics load error: {e}")

    # Bucket alerts (every alert is a real fault; info never triggers an email)
    critical_alerts = [a for a in alerts if "CRITICAL" in a]
    token_warnings = [a for a in alerts if "TOKEN" in a]
    other_alerts = [a for a in alerts if "CRITICAL" not in a and "TOKEN" not in a]

    if critical_alerts:
        status = "ACTION REQUIRED"
        status_icon = "URGENT"
    elif alerts:
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
        f"Papers fetched today:     {fmt(papers_fetched)}",
        f"Passed the judge:         {fmt(judge_passed)}",
        f"Papers saved today:       {fmt(papers_saved)}",
        f"Average score today:      {avg_score}/75",
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
        f"Key 1:                    {fmt(key1_pct, '% of 100,000 daily limit')}",
        f"Key 2:                    {fmt(key2_pct, '% of 100,000 daily limit')}",
        f"Live Groq calls:          {fmt(groq_calls)}" + (" (all responses served from cache)" if groq_calls == 0 else ""),
        f"Avg tokens per call:      {fmt(avg_tpc)}",
        "",
        "=" * 50,
        "SITE STATUS",
        "=" * 50,
        f"Site fresh:               {'Yes' if site_fresh else 'Stale - auto-remediated' if site_remediated else 'STALE - check manually'}",
        f"LangFuse traces today:    {fmt(langfuse_traces)}",
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

    if info:
        lines += [
            "",
            "=" * 50,
            "INFO",
            "=" * 50,
        ]
        for a in info:
            lines.append(f"  - {a}")

    if score_health and "POOR" in str(score_health):
        lines += [
            "",
            "=" * 50,
            "SCORE QUALITY NOTICE",
            "=" * 50,
            f"Average score is below {LOW_SIGNAL_SCORE}/75, the scorer's low-signal line. Papers are",
            "scoring too low or the scoring rubric needs tuning. Suggested actions:",
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
    subject = f"Scout Daily: {status_icon} {status} - {timestamp}"
    print(body)

    faults = sorted(set(state.get("faults", [])))
    alert_day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not faults:
        print("[REMEDIATION] no alert - no real faults, email not sent")
        return {**state, "email_sent": False}
    last = load_json(ALERT_STATE_FILE) if ALERT_STATE_FILE.exists() else None
    sent_today = last.get("sent", []) if isinstance(last, dict) and last.get("date") == alert_day else []
    if not isinstance(sent_today, list):  # a corrupted state file counts as no state
        sent_today = []
    if faults in sent_today:
        print(f"[REMEDIATION] no alert - faults {faults} already emailed today")
        return {**state, "email_sent": False}
    if is_dry_run():
        print(f"[REMEDIATION] DRY RUN - would send alert for faults {faults}; SMTP not contacted")
        print(f"Subject: {subject}")
        return {**state, "email_sent": False}

    if send_email(subject, body):
        ALERT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        ALERT_STATE_FILE.write_text(json.dumps({"date": alert_day, "sent": sent_today + [faults]}, indent=2) + "\n")
        return {**state, "email_sent": True}
    return {**state, "email_sent": False}


def send_email(subject, body) -> bool:
    """Send via Gmail. Only report() calls this, after the dry-run return."""
    try:
        gmail_user = os.getenv("GMAIL_USER")
        gmail_password = os.getenv("GMAIL_APP_PASSWORD")
        recipient = os.getenv("REPORT_EMAIL", gmail_user)

        if not gmail_user or not gmail_password:
            print("[REMEDIATION] Gmail credentials not set - alert not sent")
            return False

        msg = MIMEMultipart()
        msg["From"] = gmail_user
        msg["To"] = recipient
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_password)
            server.sendmail(gmail_user, recipient, msg.as_string())

        print(f"[REMEDIATION] Alert email sent to {recipient}")
        return True

    except Exception as e:
        print(f"[REMEDIATION] Email failed: {e}")
        return False


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
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with propagate_attributes(tags=["remediation", "monitoring", "automated"]):
        langfuse.update_current_span(input={"date": today, "run_type": "remediation"})

        graph = build_remediation_graph()

        initial_state = {
            "papers_saved": None,
            "site_fresh": False,
            "site_remediated": False,
            "key1_pct": None,
            "key2_pct": None,
            "token_warning": False,
            "token_approval": "pending",
            "langfuse_traces": None,
            "groq_calls": None,
            "actions_taken": [],
            "alerts": [],
            "faults": [],
            "info": [],
            "email_sent": False,
            "run_timestamp": ""
        }

        config = {"configurable": {"thread_id": "remediation-1"}}
        result = graph.invoke(initial_state, config=config)

    print(f"\n[REMEDIATION] Complete.")
    print(f"  Papers: {fmt(result['papers_saved'])}")
    print(f"  Site fresh: {result['site_fresh']}")
    print(f"  Token warning: {result['token_warning']}")
    print(f"  Email sent: {result['email_sent']}")
    print(f"  Alerts: {len(result['alerts'])} | Faults: {sorted(set(result['faults']))}")

    langfuse.flush()
    return result


if __name__ == "__main__":
    run_remediation()
