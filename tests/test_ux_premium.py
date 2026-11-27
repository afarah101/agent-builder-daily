"""Premium UX pass: the UX laws on the home page, the archive page and the breakdown pages. Render layer only.

- Hick: 2 tabs and one "Archive" link (end of the tab row, foot of each tab). The archive is its own page.
- Serial position: the lead story is first; "Today's signal" and "From McKinsey" come below the lead row.
- Fitts: the whole card is one link; targets are 44px; the focus ring shows.
- Similarity: one card format: image, tag, title, who · date, 2-line summary, one "How to apply" line.
- Tags: one canonical list, 3 words at most, never cut. Proximity: the meta line is one line with no orphan "·".
- Von Restorff: no "AI illustration" on a card, one footer line; emerald only on the lead tag, links and the button.
- Readability: summary contrast 4.5:1 or more. Miller: 7 cards at most before a "More stories" divider.
No network. The committed pages are checked too."""
import json
import re
import sys
from datetime import datetime
from html import unescape
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import generate_breakdowns as gb  # noqa: E402
import generate_html as gh  # noqa: E402
import generate_images as gi  # noqa: E402

SITE = ROOT / "site"
HOME = (SITE / "index.html").read_text()
NB = " "


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 27, 9, 0)

    @classmethod
    def utcnow(cls):
        return cls(2026, 11, 27, 9, 0)


def news(title, day="2026-11-26", **kw):
    return {"title": title, "url": f"https://example.test/{gi.slugify(title)}", "source": "example.test", "tier": "news",
            "pub_date": day, "summary": "A vendor ships a faster cache. It cuts cost. A third sentence.", "category": "Tools",
            "enterprise_use": "S1: Test it on one service. S2: Cost stays high. S3: Redis.", "relevance_score": 10, **kw}


def study(title, day="2026-11-25", **kw):
    return {"title": title, "url": f"https://arxiv.org/abs/{gi.slugify(title)}", "pub_date": day,
            "authors": ["Ada Lovelace", "Alan Turing", "Grace Hopper"], "summary": "A method. It works.",
            "it_breakdown": "1) Run the pipeline on one repo. 2) Compare the results.", "relevance_tag": "LLM deployment / API optimisation",
            "total_score": 50, **kw}


@pytest.fixture
def site(tmp_path, monkeypatch):
    (tmp_path / "case_studies").mkdir()
    (tmp_path / "data" / "news").mkdir(parents=True)
    (tmp_path / "site" / "images").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(gh, "datetime", Clock)
    return tmp_path


def build(site, studies, items, headlines=None, hook=""):
    (site / "case_studies" / "2026-11-27.json").write_text(json.dumps({"case_studies": studies}))
    (site / "case_studies" / "archive.json").write_text(json.dumps(
        [{"date": "2026-11-20", "total": 1, "case_studies": [study("Old study", "2026-11-20")]}]))
    (site / "data" / "news" / "news_2026-11-27.json").write_text(json.dumps({"news": items, "headlines": headlines or []}))
    (site / "data" / "news_archive.json").write_text(json.dumps([{"date": "2026-11-20", "total": 1, "news": [news("Old news", "2026-11-20")]}]))
    if hook:
        (site / "data" / "daily_hook.txt").write_text(hook)
    gh.generate_html()
    return (site / "site" / "index.html").read_text()


def panel(page, panel_id):
    """The inner HTML of one tab panel."""
    return page.split(f'<div id="{panel_id}"', 1)[1].split(">", 1)[1].split("</a></p>\n  </div>", 1)[0] + "</a></p>"  # ends at its foot


def cards_of(html):
    return re.findall(r'<article class="card [^"]*">(.*?)</article>', html, re.S)


def text_of(fragment):
    return unescape(re.sub(r"<[^>]+>", "", fragment))


# ---------- 1. Hick: 2 tabs and one Archive link ----------

