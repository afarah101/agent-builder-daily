"""Top-5 ranking in case_study_scout_langgraph.save_and_archive(): gate first, then rank. No network."""
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
import case_study_scout_langgraph as cs
import quality_checks
import judge


def item(total, title=None, **override):
    a = min(25, total); b = min(25, total - a); c = total - a - b
    return {"id": f"p{total}", "title": title or f"Paper {total}", "summary": "A useful study.",
            "url": f"https://arxiv.org/abs/{total}", "pub_date": datetime.now().strftime("%Y-%m-%d"),
            "real_world_impact": a, "ai_maturity": b, "monetisation_potential": c, "total_score": total, **override}


@pytest.fixture
def save(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(quality_checks.requests, "get", lambda *a, **k: SimpleNamespace(status_code=200, close=lambda: None))
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("network blocked")))
    monkeypatch.setattr(cs.llm_cache, "save", lambda *a, **k: 0)
    monkeypatch.setattr(judge, "judge_batch", lambda items: {it["id"]: {"verdict": "pass", "score": 1.0, "reason": "stub"} for it in items})

    def run(items):
        cs.save_and_archive({"raw_papers": items, "scored_papers": items, "output_file": "case_studies/out.json"})
        return [c["total_score"] for c in json.loads(Path("case_studies/out.json").read_text())["case_studies"]]
    return run


def test_top_five_above_minimum(save):
    assert save([item(t) for t in (21, 60, 10, 40, 25, 50, 19, 30)]) == [60, 50, 40, 30, 25]


def test_nothing_reaches_minimum(save):
    assert save([item(19), item(15)]) == []


def test_fewer_than_five_eligible(save):
    assert save([item(22), item(45)]) == [45, 22]


def test_failed_item_never_takes_a_top_five_place(save):
    bad = item(70, title="Out Of Range", ai_maturity=28)  # C1 FAIL despite the highest total
    published = save([bad] + [item(t) for t in (60, 50, 40, 30, 25, 21)])
    assert published == [60, 50, 40, 30, 25]


def test_rank_for_publish_counts_cuts():
    published, below, beyond = cs.rank_for_publish([item(t) for t in (60, 50, 40, 30, 25, 21, 19, 10)])
    assert [p["total_score"] for p in published] == [60, 50, 40, 30, 25] and below == 2 and beyond == 1


# ---------- full abstract: fetch -> score prompt -> summary prompt -> judge payload, capped at MAX_ABSTRACT_CHARS ----------

def arxiv_feed(*abstracts):
    today = datetime.now().strftime("%Y-%m-%d")
    entries = "".join(f"""<entry><id>http://arxiv.org/abs/2610.0000{i}v1</id><title>Agent paper {i}</title>
<summary>{a}</summary><published>{today}T00:00:00Z</published><author><name>A Author</name></author>
<link type="text/html" href="https://arxiv.org/abs/2610.0000{i}v1"/></entry>""" for i, a in enumerate(abstracts))
    return f'<feed xmlns="http://www.w3.org/2005/Atom">{entries}</feed>'


def test_full_abstract_reaches_score_summary_and_judge(save, monkeypatch):
    full = ("Agents in production need audit logs and rollback. " * 40)[:1900].strip()
    huge = "word " * 1200  # 6,000 chars: cut to the safety cap
    feed = arxiv_feed(full, huge)
    monkeypatch.setattr(cs.requests, "get", lambda *a, **k: SimpleNamespace(status_code=200, text=feed, close=lambda: None,
                                                                           raise_for_status=lambda: None))
    monkeypatch.setattr(cs.time, "sleep", lambda s: None)
    papers = cs.fetch_papers({"output_file": "case_studies/out.json"})["raw_papers"]
    assert len(full) == 1900 and papers[0]["abstract"] == full
    assert len(papers[1]["abstract"]) == cs.MAX_ABSTRACT_CHARS == 4000

    prompts = []
    def fake_groq(messages, **kw):
        prompts.append(messages[0]["content"])
        if "scoring AI research papers" in messages[0]["content"]:
            return json.dumps({"real_world_impact": 20, "ai_maturity": 15, "monetisation_potential": 10, "total_score": 45,
                               "score_reasoning": "r", "relevance_tag": "AI Agents", "low_signal": False})
        return json.dumps({"summary": "Agents need audit logs.", "it_breakdown": "Add audit logs.", "try_this": []})
    monkeypatch.setattr(cs, "groq_complete", fake_groq)
    scored = cs.score_papers({"raw_papers": papers[:1], "scored_papers": [], "output_file": "case_studies/out.json"})
    assert len(prompts) == 2 and all(full in p for p in prompts)  # score and summary prompts

    sent = []
    monkeypatch.setattr(judge, "judge_batch", lambda items: sent.extend(items) or
                        {it["id"]: {"verdict": "pass", "score": 1.0, "reason": "stub"} for it in items})
    cs.save_and_archive({**scored, "raw_papers": papers[:1]})
    assert [it["source"] for it in sent] == [full]  # all 1,900 chars reach the judge
