import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import quality_checks as qc

TODAY = datetime.now().strftime("%Y-%m-%d")


def case(**kw):
    item = {"title": "Agent Ops at Scale", "summary": "A study of agent operations.", "url": "https://arxiv.org/abs/1",
            "pub_date": TODAY, "real_world_impact": 10, "ai_maturity": 12, "monetisation_potential": 8}
    item.update(kw)
    return item


def news(**kw):
    item = {"title": "OpenAI models are coming to Google Cloud", "summary": "OpenAI and Google Cloud partner.",
            "url": "https://example.com/n", "pub_date": datetime.now().strftime("%d %b %Y"), "relevance_score": 7}
    item.update(kw)
    return item


@pytest.fixture(autouse=True)
def fake_http(monkeypatch):
    status = {"code": 200}
    monkeypatch.setattr(qc.requests, "get", lambda *a, **k: SimpleNamespace(status_code=status["code"], close=lambda: None))
    return status


def results(item):
    return {c: (r, why) for c, r, why in qc.run_checks(item)}


def test_score_28_of_25_fails():
    assert qc.c1_score_range(case(ai_maturity=28))[0] == qc.FAIL


def test_negative_and_non_numeric_scores_fail():
    assert qc.c1_score_range(case(real_world_impact=-1))[0] == qc.FAIL
    assert qc.c1_score_range(case(real_world_impact="high"))[0] == qc.FAIL


def test_news_score_range_is_0_to_10():
    assert qc.c1_score_range(news(relevance_score=10))[0] == qc.PASS
    assert qc.c1_score_range(news(relevance_score=11))[0] == qc.FAIL


def test_all_zero_fails():
    assert qc.c2_not_all_zero(case(real_world_impact=0, ai_maturity=0, monetisation_potential=0))[0] == qc.FAIL
    assert qc.c2_not_all_zero(case(real_world_impact=0, ai_maturity=1, monetisation_potential=0))[0] == qc.PASS


def test_missing_url_fails():
    item = case()
    del item["url"]
    assert qc.c3_required_fields(item)[0] == qc.FAIL
    assert qc.c3_required_fields(case(summary="  "))[0] == qc.FAIL
    assert results(item)["C5"][0] == qc.FAIL


def test_valid_item_passes_every_check():
    assert all(r == qc.PASS for r, _ in results(case()).values())
    assert all(r == qc.PASS for r, _ in results(news()).values())


def test_date_window():
    old = (datetime.now() - timedelta(days=121)).strftime("%Y-%m-%d")
    assert qc.c4_date_window(case(pub_date=old))[0] == qc.FAIL
    old_news = (datetime.now() - timedelta(weeks=9)).strftime("%d %b %Y")
    assert qc.c4_date_window(news(pub_date=old_news))[0] == qc.FAIL
    assert qc.c4_date_window(case(pub_date="not a date"))[0] == qc.FAIL


def test_url_status_and_network_error(fake_http, monkeypatch):
    fake_http["code"] = 301
    assert qc.c5_url_live(case())[0] == qc.PASS
    fake_http["code"] = 404
    assert qc.c5_url_live(case())[0] == qc.FAIL
    fake_http["code"] = 403
    assert qc.c5_url_live(case())[0] == qc.FLAG

    def boom(*a, **k):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(qc.requests, "get", boom)
    assert qc.c5_url_live(case())[0] == qc.FLAG


def test_company_missing_from_summary_is_flagged():
    assert qc.c6_company_in_summary(news(summary="A cloud partnership."))[0] == qc.FLAG
    assert qc.c6_company_in_summary(case(title="HiddenLens: Detecting Leaks", summary="A leak detector."))[0] == qc.FLAG
    assert qc.c6_company_in_summary(case(title="Honeypots for AI Agent Insider Threats", summary="x"))[0] == qc.PASS


