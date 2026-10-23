"""Phase 0d fixes: classify_failed held back, retry bypasses cache, empty data alert, --force once a day, saved counts.
Groq is faked and every socket is blocked."""
import json
import os
import socket
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

for k, v in {"GROQ_API_KEY": "test-key-1", "GROQ_API_KEY_2": "", "GEMINI_API_KEY": "", "LANGFUSE_PUBLIC_KEY": "pk-test",
             "LANGFUSE_SECRET_KEY": "sk-test", "LANGFUSE_HOST": "http://127.0.0.1:9", "LANGFUSE_TRACING_ENABLED": "false",
             "LANGCHAIN_TRACING_V2": "false", "LANGSMITH_TRACING": "false", "LANGCHAIN_API_KEY": ""}.items():
    os.environ.setdefault(k, v)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import llm_cache
import judge
import quality_checks
import case_study_scout_langgraph as cs
import industry_news_scout_langgraph as news
import send_daily_email as de

TODAY = datetime.now().strftime("%Y-%m-%d")


class FakeGroq:
    """Stands in for groq.Groq. responder(kwargs) returns text or raises; every call is recorded."""

    def __init__(self, responder):
        self.calls, self.responder = [], responder

    def __call__(self, api_key=None):
        def create(**kw):
            self.calls.append(kw["messages"][0]["content"])
            text = self.responder(kw)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))], usage=None)
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    def count(self, needle):
        return sum(needle in c for c in self.calls)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("network blocked")))
    # one shared requests module: URL checks (C5) and the Langfuse snapshot fetch both get this fake
    monkeypatch.setattr(quality_checks.requests, "get", lambda *a, **k: SimpleNamespace(status_code=200, close=lambda: None, json=lambda: {"data": []}))
    monkeypatch.setattr(llm_cache, "CACHE_FILE", str(tmp_path / "cache.json"))
    llm_cache.load()
    spans = []
    for mod in (cs, news):
        monkeypatch.setattr(mod.langfuse, "update_current_generation", lambda **kw: None)
        monkeypatch.setattr(mod.langfuse, "update_current_span", lambda **kw: spans.append(kw))
        monkeypatch.setattr(mod.langfuse, "score_current_trace", lambda **kw: None)
        monkeypatch.setattr(mod.langfuse, "flush", lambda: None)
    monkeypatch.setattr(judge, "judge_batch", lambda items: {it["id"]: {"verdict": "pass", "score": 1.0, "reason": "stub"} for it in items})
    monkeypatch.setattr(cs.time, "sleep", lambda s: None)
    return SimpleNamespace(tmp=tmp_path, spans=spans)


# ---------- fix 1: classify error is held back and counted as classify_failed ----------

GOOD = '{"summary": "Vendor ships an agent.", "enterprise_use": "Automate tickets.", "relevance_score": 8, "relevance_signal": "high", "category": "Agents"}'


def articles():
    return [{"title": f"AI news item {i}", "source": "TechCrunch", "summary_raw": f"Body {i}.", "url": f"https://x/{i}",
             "pub_date": datetime.now().strftime("%d %b %Y")} for i in range(3)]


def test_classify_error_not_published_and_counted(env, monkeypatch):
    def responder(kw):
        if "AI news item 1" in kw["messages"][0]["content"]:
            raise RuntimeError("groq exploded")
        return GOOD if "News item" in kw["messages"][0]["content"] else "A hook."
    monkeypatch.setattr(news, "Groq", FakeGroq(responder))
    state = news.classify_news({"raw_articles": articles(), "classified_news": [], "output_file": "logs/news_x.json"})
    news.save_and_archive(state)
    written = json.loads(Path("logs/news_x.json").read_text())["news"]
    assert [w["title"] for w in written] == ["AI news item 0", "AI news item 2"]
    assert "classify_failed" in news._quality["fails"] and news._quality["total"] == 3
    assert news._quality["pass_rate"] == pytest.approx(2 / 3)


# ---------- fix 2 + fix 6: cached zero triggers a real Groq retry; papers_saved == items written ----------

ZERO_STR = '{"real_world_impact": "0", "ai_maturity": "0", "monetisation_potential": "0", "total_score": 0}'
GOOD_SCORE = '{"real_world_impact": 15, "ai_maturity": 10, "monetisation_potential": 12, "total_score": 37}'
SUMMARY = '{"summary": "It works.", "it_breakdown": "Not directly applicable to IT infrastructure work"}'


