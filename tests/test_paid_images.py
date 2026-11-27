"""Paid images (Cloudflare Workers Paid): every shown card gets an AI image in one run; a cover shows only when Cloudflare
does not draw.

- Spend cap, not image cap: DAILY_SPEND_CAP_USD = 1.00. Neuron cap = 10,000 free + 1.00 / 0.011 x 1000 = 100,909 a UTC day.
- MAX_NEW_PER_RUN = 120: a full backfill of the page fits in one run.
- A Cloudflare 429 or 5xx stops that run only. The card keeps its cover until the next run, which draws again.
- Newest shown cards first. The cover file is deleted when its AI image exists.
- A breakdown page shows its card's AI image under the title: same file, width and height set, alt = the scene.
No network: Groq and Cloudflare are stubbed."""
import base64
import io
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import generate_breakdowns as gb  # noqa: E402
import generate_html as gh  # noqa: E402
import generate_images as gi  # noqa: E402

NOW = datetime(2026, 11, 27, 9, 0)
DAY = "2026-11-27"
SOURCE = (ROOT / "generate_images.py").read_text()


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 27, 9, 0)

    @classmethod
    def utcnow(cls):
        return cls(2026, 11, 27, 9, 0)


def news(title, day="2026-11-26", score=10):
    return {"title": title, "url": f"https://example.test/{gi.slugify(title)}", "source": "example.test",
            "pub_date": day, "summary": "A vendor ships a faster cache.", "relevance_score": score}


def study(title, day="2026-11-25", score=50):
    return {"title": title, "url": f"https://arxiv.org/abs/{gi.slugify(title)}", "pub_date": day,
            "authors": ["A. Author"], "summary": "S.", "total_score": score}


@pytest.fixture
def site(tmp_path, monkeypatch):
    """An empty site in a temp folder, with the Cloudflare secrets set and the clock at 27 Nov 2026, 09:00."""
    (tmp_path / "case_studies").mkdir()
    (tmp_path / "data" / "news").mkdir(parents=True)
    (tmp_path / "site" / "images").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(gh, "datetime", Clock)
    monkeypatch.setattr(gi, "datetime", Clock)
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "test")
    monkeypatch.setenv("IMAGE_GEN_ENABLED", "true")
    monkeypatch.setattr(gi, "api_calls", 0)
    monkeypatch.setattr(gi, "ocr_engine", lambda: None)  # these tests stub text_found; the OCR tests are in test_clean_images.py
    monkeypatch.setattr(gi, "text_found", lambda path: "")
    return tmp_path


def write_data(site, studies, items):
    (site / "case_studies" / "2026-11-27.json").write_text(json.dumps({"case_studies": studies}))
    (site / "case_studies" / "archive.json").write_text("[]")
    (site / "data" / "news" / "news_2026-11-27.json").write_text(json.dumps({"news": items}))
    (site / "data" / "news_archive.json").write_text("[]")


def stub(monkeypatch, items=None, scene="A glass cube"):
    """describe_scene and generate_image without a network (173 neurons a drawing). items: what top_items returns."""
    calls = {"scenes": [], "draws": []}
    if items is not None:
        monkeypatch.setattr(gi, "top_items", lambda now=None: items)
    monkeypatch.setattr(gi, "describe_scene", lambda item, avoid="": calls["scenes"].append(item["title"]) or scene)

    def draw(scene, path):
        calls["draws"].append(path.stem)
        path.write_bytes(b"RIFF0000WEBP")
        return json.dumps({"cf-ai-neurons": "173"})
    monkeypatch.setattr(gi, "generate_image", draw)
    return calls


def cloudflare(monkeypatch, statuses):
    """requests.post answers with each status in turn, then 200 with a real image. Returns the list of posts made."""
    posts, left = [], list(statuses)
    png = io.BytesIO()
    Image.new("RGB", (64, 64), (128, 128, 128)).save(png, "PNG")

    class Reply:
        def __init__(self, status):
            self.status_code, self.text = status, "upstream error"
            self.headers = {"content-type": "application/json", "cf-ai-neurons": "173"} if status in (200, 429) else {}

        def json(self):
            if self.status_code == 200:
                return {"success": True, "result": {"image": base64.b64encode(png.getvalue()).decode()}}
            return {"success": False, "errors": [{"message": "AiError: too many requests", "code": 3040}]}

    import requests
    monkeypatch.setattr(requests, "post", lambda url, **kw: posts.append(url) or Reply(left.pop(0) if left else 200))
    monkeypatch.setattr(gi, "describe_scene", lambda item, avoid="": "A glass cube")
    return posts


def budget(site):
    path = site / "site" / "images" / "budget.json"
    return json.loads(path.read_text()) if path.exists() else {}


