"""Images, permanent: no card is ever without a figure, and drawing can never pass the day's spend cap.

- Cover: a card with no AI image shows site/images/covers/<slug>.webp, drawn with Pillow from the slug. No text, no API call.
- image_figure: the AI image, else the cover file, else an inline cover. Never "".
- Budget: site/images/budget.json keeps {UTC date: neurons}. No drawing starts when the day's total would pass the cap
  (DAILY_NEURON_BUDGET; its value and the 429 rule are in tests/test_paid_images.py).
- After a Cloudflare 429 the run succeeds and the page builds.
- Ban list: only things that carry text. Plain objects (paper, a phone face-down, cups) may be drawn.
No network: Groq and Cloudflare are stubbed."""
import json
import random
import re
import sys
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import generate_html as gh  # noqa: E402
import generate_images as gi  # noqa: E402

NOW = datetime(2026, 11, 27, 9, 0)
DAY = "2026-11-27"
# The reply run 560 got from Cloudflare on 27 Nov 2026 (request id removed).
CF_429 = {"success": False, "result": None, "messages": [], "errors": [{
    "message": "AiError: AiError: you have used up your daily free allocation of 10,000 neurons, please upgrade to "
               "Cloudflare's Workers Paid plan if you would like to continue usage.", "code": 4006}]}


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
    monkeypatch.setattr(gi, "api_calls", 0)
    return tmp_path


def write_data(site, studies, items):
    (site / "case_studies" / "2026-11-27.json").write_text(json.dumps({"case_studies": studies}))
    (site / "case_studies" / "archive.json").write_text("[]")
    (site / "data" / "news" / "news_2026-11-27.json").write_text(json.dumps({"news": items}))
    (site / "data" / "news_archive.json").write_text("[]")


def stub(monkeypatch, items, usage='{"neurons": 173}', fail=None):
    """top_items, describe_scene and generate_image without a network; OCR reads no text. fail: the error every drawing raises."""
    calls = {"scenes": [], "draws": []}
    monkeypatch.setattr(gi, "top_items", lambda now=None: items)
    monkeypatch.setattr(gi, "text_found", lambda path: "")
    monkeypatch.setattr(gi, "describe_scene", lambda item, avoid="": calls["scenes"].append(item["title"]) or "A glass cube")

    def draw(scene, path):
        calls["draws"].append(path.stem)
        if fail:
            raise fail
        Image.new("RGB", (gi.WIDTH, gi.HEIGHT), (128, 128, 128)).save(path, "WEBP")
        return usage
    monkeypatch.setattr(gi, "generate_image", draw)
    return calls


def budget(site):
    return json.loads((site / "site" / "images" / "budget.json").read_text())


def shown_cards(page):
    """(cards, cards with a figure, figure src values) of the Case Studies and Industry News panels."""
    body = page.split('<div id="case-studies"', 1)[1].split('<div id="archive-cs"', 1)[0]
    cards = re.findall(r'<article class="card [^"]*">(.*?)</article>', body, re.S)
    srcs = [m.group(1) for c in cards if (m := re.match(r'\s*<figure class="card-figure"><img src="([^"]+)"', c))]
    return len(cards), len(srcs), srcs


# ---------- cover: made locally, same picture for the same slug, no text ----------

def test_cover_is_made_for_a_card_with_no_image(site):
    images = site / "site" / "images"
    (images / "has-an-image.webp").write_bytes(b"RIFF0000WEBP")
    assert gi.sync_covers([news("Has an image"), news("Has no image")], str(images)) == 1
    cover = images / "covers" / "has-no-image.webp"
    assert [p.name for p in (images / "covers").iterdir()] == ["has-no-image.webp"]  # the AI image needs no cover
    with Image.open(cover) as image:
        assert image.format == "WEBP" and image.size == (gi.WIDTH, gi.HEIGHT) == (1200, 675)
    assert gi.api_calls == 0
    assert gi.sync_covers([news("Has an image"), news("Has no image")], str(images)) == 0  # made once


