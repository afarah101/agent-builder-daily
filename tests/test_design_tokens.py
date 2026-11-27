"""One type and layout system on every page: the same font set and token block, the type scale, the 4/8 spacing
scale, emerald only on links, the one button and the accent word, the magazine breakdown page, and the UX-law limits
(44px targets, 4 tabs, 7 items in a group, logo links home, sizes on images, font-display: swap). No network."""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import generate_breakdowns as gb  # noqa: E402
import generate_html as gh  # noqa: E402
import generate_images as gi  # noqa: E402

SITE = ROOT / "site"
ROOT_PAGES = ["index.html", "privacy.html", "news.html", "archive.html"]
BREAKDOWNS = sorted((SITE / "breakdowns").glob("*.html"))
WITH_IMAGE = [p for p in BREAKDOWNS if (SITE / "images" / f"{p.stem}.webp").is_file()]


def css_of(html):
    return html.split("<style>")[1].split("</style>")[0]


def rules(css):
    """(selector, body) of every rule, comments removed; a rule inside @media comes out as its own rule."""
    return [(sel.strip(), body) for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", re.sub(r"/\*.*?\*/", "", css, flags=re.S))]


def breakdown(**kw):
    summary, guide = gb.parse_groq_response("Summary.\n---SPLIT---\n1. EVALUATE - Check.\n4. RISKS TO FLAG - Risk.")
    return gb.render_breakdown_page("T", "t", "https://a.test/x", "", summary, guide, "Source · 2026-10-01", "27 Oct 2026, 09:00", **kw)


def page_css():
    """The CSS of one page of each kind, as committed."""
    pages = {name: css_of((SITE / name).read_text()) for name in ROOT_PAGES}
    pages["breakdown"] = css_of(breakdown())
    return pages


# ---------- one font set and one token block ----------

@pytest.mark.parametrize("name", ROOT_PAGES)
def test_root_pages_load_the_font_set_and_the_token_block(name):
    html = (SITE / name).read_text()
    assert gh.font_links() in html, "font links"
    assert css_of(html).strip().startswith(gh.font_faces()), "Satoshi @font-face"
    assert gh.TOKENS_CSS in css_of(html), "token block"


def test_every_breakdown_page_loads_the_font_set_and_the_token_block():
    assert BREAKDOWNS
    links, faces = gh.font_links("../"), gh.font_faces("../")
    stale = [p.name for p in BREAKDOWNS if links not in (t := p.read_text()) or faces not in t or gh.TOKENS_CSS not in t]
    assert stale == []


def test_font_set_is_satoshi_geist_and_geist_mono_with_swap():
    assert "family=Geist:wght@400;600;700&family=Geist+Mono:wght@400;700&display=swap" in gh.font_links()
    assert 'font-family: "Satoshi"' in gh.font_faces() and "font-display: swap" in gh.font_faces()
    assert 'href="../fonts/satoshi-700.woff2" as="font"' in gh.font_links("../")
    assert (SITE / "fonts" / "satoshi-700.woff2").is_file()  # served from the site: the CSP allows fonts from 'self'
    token = dict(re.findall(r"--(font[\w-]*):\s*\"([^\"]+)\"", gh.TOKENS_CSS))
    assert token == {"font-head": "Satoshi", "font": "Geist", "font-mono": "Geist Mono"}


# ---------- type scale, body text, measure ----------

def test_type_scale_and_body_text():
    root = gh.TOKENS_CSS.split(":root {")[1].split("}")[0]
    sizes = {int(px) for px in re.findall(r"--fs-[\w-]+:\s*(\d+)px", root)}
    assert sizes == {14, 16, 17, 18, 22, 28, 40, 56}  # the scale, plus the 17px body
    assert "--fs-hero: 56px" in root and "--fs-body: 17px" in root and "--lh-body: 1.6;" in root
    assert re.search(r"@media \(min-width: 1024px\) \{\s*:root \{ --fs-hero: 72px; \}\s*\}", gh.TOKENS_CSS)
    assert "--ls-hero: -0.025em" in root and round(-0.025 * 72, 1) == -1.8  # the benchmark's -1.8px at 72px
    assert "--radius-panel: 28.8px" in root and "--g-hero: linear-gradient(#000, var(--c-hero-bottom))" in root
    assert "--reading-max: 680px" in root and 60 <= int(re.search(r"--measure:\s*(\d+)ch", root).group(1)) <= 70


def test_no_page_sets_a_font_size_or_a_colour_outside_the_token_block():
    for name, css in page_css().items():
        own = css.replace(gh.TOKENS_CSS, "")
        assert not re.findall(r"font-size:\s*[\d.]+(?:px|rem|em)", own), name
        assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", own), name
        body = dict(rules(own)).get("body", "")
        assert "font-size: var(--fs-body)" in body and "line-height: var(--lh-body)" in body, name


def test_headings_use_satoshi_and_tags_use_geist_mono():
    home, page = page_css()["index.html"], page_css()["breakdown"]
    assert re.search(r"\.hero-title, \.card-title, \.email-title, \.group-title \{ font-family: var\(--font-head\);", home)
    assert re.search(r"\.brand-link \{[^}]*font-family: var\(--font-head\)", home)
    for sel in (".page-title", ".section-title", ".guide-label", ".brand-link"):
        assert re.search(re.escape(sel) + r" \{[^}]*font-family: var\(--font-head\)", page), sel
    for sel in (".tag-pill", ".meta-line", ".generated-note"):
        assert re.search(re.escape(sel) + r" \{[^}]*font-family: var\(--font-mono\)", page), sel


# ---------- spacing: the 4/8 scale ----------

def test_spacing_is_on_the_4px_grid():
    spacing = re.compile(r"(?:^|;)\s*((?:margin|padding)(?:-\w+)?|gap|row-gap|column-gap)\s*:\s*([^;]+)")
    for name, css in page_css().items():
        for sel, body in rules(css):
            for prop, value in spacing.findall(body):
                off = [px for px in re.findall(r"(?<![\w.])(\d+(?:\.\d+)?)px", value) if float(px) % 4]
                assert not off, (name, sel, prop, value.strip())
    steps = [int(px) for px in re.findall(r"--s-\d+:\s*(\d+)px", gh.TOKENS_CSS)]
    assert steps == [4, 8, 12, 16, 24, 32, 48, 64, 80]


# ---------- emerald: links, the one button, the accent word ----------

BUTTONS = {".email-btn", ".source-link, .cta", ".cta"}


def test_emerald_is_only_a_link_underline_the_button_and_the_accent_word():
    for name, css in page_css().items():
        own = css.replace(gh.TOKENS_CSS, "")
        assert "10b981" not in own.lower(), name
        for sel, body in rules(own):
            for prop, value in re.findall(r"([\w-]+)\s*:\s*([^;]*var\(--c-accent\)[^;]*)", body):
                assert prop in ("color", "background", "text-decoration-color"), (name, sel, prop)
                if prop == "color":
                    assert sel == ".accent-word", (name, sel)  # emerald text is 2.5:1 on white: the dark hero only
                if prop == "background":
                    assert sel in BUTTONS, (name, sel)


def test_one_emerald_button_in_each_view():
    home = (SITE / "index.html").read_text()
    assert home.count('class="email-btn"') == 2  # the hero and the page end: never in one view
    assert breakdown().count('class="source-link"') == 1 and 'class="cta"' not in breakdown()
    for name in ("privacy.html", "news.html", "archive.html"):
        assert (SITE / name).read_text().count('class="cta"') == 1, name


# ---------- UX laws ----------

def test_hick_four_tabs_at_most():
    assert (SITE / "index.html").read_text().count('role="tab"') == 4


def cards(n):
    return "\n".join(f'<article class="card cs-card">card {i}</article>' for i in range(n))


@pytest.mark.parametrize("n, groups", [(1, [1]), (7, [7]), (8, [4, 4]), (10, [5, 5]), (15, [5, 5, 5])])
def test_miller_cards_come_in_grids_of_seven_at_most(n, groups):
    html = gh.card_grids(cards(n), "More case studies")
    grids = html.split('<div class="cards-grid">')[1:]
    assert [g.count("<article") for g in grids] == groups and sum(groups) == n
    assert html.count('<h2 class="group-title">More case studies</h2>') == len(groups) - 1


def test_miller_empty_state_stays_in_one_grid():
    empty = "<div class='empty-state'><p>No case studies available yet.</p></div>"
    assert gh.card_grids(empty, "More") == f'<div class="cards-grid">{empty}</div>'


def test_miller_archive_days_are_grouped_by_week():
    days = [{"date": f"2026-11-{d:02d}", "total": 1, "news": [{"title": f"T{d}", "url": "https://a.test"}]} for d in range(27, 0, -1)]
    html = gh.render_archive(days, "news")
    weeks = html.split('<section class="archive-week">')[1:]
    assert [w.count('class="archive-row"') for w in weeks] == [5, 7, 7, 7, 1]
    assert '<h2 class="group-title">Week of 23 Nov 2026</h2>' in weeks[0] and "Week of 26 Oct 2026" in weeks[-1]
    assert gh.week_title("d") == "Other dates"


def test_miller_committed_home_page_has_no_group_above_seven():
    home = (SITE / "index.html").read_text()
    for part in re.split(r'<div class="(?:cards-grid|archive-list)">', home)[1:]:
        group = part.split("</section>")[0].split('<h2 class="group-title">')[0]
        assert max(group.count('<article class="card '), group.count('<div class="archive-row">')) <= gh.GROUP_MAX


def test_jakob_the_logo_links_home_on_every_page():
    assert re.search(r'<a class="brand-link" href="\./"><img class="brand-logo" src="logo\.svg"', (SITE / "index.html").read_text())
    assert re.search(r'<a class="brand-link" href="\./"><img class="brand-logo" src="logo\.svg"', (SITE / "privacy.html").read_text())
    for name in ("news.html", "archive.html"):
        assert '<a class="brand-link" href="/"><img class="brand-logo" src="logo.svg"' in (SITE / name).read_text(), name
    assert '<a class="brand-link" href="../index.html"><span class="brand-logo" aria-hidden="true"></span>' in breakdown()


def test_fitts_every_link_and_button_rule_has_the_44px_target():
    assert "--tap: 44px" in gh.TOKENS_CSS
    privacy = (SITE / "privacy.html").read_text()
    assert gb.ARTICLE_CSS in privacy  # the breakdown page's shell: its targets are checked in test_breakdown_pages.py
    assert set(re.findall(r'<a class="([\w-]+)"', privacy)) == {"brand-link", "text-link", "cta"} and "<a href" not in privacy
    for name in ("news.html", "archive.html"):
        css = css_of((SITE / name).read_text())
        for sel in (".brand-link", ".cta"):
            assert re.search(re.escape(sel) + r" \{[^}]*min-height: var\(--tap\)", css), (name, sel)


def test_doherty_every_image_has_a_width_and_a_height():
    pages = [SITE / name for name in ROOT_PAGES] + WITH_IMAGE
    for path in pages:
        for img in re.findall(r"<img [^>]*>", path.read_text()):
            assert re.search(r' width="\d+"', img) and re.search(r' height="\d+"', img), (path.name, img)


# ---------- privacy page ----------

def test_privacy_page_is_one_680px_column_with_short_sections():
    page = (SITE / "privacy.html").read_text()
    assert page.count("<h1") == 1 and '<h1 class="page-title">How we use your email</h1>' in page
    assert '<article class="article prose">' in page and ".article { max-width: var(--reading-max);" in page
    sections = re.findall(r"<section>(.*?)</section>", page, re.S)
    assert 1 <= len(sections) <= gh.GROUP_MAX
    for section in sections:
        assert section.count('<h2 class="section-title">') == 1 and len(re.findall(r"<p>|<li>", section)) <= 3


# ---------- breakdown page: magazine layout ----------

def test_breakdown_page_order_with_an_image(tmp_path):
    out = tmp_path / "site" / "breakdowns"
    out.mkdir(parents=True)
    (tmp_path / "site" / "images").mkdir()
    (tmp_path / "site" / "images" / "t.webp").write_bytes(b"RIFF0000WEBP")
    (tmp_path / "site" / "images" / "alt.json").write_text(json.dumps({"t": {"alt": 'Glass "cube"'}}))
    figure = gb.article_figure("t", out)
    assert figure.startswith(f'<figure class="article-figure"><img src="../images/t.webp" width="{gi.WIDTH}" height="{gi.HEIGHT}" ')
    assert 'alt="Glass &quot;cube&quot;"' in figure and "<figcaption>AI illustration</figcaption>" in figure
    assert gb.article_figure("none", out) == "" and gb.article_figure("", out) == ""
    html = breakdown(tag="Security", figure=figure)
    order = [html.index(s) for s in ('<p class="tag-pill">Security</p>', '<h1 class="page-title">', '<p class="meta-line">',
                                     '<figure class="article-figure">', '<h2 class="section-title">Summary</h2>',
                                     '<section class="apply-panel">', 'class="source-link"', 'class="back-link"')]
    assert order == sorted(order)
    assert gb.restyle_page(html) == html  # the tag and the image stay when the page is framed again


def test_breakdown_tag_is_the_topic_or_the_kind_of_story():
    assert gb.tag_for({"category": "Security"}, "news") == "Security"
    assert gb.tag_for({"category": "Low relevance"}, "news") == "News"  # a grade is never a tag
    assert gb.tag_for({"relevance_tag": "container_security"}, "case_studies") == "Container security"
    assert gb.tag_for({}, "case_studies") == "Case study"
    assert '<p class="tag-pill">Breakdown</p>' in breakdown()  # a page whose story is in no data file
    assert '<p class="tag-pill">&lt;b&gt;</p>' in breakdown(tag="<b>")


def test_restyle_takes_the_known_tag_and_the_image(tmp_path):
    out = tmp_path / "site" / "breakdowns"
    out.mkdir(parents=True)
    (tmp_path / "site" / "images").mkdir()
    (out / "t.html").write_text(breakdown())
    (out / "u.html").write_text(breakdown(tag="Tools"))
    (tmp_path / "site" / "images" / "t.webp").write_bytes(b"RIFF0000WEBP")
    assert gb.restyle_existing_pages(out, {"t": "Security"}) == (2, [])
    t, u = (out / "t.html").read_text(), (out / "u.html").read_text()
    assert '<p class="tag-pill">Security</p>' in t and '<img src="../images/t.webp"' in t
    assert '<p class="tag-pill">Tools</p>' in u and "<img" not in u  # no data: the page keeps its tag


def test_page_shows_its_image_exactly_when_the_file_exists(tmp_path):
    out = tmp_path / "site" / "breakdowns"
    out.mkdir(parents=True)
    images = tmp_path / "site" / "images"
    images.mkdir()
    (out / "t.html").write_text(breakdown(tag="Security"))
    assert gb.sync_figures(out) == 0 and "<img" not in (out / "t.html").read_text()
    (images / "t.webp").write_bytes(b"RIFF0000WEBP")  # the image is made after the page
    assert gb.sync_figures(out) == 1 and '<img src="../images/t.webp"' in (out / "t.html").read_text()
    assert gb.sync_figures(out) == 0
    (images / "t.webp").unlink()  # generate_images.py deletes it after 4 months
    assert gb.sync_figures(out) == 1
    assert (out / "t.html").read_text() == breakdown(tag="Security")


def test_committed_pages_with_an_image_show_it():
    assert WITH_IMAGE
    for path in WITH_IMAGE:
        assert f'<img src="../images/{path.stem}.webp" width="{gi.WIDTH}" height="{gi.HEIGHT}"' in path.read_text(), path.name
    shown = sum('<figure class="article-figure">' in p.read_text() for p in BREAKDOWNS)
    assert shown == len(WITH_IMAGE)


# ---------- cards: the benchmark's pattern (therundown.ai, read live on 27 Nov 2026) ----------

def test_cards_have_no_box_as_on_the_benchmark():
    css = page_css()["index.html"]
    card = [body for sel, body in rules(css) if sel == ".card"]
    assert card and not [b for b in card if re.search(r"border|padding|background|box-shadow", b)]
    assert re.search(r"\.card-figure img \{[^}]*border-radius: var\(--radius-card\)", css)  # only the image has a radius
    assert re.search(r"\.cards-grid \{[^}]*gap: 32px 24px;", css)  # the gap separates the cards
    # titles: 18px in the grid (benchmark 17px), 22px for the lead from 641px, 28px from 1025px (benchmark 30px)
    assert re.search(r"\.card-title \{[^}]*font-size: var\(--fs-lead\)", css)
    assert re.search(r"\.cards-grid > \.card:first-child \.card-title \{ font-size: var\(--fs-h4\); \}", css)
    assert re.search(r"\.cards-grid > \.card:first-child \.card-title \{ font-size: var\(--fs-h3\); \}", css)
