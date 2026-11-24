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
    assert '<button type="button" class="md-export-btn"' in html and "onclick" not in html


def test_news_card_has_one_source_link_in_the_title():
    html = gh.render_news({"news": [{"title": "Story", "url": "https://example.test/1", "relevance_signal": "high",
                                     "relevance_score": 8}]}, {"news": {}})
    assert "window.open" not in html and len(re.findall(r'href="https://example.test/1"', html)) == 1
    assert "High relevance" in html  # relevance in words, not colour alone


def test_news_card_has_one_meta_line_with_the_source():
    html = gh.render_news({"news": [{"title": "Story", "url": "https://example.test/1", "source": "Ars Technica",
                                     "pub_date": "2026-11-24"}]}, {"news": {}})
    assert html.count('class="card-meta"') == 1
    assert ('<div class="card-meta"><span>Ars Technica</span><span class="sep" aria-hidden="true">•</span>'
            '<span>2026-11-24</span></div>') in html
    assert html.count("Ars Technica") == 1 and html.count("2026-11-24") == 1  # no duplicate source or date span


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
                   '<button type="submit" class="email-btn">', '<span class="accent-word">'):
        assert needle in page, needle
    css = page.split("<style>")[1].split("</style>")[0]
    assert "infinite" not in css
    assert not re.search(r"background-clip\s*:\s*text", css, re.I)  # -webkit- prefix included, any spacing
    # no gradient of any type except the hero background (--g-hero, used by .hero) and the score chart (no text on it)
    grad = re.compile(r"(?:repeating-)?(?:linear|radial|conic)-gradient\s*\(|-webkit-gradient\s*\(", re.I)
    allowed = {":root": "--g-hero:", ".hero": "background: var(--g-hero)", ".score-ring": "conic-gradient("}
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", re.sub(r"/\*.*?\*/", "", css, flags=re.S)):
        for decl in body.split(";"):
            if grad.search(decl) or "--g-hero" in decl:
                assert allowed.get(sel.strip(), "\0") in decl, (sel.strip(), decl.strip())
    assert re.search(r"\.accent-word\s*\{\s*color:\s*var\(--c-accent\);\s*\}", css)
    assert not [px for px in re.findall(r"font-size:\s*(\d+)px", css) if int(px) < 12]
    assert not re.search(r"border-left:\s*[2-9]px", css)
    assert not re.search(r"[\U0001F300-\U0001FAFF☀-➿]", page)  # no emoji as icons


def test_lead_story_grid(site):
    gh.generate_html()
    css = (site / "site" / "index.html").read_text().split("<style>")[1].split("</style>")[0]
    mid = re.search(r"@media \(min-width: 641px\) \{(.*?)\n    \}", css, re.S).group(1)
    wide = re.search(r"@media \(min-width: 1025px\) \{(.*?)\n    \}", css, re.S).group(1)
    assert re.search(r"\.cards-grid\s*\{\s*grid-template-columns:\s*repeat\(2,", mid)
    assert re.search(r"\.cards-grid > \.card:first-child\s*\{\s*grid-column:\s*span 2;", mid)
    assert re.search(r"\.cards-grid\s*\{\s*grid-template-columns:\s*repeat\(4,", wide)
    assert re.search(r"\.cards-grid > \.card:first-child\s*\{\s*grid-row:\s*span 2;", wide)
    assert re.search(r"\.cards-grid > \.card:first-child \.card-title\s*\{\s*font-size:\s*var\(--fs-h3\);", wide)


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


# ---------- F3: dashboard XSS, feed.xml, archive merge, raw-title slugs, S-labels, https only ----------

DASHBOARD = ROOT / "site" / "dashboard.html"
DASH_HARNESS = r"""
const vm = require('vm'), fs = require('fs');
const script = fs.readFileSync(process.argv[2], 'utf8').split('<script>')[1].split('</script>')[0];
const H = process.argv[3], els = {}, errors = [];
const el = id => els[id] || (els[id] = { id, innerHTML: '', textContent: '', style: {}, addEventListener() {} });
const paper = Object.assign({ title: H, relevance_tag: H, hallucination_detail: H, total_score: 55, eval_verdict: 'fail',
  gemini_eval_status: 'fail' }, JSON.parse(process.argv[4] || '{}'));
const day = { date: '2026-10-27', total: 1, case_studies: [paper] };
const body = url => url.includes('case_studies_archive') ? [day] : url.includes('news_archive') ? [] :
  url.includes('groq_token_stats') ? { generated_at: 'x', key_1: {}, key_2: {} } : {};
const ctx = { document: { getElementById: el }, console: { error: (...a) => errors.push(a.map(String).join(' ')), log() {} },
  fetch: async url => ({ ok: true, json: async () => body(url) }), setTimeout() {} };
vm.createContext(ctx);
vm.runInContext(script, ctx);
setTimeout(() => console.log(JSON.stringify({ errors,
  html: Object.fromEntries(Object.entries(els).map(([k, v]) => [k, String(v.innerHTML)])),
  text: Object.fromEntries(Object.entries(els).map(([k, v]) => [k, String(v.textContent)])) })), 100);
"""


