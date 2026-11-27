#!/usr/bin/env python3
"""AI editorial images for every Daily Edge AI card. Off unless IMAGE_GEN_ENABLED is "true".

For every card on the Case Studies and Industry News tabs (archive tabs get none), Groq writes a still-life scene from the title and
summary, and FLUX.1-schnell (Apache-2.0) on Cloudflare Workers AI draws it with one fixed house-style suffix. Each image is saved as
site/images/<slug>.webp; site/images/alt.json keeps the scene (the alt text), the date and the style it was made in.

No fake writing: FLUX ignores "no text", so the scene may not name a thing that carries writing (BANNED), and OCR reads every
new image. An image that shows text is drawn once more from a new scene; if that one shows text too, the card keeps no image.
A card keeps a good image until prune; a run makes at most MAX_NEW_PER_RUN drawings, cards the page shows first, newest first.
Never fails the pipeline: the first API error is logged, the run stops (no more API calls) and that card keeps no image.
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

IMAGES_DIR = "site/images"
ALT_FILE = "alt.json"
MODEL = "@cf/black-forest-labs/flux-1-schnell"  # Apache-2.0, on Cloudflare Workers AI. Never FLUX.1-dev.
CF_URL = "https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}"
CF_SECRETS = ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID")
# The fixed suffix on every image prompt. No "no text" list: the model takes no negative prompt, and naming text invites it.
HOUSE_STYLE = ("minimal still life, soft studio light, matte materials, shallow depth of field, neutral grey background, "
               "single emerald #10b981 accent, editorial magazine photo")
STYLE_ID = "still-life-1"  # stored in alt.json; an image made in another style is redrawn, so the cards look like one series

# Things that carry writing in real life, and people. FLUX draws fake letters on them, so a scene may not name one.
# Whole words, plural included; "_" stands for a space.
BANNED = {
    "landmarks": "landmark monument tower bridge skyline cathedral temple castle palace lighthouse statue eiffel colosseum "
                 "london paris tokyo manhattan big_ben golden_gate taj_mahal times_square new_york",
    "buildings": "building skyscraper office factory factories warehouse house room hall laboratory laboratories lab library "
                 "libraries shop store storefront hospital school church station airport datacenter data_center city "
                 "cities town village architecture facade",
    "streets": "street road highway avenue alley sidewalk pavement intersection crosswalk traffic plaza",
    "screens": "screen monitor display laptop computer desktop phone smartphone tablet tv television terminal dashboard "
               "interface ui hologram projector keyboard cursor icon browser app website",
    "signs": "sign signage signpost billboard poster banner placard neon plaque nameplate marquee license_plate",
    "documents": "document paper page note letter envelope ledger handbook contract certificate receipt invoice newspaper "
                 "magazine scroll manuscript notebook notepad folder whiteboard blackboard chalkboard postcard ticket card "
                 "passport handwritten handwriting writing written text word equation formula glyph font typography code "
                 "digit numeral numbered symbol signature inscription engraved question_mark",
    "books": "book bookshelf bookcase textbook diary journal",
    "labels": "label labeled labelled tag sticker stamp barcode packaging qr_code",
    "logos": "logo brand emblem badge trademark insignia",
    "people": "person people man men woman women child children crowd human worker employee engineer scientist hand finger "
              "forearm portrait",
    "faces": "face eye mouth smile",
    "flags": "flag pennant",
    "maps": "map atlas globe chart graph diagram",
}
_BANNED_RE = re.compile(r"\b(?:" + "|".join(re.escape(w.replace("_", " ")) for words in BANNED.values() for w in words.split())
                        + r")(?:s|es)?\b", re.IGNORECASE)

# Scenes that are safe by construction. A card gets one (picked by its title) when Groq names a banned thing twice.
SAFE_SCENES = (
    "Three matte ceramic spheres of different sizes resting on a smooth stone slab",
    "A stack of rough concrete cubes with one cube slightly out of line",
    "A coil of brushed steel wire resting beside a smooth river stone",
    "Two interlocking glass rings standing on a slate tile",
    "A row of plain wooden blocks rising in height like steps",
    "A cracked clay bowl with one smooth glass marble inside",
    "A bundle of copper rods tied with plain cord",
    "A smooth cone and a rough cube of pale stone side by side",
)

TEXT_LIMIT = 3  # an image where OCR reads this many letters or digits shows text
OCR_MIN_SCORE = 0.5  # RapidOCR confidence below this is noise, not writing
WIDTH, HEIGHT = 1200, 675
STEPS = 4  # flux-1-schnell default; the model returns a square image, fitted to 1200x675
QUALITY = 70
MAX_NEW_PER_RUN = 25  # 2 runs a day x 25 x ~173 neurons stays under Cloudflare's free 10,000 neurons a day
MAX_AGE_DAYS = 120  # 4 months, the site's window
FALLBACK_ALT = "Abstract editorial illustration"

api_calls = 0  # every Groq or Cloudflare request adds 1
_ocr = None  # the RapidOCR engine, loaded once


def log(msg):
    print(f"[images] {msg}", flush=True)


def slugify(title):
    """Same slug as generate_breakdowns.slugify, so card and image agree."""
    slug = str(title or "").lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug.strip())
    slug = re.sub(r"-+", "-", slug)
    return slug[:80]


def load_alts(images_dir=IMAGES_DIR):
    try:
        with open(Path(images_dir) / ALT_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def image_figure(title, images_dir=IMAGES_DIR):
    """The card's <figure> when site/images/<slug>.webp exists, else "". The alt is the scene description."""
    import html as html_lib
    slug = slugify(title)
    if not slug or not (Path(images_dir) / f"{slug}.webp").is_file():
        return ""
    alt = str((load_alts(images_dir).get(slug) or {}).get("alt") or "").strip() or FALLBACK_ALT
    return (f'<figure class="card-figure"><img src="images/{slug}.webp" loading="lazy" width="{WIDTH}" '
            f'height="{HEIGHT}" alt="{html_lib.escape(alt)}"><figcaption>AI illustration</figcaption></figure>')