def test_cover_is_seeded_by_the_slug():
    first, again, other = gi.cover_image("a-story"), gi.cover_image("a-story"), gi.cover_image("another-story")
    assert first.tobytes() == again.tobytes() and first.tobytes() != other.tobytes()


def test_cover_uses_near_black_emerald_and_two_greys():
    assert (gi.COVER_BG, gi.COVER_EMERALD) == ("#0a0a0a", "#10b981") and len(gi.COVER_GREYS) == 2
    palette = [tuple(int(c[i:i + 2], 16) for i in (1, 3, 5)) for c in (gi.COVER_BG, gi.COVER_EMERALD, *gi.COVER_GREYS)]
    assert all(r == g == b for r, g, b in palette[2:])  # the greys are neutral
    for slug in ("a-story", "another-story", "a-third-story"):
        colours = gi.cover_image(slug).getcolors(maxcolors=gi.WIDTH * gi.HEIGHT)
        flat = sum(n for n, c in colours if c in palette)
        assert flat / (gi.WIDTH * gi.HEIGHT) > 0.97, slug  # the rest is the smooth edge between two forms
        assert palette[0] == max(colours)[1]  # most of the cover is the near-black background
        assert any(c == palette[1] for _, c in colours), slug  # the emerald accent is there


@pytest.mark.parametrize("title", ["The group chat where your AI talks behind your back", "AI made self-hosting fun again",
                                   "Story 0", "O", "I", "100", "A 2027 plan", "Zero"])
def test_cover_has_no_text(tmp_path, title):
    path = gi.make_cover(gi.cover_slug(title), str(tmp_path))
    assert gi.text_found(path) == ""  # OCR finds 0 letters and 0 digits
    assert not gi.has_text(path)


def test_title_with_no_slug_still_has_a_cover(tmp_path):
    assert gi.slugify("模型") == "" and re.fullmatch(r"untitled-[0-9a-f]{10}", gi.cover_slug("模型"))
    assert gi.cover_slug("模型") != gi.cover_slug("新闻") and gi.cover_slug("A story") == "a-story"
    assert gi.sync_covers([{"title": "模型"}], str(tmp_path)) == 1
    assert f'src="images/covers/{gi.cover_slug("模型")}.webp"' in gi.image_figure("模型", str(tmp_path))


def test_covers_no_shown_card_uses_are_deleted(site):
    images = site / "site" / "images"
    gi.sync_covers([news("Stays"), news("Leaves the page"), news("Gets an image")], str(images))
    assert len(list((images / "covers").glob("*.webp"))) == 3
    (images / "gets-an-image.webp").write_bytes(b"RIFF0000WEBP")
    gi.sync_covers([news("Stays"), news("Gets an image")], str(images))
    assert [p.name for p in (images / "covers").glob("*.webp")] == ["stays.webp"]


# ---------- image_figure: the AI image, else the cover. Never no figure ----------

def test_figure_is_the_ai_image_then_the_cover_then_an_inline_cover(site):
    images = site / "site" / "images"
    inline = gi.image_figure("Story")
    assert inline.startswith('<figure class="card-figure"><img src="data:image/svg+xml,') and "<figcaption" not in inline
    assert not (images / "covers").exists()  # building a card writes no file
    gi.sync_covers([news("Story")], str(images))
    cover = gi.image_figure("Story")
    assert cover == ('<figure class="card-figure"><img src="images/covers/story.webp" loading="lazy" width="1200" '
                     'height="675" alt=""></figure>')  # decoration: empty alt, no caption
    (images / "story.webp").write_bytes(b"RIFF0000WEBP")
    ai = gi.image_figure("Story")
    assert '<img src="images/story.webp"' in ai and "<figcaption>AI illustration</figcaption>" in ai
    for title in ("", None, "模型", "<script>alert(1)</script>"):
        assert gi.image_figure(title).count("<figure") == 1 and "<script" not in gi.image_figure(title)


def test_inline_cover_is_a_safe_data_uri():
    src = gi.inline_cover('x"><script>alert(1)</script>')
    assert src.startswith("data:image/svg+xml,") and not re.search(r'["<> ]', src)
    assert "%230a0a0a" in src and "%2310b981" in src and "text" not in src.lower()


