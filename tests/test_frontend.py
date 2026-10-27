"""F2: front-end faults from the F1 audit. Status badge, card click, score cap, the 4-month window, escaping of
AI-written text, page structure and the two retired pages. No network."""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import generate_breakdowns as gb
import generate_html as gh

HOSTILE = '<script>alert(1)</script><img src=x onerror=alert(2)>'


def study(**kw):
    return {"title": "Paper", "url": "https://arxiv.org/abs/1", "pub_date": "2026-10-25", "authors": ["A. Author"],
            "summary": "S.", "it_breakdown": "Use it.", "total_score": 55, "real_world_impact": 20, "ai_maturity": 20,
            "monetisation_potential": 15, "score_reasoning": "R.", "eval_verdict": "pass",
            "gemini_eval_status": "pending", **kw}


def cards(*items):
    return gh.render_case_studies({"case_studies": list(items)}, {"case_studies": {}})


# ---------- status badge: eval_verdict first, never "Evaluating" for a passed item ----------

@pytest.mark.parametrize("verdict, gemini, shown", [
    ("pass", "pending", "Verified"), ("pass", None, "Verified"), (None, "pass", "Verified"),
    ("fail", "pending", "Flagged"), ("pending", "pending", "Evaluating"), (None, None, "Evaluating")])
def test_badge_reads_eval_verdict_first(verdict, gemini, shown):
    html = cards(study(eval_verdict=verdict, gemini_eval_status=gemini))
    assert f">{shown}</span>" in html
    if "pass" in (verdict, gemini):
        assert "Evaluating" not in html


# ---------- card click: only the title link opens the source ----------

def test_only_the_title_link_opens_the_source():
    html = cards(study(source_type="youtube_agent"))
    assert "onclick=\"window.open" not in html and "window.open" not in html
    assert not re.search(r"<article[^>]*onclick", html)
    links = re.findall(r'<a [^>]*href="https://arxiv.org/abs/1"', html)
    assert len(links) == 1
    assert re.search(r'<h2 class="card-title"><a href="https://arxiv.org/abs/1" target="_blank" rel="noopener noreferrer">', html)
    # the reasoning toggle and the copy button are plain controls, not inside a link
    assert '<summary class="trace-toggle">View Technical Reasoning</summary>' in html
    assert '<button type="button" class="md-export-btn" onclick="copyMarkdown(this)"' in html


def test_news_card_has_one_source_link_in_the_title():
    html = gh.render_news({"news": [{"title": "Story", "url": "https://example.test/1", "relevance_signal": "high",
                                     "relevance_score": 8}]}, {"news": {}})
    assert "window.open" not in html and len(re.findall(r'href="https://example.test/1"', html)) == 1
    assert "High relevance" in html  # relevance in words, not colour alone


# ---------- score ring: out of 75, no ring above 75 ----------

def test_score_ring_caps_at_75():
    assert "--pct:100;" in gh.score_ring(75) and 'aria-label="Scout score 75 of 75"' in gh.score_ring(75)
    assert gh.score_ring(0) and "--pct:0;" in gh.score_ring(0)
    for legacy in (76, 90, 100, -1, None, "90"):
        assert gh.score_ring(legacy) == ""
    assert "score-ring" not in cards(study(total_score=90))
    assert "score-ring" in cards(study(total_score=55))


# ---------- 4-month window applies to every item: evergreen no longer exempt ----------

class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 27, 9, 0)

    @classmethod
    def utcnow(cls):
        return cls(2026, 10, 27, 9, 0)


@pytest.fixture
def site(tmp_path, monkeypatch):
    (tmp_path / "case_studies").mkdir()
    (tmp_path / "data" / "news").mkdir(parents=True)
    (tmp_path / "data" / "news_archive.json").write_text("[]")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(gh, "datetime", Clock)
    return tmp_path


def test_old_evergreen_video_is_archived_and_gone_from_the_page(site):
    today = [{"date": "2026-10-27", "total": 1, "case_studies": [study(title="Fresh paper")]}]
    (site / "case_studies" / "archive.json").write_text(json.dumps(today))
    (site / "case_studies" / "2026-10-27.json").write_text(json.dumps({"case_studies": [study(title="Fresh paper")]}))
    old = study(title="From Vibe Coding to Agentic Engineering", pub_date="2026-05-06", total_score=90,
                is_evergreen=True, source_type="youtube_agent")
    (site / "case_studies" / "v_20260506_7xTGNNLPyMI.json").write_text(json.dumps({"case_studies": [old]}))
    recent_video = study(title="Recent talk", pub_date="2026-10-01", is_evergreen=True, source_type="youtube_agent")
    (site / "case_studies" / "v_20261001_abc.json").write_text(json.dumps({"case_studies": [recent_video]}))

    gh.generate_html()

    page = (site / "site" / "index.html").read_text()
    assert "From Vibe Coding" not in page.split('id="archive-cs"')[0]  # not in the live tabs
    assert "Fresh paper" in page and "Recent talk" in page  # inside the window: still live, evergreen or not
    assert not (site / "case_studies" / "v_20260506_7xTGNNLPyMI.json").exists()
    assert (site / "case_studies" / "v_20261001_abc.json").exists()
    archive = json.loads((site / "case_studies" / "archive.json").read_text())
    assert [e["date"] for e in archive] == ["2026-10-27", "2026-05-06"]  # newest stays first for the email


