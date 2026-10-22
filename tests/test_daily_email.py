"""send_daily_email.py: rendering from a fixed fixture, London send rules, once per day, stale alert. SMTP mocked."""
import json
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import send_daily_email as de

LON = de.LONDON


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # never the repo: tests use fixtures, not live data
    monkeypatch.setattr(de, "LAST_SENT", str(tmp_path / "last_sent.txt"))
    monkeypatch.setattr(de, "PREVIEW", str(tmp_path / "preview.html"))
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("network blocked")))


class Outbox:
    def __init__(self):
        self.sent = []

    def __call__(self, subject, html_body, text_body):
        self.sent.append((subject, html_body, text_body))
        return "250 OK (mock)"


FIXTURE_DATE = "2026-10-22"
FIXTURE_NEWS = [{"title": f"News {i}", "summary": f"First sentence {i}. Second sentence. Third sentence.",
                 "enterprise_use": f"Do thing {i}.", "url": f"https://news.example/{i}", "relevance_score": 10 - i,
                 "source": "TechCrunch" if i % 2 else "OpenAI", "category": "Agents" if i < 4 else "Security"} for i in range(8)]
FIXTURE_PAPERS = [{"title": f"Paper {i}", "summary": f"Paper summary {i}.", "url": f"https://arxiv.org/abs/{i}",
                   "total_score": 60 - 10 * i} for i in range(4)]


def write_fixture(root, news=FIXTURE_NEWS, papers=FIXTURE_PAPERS, date=FIXTURE_DATE):
    """Fixed Scout data on disk in the same layout as the repo, for tests that go through load_data()."""
    (root / "site").mkdir(exist_ok=True); (root / "case_studies").mkdir(exist_ok=True)
    (root / "site" / "news_archive.json").write_text(json.dumps([{"date": date, "news": news}]))
    (root / "case_studies" / "archive.json").write_text(json.dumps([{"date": date, "case_studies": papers}]))
    (root / "case_studies" / f"{date}.json").write_text(json.dumps({"timestamp": f"{date}T06:10:00"}))


def fresh_data(now):
    return {"news": FIXTURE_NEWS, "papers": FIXTURE_PAPERS, "newest": now.astimezone(timezone.utc) - timedelta(hours=5), "reason": None}


@pytest.fixture
def run(monkeypatch):
    def _run(now, argv=(), data_age=timedelta(hours=5)):
        monkeypatch.setattr(de, "load_data", lambda: {**fresh_data(now), "newest": now.astimezone(timezone.utc) - data_age})
        out, slept = Outbox(), []
        result = de.main(list(argv), now=now, sleep=slept.append, send=out)
        return result, out.sent, slept
    return _run


def test_renders_all_sections_from_fixture(tmp_path, monkeypatch):
    write_fixture(tmp_path)
    monkeypatch.chdir(tmp_path)
    data = de.load_data()
    assert [n["title"] for n in data["news"]] == [f"News {i}" for i in range(8)] and len(data["papers"]) == 4
    subject, html_body, text_body, s = de.build_email(data, data["newest"] + timedelta(hours=1))
    assert subject == "Scout Daily — Thu 22 Oct 2026 — News 0"
    for heading in ("Scout Daily", "Good morning", "In today&#x27;s Scout", "The Signal", "Research Radar", "Quick Hits",
                    de.SITE_URL, "Data as of"):
        assert heading in html_body, heading
    for heading in ("Good morning", "IN TODAY'S SCOUT", "THE SIGNAL", "RESEARCH RADAR", "QUICK HITS"):
        assert heading in text_body
    assert len(s["bullets"]) == 5 and s["signal"] == data["news"][0]
    assert s["hits"] == data["news"][1:6] and [c["title"] for c in s["radar"]] == ["Paper 0", "Paper 1", "Paper 2"]
    assert s["greeting"] == "First sentence 0. Second sentence."
    assert len(de.re.findall(r"(?<=[.!?])\s+", s["greeting"])) <= 1  # at most 2 sentences
    assert "/75" in html_body and 'max-width:600px' in html_body
    assert "Ahmed" not in html_body and "Ahmed" not in text_body and "Ahmed" not in subject


def test_saturday_does_not_send(run):
    result, sent, _ = run(datetime(2026, 10, 24, 11, 30, tzinfo=LON))
    assert result == "weekend" and sent == []


def test_10_55_sleeps_until_11_then_sends(run):
    result, sent, slept = run(datetime(2026, 10, 22, 10, 55, tzinfo=LON))
    assert slept == [300] and result == "sent" and len(sent) == 1


def test_11_30_sends_now(run):
    result, sent, slept = run(datetime(2026, 10, 22, 11, 30, tzinfo=LON))
    assert slept == [] and result == "sent" and len(sent) == 1
    assert Path(de.LAST_SENT).read_text().strip() == "2026-10-22"


def test_already_sent_today_is_skipped(run):
    Path(de.LAST_SENT).write_text("2026-10-22\n")
    result, sent, _ = run(datetime(2026, 10, 22, 11, 30, tzinfo=LON))
    assert result == "already_sent" and sent == []


def test_second_run_same_day_is_skipped(run):
    now = datetime(2026, 10, 22, 11, 30, tzinfo=LON)
    assert run(now)[0] == "sent"
    result, sent, _ = run(now + timedelta(hours=1))
    assert result == "already_sent" and sent == []


def test_data_31h_old_sends_stale_alert(run):
    result, sent, _ = run(datetime(2026, 10, 22, 11, 30, tzinfo=LON), data_age=timedelta(hours=31))
    assert result == "stale_sent" and len(sent) == 1
    subject, html_body, text_body = sent[0]
    assert subject.startswith("Scout data is stale") and "Research Radar" not in html_body and "31 hours old" in text_body


def test_failed_send_does_not_mark_sent(monkeypatch):
    now = datetime(2026, 10, 22, 11, 30, tzinfo=LON)
    monkeypatch.setattr(de, "load_data", lambda: fresh_data(now))
    def boom(*a):
        raise OSError("smtp down")
    with pytest.raises(OSError):
        de.main([], now=now, sleep=lambda s: None, send=boom)
    assert not Path(de.LAST_SENT).exists()


def test_force_ignores_weekend_and_dry_run_sends_nothing(run):
    sat = datetime(2026, 10, 24, 9, 0, tzinfo=LON)
    assert run(sat, ["--force"])[0] == "sent"
    result, sent, _ = run(sat, ["--dry-run"])
    assert result == "preview" and sent == [] and "The Signal" in Path(de.PREVIEW).read_text()
