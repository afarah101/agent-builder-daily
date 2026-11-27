"""Grounded content: generated text states only what its source states. The checks are in quality_checks.py (C7-C10);
the scouts regenerate once and then hold a story back; generate_breakdowns.py publishes an AI page only when it passes.
No network: Groq and the URL check are stubbed."""
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

for k, v in {"GROQ_API_KEY": "test-key-1", "GROQ_API_KEY_2": "", "LANGFUSE_PUBLIC_KEY": "pk-test", "LANGFUSE_SECRET_KEY": "sk-test",
             "LANGFUSE_HOST": "http://127.0.0.1:9", "LANGFUSE_TRACING_ENABLED": "false", "LANGCHAIN_TRACING_V2": "false",
             "LANGSMITH_TRACING": "false", "LANGCHAIN_API_KEY": ""}.items():
    os.environ.setdefault(k, v)
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import generate_breakdowns as gb  # noqa: E402
import judge  # noqa: E402
import quality_checks as qc  # noqa: E402

TODAY = datetime.now().strftime("%d %b %Y")

# The audited stories, as their sources read.
GTA_SOURCE = ("GTA VI launched as the highest-rated game of all time on Metacritic and made $3.79 billion on release day. "
              "Fans waited 13 years. GTA VI was created entirely without generative AI. Anthropic became the first major "
              "AI lab to go public with an $81.3 billion IPO. About 5,000 people worked on the game.")
GTA_INVENTED = ("Benchmark against industry averages for AI-aaS spend (e.g., 0.02 £/token for Anthropic vs. "
                "0.015 £/token for OpenAI).")
GTA_GROUNDED = ("GTA VI made $3.79 billion on release day after a 13-year wait, and it was created entirely without "
                "generative AI. Anthropic went public with an $81.3 billion IPO. "
                "Our take: this does not change the tools of an IT department.")
CVP_SOURCE = ("We're launching a new, expanded version of our Cyber Verification Program (CVP). The program now consists of "
              "three access tiers. Each tier includes access to Claude Opus 5.5 and Claude Mythos 5.1.")
CVE_STEP = "Use Claude to generate hypothesis scripts for a known vulnerability (e.g., CVE‑2024‑xxxx) within 30 days."
GPU_SOURCE = "Matrix-heavy work speeds up: LLM prefill (+14–36% in #9, #15, #25 and #33), diffusion and FlashAttention."


def news(**kw):
    return {"title": "Grand Theft Auto VI proves why efficiency isn't a virtue", "source": "TechCrunch", "pub_date": TODAY,
            "url": "https://techcrunch.com/2026/11/26/grand-theft-auto-vi/", "source_text": GTA_SOURCE, "summary": GTA_GROUNDED,
            "enterprise_use": "Watch the Anthropic API pricing pages over the next 30 days.", "relevance_score": 4, **kw}


def failed(item):
    return {name: reason for name, result, reason in qc.run_checks(item, network=False) if result == qc.FAIL}


# ---------- the four cases the audit asked for ----------

def test_gta_token_price_text_fails():
    assert qc.ungrounded(GTA_INVENTED, GTA_SOURCE, action=True) == ["0.02 £", "0.015 £"]
    assert "0.02 £" in failed(news(enterprise_use=GTA_INVENTED))["C7"]
    assert "£0.02" in qc.ungrounded("Anthropic charges £0.02/token.", GTA_SOURCE)[0]


def test_cve_placeholder_fails():
    assert qc.placeholders(CVE_STEP) == ["xxxx"]
    assert qc.ungrounded(CVE_STEP, CVP_SOURCE, action=True) == ["CVE-2024-xxxx"]  # the odd hyphens are read as "-"
    bad = failed(news(source_text=CVP_SOURCE, summary="Anthropic expanded its Cyber Verification Program.", enterprise_use=CVE_STEP))
    assert "xxxx" in bad["C8"] and "CVE-2024-xxxx" in bad["C7"]