def run_node(tmp_path, harness, *args):
    import shutil, subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    path = tmp_path / "harness.js"
    path.write_text(harness)
    out = subprocess.run([node, str(path), *map(str, args)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.fixture
def dashboard_run(tmp_path):
    return run_node(tmp_path, DASH_HARNESS, DASHBOARD, HOSTILE)


def test_dashboard_escapes_injected_titles(dashboard_run):
    html = dashboard_run["html"]
    rendered = "".join(html.values())
    assert "<script>" not in rendered and "<img" not in rendered
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html["qc-fail-list"]  # title and hallucination detail
    assert "&lt;script&gt;" in html["pipeline-lower"]  # top-paper title and tag


def test_dashboard_render_pipeline_has_no_error(dashboard_run):
    assert dashboard_run["errors"] == []
    assert "Score Distribution" in dashboard_run["html"]["pipeline-lower"]


def test_dashboard_ids_exist_and_no_inline_handlers():
    page = DASHBOARD.read_text()
    for el_id in set(re.findall(r"getElementById\('([\w-]+)'\)", page)):
        assert f'id="{el_id}"' in page, el_id
    assert not re.search(r"\son[a-z]+=", page)


def test_subscribe_csp_allows_apps_script():
    csp = json.loads((ROOT / "site" / "vercel.json").read_text())["headers"][0]["headers"][0]["value"]
    connect = next(d for d in csp.split(";") if d.strip().startswith("connect-src"))
    assert "https://script.google.com" in connect and "https://script.googleusercontent.com" in connect


def test_feed_escapes_every_field_and_links_https_only(tmp_path, monkeypatch):
    import xml.etree.ElementTree as ET
    monkeypatch.chdir(tmp_path)
    gh.generate_rss({"case_studies": [study(title=HOSTILE, url="javascript:alert(1)", summary=HOSTILE, it_breakdown=HOSTILE),
                                      study(title="Good & fine", url="https://a.test/?a=1&b=2")]},
                    {"news": [{"title": HOSTILE, "url": 'https://n.test/"><script>x</script>', "summary": HOSTILE},
                              {"title": "Plain", "url": "http://insecure.test"}]})
    raw = (tmp_path / "site" / "feed.xml").read_text()
    assert "<script" not in raw and "javascript:" not in raw and "<img" not in raw and "CDATA" not in raw
    items = ET.fromstring(raw).find("channel").findall("item")
    links = [i.findtext("link") for i in items]
    assert links == [None, "https://a.test/?a=1&b=2", 'https://n.test/"><script>x</script>', None]
    assert [i.findtext("guid") for i in items] == links
    assert items[0].findtext("title") == HOSTILE  # a feed reader shows the text, never runs it
    desc = items[0].findtext("description")  # HTML for the reader: the hostile text inside it is still escaped
    assert desc.startswith("<p>&lt;script&gt;alert(1)") and "<script" not in desc


def test_safe_url_allows_https_only():
    assert gh.safe_url("https://a.test/x") == "https://a.test/x"
    for bad in ("http://a.test", "javascript:alert(1)", " JavaScript:x", "data:text/html,x", "//a.test", "", None):
        assert gh.safe_url(bad) == "#", bad
    assert 'href="#"' in cards(study(url="http://a.test"))


def test_breakdown_links_use_the_raw_title():
    title = 'AT&T’s “AI” <plan>'
    assert 'href="breakdowns/att.html"' in gh.render_case_studies({"case_studies": [study(title=title)]},
                                                                  {"case_studies": {title: "att"}})
    assert 'href="breakdowns/att.html"' in gh.render_news({"news": [{"title": title, "url": "https://n.test"}]},
                                                          {"news": {title: "att"}})


def test_news_signal_points_replace_s_labels():
    news = lambda text: gh.render_news({"news": [{"title": "N", "url": "https://n.test", "enterprise_use": text}]}, {"news": {}})
    html = news("S1: Patch <b>X</b>. S2: Breach risk. S3: Okta, Intune.")
    assert ('<ul class="signal-points"><li><strong>Do this week:</strong> Patch &lt;b&gt;X&lt;/b&gt;.</li>'
            '<li><strong>Risk if ignored:</strong> Breach risk.</li><li><strong>Tools affected:</strong> Okta, Intune.</li></ul>') in html
    assert not re.search(r"S[1-3]\s*:", html)
    plain = news("Watch the rollout. S2: dangling label")
    assert "How to use:" in plain and not re.search(r"S[1-3]\s*:", plain) and "signal-points" not in plain


def test_archived_file_merges_into_an_existing_day(site):
    paper_a = study(title="Paper A", url="https://a.test/A", pub_date="2026-05-06")
    video = study(title="From Vibe Coding to Agentic Engineering", url="https://youtube.test/v", pub_date="2026-05-06",
                  source_type="youtube_agent", is_evergreen=True, total_score=90)
    archive = [{"date": "2026-10-27", "total": 1, "case_studies": [study(title="Fresh")]},
               {"date": "2026-05-06", "total": 1, "case_studies": [paper_a]}]
    (site / "case_studies" / "archive.json").write_text(json.dumps(archive))
    (site / "case_studies" / "2026-10-27.json").write_text(json.dumps({"case_studies": [study(title="Fresh")]}))
    (site / "case_studies" / "v_20260506_7xTGNNLPyMI.json").write_text(json.dumps({"case_studies": [video, paper_a]}))
    gh.generate_html()
    days = json.loads((site / "case_studies" / "archive.json").read_text())
    may = next(d for d in days if d["date"] == "2026-05-06")
    assert [c["title"] for c in may["case_studies"]] == ["Paper A", "From Vibe Coding to Agentic Engineering"]
    assert may["total"] == 2 and [d["date"] for d in days] == ["2026-10-27", "2026-05-06"]
    assert not (site / "case_studies" / "v_20260506_7xTGNNLPyMI.json").exists()


def test_files_are_kept_when_the_archive_cannot_be_written(site, monkeypatch):
    (site / "case_studies" / "v_20260506_x.json").write_text(json.dumps({"case_studies": [study(pub_date="2026-05-06")]}))
    (site / "data" / "news" / "news_2026-10-20.json").write_text(json.dumps({"news": [{"title": "Old", "url": "https://o.test"}]}))
    (site / "data" / "news" / "news_2026-10-27.json").write_text(json.dumps({"news": []}))
    monkeypatch.setattr(gh, "ARCHIVE_CS_FILE", str(site / "missing-dir" / "archive.json"))
    with pytest.raises(OSError):
        gh.generate_html()
    assert (site / "case_studies" / "v_20260506_x.json").exists()
    monkeypatch.setattr(gh, "ARCHIVE_CS_FILE", "case_studies/archive.json")
    (site / "case_studies" / "archive.json").write_text("[]")
    monkeypatch.setattr(gh, "ARCHIVE_NEWS_FILE", str(site / "missing-dir" / "news_archive.json"))
    with pytest.raises(OSError):
        gh.generate_html()
    assert (site / "data" / "news" / "news_2026-10-20.json").exists()


def test_newer_day_stays_first_when_an_older_day_is_archived_after_it(site):
    (site / "case_studies" / "archive.json").write_text(json.dumps([{"date": "2026-10-26", "total": 1, "case_studies": [study()]}]))
    (site / "case_studies" / "2026-10-24.json").write_text(json.dumps({"case_studies": [study(title="Older paper")]}))
    (site / "case_studies" / "2026-10-27.json").write_text(json.dumps({"case_studies": [study(title="Today")]}))
    (site / "data" / "news_archive.json").write_text(json.dumps([{"date": "2026-10-26", "total": 1, "news": [{"title": "N26"}]}]))
    (site / "data" / "news" / "news_2026-10-24.json").write_text(json.dumps({"news": [{"title": "N24", "url": "https://n.test/24"}]}))
    (site / "data" / "news" / "news_2026-10-27.json").write_text(json.dumps({"news": [{"title": "N27", "url": "https://n.test/27"}]}))
    gh.generate_html()
    assert [d["date"] for d in json.loads((site / "case_studies" / "archive.json").read_text())] == ["2026-10-26", "2026-10-24"]
    assert [d["date"] for d in json.loads((site / "data" / "news_archive.json").read_text())] == ["2026-10-26", "2026-10-24"]


def _ratio(a, b):
    def lum(h):
        c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_home_colour_tokens_meet_4_5_to_1(site):
    (site / "case_studies" / "archive.json").write_text("[]")
    gh.generate_html()
    root = (site / "site" / "index.html").read_text().split(":root {")[1].split("}")[0]
    tokens = dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", root))
    assert tokens["c-accent"] == "#22d3ee"
    # every text colour of a zone on every background of that zone
    zones = {"white panel": (("c-text", "c-text-muted", "c-good", "c-warn", "c-bad"), ("c-bg",)),
             "dark hero": (("c-bg", "c-on-dark-muted", "c-accent"), ("c-surface-dark", "c-surface-dark-2", "c-hero-bottom")),
             "tag pills": (("c-text",), ("c-bg-alt",))}
    pairs = [(fg, bg) for fgs, bgs in zones.values() for fg in fgs for bg in bgs]
    for fg, bg in pairs:
        assert _ratio(tokens[fg], tokens[bg]) >= 4.5, (fg, bg, round(_ratio(tokens[fg], tokens[bg]), 2))
    # muted text is not in the tag-pills zone (4.35:1 on c-bg-alt), so no muted-text rule may hit a c-bg-alt element
    style = re.sub(r"/\*.*?\*/", "", (site / "site" / "index.html").read_text().split("<style>")[1].split("</style>")[0], flags=re.S)
    rules = [(sel, body) for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", style) if not sel.strip().startswith(":root")]
    def uses(body, prop, tok):
        return re.search(rf"(?<![\w-]){prop}\s*:[^;]*(var\(--{tok}\)|{tokens[tok]})", body, re.I)
    on_alt = {c for sel, body in rules if uses(body, "background(?:-color)?", "c-bg-alt") for c in re.findall(r"\.([\w-]+)", sel)}
    for sel, body in rules:
        if uses(body, "color", "c-text-muted"):
            assert not on_alt & set(re.findall(r"\.([\w-]+)", sel)), (sel.strip(), sorted(on_alt))
    # status badges: each colour on its own tint, rounded to 8-bit as the screen shows it
    css = (site / "site" / "index.html").read_text()
    pct = int(re.search(r"\.status-badge \{ background: color-mix\(in srgb, currentColor (\d+)%, var\(--c-bg\)\)", css).group(1))
    for fg in ("c-good", "c-warn", "c-bad"):
        c, w = tokens[fg], tokens["c-bg"]
        tint = "#" + "".join("%02x" % round(int(c[i:i + 2], 16) * pct / 100 + int(w[i:i + 2], 16) * (1 - pct / 100)) for i in (1, 3, 5))
        assert _ratio(c, tint) >= 4.5, (fg, tint, round(_ratio(c, tint), 3))


def test_hostile_build_has_no_injected_markup(site):
    """Every data file seeded with hostile text and URLs: no raw tag, handler or javascript: reaches the outputs."""
    bad = dict(title=HOSTILE, url="javascript:alert(1)", summary=HOSTILE, it_breakdown=HOSTILE, score_reasoning=HOSTILE,
               relevance_tag=HOSTILE, institution=HOSTILE, authors=[HOSTILE], pub_date="2026-10-27")
    (site / "case_studies" / "archive.json").write_text(json.dumps([{"date": "2026-10-26", "total": 1, "case_studies": [study(**bad)]}]))
    (site / "case_studies" / "2026-10-27.json").write_text(json.dumps({"case_studies": [study(**bad)]}))
    news = {"title": HOSTILE, "url": "javascript:alert(1)", "summary": HOSTILE, "enterprise_use": f"S1: {HOSTILE} S2: x S3: y",
            "source": HOSTILE, "category": HOSTILE, "tier": HOSTILE, "relevance_signal": HOSTILE, "relevance_score": HOSTILE,
            "pub_date": HOSTILE}
    (site / "data" / "news" / "news_2026-10-27.json").write_text(json.dumps({"news": [news]}))
    (site / "data" / "news_archive.json").write_text(json.dumps([{"date": HOSTILE, "total": 1, "news": [news]}]))
    (site / "data" / "daily_hook.txt").write_text(HOSTILE)
    gh.generate_html()
    page = (site / "site" / "index.html").read_text()
    feed = (site / "site" / "feed.xml").read_text()
    tags, handlers = parsed(page)
    assert tags.count("script") == 2 and "img" not in tags and handlers == []  # the page's own two scripts only
    assert "javascript:" not in page.lower() and "javascript:" not in feed.lower() and "<script" not in feed and "<img" not in feed


def parsed(page):
    """Real tags and on*= attribute names, as a browser parser sees them (escaped text inside attributes is not one)."""
    from html.parser import HTMLParser
    tags, handlers = [], []

    class P(HTMLParser):
        def handle_starttag(self, tag, attrs):
            tags.append(tag)
            handlers.extend(f"{tag}[{k}]" for k, _ in attrs if k.startswith("on"))
    P().feed(page)
    return tags, handlers


# ---------- F3b: subscribe shows success only on a confirmed reply; dashboard QC counts ----------

SUBSCRIBE_JS = ROOT / "site" / "js" / "subscribe.js"
SUB_HARNESS = r"""
const vm = require('vm'), fs = require('fs');
const [file, mode] = process.argv.slice(2);
const node = () => ({ textContent: '', disabled: false, value: '' });
const input = Object.assign(node(), { value: ' reader@example.test ' }), btn = node(), confirm = node();
const container = { querySelector: s => s === '.email-input' ? input : btn };
const timers = [], calls = [];
let release;
const reply = { ok: { ok: true, status: 200, text: async () => 'Success! Email captured: reader@example.test' },
  http500: { ok: false, status: 500, text: async () => 'Success! (from an error page)' },
  unconfirmed: { ok: true, status: 200, text: async () => 'Error: script failed' } };
const ctx = {
  document: { querySelector: () => container, getElementById: () => confirm },
  AbortController, setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length; }, clearTimeout() {},
  fetch: (url, opts) => {
    calls.push({ url, method: opts.method, type: opts.headers && opts.headers['Content-Type'], body: opts.body, mode: opts.mode || null });
    if (mode === 'network') return Promise.reject(new TypeError('Failed to fetch'));
    return new Promise((resolve, reject) => {
      if (mode === 'timeout') opts.signal.addEventListener('abort', () => reject(new Error('AbortError')));
      else release = () => resolve(reply[mode]);
    });
  },
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(file, 'utf8'), ctx);
const tick = () => new Promise(r => setImmediate(r));
(async () => {
  const done = Promise.resolve(vm.runInContext('submitEmail({ preventDefault() {} })', ctx));
  await tick();
  const before = { btn: btn.textContent, confirm: confirm.textContent };  // the reply has not arrived yet
  if (mode === 'timeout') timers.filter(t => t.ms === 10000).forEach(t => t.fn());
  else if (release) release();
  for (let i = 0; i < 10; i++) await tick();
  await done;
  console.log(JSON.stringify({ before, btn: btn.textContent, confirm: confirm.textContent, disabled: btn.disabled,
    inputValue: input.value, calls, timeouts: timers.map(t => t.ms) }));
})();
"""


def subscribe(tmp_path, mode):
    return run_node(tmp_path, SUB_HARNESS, SUBSCRIBE_JS, mode)


def test_subscribe_shows_success_only_after_a_confirmed_reply(tmp_path):
    r = subscribe(tmp_path, "ok")
    assert r["before"] == {"btn": "Submitting…", "confirm": ""}  # nothing claims success while the request is out
    assert r["btn"] == "Subscribed" and r["confirm"] == "Subscribed" and r["inputValue"] == ""
    [call] = r["calls"]
    assert call["method"] == "POST" and call["type"].startswith("text/plain") and call["mode"] is None  # readable, not no-cors
    assert json.loads(call["body"]) == {"email": "reader@example.test"} and "@" not in call["url"]  # email not in the URL


@pytest.mark.parametrize("mode", ["network", "http500", "unconfirmed", "timeout"])
def test_subscribe_errors_and_timeout_show_the_error(tmp_path, mode):
    r = subscribe(tmp_path, mode)
    assert r["confirm"] == "Something went wrong, please try again"
    assert r["btn"] == "Subscribe" and r["disabled"] is False and "Subscribed" not in r["btn"] + r["confirm"]
    assert 10000 in r["timeouts"]


def test_dashboard_counts_a_passed_item_as_passed(tmp_path):
    r = run_node(tmp_path, DASH_HARNESS, DASHBOARD, "Paper", json.dumps({"eval_verdict": "pass", "gemini_eval_status": "pending"}))
    assert (r["text"]["qc-pass-count"], r["text"]["qc-fail-count"], r["text"]["qc-pending-count"]) == ("1", "0", "0")
