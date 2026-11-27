"""Pre-launch items: dates, archive page, SEO tags, privacy page, consent box, skip link and focus, lead image.
Checks the generators and the committed pages. No network."""
import json
import re
import struct
import sys
from html import unescape
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import generate_breakdowns as gb  # noqa: E402
import generate_html as gh  # noqa: E402

SITE = ROOT / "site"
HOME, ARCHIVE, PRIVACY = (SITE / n for n in ("index.html", "archive.html", "privacy.html"))
BREAKDOWNS = sorted((SITE / "breakdowns").glob("*.html"))
ROOT_PAGES = {HOME: "", ARCHIVE: "archive.html", PRIVACY: "privacy.html"}
TAGS = ('<meta name="description" content="', '<link rel="canonical" href="', '<meta property="og:title" content="',
        '<meta property="og:description" content="', '<meta property="og:image" content="', '<meta name="twitter:card" content="')


def head_of(path):
    return path.read_text(encoding="utf-8").split("</head>")[0]


def tag_value(head, start):
    return unescape(head.split(start, 1)[1].split('"', 1)[0])


# ---------- 1. Dates ----------

def test_no_committed_page_shows_a_cut_year():
    cut = re.compile(r"\b\d{1,2} [A-Z][a-z]{2} 202(?!\d)| 202[^0-9]")
    bad = [p.name for p in [*ROOT_PAGES, *BREAKDOWNS] if cut.search(re.sub(r"<style>.*?</style>", "", p.read_text(encoding="utf-8"), flags=re.S))]
    assert bad == []


@pytest.mark.parametrize("stored, shown", [("2026-11-26", "26 Nov 2026"), ("26 Nov 2026", "26 Nov 2026"),
                                           ("06 Oct 2026", "6 Oct 2026"), ("Thu, 26 Nov 2026 10:00:00 GMT", "26 Nov 2026")])
def test_every_stored_date_form_shows_the_whole_year(stored, shown):
    assert gh.show_date(stored) == shown
    assert gh.meta_text({"source": "S", "pub_date": stored}, "news").replace(gh.NBSP, " ").endswith(shown)
    assert gh.clean_meta(f"S · {stored}").replace(gh.NBSP, " ").endswith(shown)


def test_archive_rows_show_the_date_in_the_same_form():
    html = gh.render_archive([{"date": "2026-11-26", "total": 1,
                               "news": [{"title": "T", "url": "https://a.test/x", "summary": "A thing shipped."}]}], "news")
    assert '<div class="archive-date">26 Nov 2026</div>' in html and "2026-11-26" not in html


# ---------- 2. Archive ----------

def test_archive_is_a_real_page_linked_from_the_home_page():
    archive = ARCHIVE.read_text(encoding="utf-8")
    assert "http-equiv" not in archive.lower() and "location.replace" not in archive and "location.href" not in archive
    assert '<h1 class="hero-title">Archive</h1>' in archive
    for section in archive.split('<h2 class="section-title"')[1:]:
        dates = [gh.parse_date(d) for d in re.findall(r'<div class="archive-date">([^<]+)</div>', section)]
        assert 1 <= len(dates) <= 30 and all(dates) and dates == sorted(dates, reverse=True)  # newest first, 30 days at most
    assert 'href="archive.html#case-studies"' in HOME.read_text(encoding="utf-8")


def test_archive_shows_30_days_at_most():
    days = [{"date": f"2026-{m:02d}-{d:02d}", "total": 1,
             "news": [{"title": "T", "url": "https://a.test/x", "summary": "A thing shipped."}]}
            for m in (11, 10) for d in range(28, 0, -1)]
    assert gh.render_archive(days, "news").count('<div class="archive-row">') == 30


# ---------- 3. SEO ----------

def test_every_root_page_has_the_seo_tags_and_its_own_address():
    for path, name in ROOT_PAGES.items():
        head = head_of(path)
        assert all(head.count(t) == 1 for t in TAGS), path.name
        assert tag_value(head, TAGS[1]) == f"{gh.SITE_URL}/{name}", path.name
        assert tag_value(head, TAGS[4]) == gh.OG_IMAGE and 'name="twitter:card" content="summary_large_image"' in head


