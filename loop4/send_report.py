#!/usr/bin/env python3
"""Email loop4/out/report.json in plain English. --dry-run prints instead of sending."""
import argparse
import html
from datetime import datetime, timezone

from dotenv import load_dotenv

from loop4.merge_findings import BOTH_AGREE, REPORT_FILE, Report
from send_daily_email import smtp_send

load_dotenv()


def describe_trace_count(ids):
    return f"{len(ids)} run" if len(ids) == 1 else f"{len(ids)} runs"


def render_agreed(n, f):
    return (f"{n}. [{f.severity}] {f.category}: {f.description}\n"
            f"   Fix: {f.suggested_fix}\n"
            f"   File: {f.target_file}\n"
            f"   Seen in {describe_trace_count(f.evidence_trace_ids)}: {', '.join(f.evidence_trace_ids[:3])}")


def render_check(n, f):
    return (f"{n}. [{f.severity}] {f.category}, {f.label}: {f.description}\n"
            f"   Seen in {describe_trace_count(f.evidence_trace_ids)}: {', '.join(f.evidence_trace_ids[:3])}")


def render_body(report):
    agreed = [f for f in report.findings if f.label == BOTH_AGREE]
    others = [f for f in report.findings if f.label != BOTH_AGREE]
    lines = [
        "Scout Daily weekly agent review",
        "",
        f"Runs in the last 7 days: {report.run_count}",
        f"Error rate: {report.error_rate:.0%} ({report.error_count} of {report.run_count} runs)",
        f"Newest run: {report.newest_trace}",
    ]
    if report.fetch_failures:
        lines.append(f"Runs that could not be fetched: {report.fetch_failures}")
    for r in report.readers:
        failed = f", {r.failed_batches} of {r.batches} batches failed" if r.failed_batches else ""
        lines.append(f"{r.reader.title()} ({r.model}) read {r.traces_reviewed} of {report.run_count} runs{failed}")
    lines += ["", "FAULTS BOTH READERS AGREE ON", ""]
    lines += [render_agreed(n, f) for n, f in enumerate(agreed, 1)] or ["None this week."]
    lines += ["", "CHECK THIS (only one reader saw it, so no fix is suggested)", ""]
    lines += [render_check(n, f) for n, f in enumerate(others, 1)] or ["None this week."]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="print the email, send nothing")
    args = parser.parse_args()
    with open(REPORT_FILE, encoding="utf-8") as f:
        report = Report.model_validate_json(f.read())
    subject = f"Scout Daily — weekly agent review {datetime.now(timezone.utc).date()}"
    body = render_body(report)
    if args.dry_run:
        print(f"[LOOP4] DRY RUN, nothing sent\nSubject: {subject}\n\n{body}")
        return
    html_body = f'<pre style="white-space:pre-wrap;font-family:Arial,sans-serif;font-size:15px">{html.escape(body)}</pre>'
    print(f"[LOOP4] SMTP: {smtp_send(subject, html_body, body)}")


if __name__ == "__main__":
    main()
