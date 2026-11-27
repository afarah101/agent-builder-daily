"""Breakdown pages and the archive page use the Daily Edge AI design: the home page's font set and token block,
the dark hero header (logo + name), the white panel, one emerald button, the footer (Privacy link), and no scores."""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import generate_breakdowns as gb  # noqa: E402
import generate_html as gh  # noqa: E402
PAGES = sorted((ROOT / "site" / "breakdowns").glob("*.html"))
OLD = re.compile(r"scout daily|syne|00ff88|/75|scored|ai-reviewed", re.I)
TEXT = re.compile(r'<div class="summary-body">.*?</section>|<div class="guide-grid">.*?</section>', re.S)


def frame(html):
    """The page without its stored article text: some articles use real words such as "underscored"."""
    return TEXT.sub("", html)

CURRENT_SHAPE = """<!DOCTYPE html><html><head><title>X - IT Breakdown - Scout Daily</title></head><body>
<main class="page-wrap">
  <h1 class="page-title">Edge AI &amp; robots</h1>
  <p class="meta-line">Ars Technica · 2026-10-01 · Generated 01 Oct 2026, 09:00</p>
  <a href="https://example.test/story?a=1&amp;b=2" target="_blank" rel="noopener noreferrer" class="source-link">View original source</a>
  <section>
    <h2 class="section-title">Detailed summary</h2>
    <div class="summary-body"><p>Latency (&lt;10 ms).</p><p>Second para.</p></div>
  </section>
  <div class="divider"></div>
  <section>
    <h2 class="section-title">IT engineer application guide</h2>
    <div class="guide-grid"><div class="guide-block red"><h3 class="guide-label">Risks to flag</h3><div class="guide-text">Data residency.</div></div></div>
  </section>
  <p class="generated-note">AI-generated breakdown · Scout Daily · 01 Oct 2026, 09:00</p>
</main></body></html>"""


def page(title="T", url="https://a.test/x"):
    s, g = gb.parse_groq_response("Summary.\n---SPLIT---\n1. EVALUATE - Check.\n4. RISKS TO FLAG - Risk.")
    return gb.render_breakdown_page(title, "t", url, "Read", s, g, "Source · 2026-10-01", "27 Oct 2026, 09:00")


def css_of(html):
    return html.split("<style>")[1].split("</style>")[0]


def test_new_page_has_home_design():
    html = page(title="Edge AI")
    css = css_of(html)
    assert "<title>Edge AI — IT Breakdown — Daily Edge AI</title>" in html
    assert 'css2?family=Geist+Mono:wght@400;700' in html and gh.font_links("../") in html
    assert css.strip().startswith(gh.font_faces("../")) and gh.TOKENS_CSS in css and gb.ARTICLE_CSS in css
    assert ".hero { background: var(--g-hero);" in css and "--c-accent: #10b981" in css
    assert '<link rel="icon" href="../favicon.svg" type="image/svg+xml">' in html
    assert '<a class="brand-link" href="../index.html"><span class="brand-logo" aria-hidden="true"></span>' in html
    assert "background: url(../logo.svg)" in css and "<img" not in html  # no <img> without an image file: the escape tests forbid any
    assert "Daily Edge <span class=\"accent-word\">AI</span>" in html
    assert '<a class="back-link" href="../index.html">← Back to today</a>' in html
    assert '<footer>' in html and '<a href="../privacy.html">Privacy</a>' in html
    assert not OLD.search(html)


def test_new_page_keeps_sections_and_order():
    html = page()  # magazine order: tag, H1, meta, summary, "How to apply" panel, source button, back link
    order = [html.index(s) for s in ('<p class="tag-pill">', 'class="page-title"', 'class="meta-line"',
                                     '<h2 class="section-title">Summary</h2>', '<section class="apply-panel">',
                                     '<h2 class="section-title">How to apply</h2>', 'class="source-link"',
                                     'class="back-link"', "<footer>")]
    assert order == sorted(order)


def test_phone_rules_in_css():
    css = css_of(page())
    body = css.split("body {")[1].split("}")[0]
    assert "--fs-body: 16px" in css and "--lh-body: 1.5;" in css and "font-size: var(--fs-body)" in body and "line-height: var(--lh-body)" in body
    assert "--measure: 68ch" in css and "--reading-max: 672px" in css  # line length under 70ch in a 672px column
    assert re.search(r"\.summary-body \{[^}]*max-width: var\(--measure\)", css)
    assert "overflow-wrap: anywhere" in body  # long words and URLs wrap at 360px
    for sel in (r"\.brand-link", r"\.source-link, \.cta", r"\.back-link, \.text-link, footer a"):
        assert re.search(sel + r" \{[^}]*min-height: var\(--tap\)", css), sel
    assert "--tap: 44px" in css
    assert re.search(r"@media \(max-width: 700px\) \{[^@]*\.header-inner, \.article-head \{ padding: 0 var\(--s-16\); \}", css)
    assert not re.findall(r"font-size:\s*\d+px", css.replace(gh.TOKENS_CSS, ""))  # every size is a token


def test_current_shape_restyled_keeps_text():
    new = gb.restyle_page(CURRENT_SHAPE)
    assert new and not OLD.search(new) and "url(../logo.svg)" in new
    assert '<h1 class="page-title">Edge AI &amp; robots</h1>' in new
    assert '<p class="meta-line">Ars Technica\u00a0· 1\u00a0Oct\u00a02026</p>' in new  # who · date; the stamp is in the note
    assert '<p class="generated-note">AI-generated breakdown&nbsp;· Daily Edge AI&nbsp;· 01 Oct 2026, 09:00</p>' in new
    assert gb.restyle_page(new) == new  # the current shape round-trips
    assert '<p>Latency (&lt;10 ms).</p><p>Second para.</p>' in new
    assert '<div class="guide-block red"><h3 class="guide-label">Risks to flag</h3><div class="guide-text">Data residency.</div>' in new
    assert 'href="https://example.test/story?a=1&amp;b=2"' in new


def test_restyle_is_idempotent_and_keeps_pages_without_source():
    no_source = re.sub(r'\s*<a href[^>]*class="source-link">[^<]*</a>', "", CURRENT_SHAPE)
    once = gb.restyle_page(no_source)
    assert once and "source-link" not in once.split("</style>")[1]
    assert gb.restyle_page(once) == once
    assert gb.restyle_page(gb.restyle_page(CURRENT_SHAPE)) == gb.restyle_page(CURRENT_SHAPE)


def test_every_committed_breakdown_page_has_the_new_design():
    assert PAGES
    stale = [p.name for p in PAGES if "url(../logo.svg)" not in (t := p.read_text())
             or gh.TOKENS_CSS not in t or "← Back to today" not in t or OLD.search(frame(t))]
    assert stale == []


@pytest.mark.parametrize("path", PAGES[:1] + PAGES[-1:], ids=lambda p: p.name[:40])
def test_committed_page_round_trips(path):
    html = path.read_text()
    assert gb.restyle_page(html) == html


def test_archive_page_has_new_design():
    html = (ROOT / "site" / "archive.html").read_text()
    assert "http-equiv" not in html and '<h1 class="hero-title">Archive</h1>' in html  # the archive page, not a redirect
    assert gh.font_links() in html and gh.TOKENS_CSS in html and 'src="logo.svg"' in html
    assert "background: var(--g-hero)" in html and "font-size: var(--fs-body)" in html and not OLD.search(html)
