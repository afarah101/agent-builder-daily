"""News full-text fetch: thin snippets get the article body (capped at 3000); any failure keeps the snippet. No network."""
import os
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

for k, v in {"GROQ_API_KEY": "test-key-1", "GROQ_API_KEY_2": "", "LANGFUSE_PUBLIC_KEY": "pk-test", "LANGFUSE_SECRET_KEY": "sk-test",
             "LANGFUSE_HOST": "http://127.0.0.1:9", "LANGFUSE_TRACING_ENABLED": "false", "LANGCHAIN_TRACING_V2": "false",
             "LANGSMITH_TRACING": "false", "LANGCHAIN_API_KEY": ""}.items():
    os.environ.setdefault(k, v)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import industry_news_scout_langgraph as news


@pytest.fixture
def web(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("network blocked")))
    state = SimpleNamespace(gets=[], body="Article body. " * 400, fail=None)

    def get(url, headers=None, timeout=None):
        state.gets.append((url, timeout))
        if state.fail:
            raise state.fail
        return SimpleNamespace(text="<html>" + state.body + "</html>", raise_for_status=lambda: None)
    monkeypatch.setattr(news.requests, "get", get)
    monkeypatch.setattr(news.trafilatura, "extract", lambda html, **kw: html.removeprefix("<html>").removesuffix("</html>"))
    return state


def article(snippet):
    return {"title": "T", "summary_raw": snippet, "url": "https://example.com/a"}


def test_short_snippet_triggers_fetch_with_10s_timeout(web):
    text = news.fetch_source_text(article("Short teaser."))
    assert web.gets == [("https://example.com/a", 10)] and text.startswith("Article body.")


def test_full_text_is_capped_at_3000(web):
    assert len(news.fetch_source_text(article("x"))) == 3000


def test_long_snippet_is_not_fetched(web):
    snippet = "y" * 400
    assert news.fetch_source_text(article(snippet)) == snippet and web.gets == []


@pytest.mark.parametrize("failure", ["network", "empty_extract"])
def test_fetch_failure_keeps_the_snippet(web, monkeypatch, failure):
    if failure == "network":
        web.fail = TimeoutError("timed out")
    else:
        monkeypatch.setattr(news.trafilatura, "extract", lambda html, **kw: None)
    assert news.fetch_source_text(article("Short teaser.")) == "Short teaser."


def test_classify_uses_source_text_up_to_3000(monkeypatch):
    prompts = []
    monkeypatch.setattr(news, "groq_complete", lambda messages, **kw: prompts.append(messages[0]["content"]) or
                        '{"summary": "s", "enterprise_use": "e", "relevance_score": 7, "relevance_signal": "high", "category": "C"}')
    body = "B" * 5000
    news.classify_news({"raw_articles": [{"title": "T", "source": "S", "summary_raw": "short", "source_text": body, "url": "u"}],
                        "classified_news": [], "output_file": "x.json"})
    assert "B" * 3000 in prompts[0] and "B" * 3001 not in prompts[0]
