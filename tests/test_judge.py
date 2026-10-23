"""judge.py batch judging: one call per 25 items, id matching, errors held back, retry on 429/5xx. Gemini mocked."""
import json
import os
import random
import socket
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

for k, v in {"GROQ_API_KEY": "test-key-1", "GROQ_API_KEY_2": "", "GEMINI_API_KEY": "", "LANGFUSE_PUBLIC_KEY": "pk-test",
             "LANGFUSE_SECRET_KEY": "sk-test", "LANGFUSE_HOST": "http://127.0.0.1:9", "LANGFUSE_TRACING_ENABLED": "false",
             "LANGCHAIN_TRACING_V2": "false", "LANGSMITH_TRACING": "false", "LANGCHAIN_API_KEY": ""}.items():
    os.environ.setdefault(k, v)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import judge


class ApiError(Exception):
    def __init__(self, code):
        super().__init__(f"{code} error")
        self.code = code


class FakeGemini:
    """Stands in for judge._ask. `answer(chunk_items)` builds the reply; every call and its time are recorded."""

    def __init__(self, monkeypatch, answer):
        self.t, self.calls, self.slept, self.answer = 1000.0, [], [], answer
        monkeypatch.setattr(judge, "clock", lambda: self.t)
        monkeypatch.setattr(judge, "sleep", self._sleep)
        monkeypatch.setattr(judge, "_calls", [])
        monkeypatch.setattr(judge, "_ask", self._ask)
        monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("network blocked")))

    def _sleep(self, s):
        self.slept.append(s)
        self.t += s

    def _ask(self, prompt):
        items = json.loads(prompt.split("ITEMS (JSON list; each has id, title, source, summary, action):\n", 1)[1].split("\n\nReturn JSON only", 1)[0])
        self.calls.append([it["id"] for it in items])
        reply = self.answer(items, len(self.calls))
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, str) else json.dumps(reply)


def verdicts(items, rule=lambda it: ("pass", 0.9)):
    return {"verdicts": [{"id": it["id"], "verdict": rule(it)[0], "score": rule(it)[1], "reason": f"r-{it['title']}"} for it in items]}


def make(n):
    return [{"title": f"T{i}", "summary_raw": f"src {i}", "summary": f"sum {i}", "enterprise_use": f"act {i}"} for i in range(n)]


def gate(items):
    return judge.gate(items, "title", "summary_raw", "summary", "enterprise_use", log=lambda *_: None)


def test_25_items_is_one_call(monkeypatch):
    g = FakeGemini(monkeypatch, lambda items, n: verdicts(items))
    kept, held, stats = gate(make(25))
    assert len(g.calls) == 1 and len(g.calls[0]) == 25 and len(kept) == 25 and stats["pass_rate"] == 1.0


def test_30_items_is_two_calls(monkeypatch):
    g = FakeGemini(monkeypatch, lambda items, n: verdicts(items))
    kept, _, _ = gate(make(30))
    assert [len(c) for c in g.calls] == [25, 5] and len(kept) == 30


def test_missing_verdict_is_error_and_held_back(monkeypatch):
    g = FakeGemini(monkeypatch, lambda items, n: verdicts([it for it in items if it["title"] != "T3"]))
    items = make(5)
    kept, held, stats = gate(items)
    assert held == [items[3]] and items[3] not in kept and stats["errors"] == 1 and stats["passed"] == 4


@pytest.mark.parametrize("damage", ["duplicate", "malformed_verdict", "score_out_of_range", "no_score"])
def test_duplicate_or_malformed_verdict_is_error(monkeypatch, damage):
    def answer(items, n):
        out = verdicts(items)
        v = out["verdicts"]
        if damage == "duplicate":
            v.append(dict(v[0]))
        elif damage == "malformed_verdict":
            v[0]["verdict"] = "maybe"
        elif damage == "score_out_of_range":
            v[0]["score"] = 1.4
        else:
            del v[0]["score"]
        return out
    FakeGemini(monkeypatch, answer)
    items = make(3)
    kept, held, stats = gate(items)
    assert held == [items[0]] and stats["errors"] == 1 and kept == items[1:]


def test_extra_verdict_is_ignored(monkeypatch):
    FakeGemini(monkeypatch, lambda items, n: {"verdicts": verdicts(items)["verdicts"] + [{"id": "999", "verdict": "pass", "score": 1.0}]})
    kept, _, stats = gate(make(2))
    assert len(kept) == 2 and stats["total"] == 2


def test_shuffled_verdicts_match_by_id(monkeypatch):
    rule = lambda it: ("fail", 0.1) if it["title"] in ("T1", "T4") else ("pass", 0.95)

    def answer(items, n):
        out = verdicts(items, rule)
        random.Random(7).shuffle(out["verdicts"])
        assert [v["id"] for v in out["verdicts"]] != [it["id"] for it in items]  # order really changed
        return out
    FakeGemini(monkeypatch, answer)
    items = make(6)
    kept, held, stats = gate(items)
    assert held == [items[1], items[4]] and kept == [items[0], items[2], items[3], items[5]]
    results = judge.judge_batch([{"id": str(i), "title": it["title"], "source": "", "summary": "", "action": ""} for i, it in enumerate(items)])
    assert results["1"]["reason"] == "r-T1" and results["4"]["verdict"] == "fail"


def test_pass_below_threshold_is_held_back(monkeypatch):
    FakeGemini(monkeypatch, lambda items, n: verdicts(items, lambda it: ("pass", 0.6)))
    kept, held, stats = gate(make(1))
    assert kept == [] and stats["failed"] == 1


