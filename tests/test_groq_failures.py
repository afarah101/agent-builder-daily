"""Groq failures must show up: empty hooks are not published, all-failed scripts exit non-zero and fail the run, and
partial failures reach data/failed_items.json and the digest. Groq is faked and sockets are blocked, so no tokens can be spent."""
import json
import os
import re
import runpy
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

for k, v in {"GROQ_API_KEY": "test-key-1", "GROQ_API_KEY_2": "", "LANGFUSE_PUBLIC_KEY": "pk-test",
             "LANGFUSE_SECRET_KEY": "sk-test", "LANGFUSE_HOST": "http://127.0.0.1:9", "LANGFUSE_TRACING_ENABLED": "false",
             "LANGCHAIN_TRACING_V2": "false", "LANGSMITH_TRACING": "false", "LANGCHAIN_API_KEY": ""}.items():
    os.environ.setdefault(k, v)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import requests
import judge
import llm_cache
import quality_checks
import send_daily_email
import industry_news_scout_langgraph as news

GOOD_HOOK = " ".join(["word"] * 22)
GOOD_CLASSIFY = ('{"summary": "S.", "enterprise_use": "E.", "relevance_score": 8, "relevance_signal": "high", '
                 '"category": "Tools"}')
GOOD_BREAKDOWN = "SECTION_SUMMARY: Summary.\n---SPLIT---\nSECTION_IT_GUIDE: Guide."
ALL_FAILED = int(re.search(r"EXIT_ALL_GROQ_CALLS_FAILED = (\d+)", (ROOT / "orchestrator.py").read_text()).group(1))


class FakeGroq:
    """responder(kw) returns the reply text, or raises like the real client would."""

    def __init__(self, responder):
        self.calls, self.responder = [], responder

    def __call__(self, api_key=None):
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=self._create)))

    def _create(self, **kw):
        self.calls.append(kw)
        reply = SimpleNamespace(message=SimpleNamespace(content=self.responder(kw)))
        return SimpleNamespace(choices=[reply], usage=None)


def prompt_of(kw):
    return kw["messages"][0]["content"]


def unauthorized(kw):
    raise Exception("Error code: 401 - Invalid API Key")


def _blocked(*args, **kwargs):
    raise RuntimeError("network blocked in tests")


def _no_http(*args, **kwargs):
    raise requests.ConnectionError("network blocked in tests")


def run_as_script(name):
    """Run the real file as `python name` would, and return its exit code."""
    try:
        runpy.run_path(str(ROOT / name), run_name="__main__")
    except SystemExit as e:
        return e.code
    return 0


def failed_items():
    return json.loads(Path("data/failed_items.json").read_text())


@pytest.fixture
def scout(tmp_path, monkeypatch):
    (tmp_path / "data" / "news").mkdir(parents=True)
    (tmp_path / "case_studies").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(requests, "get", _no_http)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setenv("GROQ_API_KEY", "test-key-1")
    monkeypatch.setenv("GROQ_API_KEY_2", "")
    monkeypatch.setattr(llm_cache, "CACHE_FILE", str(tmp_path / "groq_cache.json"))
    llm_cache.load()
    return monkeypatch


# ---------- hook ----------

@pytest.fixture
def hook(scout, monkeypatch):
    """Runs generate_daily_hook with a fake Groq, starting from yesterday's hook on disk."""
    monkeypatch.setattr(news, "_groq_calls", {"attempted": 0, "failed": 0})
    monkeypatch.setattr(news, "_failed_items", {"classify": 0, "hook": 0})
    spans = []
    monkeypatch.setattr(news.langfuse, "update_current_span", lambda **kw: spans.append(kw))
    Path("data/daily_hook.txt").write_text("Yesterday's hook.")

    def run(responder):
        fake = FakeGroq(responder)
        monkeypatch.setattr(news, "Groq", fake)
        news.generate_daily_hook([{"title": "A"}, {"title": "B"}])
        return fake

    return SimpleNamespace(run=run, spans=spans)


def test_empty_hook_retries_once_with_the_reason_then_fails_and_is_not_published(hook, capsys):
    fake = hook.run(lambda kw: "")
    assert len(fake.calls) == 2
    assert "it was empty" not in prompt_of(fake.calls[0]) and "it was empty" in prompt_of(fake.calls[1])
    assert all(c["max_tokens"] == 800 for c in fake.calls)
    assert news._failed_items["hook"] == 1
    assert not Path("data/daily_hook.txt").exists()  # nor does yesterday's hook stand in for it
    assert [s["level"] for s in hook.spans] == ["ERROR"]
    assert "[HOOK] Failed" in capsys.readouterr().out


def test_empty_hook_is_a_failed_item_not_a_failed_call(hook):
    hook.run(lambda kw: "")
    assert news._groq_calls == {"attempted": 2, "failed": 0}
    assert not news.every_groq_call_failed()


