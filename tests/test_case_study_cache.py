"""Cache wiring in case_study_scout_langgraph.py. Mock Groq only; all sockets blocked, so no tokens can be spent."""
import os
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

for k, v in {"GROQ_API_KEY": "test-key-1", "GROQ_API_KEY_2": "", "GEMINI_API_KEY": "", "LANGFUSE_PUBLIC_KEY": "pk-test",
             "LANGFUSE_SECRET_KEY": "sk-test", "LANGFUSE_HOST": "http://127.0.0.1:9", "LANGFUSE_TRACING_ENABLED": "false",
             "LANGCHAIN_TRACING_V2": "false", "LANGSMITH_TRACING": "false", "LANGCHAIN_API_KEY": ""}.items():
    os.environ[k] = v

NETWORK_ATTEMPTS = []


def _blocked(*args, **kwargs):
    NETWORK_ATTEMPTS.append(args)
    raise RuntimeError("network blocked in tests")


socket.socket.connect = _blocked
socket.create_connection = _blocked

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import llm_cache
import case_study_scout_langgraph as cs

SCORE_JSON = '{"real_world_impact": 10, "ai_maturity": 10, "monetisation_potential": 10, "total_score": 55, "low_signal": false}'
SUMMARY_JSON = '{"summary": "It does X.", "it_breakdown": "Not directly applicable to IT infrastructure work"}'


class FakeGroq:
    """Stands in for groq.Groq; records every completion request (each would be one network call)."""

    def __init__(self, responder):
        self.calls, self.responder = [], responder

    def __call__(self, api_key=None):
        create = lambda **kw: (self.calls.append(kw), self._resp(self.responder(kw)))[1]
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    @staticmethod
    def _resp(text):
        usage = SimpleNamespace(prompt_tokens=100, completion_tokens=20, total_tokens=120)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))], usage=usage)


def by_prompt(score=SCORE_JSON, summary=SUMMARY_JSON):
    return lambda kw: score if "scoring AI research papers" in kw["messages"][0]["content"] else summary


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_cache, "CACHE_FILE", str(tmp_path / "groq_cache.json"))
    llm_cache.load()
    logged = []
    monkeypatch.setattr(cs.langfuse, "update_current_generation", lambda **kw: logged.append(kw))
    monkeypatch.setattr(cs, "gemini_evaluate", lambda *a, **k: {})
    monkeypatch.setattr(cs.time, "sleep", lambda s: None)
    NETWORK_ATTEMPTS.clear()
    return SimpleNamespace(logged=logged, path=tmp_path / "groq_cache.json")


def papers(n=2):
    return [{"id": f"p{i}", "title": f"Agentic infra paper {i}", "authors": ["A. Author"], "institution": "MIT",
             "pub_date": "2026-10-01", "abstract": f"We build an agent for cloud infrastructure number {i}. It works.",
             "url": f"https://arxiv.org/abs/{i}"} for i in range(n)]


def state():
    return {"raw_papers": papers(), "scored_papers": [], "output_file": "unused.json"}


def new_run():
    llm_cache.save()
    llm_cache.load()


def test_same_request_twice_second_makes_no_call(env, monkeypatch):
    fake = FakeGroq(by_prompt())
    monkeypatch.setattr(cs, "Groq", fake)
    msgs = [{"role": "user", "content": "You are scoring AI research papers ... Title: X"}]
    first = cs.groq_complete(msgs, max_tokens=400, model=cs.MODEL_SCORING, validate=cs._parse_json_output)
    second = cs.groq_complete(msgs, max_tokens=400, model=cs.MODEL_SCORING, validate=cs._parse_json_output)
    assert first == second == SCORE_JSON
    assert len(fake.calls) == 1
    assert NETWORK_ATTEMPTS == []
    assert [e["metadata"]["cache_hit"] for e in env.logged] == [False, True]
    assert env.logged[1]["usage_details"] == {"input": 0, "output": 0, "total": 0}


@pytest.mark.parametrize("change", ["model", "prompt", "max_tokens"])
def test_request_change_is_a_miss(env, monkeypatch, change):
    fake = FakeGroq(by_prompt())
    monkeypatch.setattr(cs, "Groq", fake)
    base = dict(messages=[{"role": "user", "content": "You are scoring AI research papers ... Title: X"}],
                max_tokens=400, model=cs.MODEL_SCORING, validate=cs._parse_json_output)
    cs.groq_complete(**base)
    changed = {**base}
    if change == "model":
        changed["model"] = "openai/gpt-oss-120b"
    elif change == "prompt":
        changed["messages"] = [{"role": "user", "content": "You are scoring AI research papers ... Title: Y"}]
    else:
        changed["max_tokens"] = 500
    cs.groq_complete(**changed)
    assert len(fake.calls) == 2
    assert env.logged[1]["metadata"]["cache_hit"] is False


def test_score_and_summary_second_run_makes_zero_groq_calls(env, monkeypatch):
    fake = FakeGroq(by_prompt())
    monkeypatch.setattr(cs, "Groq", fake)
    run1 = cs.score_papers(state())["scored_papers"]
    assert len(fake.calls) == 4  # 2 papers x (score + summary)
    new_run()
    run2 = cs.score_papers(state())["scored_papers"]
    assert len(fake.calls) == 4
    assert run1 == run2
    assert NETWORK_ATTEMPTS == []
    assert sum(e["metadata"]["cache_hit"] for e in env.logged) == 4
    assert all("cache_hit" in e["metadata"] for e in env.logged)


def test_malformed_json_is_never_cached(env, monkeypatch):
    fake = FakeGroq(by_prompt(score="Sorry, here is the score: seven"))
    monkeypatch.setattr(cs, "Groq", fake)
    cs.score_papers(state())
    assert len(fake.calls) == 6  # 3 retries per paper, every one a real call
    new_run()
    assert llm_cache.load() == {}
    cs.score_papers(state())
    assert len(fake.calls) == 12
    assert NETWORK_ATTEMPTS == []
