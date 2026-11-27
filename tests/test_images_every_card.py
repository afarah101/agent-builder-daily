"""AI image on every card: top_items matches the cards the page shows, existing images are kept, at most
MAX_NEW_PER_RUN draws a run (newest cards first), a card with no file has no <img>, prune spares shown cards.
No text: banned scene words are asked for once more, then a fallback; OCR text means one redraw, then skip.
No network: Groq, Cloudflare and OCR are stubbed."""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import generate_html as gh
import generate_images as gi

REAL_TEXT_IN_IMAGE = gi.text_in_image  # kept before the autouse stub replaces it
NOW = datetime(2026, 10, 27, 9, 0)


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 27, 9, 0)

    @classmethod
    def utcnow(cls):
        return cls(2026, 10, 27, 9, 0)


def study(title, day="2026-10-25", score=50):
    return {"title": title, "url": f"https://arxiv.org/abs/{gi.slugify(title)}", "pub_date": day,
            "authors": ["A. Author"], "summary": "S.", "total_score": score}


def news(title, day="2026-10-26", score=10):
    return {"title": title, "url": f"https://example.test/{gi.slugify(title)}", "source": "example.test",
            "pub_date": day, "summary": "S.", "relevance_score": score}


@pytest.fixture(autouse=True)
def ocr_clean(monkeypatch):
    """No tesseract needed in tests: OCR is ready and finds no text unless a test says otherwise."""
    monkeypatch.setattr(gi, "ocr_ready", lambda: True)
    monkeypatch.setattr(gi, "text_in_image", lambda path: [])


@pytest.fixture
def site(tmp_path, monkeypatch):
    (tmp_path / "case_studies").mkdir()
    (tmp_path / "data" / "news").mkdir(parents=True)
    (tmp_path / "site" / "images").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(gh, "datetime", Clock)
    return tmp_path


def write_data(site, studies, archive_studies, items, archive_news):
    (site / "case_studies" / "2026-10-27.json").write_text(json.dumps({"case_studies": studies}))
    (site / "case_studies" / "archive.json").write_text(
        json.dumps([{"date": "2026-10-20", "case_studies": archive_studies}]))
    (site / "data" / "news" / "news_2026-10-27.json").write_text(json.dumps({"news": items}))
    (site / "data" / "news_archive.json").write_text(json.dumps([{"date": "2026-10-20", "news": archive_news}]))


def panel_titles(page, panel):
    seg = page.split(f'<div id="{panel}"', 1)[1].split('role="tabpanel"', 2)[1]
    return re.findall(r'<h2 class="card-title"><a [^>]*>([^<]*)</a>', seg)


def test_top_items_returns_every_card_the_two_tabs_show(site):
    write_data(site,
               [study(f"Study {i}", score=60 - i) for i in range(3)] + [study("Too old", day="2026-01-01")],
               [study(f"Old study {i}", day="2026-10-20") for i in range(7)],
               [news(f"News {i}") for i in range(2)] + [news("Future", day="2026-12-01")],
               [news(f"Old news {i}", day="2026-10-20") for i in range(8)])
    items = gi.top_items(now=NOW)
    gh.generate_html()
    page = (site / "site" / "index.html").read_text()
    shown = panel_titles(page, "case-studies") + panel_titles(page, "industry-news")
    assert len(shown) >= 16 and sorted(i["title"] for i in items) == sorted(shown)
    assert "Too old" not in shown and "Future" not in shown  # out of window / future date: no card, no image


def stub_api(monkeypatch, calls):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "test")
    monkeypatch.setattr(gi, "describe_scene", lambda item, avoid="": f"scene for {item['title']}")

    def draw(scene, path):
        calls.append(path.stem)
        path.write_bytes(b"RIFF0000WEBP")
        return json.dumps({"neurons": 173})
    monkeypatch.setattr(gi, "generate_image", draw)


def test_existing_images_are_skipped(site, monkeypatch):
    items = [news("Kept", day="2026-10-26"), news("New", day="2026-10-25")]
    monkeypatch.setattr(gi, "top_items", lambda: items)
    (site / "site" / "images" / "kept.webp").write_bytes(b"old")
    calls = []
    stub_api(monkeypatch, calls)
    assert gi.run(today=NOW) == 1
    assert calls == ["new"] and (site / "site" / "images" / "kept.webp").read_bytes() == b"old"


