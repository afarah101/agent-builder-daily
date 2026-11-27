"""Clean editorial images: one fixed house style, no scene that carries writing, and an OCR check on every drawing.

FLUX ignores "no text". So the scene may not name a thing that carries writing (the ban list), every new image is
read by OCR, an image that shows text is drawn once more from a new scene, and a second miss keeps no image.
No network: Groq and Cloudflare are stubbed. OCR is real (RapidOCR, local)."""
import base64
import io
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import generate_breakdowns as gb  # noqa: E402
import generate_images as gi  # noqa: E402

NOW = datetime(2026, 11, 27, 9, 0)
SUFFIX = ("minimal still life, soft studio light, matte materials, shallow depth of field, neutral grey background, "
          "single emerald #10b981 accent, editorial magazine photo")
GROUPS = ("landmarks", "buildings", "streets", "screens", "signs", "documents", "books", "labels", "logos", "people",
          "faces", "flags", "maps")


def news(title, day="2026-11-26"):
    return {"title": title, "url": f"https://example.test/{gi.slugify(title)}", "source": "example.test",
            "pub_date": day, "summary": "A vendor ships a faster cache."}


def picture(path, text=None):
    """A real 1200x675 WebP: a grey still life with one emerald disc, with or without drawn text."""
    image = Image.new("RGB", (gi.WIDTH, gi.HEIGHT), (128, 128, 128))
    draw = ImageDraw.Draw(image)
    draw.ellipse((450, 190, 750, 490), fill=(16, 185, 129))
    if text:
        draw.text((120, 60), text, font=ImageFont.load_default(size=90), fill=(20, 20, 20))
    image.save(path, "WEBP", quality=gi.QUALITY)
    return path


@pytest.fixture
def images(tmp_path, monkeypatch):
    out = tmp_path / "site" / "images"
    out.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "test")
    monkeypatch.setattr(gi, "api_calls", 0)
    return out


def stub(monkeypatch, items, scenes=None, text_on=()):
    """top_items, describe_scene and generate_image without a network. text_on: drawing numbers (1-based) that show text."""
    calls = {"scenes": [], "draws": []}
    monkeypatch.setattr(gi, "top_items", lambda: items)

    def describe(item, avoid=""):
        scene = (scenes or {}).get((item["title"], bool(avoid)), f"A glass cube for {item['title']}" + (" again" if avoid else ""))
        calls["scenes"].append((item["title"], avoid))
        return scene

    def draw(scene, path):
        calls["draws"].append((path.stem, scene))
        picture(path, "GRAND OPENING" if len(calls["draws"]) in text_on else None)
        return json.dumps({"neurons": 173})
    monkeypatch.setattr(gi, "describe_scene", describe)
    monkeypatch.setattr(gi, "generate_image", draw)
    return calls


# ---------- house style: one fixed suffix on every prompt ----------

def test_house_style_is_the_fixed_suffix():
    assert gi.HOUSE_STYLE == SUFFIX and gi.MAX_NEW_PER_RUN == 25


@pytest.mark.parametrize("scene", ["Two clay blocks pressed into one.", "A glass cube", *gi.SAFE_SCENES])
def test_suffix_is_on_every_image_prompt(scene):
    prompt = gi.image_prompt(scene)
    assert prompt.endswith(". " + SUFFIX) and prompt.startswith(scene.rstrip("."))
    assert not re.search(r"no text|no letters|no logos|watermark", prompt)  # FLUX takes no negative prompt


def test_generate_image_sends_scene_plus_suffix(images, monkeypatch):
    sent = []
    png = io.BytesIO()
    Image.new("RGB", (64, 64), (128, 128, 128)).save(png, "PNG")

    class Reply:
        status_code, headers = 200, {"content-type": "application/json"}
        text = ""

        def json(self):
            return {"success": True, "result": {"image": base64.b64encode(png.getvalue()).decode()}}

    import requests
    monkeypatch.setattr(requests, "post", lambda url, **kw: sent.append(kw["json"]["prompt"]) or Reply())
    for scene in ("A cracked clay bowl", gi.SAFE_SCENES[0]):
        gi.generate_image(scene, images / "x.webp")
    assert sent == [f"A cracked clay bowl. {SUFFIX}", f"{gi.SAFE_SCENES[0]}. {SUFFIX}"]
    assert Image.open(images / "x.webp").size == (gi.WIDTH, gi.HEIGHT)


# ---------- ban list: the scene may not name a thing that carries writing ----------

