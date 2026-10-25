"""remediation_agent.py: true values, email only on real faults, once per day per fault set. No network, no SMTP."""
import json
import os
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

for k, v in {"LANGFUSE_PUBLIC_KEY": "pk-test", "LANGFUSE_SECRET_KEY": "sk-test", "LANGFUSE_HOST": "http://127.0.0.1:9",
             "LANGFUSE_TRACING_ENABLED": "false", "LANGCHAIN_TRACING_V2": "false", "LANGSMITH_TRACING": "false"}.items():
    os.environ.setdefault(k, v)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import remediation_agent as ra

TODAY = datetime.now().strftime("%Y-%m-%d")
UTC_TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")


def write(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data))


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A healthy day's pipeline output in a temp repo, with SMTP and the network replaced."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("network blocked")))
    monkeypatch.setenv("GMAIL_USER", "scout@example.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "test-password")
    monkeypatch.delenv("REMEDIATION_DRY_RUN", raising=False)
    sent = []

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def login(self, *a): pass
        def sendmail(self, frm, to, msg): sent.append(msg)

    monkeypatch.setattr(ra.smtplib, "SMTP_SSL", FakeSMTP)

    write("site/index.html", {})
    write(f"case_studies/{TODAY}.json", {"total_fetched": 150, "case_studies": [{"total_score": 60, "gemini_eval_status": "pass"}] * 2})
    write("monitoring/metrics.json", [{"date": TODAY, "article_count": 2, "total_fetched": 150, "judge_passed": 3,
                                       "rank_cut_below_min": 1, "rank_cut_beyond_top": 0}])
    write("site/groq_token_stats.json", {"generated_at": TODAY + "T11:00:00", "key_1": {"total_tokens": 12000, "calls": 4},
                                         "key_2": {"total_tokens": 0, "calls": 0}, "tokens_per_call": [3000] * 4})
    write("site/langfuse_snapshot.json", {"generated_at": UTC_TODAY + "T11:06:00+00:00", "trace_counts": [
        {"traceName": "case-study-scout", "time_dimension": UTC_TODAY, "count_count": 1},
        {"traceName": "industry-news-scout", "time_dimension": UTC_TODAY, "count_count": 1},
        {"traceName": "case-study-scout", "time_dimension": "2000-01-01", "count_count": 9}]})
    return sent


def zero_saved(judge_passed):
    write(f"case_studies/{TODAY}.json", {"case_studies": []})
    write("monitoring/metrics.json", [{"date": TODAY, "article_count": 0, "total_fetched": 150, "judge_passed": judge_passed,
                                       "rank_cut_below_min": 11, "rank_cut_beyond_top": 0}])


def test_healthy_run_sends_nothing(repo, capsys):
    result = ra.run_remediation()
    out = capsys.readouterr().out
    assert repo == [] and result["email_sent"] is False
    assert "no alert" in out and "Papers fetched today:     150" in out
    assert result["papers_saved"] == 2 and result["key1_pct"] == 12.0 and result["langfuse_traces"] == 2
    assert not Path("data/remediation_last_alert.json").exists()


def test_rank_cut_is_info_not_fault(repo, capsys):
    zero_saved(judge_passed=11)
    result = ra.run_remediation()
    out = capsys.readouterr().out
    assert repo == [] and result["faults"] == []
    assert "The judge passed 11, ranking cut 11" in out


def test_real_fault_sends_once_and_same_fault_twice_a_day_sends_once(repo, capsys):
    zero_saved(judge_passed=0)
    first = ra.run_remediation()
    second = ra.run_remediation()
    assert first["email_sent"] is True and second["email_sent"] is False
    assert len(repo) == 1
    assert "already emailed today" in capsys.readouterr().out
    state = json.loads(Path("data/remediation_last_alert.json").read_text())
    assert state == {"date": UTC_TODAY, "sent": [["papers_zero"]]}


def test_new_fault_set_same_day_sends_again(repo):
    zero_saved(judge_passed=0)
    ra.run_remediation()
    write("site/langfuse_snapshot.json", {"generated_at": UTC_TODAY + "T12:00:00+00:00", "trace_counts": []})
    ra.run_remediation()
    assert len(repo) == 2


def test_missing_files_show_not_checked(repo, capsys):
    Path("site/groq_token_stats.json").unlink()
    Path("site/langfuse_snapshot.json").write_text("{not json")
    result = ra.run_remediation()
    out = capsys.readouterr().out
    assert result["key1_pct"] is None and result["langfuse_traces"] is None
    assert "Key 1:                    Not checked" in out
    assert "LangFuse traces today:    Not checked" in out
    assert "Avg tokens per call:      Not checked" in out
    assert repo == []


def test_dry_run_prints_and_never_touches_smtp(repo, monkeypatch, capsys):
    monkeypatch.setenv("REMEDIATION_DRY_RUN", "1")
    monkeypatch.setattr(ra.smtplib, "SMTP_SSL", lambda *a, **k: pytest.fail("SMTP contacted in dry run"))
    zero_saved(judge_passed=0)
    result = ra.run_remediation()
    out = capsys.readouterr().out
    assert result["email_sent"] is False
    assert "DRY RUN - would send alert" in out and "SCOUT DAILY - Pipeline Digest" in out
    assert not Path("data/remediation_last_alert.json").exists()