def test_news_announcements_url_fails():
    assert qc.is_listing_url("https://www.anthropic.com/news/announcements")
    assert "listing page" in failed(news(url="https://www.anthropic.com/news/announcements"))["C9"]


def test_grounded_summary_passes():
    assert qc.ungrounded(GTA_GROUNDED, GTA_SOURCE) == [] and qc.placeholders(GTA_GROUNDED) == []
    assert failed(news()) == {} and qc.grounding_problems(news()) == []


# ---------- C7: every number, %, currency amount, date and ID must be in the source ----------

@pytest.mark.parametrize("text, missing", [
    ("Throughput rose by up to 36%.", []),
    ("Throughput rose by 14-36%.", []),
    ("Throughput rose by 40%.", ["40%"]),
    ("It cut compute cost by 26 percent.", ["26 percent"]),
    ("Four issues report gains.", ["Four"]),
    ("Issues 9, 15, 25 and 33 report gains.", []),
    ("It was released on 24 Oct 2026.", ["24 Oct 2026"]),
    ("It needs an RTX 4090.", ["4090"]),
    ("It patches CVE-2026-1234.", ["CVE-2026-1234"]),
    ("It works with GPT-6.", ["GPT-6"]),
])
def test_claims_are_checked_against_the_source(text, missing):
    assert qc.ungrounded(text, GPU_SOURCE) == missing


def test_a_date_must_match_a_date_in_the_source():
    source = "Published Oct 6, 2026. The program has three access tiers."
    assert qc.ungrounded("Anthropic published it on 6 October 2026 with three tiers.", source) == []
    assert qc.ungrounded("Anthropic published it on 24 October 2026.", source) == ["24 October 2026"]
    assert qc.ungrounded("The program has 3 tiers.", source) == []          # "three" in the source grounds "3"
    assert qc.ungrounded("The program has two tiers and five models.", source) == ["five"]


def test_money_needs_the_same_amount_and_currency():
    assert qc.ungrounded("The IPO raised $81.3 billion.", GTA_SOURCE) == []
    assert qc.ungrounded("The IPO raised £81.3 billion.", GTA_SOURCE) == ["£81.3 billion"]
    assert qc.ungrounded("The IPO raised $81.3 million.", GTA_SOURCE) == ["$81.3 million"]
    assert qc.ungrounded("The game sold 10k copies.", "It sold 10,000 copies.") == []


def test_a_time_frame_is_advice_only_in_the_action_text():
    advice = "1. Rotate the keys on a 90-day cycle.\n2. Pilot it over 6-12 months with Microsoft 365 and ISO 27001."
    assert qc.ungrounded(advice, GTA_SOURCE, action=True) == []
    assert qc.ungrounded("The team trained the model for 90 days.", GTA_SOURCE) == ["90"]
    assert qc.ungrounded("Pilot it with 10-15 engineers.", GTA_SOURCE, action=True) == ["10", "15"]


# ---------- C8 placeholders, C9 listing URLs, C10 forward views ----------

@pytest.mark.parametrize("text", ["CVE-2024-xxxx", "Price: TBD", "Lorem ipsum dolor", "Go to [insert tool]", "See https://example.com/docs"])
def test_placeholders_are_rejected(text):
    assert qc.placeholders(text) and "C8" in failed(news(enterprise_use=text))


@pytest.mark.parametrize("url, listing", [
    ("https://www.anthropic.com/news", True),
    ("https://www.anthropic.com/news/announcements", True),
    ("https://example.test/blog/", True),
    ("https://example.test/index.html", True),
    ("https://example.test/category/ai", True),
    ("https://www.anthropic.com/news/cyber-verification-program", False),
    ("https://openai.com/index/openai-nask-ai-safety-security-partnership", False),
    ("https://github.com/Zagitta/gpu-unlock", False),
    ("https://arxiv.org/abs/2611.01234v1", False),
    ("https://news.ycombinator.com/item?id=50219700", False),
])
def test_listing_urls(url, listing):
    assert qc.is_listing_url(url) is listing


