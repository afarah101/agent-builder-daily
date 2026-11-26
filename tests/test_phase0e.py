"""Phase 0e: empty or malformed Scout data sends the stale alert; score_ring is out of 75; no name in the news prompt."""
import json
import socket
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import send_daily_email as de
import generate_html
from test_daily_email import write_fixture, FIXTURE_DATE

NOW = datetime(2026, 10, 22, 11, 30, tzinfo=de.LONDON)


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(de, "LAST_SENT", str(tmp_path / "last_sent.txt"))
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("network blocked")))
    sent = []

    def _run():
        result = de.main([], now=NOW, sleep=lambda s: None, send=lambda s, h, t: sent.append((s, h, t)) or "250 OK (mock)")
        return result, sent
    return _run


def test_today_with_zero_news_sends_alert_not_digest(tmp_path, run):
    write_fixture(tmp_path, news=[], papers=[])
    result, sent = run()
    assert result == "stale_sent" and len(sent) == 1
    subject, html_body, text_body = sent[0]
    assert "no news items today" in subject and "The Signal" not in html_body and "Research Radar" not in html_body


def test_zero_news_but_papers_still_alerts(tmp_path, run):
    write_fixture(tmp_path, news=[])
    result, sent = run()
    assert result == "stale_sent" and "no news items today" in sent[0][0]


def _news(root):
    return root / "site" / "news_archive.json"


@pytest.mark.parametrize("case", ["missing_date_key", "bad_date_format", "corrupt_day_file", "non_object_entry"])
def test_malformed_data_sends_alert_without_crashing(tmp_path, run, case):
    write_fixture(tmp_path)
    news_day = {"date": FIXTURE_DATE, "news": [{"title": "T", "summary": "S.", "url": "https://x/1", "relevance_score": 9}]}
    if case == "missing_date_key":
        _news(tmp_path).write_text(json.dumps([{"news": news_day["news"]}]))
    elif case == "bad_date_format":
        _news(tmp_path).write_text(json.dumps([{**news_day, "date": "22 Oct 2026"}]))
    elif case == "corrupt_day_file":
        (tmp_path / "case_studies" / f"{FIXTURE_DATE}.json").write_text("{not json")
    else:
        _news(tmp_path).write_text(json.dumps(["not an object"]))
    result, sent = run()
    assert result == "stale_sent" and len(sent) == 1
    assert sent[0][0].startswith("Scout data is stale") and "malformed" in sent[0][2]


def test_well_formed_fixture_still_sends_digest(tmp_path, run):
    write_fixture(tmp_path)
    result, sent = run()
    assert result == "sent" and sent[0][0] == "Daily Edge AI — Thu 22 Oct 2026 — News 0"


def test_score_ring_is_out_of_75():
    assert "--pct:100;" in generate_html.score_ring(75)
    assert "--pct:40;" in generate_html.score_ring(30)
    assert "0-75 metric" in generate_html.score_ring(30) and "0-100" not in generate_html.score_ring(30)


def test_no_personal_name_in_news_prompt():
    assert "Ahmed" not in (ROOT / "industry_news_scout_langgraph.py").read_text()
    assert "How the reader specifically can use or act on this." in (ROOT / "industry_news_scout_langgraph.py").read_text()