def covers(site):
    return sorted(p.stem for p in (site / "site" / "images" / "covers").glob("*.webp"))


# ---------- spend cap, not image cap ----------

def test_spend_cap_is_1_usd_and_the_neuron_cap_is_100909():
    assert gi.DAILY_SPEND_CAP_USD == 1.00 and gi.USD_PER_1000_NEURONS == 0.011 and gi.FREE_DAILY_NEURONS == 10000
    assert gi.DAILY_NEURON_BUDGET == 100909
    assert gi.DAILY_NEURON_BUDGET == gi.FREE_DAILY_NEURONS + int(gi.DAILY_SPEND_CAP_USD / gi.USD_PER_1000_NEURONS * 1000)
    assert re.search(r"^DAILY_SPEND_CAP_USD = 1\.00\b", SOURCE, re.M) and re.search(r"^DAILY_NEURON_BUDGET = 100909\b", SOURCE, re.M)
    assert "9000" not in SOURCE  # the free-plan budget is gone


def test_estimated_cost_in_usd():
    assert gi.cost_usd(0) == gi.cost_usd(10000) == 0.0  # the free neurons cost nothing
    assert gi.cost_usd(20000) == pytest.approx(0.11) and gi.cost_usd(30760) == pytest.approx(0.22836)
    assert 0.99 < gi.cost_usd(gi.DAILY_NEURON_BUDGET) <= gi.DAILY_SPEND_CAP_USD  # the cap is never more than 1.00 USD


def test_no_drawing_starts_when_it_would_pass_the_cap(site, monkeypatch, capsys):
    images = site / "site" / "images"
    (images / "budget.json").write_text(json.dumps({DAY: 100909 - 175}))
    assert gi.Budget(str(images), DAY).can_draw()  # one drawing fits exactly
    (images / "budget.json").write_text(json.dumps({DAY: 100909 - 174}))
    assert not gi.Budget(str(images), DAY).can_draw()
    calls = stub(monkeypatch, [news("Story")])
    assert gi.run(today=NOW) == 0 and calls == {"scenes": [], "draws": []} and gi.api_calls == 0
    assert ("daily spend cap reached (100735 of 100909 neurons, estimated cost $1.00 of the $1.00 cap, on 2026-11-27 UTC): "
            "1 cards keep their cover, 0 API calls") in capsys.readouterr().out
    assert gi.run(today=datetime(2026, 11, 28, 6, 0)) == 1  # the next UTC day starts at 0


def test_the_cap_does_not_stop_a_day_of_normal_runs(site, monkeypatch):
    (site / "site" / "images" / "budget.json").write_text(json.dumps({DAY: 10000}))  # the free neurons are used
    calls = stub(monkeypatch, [news(f"Story {i:03d}") for i in range(120)])
    assert gi.run(today=NOW) == 120 and len(calls["draws"]) == 120  # the old 9000 budget stopped here at 0 drawings
    assert budget(site) == {DAY: 10000 + 120 * 173.0}


def test_run_logs_the_days_neurons_and_the_estimated_cost(site, monkeypatch, capsys):
    stub(monkeypatch, [news("One"), news("Two"), news("Three")])
    assert gi.run(today=NOW) == 3
    assert ("done: 3 generated, 0 API calls, neurons 519.0; day total 519 of 100909 neurons, "
            "estimated cost $0.00 of the $1.00 cap, on 2026-11-27 UTC") in capsys.readouterr().out
    (site / "site" / "images" / "budget.json").write_text(json.dumps({DAY: 30587}))
    stub(monkeypatch, [news("Four")])
    assert gi.run(today=NOW) == 1
    assert "day total 30760 of 100909 neurons, estimated cost $0.23 of the $1.00 cap" in capsys.readouterr().out


# ---------- 120 drawings a run: a full backfill in one run ----------

def test_run_limit_is_120(site, monkeypatch, capsys):
    assert gi.MAX_NEW_PER_RUN == 120 and re.search(r"^MAX_NEW_PER_RUN = 120\b", SOURCE, re.M)
    items = [news(f"Story {i:03d}", day=f"2026-11-{1 + i % 26:02d}") for i in range(130)]
    calls = stub(monkeypatch, items)
    assert gi.run(today=NOW) == 120 and len(calls["draws"]) == 120
    assert "130 cards need an image; this run draws 120, the next run the rest" in capsys.readouterr().out
    newest = sorted(items, key=lambda x: x["pub_date"], reverse=True)[:120]
    assert {gi.slugify(i["title"]) for i in newest} == set(calls["draws"])
    calls["draws"].clear()
    assert gi.run(today=NOW) == 10 and len(calls["draws"]) == 10  # the next run draws the rest
    assert budget(site) == {DAY: 130 * 173.0}