def test_two_tabs_and_an_archive_link_at_the_end_of_the_tab_row(site):
    page = build(site, [study("A")], [news("B")])
    assert re.findall(r'role="tab"[^>]*>([^<]+)<', page) == ["Case Studies", "Industry News"]
    row = page.split('<div class="tab-row">')[1].split("<label")[0]
    assert row.rstrip().endswith('<a class="archive-link" href="archive.html#case-studies">Archive</a>\n      </div>')
    assert 'id="archive-cs"' not in page and 'id="archive-news"' not in page and "Archive: Studies" not in page


def test_archive_link_at_the_foot_of_each_tab(site):
    page = build(site, [study("A")], [news("B")])
    for panel_id, what in (("case-studies", "case studies"), ("industry-news", "news")):
        assert panel(page, panel_id).rstrip().endswith(
            f'<p class="panel-foot"><a class="archive-link" href="archive.html#{panel_id}">Archive: older {what}</a></p>'), panel_id
    assert page.count('class="archive-link"') == 3  # the tab row and the 2 feet


def test_archive_is_its_own_page_with_both_sections(site):
    build(site, [study("A")], [news("B")])
    archive = (site / "site" / "archive.html").read_text()
    assert "<title>Archive — Daily Edge AI</title>" in archive and '<h1 class="hero-title">Archive</h1>' in archive
    assert '<h2 class="section-title" id="case-studies">Case Studies</h2>' in archive
    assert '<h2 class="section-title" id="industry-news">Industry News</h2>' in archive
    assert "Old study" in archive and "Old news" in archive and archive.count('class="archive-row"') == 2
    assert gh.TOKENS_CSS in archive and gh.font_links() in archive and 'role="tab"' not in archive
    assert '<a class="archive-link" href="./">← Back to today</a>' in archive and "Images are AI-generated." in archive


# ---------- 2. Serial position: the lead story first, the signal blocks below the lead row ----------

def test_lead_story_is_first_and_signal_blocks_come_below_the_lead_row(site):
    items = [news(f"News {i}", relevance_score=20 - i) for i in range(9)]
    page = build(site, [study("A")], items, headlines=[{"title": "A McKinsey report", "url": "https://mckinsey.test/r", "pub_date": "25 Nov 2026"}],
                 hook="Agents need budgets.")
    body = panel(page, "industry-news")
    order = [body.index(s) for s in ('<div class="cards-grid lead-grid">', "News 0</a>", "News 2</a>", "Today's signal", "From McKinsey",
                                     "News 3</a>", '<h2 class="group-title">More stories</h2>', "News 7</a>")]
    assert order == sorted(order)
    assert body.strip().startswith('<div class="cards-grid lead-grid"><article class="card news-card">')  # nothing above the lead
    lead_row = body.split("</div>\n    <section")[0]
    assert len(cards_of(lead_row + "</article>")) >= 3 and body.count('<section class="signal">') == 2


def test_case_study_tab_has_no_signal_block_and_starts_with_the_lead(site):
    body = panel(build(site, [study(f"Study {i}", total_score=60 - i) for i in range(4)], [news("B")]), "case-studies")
    assert body.strip().startswith('<div class="cards-grid lead-grid"><article class="card cs-card">') and "signal" not in body
    assert re.findall(r'class="card-link"[^>]*>([^<]+)<', body)[0] == "Study 0"  # the highest score leads


# ---------- 3. Fitts: one link for the whole card, 44px targets, a focus ring ----------

