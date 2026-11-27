"""Every card on the home page is one link to its own breakdown page, and no relevance or score label shows.

The card's only <a> is the title, stretched over the card with CSS. Every shown card (top_up archive cards included)
has a page under site/breakdowns/ and a slug_map entry, with or without Groq. No network: Groq is stubbed."""
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import generate_breakdowns as gb  # noqa: E402
import generate_html as gh  # noqa: E402

INDEX = ROOT / "site" / "index.html"
CARD = re.compile(r'<article class="card (?:cs|news)-card">(.*?)</article>', re.S)
HREF = re.compile(r'<a [^>]*href="([^"]*)"')
TODAY = datetime.now()
DAY = lambda back: (TODAY - timedelta(days=back)).strftime("%Y-%m-%d")  # noqa: E731
STORY_TEXT = re.compile(r'<p class="card-summary">.*?</p>|<p class="insight">.*?</p>|<ul class="signal-points">.*?</ul>'
                        r'|<section class="signal">.*?</section>|<a [^>]*>.*?</a>|<img [^>]*>', re.S)
FULL = "Full summary.\n---SPLIT---\n1. EVALUATE - Check it.\n5. QUICK WIN - Ship it."


def cards_of(page):
    return CARD.findall(page)


def study(title, day=2, **kw):
    return {"title": title, "url": f"https://arxiv.org/abs/{gb.slugify(title)}", "pub_date": DAY(day), "authors": ["A. Author"],
            "summary": f"Summary of {title}.", "it_breakdown": f"Apply {title}.", "total_score": 50, "relevance_tag": "container_security", **kw}


def story(title, day=1, **kw):
    return {"title": title, "url": f"https://example.test/{gb.slugify(title)}", "source": "example.test",
            "pub_date": (TODAY - timedelta(days=day)).strftime("%d %b %Y"), "summary": f"Summary of {title}.",
            "enterprise_use": "S1: Patch it. S2: Breach risk. S3: Okta.", "relevance_score": 8, "category": "Security", **kw}


@pytest.fixture
def site(tmp_path, monkeypatch):
    (tmp_path / "case_studies").mkdir()
    (tmp_path / "data" / "news").mkdir(parents=True)
    (tmp_path / "site" / "breakdowns").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(gb.time, "sleep", lambda s: None)
    return tmp_path


def write_data(site, studies, archive_studies, items, archive_news):
    (site / "case_studies" / f"{DAY(0)}.json").write_text(json.dumps({"case_studies": studies}))
    (site / "case_studies" / "archive.json").write_text(json.dumps([{"date": DAY(2), "total": len(archive_studies), "case_studies": archive_studies}]))
    (site / "data" / "news" / f"news_{DAY(0)}.json").write_text(json.dumps({"news": items}))
    (site / "data" / "news_archive.json").write_text(json.dumps([{"date": DAY(2), "total": len(archive_news), "news": archive_news}]))


# ---------- the committed home page ----------

def test_every_committed_card_has_exactly_one_breakdown_link_and_the_page_exists():
    cards = cards_of(INDEX.read_text())
    assert len(cards) >= 2
    for card in cards:
        hrefs = HREF.findall(card)
        assert len(hrefs) == 1, card[:200]  # one <a>: no source link, no second "read more" link
        assert re.fullmatch(r"breakdowns/[a-z0-9-]+\.html", hrefs[0]), hrefs[0]
        assert (ROOT / "site" / hrefs[0]).is_file(), hrefs[0]


def test_every_committed_card_page_has_summary_how_to_apply_source_and_back_link():
    for card in cards_of(INDEX.read_text()):
        page = (ROOT / "site" / HREF.findall(card)[0]).read_text()
        assert '<h2 class="section-title">Summary</h2>' in page and '<h2 class="section-title">How to apply</h2>' in page
        assert '<a class="back-link" href="../index.html">← Back to today</a>' in page
        assert re.search(r'<a href="https://[^"]+" target="_blank" rel="noopener noreferrer" class="source-link">Read the original →</a>', page)
        assert "<p>" in page.split('class="summary-body">')[1] and 'class="guide-block' in page


def test_slug_map_covers_every_committed_card():
    slug_map = json.loads((ROOT / "site" / "breakdowns" / "slug_map.json").read_text())
    slugs = set(slug_map["case_studies"].values()) | set(slug_map["news"].values())
    page = INDEX.read_text()
    assert {HREF.findall(c)[0] for c in cards_of(page)} == {f"breakdowns/{s}.html" for s in slugs}
    assert len(slug_map["case_studies"]) == page.count('class="card cs-card"') > 0
    assert len(slug_map["news"]) == page.count('class="card news-card"') > 0