def test_at_most_max_new_per_run_newest_first(site, monkeypatch, capsys):
    assert gi.MAX_NEW_PER_RUN == 25
    items = [news(f"Story {i:02d}", day=f"2026-10-{1 + i % 26:02d}") for i in range(40)]
    monkeypatch.setattr(gi, "top_items", lambda: items)
    calls = []
    stub_api(monkeypatch, calls)
    assert gi.run(today=NOW) == 25 and len(calls) == 25
    newest = sorted(items, key=lambda x: x["pub_date"], reverse=True)[:25]
    assert {gi.slugify(i["title"]) for i in newest} == set(calls)
    out = capsys.readouterr().out
    assert "this run draws 25" in out and "neurons 4325.0" in out  # 25 x 173, logged per run
    calls.clear()
    assert gi.run(today=NOW) == 15 and len(calls) == 15  # the next run draws the rest


def test_card_with_no_file_renders_no_img(site):
    html = gh.render_case_studies({"case_studies": [study("No picture")]}, {"case_studies": {}})
    html += gh.render_news({"news": [news("No picture either")]}, {"news": {}})
    assert "<img" not in html and "<figure" not in html


def test_every_card_with_a_file_renders_its_figure(site):
    for t in ("First", "Second", "Third"):
        (site / "site" / "images" / f"{gi.slugify(t)}.webp").write_bytes(b"x")
    html = gh.render_news({"news": [news(t) for t in ("First", "Second", "Third", "Fourth")]}, {"news": {}})
    imgs = re.findall(r"<img [^>]*>", html)
    assert len(imgs) == 3 and all('loading="lazy"' in i and 'width="1200"' in i and 'height="675"' in i for i in imgs)


def test_failed_image_call_stops_the_run_and_leaves_no_file(site, monkeypatch):
    monkeypatch.setattr(gi, "top_items", lambda: [news("Broken")])
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "test")
    monkeypatch.setattr(gi, "describe_scene", lambda item: "scene")
    monkeypatch.setattr(gi, "generate_image", lambda scene, path: (_ for _ in ()).throw(RuntimeError("HTTP 500")))
    assert gi.run(today=NOW) == 0
    assert not (site / "site" / "images" / "broken.webp").exists()
    assert "<img" not in gh.render_news({"news": [news("Broken")]}, {"news": {}})


def test_prune_never_deletes_an_image_a_shown_card_uses(site):
    images = site / "site" / "images"
    for slug in ("shown", "gone"):
        (images / f"{slug}.webp").write_bytes(b"x")
    alts = {"shown": {"created": "2026-01-01"}, "gone": {"created": "2026-01-01"}}
    gi.prune(str(images), alts, NOW, keep={"shown"})
    assert (images / "shown.webp").exists() and not (images / "gone.webp").exists()


def test_banned_scene_word_retries_once_then_uses_fallback(monkeypatch, capsys):
    prompts = []
    answers = iter(["A glowing phone screen on a desk", "A book of binary code beside a laptop"])
    monkeypatch.setattr(gi, "ask_groq", lambda prompt: prompts.append(prompt) or next(answers))
    item = news("Chip export rules")
    scene = gi.describe_scene(item)
    assert len(prompts) == 2 and "rejected" in prompts[1] and "phone" in prompts[1]
    assert scene == gi.fallback_scene(item) and scene in gi.FALLBACK_SCENES and gi.banned_in(scene) == []
    assert "using a safe fallback scene" in capsys.readouterr().out


def test_banned_scene_word_then_clean_retry_keeps_the_retry(monkeypatch):
    answers = iter(["Three monitors in a dark room", "A glass sphere above a still mountain lake"])
    monkeypatch.setattr(gi, "ask_groq", lambda prompt: next(answers))
    assert gi.describe_scene(news("Planet hunt")) == "A glass sphere above a still mountain lake"


def test_banned_in_finds_plurals_and_digits():
    assert gi.banned_in("Two signs, 3 labels and keyboards") == ["digits", "keyboard", "label", "sign"]
    assert gi.banned_in("Soft light on a curved stone arch over water") == []
    assert all(gi.banned_in(s) == [] for s in gi.FALLBACK_SCENES)