def test_empty_hook_recovered_by_the_retry_is_published(hook):
    replies = iter(["", GOOD_HOOK])
    fake = hook.run(lambda kw: next(replies))
    assert len(fake.calls) == 2
    assert Path("data/daily_hook.txt").read_text() == GOOD_HOOK
    assert news._failed_items["hook"] == 0 and hook.spans == []


def test_good_hook_makes_one_call(hook):
    assert len(hook.run(lambda kw: GOOD_HOOK).calls) == 1
    assert Path("data/daily_hook.txt").read_text() == GOOD_HOOK


def test_hook_api_error_is_marked_failed_and_not_published(hook, capsys):
    fake = hook.run(unauthorized)
    assert len(fake.calls) == 1
    assert news._failed_items["hook"] == 1
    assert news._groq_calls == {"attempted": 1, "failed": 1}
    assert news.every_groq_call_failed()
    assert not Path("data/daily_hook.txt").exists()
    assert [s["level"] for s in hook.spans] == ["ERROR"]
    assert "[GROQ] Call failed (news.daily_hook): Error code: 401 - Invalid API Key" in capsys.readouterr().out


def test_api_error_on_the_retry_keeps_the_short_hook(hook):
    def short_then_error(kw):
        if "last attempt" in prompt_of(kw):
            unauthorized(kw)
        return "Too short."

    hook.run(short_then_error)
    assert Path("data/daily_hook.txt").read_text() == "Too short."
    assert news._groq_calls == {"attempted": 2, "failed": 1}
    assert news._failed_items["hook"] == 0


# ---------- the news scout, run as a script ----------

def run_news(monkeypatch, responder, titles=("Good one", "Good two", "Failing three")):
    fake = FakeGroq(responder)
    entries = [{"title": t, "link": f"https://x/{i}", "published": format_datetime(datetime.now(timezone.utc)),
                "summary": "Body. " * 100} for i, t in enumerate(titles)]
    monkeypatch.setattr("groq.Groq", fake)
    monkeypatch.setattr("feedparser.parse", lambda url: SimpleNamespace(entries=entries))
    monkeypatch.setattr(quality_checks, "gate", lambda items, **k: (items, [], []))
    monkeypatch.setattr(judge, "gate", lambda items, *a: (items, [], {"total": len(items)}))
    monkeypatch.setattr(quality_checks, "send_scores", lambda *a: None)
    monkeypatch.setattr(judge, "send_scores", lambda *a: None)
    return run_as_script("industry_news_scout_langgraph.py"), fake


def test_news_exits_non_zero_when_every_call_fails(scout, capsys):
    code, fake = run_news(scout, unauthorized)
    assert code == ALL_FAILED and len(fake.calls) == 3
    assert failed_items()["news"] == {"groq_calls": 3, "failed_calls": 3, "failed_items": 3}
    out = capsys.readouterr().out
    assert out.count("[GROQ] Call failed (news.classify): Error code: 401") == 3
    assert "[GROQ] News: 3 of 3 Groq calls failed" in out


def test_news_continues_and_counts_when_some_calls_fail(scout, capsys):
    def respond(kw):
        if "Failing three" in prompt_of(kw) and "UK AI newsletter" not in prompt_of(kw):
            raise Exception("Error code: 500 - boom")
        return GOOD_HOOK if "UK AI newsletter" in prompt_of(kw) else GOOD_CLASSIFY

    code, fake = run_news(scout, respond)
    assert code == 0
    assert failed_items()["news"] == {"groq_calls": 4, "failed_calls": 1, "failed_items": 1}  # 3 classify + 1 hook
    assert Path("data/daily_hook.txt").read_text() == GOOD_HOOK
    assert "[GROQ] Call failed (news.classify): Error code: 500 - boom" in capsys.readouterr().out
    saved = json.loads(next(Path("data/news").glob("news_*.json")).read_text())["news"]
    assert sorted(x["title"] for x in saved) == ["Good one", "Good two"]


# ---------- breakdowns, run as a script ----------

def run_breakdowns(monkeypatch, responder, titles):
    Path("data/news/news_2026-11-02.json").write_text(json.dumps({"news": [{"title": t} for t in titles]}))
    fake = FakeGroq(responder)
    monkeypatch.setattr("groq.Groq", fake)
    return run_as_script("generate_breakdowns.py"), fake


def test_breakdowns_exit_non_zero_when_every_call_fails(scout, capsys):
    code, fake = run_breakdowns(scout, unauthorized, ["One", "Two"])
    assert code == ALL_FAILED and len(fake.calls) == 6  # 3 attempts per item
    assert failed_items()["breakdowns"] == {"groq_calls": 6, "failed_calls": 6, "failed_items": 2}
    assert not list(Path("site/breakdowns").glob("*.html"))
    out = capsys.readouterr().out
    assert "Groq error (attempt 3): Error code: 401" in out
    assert "[GROQ] Breakdowns: 6 of 6 Groq calls failed" in out


