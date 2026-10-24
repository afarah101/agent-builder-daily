"""News history lives in tracked data/: the scout's dedup loads the rebuilt archive, and generate_html.py publishes
the full archive to site/. Sockets blocked and Groq mocked, so no tokens can be spent."""
import json
import os
import shutil
import socket
import subprocess
import sys
from email.utils import format_datetime
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

for k, v in {"GROQ_API_KEY": "test-key-1", "GROQ_API_KEY_2": "", "LANGFUSE_PUBLIC_KEY": "pk-test",
             "LANGFUSE_SECRET_KEY": "sk-test", "LANGFUSE_HOST": "http://127.0.0.1:9", "LANGFUSE_TRACING_ENABLED": "false",
             "LANGCHAIN_TRACING_V2": "false", "LANGSMITH_TRACING": "false", "LANGCHAIN_API_KEY": ""}.items():
    os.environ.setdefault(k, v)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import industry_news_scout_langgraph as news

ARCHIVE = ROOT / "data" / "news_archive.json"


def _blocked(*args, **kwargs):
    raise RuntimeError("network blocked in tests")


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    (tmp_path / "data" / "news").mkdir(parents=True)
    shutil.copy(ARCHIVE, tmp_path / "data" / "news_archive.json")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_paths_point_at_tracked_data():
    assert news.OUTPUT_DIR == "data/news" and news.ARCHIVE_FILE == "data/news_archive.json"


def test_fetch_news_skips_item_already_in_archive(workdir, monkeypatch, capsys):
    archived = json.loads(ARCHIVE.read_text())[0]["news"][0]
    now = format_datetime(datetime.now().astimezone())
    feed = SimpleNamespace(entries=[
        {"title": archived["title"], "link": archived["url"], "published": now, "summary": "old"},
        {"title": "Brand new story", "link": "https://example.test/new", "published": now, "summary": "new"},
    ])
    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(news, "groq_complete", _blocked)
    monkeypatch.setattr(news, "RSS_SOURCES", news.RSS_SOURCES[:1])
    monkeypatch.setattr(news, "SCRAPE_SOURCES", [])
    monkeypatch.setattr(news.feedparser, "parse", lambda url: feed)
    monkeypatch.setattr(news, "fetch_source_text", lambda a: a["summary_raw"])

    out = news.fetch_news({})

    loaded = int(capsys.readouterr().out.split("Cross-run dedup: ")[1].split()[0])
    assert loaded > 0
    assert [a["url"] for a in out["raw_articles"]] == ["https://example.test/new"]
    assert out["output_file"].startswith("data/news/news_")


def test_fetch_news_dedups_against_active_daily_files(workdir, monkeypatch):
    (workdir / "data" / "news_archive.json").write_text("[]")
    (workdir / "data" / "news" / "news_2026-10-24.json").write_text(json.dumps({"news": [{"id": "https://example.test/today"}]}))
    now = format_datetime(datetime.now().astimezone())
    feed = SimpleNamespace(entries=[{"title": "Today", "link": "https://example.test/today", "published": now}])
    monkeypatch.setattr(news, "RSS_SOURCES", news.RSS_SOURCES[:1])
    monkeypatch.setattr(news, "SCRAPE_SOURCES", [])
    monkeypatch.setattr(news.feedparser, "parse", lambda url: feed)
    assert news.fetch_news({})["raw_articles"] == []


def test_generate_html_publishes_full_archive(workdir):
    (workdir / "case_studies").mkdir()
    subprocess.run([sys.executable, str(ROOT / "generate_html.py")], cwd=workdir, check=True, capture_output=True)
    site = json.loads((workdir / "site" / "news_archive.json").read_text())
    assert len(site) == len(json.loads(ARCHIVE.read_text())) > 1
    assert site == json.loads((workdir / "data" / "news_archive.json").read_text())