def test_every_shown_card_renders_a_figure(site, monkeypatch):
    write_data(site, [study(f"Study {i}", score=60 - i) for i in range(8)], [news(f"News {i}") for i in range(13)])
    (site / "site" / "images" / "news-0.webp").write_bytes(b"RIFF0000WEBP")  # one card has an AI image
    monkeypatch.delenv("IMAGE_GEN_ENABLED", raising=False)
    assert gi.main([]) == 0 and gi.api_calls == 0  # covers are made also when image drawing is off
    gh.generate_html()
    cards, figures, srcs = shown_cards((site / "site" / "index.html").read_text())
    assert cards == 21 and figures == cards
    assert sum(s.startswith("images/covers/") for s in srcs) == 20 and "images/news-0.webp" in srcs
    assert all((site / "site" / s).is_file() for s in srcs)  # no inline cover, no missing file


def test_page_built_before_any_cover_file_still_has_a_figure_on_every_card(site):
    write_data(site, [study("Study")], [news("News")])
    gh.generate_html()  # generate_images.py did not run
    cards, figures, srcs = shown_cards((site / "site" / "index.html").read_text())
    assert cards == figures == 2 and all(s.startswith("data:image/svg+xml,") for s in srcs)


# ---------- daily budget: one neuron total a UTC day, across all runs ----------

CAP = gi.DAILY_NEURON_BUDGET


def test_budget_constants():
    assert gi.NEURONS_PER_DRAWING == 175 and gi.FREE_DAILY_NEURONS == 10000 and CAP > gi.FREE_DAILY_NEURONS


def test_budget_stops_at_the_cap(site, monkeypatch, capsys):
    (site / "site" / "images" / "budget.json").write_text(json.dumps({DAY: CAP - 350}))
    calls = stub(monkeypatch, [news(f"Story {i}") for i in range(10)])
    assert gi.run(today=NOW) == 2 and len(calls["draws"]) == 2  # 2 x 173 = 346; a third drawing would pass the cap
    assert budget(site) == {DAY: CAP - 4.0} and budget(site)[DAY] <= CAP
    assert f"daily spend cap reached ({CAP - 4} of {CAP} neurons" in capsys.readouterr().out
    calls["draws"].clear(), calls["scenes"].clear()
    monkeypatch.setattr(gi, "api_calls", 0)
    assert gi.run(today=NOW) == 0 and calls == {"scenes": [], "draws": []} and gi.api_calls == 0  # no Groq call either
    assert "8 cards keep their cover, 0 API calls" in capsys.readouterr().out


def test_budget_is_one_total_for_all_runs_of_the_day(site, monkeypatch):
    (site / "site" / "images" / "budget.json").write_text(json.dumps({DAY: CAP - 9000}))
    monkeypatch.setattr(gi, "MAX_NEW_PER_RUN", 25)
    calls = stub(monkeypatch, [news(f"Story {i:03d}") for i in range(80)])
    made = [gi.run(today=NOW) for _ in range(4)]
    assert made == [25, 25, 2, 0] and len(calls["draws"]) == 52  # 52 x 173 = 8996 of the 9000 that were left
    assert budget(site) == {DAY: CAP - 4.0}
    assert gi.run(today=datetime(2026, 11, 28, 6, 0)) == 25  # a new UTC day starts at 0
    assert budget(site) == {DAY: CAP - 4.0, "2026-11-28": 4325.0}


def test_neurons_not_reported_count_175_a_drawing(site, monkeypatch):
    stub(monkeypatch, [news("One"), news("Two")], usage="not reported")
    assert gi.run(today=NOW) == 2 and budget(site) == {DAY: 350.0}
    real = json.dumps({"cf-ai-neurons": "172.80", "usage": {"prompt_tokens": 0, "neurons": 172.8000030517578}})
    assert gi.neurons_of(real) == pytest.approx(172.8)  # the header and the body say the same: counted once
    assert gi.neurons_of("not reported") == gi.neurons_of(None) == gi.neurons_of('{"neurons": 0}') == 175


