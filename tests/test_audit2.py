"""The second pre-launch audit: the meaning check, no published fallback, honest archive counts, one "How to apply"
heading, and the sitemap and robots.txt.

- Meaning check: after the number check, one batched judge call labels every sentence of the summary and the how-to
  text SUPPORTED, NOT IN SOURCE or CONTRADICTED. A contradicted sentence, or one the source does not state and that is
  not labelled "Our take:", is written again once and then held back.
- No fallback is published: a story with no source-checked summary has no card, no archive row and no page.
- Archive: each day's count is the number of links listed.
- Breakdown: one "How to apply" heading.
- SEO: site/sitemap.xml lists every live page and site/robots.txt points to it; both are written on every run.
No network: Groq and the judge are stubbed.
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import generate_breakdowns as gb  # noqa: E402
import generate_html as gh  # noqa: E402
import judge  # noqa: E402
import quality_checks as q  # noqa: E402

SITE = ROOT / "site"
NB = " "
SOURCE = ("Megaspeed, a Singapore-based data centre operator, is accused of smuggling Nvidia GPUs into China. "
          "The Attorney General has suggested Nvidia is not a target of an investigation.")


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 27, 9, 0)

    @classmethod
    def utcnow(cls):
        return cls(2026, 11, 27, 9, 0)


def story(title="Story", **kw):
    return {"title": title, "url": f"https://example.test/{gb.slugify(title)}", "source": "example.test",
            "pub_date": "2026-11-26", "summary": "Megaspeed is accused of smuggling GPUs.", "source_text": SOURCE,
            "enterprise_use": "S1: Check your GPU contracts. S2: Legal risk. S3: Your asset inventory.",
            "relevance_score": 10, "category": "Security", **kw}


@pytest.fixture
def site(tmp_path, monkeypatch):
    (tmp_path / "case_studies").mkdir()
    (tmp_path / "data" / "news").mkdir(parents=True)
    (tmp_path / "site" / "images").mkdir(parents=True)
    (tmp_path / "site" / "breakdowns").mkdir(parents=True)
    (tmp_path / "site" / "privacy.html").write_text("<html><body>privacy</body></html>")  # a page of the site, as live
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(gh, "datetime", Clock)
    return tmp_path


def write_data(site, studies=(), items=(), archive_studies=(), archive_news=()):
    (site / "case_studies" / "2026-11-27.json").write_text(json.dumps({"case_studies": list(studies)}))
    (site / "case_studies" / "archive.json").write_text(json.dumps(
        [{"date": "2026-11-20", "total": len(archive_studies), "case_studies": list(archive_studies)}] if archive_studies else []))
    (site / "data" / "news" / "news_2026-11-27.json").write_text(json.dumps({"news": list(items)}))
    (site / "data" / "news_archive.json").write_text(json.dumps(
        [{"date": "2026-11-20", "total": len(archive_news), "news": list(archive_news)}] if archive_news else []))


# ---------- 1. the meaning check ----------

def test_sentences_are_split_for_the_check():
    assert judge.sentences_of("Dr. Who met the U.S. team. They agreed.") == ["Dr. Who met the U.S. team.", "They agreed."]
    assert judge.sentences_of("1. Patch it today.\n- Watch the cost.") == ["Patch it today.", "Watch the cost."]
    assert judge.sentences_of("") == [] and judge.sentences_of(None) == []


@pytest.mark.parametrize("label, sentence, held", [
    ("CONTRADICTED", "Nvidia is the target of the investigation.", True),
    ("NOT IN SOURCE", "Nvidia will lose the China market next year.", True),
    ("NOT IN SOURCE", "Our take: Nvidia may lose the China market.", False),  # labelled as the writer's view
    ("SUPPORTED", "Megaspeed is accused of smuggling GPUs.", False),
])
def test_each_label_decides_whether_a_sentence_holds_the_story_back(label, sentence, held):
    found = judge.meaning_problems([{"label": label, "reason": "r"}], [sentence])
    assert bool(found) is held
    if held:
        assert sentence[:40] in found[0]


def test_a_contradicted_sentence_holds_the_story_back(site, monkeypatch, capsys):
    """The whole path: the number check passes, the meaning check finds a contradiction, one rewrite, then held back."""
    item = story("Megaspeed")
    page = "Nvidia is the target of the US investigation.\n---SPLIT---\n1. EVALUATE - Check your GPU contracts."
    calls = []
    monkeypatch.setattr(gb, "generate_content_for_news", lambda it, note="": calls.append(note) or page)
    monkeypatch.setattr(gb, "judge_pages", lambda pages: [("pass", "")] * len(pages))
    monkeypatch.setattr(judge, "meaning_check", lambda batch: {
        it["id"]: [{"label": "CONTRADICTED" if "Nvidia is the target" in s else "SUPPORTED", "reason": "the source says the customer is accused"}
                   for s in it["sentences"]] for it in batch})
    gb.HELD_BACK.clear()
    assert gb.page_problems(*gb.split_sections(page), item) == []  # the number check sees nothing wrong
    gb.process_items([item], "news", "27 Nov 2026, 09:00", site / "site" / "breakdowns", use_ai=True)
    held = [(t, why) for t, why in gb.HELD_BACK]
    assert len(held) == 1 and held[0][0] == "Megaspeed" and "meaning check" in held[0][1] and "contradicts the source" in held[0][1]
    assert len(calls) == 2 and calls[0] == "" and "GROUNDING RULES" in calls[1]  # written once more, then held back
    out = capsys.readouterr().out
    assert "[MEANING] failed for Megaspeed" in out
    html = (site / "site" / "breakdowns" / "megaspeed.html").read_text()
    assert "Nvidia is the target" not in html and "Megaspeed is accused of smuggling GPUs." in html  # the story's own text


def test_a_rewrite_that_passes_is_published(site, monkeypatch):
    item = story("Megaspeed")
    bad = "Nvidia is the target of the US investigation.\n---SPLIT---\n1. EVALUATE - Check contracts."
    good = "Megaspeed is accused of smuggling GPUs.\n---SPLIT---\n1. EVALUATE - Check contracts."
    answers = iter([bad, good])
    monkeypatch.setattr(gb, "generate_content_for_news", lambda it, note="": next(answers))
    monkeypatch.setattr(gb, "judge_pages", lambda pages: [("pass", "")] * len(pages))
    monkeypatch.setattr(judge, "meaning_check", lambda batch: {
        it["id"]: [{"label": "CONTRADICTED" if "Nvidia is the target" in s else "SUPPORTED", "reason": "r"} for s in it["sentences"]]
        for it in batch})
    gb.HELD_BACK.clear()
    gb.process_items([item], "news", "27 Nov 2026, 09:00", site / "site" / "breakdowns", use_ai=True)
    assert gb.HELD_BACK == []
    assert "Megaspeed is accused of smuggling GPUs." in (site / "site" / "breakdowns" / "megaspeed.html").read_text()


def test_the_meaning_check_is_one_batched_call_for_the_run(site, monkeypatch):
    items = [story(f"Story {i}") for i in range(6)]
    page = "Megaspeed is accused of smuggling GPUs.\n---SPLIT---\n1. EVALUATE - Check contracts."
    batches = []
    monkeypatch.setattr(gb, "generate_content_for_news", lambda it, note="": page)
    monkeypatch.setattr(gb, "judge_pages", lambda pages: [("pass", "")] * len(pages))
    monkeypatch.setattr(judge, "meaning_check",
                        lambda batch: batches.append(len(batch)) or {it["id"]: [{"label": "SUPPORTED", "reason": ""}] * len(it["sentences"]) for it in batch})
    gb.process_items(items, "news", "27 Nov 2026, 09:00", site / "site" / "breakdowns", use_ai=True)
    assert batches == [6, 0]  # one call for the whole run; the second has nothing to re-check


def test_a_check_that_cannot_run_holds_nothing_back(site, monkeypatch):
    """No GEMINI_API_KEY, or a model error: the number check has run, so the page is published."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert judge.meaning_check([{"id": "0", "title": "T", "source": SOURCE, "sentences": ["A sentence."]}]) == {}
    assert gb.meaning_check([(story(), "Megaspeed is accused of smuggling GPUs.", "Check contracts.")]) == [[]]