def test_window_drops_old_items_from_a_live_file(site):
    (site / "case_studies" / "archive.json").write_text("[]")
    (site / "case_studies" / "2026-10-27.json").write_text(json.dumps({"case_studies": [
        study(title="Brand-new paper"), study(title="Stale paper", pub_date="2026-06-01"), study(title="No date", pub_date="")]}))
    gh.generate_html()
    live = (site / "site" / "index.html").read_text().split('id="archive-cs"')[0]
    assert "Brand-new paper" in live and "No date" in live and "Stale paper" not in live


# ---------- escaping of AI-written text ----------

def test_breakdown_summary_and_guide_are_escaped():
    raw = f"SECTION_SUMMARY: Para one {HOSTILE}\n\nPara two\n---SPLIT---\nSECTION_IT_GUIDE:\n1. EVALUATE - {HOSTILE}\n3. TOOLS TO CONSIDER\nUse <b>x</b>"
    summary, guide = gb.parse_groq_response(raw)
    page = gb.render_breakdown_page("T", "t", "https://a.test", "Read", summary, guide, "meta", "now")
    for html in (summary, guide, page):
        assert "<script>" not in html and "<img" not in html and "<b>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in summary and "&lt;script&gt;" in guide
    assert summary.startswith("<p>Para one ") and '<h3 class="guide-label">Evaluate</h3>' in guide


def test_main_page_escapes_ai_fields():
    html = cards(study(summary=HOSTILE, it_breakdown=HOSTILE, score_reasoning=HOSTILE, relevance_tag=HOSTILE, pub_date=HOSTILE,
                       institution=HOSTILE))
    html += gh.render_news({"news": [{"title": "N", "url": "https://n.test", "summary": HOSTILE, "enterprise_use": HOSTILE,
                                      "source": HOSTILE, "category": HOSTILE, "relevance_score": HOSTILE}]}, {"news": {}})
    html += gh.render_archive([{"date": "d", "total": 1, "case_studies": [{"title": HOSTILE, "url": "javascript:x"}]}])
    assert "<script>" not in html and "<img" not in html and "javascript:" not in html


def test_daily_hook_is_escaped(site):
    (site / "case_studies" / "archive.json").write_text("[]")
    (site / "data" / "daily_hook.txt").write_text(HOSTILE)
    gh.generate_html()
    page = (site / "site" / "index.html").read_text()
    assert HOSTILE not in page and "&lt;script&gt;alert(1)" in page


# ---------- page structure and the F1 accessibility floor ----------

def test_page_structure(site):
    (site / "case_studies" / "archive.json").write_text("[]")
    gh.generate_html()
    page = (site / "site" / "index.html").read_text()
    for needle in ('<h1 class="brand">', "<main", "<header", 'role="tablist"', 'role="tab"', 'aria-selected="true"',
                   'role="tabpanel"', '<label class="visually-hidden" for="scout-search">',
                   '<label class="visually-hidden" for="email-input">', ":focus-visible", "prefers-reduced-motion",
                   '<button type="submit" class="email-btn">'):
        assert needle in page, needle
    css = page.split("<style>")[1].split("</style>")[0]
    assert "infinite" not in css and "background-clip: text" not in css
    assert not [px for px in re.findall(r"font-size:\s*(\d+)px", css) if int(px) < 12]
    assert not re.search(r"border-left:\s*[2-9]px", css)
    assert not re.search(r"[\U0001F300-\U0001FAFF☀-➿]", page)  # no emoji as icons


# ---------- the two retired pages redirect to the current site ----------

@pytest.mark.parametrize("name", ["archive.html", "news.html"])
def test_retired_pages_redirect(name):
    page = (ROOT / "site" / name).read_text()
    assert '<meta http-equiv="refresh" content="0; url=/">' in page and '<a href="/">' in page
    assert "background: #0f1117" in page and "Agent Builder Daily" not in page
    assert len(page) < 2000


# ---------- F2b: every breakdown field escaped, https-only source link, old pages restyled ----------

