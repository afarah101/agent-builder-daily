"""remediation_agent.py: true values, email only on real faults, once per day per fault set. No network, no SMTP."""
import email
import json
import os
import socket
import subprocess
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


def subject(msg):
    return email.message_from_string(msg)["Subject"]


@pytest.fixture
def utc_clock(monkeypatch):
    """Pin remediation_agent's clock to hh:mm UTC on today's UTC date."""
    def set_clock(hh, mm=0):
        fixed = datetime.fromisoformat(f"{UTC_TODAY}T{hh:02d}:{mm:02d}:00+00:00")

        class FixedDT(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed.astimezone(tz) if tz else fixed.astimezone().replace(tzinfo=None)

        monkeypatch.setattr(ra, "datetime", FixedDT)
    return set_clock


def test_load_json_missing_or_malformed_is_none(tmp_path):
    (tmp_path / "bad.json").write_text("{not json")
    assert ra.load_json(tmp_path / "missing.json") is None
    assert ra.load_json(tmp_path / "bad.json") is None


def test_missing_case_file_is_not_checked_and_a_fault(repo, capsys):
    Path(f"case_studies/{TODAY}.json").unlink()
    result = ra.run_remediation()
    assert result["papers_saved"] is None and "papers_file_missing" in result["faults"]
    assert "Papers saved today:       Not checked" in capsys.readouterr().out
    assert len(repo) == 1


def test_fault_emailed_yesterday_is_emailed_again_today(repo):
    zero_saved(judge_passed=0)
    write("data/remediation_last_alert.json", {"date": "2000-01-01", "sent": [["papers_zero"]]})
    assert ra.run_remediation()["email_sent"] is True and len(repo) == 1
    assert json.loads(Path("data/remediation_last_alert.json").read_text()) == {"date": UTC_TODAY, "sent": [["papers_zero"]]}


def test_failed_send_writes_no_state_and_retries(repo, monkeypatch):
    class FailingSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def login(self, *a): raise ra.smtplib.SMTPAuthenticationError(535, b"bad creds")

    zero_saved(judge_passed=0)
    real_smtp = ra.smtplib.SMTP_SSL
    monkeypatch.setattr(ra.smtplib, "SMTP_SSL", FailingSMTP)
    assert ra.run_remediation()["email_sent"] is False
    assert not Path("data/remediation_last_alert.json").exists()
    monkeypatch.setattr(ra.smtplib, "SMTP_SSL", real_smtp)
    assert ra.run_remediation()["email_sent"] is True and len(repo) == 1


def test_earlier_fault_set_today_is_not_resent(repo):
    zero_saved(judge_passed=0)
    snapshot = Path("site/langfuse_snapshot.json").read_text()
    ra.run_remediation()                                     # ["papers_zero"]
    write("site/langfuse_snapshot.json", {"generated_at": UTC_TODAY + "T12:00:00+00:00", "trace_counts": []})
    ra.run_remediation()                                     # ["langfuse_no_rows", "papers_zero"]
    Path("site/langfuse_snapshot.json").write_text(snapshot)
    assert ra.run_remediation()["email_sent"] is False       # ["papers_zero"] again
    assert len(repo) == 2
    state = json.loads(Path("data/remediation_last_alert.json").read_text())
    assert state["sent"] == [["papers_zero"], ["langfuse_no_rows", "papers_zero"]]


def test_token_notice_is_info_not_fault(repo, capsys):
    write("site/groq_token_stats.json", {"generated_at": TODAY + "T11:00:00", "key_1": {"total_tokens": 60000, "calls": 4},
                                         "key_2": {"total_tokens": 0, "calls": 0}, "tokens_per_call": [15000] * 4})
    result = ra.run_remediation()
    assert result["faults"] == [] and repo == []
    assert "TOKEN NOTICE: Key 1 at 60.0%" in capsys.readouterr().out


def test_stale_site_auto_remediated_is_info_not_fault(repo, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **k: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
    old = datetime.now(timezone.utc).timestamp() - 48 * 3600
    os.utime("site/index.html", (old, old))
    result = ra.run_remediation()
    assert result["site_remediated"] is True and result["faults"] == [] and repo == []
    assert ["python3", "generate_html.py"] in calls
    assert "INFO: Site was stale" in capsys.readouterr().out


def test_stale_groq_stats_are_not_checked(repo, capsys):
    write("site/groq_token_stats.json", {"generated_at": "2000-01-01T11:00:00", "key_1": {"total_tokens": 12000, "calls": 4},
                                         "key_2": {"total_tokens": 0, "calls": 0}, "tokens_per_call": [3000] * 4})
    result = ra.run_remediation()
    assert result["key1_pct"] is None and result["groq_calls"] is None
    assert "Key 1:                    Not checked" in capsys.readouterr().out


def test_stale_langfuse_snapshot_is_not_checked(repo, capsys):
    write("site/langfuse_snapshot.json", {"generated_at": "2000-01-01T11:06:00+00:00",
                                          "trace_counts": [{"traceName": "case-study-scout", "time_dimension": UTC_TODAY, "count_count": 5}]})
    result = ra.run_remediation()
    assert result["langfuse_traces"] is None
    assert "LangFuse traces today:    Not checked" in capsys.readouterr().out


def test_all_time_total_traces_is_not_used_as_today(repo, capsys):
    write("site/langfuse_snapshot.json", {"total_traces": 412})
    result = ra.run_remediation()
    out = capsys.readouterr().out
    assert result["langfuse_traces"] is None and "langfuse_zero" not in result["faults"]
    assert "only the all-time total_traces (412)" in out
    assert "LangFuse traces today:    Not checked" in out


@pytest.mark.parametrize("snapshot", [{"generated_at": UTC_TODAY + "T11:06:00+00:00", "trace_counts": []},
                                      {"generated_at": UTC_TODAY + "T11:06:00+00:00"}])
def test_empty_or_missing_trace_counts_is_not_checked_never_zero(repo, capsys, snapshot):
    write("site/langfuse_snapshot.json", snapshot)
    result = ra.run_remediation()
    assert result["langfuse_traces"] is None and "langfuse_zero" not in result["faults"]
    assert "LangFuse traces today:    Not checked" in capsys.readouterr().out


def test_no_trace_row_for_today_is_a_true_zero(repo, utc_clock):
    utc_clock(12)
    write("site/langfuse_snapshot.json", {"generated_at": UTC_TODAY + "T11:59:00+00:00",
                                          "trace_counts": [{"traceName": "case-study-scout", "time_dimension": "2000-01-01", "count_count": 3}]})
    result = ra.run_remediation()
    assert result["langfuse_traces"] == 0 and "langfuse_zero" in result["faults"]


def test_no_trace_row_just_after_utc_midnight_is_not_checked(repo, utc_clock):
    utc_clock(0, 5)
    write("site/langfuse_snapshot.json", {"generated_at": UTC_TODAY + "T00:04:00+00:00",
                                          "trace_counts": [{"traceName": "case-study-scout", "time_dimension": "2000-01-01", "count_count": 3}]})
    result = ra.run_remediation()
    assert result["langfuse_traces"] is None and "langfuse_zero" not in result["faults"]


@pytest.mark.parametrize("path,content,label", [
    ("monitoring/metrics.json", [1], "Passed the judge:         Not checked"),
    ("monitoring/metrics.json", [{"date": TODAY, "judge_passed": "11", "rank_cut_below_min": None}], "Passed the judge:         Not checked"),
    (f"case_studies/{TODAY}.json", {"case_studies": None}, "Papers saved today:       Not checked"),
    (f"case_studies/{TODAY}.json", {"case_studies": "abc"}, "Papers saved today:       Not checked"),
    (f"case_studies/{TODAY}.json", {"total_fetched": 150}, "Papers saved today:       Not checked"),
    ("site/groq_token_stats.json", {"generated_at": TODAY + "T06:00:00", "key_1": []}, "Key 1:                    Not checked"),
    ("site/groq_token_stats.json", {"generated_at": TODAY + "T06:00:00", "key_1": {"total_tokens": "5", "calls": 1},
                                    "key_2": {"total_tokens": 0, "calls": 0}}, "Key 1:                    Not checked"),
    ("site/groq_token_stats.json", {"generated_at": TODAY + "T06:00:00", "key_1": {"total_tokens": 5, "calls": 1},
                                    "key_2": {"total_tokens": 0, "calls": 0}, "daily_limit_per_key": 0}, "Key 1:                    Not checked"),
    ("site/groq_token_stats.json", {"generated_at": TODAY + "T06:00:00"}, "Live Groq calls:          Not checked"),
    ("site/groq_token_stats.json", {"generated_at": TODAY + "T06:00:00", "key_1": {"total_tokens": 5, "calls": 1},
                                    "key_2": {"total_tokens": 0, "calls": 0}, "tokens_per_call": "abc"}, "Avg tokens per call:      Not checked"),
    ("site/langfuse_snapshot.json", {"generated_at": UTC_TODAY + "T06:09:00+00:00", "trace_counts": [1]}, "LangFuse traces today:    Not checked"),
    ("site/langfuse_snapshot.json", {"generated_at": UTC_TODAY + "T06:09:00+00:00",
                                     "trace_counts": [{"time_dimension": UTC_TODAY, "count_count": "abc"}]}, "LangFuse traces today:    Not checked"),
])
def test_wrong_type_values_are_not_checked_not_a_crash(repo, capsys, path, content, label):
    write(path, content)
    ra.run_remediation()
    assert label in capsys.readouterr().out


def test_wrong_type_judge_fields_with_zero_saved_do_not_crash(repo):
    write(f"case_studies/{TODAY}.json", {"case_studies": []})
    write("monitoring/metrics.json", [{"date": TODAY, "judge_passed": 11, "rank_cut_below_min": None}])
    result = ra.run_remediation()
    assert result["faults"] == [] and any("ranking cut Not checked" in i for i in result["info"])


def test_corrupted_state_file_counts_as_no_state(repo):
    zero_saved(judge_passed=0)
    write("data/remediation_last_alert.json", {"date": UTC_TODAY, "sent": "papers_zero"})
    assert ra.run_remediation()["email_sent"] is True and len(repo) == 1


def test_subject_never_says_ok_with_an_alert(repo, utc_clock):
    utc_clock(12)
    write("site/langfuse_snapshot.json", {"generated_at": UTC_TODAY + "T11:59:00+00:00",
                                          "trace_counts": [{"traceName": "case-study-scout", "time_dimension": "2000-01-01", "count_count": 3}]})
    result = ra.run_remediation()
    assert result["faults"] == ["langfuse_zero"] and len(repo) == 1
    assert " OK " not in subject(repo[0]) and "WARNING" in subject(repo[0])


@pytest.mark.parametrize("score,health", [(60, "OK (at or above the 30/75"), (20, "POOR - average is below the 30/75")])
def test_scores_use_the_75_scale(repo, capsys, score, health):
    write(f"case_studies/{TODAY}.json", {"total_fetched": 150, "case_studies": [{"total_score": score, "gemini_eval_status": "pass"}] * 2})
    ra.run_remediation()
    out = capsys.readouterr().out
    assert f"Average score today:      {score}/75" in out and "/100" not in out
    assert f"Score health:             {health}" in out
    assert ("Average score is below 30/75" in out) is (score < 30)


@pytest.mark.parametrize("value,expected", [("1", True), ("true", True), ("yes", True), ("TRUE", True), (" Yes ", True),
                                            ("0", False), ("false", False), ("", False)])
def test_dry_run_flag_values(monkeypatch, value, expected):
    monkeypatch.setenv("REMEDIATION_DRY_RUN", value)
    assert ra.is_dry_run() is expected