def test_second_try_counts_in_the_budget_and_does_not_start_past_it(site, monkeypatch):
    (site / "site" / "images" / "budget.json").write_text(json.dumps({DAY: CAP - 300}))
    calls = stub(monkeypatch, [news("Story")])
    monkeypatch.setattr(gi, "text_found", lambda path: "GRAND OPENING")  # every drawing shows text
    assert gi.run(today=NOW) == 0 and calls["draws"] == ["story"]  # 127 left after one drawing: no room for the second try
    assert budget(site) == {DAY: CAP - 127.0} and not (site / "site" / "images" / "story.webp").exists()


def test_failed_drawing_counts_one_drawing(site, monkeypatch):
    stub(monkeypatch, [news("Story")], fail=RuntimeError("Cloudflare HTTP 500: timeout"))
    assert gi.run(today=NOW) == 0 and budget(site) == {DAY: 175.0}  # the call can have used neurons


def test_budget_file_keeps_30_days(site, monkeypatch):
    (site / "site" / "images" / "budget.json").write_text(json.dumps({"2026-10-01": 9000, "2026-11-20": 500, "note": "x"}))
    stub(monkeypatch, [news("Story")])
    gi.run(today=NOW)
    assert budget(site) == {"2026-11-20": 500.0, DAY: 173.0}


def test_dry_run_uses_no_budget(site, monkeypatch):
    calls = stub(monkeypatch, [news("Story")])
    assert gi.run(dry_run=True, today=NOW) == 0 and calls["draws"] == []
    assert not (site / "site" / "images" / "budget.json").exists()


# ---------- Cloudflare 429: the run stops, the page still builds ----------

def reply_429(monkeypatch):
    posts = []

    class Reply:
        status_code, headers, text = 429, {"content-type": "application/json"}, ""

        def json(self):
            return CF_429

    import requests
    monkeypatch.setattr(requests, "post", lambda url, **kw: posts.append(url) or Reply())
    return posts


def test_generate_image_raises_outage_on_429(site, monkeypatch):
    reply_429(monkeypatch)
    with pytest.raises(gi.Outage, match="Cloudflare HTTP 429: .*daily free allocation of 10,000 neurons"):
        gi.generate_image("A glass cube", site / "site" / "images" / "x.webp")
    assert issubclass(gi.Outage, RuntimeError) and not (site / "site" / "images" / "x.webp").exists()


def test_after_a_429_the_run_succeeds_and_the_page_builds_with_a_figure_on_every_card(site, monkeypatch, capsys):
    write_data(site, [study(f"Study {i}", score=60 - i) for i in range(3)], [news(f"News {i}") for i in range(4)])
    monkeypatch.setenv("IMAGE_GEN_ENABLED", "true")
    monkeypatch.setattr(gi, "describe_scene", lambda item, avoid="": "A glass cube")
    posts = reply_429(monkeypatch)
    assert gi.main([]) == 0 and len(posts) == 1  # exit code 0: "python generate_images.py && python generate_html.py" goes on
    assert not (site / "site" / "images" / "budget.json").exists()  # nothing is stored about the day
    assert "covers: 7 made" in capsys.readouterr().out
    gh.generate_html()
    cards, figures, srcs = shown_cards((site / "site" / "index.html").read_text())
    assert cards == figures == 7 and all(s.startswith("images/covers/") and (site / "site" / s).is_file() for s in srcs)


# ---------- order: newest shown cards first; the AI image replaces the cover ----------

def test_newest_shown_cards_are_drawn_first_and_the_ai_image_replaces_the_cover(site, monkeypatch):
    write_data(site, [], [news("Old", "2026-11-20"), news("Newest", "2026-11-27"), news("Middle", "2026-11-24")])
    images = site / "site" / "images"
    monkeypatch.delenv("IMAGE_GEN_ENABLED", raising=False)
    gi.main([])
    assert sorted(p.stem for p in (images / "covers").glob("*.webp")) == ["middle", "newest", "old"]
    calls = stub(monkeypatch, gi.top_items())
    monkeypatch.setattr(gi, "MAX_NEW_PER_RUN", 2)
    monkeypatch.setenv("IMAGE_GEN_ENABLED", "true")
    assert gi.main([]) == 0 and calls["draws"] == ["newest", "middle"]
    assert sorted(p.stem for p in (images / "covers").glob("*.webp")) == ["old"]  # the two covers are gone
    assert 'src="images/newest.webp"' in gi.image_figure("Newest") and 'src="images/covers/old.webp"' in gi.image_figure("Old")