def test_a_missing_or_mis_sized_answer_is_not_a_pass(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr(judge, "_ask", lambda prompt, model: json.dumps(
        {"items": [{"id": "0", "labels": [{"label": "SUPPORTED", "reason": ""}]}]}))  # one label for two sentences
    assert judge.meaning_check([{"id": "0", "title": "T", "source": SOURCE, "sentences": ["One.", "Two."]}]) == {}


# ---------- 2. the four stories named in the audit ----------

@pytest.mark.parametrize("title, must_say, must_not_say", [
    ("the questions jensen huang and nvidia will still have to answer about megaspeed",
     ["Megaspeed", "not a target"], ["Nvidia faces a U.S. investigation"]),
    ("z.ai's glm-5.4 aims to match models up to four times its size by running 28 layers twice",
     ["match models up to four times its size"], ["four times the performance"]),
    ("towerbridge: bridging the dual-encoder modality gap via weight-space programming",
     ["16.8 RSUM"], ["up to 17 points", "provided script"]),
    ("uk warns ai-driven cyberattacks are exposing years of weak security",
     ["National Cyber Security Centre"], ["National Cybersecurity Center"]),
])
def test_the_audit_stories_match_their_sources(title, must_say, must_not_say):
    items = [i for i in all_stories() if str(i.get("title", "")).strip().lower() == title]
    assert items, title
    for item in items:
        text = " ".join(str(item.get(f) or "") for f in ("summary", "it_breakdown", "enterprise_use"))
        for phrase in must_say:
            assert phrase in text, (title, phrase)
        for phrase in must_not_say:
            assert phrase not in text, (title, phrase)
        assert gb.publishable(item), title  # and it passes the source checks, so it is published again


def all_stories():
    out = []
    for path, key in [*((p, "case_studies") for p in (ROOT / "case_studies").glob("*.json")),
                      *((p, "news") for p in [*(ROOT / "data" / "news").glob("*.json"), ROOT / "data" / "news_archive.json"])]:
        data = gh.load_json(str(path))
        for day in (data if isinstance(data, list) else [data]):
            out += (day.get(key) or []) if isinstance(day, dict) else []
    return out


# ---------- 3. no fallback is published ----------

def test_a_story_with_no_checked_summary_is_not_published(site):
    ok = story("Checked")
    unchecked = story("Unchecked", summary="Revenue rose 42% in the quarter.")  # a number the source does not state
    empty = story("Empty", summary="")
    listing = story("Listing", url="https://www.anthropic.com/news/announcements")
    assert [gb.publishable(i) for i in (ok, unchecked, empty, listing)] == [True, False, False, False]
    gb.HELD_BACK.clear()
    out = site / "site" / "breakdowns"
    slugs = gb.process_items([ok, unchecked, empty, listing], "news", "27 Nov 2026, 09:00", out, use_ai=False)
    assert slugs == {"Checked": "checked"} and [p.name for p in sorted(out.glob("*.html"))] == ["checked.html"]
    assert sorted(t for t, _ in gb.HELD_BACK) == ["Empty", "Listing", "Unchecked"]
    assert all(why == "no source-checked summary" for _, why in gb.HELD_BACK)


def test_such_a_story_is_on_no_card_and_no_archive_row(site):
    write_data(site, items=[story("Checked"), story("Unchecked", summary="Revenue rose 42% in the quarter.")],
               archive_news=[story("Old checked", pub_date="2026-11-20"),
                             story("Old unchecked", pub_date="2026-11-20", summary="Revenue rose 42% in the quarter.")])
    gh.generate_html()
    page = (site / "site" / "index.html").read_text()
    archive = (site / "site" / "archive.html").read_text()
    assert "Checked" in page and "Unchecked" not in page
    assert "Old checked" in archive and "Old unchecked" not in archive


def test_an_old_fallback_page_is_removed_on_the_next_render(site):
    out = site / "site" / "breakdowns"
    (out / "gone.html").write_text(gb.render_breakdown_page(
        "Gone", "gone", "https://example.test/gone", "", gb.summary_block_html([gb.NO_SUMMARY]),
        gb.guide_block_html(gh.HOW_LABEL, "", gb.NO_HO_TO if hasattr(gb, "NO_HO_TO") else gb.NO_HOW_TO),
        "example.test", "27 Nov 2026, 09:00"))
    assert gb.page_is_fallback(out / "gone.html")
    write_data(site, items=[story("Checked")])
    gh.generate_html()
    assert not (out / "gone.html").exists()
    assert [p.name for p in sorted(out.glob("*.html"))] == ["checked.html"]


def test_a_withdrawn_page_keeps_its_address():
    """A withdrawn page is not a fallback: its story is in no data file, and old links to it still work."""
    page = gb.render_breakdown_page("T", "t", "https://a.test/x", "", gb.summary_block_html([gb.WITHDRAWN_NOTE]),
                                    gb.guide_block_html(gh.HOW_LABEL, "", gb.NO_HOW_TO), "a.test", "27 Nov 2026, 09:00")
    assert gb.WITHDRAWN_NOTE in page and gb.NO_SUMMARY not in page


def test_no_committed_page_is_a_fallback():
    pages = sorted((SITE / "breakdowns").glob("*.html"))
    assert len(pages) > 1000
    assert [p.name for p in pages if gb.page_is_fallback(p)] == []
    assert [p.name for p in SITE.rglob("*.html") if gb.NO_SUMMARY in p.read_text(encoding="utf-8")] == []


# ---------- 4. the archive count is the number of links ----------

def test_archive_count_is_the_number_of_links():
    row = lambda n: {"title": f"Story {n}", "url": f"https://a.test/{n}", "summary": "A thing shipped."}  # noqa: E731
    archive = [{"date": "2026-11-26", "total": 99, "news": [row(i) for i in range(9)]},
               {"date": "2026-11-25", "total": 0, "news": [{**row(1), "title": "One"}]}]
    html = gh.render_archive(archive, "news")
    rows = re.findall(r'<div class="archive-row">(.*?)</div>\s*</div>', html, re.S)
    assert len(rows) == 2
    for row in rows:
        count = int(re.search(r'<div class="archive-count">(\d+) items?</div>', row)[1])
        assert count == len(re.findall(r'<a class="archive-title"', row))
    assert ">9 items<" in rows[0] and ">1 item<" in rows[1] and "99" not in html  # the stored total is never shown


def test_committed_archive_page_counts_match_its_links():
    html = (SITE / "archive.html").read_text()
    rows = re.findall(r'<div class="archive-row">(.*?)</div>\s*</div>', html, re.S)
    assert rows
    for row in rows:
        count = re.search(r'<div class="archive-count">(\d+) items?</div>', row)
        assert count and int(count[1]) == len(re.findall(r'<a class="archive-title"', row))


# ---------- 5. one "How to apply" heading on a breakdown page ----------

def headings(page):
    return re.findall(r"<h[23][^>]*>([^<]*)</h[23]>", page)


def test_the_how_to_apply_block_carries_no_heading_of_its_own():
    for label in ("What to do", "Do this week", "How to use", "How to apply"):
        assert gb.guide_block_html(label, "", "Patch it.") == '<div class="guide-block"><div class="guide-text">Patch it.</div></div>'
    assert gb.guide_block_html("Evaluate", "", "Check.") == '<div class="guide-block"><h3 class="guide-label">Evaluate</h3><div class="guide-text">Check.</div></div>'


def test_a_page_shows_how_to_apply_once():
    page = gb.render_breakdown_page("T", "t", "https://a.test/x", "", "<p>S.</p>",
                                    gb.guide_block_html("Do this week", "", "Patch it."), "a.test", "27 Nov 2026, 09:00")
    assert headings(page).count("How to apply") == 1
    assert gb.restyle_page(page) == page  # and the page still round-trips


def test_no_committed_page_repeats_the_heading():
    bad = [p.name for p in (SITE / "breakdowns").glob("*.html") if headings(p.read_text(encoding="utf-8")).count("How to apply") != 1]
    assert bad == []


# ---------- 7. sitemap.xml and robots.txt ----------

def test_sitemap_lists_every_live_page(site):
    write_data(site, items=[story("Checked")])
    gh.generate_html()
    xml = (site / "site" / "sitemap.xml").read_text()
    assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"' in xml
    locs = re.findall(r"<loc>([^<]+)</loc>", xml)
    assert locs[:3] == [f"{gh.SITE_URL}/", f"{gh.SITE_URL}/archive.html", f"{gh.SITE_URL}/privacy.html"]
    assert f"{gh.SITE_URL}/breakdowns/checked.html" in locs and len(locs) == len(set(locs))
    assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) for day in re.findall(r"<lastmod>([^<]+)</lastmod>", xml))
    assert len(re.findall(r"<url>", xml)) == len(locs)