def test_a_forward_view_needs_the_our_take_label():
    source = "OpenAI and NASK have signed an agreement to explore collaboration on AI safety research."
    guess = "OpenAI and NASK signed an agreement. The partnership will likely produce whitepapers and could shape rules."
    assert qc.unlabelled_forward_views(guess, source) == ["likely", "could"]
    assert "C10" in failed(news(source_text=source, summary=guess))
    labelled = "OpenAI and NASK signed an agreement. Our take: this could shape future rules."
    assert qc.unlabelled_forward_views(labelled, source) == []
    assert qc.unlabelled_forward_views("The firm said it could ship later.", "It could ship later, the firm said.") == []


def test_the_gate_holds_back_an_ungrounded_story(monkeypatch):
    monkeypatch.setattr(qc.requests, "get", lambda *a, **k: type("R", (), {"status_code": 200, "close": lambda self: None})())
    logs = []
    kept, held, _ = qc.gate([news(), news(title="Bad", enterprise_use=GTA_INVENTED)], log=logs.append)
    assert [x["title"] for x in held] == ["Bad"] and len(kept) == 1
    assert any("HELD BACK Bad" in m and "C7" in m for m in logs)


# ---------- the scouts: regenerate once, then the gate holds the story back ----------

def test_news_scout_regenerates_once_then_keeps_the_answer_for_the_gate(monkeypatch):
    import industry_news_scout_langgraph as scout
    answers = iter([{"summary": "GTA VI made $9 billion.", "enterprise_use": "Nothing to do."},
                    {"summary": GTA_GROUNDED, "enterprise_use": "Nothing to do."}])
    prompts = []

    def fake(messages, **kw):
        prompts.append(messages[0]["content"])
        return json.dumps({**next(answers), "relevance_score": 4, "relevance_signal": "medium", "category": "Other"})
    monkeypatch.setattr(scout, "groq_complete", fake)
    article = {k: v for k, v in news().items() if k not in ("summary", "enterprise_use", "relevance_score")}
    out = scout.classify_news({"raw_articles": [{**article, "summary_raw": "", "id": "1", "tier": "news"}], "output_file": "x"})
    assert len(prompts) == 2 and "broke the GROUNDING RULES" in prompts[1] and "$9 billion" in prompts[1]
    assert scout.GROUNDING_RULES in prompts[0] and "Our take:" in prompts[0]
    assert out["classified_news"][0]["summary"] == GTA_GROUNDED


def test_case_study_scout_regenerates_once(monkeypatch):
    import case_study_scout_langgraph as scout
    monkeypatch.setenv("TRY_THIS_ENABLED", "0")
    summaries = iter(["Deco cuts cloud cost by 70%.", "Deco routes 12 agents. Our take: it could cut cost."])
    sites = []

    def fake(messages, call_site="", **kw):
        sites.append(call_site)
        if "score" in call_site:
            return json.dumps({"real_world_impact": 20, "ai_maturity": 15, "monetisation_potential": 10})
        return json.dumps({"summary": next(summaries), "it_breakdown": "Test it on one host within 30 days."})
    monkeypatch.setattr(scout, "groq_complete", fake)
    paper = {"id": "p1", "title": "Deco: a router for agents", "abstract": "We present Deco, which routes 12 agents in production.",
             "authors": ["A. Author"], "institution": "MIT", "pub_date": datetime.now().strftime("%Y-%m-%d"), "url": "https://arxiv.org/abs/1"}
    out = scout.score_papers({"raw_papers": [paper], "scored_papers": [], "output_file": "x"})["scored_papers"][0]
    assert sites == ["case_study.score_papers", "case_study.summary", "case_study.summary.regenerate"]
    assert out["summary"].startswith("Deco routes 12 agents.") and qc.grounding_problems(out) == []