def test_ban_list_rejects_tower_bridge_at_night():
    assert gi.banned_words("Tower Bridge at night") == ["tower", "bridge"]


def test_ban_list_has_every_group():
    assert set(GROUPS) <= set(gi.BANNED)


@pytest.mark.parametrize("scene", [
    "A London Tower Bridge glows faintly as a translucent ribbon of words weaves across its arches",
    "A sleek laptop sits on a wooden desk, its screen displaying a document",
    "A crumpled, handwritten note lies beside a stack of dusty, open laptops",
    "A lone server rack glows under a single SSH terminal screen",
    "A battered ledger page with a lone pencil", "A weathered book on a stone shelf", "A neon sign above a street",
    "Glass jars with paper labels", "A steel plate with an engraved logo", "Two people shaking hands", "A smiling face",
    "A row of flags", "A folded map on a table", "A glass skyscraper", "Three monitors on a desk", "A stack of BOOKS",
    "A humming factory", "The Eiffel Tower made of wire", "A bar graph made of glass", "A stack of employee handbooks"])
def test_ban_list_rejects(scene):
    assert gi.banned_words(scene), scene


@pytest.mark.parametrize("scene", [
    "Two clay blocks pressed into one on a steel plate", "One bare silicon wafer behind a glass pane",
    "A single origami crane folded from plain matte fibre", "A brass key resting on a slab of slate",
    "A sandglass with emerald grains", "A mushroom-shaped block of sandpaper-rough stone and a signal-red cord",
    *gi.SAFE_SCENES])
def test_ban_list_allows_objects_materials_and_forms(scene):
    assert gi.banned_words(scene) == []


def test_scene_prompt_asks_for_objects_only_and_names_the_bans():
    prompt = gi.scene_prompt(news("Faster cache"))
    assert "physical objects, raw materials or abstract geometric forms" in prompt and "No setting" in prompt
    for word in ("landmarks", "buildings", "streets", "screens", "monitors", "signs", "documents", "books", "labels",
                 "logos", "flags", "maps", "No people, no faces"):
        assert word in prompt, word
    again = gi.scene_prompt(news("Faster cache"), avoid="tower, bridge")
    assert "Do not use: tower, bridge." in again and "Do not use" not in prompt


def test_describe_scene_retries_once_then_uses_a_safe_scene(monkeypatch):
    prompts = []
    answers = iter(["Tower Bridge at night", "A brass key on a slab of slate", "A neon sign", "An old book"])
    monkeypatch.setattr(gb, "call_groq", lambda prompt: prompts.append(prompt) or next(answers))
    monkeypatch.setattr(gi, "api_calls", 0)
    assert gi.describe_scene(news("Faster cache")) == "A brass key on a slab of slate"
    assert len(prompts) == 2 and "Do not use: tower, bridge." in prompts[1]
    scene = gi.describe_scene(news("Faster cache"))  # banned twice: no third call
    assert scene in gi.SAFE_SCENES and scene == gi.safe_scene(news("Faster cache")) and len(prompts) == 4
    assert gi.api_calls == 4


# ---------- text guard: OCR reads every drawing ----------

def test_ocr_guard_rejects_an_image_with_drawn_text(tmp_path):
    drawn = picture(tmp_path / "text.webp", "GRAND OPENING")
    found = gi.text_found(drawn)
    assert gi.has_text(drawn) and len(found) >= 10 and "GRAND" in found.upper()


def test_ocr_guard_accepts_an_image_with_no_text(tmp_path):
    plain = picture(tmp_path / "plain.webp")
    assert gi.text_found(plain) == "" and not gi.has_text(plain)


def test_three_letters_is_the_limit(tmp_path, monkeypatch):
    assert gi.TEXT_LIMIT == 3
    path = picture(tmp_path / "x.webp")
    for found, shows in (("", False), ("R", False), ("00", False), ("EOK", True), ("n08", True)):
        monkeypatch.setattr(gi, "text_found", lambda p, found=found: found)
        assert gi.has_text(path) is shows, found


def test_low_confidence_noise_is_not_text(tmp_path, monkeypatch):
    monkeypatch.setattr(gi, "ocr_engine", lambda: lambda pixels: ([[None, "ABCDEF", 0.3], [None, "x-1", 0.9]], None))
    assert gi.text_found(picture(tmp_path / "x.webp")) == "x1"


