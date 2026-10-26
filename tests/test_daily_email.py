"""send_daily_email.py: rendering from a fixed fixture, London send rules, once per day, stale alert. SMTP mocked."""
import json
import os
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
                 "enterprise_use": f"S1: Do thing {i}. S2: Risk {i}. S3: Tool {i}.", "url": f"https://news.example/{i}",
                 "relevance_score": 10 - i, "source": "TechCrunch" if i % 2 else "OpenAI",
                 "category": "Agents" if i < 4 else "Security"} for i in range(8)]
STEPS = ["Open the Intune compliance policy and list devices missing updates.",
         "Enable Okta ThreatInsight in report-only mode.", "Add an Azure Monitor alert rule for failed sign-ins."]
FIXTURE_PAPERS = [{"title": f"Paper {i}", "summary": f"Paper summary {i}. More detail. Even more.", "url": f"https://arxiv.org/abs/{i}",
                   "total_score": 60 - 10 * i, **({"try_this": STEPS} if i == 0 else {})} for i in range(4)]


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
    for heading in ("Scout Daily", "Good morning", "In today&#x27;s Scout", "The Signal", "Case study of the day",
                    "Try this today", "Research Radar", "Quick Hits", de.SITE_URL, "Data as of"):
        assert heading in html_body, heading
    for heading in ("Good morning", "IN TODAY'S SCOUT", "THE SIGNAL", "CASE STUDY OF THE DAY", "RESEARCH RADAR", "QUICK HITS"):
        assert heading in text_body
    assert html_body.index("The Signal") < html_body.index("Case study of the day") < html_body.index("Research Radar")
    assert s["bullets"] == ["The Signal: News 0", "Case study of the day: Paper 0",
                            "Research Radar: 3 paper(s), led by Paper 1 (50/75)", "Quick Hits: 5 more stories from OpenAI, TechCrunch"]
    assert s["signal"] == data["news"][0]
    assert s["hits"] == data["news"][1:6] and [c["title"] for c in s["radar"]] == ["Paper 1", "Paper 2", "Paper 3"]
    assert s["greeting"] == "Today: News 0, plus Paper 0."
    case_text = text_body.split("CASE STUDY OF THE DAY")[1].split("RESEARCH RADAR")[0]
    assert "Paper summary 0. More detail." in case_text and "Even more." not in case_text  # 2-sentence case summary
    assert "https://arxiv.org/abs/0" in case_text and 'href="https://arxiv.org/abs/0"' in html_body
    assert all(step in text_body for step in STEPS) and "1. Open the Intune" in text_body
    assert "/75" in html_body and 'max-width:600px' in html_body
    assert "Ahmed" not in html_body and "Ahmed" not in text_body and "Ahmed" not in subject


def test_filler_bullets_removed(tmp_path):
    write_fixture(tmp_path)
    data = de.load_data()
    _, html_body, text_body, _ = de.build_email(data, data["newest"] + timedelta(hours=1))
    for body in (html_body, text_body):
        assert "Most common category" not in body and "average relevance" not in body and "news items scored today" not in body


def test_teaser_never_repeats_the_signal_summary(tmp_path):
    # The Signal's title is also its summary's first sentence, so a title-based teaser would repeat the summary.
    news = [{**FIXTURE_NEWS[0], "title": "Ads delivered zero-click exploits",
             "summary": "Ads delivered zero-click exploits. Forty-five devices were infected."}] + FIXTURE_NEWS[1:]
    write_fixture(tmp_path, news=news)
    data = de.load_data()
    _, html_body, text_body, s = de.build_email(data, data["newest"] + timedelta(hours=1))
    summary = data["news"][0]["summary"]
    assert s["greeting"] == "Today: Paper 0, plus News 1."
    assert s["greeting"] != de.two_sentences(summary)
    for sentence in de.re.split(r"(?<=[.!?])\s+", summary):
        assert sentence.rstrip(".").lower() not in s["greeting"].lower()
    assert "\n" not in s["greeting"]  # one line


def test_signal_has_three_headed_points_and_no_s_labels(tmp_path):
    write_fixture(tmp_path)
    data = de.load_data()
    _, html_body, text_body, s = de.build_email(data, data["newest"] + timedelta(hours=1))
    assert s["signal_points"] == [("Do this week", "Do thing 0."), ("Risk if ignored", "Risk 0."), ("Tools affected", "Tool 0.")]
    assert "<b>Do this week:</b> Do thing 0." in html_body and "- Tools affected: Tool 0." in text_body
    assert "Enterprise use" not in html_body
    for body in (html_body, text_body):
        assert not de.re.search(r"\bS[1-3]\s*:", body)


@pytest.mark.parametrize("text, expected", [
    ("Patch the VPN this week.", [(None, "Patch the VPN this week.")]),             # no labels: one plain point
    ("Intro. S1: a. S2: b. S3: c.", [(None, "Intro. a. b. c.")]),                    # labels not leading: stripped
    ("S1: a. S3: c.", [(None, "a. c.")]),                                            # one label missing
    ("", []),
])
def test_signal_points_never_leak_labels(text, expected):
    assert de.signal_points(text) == expected


def cs_days(*days):
    return [{"date": d, "case_studies": items} for d, items in days]