def test_robots_points_to_the_sitemap(site):
    write_data(site, items=[story("Checked")])
    gh.generate_html()
    robots = (site / "site" / "robots.txt").read_text()
    assert robots == f"User-agent: *\nAllow: /\n\nSitemap: {gh.SITE_URL}/sitemap.xml\n"


def test_the_sitemap_is_written_again_on_every_run(site):
    write_data(site, items=[story("Checked")])
    gh.generate_html()
    first = (site / "site" / "sitemap.xml").read_text()
    (site / "site" / "sitemap.xml").unlink()
    write_data(site, items=[story("Checked"), story("Second")])
    gh.generate_html()
    again = (site / "site" / "sitemap.xml").read_text()
    assert f"{gh.SITE_URL}/breakdowns/second.html" in again and first != again


def test_a_page_with_no_text_of_its_own_is_not_in_the_sitemap(site):
    write_data(site, items=[story("Checked")])
    gh.generate_html()
    out = site / "site" / "breakdowns"
    (out / "withdrawn.html").write_text(gb.render_breakdown_page(
        "Withdrawn", "withdrawn", "https://a.test/x", "", gb.summary_block_html([gb.WITHDRAWN_NOTE]),
        gb.guide_block_html(gh.HOW_LABEL, "", gb.NO_HOW_TO), "a.test", "27 Nov 2026, 09:00"))
    gh.generate_seo_files(str(site / "site"))
    xml = (site / "site" / "sitemap.xml").read_text()
    assert "/breakdowns/checked.html" in xml and "/breakdowns/withdrawn.html" not in xml