def test_every_breakdown_page_has_the_seo_tags_and_its_own_address():
    assert BREAKDOWNS
    bad = []
    for path in BREAKDOWNS:
        head = head_of(path)
        if not (all(head.count(t) == 1 for t in TAGS) and tag_value(head, TAGS[1]) == f"{gh.SITE_URL}/breakdowns/{path.stem}.html"
                and tag_value(head, TAGS[4]) == gh.OG_IMAGE and 30 <= len(tag_value(head, TAGS[0])) <= gh.DESCRIPTION_MAX):
            bad.append(path.name)
    assert bad == []


def test_titles_and_canonical_urls_are_unique():
    pages = [*ROOT_PAGES, *BREAKDOWNS]
    heads = [head_of(p) for p in pages]
    canonicals = [tag_value(h, TAGS[1]) for h in heads]
    assert len(set(canonicals)) == len(pages)
    titles = [re.search(r"<title>(.*?)</title>", h, re.S)[1] for h in heads]
    repeats = {t for t in titles if titles.count(t) > 1}
    # Two stories with the same headline are two pages; no title is shared for any other reason
    assert all(t.endswith("— IT Breakdown — Daily Edge AI") for t in repeats) and len(repeats) <= len(pages) // 100


def test_og_image_is_1200_by_630():
    data = (SITE / "og-image.png").read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and struct.unpack(">II", data[16:24]) == (1200, 630)


def test_seo_values_are_escaped_and_short():
    tags = gh.seo_tags('A "quote" & <b>', "x " * 200, "breakdowns/a.html", "article")
    assert "<b>" not in tags and "&quot;quote&quot; &amp; &lt;b&gt;" in tags
    assert len(tag_value(tags, TAGS[0])) <= gh.DESCRIPTION_MAX and tag_value(tags, TAGS[0]).endswith("…")
    page = gb.render_breakdown_page("Title", "a-slug", "https://a.test/x", "", gb.summary_block_html([gb.NO_SUMMARY]),
                                    gb.guide_block_html("How to apply", "", "Do it."), "S · 2026-11-26", "27 Nov 2026, 10:00")
    assert 'rel="canonical" href="https://agent-builder-daily.vercel.app/breakdowns/a-slug.html"' in page
    assert tag_value(page, TAGS[0]).startswith("Title: ")  # a stock note is not the description: each page has its own
    assert gb.restyle_page(page) == page and "a-slug" in gb.restyle_page(page.replace("a-slug", "b-slug"), slug="a-slug")


# ---------- 4. Privacy (UK GDPR) ----------

PRIVACY_ITEMS = {
    "controller": "Axiom Tech LTD, trading as Optomate",
    "contact": "mailto:admin@optomate.co.uk",
    "lawful basis": "lawful basis under the UK GDPR is your consent",
    "retention": "until you unsubscribe. Then we delete it within 7 days",
    "processor": "Google is our data processor",
    "transfer": "Google may process data outside the UK",
    "transfer safeguards": "UK International Data Transfer Addendum",
    "portability": "to receive it in a portable form (data portability)",
    "object": "Right to object:",
    "complaint": "https://ico.org.uk",
    "consent record": "record of your consent",
}


def test_privacy_page_has_every_uk_gdpr_item():
    page = " ".join(PRIVACY.read_text(encoding="utf-8").split())
    missing = [name for name, text in PRIVACY_ITEMS.items() if text not in page]
    assert missing == []
    rights = page.split("Your rights</h2>")[1].split("</section>")[0]
    for right in ("access", "correct", "delete", "restrict", "object", "withdraw your consent"):
        assert right in rights, right
    assert "Information Commissioner's Office (ICO)" in page
    # The company address was taken off the page on 27 Nov 2026 (the owner's decision): no placeholder may be live
    assert "AHMED TO FILL" not in page and "Address:" not in page


# ---------- 5. Consent ----------

def test_signup_form_has_an_unticked_required_consent_box():
    for n in ("", "-2"):
        form = gh.generate_email_capture(n)
        box = re.search(r"<input type=\"checkbox\"[^>]*>", form)[0]
        assert f'id="consent{n}"' in box and " required" in box and "checked" not in box
        assert f'<label class="consent" for="consent{n}">' in form
        assert "<span>Send me the Daily Edge AI email. I can unsubscribe at any time.</span>" in form
        # the order on screen and for the Tab key: email, consent, Subscribe
        assert form.index('type="email"') < form.index('type="checkbox"') < form.index('type="submit"')
        assert form.index("<form") < form.index('type="checkbox"') < form.index("</form>")