def test_whole_card_is_one_link_with_a_focus_ring(site):
    page = build(site, [study(f"Study {i}") for i in range(3)], [news(f"News {i}") for i in range(3)])
    cards = cards_of(page)
    assert len(cards) >= 6 and all(c.count("<a ") == 1 and c.count('<a class="card-link" href="breakdowns/') == 1 for c in cards)
    assert all("<a" not in c.split('<h2 class="card-title">')[0] for c in cards)  # the image and the tag are not links
    assert all("<a" not in c.split("</h2>")[1] for c in cards)  # the meta, the summary and the how-to line are not links
    for card in cards:  # the one link is the title; the tag and the meta line hold no link and are in no link
        link = re.search(r"<a [^>]*>.*?</a>", card, re.S)[0]
        assert "tag-pill" not in link and "card-meta" not in link and link in re.search(r'<h2 class="card-title">.*?</h2>', card, re.S)[0]
        assert "<a" not in re.search(r'<div class="card-tags">.*?</div>', card, re.S)[0] + re.search(r'<p class="card-meta">.*?</p>', card, re.S)[0]
    css = page.split("<style>")[1].split("</style>")[0]
    assert re.search(r"\.card-link::after \{ content: \"\"; position: absolute; inset: 0;", css) and ".card { position: relative; }" in css
    assert ".card-link:focus-visible::after { outline: 2px solid var(--c-text); outline-offset: 2px; }" in css
    assert "--tap: 44px" in css
    for sel in (".tab", ".archive-link", ".card-title a", ".search", ".email-btn", ".brand-link"):
        assert re.search(re.escape(sel) + r" \{[^}]*min-height: var\(--tap\)", css), sel


# ---------- 4. Similarity: one card format ----------

def test_every_card_has_the_same_parts_in_the_same_order(site):
    (site / "site" / "images" / "a-study.webp").write_bytes(b"RIFF0000WEBP")
    page = build(site, [study("A study")], [news("A story")])
    for card in cards_of(page):
        order = [card.index(s) for s in ('<figure class="card-figure"><img ', '<span class="tag-pill">', '<h2 class="card-title">',
                                         '<p class="card-meta">', '<p class="card-summary">', '<p class="insight"><strong>How to apply:</strong>')]
        assert order == sorted(order)
        assert not re.search(r"<ul|<li|signal-points|tier-label|figcaption|class=\"sep\"", card)


@pytest.mark.parametrize("raw, shown", [
    ("S1: Test it on one service. S2: Cost stays high. S3: Redis.", "Test it on one service."),  # "Do this week"
    ("Watch the rollout. Then plan a pilot.", "Watch the rollout."),  # the old "How to use" line
    ("1. Check the U.S. export rules, e.g. for GPUs. 2. Flag contracts.", "Check the U.S. export rules, e.g. for GPUs.")])
def test_news_card_shows_one_how_to_apply_sentence(raw, shown):
    html = gh.render_news({"news": [news("Story", enterprise_use=raw)]}, {"news": {}})
    assert f'<p class="insight"><strong>How to apply:</strong> {shown}</p>' in html
    assert not re.search(r"How to use|Do this week|Risk if ignored|Tools affected|S[1-3]:", html)


def test_case_study_card_shows_one_how_to_apply_sentence():
    cards = lambda **kw: gh.render_case_studies({"case_studies": [study("Paper", **kw)]}, {"case_studies": {}})
    assert '<p class="insight"><strong>How to apply:</strong> Run the pipeline on one repo.</p>' in cards()
    assert "Compare the results" not in cards()  # the long text is on the breakdown page
    assert gh.HOW_LABEL == "How to apply"


@pytest.mark.parametrize("stored", ["Not directly applicable to IT infrastructure work", "", None])
def test_every_card_has_the_how_to_apply_line_also_with_no_stored_steps(stored):
    line = '<p class="insight"><strong>How to apply:</strong> No direct steps for this story.</p>'
    paper = gh.render_case_studies({"case_studies": [study("Paper", it_breakdown=stored, how_to_apply=stored)]}, {"case_studies": {}})
    story = gh.render_news({"news": [news("Story", enterprise_use=stored)]}, {"news": {}})
    assert line in paper and line in story and "Not directly applicable" not in paper + story  # one format; the stored text is not changed