def paper():
    return {"id": "http://arxiv.org/abs/1", "title": "Agent Ops", "authors": ["A"], "institution": "MIT", "pub_date": TODAY,
            "abstract": "We study agents in infrastructure.", "url": "https://arxiv.org/abs/1"}


def test_cached_zero_retry_makes_real_groq_call(env, monkeypatch):
    replies = iter([ZERO_STR, ZERO_STR, GOOD_SCORE])

    def responder(kw):
        return next(replies) if "scoring AI research papers" in kw["messages"][0]["content"] else SUMMARY
    fake = FakeGroq(responder)
    monkeypatch.setattr(cs, "Groq", fake)
    state = lambda: {"raw_papers": [paper()], "scored_papers": [], "output_file": "x.json"}
    cs.score_papers(state())  # run 1: string zeros pass the cache gate and get cached; retry also zero
    assert fake.count("scoring AI research papers") == 2
    out = cs.score_papers(state())["scored_papers"]  # run 2: first call is a cache hit; retry must reach Groq
    assert fake.count("scoring AI research papers") == 3
    assert out[0]["total_score"] == 37 and "score_failed" not in out[0]


def test_papers_saved_equals_items_written(env, monkeypatch):
    papers = [{**paper(), "id": f"http://arxiv.org/abs/{i}", "url": f"https://arxiv.org/abs/{i}", "title": f"Agent Ops {i}"} for i in range(7)]
    monkeypatch.setattr(cs, "fetch_papers", lambda state: {"raw_papers": papers, "scored_papers": [], "output_file": state["output_file"]})
    monkeypatch.setattr(cs, "Groq", FakeGroq(lambda kw: GOOD_SCORE if "scoring AI research papers" in kw["messages"][0]["content"] else SUMMARY))
    Path("monitoring").mkdir(); Path("site").mkdir()
    cs.run_case_study_scout()
    written = json.loads(Path(f"case_studies/{TODAY}.json").read_text())["case_studies"]
    saved = [s["output"]["papers_saved"] for s in env.spans if "papers_saved" in (s.get("output") or {})]
    assert len(written) == 5 and saved == [5]


def test_items_saved_equals_items_written(env, monkeypatch):
    monkeypatch.setattr(news, "fetch_news", lambda state: {"raw_articles": articles(), "classified_news": [], "output_file": state["output_file"]})
    monkeypatch.setattr(news, "Groq", FakeGroq(lambda kw: (_ for _ in ()).throw(RuntimeError("down"))
                                               if "AI news item 2" in kw["messages"][0]["content"] else (GOOD if "News item" in kw["messages"][0]["content"] else "A hook.")))
    news.run_industry_news_scout()
    written = json.loads(next(Path("logs").glob("news_*.json")).read_text())["news"]
    saved = [s["output"]["items_saved"] for s in env.spans if "items_saved" in (s.get("output") or {})]
    assert len(written) == 2 and saved == [2]


# ---------- fix 4 + fix 5: email ----------

LON = de.LONDON


@pytest.fixture
def email_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("network blocked")))
    sent = []
    return SimpleNamespace(send=lambda s, h, t: sent.append((s, t)) or "250 OK (mock)", sent=sent)


@pytest.mark.parametrize("setup", ["missing", "empty_lists", "bad_json"])
def test_empty_or_missing_data_sends_stale_alert(email_env, setup):
    if setup != "missing":
        Path("site").mkdir(); Path("case_studies").mkdir()
        body = "[]" if setup == "empty_lists" else "{not json"
        Path(de.NEWS_ARCHIVE).write_text(body); Path(de.CS_ARCHIVE).write_text(body)
    result = de.main([], now=datetime(2026, 10, 22, 11, 30, tzinfo=LON), sleep=lambda s: None, send=email_env.send)
    assert result == "stale_sent" and len(email_env.sent) == 1
    subject, text = email_env.sent[0]
    assert subject.startswith("Scout data is stale") and "missing or empty" in text


def test_force_twice_in_one_day_sends_one_email(email_env, monkeypatch):
    now = datetime(2026, 10, 24, 9, 0, tzinfo=LON)  # Saturday, before 11:00: --force skips only these rules
    news_item = {"title": "T", "summary": "S.", "enterprise_use": "E", "url": "https://x/1", "relevance_score": 9}
    monkeypatch.setattr(de, "load_data", lambda: {"news": [news_item], "papers": [], "newest": now - timedelta(hours=1)})
    results = [de.main(["--force"], now=now, sleep=lambda s: None, send=email_env.send) for _ in range(2)]
    assert results == ["sent", "already_sent"] and len(email_env.sent) == 1