def test_the_committed_sitemap_and_robots_are_live():
    xml = (SITE / "sitemap.xml").read_text()
    locs = re.findall(r"<loc>([^<]+)</loc>", xml)
    assert locs[:3] == [f"{gh.SITE_URL}/", f"{gh.SITE_URL}/archive.html", f"{gh.SITE_URL}/privacy.html"]
    pages = {loc.rsplit("/breakdowns/", 1)[1] for loc in locs if "/breakdowns/" in loc}
    assert len(pages) > 500 and all((SITE / "breakdowns" / name).is_file() for name in pages)
    assert (SITE / "robots.txt").read_text().endswith(f"Sitemap: {gh.SITE_URL}/sitemap.xml\n")


# ---------- 6. the privacy notice ----------

def test_privacy_notice_has_portability_object_and_the_transfer_text():
    page = " ".join((SITE / "privacy.html").read_text(encoding="utf-8").split())
    assert "to receive it in a portable form (data portability)" in page
    assert ("Right to object: you can object to how we use your data at any time. Tell us and we stop using it for "
            "that purpose. You do not have to give a reason.") in page
    assert ("Google may process data outside the UK. Google protects these transfers with the safeguards in its data "
            "processing terms, including the UK International Data Transfer Addendum. Email us for a copy.") in page