def test_card_summary_is_at_most_2_full_sentences_and_css_clamps_it_to_2_lines(site):
    page = build(site, [study("A")], [news("B")])
    assert '<p class="card-summary">A vendor ships a faster cache. It cuts cost.</p>' in page and "A third sentence" not in page
    assert "…" not in "".join(cards_of(page)) and "..." not in "".join(cards_of(page))  # no "…" in the text
    css = page.split("<style>")[1].split("</style>")[0]
    assert re.search(r"\.card-summary \{ -webkit-line-clamp: 2; line-clamp: 2; color: var\(--c-text-soft\); \}", css)
    assert re.search(r"\.insight \{ -webkit-line-clamp: 1; line-clamp: 1;", css)
    assert gh.sentences("Dr. Who met the U.S. team, i.e. the lab. They agreed.", 1) == "Dr. Who met the U.S. team, i.e. the lab."
    assert gh.sentences("No full stop", 2) == "No full stop" and gh.sentences("", 2) == ""


# ---------- 5. Tags: one canonical list ----------

def test_canonical_tags_are_3_words_at_most():
    assert all(1 <= len(tag.split()) <= 3 and "…" not in tag for tag in gh.CANONICAL_TAGS)
    for tag in ("Security", "LLM Ops", "AI Agents", "BI & Data", "Tools", "Product Launch", "Research", "Policy", "Infrastructure", "Funding"):
        assert tag in gh.CANONICAL_TAGS


@pytest.mark.parametrize("raw, tag", [
    ("LLM deployment / API optimisation", "LLM Ops"), ("LLM DEPLOYMENT & OPTIMISATION", "LLM Ops"), ("MLOps", "LLM Ops"), ("LLMs", "LLM Ops"),
    ("container_security", "Security"), ("Security / LLM Agent Protection", "Security"), ("AI Safety / LLM Security", "Security"),
    ("BI automation", "BI & Data"), ("Database debugging, llm agents, forensic layer", "BI & Data"),
    ("agentic systems", "AI Agents"), ("LLM agent policy learning", "AI Agents"), ("Regulation", "Policy"),
    ("Product Launch", "Product Launch"), ("Model Release", "Model Release"), ("API Update", "Tools"), ("research", "Research"),
    ("Datacenter llm infrastructure", "Infrastructure"), ("GPU power management for AI workloads", "Infrastructure"),
    ("Computer Vision", "Computer Vision"), ("NLP, MLOps, Other", "NLP"), ("Healthcare AI", "Healthcare AI"), ("Series B funding", "Funding"),
    ("HVAC control", "Automation"), ("Other", "Case Study"), ("Low relevance", "Case Study"), ("IT", "Case Study"), (None, "Case Study"),
    ("<script>alert(1)</script>", "Case Study")])
def test_every_raw_tag_maps_to_one_canonical_tag(raw, tag):
    assert gh.canonical_tag(raw, "case_studies") == tag and tag in gh.CANONICAL_TAGS
    assert f'<span class="tag-pill">{gh.esc(tag)}</span>' in gh.render_case_studies(
        {"case_studies": [study("Paper", relevance_tag=raw)]}, {"case_studies": {}})


def test_news_with_no_topic_shows_news_and_the_tag_is_never_cut(site):
    assert gh.canonical_tag("Other", "news") == "News" and gh.canonical_tag("anything unknown") == "Breakdown"
    css = build(site, [study("A")], [news("B")]).split("<style>")[1].split("</style>")[0]
    assert "text-overflow: ellipsis" not in re.search(r"\.tag-pill \{[^}]*\}", css)[0]


def test_every_tag_in_the_data_maps_to_a_canonical_tag():
    seen = set()
    for path, kind, key in [*((f, "case_studies", "relevance_tag") for f in (ROOT / "case_studies").glob("*.json")),
                            *((f, "news", "category") for f in [*(ROOT / "data" / "news").glob("*.json"), ROOT / "data" / "news_archive.json"])]:
        data = gh.load_json(str(path))
        for day in data if isinstance(data, list) else [data]:
            for item in (day.get(kind) or []) if isinstance(day, dict) else []:
                seen.add(gh.canonical_tag(item.get(key), kind))
    assert seen and seen <= set(gh.CANONICAL_TAGS)


