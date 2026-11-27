"""AI image on every card: top_items matches the cards the page shows, existing images are kept, at most
MAX_NEW_PER_RUN new images a run (newest cards first), a card with no file shows its cover, prune spares shown cards.
No network: Groq and Cloudflare are stubbed."""
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
    monkeypatch.setattr(gi, "describe_scene", lambda item: f"scene for {item['title']}")

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
    monkeypatch.setattr(gi, "MAX_NEW_PER_RUN", 25)  # the run's limit, set low for this test
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


def test_card_with_no_file_renders_its_cover(site):
    html = gh.render_case_studies({"case_studies": [study("No picture")]}, {"case_studies": {}})
    html += gh.render_news({"news": [news("No picture either")]}, {"news": {}})
    assert html.count('<figure class="card-figure"><img src="') == 2 and "<figcaption" not in html
    assert "images/no-picture.webp" not in html and "images/no-picture-either.webp" not in html


def test_every_card_with_a_file_renders_its_figure(site):
    for t in ("First", "Second", "Third"):
        (site / "site" / "images" / f"{gi.slugify(t)}.webp").write_bytes(b"x")
    html = gh.render_news({"news": [news(t) for t in ("First", "Second", "Third", "Fourth")]}, {"news": {}})
    imgs = re.findall(r"<img [^>]*>", html)
    assert len(imgs) == 4 and all('loading="lazy"' in i and 'width="1200"' in i and 'height="675"' in i for i in imgs)
    assert [bool(re.search(r'src="images/[a-z]+\.webp"', i)) for i in imgs] == [True, True, True, False]  # 3 AI images, 1 cover
    assert "<figcaption" not in html  # no caption on a card: the page footer says the images are AI-generated


def test_failed_image_call_stops_the_run_and_leaves_no_file(site, monkeypatch):
    monkeypatch.setattr(gi, "top_items", lambda: [news("Broken")])
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "test")
    monkeypatch.setattr(gi, "describe_scene", lambda item: "scene")
    monkeypatch.setattr(gi, "generate_image", lambda scene, path: (_ for _ in ()).throw(RuntimeError("HTTP 500")))
    assert gi.run(today=NOW) == 0
    assert not (site / "site" / "images" / "broken.webp").exists()
    html = gh.render_news({"news": [news("Broken")]}, {"news": {}})
    assert html.count("<figure") == 1 and "images/broken.webp" not in html  # the card shows its cover


def test_prune_never_deletes_an_image_a_shown_card_uses(site):
    images = site / "site" / "images"
    for slug in ("shown", "gone"):
        (images / f"{slug}.webp").write_bytes(b"x")
    alts = {"shown": {"created": "2026-01-01"}, "gone": {"created": "2026-01-01"}}
    gi.prune(str(images), alts, NOW, keep={"shown"})
    assert (images / "shown.webp").exists() and not (images / "gone.webp").exists()