def test_a_page_the_meaning_check_could_not_read_is_named_in_the_run_log(monkeypatch, capsys):
    """A quota error leaves a page unchecked. The page still goes out (the number check ran), and the run says so."""
    monkeypatch.setattr(judge, "meaning_check", lambda batch: {})  # 429 on every model
    pages = [(story("One"), "Megaspeed is accused.", "Check contracts."), (story("Two"), "Megaspeed is accused.", "")]
    assert gb.meaning_check(pages) == [[], []]
    out = capsys.readouterr().out
    assert "[MEANING] NOT CHECKED for 2 of 2 pages (no labels came back): One; Two." in out
    assert "The number check and the judge still ran." in out


def test_a_page_the_check_did_read_is_not_named(monkeypatch, capsys):
    monkeypatch.setattr(judge, "meaning_check",
                        lambda batch: {it["id"]: [{"label": "SUPPORTED", "reason": ""}] * len(it["sentences"]) for it in batch})
    assert gb.meaning_check([(story("One"), "Megaspeed is accused.", "Check contracts.")]) == [[]]
    assert "NOT CHECKED" not in capsys.readouterr().out


# ---------- the meaning check covers the run even when Gemini has nothing left ----------

GEMINI_DEAD = type("Dead", (Exception,), {"code": 429})