def test_breakdowns_exit_zero_when_no_live_call_was_needed(scout):
    Path("site/breakdowns").mkdir(parents=True)
    Path("site/breakdowns/one.html").write_text("already built")
    code, fake = run_breakdowns(scout, unauthorized, ["One"])
    assert code == 0 and fake.calls == []
    assert failed_items()["breakdowns"] == {"groq_calls": 0, "failed_calls": 0, "failed_items": 0}


def test_breakdowns_count_partial_failures_and_the_digest_states_the_total(scout):
    Path("data/failed_items.json").write_text(json.dumps(
        {"date": "2026-11-02", "news": {"groq_calls": 14, "failed_calls": 1, "failed_items": 1}}))

    def respond(kw):
        if "Failing" in prompt_of(kw):
            raise Exception("Error code: 500 - boom")
        return "no split marker" if "Garbled" in prompt_of(kw) else GOOD_BREAKDOWN

    code, _ = run_breakdowns(scout, respond, ["Good one", "Good two", "Failing three", "Garbled four"])
    assert code == 0
    report = failed_items()
    assert report["breakdowns"] == {"groq_calls": 6, "failed_calls": 3, "failed_items": 2}  # 3 single calls + 3 attempts
    assert report["news"]["failed_items"] == 1  # the news scout's count survives
    assert len(list(Path("site/breakdowns").glob("*.html"))) == 2
    notice = send_daily_email.failed_items_notice(send_daily_email.load_failed_items())
    assert notice == f"3 items failed in the last scout run ({report['date']}): 1 news, 2 breakdowns."


def test_daily_limit_counts_every_item_left_without_a_page(scout):
    def daily_limit(kw):
        raise Exception("Rate limit reached: tokens per day")

    Path("site/breakdowns").mkdir(parents=True)
    Path("site/breakdowns/already-built.html").write_text("already built")
    code, fake = run_breakdowns(scout, daily_limit, ["First", "Already built", "Second", "Third"])
    assert code == ALL_FAILED and len(fake.calls) == 1
    assert failed_items()["breakdowns"]["failed_items"] == 3


# ---------- digest wording ----------

@pytest.mark.parametrize("report", [{}, [], {"news": {"failed_items": 0}}, {"news": "broken"}, {"news": {"failed_items": "x"}}])
def test_digest_is_quiet_with_no_failures_or_a_broken_report(report):
    assert send_daily_email.failed_items_notice(report) == ""


def test_digest_keeps_one_count_when_the_other_section_is_broken():
    report = {"date": "2026-11-02", "news": {"failed_items": 1}, "breakdowns": None}
    assert send_daily_email.failed_items_notice(report) == "1 item failed in the last scout run (2026-11-02): 1 news, 0 breakdowns."


# ---------- the orchestrator fails the whole run ----------

STAGES = ["case_study_scout_langgraph.py", "industry_news_scout_langgraph.py", "generate_breakdowns.py", "generate_html.py",
          "langfuse_proxy.py", "remediation_agent.py", "creator_watch.py"]


def run_orchestrator(tmp_path, exit_codes):
    """The real orchestrator over stub stages that record that they ran, print a log line, and exit as told."""
    for stage in STAGES:
        (tmp_path / stage).write_text(
            f"import pathlib, sys\npathlib.Path('ran_{stage}').touch()\nprint('log from {stage}')\n"
            f"sys.exit({exit_codes.get(stage, 0)})\n")
    done = subprocess.run([sys.executable, str(ROOT / "orchestrator.py")], cwd=tmp_path, capture_output=True, text=True,
                          encoding="utf-8", timeout=60)
    return done, [s for s in STAGES if (tmp_path / f"ran_{s}").exists()]


@pytest.mark.parametrize("stage", ["industry_news_scout_langgraph.py", "generate_breakdowns.py"])
def test_orchestrator_fails_the_run_when_every_call_fails(tmp_path, stage):
    done, ran = run_orchestrator(tmp_path, {stage: ALL_FAILED})
    assert done.returncode != 0
    assert ran[-1] == stage  # no later stage ran
    assert f"CRITICAL ERROR in {stage}" in done.stdout
    assert f"log from {stage}" in done.stdout  # the failed calls' log lines are not lost
    assert "Success!" not in done.stdout


def test_orchestrator_still_tolerates_other_breakdown_failures(tmp_path):
    done, ran = run_orchestrator(tmp_path, {"generate_breakdowns.py": 1})
    assert done.returncode == 0 and ran == STAGES
    assert "WARNING: generate_breakdowns.py failed" in done.stdout and "Success!" in done.stdout