def test_home_page_blocks_a_submit_with_no_consent():
    home = HOME.read_text(encoding="utf-8")
    assert home.count('type="checkbox"') == 2 and home.count(" required />") == 4 and "novalidate" not in home
    assert "if (agreed(e, 'consent')) submitEmail(e)" in home and "if (!agreed(e, 'consent-2')) return;" in home
    assert "document.getElementById(id).checked || (e.preventDefault()" in home


def test_apps_script_note_stores_the_time_and_the_same_consent_text():
    note = (ROOT / "apps-script" / "README.md").read_text(encoding="utf-8")
    assert gh.CONSENT_TEXT in note and "new Date().toISOString()" in note and "appendRow([email," in note
    assert "'Success! Email captured: ' + email" in note  # the reply site/js/subscribe.js waits for


# ---------- 6. Accessibility ----------

def luminance(hex_colour):
    rgb = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    r, g, b = (v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


TOKEN = dict(re.findall(r"--(c-[\w-]+):\s*(#[0-9a-fA-F]{6})", gh.TOKENS_CSS))
TEXT_PAIRS = [  # (text token, background token): every pair the pages use
    ("c-text", "c-bg"), ("c-text", "c-bg-alt"), ("c-text-soft", "c-bg"), ("c-text-soft", "c-bg-alt"), ("c-text-muted", "c-bg"),
    ("c-bg", "c-surface-dark"), ("c-bg", "c-surface-dark-2"), ("c-bg", "c-hero-bottom"),
    ("c-on-dark-muted", "c-surface-dark"), ("c-on-dark-muted", "c-surface-dark-2"), ("c-on-dark-muted", "c-hero-bottom"),
    ("c-on-accent", "c-accent"), ("c-on-accent", "c-accent-hover"),
]


@pytest.mark.parametrize("text, background", TEXT_PAIRS)
def test_text_contrast_is_4_5_to_1_or_more(text, background):
    assert contrast(TOKEN[text], TOKEN[background]) >= 4.5, (text, background, round(contrast(TOKEN[text], TOKEN[background]), 2))


def test_muted_text_is_never_on_the_grey_background():
    assert contrast(TOKEN["c-text-muted"], TOKEN["c-bg-alt"]) < 4.5  # the reason for the rule
    for path in (HOME, ARCHIVE, PRIVACY, BREAKDOWNS[0]):
        css = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8").split("<style>")[1].split("</style>")[0], flags=re.S)
        for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
            if "var(--c-bg-alt)" in body:
                assert "var(--c-text-muted)" not in body, (path.name, selector.strip())


@pytest.mark.parametrize("path", [HOME, ARCHIVE, PRIVACY, BREAKDOWNS[0], BREAKDOWNS[-1]], ids=lambda p: p.name[:30])
def test_page_has_a_skip_link_a_main_target_and_a_focus_ring(path):
    html = path.read_text(encoding="utf-8")
    body = html.split("<body>")[1]
    first_link = re.search(r"<a [^>]*>", body)[0]
    assert "skip-link" in first_link and 'href="#main"' in first_link  # the first Tab stop
    assert html.count('id="main"') == 1 and re.search(r'<main class="content" id="main">', html)
    css = html.split("<style>")[1].split("</style>")[0]
    assert gh.SKIP_CSS in css and ".skip-link:focus { transform: none;" in css
    assert ":focus-visible { outline: 2px solid var(--c-text); outline-offset: 2px; }" in css
    assert "outline: none" not in css.replace(".card-link:focus-visible { outline: none; }", "")  # the card ring is on ::after
    assert "tabindex" not in body  # the Tab order is the order of the page


# ---------- 7. Speed ----------

def test_fonts_do_not_block_the_first_paint():
    for path, prefix in ((HOME, ""), (ARCHIVE, ""), (PRIVACY, ""), (BREAKDOWNS[0], "../")):
        html = path.read_text(encoding="utf-8")
        assert 'rel="stylesheet"' not in html.split("</head>")[0], path.name
        assert html.rstrip().endswith(gh.font_links(prefix) + "\n</body>\n</html>"), path.name
        assert "font-display: swap" in html


def test_lead_image_loads_first_and_the_others_stay_lazy():
    cards = "".join(f'<article><img src="images/{n}.webp" loading="lazy" width="1200" height="675" alt=""></article>' for n in "abc")
    lead = gh.eager_lead(cards)
    assert lead.count('loading="lazy"') == 2 and lead.index('fetchpriority="high"') < lead.index('loading="lazy"')
    home = HOME.read_text(encoding="utf-8")
    images = re.findall(r"<img [^>]*class=\"brand-logo\"[^>]*>|<img [^>]*>", home.split('<main')[1])
    assert 'fetchpriority="high"' in images[0] and "loading=" not in images[0]
    assert all('loading="lazy"' in i for i in images[1:])
    assert all(re.search(r' width="\d+"', i) and re.search(r' height="\d+"', i) for i in images)  # no layout shift


# ---------- 8. Phone widths ----------

def test_phone_layout_rules():
    home = HOME.read_text(encoding="utf-8")
    assert '<meta name="viewport" content="width=device-width, initial-scale=1.0">' in home
    assert re.search(r"body \{[^}]*overflow-wrap: anywhere", home)
    assert re.search(r"\.email-form, \.email-form-2 \{[^}]*flex-wrap: wrap", home)
    assert re.search(r"\.email-input \{\s*flex: 1 1 100%;\s*min-width: 0;", home)
    assert re.search(r"\.consent \{[^}]*min-height: var\(--tap\)", home)
    slug_map = json.loads((SITE / "breakdowns" / "slug_map.json").read_text())
    assert slug_map and all((SITE / "breakdowns" / f"{slug}.html").is_file() for group in slug_map.values() for slug in group.values())


# ---------- 9. The page judge: one call for all the pages of a run ----------

RAW = ("The game made $3 billion.\n\nOur take: it could change little.\n---SPLIT---\n"
       "1. EVALUATE - List your suppliers.\n5. QUICK WIN - Review contracts in under 30 days.")


def story(title):
    return {"title": title, "source": "Press", "pub_date": "26 Nov 2026", "url": f"https://a.test/{title}", "relevance_score": 5,
            "source_text": "The game made $3 billion on release day.", "summary": "The game made $3 billion.", "enterprise_use": "Nothing to do."}


def test_all_pages_of_a_run_go_to_the_judge_in_one_call(tmp_path, monkeypatch):
    import judge
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(gb.time, "sleep", lambda s: None)
    monkeypatch.setattr(gb, "HELD_BACK", [])
    monkeypatch.setattr(gb, "generate_content_for_news", lambda item, feedback="": RAW)
    calls = []

    def fake(items):  # verdict by title: "pass", "fail", and no verdict for the third
        calls.append([it["title"] for it in items])
        answers = {"good": {"verdict": "pass", "score": 0.9, "reason": "ok"}, "bad": {"verdict": "fail", "score": 0.1, "reason": "invented"},
                   "lost": {"verdict": "error", "score": 0.0, "reason": "quota"}}
        return {it["id"]: answers[it["title"]] for it in items}
    monkeypatch.setattr(judge, "judge_batch", fake)
    gb.process_items([story(t) for t in ("good", "bad", "lost")], "news", "27 Nov 2026, 10:00", tmp_path)
    assert calls == [["good", "bad", "lost"]]  # one batch, not one call for each page
    good, bad, lost = ((tmp_path / f"{t}.html").read_text() for t in ("good", "bad", "lost"))
    assert "List your suppliers." in good and not gb.is_short_page(tmp_path / "good.html")
    assert "List your suppliers." not in bad and gb.HELD.strip() in bad      # failed: held back, not tried again
    assert "List your suppliers." not in lost and gb.is_short_page(tmp_path / "lost.html")  # no verdict: tried again later
    assert [t for t, _ in gb.HELD_BACK] == ["bad"]


def test_no_gemini_key_skips_the_judge(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert gb.judge_pages([(story("a"), "S.", "G."), (story("b"), "S.", "G.")]) == [("pass", "judge skipped: no GEMINI_API_KEY")] * 2
    assert gb.judge_pages([]) == []