def gemini_exhausted(monkeypatch):
    def dead(prompt, model):
        raise GEMINI_DEAD("429 RESOURCE_EXHAUSTED")
    monkeypatch.setattr(judge, "_ask", dead)
    monkeypatch.setattr(judge, "_pace", lambda: None)  # the rate limiter paces real calls only


def test_groq_reads_the_batch_when_gemini_has_nothing_left(monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setenv("GROQ_API_KEY", "test")
    gemini_exhausted(monkeypatch)
    answer = json.dumps({"items": [{"id": "0", "labels": [{"label": "CONTRADICTED", "reason": "the source says the customer"},
                                                          {"label": "SUPPORTED", "reason": ""}]}]})
    monkeypatch.setattr(gb, "call_groq", lambda prompt, note="": "```json\n" + answer + "\n```")  # a code fence is read too
    out = judge.meaning_check([{"id": "0", "title": "T", "source": SOURCE, "sentences": ["Nvidia is the target.", "Megaspeed is accused."]}])
    assert [lab["label"] for lab in out["0"]] == ["CONTRADICTED", "SUPPORTED"]
    log = capsys.readouterr().out
    assert "all Gemini models failed" in log and "model used: Groq (Gemini had nothing left)" in log


def test_a_contradicted_sentence_is_still_held_back_with_gemini_exhausted(site, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setenv("GROQ_API_KEY", "test")
    gemini_exhausted(monkeypatch)
    page = "Nvidia is the target of the US investigation.\n---SPLIT---\n1. EVALUATE - Check contracts."
    monkeypatch.setattr(gb, "generate_content_for_news", lambda it, note="": page)
    monkeypatch.setattr(gb, "judge_pages", lambda pages: [("pass", "")] * len(pages))
    monkeypatch.setattr(gb, "call_groq", lambda prompt, note="": json.dumps({"items": [
        {"id": str(n), "labels": [{"label": "CONTRADICTED" if "Nvidia is the target" in s else "SUPPORTED", "reason": "r"}
                                  for s in re.findall(r'"((?:[^"\\]|\\.)*)"', prompt.split('"sentences"')[1 + n].split("]")[0])]}
        for n in range(prompt.count('"sentences"'))]}))
    gb.HELD_BACK.clear()
    gb.process_items([story("Megaspeed")], "news", "27 Nov 2026, 09:00", site / "site" / "breakdowns", use_ai=True)
    assert len(gb.HELD_BACK) == 1 and "meaning check" in gb.HELD_BACK[0][1] and "contradicts the source" in gb.HELD_BACK[0][1]


def test_no_model_key_at_all_checks_nothing(monkeypatch):
    for key in ("GEMINI_API_KEY", "GROQ_API_KEY", "GROQ_API_KEY_2"):
        monkeypatch.delenv(key, raising=False)
    assert judge.meaning_check([{"id": "0", "title": "T", "source": SOURCE, "sentences": ["A sentence."]}]) == {}


def test_groq_with_nothing_left_leaves_the_pages_unchecked(monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setenv("GROQ_API_KEY", "test")
    gemini_exhausted(monkeypatch)
    monkeypatch.setattr(gb, "call_groq", lambda prompt, note="": "DAILY_LIMIT")
    assert judge.meaning_check([{"id": "0", "title": "T", "source": SOURCE, "sentences": ["A sentence."]}]) == {}
    assert "Groq fallback has nothing left either" in capsys.readouterr().out