def test_gate_holds_back_fails_and_keeps_flags(fake_http):
    good, bad, flagged = case(), case(ai_maturity=28, title="Bad"), news(summary="A cloud partnership.")
    kept, held, res = qc.gate([good, bad, flagged], log=lambda *_: None)
    assert kept == [good, flagged] and held == [bad]
    s = qc.summarize(res)
    assert s["pass_rate"] == pytest.approx(2 / 3) and list(s["fails"]) == ["C1"]


def test_send_scores_uses_given_client():
    calls = []
    client = SimpleNamespace(score_current_trace=lambda **kw: calls.append(kw))
    qc.send_scores(client, {"total": 3, "passed": 2, "pass_rate": 2 / 3, "fails": {"C1": ["Bad: out of range"]}})
    assert [c["name"] for c in calls] == ["quality_pass_rate", "quality_fail_C1"]
    assert calls[0]["value"] == pytest.approx(0.6667) and calls[1]["value"] == 1


# --- scout wiring: clamp, one retry on all-zero, score_failed held back (Groq mocked, no network) ---

@pytest.fixture
def cs(monkeypatch, tmp_path):
    import os
    for k, v in {"GROQ_API_KEY": "test-key-1", "GROQ_API_KEY_2": "", "GEMINI_API_KEY": "", "LANGFUSE_PUBLIC_KEY": "pk-test",
                 "LANGFUSE_SECRET_KEY": "sk-test", "LANGFUSE_HOST": "http://127.0.0.1:9", "LANGFUSE_TRACING_ENABLED": "false",
                 "LANGCHAIN_TRACING_V2": "false", "LANGSMITH_TRACING": "false", "LANGCHAIN_API_KEY": ""}.items():
        os.environ.setdefault(k, v)
    import case_study_scout_langgraph as mod
    import llm_cache
    monkeypatch.setattr(llm_cache, "CACHE_FILE", str(tmp_path / "c.json"))
    llm_cache.load()
    monkeypatch.setattr(mod.langfuse, "update_current_generation", lambda **kw: None)
    monkeypatch.setattr(mod, "gemini_evaluate", lambda *a, **k: {})
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    return mod


def run_scoring(mod, monkeypatch, score_replies):
    replies, calls = list(score_replies), []

    def create(**kw):
        scoring = "scoring AI research papers" in kw["messages"][0]["content"]
        calls.append("score" if scoring else "summary")
        text = replies.pop(0) if scoring else '{"summary": "It works.", "it_breakdown": "n/a"}'
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))], usage=None)
    monkeypatch.setattr(mod, "Groq", lambda api_key=None: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    paper = {"id": "p1", "title": "Agent Ops", "authors": ["A"], "institution": "MIT", "pub_date": TODAY,
             "abstract": "We study agents in infra.", "url": "https://arxiv.org/abs/1"}
    out = mod.score_papers({"raw_papers": [paper], "scored_papers": [], "output_file": "x.json"})["scored_papers"]
    return out, calls


def score(i, m, mon):
    return f'{{"real_world_impact": {i}, "ai_maturity": {m}, "monetisation_potential": {mon}, "total_score": 50}}'


def test_scout_clamps_score_above_25(cs, monkeypatch):
    out, _ = run_scoring(cs, monkeypatch, [score(25, 28, 20)])
    assert out[0]["ai_maturity"] == 25 and out[0]["total_score"] == 70
    assert qc.c1_score_range(out[0])[0] == qc.PASS


def test_scout_all_zero_retries_once_then_uses_good_retry(cs, monkeypatch):
    out, calls = run_scoring(cs, monkeypatch, [score(0, 0, 0), score(5, 6, 7)])
    assert calls == ["score", "score", "summary"]
    assert out[0]["ai_maturity"] == 6 and "score_failed" not in out[0]


def test_scout_all_zero_twice_marks_score_failed_and_gate_holds_back(cs, monkeypatch):
    out, calls = run_scoring(cs, monkeypatch, [score(0, 0, 0), score(0, 0, 0)])
    assert calls == ["score", "score"]  # no summary call for a failed score
    assert out[0]["score_failed"] is True
    kept, held, _ = qc.gate(out, log=lambda *_: None)
    assert kept == [] and held == out
