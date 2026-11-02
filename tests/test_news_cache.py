"""Cache wiring in industry_news_scout_langgraph.py. Mock Groq only; all sockets blocked, so no tokens can be spent."""
import os
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

for k, v in {"GROQ_API_KEY": "test-key-1", "GROQ_API_KEY_2": "", "LANGFUSE_PUBLIC_KEY": "pk-test",
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
import industry_news_scout_langgraph as news

GOOD = '{"summary": "Vendor ships agent.", "enterprise_use": "Automate tickets.", "relevance_score": 8, "relevance_signal": "high", "category": "Agents"}'


class FakeGroq:
    def __init__(self, responder):
        self.calls, self.responder = [], responder

    def __call__(self, api_key=None):
        create = lambda **kw: (self.calls.append(kw), self._resp(self.responder(kw)))[1]
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    @staticmethod
    def _resp(text):
        usage = SimpleNamespace(prompt_tokens=90, completion_tokens=30, total_tokens=120)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))], usage=usage)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_cache, "CACHE_FILE", str(tmp_path / "groq_cache.json"))
    llm_cache.load()
    logged = []
    monkeypatch.setattr(news.langfuse, "update_current_generation", lambda **kw: logged.append(kw))
    NETWORK_ATTEMPTS.clear()
    return SimpleNamespace(logged=logged)


def articles(n=5):
    return [{"title": f"AI news item {i}", "source": "TechCrunch", "summary_raw": f"Body of item {i}.", "url": f"https://x/{i}"}
            for i in range(n)]


def state():
    return {"raw_articles": articles(), "classified_news": [], "output_file": "unused.json"}


def test_classify_same_5_items_twice_second_run_zero_calls(env, monkeypatch):
    fake = FakeGroq(lambda kw: GOOD)
    monkeypatch.setattr(news, "Groq", fake)
    run1 = news.classify_news(state())["classified_news"]
    assert len(fake.calls) == 5
    llm_cache.save(); llm_cache.load()
    run2 = news.classify_news(state())["classified_news"]
    assert len(fake.calls) == 5
    assert run1 == run2 and all(a["relevance_score"] == 8 for a in run2)
    assert NETWORK_ATTEMPTS == []
    hits = [e["metadata"]["cache_hit"] for e in env.logged]
    assert hits == [False] * 5 + [True] * 5
    assert all(e["usage_details"]["total"] == 0 for e in env.logged[5:])


@pytest.mark.parametrize("bad", ["not json at all", '{"relevance_score": "high"}'])
def test_unparseable_classification_is_not_cached(env, monkeypatch, bad):
    fake = FakeGroq(lambda kw: bad)
    monkeypatch.setattr(news, "Groq", fake)
    news.classify_news(state())
    llm_cache.save()
    assert llm_cache.load() == {}
    news.classify_news(state())
    assert len(fake.calls) == 10


def test_daily_hook_is_tagged_but_never_cached(env, monkeypatch, tmp_path):
    fake = FakeGroq(lambda kw: "Agents are eating IT tickets.")
    monkeypatch.setattr(news, "Groq", fake)
    monkeypatch.chdir(tmp_path)
    news.generate_daily_hook([{"title": "A"}, {"title": "B"}])
    news.generate_daily_hook([{"title": "A"}, {"title": "B"}])
    # A 5-word hook is under the 20-word minimum, so each run calls Groq twice, and neither call is cached.
    assert len(fake.calls) == 4
    prompts = [c["messages"][0]["content"] for c in fake.calls]
    assert ["it was only 5 words" in p for p in prompts] == [False, True, False, True]
    assert [e["metadata"]["cache_hit"] for e in env.logged] == [False] * 4
    assert [e["metadata"]["call_site"] for e in env.logged] == ["news.daily_hook"] * 4
    assert llm_cache.load() == {}
    assert (tmp_path / "data" / "daily_hook.txt").read_text() == "Agents are eating IT tickets."  # short but not empty: published