def test_news_scout_holds_a_scraped_story_with_no_source_date(monkeypatch, capsys):
    import industry_news_scout_langgraph as scout
    monkeypatch.setattr(scout, "groq_complete", lambda *a, **k: pytest.fail("a story with no source date must not be classified"))
    out = scout.classify_news({"raw_articles": [{"id": "1", "title": "Undated story from a listing page", "summary_raw": "",
                                                 "source": "Anthropic", "tier": "ai_labs", "pub_date": "", "scraped": True,
                                                 "url": "https://www.anthropic.com/news/some-story"}], "output_file": "x"})
    assert out["classified_news"] == [] and "no publish date" in capsys.readouterr().out


def test_news_scout_classifies_a_scraped_story_with_a_recent_source_date(monkeypatch, capsys):
    import industry_news_scout_langgraph as scout
    monkeypatch.setattr(scout, "groq_complete", lambda *a, **k: json.dumps(
        {"summary": "Anthropic expanded its program.", "enterprise_use": "Nothing to do.", "relevance_score": 5,
         "relevance_signal": "medium", "category": "Security"}))
    story = {"id": "1", "title": "Expanding the Cyber Verification Program", "summary_raw": "", "source": "Anthropic", "tier": "ai_labs",
             "scraped": True, "url": "https://www.anthropic.com/news/cyber-verification-program", "source_text": CVP_SOURCE}
    recent = scout.format_date((datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d"))  # "17 Nov 2026"
    old = scout.format_date((datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d"))
    out = scout.classify_news({"raw_articles": [{**story, "pub_date": recent}, {**story, "id": "2", "pub_date": old}], "output_file": "x"})
    assert [(a["id"], a["pub_date"]) for a in out["classified_news"]] == [("1", recent)]
    assert "older than 8 weeks" in capsys.readouterr().out


def test_page_facts_reads_the_sources_own_date_and_title():
    import industry_news_scout_langgraph as scout
    html = ('<html><head><meta property="og:title" content="Expanding the Cyber Verification Program"/>'
            '<meta property="article:published_time" content="2026-10-06T00:00:00.000Z"/></head>'
            '<body><p>Related: Nov 25, 2026</p><time datetime="2026-11-25">Nov 25, 2026</time></body></html>')
    assert scout.page_facts(html) == {"date": "2026-10-06", "title": "Expanding the Cyber Verification Program"}
    assert scout.page_facts("<html><body><p>Updated on Nov 25, 2026</p></body></html>") == {"date": "", "title": ""}


def test_scrape_skips_listing_links_and_never_dates_a_story_today(monkeypatch):
    import industry_news_scout_langgraph as scout
    page = ('<a href="/news/announcements">Announcements and more</a>'
            '<a href="/news/cyber-verification-program"><h3>Expanding the Cyber Verification Program</h3><p>We are launching it.</p></a>')
    monkeypatch.setattr(scout.requests, "get", lambda *a, **k: type("R", (), {"status_code": 200, "text": page})())
    found = scout.scrape_articles(scout.SCRAPE_SOURCES[0], set())
    assert [(a["title"], a["url"], a["pub_date"]) for a in found] == [
        ("Expanding the Cyber Verification Program", "https://www.anthropic.com/news/cyber-verification-program", "")]


def test_judge_gets_the_grounding_rules_only_when_asked(monkeypatch):
    seen = []
    monkeypatch.setattr(judge, "_ask", lambda prompt, model: seen.append(prompt) or json.dumps(
        {"verdicts": [{"id": "0", "verdict": "pass", "score": 1.0, "reason": "ok"}]}))
    monkeypatch.setattr(judge, "_pace", lambda: None)
    item = [{"title": "T", "summary_raw": "S", "summary": "S", "enterprise_use": "A"}]
    judge.gate(item, "title", "summary_raw", "summary", "enterprise_use", log=lambda *_: None)
    judge.gate(item, "title", "summary_raw", "summary", "enterprise_use", log=lambda *_: None, extra_rules=judge.GROUNDING_RULES)
    assert judge.GROUNDING_RULES not in seen[0] and judge.GROUNDING_RULES in seen[1] and '"Our take:"' in seen[1]
    assert judge._extra["rules"] == ""


# ---------- breakdown pages ----------

GOOD_PAGE = ("GTA VI made $3.79 billion on release day.\n\nOur take: it could change little for IT teams.\n---SPLIT---\n"
             "1. EVALUATE - List your AI suppliers.\n5. QUICK WIN - Review contracts in under 30 days.\n"
             "6. LONG-TERM PLAY - Plan the 6-12 month supplier review.")
BAD_PAGE = GOOD_PAGE.replace("List your AI suppliers.", "Benchmark 0.02 £/token for Anthropic vs. 0.015 £/token for OpenAI.")


def test_page_prompt_carries_the_source_text_and_the_rules(monkeypatch):
    prompts = []
    monkeypatch.setattr(gb, "call_groq", lambda prompt, retries=3: prompts.append(prompt))
    gb.generate_content_for_news(news())
    gb.generate_content_for_case_study({"title": "Paper", "abstract": "We test 12 agents.", "authors": ["A"]})
    assert GTA_SOURCE in prompts[0] and "We test 12 agents." in prompts[1]
    assert all(gb.GROUNDING_RULES in p and "Our take:" in p and "metrics or benchmarks if available" not in p for p in prompts)


def test_page_problems_on_the_audited_text():
    assert gb.page_problems(*gb.split_sections(GOOD_PAGE), news()) == []
    assert gb.page_problems(*gb.split_sections(BAD_PAGE), news()) == ["not in source: 0.02 £, 0.015 £"]
    assert gb.page_problems("Summary.", CVE_STEP, news(source_text=CVP_SOURCE)) == ["not in source: CVE-2024-xxxx", "placeholder text: xxxx"]


def test_ai_page_is_published_only_when_grounded(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(gb.time, "sleep", lambda s: None)
    monkeypatch.setattr(gb, "HELD_BACK", [])
    calls = []

    def run(answers, name):
        calls.clear()
        feed = iter(answers)
        monkeypatch.setattr(gb, "generate_content_for_news", lambda item, feedback="": calls.append(feedback) or next(feed))
        slugs = gb.process_items([news(title=name)], "news", "27 Nov 2026, 10:00", tmp_path)
        return (tmp_path / f"{slugs[name]}.html").read_text()

    page = run([GOOD_PAGE], "Good story")
    assert calls == [""] and "List your AI suppliers." in page and not gb.is_short_page(tmp_path / "good-story.html")

    page = run([BAD_PAGE, GOOD_PAGE], "Fixed story")  # one regenerate, with the problems in the prompt
    assert len(calls) == 2 and "0.02 £" in calls[1] and "List your AI suppliers." in page and "£/token" not in page

    page = run([BAD_PAGE, BAD_PAGE], "Held story")  # failed twice: the AI page is held back
    assert len(calls) == 2 and "£/token" not in page and gb.HELD.strip() in page
    assert "GTA VI made $3.79 billion" in page  # the scout's own source-checked summary is shown
    assert [t for t, _ in gb.HELD_BACK] == ["Held story"]
    run([GOOD_PAGE], "Held story")
    assert calls == []  # a held page is not generated again


def test_short_page_leaves_out_stored_text_that_fails_the_checks(tmp_path):
    item = news(title="Old story", summary="GTA VI made $9 billion.", enterprise_use="S1: Budget 0.02 £/token. S2: Watch the price. S3: Okta.")
    summary_html, guide_html = gb.short_content(item, "news")
    assert "$9 billion" not in summary_html and gb.NO_SUMMARY in summary_html
    assert "£/token" not in guide_html and "Watch the price." in guide_html


def test_scan_finds_the_audited_failures_and_fix_re_renders_withdraws_or_removes(tmp_path, monkeypatch):
    out = tmp_path / "site" / "breakdowns"
    out.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    def write(slug, raw, url, meta="TechCrunch · 26 Nov 2026"):
        summary, guide = gb.parse_groq_response(raw)
        (out / f"{slug}.html").write_text(gb.render_breakdown_page(slug, slug, url, "", summary, guide, meta, "27 Nov 2026, 10:00"))
    story = news(title="gta")
    write("gta", BAD_PAGE, story["url"])
    write("good", GOOD_PAGE, story["url"])
    write("announcements", "Anthropic announced things.\n---SPLIT---\n1. EVALUATE - Check.", "https://www.anthropic.com/news/announcements")
    write("orphan", "It costs $5.\n---SPLIT---\n1. EVALUATE - Check CVE-2024-xxxx.", "https://example.test/a-story")
    index = {"gta": (story, "news"), "good": (news(title="good"), "news"),
             "announcements": (news(title="announcements", url="https://www.anthropic.com/news/announcements"), "news")}
    checked, failures = gb.scan_pages(out, index)
    assert checked == 4 and sorted(failures) == ["announcements.html", "gta.html", "orphan.html"]
    assert "0.02 £" in failures["gta.html"][0] and "listing page" in failures["announcements.html"][-1]
    assert any("xxxx" in p for p in failures["orphan.html"])
    rendered, withdrawn, removed = gb.fix_pages(failures, out, index)
    assert (rendered, withdrawn, removed) == (["gta.html"], ["orphan.html"], ["announcements.html"])
    assert "£/token" not in (out / "gta.html").read_text() and gb.is_short_page(out / "gta.html")
    orphan = (out / "orphan.html").read_text()  # no stored source: the address and the link stay, the text goes
    assert gb.WITHDRAWN_NOTE in orphan and "$5" not in orphan and "xxxx" not in orphan and "https://example.test/a-story" in orphan
    assert not gb.is_short_page(out / "orphan.html")
    assert gb.scan_pages(out, index) == (3, {})
    write("orphan-two", "It costs $5.\n---SPLIT---\n1. EVALUATE - Check.", "https://example.test/b-story")
    assert gb.fix_pages(gb.scan_pages(out, index)[1], out, index, delete=True) == ([], [], ["orphan-two.html"])


def test_an_old_scraped_story_shows_no_date():
    meta = lambda item: gb.meta_line_for(item, "news").replace("\u00a0", " ")
    old = {"title": "T", "source": "Anthropic", "pub_date": "24 Oct 2026"}
    assert meta(old) == "Anthropic · Press"                                               # the day of the scrape is not shown
    assert meta({**old, "scraped": True, "pub_date": "06 Oct 2026"}) == "Anthropic · Press · 6 Oct 2026"
    assert meta({**old, "source": "TechCrunch"}) == "TechCrunch · Press · 24 Oct 2026"


# ---------- the live site ----------

def test_no_live_page_fails_the_grounding_checks(monkeypatch):
    # A page is checked against its story's stored source. A page whose story has left the archives (60 to 90 days)
    # gets the checks that need no source.
    monkeypatch.chdir(ROOT)
    checked, failures = gb.scan_pages(ROOT / "site" / "breakdowns", strict=False)
    assert checked > 0 and failures == {}


def test_the_audited_text_is_gone_from_the_site():
    pages = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "site" / "breakdowns").glob("*.html"))
    for banned in ("£0.02/token", "0.02 £/token", "CVE-2024-xxxx", "CVE‑2024‑xxxx", '/news/announcements"'):
        assert banned not in pages, banned
    for archive in ("data/news_archive.json", "site/news_archive.json"):
        assert '/news/announcements"' not in (ROOT / archive).read_text(encoding="utf-8")


def test_a_listing_page_story_gets_no_text_and_no_link(tmp_path):
    item = news(title="Announcements", url="https://www.anthropic.com/news/announcements")
    slugs = gb.process_items([item], "news", "27 Nov 2026, 10:00", tmp_path, use_ai=False)
    page = (tmp_path / f"{slugs['Announcements']}.html").read_text()
    assert gb.NO_SUMMARY in page and "GTA VI made" not in page and "/news/announcements" not in page
    assert gb.scan_pages(tmp_path, {"announcements": (item, "news")}) == (1, {})
