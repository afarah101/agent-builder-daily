"""Same-day scout reruns add to today's news, never replace it. Runs the real CI order (scout save, generate_breakdowns,
generate_html) on a fixed clock with Groq and the judge mocked and sockets blocked, so no tokens can be spent."""
import json
import os
import socket
import sys
from datetime import datetime
from email.utils import format_datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

for k, v in {"GROQ_API_KEY": "test-key-1", "GROQ_API_KEY_2": "", "LANGFUSE_PUBLIC_KEY": "pk-test",
             "LANGFUSE_SECRET_KEY": "sk-test", "LANGFUSE_HOST": "http://127.0.0.1:9", "LANGFUSE_TRACING_ENABLED": "false",
             "LANGCHAIN_TRACING_V2": "false", "LANGSMITH_TRACING": "false", "LANGCHAIN_API_KEY": ""}.items():
    os.environ.setdefault(k, v)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import generate_breakdowns as gb
import generate_html as gh
import industry_news_scout_langgraph as news
import send_daily_email

DAY1, DAY2 = datetime(2026, 11, 2, 9, 0), datetime(2026, 11, 3, 9, 0)


def _blocked(*args, **kwargs):
    raise RuntimeError("network blocked in tests")


class Clock(datetime):
    at = DAY1

    @classmethod
    def now(cls, tz=None):
        return cls.at if tz is None else cls.at.astimezone(tz)

    @classmethod
    def utcnow(cls):
        return cls.at


def _fake_groq(messages, **kw):
    if kw.get("call_site") == "news.daily_hook":
        return "Hook of the day."
    return json.dumps({"summary": "S.", "enterprise_use": "E.", "relevance_score": 7, "relevance_signal": "high",
                       "category": "Agents"})


@pytest.fixture
def ci(tmp_path, monkeypatch):
    (tmp_path / "data" / "news").mkdir(parents=True)
    (tmp_path / "data" / "news_archive.json").write_text("[]")
    (tmp_path / "case_studies").mkdir()
    (tmp_path / "case_studies" / "archive.json").write_text(json.dumps([{"date": "2026-11-02", "case_studies": []}]))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    for mod in (news, gb, gh):
        monkeypatch.setattr(mod, "datetime", Clock)
    monkeypatch.setattr(news, "groq_complete", _fake_groq)
    monkeypatch.setattr(gb, "call_groq", lambda prompt, retries=3: None)
    monkeypatch.setattr(news.quality_checks, "gate", lambda items, **k: (items, [], []))
    monkeypatch.setattr(news.judge, "gate", lambda items, *a: (items, [], {"total": len(items)}))
    monkeypatch.setattr(news.llm_cache, "save", lambda *a, **k: 0)
    monkeypatch.setattr(news, "fetch_source_text", lambda a: a["summary_raw"])
    monkeypatch.setattr(news, "RSS_SOURCES", news.RSS_SOURCES[:1])
    monkeypatch.setattr(news, "SCRAPE_SOURCES", [])
    monkeypatch.setattr(news, "MAX_PER_SOURCE", 10)

    def run(day, ids):
        Clock.at = day
        published = format_datetime(day.astimezone())
        feed = SimpleNamespace(entries=[{"title": f"Story {i}", "link": f"https://example.test/{i}", "published": published,
                                         "summary": f"About {i}."} for i in ids])
        monkeypatch.setattr(news.feedparser, "parse", lambda url: feed)
        today = day.strftime("%Y-%m-%d")
        news.build_graph().invoke({"raw_articles": [], "classified_news": [], "output_file": f"{news.OUTPUT_DIR}/news_{today}.json"},
                                  config={"configurable": {"thread_id": "news-" + today}})
        gb.main()
        gh.generate_html()

    return run


def _ids(entry):
    return sorted(item["url"].rsplit("/", 1)[1] for item in entry["news"])


def _day(path, date):
    return next(e for e in json.loads(Path(path).read_text()) if e["date"] == date)


def test_same_day_runs_add_and_empty_run_changes_nothing(ci):
    ci(DAY1, ["a1", "a2", "a3", "a4"])                         # Run A
    ci(DAY1, ["a1", "a2", "b1", "b2"])                         # Run B
    day1 = _day("data/news_archive.json", "2026-11-02")
    assert _ids(day1) == ["a1", "a2", "a3", "a4", "b1", "b2"] and day1["total"] == 6
    assert len(json.loads(Path("data/news/news_2026-11-02.json").read_text())["news"]) == 6

    ci(DAY2, ["a3", "c1", "c2"])                               # Run C
    assert _ids(_day("data/news_archive.json", "2026-11-03")) == ["c1", "c2"]  # a3 not republished
    before = Path("data/news/news_2026-11-03.json").read_bytes()

    ci(DAY2, [])                                               # Run D: nothing new
    assert Path("data/news/news_2026-11-03.json").read_bytes() == before
    for path in ("data/news_archive.json", "site/news_archive.json"):
        assert _ids(_day(path, "2026-11-03")) == ["c1", "c2"]
        assert _ids(_day(path, "2026-11-02")) == ["a1", "a2", "a3", "a4", "b1", "b2"]
    assert Path("data/daily_hook.txt").read_text() == "Hook of the day."

    now = datetime(2026, 11, 3, 12, 0, tzinfo=send_daily_email.LONDON)
    assert send_daily_email.main(["--dry-run"], now=now) == "preview"  # a digest, not the stale alert


def test_save_dedupes_by_id_then_url_and_keeps_existing(ci, monkeypatch):
    Clock.at = DAY1
    monkeypatch.setattr(news, "generate_daily_hook", lambda items: None)
    out = "data/news/news_2026-11-02.json"
    save = lambda items: news.save_and_archive({"raw_articles": items, "classified_news": items, "output_file": out})
    save([{"id": "x1", "url": "https://u/1", "title": "One"}, {"url": "https://u/2", "title": "Two"}])
    save([{"id": "x1", "url": "https://u/other", "title": "One again"}, {"url": "https://u/2", "title": "Two again"},
          {"id": "x3", "title": "Three"}])
    day = _day("data/news_archive.json", "2026-11-02")
    assert [x["title"] for x in day["news"]] == ["One", "Two", "Three"] and day["total"] == 3
    assert json.loads(Path(out).read_text())["news"] == day["news"]
