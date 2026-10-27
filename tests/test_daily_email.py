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
    monkeypatch.delenv("TRY_THIS_ENABLED", raising=False)  # steps are off unless a test uses steps_on
    monkeypatch.setattr(de, "LAST_SENT", str(tmp_path / "last_sent.txt"))
    monkeypatch.setattr(de, "PREVIEW", str(tmp_path / "preview.html"))
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("network blocked")))


@pytest.fixture
def steps_on(monkeypatch):
    monkeypatch.setenv("TRY_THIS_ENABLED", "1")


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


def test_renders_all_sections_from_fixture(tmp_path, monkeypatch, steps_on):
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
    assert s["greeting"] == "Today: 1 agents story, 1 case study, 3 papers, 5 quick hits."
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


def test_teaser_uses_counts_not_titles(tmp_path):
    news = [{**FIXTURE_NEWS[0], "category": "Security"}] + FIXTURE_NEWS[1:]
    write_fixture(tmp_path, news=news)
    data = de.load_data()
    _, html_body, text_body, s = de.build_email(data, data["newest"] + timedelta(hours=1))
    assert s["greeting"] == "Today: 1 security story, 1 case study, 3 papers, 5 quick hits."
    titles = [n["title"] for n in data["news"]] + [c["title"] for c in data["papers"]]
    assert not any(t in s["greeting"] for t in titles) and "\n" not in s["greeting"]  # no title, one line


@pytest.mark.parametrize("top, case, radar, hits, expected", [
    ({"category": "Other"}, None, [{}], [{}], "Today: 1 top story, 1 paper, 1 quick hit."),
    ({"title": "x"}, {"title": "c"}, [], [], "Today: 1 top story, 1 case study."),
    ({}, None, [], [], "Here is today's Scout."),
])
def test_teaser_counts_skip_empty_parts(top, case, radar, hits, expected):
    assert de.teaser(top, case, radar, hits) == expected


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
def test_malformed_try_this_is_not_rendered(tmp_path, monkeypatch, steps, steps_on):
    days = cs_days(("2026-10-22", [{"title": "T", "total_score": 30, "try_this": steps}]))
    _, html_body, text_body, s = email_on(tmp_path, monkeypatch, days)
    assert s["case"]["title"] == "T" and "Try this today" not in html_body and "Try this today" not in text_body



@pytest.mark.parametrize("value", [None, "", "true", "yes", "0", "on"])
def test_switch_off_never_renders_seeded_steps(tmp_path, monkeypatch, value):
    if value is not None:
        monkeypatch.setenv("TRY_THIS_ENABLED", value)
    write_fixture(tmp_path)  # Paper 0, the case study of the day, carries three saved steps
    data = de.load_data()
    _, html_body, text_body, s = de.build_email(data, data["newest"] + timedelta(hours=1))
    assert s["case"]["title"] == "Paper 0" and "Case study of the day" in html_body and "CASE STUDY OF THE DAY" in text_body
    assert de.try_this(s["case"]) == []
    for body in (html_body, text_body):
        assert "Try this today" not in body and not any(step in body for step in STEPS)


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


def test_empty_try_this_shows_case_study_without_steps(tmp_path, monkeypatch, steps_on):
    days = cs_days(("2026-10-22", [{"title": "No action paper", "summary": "One. Two.", "total_score": 30, "try_this": []}]))
    _, html_body, text_body, s = email_on(tmp_path, monkeypatch, days)
    assert s["case"]["title"] == "No action paper" and "Case study of the day" in html_body
    assert "Try this today" not in html_body and "Try this today" not in text_body and "<ol" not in html_body


# ---------- escaping: every scraped or model-written string is HTML-escaped (each test fails if its _e() is removed) ----------

EVIL = '<script>alert(1)</script> & "q"'
EVIL_ESCAPED = "&lt;script&gt;alert(1)&lt;/script&gt; &amp; &quot;q&quot;"


def evil_email(tmp_path, monkeypatch, **case):
    now = datetime.fromisoformat("2026-10-22").replace(hour=11, tzinfo=LON)
    news = [{**FIXTURE_NEWS[0], "title": EVIL, "enterprise_use": f"S1: {EVIL} S2: b. S3: c."}] + FIXTURE_NEWS[1:]
    papers = [{**FIXTURE_PAPERS[1], "title": EVIL}] + FIXTURE_PAPERS[2:]
    day = {"title": "Case", "total_score": 30, "url": "https://arxiv.org/abs/c", **case}
    return de.build_email({**fresh_data(now), "news": news, "papers": papers, "cs_days": cs_days(("2026-10-22", [day]))}, now)[1]


def test_case_study_steps_are_escaped(tmp_path, monkeypatch, steps_on):
    body = evil_email(tmp_path, monkeypatch, try_this=[EVIL, "Step two.", "Step three."])
    steps = body.split("Try this today")[1].split("</ol>")[0]
    assert "<script>" not in steps and EVIL_ESCAPED in steps


def test_signal_points_are_escaped(tmp_path, monkeypatch):
    points = evil_email(tmp_path, monkeypatch).split("<b>Do this week:</b> ")[1].split("</li>")[0]
    assert points == EVIL_ESCAPED


def test_titles_are_escaped(tmp_path, monkeypatch):
    body = evil_email(tmp_path, monkeypatch, title=EVIL)
    assert "<script>" not in body
    assert body.count(f">{EVIL_ESCAPED}</a>") >= 3  # Signal, Case study of the day and Research Radar links