def test_every_shown_card_gets_an_ai_image_in_one_run(site, monkeypatch):
    write_data(site, [study(f"Study {i}", score=60 - i) for i in range(8)], [news(f"News {i}") for i in range(13)])
    monkeypatch.setenv("IMAGE_GEN_ENABLED", "false")
    gi.main([])
    assert len(covers(site)) == 21  # before the run: 21 cards, 21 covers, as on 27 Nov 2026
    monkeypatch.setenv("IMAGE_GEN_ENABLED", "true")
    calls = stub(monkeypatch)
    assert gi.main([]) == 0 and len(calls["draws"]) == 21
    assert covers(site) == [] and len(list((site / "site" / "images").glob("*.webp"))) == 21
    gh.generate_html()
    page = (site / "site" / "index.html").read_text().split('<div id="archive-cs"', 1)[0]
    assert page.count('<article class="card ') == 21 == page.count("<figcaption>AI illustration</figcaption>")
    assert "images/covers/" not in page and "data:image/svg" not in page


# ---------- a 429 or 5xx stops this run only ----------

def test_a_429_stops_only_the_current_run(site, monkeypatch, capsys):
    write_data(site, [], [news(f"News {i}", day=f"2026-11-{20 + i}") for i in range(4)])
    posts = cloudflare(monkeypatch, [200, 429])
    assert gi.main([]) == 0  # the run succeeds
    assert len(posts) == 2  # one drawing, then the 429: no third call
    assert "Cloudflare did not draw news-2, stopping this run, cards keep their cover until the next run: Cloudflare HTTP 429" \
        in capsys.readouterr().out
    assert budget(site) == {DAY: 173.0}  # only the drawing that was made: no "exhausted" marker for the day
    assert covers(site) == ["news-0", "news-1", "news-2"]  # the 3 cards with no image keep their cover
    assert 'src="images/news-3.webp"' in gi.image_figure("News 3") and 'src="images/covers/news-2.webp"' in gi.image_figure("News 2")
    posts.clear()
    assert gi.main([]) == 0 and len(posts) == 3  # the next run, the same UTC day: it draws the 3 that are left
    assert covers(site) == [] and budget(site) == {DAY: 4 * 173.0}


@pytest.mark.parametrize("status", [500, 502, 503, 524])
def test_a_5xx_stops_only_the_current_run(site, monkeypatch, capsys, status):
    items = [news("First", "2026-11-26"), news("Second", "2026-11-25")]
    monkeypatch.setattr(gi, "top_items", lambda now=None: items)
    posts = cloudflare(monkeypatch, [status])
    assert gi.run(today=NOW) == 0 and len(posts) == 1
    assert f"stopping this run, cards keep their cover until the next run: Cloudflare HTTP {status}" in capsys.readouterr().out
    assert budget(site) == {}  # Cloudflare drew nothing: nothing is counted, nothing is stored about the day
    assert gi.run(today=NOW) == 2 and len(posts) == 3  # the next run draws both


def test_outage_is_a_429_or_a_5xx_only(site, monkeypatch):
    for status, error in ((429, gi.Outage), (500, gi.Outage), (503, gi.Outage), (401, RuntimeError), (400, RuntimeError)):
        cloudflare(monkeypatch, [status])
        with pytest.raises(error, match=f"Cloudflare HTTP {status}") as caught:
            gi.generate_image("A glass cube", site / "site" / "images" / "x.webp")
        assert (caught.type is gi.Outage) == (status == 429 or status >= 500)
    assert "exhaust" not in SOURCE  # no code path marks a whole day as used up


# ---------- newest first; the AI image replaces the cover ----------

def test_newest_shown_cards_are_drawn_first(site, monkeypatch):
    calls = stub(monkeypatch, [news("Old", "2026-11-20"), news("Newest", "2026-11-27"), news("Middle", "2026-11-24")])
    assert gi.run(today=NOW) == 3 and calls["draws"] == ["newest", "middle", "old"]


def test_the_cover_is_deleted_when_the_ai_image_exists(site, monkeypatch):
    images = site / "site" / "images"
    items = [news("Story"), news("模型")]  # the second title has no slug: its cover has a hash name, and it gets no AI image
    gi.sync_covers(items, str(images))
    assert covers(site) == ["story", gi.cover_slug("模型")]
    stub(monkeypatch, items)
    assert gi.run(today=NOW) == 1  # run() alone, with no cover sync after it
    assert (images / "story.webp").is_file() and covers(site) == [gi.cover_slug("模型")]  # deleted when the image was made
    assert '<img src="images/story.webp"' in gi.image_figure("Story") and "covers" not in gi.image_figure("Story")