def test_429_twice_holds_back_whole_chunk(monkeypatch):
    g = FakeGemini(monkeypatch, lambda items, n: ApiError(429))
    items = make(4)
    kept, held, stats = gate(items)
    assert kept == [] and held == items and stats["errors"] == 4
    assert len(g.calls) == 2 and g.slept == [judge.RETRY_DELAY]  # exactly one retry after 10 s


def test_503_then_success_is_retried(monkeypatch):
    g = FakeGemini(monkeypatch, lambda items, n: ApiError(503) if n == 1 else verdicts(items))
    kept, _, _ = gate(make(3))
    assert len(kept) == 3 and len(g.calls) == 2


def test_non_retryable_error_is_not_retried(monkeypatch):
    g = FakeGemini(monkeypatch, lambda items, n: ApiError(400))
    kept, _, stats = gate(make(2))
    assert kept == [] and stats["errors"] == 2 and len(g.calls) == 1


def test_unparseable_response_is_all_errors(monkeypatch):
    FakeGemini(monkeypatch, lambda items, n: "not json")
    kept, _, stats = gate(make(3))
    assert kept == [] and stats["errors"] == 3


def test_pacing_stays_under_12_a_minute(monkeypatch):
    g = FakeGemini(monkeypatch, lambda items, n: verdicts(items))
    times = []
    orig = g._ask

    def timed(prompt):
        times.append(g.t)
        return orig(prompt)
    monkeypatch.setattr(judge, "_ask", timed)
    gate(make(25 * 30))  # 30 calls
    assert len(times) == 30
    assert max(sum(1 for t in times if s <= t < s + 60) for s in times) <= judge.MAX_PER_MINUTE < 12


def test_send_scores_names_and_values():
    sent = []
    judge.send_scores(SimpleNamespace(score_current_trace=lambda **kw: sent.append((kw["name"], kw["value"]))),
                      {"total": 4, "passed": 2, "failed": 1, "errors": 1, "pass_rate": 0.5})
    assert sent == [("judge_pass_rate", 0.5), ("judge_fail", 1), ("judge_error", 1)]


# ---------- scout wiring: one batch per run, after the quality gate, before ranking / writing ----------

def _fake_batch(results, calls):
    def fake(items):
        calls.append(len(items))
        return {it["id"]: results.get(it["title"], {"verdict": "pass", "score": 0.9, "reason": "ok"}) for it in items}
    return fake


def test_case_scout_one_batch_before_ranking(tmp_path, monkeypatch):
    import case_study_scout_langgraph as cs
    import quality_checks
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(quality_checks.requests, "get", lambda *a, **k: SimpleNamespace(status_code=200, close=lambda: None))
    monkeypatch.setattr(cs.llm_cache, "save", lambda *a, **k: 0)
    today = datetime.now().strftime("%Y-%m-%d")
    papers = [{"id": f"p{t}", "title": f"Paper {t}", "abstract": "A.", "summary": "S.", "it_breakdown": "B.",
               "url": f"https://arxiv.org/abs/{t}", "pub_date": today, "real_world_impact": min(25, t),
               "ai_maturity": max(0, t - 25), "monetisation_potential": 0, "total_score": t} for t in (50, 45, 40, 35, 30, 25)]
    calls = []
    monkeypatch.setattr(judge, "judge_batch", _fake_batch({"Paper 50": {"verdict": "fail", "score": 0.1, "reason": "r"},
                                                          "Paper 40": {"verdict": "pass", "score": 0.5, "reason": "r"}}, calls))
    cs.save_and_archive({"raw_papers": papers, "scored_papers": papers, "output_file": "case_studies/out.json"})
    out = json.loads(Path("case_studies/out.json").read_text())["case_studies"]
    assert calls == [6]  # one batch call for the whole run
    assert [p["total_score"] for p in out] == [45, 35, 30, 25] and all(p["eval_verdict"] == "pass" for p in out)
    assert cs._judge == {"total": 6, "passed": 4, "failed": 2, "errors": 0, "pass_rate": pytest.approx(4 / 6)}


def test_news_scout_one_batch_before_writing(tmp_path, monkeypatch):
    import industry_news_scout_langgraph as news
    import quality_checks
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(quality_checks.requests, "get", lambda *a, **k: SimpleNamespace(status_code=200, close=lambda: None))
    monkeypatch.setattr(news.llm_cache, "save", lambda *a, **k: 0)
    monkeypatch.setattr(news, "generate_daily_hook", lambda items: None)
    items = [{"title": t, "summary_raw": "raw", "summary": "S.", "enterprise_use": "E.", "url": f"https://x/{i}", "source": "S",
              "pub_date": datetime.now().strftime("%d %b %Y"), "relevance_score": 8} for i, t in enumerate(("Good", "Wrong", "Broken"))]
    calls = []
    monkeypatch.setattr(judge, "judge_batch", _fake_batch({"Wrong": {"verdict": "fail", "score": 0.1, "reason": "swapped company"},
                                                          "Broken": {"verdict": "error", "score": 0.0, "reason": "judge failed"}}, calls))
    news.save_and_archive({"raw_articles": items, "classified_news": items, "output_file": "logs/news.json"})
    out = json.loads(Path("logs/news.json").read_text())["news"]
    assert calls == [3] and [x["title"] for x in out] == ["Good"] and news._run["written"] == 1
    assert news._judge["failed"] == 1 and news._judge["errors"] == 1