def test_no_relevance_or_score_label_on_the_committed_page():
    page = INDEX.read_text()
    labels = re.findall(r'<span class="(?:tag-pill|tier-label)">(.*?)</span>', page)
    assert labels and not [t for t in labels if re.search(r"relevan|score|signal|\d\s*/\s*\d", t, re.I)]
    assert not [t for t in labels if re.fullmatch(r"(low|medium|moderate|high)", t.strip(), re.I)]
    frame = STORY_TEXT.sub("", page.split("</style>")[1])  # the page without the stories' own sentences
    assert "relevance" not in frame.lower() and "score-ring" not in frame and "/75" not in frame


# ---------- label code: a topic tag only ----------

@pytest.mark.parametrize("raw", ["Low relevance", "low_relevance", "high", "Medium", "IT_relevance_moderate", "high_novelty_low_relevance",
                                 "Niche research, low impact", "moderately relevant", "relevance_score: 7", "", None])
def test_a_grade_is_never_shown_as_the_tag(raw):
    assert gh.topic_tag(raw) == "Other"
    html = gh.render_case_studies({"case_studies": [study("Paper", relevance_tag=raw)]}, {"case_studies": {}})
    assert '<span class="tag-pill">Case Study</span>' in html and "relevan" not in html.lower() and "impact" not in html.lower()


@pytest.mark.parametrize("raw, shown", [("container_security", "Container security"), ("Security", "Security"),
                                        ("High-performance computing", "High-performance computing"), ("LLMs", "LLMs")])
def test_a_topic_tag_is_kept(raw, shown):
    assert gh.topic_tag(raw) == shown


# ---------- card markup: the whole card is the tap target ----------

def test_card_has_one_link_stretched_over_it():
    cs = gh.render_case_studies({"case_studies": [study("Paper")]}, {"case_studies": {"Paper": "paper"}})
    news = gh.render_news({"news": [story("Story")]}, {"news": {"Story": "story"}})
    for html, slug, url in ((cs, "paper", "https://arxiv.org/abs/paper"), (news, "story", "https://example.test/story")):
        assert html.count("<a ") == 1 and f'<a class="card-link" href="breakdowns/{slug}.html">' in html
        assert url not in html and "target=" not in html  # the source link is on the breakdown page; same tab
        assert "Read the breakdown" not in html and "card-footer" not in html and "onclick" not in html


def test_card_without_a_page_has_no_dead_link():
    html = gh.render_news({"news": [story("Story")]}, {"news": {}})
    assert "<a " not in html and '<h2 class="card-title">Story</h2>' in html


def test_stretched_link_focus_ring_and_44px(site):
    write_data(site, [study("Paper")], [], [story("Story")], [])
    gh.generate_html()
    css = (site / "site" / "index.html").read_text().split("<style>")[1].split("</style>")[0]
    assert re.search(r"\.card \{ position: relative; \}", css)
    assert re.search(r'\.card-link::after \{ content: ""; position: absolute; inset: 0;', css)
    assert re.search(r"\.card-link:focus-visible::after \{ outline: 2px solid var\(--c-text\); outline-offset: 2px; \}", css)
    assert re.search(r"\.card-title a \{[^}]*min-height: var\(--tap\)", css) and "--tap: 44px" in css


# ---------- a page and a slug for every shown card ----------

def test_built_page_links_every_card_top_up_cards_too(site):
    write_data(site, [], [study(f"Old study {i}") for i in range(9)],
               [story(f"News {i}") for i in range(2)], [story(f"Old news {i}", day=3) for i in range(8)])
    gh.generate_html()  # no generate_breakdowns run, no slug_map.json, no pages: the root-cause case
    page = (site / "site" / "index.html").read_text()
    cards = cards_of(page)
    assert len(cards) >= 16 and page.count('class="card cs-card"') == 9
    for card in cards:
        hrefs = HREF.findall(card)
        assert len(hrefs) == 1 and (site / "site" / hrefs[0]).is_file(), card[:200]
    slug_map = json.loads((site / "site" / "breakdowns" / "slug_map.json").read_text())
    assert len(slug_map["case_studies"]) == 9 and len(slug_map["news"]) == len(cards) - 9
    assert all(t.startswith("Old study") for t in slug_map["case_studies"])


def test_shown_items_are_the_cards_on_the_page(site):
    write_data(site, [study("New study"), study("Too old", day=200)], [study(f"Old study {i}") for i in range(8)],
               [story("News"), story("Future", day=-30)], [story(f"Old news {i}", day=3) for i in range(8)])
    studies, news = gb.shown_items()
    gh.generate_html()
    page = (site / "site" / "index.html").read_text()
    titles = lambda cls: re.findall(rf'<article class="card {cls}">.*?<a class="card-link"[^>]*>([^<]*)</a>', page, re.S)  # noqa: E731
    assert [s["title"] for s in studies] == titles("cs-card") and len(studies) == 9
    assert [n["title"] for n in news] == titles("news-card") and len(news) == 9
    assert "Too old" not in page and "Future" not in page