def test_a_cover_left_beside_an_ai_image_is_deleted_by_the_next_run(site, monkeypatch):
    images = site / "site" / "images"
    write_data(site, [], [news("Story")])
    (images / "covers").mkdir()
    (images / "covers" / "story.webp").write_bytes(b"RIFF0000WEBP")
    (images / "story.webp").write_bytes(b"RIFF0000WEBP")  # an image a workflow of an earlier version made
    calls = stub(monkeypatch)
    assert gi.main([]) == 0 and calls["draws"] == [] and covers(site) == []


def test_a_card_whose_drawing_shows_text_twice_keeps_its_cover(site, monkeypatch):
    write_data(site, [], [news("Story")])
    calls = stub(monkeypatch)
    monkeypatch.setattr(gi, "text_found", lambda path: "GRAND OPENING")
    assert gi.main([]) == 0 and calls["draws"] == ["story", "story"]
    assert covers(site) == ["story"] and not (site / "site" / "images" / "story.webp").exists()


# ---------- breakdown page: the card's AI image under the title ----------

def breakdown_page():
    summary, guide = gb.parse_groq_response("Summary.\n---SPLIT---\n1. EVALUATE - Check.\n4. RISKS TO FLAG - Risk.")
    return gb.render_breakdown_page("T", "t", "https://a.test/x", "", summary, guide, "Source · 2026-10-01", "27 Oct 2026, 09:00")


def test_breakdown_page_shows_the_cards_ai_image_under_the_title(tmp_path):
    out = tmp_path / "site" / "breakdowns"
    images = tmp_path / "site" / "images"
    out.mkdir(parents=True)
    images.mkdir()
    (out / "t.html").write_text(breakdown_page())
    assert gb.sync_figures(out) == 0 and "<img" not in (out / "t.html").read_text()  # no AI image: no figure
    (images / "t.webp").write_bytes(b"RIFF0000WEBP")
    (images / "alt.json").write_text(json.dumps({"t": {"alt": 'Two clay blocks, one "pressed" flat', "style": gi.STYLE_ID}}))
    (images / "covers").mkdir()
    (images / "covers" / "t.webp").write_bytes(b"RIFF0000WEBP")  # a cover is not an AI image
    assert gb.sync_figures(out) == 1
    html = (out / "t.html").read_text()
    figure = ('<figure class="article-figure"><img src="../images/t.webp" width="1200" height="675" '
              'alt="Two clay blocks, one &quot;pressed&quot; flat" fetchpriority="high" decoding="async">'
              '<figcaption>AI illustration</figcaption></figure>')
    assert html.count("<figure") == 1 and figure in html  # the card's file, width and height set, alt = the scene
    order = [html.index(s) for s in ('<h1 class="page-title">', "</header>", '<figure class="article-figure">',
                                     '<h2 class="section-title">Summary</h2>')]
    assert order == sorted(order)  # under the title, above the text
    assert gb.sync_figures(out) == 0  # nothing to change


def test_breakdown_page_alt_follows_a_redrawn_image(tmp_path):
    out = tmp_path / "site" / "breakdowns"
    images = tmp_path / "site" / "images"
    out.mkdir(parents=True)
    images.mkdir()
    (out / "t.html").write_text(breakdown_page())
    (images / "t.webp").write_bytes(b"RIFF0000WEBP")
    (images / "alt.json").write_text(json.dumps({"t": {"alt": "An old scene"}}))
    assert gb.sync_figures(out) == 1 and 'alt="An old scene"' in (out / "t.html").read_text()
    (images / "alt.json").write_text(json.dumps({"t": {"alt": "A glass cube", "style": gi.STYLE_ID}}))  # the image was redrawn
    assert gb.sync_figures(out) == 1
    html = (out / "t.html").read_text()
    assert 'alt="A glass cube"' in html and "An old scene" not in html and html.count("<figure") == 1
    (images / "t.webp").unlink()
    assert gb.sync_figures(out) == 1 and "<figure" not in (out / "t.html").read_text()


def test_breakdown_pages_show_the_image_made_in_the_same_run(site, monkeypatch):
    write_data(site, [study("A study")], [news("A story")])
    gh.generate_html()  # orchestrator.py: the pages exist before any image
    pages = {slug: site / "site" / "breakdowns" / f"{slug}.html" for slug in ("a-study", "a-story")}
    assert all(p.is_file() and "<figure" not in p.read_text() for p in pages.values())
    stub(monkeypatch, scene="A brass key on a slab of slate")
    assert gi.main([]) == 0  # the workflow's next step: python generate_images.py && python generate_html.py
    gh.generate_html()
    for slug, page in pages.items():
        assert (f'<figure class="article-figure"><img src="../images/{slug}.webp" width="1200" height="675" '
                'alt="A brass key on a slab of slate"') in page.read_text(), slug
