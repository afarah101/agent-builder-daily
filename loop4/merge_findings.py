#!/usr/bin/env python3
"""Merge the two Readers' findings into loop4/out/report.json.

Findings join when they share a category and at least one trace. A group holding both Readers is "both agree".
"""
import json
import sys
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel

from loop4.review_traces import Finding, OUT_DIR, TRACES_FILE

REPORT_FILE = f"{OUT_DIR}/report.json"
BOTH_AGREE = "both agree"
SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}
READER_NAMES = {"groq": "Groq", "gemini": "Gemini"}


class MergedFinding(Finding):
    suggested_fix: Optional[str]  # only "both agree" keeps a fix
    label: str
    readers: list[str]


class ReaderSummary(BaseModel):
    reader: str
    model: str
    batches: int
    failed_batches: int
    traces_reviewed: int
    findings: list[Finding]


class Report(BaseModel):
    generated_at: str
    run_count: int
    error_count: int
    error_rate: float
    newest_trace: str
    fetch_failures: int
    readers: list[ReaderSummary]
    findings: list[MergedFinding]


def reader_of(finding):
    return finding.id.split("-")[0]


def group_findings(findings):
    """Connected groups: same category and overlapping trace ids, within or across Readers."""
    groups = []
    for finding in findings:
        evidence = set(finding.evidence_trace_ids)
        touching = [g for g in groups if g["category"] == finding.category and g["traces"] & evidence]
        merged = {"category": finding.category, "traces": evidence, "members": [finding]}
        for g in touching:
            merged["traces"] |= g["traces"]
            merged["members"] = g["members"] + merged["members"]
            groups.remove(g)
        groups.append(merged)
    return groups


def merge_group(n, group):
    members = sorted(group["members"], key=lambda f: SEVERITY_RANK[f.severity])
    lead = members[0]
    readers = sorted({reader_of(f) for f in members})
    agreed = len(readers) == 2
    known_targets = [f.target_file for f in members if f.target_file != "unknown"]
    return MergedFinding(
        id=f"M{n}",
        category=lead.category,
        description=lead.description,
        evidence_trace_ids=sorted(group["traces"]),
        severity=lead.severity,
        suggested_fix=lead.suggested_fix if agreed else None,
        target_file=known_targets[0] if known_targets else "unknown",
        label=BOTH_AGREE if agreed else f"only one ({READER_NAMES[readers[0]]})",
        readers=readers,
    )


def merge(findings):
    groups = group_findings(findings)
    merged = [merge_group(n, g) for n, g in enumerate(groups, 1)]
    return sorted(merged, key=lambda m: (m.label != BOTH_AGREE, SEVERITY_RANK[m.severity]))


def self_check():
    def finding(id, category, traces):
        return Finding(id=id, category=category, description=id, evidence_trace_ids=traces, severity="low",
                       suggested_fix=f"fix {id}", target_file="judge.py")
    merged = merge([finding("groq-1", "latency", ["a"]), finding("gemini-1", "latency", ["a", "b"]),
                    finding("groq-2", "latency", ["z"]), finding("gemini-2", "rate_limit", ["a"])])
    assert len(merged) == 3, merged
    agreed = [m for m in merged if m.label == BOTH_AGREE]
    assert len(agreed) == 1 and agreed[0].evidence_trace_ids == ["a", "b"] and agreed[0].suggested_fix, merged
    assert merged[0].label == BOTH_AGREE, "agreed faults sort first"
    assert {m.label for m in merged[1:]} == {"only one (Groq)", "only one (Gemini)"}, merged
    assert all(m.suggested_fix is None for m in merged[1:]), "one reader alone keeps no fix"
    print("[LOOP4] merge self-check passed")


def main():
    if "--self-check" in sys.argv:
        return self_check()
    with open(TRACES_FILE, encoding="utf-8") as f:
        fetched = json.load(f)
    traces = fetched["traces"]
    results = []
    for name in READER_NAMES:
        with open(f"{OUT_DIR}/findings_{name}.json", encoding="utf-8") as f:
            results.append(json.load(f))
    errors = sum(t["status"] == "error" for t in traces)
    report = Report(
        generated_at=datetime.now(timezone.utc).isoformat(),
        run_count=len(traces),
        error_count=errors,
        error_rate=errors / len(traces),
        newest_trace=max(t["timestamp"] for t in traces),
        fetch_failures=fetched["fetch_failures"],
        readers=[ReaderSummary(traces_reviewed=len(r["reviewed_trace_ids"]), **{k: r[k] for k in ("reader", "model", "batches", "failed_batches", "findings")}) for r in results],
        findings=merge([Finding(**f) for r in results for f in r["findings"]]),
    )
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(report.model_dump_json(indent=1))
    agreed = sum(m.label == BOTH_AGREE for m in report.findings)
    print(f"[LOOP4] Report: {report.run_count} runs, {agreed} agreed, {len(report.findings) - agreed} check-this")


if __name__ == "__main__":
    main()
