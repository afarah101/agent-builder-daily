"""judge.py: verdict rules, one retry, errors held back, pacing under 12 a minute, scout wiring. Gemini mocked; no network."""
import json
import os
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


class FakeTime:
    """Fake clock: sleep() advances it, so pacing and the 10 s retry are checked without waiting."""

    def __init__(self):
        self.t, self.slept = 1000.0, []

    def clock(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


@pytest.fixture
def fake(monkeypatch):
    ft = FakeTime()
    monkeypatch.setattr(judge, "clock", ft.clock)
    monkeypatch.setattr(judge, "sleep", ft.sleep)
    monkeypatch.setattr(judge, "_calls", [])
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("network blocked")))
    ft.asked = []

    def script(*replies):
        it = iter(replies)

        def ask(prompt):
            ft.asked.append(ft.t)
            r = next(it)
            if isinstance(r, Exception):
                raise r
            return json.dumps(r)
        monkeypatch.setattr(judge, "_ask", ask)
    ft.script = script
    return ft


ITEM = {"title": "OpenAI models are coming to Google Cloud", "summary_raw": "OpenAI models come to Google Cloud.",
        "summary": "OpenAI models arrive on Google Cloud.", "enterprise_use": "Check Vertex AI model access."}


def run_gate(items):
    return judge.gate(items, "title", "summary_raw", "summary", "enterprise_use", log=lambda *_: None)


def test_fail_verdict_is_held_back(fake):
    fake.script({"verdict": "fail", "score": 0.2, "reason": "summary names Gemini"})
    kept, held, stats = run_gate([ITEM])
    assert kept == [] and held == [ITEM] and stats["failed"] == 1 and stats["pass_rate"] == 0.0


def test_pass_below_threshold_is_held_back(fake):
    fake.script({"verdict": "pass", "score": 0.6, "reason": "mostly faithful"})
    kept, held, stats = run_gate([ITEM])
    assert kept == [] and held == [ITEM] and stats["failed"] == 1


def test_pass_at_threshold_and_above_is_kept(fake):
    fake.script({"verdict": "pass", "score": 0.9, "reason": "faithful"}, {"verdict": "pass", "score": 0.7, "reason": "ok"})
    kept, held, stats = run_gate([ITEM, {**ITEM, "title": "Second"}])
    assert len(kept) == 2 and held == [] and stats["pass_rate"] == 1.0


def test_error_twice_is_held_back_and_counted(fake):
    fake.script(RuntimeError("429 RESOURCE_EXHAUSTED"), RuntimeError("429 RESOURCE_EXHAUSTED"))
    kept, held, stats = run_gate([ITEM])
    assert kept == [] and held == [ITEM] and stats["errors"] == 1 and stats["passed"] == 0
    assert fake.slept == [judge.RETRY_DELAY] and len(fake.asked) == 2  # exactly one retry, 10 s apart
    assert fake.asked[1] - fake.asked[0] >= 10


def test_error_then_pass_is_kept(fake):
    fake.script(RuntimeError("timeout"), {"verdict": "pass", "score": 0.95, "reason": "faithful"})
    kept, _, stats = run_gate([ITEM])
    assert kept == [ITEM] and stats["errors"] == 0


@pytest.mark.parametrize("bad", [{"verdict": "maybe", "score": 0.9}, {"verdict": "pass", "score": 1.5}, {"verdict": "pass"}])
def test_malformed_judge_output_is_an_error_not_a_pass(fake, bad):
    fake.script(bad, bad)
    r = judge.judge_item("t", "s", "s", "a")
    assert r["verdict"] == "error" and not judge.passed(r)


def test_pacing_never_exceeds_12_calls_a_minute(fake):
    fake.script(*[{"verdict": "pass", "score": 0.9, "reason": ""}] * 40)
    run_gate([{**ITEM, "title": f"T{i}"} for i in range(40)])
    calls = fake.asked
    assert len(calls) == 40
    worst = max(sum(1 for c in calls if start <= c < start + 60) for start in calls)
    assert worst <= judge.MAX_PER_MINUTE < 12


def test_send_scores_names_and_values():
    sent = []
    judge.send_scores(SimpleNamespace(score_current_trace=lambda **kw: sent.append((kw["name"], kw["value"]))),
                      {"total": 4, "passed": 2, "failed": 1, "errors": 1, "pass_rate": 0.5})
    assert sent == [("judge_pass_rate", 0.5), ("judge_fail", 1), ("judge_error", 1)]


# ---------- scout wiring: judge runs after the quality gate, before ranking / writing ----------

def test_case_scout_judge_runs_before_ranking(tmp_path, monkeypatch, fake):
    import case_study_scout_langgraph as cs
    import quality_checks
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(quality_checks.requests, "get", lambda *a, **k: SimpleNamespace(status_code=200, close=lambda: None))
    monkeypatch.setattr(cs.llm_cache, "save", lambda *a, **k: 0)
    today = datetime.now().strftime("%Y-%m-%d")
    papers = [{"id": f"p{t}", "title": f"Paper {t}", "abstract": "A.", "summary": "S.", "it_breakdown": "B.",
               "url": f"https://arxiv.org/abs/{t}", "pub_date": today, "real_world_impact": min(25, t),
               "ai_maturity": max(0, t - 25), "monetisation_potential": 0, "total_score": t} for t in (50, 45, 40, 35, 30, 25)]
    verdicts = {"Paper 50": ("fail", 0.1), "Paper 40": ("pass", 0.5)}
    monkeypatch.setattr(judge, "judge_item", lambda title, *a: dict(zip(("verdict", "score"), verdicts.get(title, ("pass", 0.9))), reason="r"))
    cs.save_and_archive({"raw_papers": papers, "scored_papers": papers, "output_file": "case_studies/out.json"})
    out = json.loads(Path("case_studies/out.json").read_text())["case_studies"]
    assert [p["total_score"] for p in out] == [45, 35, 30, 25]  # judge-failed 50 and 40 never take a top-5 place
    assert all(p["eval_verdict"] == "pass" for p in out)
    assert cs._judge == {"total": 6, "passed": 4, "failed": 2, "errors": 0, "pass_rate": pytest.approx(4 / 6)}


def test_news_scout_judge_holds_back_before_writing(tmp_path, monkeypatch, fake):
    import industry_news_scout_langgraph as news
    import quality_checks
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(quality_checks.requests, "get", lambda *a, **k: SimpleNamespace(status_code=200, close=lambda: None))
    monkeypatch.setattr(news.llm_cache, "save", lambda *a, **k: 0)
    monkeypatch.setattr(news, "generate_daily_hook", lambda items: None)
    items = [{"title": t, "summary_raw": "raw", "summary": "S.", "enterprise_use": "E.", "url": f"https://x/{i}", "source": "S",
              "pub_date": datetime.now().strftime("%d %b %Y"), "relevance_score": 8} for i, t in enumerate(("Good", "Wrong", "Broken"))]
    results = {"Wrong": {"verdict": "fail", "score": 0.1, "reason": "swapped company"},
               "Broken": {"verdict": "error", "score": 0.0, "reason": "judge failed twice"}}
    monkeypatch.setattr(judge, "judge_item", lambda title, *a: results.get(title, {"verdict": "pass", "score": 0.9, "reason": "ok"}))
    news.save_and_archive({"raw_articles": items, "classified_news": items, "output_file": "logs/news.json"})
    out = json.loads(Path("logs/news.json").read_text())["news"]
    assert [x["title"] for x in out] == ["Good"] and news._run["written"] == 1
    assert news._judge["failed"] == 1 and news._judge["errors"] == 1