def test_ocr_text_redraws_once_then_skips(site, monkeypatch, capsys):
    monkeypatch.setattr(gi, "top_items", lambda: [news("Texty"), news("Next", day="2026-10-25")])
    calls, scenes = [], []
    stub_api(monkeypatch, calls)
    monkeypatch.setattr(gi, "describe_scene", lambda item, avoid="": scenes.append(avoid) or "scene")
    monkeypatch.setattr(gi, "text_in_image", lambda path: ["HELLO"] if path.stem == "texty" else [])
    assert gi.run(today=NOW) == 1  # the run goes on to the next card
    assert calls == ["texty", "texty", "next"] and len(scenes) == 3 and "showed text" in scenes[1]
    assert not (site / "site" / "images" / "texty.webp").exists() and (site / "site" / "images" / "next.webp").exists()
    alts = json.loads((site / "site" / "images" / "alt.json").read_text())
    assert "texty" not in alts and "next" in alts
    out = capsys.readouterr().out
    assert "skipped: text (texty)" in out and "1 generated, 1 regenerated, 1 skipped (text), 3 draws" in out
    assert "neurons 519.0" in out  # every draw counts, the deleted ones too


def test_ocr_text_then_clean_redraw_keeps_the_redraw(site, monkeypatch, capsys):
    monkeypatch.setattr(gi, "top_items", lambda: [news("Second try")])
    calls, seen = [], []
    stub_api(monkeypatch, calls)
    monkeypatch.setattr(gi, "text_in_image", lambda path: ["WORD"] if not seen and not seen.append(1) else [])
    assert gi.run(today=NOW) == 1 and calls == ["second-try", "second-try"]
    assert (site / "site" / "images" / "second-try.webp").exists()
    assert "1 generated, 1 regenerated, 0 skipped (text)" in capsys.readouterr().out


def test_ocr_clean_keeps_the_image_with_one_draw(site, monkeypatch, capsys):
    monkeypatch.setattr(gi, "top_items", lambda: [news("Clean")])
    calls = []
    stub_api(monkeypatch, calls)
    assert gi.run(today=NOW) == 1 and calls == ["clean"]
    assert (site / "site" / "images" / "clean.webp").exists()
    assert "0 regenerated, 0 skipped (text), 1 draws" in capsys.readouterr().out


def test_ocr_error_is_treated_as_text_and_never_fails_the_run(site, monkeypatch):
    monkeypatch.setattr(gi, "top_items", lambda: [news("Unreadable")])
    calls = []
    stub_api(monkeypatch, calls)
    monkeypatch.setattr(gi, "text_in_image", lambda path: (_ for _ in ()).throw(OSError("bad file")))
    assert gi.run(today=NOW) == 0 and len(calls) == 2
    assert not (site / "site" / "images" / "unreadable.webp").exists()


def test_no_ocr_means_no_api_calls(site, monkeypatch, capsys):
    monkeypatch.setattr(gi, "top_items", lambda: [news("Any")])
    calls = []
    stub_api(monkeypatch, calls)
    monkeypatch.setattr(gi, "ocr_ready", lambda: False)
    assert gi.run(today=NOW) == 0 and calls == []
    assert "OCR not available" in capsys.readouterr().out


def test_redraws_count_against_the_per_run_draw_budget(site, monkeypatch):
    monkeypatch.setattr(gi, "top_items", lambda: [news(f"Story {i:02d}") for i in range(30)])
    calls = []
    stub_api(monkeypatch, calls)
    monkeypatch.setattr(gi, "text_in_image", lambda path: ["TEXT"])
    assert gi.run(today=NOW) == 0 and len(calls) == gi.MAX_NEW_PER_RUN


def test_text_in_image_reads_real_text(tmp_path):
    pytest.importorskip("pytesseract")
    if not gi.ocr_ready():
        pytest.skip("tesseract binary not installed")
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.load_default(size=72)
    for name, words in (("text", "BREAKING NEWS"), ("blank", "")):
        img = Image.new("RGB", (1200, 675), (20, 30, 28))
        ImageDraw.Draw(img).text((200, 280), words, fill=(240, 240, 240), font=font)
        img.save(tmp_path / f"{name}.webp", "WEBP", quality=70)
    assert REAL_TEXT_IN_IMAGE(tmp_path / "text.webp")  # real tesseract finds the words
    assert REAL_TEXT_IN_IMAGE(tmp_path / "blank.webp") == []