def names_in(item):
    """Names of people, companies and sources that must not reach the image prompt."""
    names = [*item.get("authors", []), item.get("institution", ""), item.get("source", "")]
    # capitalised words inside the title (not its first word) are usually product, company or person names
    words = str(item.get("title", "")).split()
    names += [w.strip(".,:;!?()'\"") for w in words[1:] if w[:1].isupper()]
    return [n for n in (str(x).strip() for x in names) if len(n) > 1]


def strip_names(text, names):
    for name in sorted(set(names), key=len, reverse=True):
        text = re.sub(re.escape(name), "", text, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", text).strip(" ,.;:-")


def banned_words(scene):
    """The banned things a scene names, lower case, in the order they appear. Empty: the scene may be drawn."""
    return list(dict.fromkeys(m.group(0).lower() for m in _BANNED_RE.finditer(str(scene or ""))))


def safe_scene(item):
    """One of SAFE_SCENES, always the same one for the same title."""
    return SAFE_SCENES[sum(str(item.get("title", "")).encode("utf-8")) % len(SAFE_SCENES)]


def image_prompt(scene):
    """The prompt FLUX gets: the scene, then the fixed house style."""
    return f"{str(scene).strip().rstrip('.')}. {HOUSE_STYLE}"[:2048]


def scene_prompt(item, avoid=""):
    names = names_in(item)
    title = strip_names(str(item.get("title", "")), names)
    summary = strip_names(str(item.get("summary", ""))[:600], names)
    again = f"\nDo not use: {avoid}. Choose different, simpler objects with smooth blank surfaces." if avoid else ""
    return f"""Describe ONE still-life subject for an editorial photo, in one sentence of at most 20 words.
Topic: {title}
Summary: {summary}
Rules: choose one to three plain physical objects, raw materials or abstract geometric forms that stand for the story's
idea (for a story about a chip export ban: one bare silicon wafer behind a glass pane; about merging two models: two clay
blocks pressed into one). Name only the objects and what they are made of. No setting, no room, no place.
Nothing that carries writing in real life: no landmarks, buildings, streets, screens, monitors, signs, documents, books,
labels, logos, flags, maps, charts, keyboards or packaging. No people, no faces, no hands.
No company, product or person names. No words, letters, numbers or symbols.{again}
Answer with the sentence only."""


def describe_scene(item, avoid=""):
    """A still-life scene for the item, from Groq. A scene that names a banned thing gets one more Groq call with those
    words listed; after that the card gets its safe scene. So no banned word ever reaches the image model.
    avoid: what the new scene must not repeat (the scene whose image showed text)."""
    global api_calls
    from generate_breakdowns import call_groq  # the existing Groq client, with its key fallback
    names = names_in(item)
    for _ in range(2):
        api_calls += 1
        raw = call_groq(scene_prompt(item, avoid))
        if not raw or raw == "DAILY_LIMIT":
            raise RuntimeError(f"Groq returned {raw!r}")
        scene = strip_names(raw.strip().strip('"').splitlines()[0], names)[:300]
        hits = banned_words(scene)
        if scene and not hits:
            return scene
        log(f"scene rejected, names {hits or 'nothing'}: {scene}")
        avoid = ", ".join(filter(None, [avoid, *hits]))
    return safe_scene(item)


def ocr_engine():
    """RapidOCR (Apache-2.0): runs on onnxruntime with its models inside the package. No network, no API call, free."""
    global _ocr
    if _ocr is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr = RapidOCR()
    return _ocr


def text_found(path):
    """The letters and digits OCR reads in the image, as one string. A file that is not an image holds none."""
    from PIL import Image
    try:
        with Image.open(path) as image:
            pixels = image.convert("RGB")
    except OSError:
        return ""
    import numpy as np
    lines = ocr_engine()(np.asarray(pixels))[0] or []
    return "".join(c for _, text, score in lines if float(score) >= OCR_MIN_SCORE for c in str(text) if c.isalnum())


def has_text(path):
    """True when the image shows writing: OCR reads TEXT_LIMIT or more letters or digits."""
    return len(text_found(path)) >= TEXT_LIMIT


def generate_image(scene, out_path):
    """Draw the scene with FLUX.1-schnell on Cloudflare Workers AI and save it as WebP at quality 70.
    Returns the neurons the call used, as reported by Cloudflare, or "not reported"."""
    global api_calls
    import base64, io
    import requests
    from PIL import Image, ImageOps
    token, account = (os.environ.get(k) for k in CF_SECRETS)
    if not token or not account:
        raise RuntimeError("CLOUDFLARE_API_TOKEN or CLOUDFLARE_ACCOUNT_ID is not set")
    api_calls += 1
    resp = requests.post(CF_URL.format(account=account, model=MODEL), headers={"Authorization": f"Bearer {token}"},
                         json={"prompt": image_prompt(scene), "steps": STEPS}, timeout=120)
    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    if resp.status_code != 200 or not body.get("success"):
        raise RuntimeError(f"Cloudflare HTTP {resp.status_code}: {body.get('errors') or resp.text[:200]}")
    image = Image.open(io.BytesIO(base64.b64decode(body["result"]["image"])))
    image = ImageOps.fit(image.convert("RGB"), (WIDTH, HEIGHT))
    tmp = out_path.with_suffix(".tmp.webp")
    image.save(tmp, "WEBP", quality=QUALITY, method=6)
    tmp.replace(out_path)
    usage = {k: v for k, v in resp.headers.items() if "neuron" in k.lower()}
    usage.update({k: v for k, v in (body.get("result") or {}).items() if k != "image"})
    return json.dumps(usage) if usage else "not reported"


def top_items(now=None):
    """Every card the Case Studies and Industry News tabs show, built as generate_html builds them
    (top_up, 4-month window, score order, honesty checks). Reads only: archives nothing."""
    import generate_html as gh
    now = now or datetime.now()
    cutoff = (now - timedelta(days=gh.CASE_STUDY_WINDOW_DAYS)).strftime("%Y-%m-%d")
    studies = []
    for f in sorted(Path(gh.CASE_STUDIES_DIR).glob("*.json")):
        if "archive" in f.name:
            continue
        data = gh.load_json(str(f))
        studies += (data.get("case_studies") or []) if isinstance(data, dict) else []
    studies = gh.top_up(studies, gh.load_json(gh.ARCHIVE_CS_FILE), "case_studies")  # same stories the page shows
    studies = sorted((s for s in studies if gh.in_window(s, cutoff)), key=lambda x: gh.by_score(x, "total_score"), reverse=True)
    news_files = sorted(Path(gh.NEWS_DIR).glob("news_*.json"))
    news_data = gh.load_json(str(news_files[-1])) if news_files else {}
    news = sorted((news_data or {}).get("news") or [], key=lambda x: gh.by_score(x, "relevance_score"), reverse=True)
    news = gh.top_up(news, gh.load_json(gh.ARCHIVE_NEWS_FILE), "news")
    changes = []  # read only: what honest() hides is not shown, so it gets no image
    return gh.honest(studies, now, changes, "case study") + gh.honest(news, now, changes, "news")


def newest_first(items):
    """Cards to draw, newest pub_date first, so today's cards get images before the backlog."""
    import generate_html as gh
    return sorted(items, key=lambda x: gh.parse_date(x.get("pub_date")) or "", reverse=True)


def neuron_total(used):
    """Sum of the neurons Cloudflare reported for this run's images, or "not reported"."""
    total, found = 0.0, False
    for u in used:
        try:
            for k, v in json.loads(u).items():
                if "neuron" in k.lower():
                    total += float(v); found = True
        except (TypeError, ValueError, AttributeError):
            continue
    return round(total, 1) if found else "not reported"


def prune(images_dir, alts, today, keep=()):
    """Delete images made more than 4 months ago (date from alt.json; file age only when no date is stored).
    An image a shown card uses (its slug in keep) is never deleted."""
    cutoff = today - timedelta(days=MAX_AGE_DAYS)
    keep = set(keep)
    for path in Path(images_dir).glob("*.webp"):
        slug = path.stem
        if slug in keep:
            continue
        try:
            made = datetime.strptime((alts.get(slug) or {}).get("created", ""), "%Y-%m-%d")
        except ValueError:
            made = datetime.fromtimestamp(path.stat().st_mtime)
        if made < cutoff:
            path.unlink()
            alts.pop(slug, None)
            log(f"deleted old image {slug}")


def is_good(slug, out, alts):
    """True when the card's image stays: the file exists and alt.json says it was made in the current house style from
    a scene with no banned word (such an image also passed the text check). A file alt.json does not know is kept."""
    if not (out / f"{slug}.webp").exists():
        return False
    entry = alts.get(slug)
    if not isinstance(entry, dict):
        return True
    return entry.get("style") == STYLE_ID and not banned_words(entry.get("alt"))


def draw_clean(item, path, draws_left):
    """Draw the card's image; if OCR reads text on it, draw it once more from a new scene.
    Returns (scene, neurons of each drawing). scene is None when no clean image was made: the file is then gone."""
    scene, used = None, []
    for attempt in (1, 2):
        if len(used) >= draws_left:
            break
        scene = describe_scene(item) if attempt == 1 else describe_scene(item, avoid=scene)
        used.append(generate_image(scene, path))
        found = text_found(path)
        if len(found) < TEXT_LIMIT:
            return scene, used
        path.unlink(missing_ok=True)  # no image is better than one with fake writing
        log(f"text on {path.stem}, try {attempt} ({found[:40]!r}): {scene}")
    return None, used


def run(dry_run=False, images_dir=IMAGES_DIR, today=None):
    today = today or datetime.now()
    out = Path(images_dir)
    out.mkdir(parents=True, exist_ok=True)
    alts = load_alts(images_dir)
    try:
        items = top_items()
    except Exception as e:
        log(f"could not load stories: {e}")
        items = []
    try:
        prune(images_dir, alts, today, keep={slugify(i.get("title", "")) for i in items})
    except Exception as e:
        log(f"prune error: {e}")
    missing = [k for k in CF_SECRETS if not os.environ.get(k)]
    if not dry_run and missing:
        # checked once, before any Groq call, so a missing secret wastes no API calls
        log(f"{', '.join(missing)} not set: add under Settings > Secrets and variables > Actions. "
            "No images generated, 0 API calls")
        return 0
    made, used = 0, []
    todo, seen = [], set()
    for item in newest_first(items):
        slug = slugify(item.get("title", ""))
        if not slug or slug in seen:
            continue
        seen.add(slug)
        if is_good(slug, out, alts):
            log(f"skip (good image): {slug}")
            continue
        todo.append((slug, item))
    if len(todo) > MAX_NEW_PER_RUN:
        log(f"{len(todo)} cards need an image; this run draws {MAX_NEW_PER_RUN}, the next run the rest")
    if todo and not dry_run:
        try:
            ocr_engine()  # checked once, before any API call: without OCR no image can be checked for text
        except Exception as e:
            log(f"OCR not available ({type(e).__name__}: {e}): pip install rapidocr-onnxruntime. "
                "No images generated, 0 API calls")
            todo = []
    for slug, item in todo:
        if len(used) >= MAX_NEW_PER_RUN:  # every drawing counts, a second try too: the neuron budget holds
            break
        path = out / f"{slug}.webp"
        if dry_run:
            log(f"dry run, would generate: {slug}")
            used.append(None)  # counts toward the run's limit, as a real drawing does
            continue
        try:
            scene, neurons = draw_clean(item, path, MAX_NEW_PER_RUN - len(used))
            used += neurons
            if scene is None:
                alts.pop(slug, None)
                log(f"no clean image for {slug}: the card keeps no image this run")
                continue
            alts[slug] = {"alt": scene, "created": today.strftime("%Y-%m-%d"), "model": MODEL,
                          "provider": "cloudflare-workers-ai", "neurons": neurons[-1], "style": STYLE_ID}
            made += 1
            log(f"generated {slug} ({path.stat().st_size / 1024:.1f} KB, neurons {neurons[-1]}): {scene}")
        except Exception as e:
            # stop the whole run: no more Groq or Cloudflare calls after the first error
            log(f"error for {slug}, stopping the run, cards keep no image: {type(e).__name__}: {e}")
            break
    if not dry_run:
        for slug in [k for k in alts if not (out / f"{k}.webp").exists()]:
            del alts[slug]  # an entry with no file (image deleted or rejected) describes nothing
        try:
            with open(out / ALT_FILE, "w") as f:
                json.dump(alts, f, indent=2, sort_keys=True)
        except Exception as e:
            log(f"could not write {ALT_FILE}: {e}")
    log(f"done: {made} generated, {api_calls} API calls, neurons {neuron_total(used)}")
    return made


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    dry_run = "--dry-run" in argv
    if os.environ.get("IMAGE_GEN_ENABLED", "").strip().lower() != "true":
        log("IMAGE_GEN_ENABLED is not \"true\": no images generated, 0 API calls")
        return 0
    try:
        run(dry_run=dry_run)
    except Exception as e:  # never fail the pipeline
        log(f"unexpected error, continuing with no images: {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
