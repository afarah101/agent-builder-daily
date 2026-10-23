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
    monkeypatch.setattr(judge, "judge_item", lambda *a, **k: {"verdict": "pass", "score": 1.0, "reason": "stub"})

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