def email_on(tmp_path, monkeypatch, days, today="2026-10-22"):
    now = datetime.fromisoformat(today).replace(hour=11, tzinfo=LON)
    data = {**fresh_data(now), "cs_days": days}
    return de.build_email(data, now)


def test_case_study_falls_back_to_newest_day_in_last_7_days_with_date(tmp_path, monkeypatch):
    days = cs_days(("2026-10-22", []), ("2026-10-19", [{"title": "Old A", "total_score": 20}, {"title": "Old B", "total_score": 40,
                   "summary": "B one. B two. B three.", "url": "https://arxiv.org/abs/b"}]), ("2026-10-18", [{"title": "Older", "total_score": 70}]))
    _, html_body, text_body, s = email_on(tmp_path, monkeypatch, days)
    assert s["case"]["title"] == "Old B" and str(s["case"]["shown_date"]) == "2026-10-19"
    assert "Case study of the day: Old B (from Mon 19 Oct)" in s["bullets"]
    assert "From Mon 19 Oct 2026" in html_body and "From Mon 19 Oct 2026" in text_body
    assert "Try this today" not in html_body  # no try_this on this item: steps block omitted, no placeholder


def test_case_study_from_today_shows_no_date(tmp_path, monkeypatch):
    days = cs_days(("2026-10-22", [{"title": "Fresh", "total_score": 30}]), ("2026-10-21", [{"title": "Yesterday", "total_score": 70}]))
    _, html_body, _, s = email_on(tmp_path, monkeypatch, days)
    assert s["case"]["title"] == "Fresh" and s["case"]["shown_date"] is None and "From " not in html_body


@pytest.mark.parametrize("days", [
    [],                                                                              # empty archive
    cs_days(("2026-10-22", []), ("2026-10-21", [])),                                 # recent days, no items
    cs_days(("2026-10-15", [{"title": "Week old", "total_score": 70}])),             # 7 days old: outside the window
])
def test_case_study_section_omitted_without_placeholder(tmp_path, monkeypatch, days):
    _, html_body, text_body, s = email_on(tmp_path, monkeypatch, days)
    assert s["case"] is None and not any(b.startswith("Case study") for b in s["bullets"])
    for body in (html_body, text_body):
        assert "Case study of the day" not in body and "CASE STUDY" not in body and "Try this today" not in body
    assert "No case study" not in html_body


def test_research_radar_skips_the_case_study_of_the_day(tmp_path):
    write_fixture(tmp_path)
    data = de.load_data()
    _, html_body, text_body, s = de.build_email(data, data["newest"] + timedelta(hours=1))
    assert s["case"]["title"] == "Paper 0" and "Paper 0" not in [c["title"] for c in s["radar"]]
    radar_text = text_body.split("RESEARCH RADAR")[1].split("QUICK HITS")[0]
    assert "Paper 0" not in radar_text and "Paper 1" in radar_text


def test_research_radar_keeps_all_papers_when_case_is_from_another_day(tmp_path, monkeypatch):
    days = cs_days(("2026-10-22", []), ("2026-10-20", [{"title": "Older pick", "url": "https://arxiv.org/abs/old", "total_score": 40}]))
    _, _, _, s = email_on(tmp_path, monkeypatch, days)
    assert s["case"]["title"] == "Older pick" and [c["title"] for c in s["radar"]] == ["Paper 0", "Paper 1", "Paper 2"]


def test_case_study_6_days_old_is_still_used(tmp_path, monkeypatch):
    _, _, _, s = email_on(tmp_path, monkeypatch, cs_days(("2026-10-16", [{"title": "Six days", "total_score": 5}])))
    assert s["case"]["title"] == "Six days"


@pytest.mark.parametrize("steps", [STEPS[:2], STEPS + ["Fourth."], "one string", [1, 2, 3], ["a", "", "c"]])
def test_malformed_try_this_is_not_rendered(tmp_path, monkeypatch, steps):
    days = cs_days(("2026-10-22", [{"title": "T", "total_score": 30, "try_this": steps}]))
    _, html_body, text_body, s = email_on(tmp_path, monkeypatch, days)
    assert s["case"]["title"] == "T" and "Try this today" not in html_body and "Try this today" not in text_body


def test_scout_try_this_parser_keeps_exactly_three_short_steps():
    for k, v in {"GROQ_API_KEY": "test-key-1", "LANGFUSE_PUBLIC_KEY": "pk-test", "LANGFUSE_SECRET_KEY": "sk-test",
                 "LANGFUSE_HOST": "http://127.0.0.1:9", "LANGFUSE_TRACING_ENABLED": "false", "LANGCHAIN_TRACING_V2": "false",
                 "LANGSMITH_TRACING": "false"}.items():
        os.environ.setdefault(k, v)
    import case_study_scout_langgraph as cs
    assert cs._try_this_steps([" Open  Intune. ", "Enable Okta MFA;", "Check GitHub Actions logs."]) == \
        ["Open Intune.", "Enable Okta MFA", "Check GitHub Actions logs."]  # live output ended steps with ';'
    for bad in (None, "text", STEPS[:2], STEPS + ["x"], ["a", "", "c"], STEPS[:2] + [" ".join(["word"] * 26)]):
        assert cs._try_this_steps(bad) == []


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