OLD_PAGE = """<!DOCTYPE html><html lang="en"><head><title>Old - IT Breakdown - Scout Daily</title><style>
:root{--accent:#6366f1;--text-muted:#64748b}.badge{font-size:10px}</style></head><body>
<div class="topbar"><a href="../index.html" class="brand-link">Scout Daily</a><a href="../index.html" class="back-btn">← Back to feed</a></div>
<div class="page-wrap">
  <div class="badge">IT Engineer Breakdown</div>
  <h1 class="page-title">Edge AI & robots</h1>
  <div class="meta-line">Ars Technica · 2026-10-01 · Generated 01 Oct 2026, 09:00</div>
  <a href="https://example.test/story?a=1&b=2" target="_blank" class="source-link">View Original Source →</a>
  <div class="section-header"><div class="section-num">1</div><div class="section-title">Detailed Summary</div></div>
  <div class="summary-body"><p>Latency (<10 ms) and throughput (>1 TB/day).</p><p>Second para.</p></div>
  <div class="divider"></div>
  <div class="section-header"><div class="section-num">2</div><div class="section-title">IT Engineer Application Guide</div></div>
  <div class="guide-grid"><div class="guide-block"><div class="guide-label">EVALUATE</div><div class="guide-text">- Step one
   - Step two</div></div>
<div class="guide-block red"><div class="guide-label">RISKS TO FLAG</div><div class="guide-text">Data residency.</div></div></div>
  <div class="generated-note">AI-generated breakdown · Scout Daily · 01 Oct 2026, 09:00</div>
</div></body></html>"""


def breakdown(title="T", url="https://a.test/x", meta="Source · 2026-10-01", summary="S.", guide="1. EVALUATE - G."):
    s, g = gb.parse_groq_response(f"{summary}\n---SPLIT---\n{guide}")
    return gb.render_breakdown_page(title, "t", url, "Read", s, g, meta, "27 Oct 2026, 09:00")


def test_breakdown_title_meta_url_summary_are_escaped():
    page = breakdown(title=HOSTILE, meta=HOSTILE, url='https://a.test/"><script>alert(3)</script>', summary=HOSTILE)
    assert "<script>" not in page and "<img" not in page
    assert page.count("&lt;script&gt;alert(1)&lt;/script&gt;") == 4  # <title>, h1, meta line, summary
    assert 'href="https://a.test/&quot;&gt;&lt;script&gt;alert(3)&lt;/script&gt;"' in page


@pytest.mark.parametrize("url", ["javascript:alert(1)", " JavaScript:alert(1)", "data:text/html,x", "http://a.test", "", None])
def test_only_https_becomes_a_source_link(url):
    page = breakdown(url=url)
    assert "source-link" not in page.split("</style>")[1] and "javascript:" not in page.lower()


def test_https_source_link_is_noopener():
    page = breakdown(url="https://a.test/x")
    assert '<a href="https://a.test/x" target="_blank" rel="noopener noreferrer" class="source-link">' in page


def test_breakdown_page_uses_f2_tokens():
    page = breakdown()
    css = page.split("<style>")[1].split("</style>")[0]
    assert "--text-muted:#8592a8" in css and "--accent:#00ff88" in css and "#6366f1" not in css
    assert not [px for px in re.findall(r"font-size:\s*(\d+)px", css) if int(px) < 12]
    assert ".brand-link,.back-btn,.source-link{display:inline-flex;align-items:center;min-height:var(--tap)" in css
    assert "--tap:44px" in css and "background-clip" not in css and not re.search(r"border-left:\s*[2-9]px", css)
    assert "<main" in page and 'class="section-num"' not in page and 'class="badge"' not in page


def test_old_page_restyled_keeps_its_text():
    new = gb.restyle_page(OLD_PAGE)
    assert new and "--text-muted:#8592a8" in new and "#6366f1" not in new and 'class="badge"' not in new
    assert '<h1 class="page-title">Edge AI &amp; robots</h1>' in new
    assert '<p class="meta-line">Ars Technica · 2026-10-01 · Generated 01 Oct 2026, 09:00</p>' in new
    assert '<p>Latency (&lt;10 ms) and throughput (&gt;1 TB/day).</p><p>Second para.</p>' in new
    assert '<h3 class="guide-label">Evaluate</h3><div class="guide-text">- Step one\n   - Step two</div>' in new
    assert '<div class="guide-block red"><h3 class="guide-label">Risks to flag</h3>' in new
    assert 'href="https://example.test/story?a=1&amp;b=2" target="_blank" rel="noopener noreferrer"' in new
    assert "AI-generated breakdown · Scout Daily · 01 Oct 2026, 09:00" in new


def test_restyle_leaves_unknown_pages_alone(tmp_path):
    (tmp_path / "odd.html").write_text("<html><body>hand-made</body></html>")
    (tmp_path / "old.html").write_text(OLD_PAGE)
    assert gb.restyle_existing_pages(tmp_path) == (1, ["odd.html"])
    assert (tmp_path / "odd.html").read_text() == "<html><body>hand-made</body></html>"


def test_every_committed_breakdown_page_has_the_new_style():
    pages = sorted((ROOT / "site" / "breakdowns").glob("*.html"))
    assert pages
    stale = [p.name for p in pages if "--text-muted:#8592a8" not in p.read_text() or "#6366f1" in p.read_text()]
    assert stale == []