# ---------- ban list: only things that carry text ----------

@pytest.mark.parametrize("scene", [
    "A stack of blank sheets, a folded paper, and a glass bottle",  # rejected for "paper" in run 560
    "A cracked smartphone, a spilled coffee cup, a shattered glass",  # rejected for "smartphone" in run 560
    "A phone lying face-down beside two ceramic cups", "Three paper cups on a steel tray",
    "A plain envelope and a folder made of grey fibre"])
def test_ban_list_allows_plain_objects(scene):
    assert gi.banned_words(scene) == []


def test_face_down_names_no_face_but_a_face_is_still_banned():
    assert gi.banned_words("A phone lying face down") == [] and gi.banned_words("A phone, face-down") == []
    assert gi.banned_words("A clay face looking down") == ["face"] and gi.banned_words("A phone lying face-up") == ["face"]


def test_things_that_carry_print_stay_banned():
    for scene in ("A blank card", "A cardboard box with packaging", "An open laptop", "A keyboard"):
        assert gi.banned_words(scene), scene


@pytest.mark.parametrize("group, scene", [
    ("signs", "A neon sign"), ("screens", "A glowing screen"), ("screens", "Three monitors"),
    ("documents", "A signed document"), ("books", "An old book"), ("labels", "A jar with a label"),
    ("logos", "A steel logo"), ("maps", "A folded map"), ("flags", "A row of flags"), ("landmarks", "A stone landmark"),
    ("buildings", "A glass building"), ("streets", "An empty street"), ("people", "Two people"), ("faces", "A clay face")])
def test_ban_list_keeps_every_subject_that_carries_text(group, scene):
    words = set(gi.BANNED[group].split())
    hits = gi.banned_words(scene)
    assert hits and any(h in words or h.removesuffix("s") in words for h in hits), (group, hits)


def test_scene_prompt_allows_plain_objects():
    prompt = gi.scene_prompt(news("Faster cache"))
    assert "a sheet of blank paper, a phone lying face-down, a cup" in prompt
    assert "No words, letters, numbers or symbols" in prompt


def test_ocr_guard_is_still_the_final_check(site, monkeypatch, capsys):
    calls = stub(monkeypatch, [news("Story")])
    monkeypatch.setattr(gi, "text_found", lambda path: "GRAND OPENING")
    assert gi.run(today=NOW) == 0 and calls["draws"] == ["story", "story"]  # drawn, read, rejected, twice
    assert not (site / "site" / "images" / "story.webp").exists()
    assert "the card keeps its cover" in capsys.readouterr().out


# ---------- the committed site ----------

def test_committed_page_has_a_figure_on_every_shown_card_and_every_file_exists():
    cards, figures, srcs = shown_cards((ROOT / "site" / "index.html").read_text())
    assert cards > 0 and figures == cards
    assert [s for s in srcs if not (ROOT / "site" / s).is_file()] == []


def test_committed_covers_show_no_text():
    covers = sorted((ROOT / "site" / "images" / gi.COVERS_DIR).glob("*.webp"))  # none when every card has its AI image
    for path in random.Random(0).sample(covers, min(5, len(covers))):  # OCR on 5 covers
        with Image.open(path) as image:
            assert image.size == (gi.WIDTH, gi.HEIGHT), path.name
        assert gi.text_found(path) == "", path.name


def test_committed_budget_file_is_date_to_neurons():
    data = json.loads((ROOT / "site" / "images" / gi.BUDGET_FILE).read_text())
    assert isinstance(data, dict) and all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) and isinstance(n, (int, float)) and n >= 0 for day, n in data.items())