# ---------- 6. Proximity: the meta line ----------

def test_meta_line_is_who_type_date_on_one_line():
    html = gh.render_news({"news": [news("Story", source="TechCrunch", pub_date="25 Nov 2026")]}, {"news": {}})
    assert "tier-label" not in html and html.count("Press") == 1  # no floating "Press" label: the source type is in the meta line only
    assert "Press" in re.search(r'<span class="meta-rest">.*?</span>', html)[0] and "Press" not in re.search(r'<div class="card-header">.*?</div>\s*</div>', html, re.S)[0]
    assert (f'<p class="card-meta"><span class="meta-who">TechCrunch</span>'
            f'<span class="meta-rest">{NB}·{NB}Press{NB}·{NB}25 Nov 2026</span></p>') in html  # the source type is in the meta line
    lab = gh.render_news({"news": [news("Story", source="Hugging Face", tier="ai_labs")]}, {"news": {}})
    assert f"{NB}·{NB}AI Lab{NB}·{NB}26 Nov 2026" in lab
    paper = gh.render_case_studies({"case_studies": [study("Paper", pub_date="2026-08-05")]}, {"case_studies": {}})
    assert (f'<p class="card-meta"><span class="meta-who">Ada Lovelace, Alan Turing et al.</span>'
            f'<span class="meta-rest">{NB}·{NB}5 Aug 2026</span></p>') in paper
    assert "Grace Hopper" not in paper and "Academic Institution" not in paper


def test_meta_line_has_no_separator_that_can_start_a_line(site):
    page = build(site, [study("A", authors=[])], [news("B", source="")])
    for meta in re.findall(r'<p class="card-meta">(.*?)</p>', page):
        assert not re.search(r"(^|[ \n])·", text_of(meta)), meta  # every "·" follows a no-break space, or there is none
        assert not text_of(meta).startswith(("·", NB))
    css = page.split("<style>")[1].split("</style>")[0]
    assert re.search(r"\.card-meta \{[^}]*white-space: nowrap", css)
    assert re.search(r"\.meta-who \{[^}]*overflow: hidden; text-overflow: ellipsis", css) and re.search(r"\.meta-rest \{ flex: none", css)


def test_short_names_and_dates():
    assert gh.short_names(["A", "B", "C", "D"]) == "A, B et al." and gh.short_names(["A", "B"]) == "A, B" and gh.short_names([]) == ""
    assert gh.show_date("2026-11-05") == gh.show_date("05 Nov 2026") == "5 Nov 2026" and gh.show_date("soon") == "soon"


# ---------- 7. Von Restorff: no caption on a card, emerald on 3 things ----------

def test_no_ai_illustration_on_a_card_and_one_footer_line(site):
    (site / "site" / "images" / "a.webp").write_bytes(b"RIFF0000WEBP")
    page = build(site, [study("A")], [news("B")])
    assert 'src="images/a.webp"' in page and "AI illustration" not in page and "<figcaption" not in page
    assert page.count("Images are AI-generated.") == 1 and "Images are AI-generated.</span>\n</footer>" in page


def test_emerald_is_on_the_lead_tag_the_links_and_the_subscribe_button_only(site):
    css = build(site, [study("A")], [news("B")]).split("<style>")[1].split("</style>")[0].replace(gh.TOKENS_CSS, "")
    uses = {}
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", re.sub(r"/\*.*?\*/", "", css, flags=re.S)):
        for prop in re.findall(r"([\w-]+)\s*:\s*[^;]*var\(--c-accent(?:-hover)?\)", body):
            uses.setdefault(prop, set()).add(sel.strip())
    assert set(uses) == {"background", "color", "text-decoration-color"}
    assert uses["background"] == {".email-btn", ".email-btn:hover", ".lead-grid > .card:first-child .tag-pill"}
    assert uses["color"] == {".accent-word"}  # the "AI" of the logo, on the dark hero
    assert all(re.search(r"link|a:hover|footer a|\.archive-title:hover", s) for s in uses["text-decoration-color"])