def test_short_page_when_groq_gives_nothing(site):
    out = site / "site" / "breakdowns"
    items = [study("Paper"), study("No notes", it_breakdown="Not directly applicable to IT work")]
    slugs = gb.process_items(items, "case_studies", "27 Nov 2026, 09:00", out, use_ai=False)
    slugs.update(gb.process_items([story("Story")], "news", "27 Nov 2026, 09:00", out, use_ai=False))
    assert slugs == {"Paper": "paper", "No notes": "no-notes", "Story": "story"}
    paper, none, news = ((out / f"{s}.html").read_text() for s in ("paper", "no-notes", "story"))
    assert "<p>Summary of Paper.</p>" in paper and '<h3 class="guide-label">How to apply</h3><div class="guide-text">Apply Paper.</div>' in paper
    assert 'href="https://arxiv.org/abs/paper" target="_blank" rel="noopener noreferrer" class="source-link">Read the original →</a>' in paper
    assert '<a class="back-link" href="../index.html">← Back to today</a>' in paper
    assert "Not directly applicable" not in none and "No how-to notes are stored" in none
    assert news.count('class="guide-block"') == 3 and "S1:" not in news and "Patch it." in news  # S1/S2/S3 as three blocks
    assert all(gb.is_short_page(out / f"{s}.html") and gb.restyle_page(t) == t for s, t in (("paper", paper), ("story", news)))


def test_every_item_gets_a_page_when_groq_fails_or_hits_the_limit(site, monkeypatch):
    out = site / "site" / "breakdowns"
    answers = iter([None, "no split marker", "DAILY_LIMIT"])
    calls = []
    monkeypatch.setattr(gb, "generate_content_for_news", lambda item: calls.append(item["title"]) or next(answers))
    slugs = gb.process_items([story(f"News {i}") for i in range(5)], "news", "27 Nov 2026, 09:00", out)
    assert len(slugs) == 5 and all((out / f"{s}.html").is_file() for s in slugs.values())
    assert calls == ["News 0", "News 1", "News 2"]  # after the daily limit: no more calls, still a page each


def test_short_page_becomes_full_and_a_full_page_is_never_rewritten(site, monkeypatch):
    out = site / "site" / "breakdowns"
    gb.process_items([study("Paper")], "case_studies", "27 Nov 2026, 09:00", out, use_ai=False)
    calls = []
    monkeypatch.setattr(gb, "generate_content_for_case_study", lambda cs: calls.append(cs["title"]) or FULL)
    assert gb.process_items([study("Paper")], "case_studies", "28 Nov 2026, 09:00", out) == {"Paper": "paper"}
    full = (out / "paper.html").read_text()
    assert calls == ["Paper"] and "<p>Full summary.</p>" in full and "Quick win" in full and not gb.is_short_page(out / "paper.html")
    gb.process_items([study("Paper")], "case_studies", "29 Nov 2026, 09:00", out)
    gb.ensure_pages([study("Paper")], [], out)
    assert calls == ["Paper"] and (out / "paper.html").read_text() == full


def test_short_page_is_kept_while_groq_still_fails(site, monkeypatch):
    out = site / "site" / "breakdowns"
    gb.process_items([study("Paper")], "case_studies", "27 Nov 2026, 09:00", out, use_ai=False)
    before = (out / "paper.html").read_text()
    monkeypatch.setattr(gb, "generate_content_for_case_study", lambda cs: None)
    assert gb.process_items([study("Paper")], "case_studies", "28 Nov 2026, 09:00", out) == {"Paper": "paper"}
    assert (out / "paper.html").read_text() == before


def test_a_title_without_ascii_still_gets_a_page(site):
    slugs = gb.ensure_pages([], [story("日本語のニュース", url="https://example.test/jp")], site / "site" / "breakdowns")
    slug = slugs["news"]["日本語のニュース"]
    assert re.fullmatch(r"story-[0-9a-f]{10}", slug) and (site / "site" / "breakdowns" / f"{slug}.html").is_file()


def test_hostile_text_is_escaped_on_a_short_page(site):
    bad = '<script>alert(1)</script><img src=x onerror=alert(2)>'
    out = site / "site" / "breakdowns"
    gb.ensure_pages([study("Paper", summary=bad, it_breakdown=bad, url="javascript:alert(1)", authors=[bad])], [], out)
    page = (out / "paper.html").read_text()
    assert "<script" not in page and "<img" not in page and "javascript:" not in page and "source-link\">" not in page


# ---------- breakdown page: the home page's tokens ----------

def test_breakdown_page_uses_the_home_page_tokens(site):
    write_data(site, [study("Paper")], [], [story("Story")], [])
    gh.generate_html()
    home = (site / "site" / "index.html").read_text().split("<style>")[1].split("</style>")[0]
    page = (site / "site" / "breakdowns" / "paper.html").read_text().split("<style>")[1].split("</style>")[0]
    assert gh.TOKENS_CSS in home and gh.TOKENS_CSS in page  # one token block, the same bytes
    assert not re.findall(r"#[0-9a-fA-F]{6}\b", page.replace(gh.TOKENS_CSS, ""))  # no colour outside the token block