def test_href_is_escaped(tmp_path, monkeypatch):
    body = evil_email(tmp_path, monkeypatch, url='https://x.test/"><script>x</script>')
    assert 'href="https://x.test/&quot;&gt;&lt;script&gt;x&lt;/script&gt;"' in body and '"><script>' not in body


# ---------- N1e: the escapes and the guard the audit's surviving mutants showed were untested ----------

def test_signal_heading_is_escaped(tmp_path, monkeypatch):
    monkeypatch.setattr(de, "SIGNAL_HEADINGS", (EVIL, "Risk if ignored", "Tools affected"))
    body = evil_email(tmp_path, monkeypatch)
    assert f"<b>{EVIL_ESCAPED}:</b>" in body and "<b><script>" not in body


def test_case_study_summary_is_escaped(tmp_path, monkeypatch):
    block = evil_email(tmp_path, monkeypatch, summary=EVIL).split("Case study of the day</div>")[1].split("</td></tr>")[0]
    assert EVIL_ESCAPED in block and "<script>" not in block


def test_greeting_is_escaped(tmp_path, monkeypatch):
    now = datetime.fromisoformat("2026-10-22").replace(hour=11, tzinfo=LON)
    news = [{**FIXTURE_NEWS[0], "category": EVIL}] + FIXTURE_NEWS[1:]
    _, body, _, s = de.build_email({**fresh_data(now), "news": news, "cs_days": []}, now)
    greeting = body.split("Good morning</div>")[1].split("</td></tr>")[0]
    assert EVIL.lower() in s["greeting"] and "&lt;script&gt;" in greeting and "<script>" not in greeting


def test_future_dated_case_study_is_never_shown(tmp_path, monkeypatch):
    days = cs_days(("2026-10-23", [{"title": "Tomorrow", "total_score": 70}]), ("2026-10-20", [{"title": "Two days ago", "total_score": 10}]))
    assert email_on(tmp_path, monkeypatch, days)[3]["case"]["title"] == "Two days ago"
    assert email_on(tmp_path, monkeypatch, days[:1])[3]["case"] is None


def creator(i, age_days, **kw):
    published = (datetime(2026, 10, 22, 11, tzinfo=timezone.utc) - timedelta(days=age_days)).isoformat()
    return {"title": f"Video {i}", "url": f"https://www.youtube.com/watch?v=vid{i:08d}", "creator": "DeepLearning.AI",
            "published": published, "summary": f"Summary {i}.", "key_idea": f"Idea {i}.", **kw}


def creator_email(creators):
    now = datetime.fromisoformat("2026-10-22").replace(hour=11, tzinfo=LON)
    days = cs_days(("2026-10-22", [{"title": "Case pick", "total_score": 70}]))
    return de.build_email({**fresh_data(now), "cs_days": days, "creators": creators}, now)


@pytest.mark.parametrize("creators", [None, [], [creator(1, 7.1)], [{"title": "No date", "url": "https://x.example/"}]])
def test_creator_watch_omitted_without_placeholder(creators):
    _, html_body, text_body, s = creator_email(creators)
    assert s["creators"] == []
    assert "Creator Watch" not in html_body and "CREATOR WATCH" not in text_body and "AI summary" not in html_body
    assert creator_email(None)[1] == html_body  # nothing else on the page moves


def test_creator_watch_after_case_study_max_3_newest_first():
    rows = [creator(1, 3), creator(2, 0.5), creator(3, 8), creator(4, 1), creator(5, 2), creator(2, 0.5, title="Dup")]
    subject, html_body, text_body, s = creator_email(rows)
    assert [c["title"] for c in s["creators"]] == ["Video 2", "Video 4", "Video 5"]
    heading = lambda name: html_body.index(f">{name}</div>")
    assert heading("Case study of the day") < heading("Creator Watch") < heading("Research Radar")
    assert text_body.index("CASE STUDY OF THE DAY") < text_body.index("CREATOR WATCH") < text_body.index("RESEARCH RADAR")
    assert html_body.count("<b>AI summary:</b>") == 3 and "AI summary: Summary 2." in text_body
    assert 'href="https://www.youtube.com/watch?v=vid00000002"' in html_body and "Video 1" not in html_body
    # every other section is byte-identical to the page without Creator Watch
    base_subject, base_html, _, base_s = creator_email([])
    at = heading("Creator Watch")
    start, end = html_body.rindex("<tr><td", 0, at), html_body.index("</td></tr>", at) + len("</td></tr>")
    assert subject == base_subject and html_body[:start] + html_body[end:] == base_html
    assert {k: v for k, v in s.items() if k != "creators"} == {k: v for k, v in base_s.items() if k != "creators"}


def test_creator_watch_fields_are_escaped():
    _, html_body, _, _ = creator_email([creator(1, 1, title=EVIL, creator=EVIL, summary=EVIL, key_idea=EVIL)])
    block = html_body.split("Creator Watch</div>")[1].split("</td></tr>")[0]
    assert block.count(EVIL_ESCAPED) == 4 and "<script>" not in block


def test_creator_watch_rejects_non_http_url():
    assert creator_email([creator(1, 1, url="javascript:alert(1)")])[3]["creators"] == []


def test_load_creators_skips_unreadable_files(tmp_path):
    (tmp_path / "data" / "creators").mkdir(parents=True)
    (tmp_path / "data" / "creators" / "2026-10-21.json").write_text("{not json")
    (tmp_path / "data" / "creators" / "2026-10-22.json").write_text(json.dumps({"items": [creator(1, 1), "junk"]}))
    assert de.load_creators() == [creator(1, 1)]