# ---------- 8. Readability ----------

def luminance(colour):
    r, g, b = (int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return sum(k * (v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4) for k, v in zip((0.2126, 0.7152, 0.0722), (r, g, b)))


def test_summary_contrast_is_at_least_4_5_to_1():
    token = dict(re.findall(r"--(c-[\w-]+):\s*(#[0-9a-fA-F]{6})", gh.TOKENS_CSS))
    text, page = luminance(token["c-text-soft"]), luminance(token["c-bg"])
    ratio = (max(text, page) + 0.05) / (min(text, page) + 0.05)
    assert ratio >= 4.5 and round(ratio, 2) == 10.37
    assert re.search(r"\.card-summary \{[^}]*color: var\(--c-text-soft\)", HOME)


# ---------- 9. Miller ----------

def test_seven_cards_at_most_before_each_more_stories_divider(site):
    body = panel(build(site, [study("A")], [news(f"News {i:02d}", relevance_score=40 - i) for i in range(19)]), "industry-news")
    groups = [len(cards_of(part)) for part in body.split('<h2 class="group-title">More stories</h2>')]
    assert groups == [7, 6, 6] and gh.MORE_TITLE == "More stories" and "More industry news" not in body


# ---------- 10. Image style ----------

def test_image_style_ends_with_the_emerald_only_words_and_no_image_is_redrawn():
    assert gi.HOUSE_STYLE.endswith("single emerald #10b981 accent only, no orange, no copper, no warm metals")
    assert gi.image_prompt("A glass cube").endswith(gi.HOUSE_STYLE) and gi.HOUSE_STYLE.count("emerald") == 1
    assert gi.STYLE_ID == "still-life-1"  # the same id: is_good keeps every image made before this change
    alts = json.loads((SITE / "images" / "alt.json").read_text())
    styled = [entry["style"] for entry in alts.values() if entry.get("style")]
    assert len(styled) >= 20 and set(styled) == {gi.STYLE_ID}


# ---------- 11. Breakdown pages: the same tag, meta and label rules ----------

def test_breakdown_page_uses_the_cards_tag_meta_and_label(tmp_path):
    out = tmp_path / "site" / "breakdowns"
    out.mkdir(parents=True)
    gb.process_items([study("Paper", pub_date="2026-08-05")], "case_studies", "27 Nov 2026, 09:00", out, use_ai=False)
    gb.process_items([news("Story", source="TechCrunch")], "news", "27 Nov 2026, 09:00", out, use_ai=False)
    paper, story = (out / "paper.html").read_text(), (out / "story.html").read_text()
    assert '<p class="tag-pill">LLM Ops</p>' in paper and '<p class="tag-pill">Tools</p>' in story
    assert f'<p class="meta-line">Ada Lovelace, Alan Turing et al.{NB}· 5{NB}Aug{NB}2026</p>' in paper
    assert f'<p class="meta-line">TechCrunch{NB}· Press{NB}· 26{NB}Nov{NB}2026</p>' in story
    assert '<h2 class="section-title">How to apply</h2>' in paper
    assert '<h3 class="guide-label">How to apply</h3><div class="guide-text">Test it on one service.</div>' in story  # was "Do this week"
    assert not re.search(r"Do this week|What to do|How to use|Generated 27 Nov", paper + story)
    assert "Images are AI-generated." in paper and gb.is_short_page(out / "paper.html") and gb.restyle_page(paper) == paper


@pytest.mark.parametrize("stored, shown", [
    ("Jessica Hösl, Benedikt Hofmann, Patrick Stöckle · Academic Institution · 2026-08-26", f"Jessica Hösl, Benedikt Hofmann et al.{NB}· 26{NB}Aug{NB}2026"),
    ("Ars Technica · 26 Nov 2026", f"Ars Technica{NB}· 26{NB}Nov{NB}2026"), ("Unknown · 2026-01-02", f"2{NB}Jan{NB}2026"),
    ("A, B · MIT · Video · 2026-01-02", f"A, B{NB}· Video{NB}· 2{NB}Jan{NB}2026"), ("", "")])
def test_old_meta_lines_are_shown_in_the_new_form(stored, shown):
    assert gh.clean_meta(stored) == shown and gh.clean_meta(shown) == shown


def test_old_page_restyles_to_the_new_rules_and_keeps_the_short_mark():
    old = gb.render_breakdown_page("T", "t", "https://a.test/x", "", "<p>S.</p>", gb.guide_block_html("Do this week", "", "Patch."),
                                   "A, B, C · Academic Institution · 2026-08-26", "27 Nov 2026, 09:00 (short version)", tag="Container security")
    old = old.replace(f'<p class="meta-line">A, B et al.{NB}· 26{NB}Aug{NB}2026</p>',
                      '<p class="meta-line">A, B, C · Academic Institution · 2026-08-26 · Generated 27 Nov 2026, 09:00 (short version)</p>')
    new = gb.restyle_page(old)
    assert f'<p class="meta-line">A, B et al.{NB}· 26{NB}Aug{NB}2026</p>' in new and '<p class="tag-pill">Security</p>' in new
    assert "27 Nov 2026, 09:00 (short version)</p>" in new and gb.restyle_page(new) == new


# ---------- the committed pages ----------

def test_committed_home_page_follows_every_rule():
    assert HOME.count('role="tab"') == 2 and HOME.count('class="archive-link"') == 3
    for panel_id in ("case-studies", "industry-news"):
        body = panel(HOME, panel_id)
        cards = cards_of(body)
        assert cards and body.strip().startswith('<div class="cards-grid lead-grid"><article class="card ')
        for card in cards:
            assert card.count("<a ") == 1 and 'class="card-link"' in card
            tag = text_of(re.search(r'<span class="tag-pill">(.*?)</span>', card)[1])
            assert tag in gh.CANONICAL_TAGS and len(tag.split()) <= 3 and "…" not in tag
            labels = re.findall(r"<strong>(.*?)</strong>", card)
            assert labels == ["How to apply:"] and "AI illustration" not in card and "<ul" not in card
            assert "tier-label" not in card and "<a" not in card.split('<h2 class="card-title">')[0] + card.split("</h2>")[1]
            assert card.count('<p class="card-meta">') == 1 and card.count('<p class="card-summary">') == 1
        assert all(len(cards_of(part)) <= 7 for part in body.split('<h2 class="group-title">More stories</h2>'))
    assert "Images are AI-generated." in HOME.split("<footer>")[1]


def test_committed_archive_and_breakdown_pages_follow_the_rules():
    archive = (SITE / "archive.html").read_text()
    assert '<h1 class="hero-title">Archive</h1>' in archive and archive.count('class="archive-row"') > 0
    pages = sorted((SITE / "breakdowns").glob("*.html"))
    bad = []
    for path in pages:
        html = path.read_text()
        tag = text_of(gb.TAG.search(html)["tag"])
        meta = re.search(r'<p class="meta-line">(.*?)</p>', html)
        if (tag not in gh.CANONICAL_TAGS or meta is None or "Generated" in meta[1] or re.search(r"(^| )·", text_of(meta[1]))
                or re.search(r'class="guide-label">(?:Do this week|What to do|How to use)<', html) or "Images are AI-generated." not in html):
            bad.append(path.name)
    assert len(pages) > 2000 and bad == []