def test_text_on_first_drawing_draws_once_more_from_a_new_scene(images, monkeypatch, capsys):
    calls = stub(monkeypatch, [news("Story")], text_on={1})
    assert gi.run(today=NOW) == 1
    assert calls["scenes"] == [("Story", ""), ("Story", "A glass cube for Story")]  # the second scene avoids the first
    assert [d[1] for d in calls["draws"]] == ["A glass cube for Story", "A glass cube for Story again"]
    assert not gi.has_text(images / "story.webp")
    entry = json.loads((images / "alt.json").read_text())["story"]
    assert entry["alt"] == "A glass cube for Story again" and entry["style"] == gi.STYLE_ID
    assert "text on story, try 1" in capsys.readouterr().out


def test_text_twice_keeps_no_image_and_the_run_goes_on(images, monkeypatch, capsys):
    calls = stub(monkeypatch, [news("Bad", day="2026-11-26"), news("Good", day="2026-11-25")], text_on={1, 2})
    assert gi.run(today=NOW) == 1
    assert [d[0] for d in calls["draws"]] == ["bad", "bad", "good"]  # two tries only, then the next card
    assert not (images / "bad.webp").exists() and (images / "good.webp").exists()
    assert list(json.loads((images / "alt.json").read_text())) == ["good"]
    assert "no clean image for bad" in capsys.readouterr().out


def test_no_ocr_means_no_api_call(images, monkeypatch, capsys):
    calls = stub(monkeypatch, [news("Story")])
    monkeypatch.setattr(gi, "ocr_engine", lambda: (_ for _ in ()).throw(ImportError("No module named 'rapidocr_onnxruntime'")))
    assert gi.run(today=NOW) == 0 and calls["draws"] == [] and calls["scenes"] == []
    assert "OCR not available" in capsys.readouterr().out and not (images / "story.webp").exists()


# ---------- order and budget: shown cards, newest first, good images kept, 25 drawings a run ----------

def test_good_image_is_kept_and_bad_or_old_style_images_are_redrawn(images, monkeypatch):
    items = [news("Good", "2026-11-26"), news("Banned", "2026-11-25"), news("Old style", "2026-11-24"),
             news("Unknown", "2026-11-23"), news("Missing", "2026-11-22")]
    for slug in ("good", "banned", "old-style", "unknown"):
        (images / f"{slug}.webp").write_bytes(b"old")
    (images / "alt.json").write_text(json.dumps({
        "good": {"alt": "A brass key on slate", "style": gi.STYLE_ID},
        "banned": {"alt": "Tower Bridge at night", "style": gi.STYLE_ID},
        "old-style": {"alt": "A brass key on slate"},
        "gone": {"alt": "A file that was deleted", "style": gi.STYLE_ID}}))
    calls = stub(monkeypatch, items)
    assert gi.run(today=NOW) == 3
    assert [d[0] for d in calls["draws"]] == ["banned", "old-style", "missing"]  # newest first
    assert (images / "good.webp").read_bytes() == b"old" and (images / "unknown.webp").read_bytes() == b"old"
    alts = json.loads((images / "alt.json").read_text())
    assert sorted(alts) == ["banned", "good", "missing", "old-style"]  # the entry with no file is dropped
    assert all(alts[s]["style"] == gi.STYLE_ID and not gi.banned_words(alts[s]["alt"]) for s in alts)
    calls["draws"].clear()
    assert gi.run(today=NOW) == 0 and calls["draws"] == []  # every shown card now has a good image


def test_second_tries_count_toward_the_25_drawings(images, monkeypatch):
    items = [news(f"Story {i:02d}", f"2026-11-{26 - i % 20:02d}") for i in range(30)]
    calls = stub(monkeypatch, items, text_on={1, 3})  # two cards need a second drawing
    assert gi.run(today=NOW) == 23 and len(calls["draws"]) == 25
    assert len(list(images.glob("*.webp"))) == 23


# ---------- the committed images ----------

def test_no_committed_image_has_a_banned_scene():
    folder = ROOT / "site" / "images"
    alts = json.loads((folder / "alt.json").read_text())
    bad = {p.stem: gi.banned_words(alts[p.stem].get("alt")) for p in folder.glob("*.webp")
           if p.stem in alts and gi.banned_words(alts[p.stem].get("alt"))}
    assert bad == {}
    assert not (folder / "towerbridge-bridging-the-dual-encoder-modality-gap-via-weight-space-programming.webp").exists()


def test_no_committed_image_shows_text():
    shown = {p.name: gi.text_found(p) for p in (ROOT / "site" / "images").glob("*.webp")}
    assert {name: found for name, found in shown.items() if len(found) >= gi.TEXT_LIMIT} == {}
