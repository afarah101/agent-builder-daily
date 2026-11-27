#!/usr/bin/env python3
"""AI editorial images for every Daily Edge AI card. Off unless IMAGE_GEN_ENABLED is "true".

For every card on the Case Studies and Industry News tabs (archive tabs get none), Groq writes a still-life scene from the title and
summary, and FLUX.1-schnell (Apache-2.0) on Cloudflare Workers AI draws it with one fixed house-style suffix. Each image is saved as
site/images/<slug>.webp; site/images/alt.json keeps the scene (the alt text), the date and the style it was made in.

No fake writing: FLUX ignores "no text", so the scene may not name a thing that carries writing (BANNED), and OCR reads every
new image. An image that shows text is drawn once more from a new scene; if that one shows text too, the card keeps its cover.
A card keeps a good image until prune; a run makes at most MAX_NEW_PER_RUN drawings, cards the page shows first, newest first.

Never blank: a card with no AI image shows a cover, site/images/covers/<slug>.webp, drawn here with Pillow from the slug
(abstract shapes, no text, 0 API calls). Every run makes the missing covers, also when IMAGE_GEN_ENABLED is off.
image_figure gives every card a figure: the AI image, else the cover, else (no cover file yet) the same idea as inline SVG.

Spend cap (Workers Paid): site/images/budget.json keeps the neurons used on each UTC day, across all runs. No drawing starts
when it would take the day above DAILY_NEURON_BUDGET: the 10,000 free neurons plus DAILY_SPEND_CAP_USD at $0.011 per 1,000.
Every run logs the day's neurons and the estimated cost in USD.
Never fails the pipeline: the first API error is logged, the run stops (no more API calls) and that card keeps its cover.
A Cloudflare 429 or 5xx stops that run only: the next run draws again, and the card keeps its cover until then.
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

IMAGES_DIR = "site/images"
ALT_FILE = "alt.json"
MODEL = "@cf/black-forest-labs/flux-1-schnell"  # Apache-2.0, on Cloudflare Workers AI. Never FLUX.1-dev.
CF_URL = "https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}"
CF_SECRETS = ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID")
# The fixed suffix on every image prompt. No "no text" list: the model takes no negative prompt, and naming text invites it.
HOUSE_STYLE = ("minimal still life, soft studio light, matte materials, shallow depth of field, neutral grey background, "
               "editorial magazine photo, single emerald #10b981 accent only, no orange, no copper, no warm metals")
STYLE_ID = "still-life-1"  # stored in alt.json; an image made in another style is redrawn, so the cards look like one series

# Things that carry writing in real life, and people. FLUX draws fake letters on them, so a scene may not name one.
# Plain objects are not here (paper, an envelope, a folder, a phone lying face-down, a cup): OCR checks every drawing.
# Whole words, plural included; "_" stands for a space.
BANNED = {
    "landmarks": "landmark monument tower bridge skyline cathedral temple castle palace lighthouse statue eiffel colosseum "
                 "london paris tokyo manhattan big_ben golden_gate taj_mahal times_square new_york",
    "buildings": "building skyscraper office factory factories warehouse house room hall laboratory laboratories lab library "
                 "libraries shop store storefront hospital school church station airport datacenter data_center city "
                 "cities town village architecture facade",
    "streets": "street road highway avenue alley sidewalk pavement intersection crosswalk traffic plaza",
    "screens": "screen monitor display laptop computer desktop tablet tv television terminal dashboard "
               "interface ui keyboard cursor icon browser app website",
    "signs": "sign signage signpost billboard poster banner placard neon plaque nameplate marquee license_plate",
    "documents": "document page note letter ledger handbook contract certificate receipt invoice newspaper "
                 "magazine scroll manuscript notebook notepad whiteboard blackboard chalkboard postcard ticket card "
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
MAX_NEW_PER_RUN = 120  # drawings in one run: a full backfill of the page in one run; the day's limit is DAILY_NEURON_BUDGET
MAX_AGE_DAYS = 120  # 4 months, the site's window
FALLBACK_ALT = "Abstract editorial illustration"

# The day's spend cap on Cloudflare Workers Paid. site/images/budget.json keeps {UTC date: neurons used}; all runs of a
# day share it. A cap on spend, not on images: about 580 drawings fit in one day.
BUDGET_FILE = "budget.json"
DAILY_SPEND_CAP_USD = 1.00  # the most one UTC day of drawings may cost
USD_PER_1000_NEURONS = 0.011  # the Workers AI price above the free neurons
FREE_DAILY_NEURONS = 10000  # included in every UTC day at no cost
DAILY_NEURON_BUDGET = 100909  # 10,000 free + 1.00 / 0.011 x 1000. No drawing starts when it would take the day above this
NEURONS_PER_DRAWING = 175  # counted for a drawing when Cloudflare reports no number (a drawing uses about 173)
BUDGET_KEEP_DAYS = 30  # older days leave budget.json

# The cover of a card with no AI image: site/images/covers/<slug>.webp, drawn with Pillow. No text, no API call.
COVERS_DIR = "covers"
COVER_BG = "#0a0a0a"
COVER_EMERALD = "#10b981"
COVER_GREYS = ("#262626", "#525252")

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


def cover_slug(title):
    """The file name of a card's cover: the card's slug, or a hash when the title has no letter a slug keeps."""
    import hashlib
    return slugify(title) or "untitled-" + hashlib.sha256(str(title or "").encode("utf-8")).hexdigest()[:10]


def _rect(cx, cy, w, h, deg):
    """Corner points of a w x h rectangle centred on (cx, cy), turned by deg degrees."""
    import math
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [(cx + x * c - y * s, cy + x * s + y * c) for x, y in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2))]


def cover_image(slug):
    """The cover of a slug, always the same picture for the same slug: on near-black, one large dark-grey form, one
    mid-grey band or corner, one emerald accent. Every form leaves the frame, so OCR reads no letter in it
    (a closed form that stands alone reads as "O")."""
    import hashlib, random
    from PIL import Image, ImageDraw
    rng = random.Random(int(hashlib.sha256(str(slug).encode("utf-8")).hexdigest(), 16))
    k = 2  # drawn at twice the size, then scaled down: smooth edges
    w, h = WIDTH * k, HEIGHT * k
    image = Image.new("RGB", (w, h), COVER_BG)
    draw = ImageDraw.Draw(image)
    dark, mid = COVER_GREYS
    # 1. one large dark-grey form, on the left or the right
    side = rng.choice(("left", "right"))
    cx = rng.uniform(0.0, 0.3) * w if side == "left" else rng.uniform(0.7, 1.0) * w
    cy = rng.uniform(0.15, 0.85) * h
    r = rng.uniform(0.42, 0.62) * h
    if rng.random() < 0.6:
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=dark)
    else:
        draw.polygon(_rect(cx, cy, r * 2.2, r * 1.5, rng.uniform(-35, 35)), fill=dark)
    # 2. one mid-grey band from edge to edge, or a mid-grey triangle in a far corner
    if rng.random() < 0.5:
        draw.polygon(_rect(rng.uniform(0.3, 0.7) * w, rng.uniform(0.3, 0.7) * h, w * 1.6, rng.uniform(0.06, 0.16) * h,
                           rng.choice((-1, 1)) * rng.uniform(12, 40)), fill=mid)
    else:
        x0, y0 = (w if side == "left" else 0), rng.choice((0, h))
        draw.polygon([(x0, y0), (x0 + (-1 if x0 else 1) * rng.uniform(0.3, 0.55) * w, y0),
                      (x0, y0 + (-1 if y0 else 1) * rng.uniform(0.5, 0.9) * h)], fill=mid)
    # 3. one emerald accent on the far side, cut by the frame edge
    far = (rng.uniform(0.55, 0.85) if side == "left" else rng.uniform(0.15, 0.45)) * w
    kind = rng.choice(("half-disc", "band", "corner"))
    if kind == "half-disc":  # a disc cut in half by the top or bottom edge
        ar, ay = rng.uniform(0.12, 0.2) * h, rng.choice((0, h))
        draw.ellipse((far - ar, ay - ar, far + ar, ay + ar), fill=COVER_EMERALD)
    elif kind == "band":  # a thin band from edge to edge
        draw.polygon(_rect(far, rng.uniform(0.3, 0.7) * h, w * 1.6, rng.uniform(0.012, 0.024) * h,
                           rng.choice((-1, 1)) * rng.uniform(50, 75)), fill=COVER_EMERALD)
    else:  # a triangle in a far corner
        x0, y0 = (w if side == "left" else 0), rng.choice((0, h))
        draw.polygon([(x0, y0), (x0 + (-1 if x0 else 1) * rng.uniform(0.1, 0.18) * w, y0),
                      (x0, y0 + (-1 if y0 else 1) * rng.uniform(0.18, 0.32) * h)], fill=COVER_EMERALD)
    return image.resize((WIDTH, HEIGHT), Image.LANCZOS)


def make_cover(slug, images_dir=IMAGES_DIR):
    """Draw and save site/images/covers/<slug>.webp when it is not there. Returns its path. No API call."""
    path = Path(images_dir) / COVERS_DIR / f"{slug}.webp"
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp.webp")
        cover_image(slug).save(tmp, "WEBP", quality=QUALITY, method=6)
        tmp.replace(path)
    return path


def inline_cover(slug):
    """A cover as a data: URI of a small SVG, for a card whose cover file is not made yet. No file, no API call."""
    from urllib.parse import quote
    x = 200 + int.from_bytes(str(slug).encode("utf-8")[:2] or b"a", "big") % 800
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}"><rect width="{WIDTH}" height="{HEIGHT}" '
           f'fill="{COVER_BG}"/><circle cx="{WIDTH - x}" cy="{HEIGHT}" r="420" fill="{COVER_GREYS[0]}"/>'
           f'<path d="M0 0h420L0 480z" fill="{COVER_GREYS[1]}"/><circle cx="{x}" cy="0" r="110" fill="{COVER_EMERALD}"/></svg>')
    return "data:image/svg+xml," + quote(svg)


def image_figure(title, images_dir=IMAGES_DIR):
    """The card's <figure>, on every card. The AI image when site/images/<slug>.webp exists (alt: the scene, caption
    "AI illustration"); else the card's cover, site/images/covers/<slug>.webp; else an inline cover.
    A cover is decoration: alt="" and no caption. Reads only: sync_covers makes the cover files."""
    import html as html_lib
    slug, out = slugify(title), Path(images_dir)
    if slug and (out / f"{slug}.webp").is_file():
        alt = str((load_alts(images_dir).get(slug) or {}).get("alt") or "").strip() or FALLBACK_ALT
        return (f'<figure class="card-figure"><img src="images/{slug}.webp" loading="lazy" width="{WIDTH}" '
                f'height="{HEIGHT}" alt="{html_lib.escape(alt)}"><figcaption>AI illustration</figcaption></figure>')
    slug = cover_slug(title)
    src = f"images/{COVERS_DIR}/{slug}.webp" if (out / COVERS_DIR / f"{slug}.webp").is_file() else inline_cover(slug)
    return (f'<figure class="card-figure"><img src="{src}" loading="lazy" width="{WIDTH}" '
            f'height="{HEIGHT}" alt=""></figure>')


def sync_covers(items, images_dir=IMAGES_DIR):
    """Make the cover of every shown card that has no AI image, and delete the covers no shown card uses.
    Returns how many covers were made. No API call."""
    out, made, shown = Path(images_dir), 0, set()
    for item in items:
        title = item.get("title", "")
        shown.add(cover_slug(title))
        slug = slugify(title)
        if slug and (out / f"{slug}.webp").is_file():
            continue  # the AI image is the figure
        if not (out / COVERS_DIR / f"{cover_slug(title)}.webp").is_file():
            make_cover(cover_slug(title), images_dir)
            made += 1
    gone = prune_covers(images_dir, shown)
    log(f"covers: {made} made, {gone} deleted, {len(list((out / COVERS_DIR).glob('*.webp')))} in use, 0 API calls")
    return made


def prune_covers(images_dir, shown):
    """Delete the covers no shown card uses: the card left the page, or it has an AI image now. Returns how many."""
    out, gone = Path(images_dir), 0
    for path in (out / COVERS_DIR).glob("*.webp"):
        if path.stem not in shown or (out / f"{path.stem}.webp").is_file():
            path.unlink()
            gone += 1
    return gone


class Outage(RuntimeError):
    """Cloudflare answered HTTP 429 or 5xx: it drew nothing. This run stops; the next run draws again."""


def cost_usd(neurons):
    """The estimated cost in USD of one UTC day's neurons: the first 10,000 are free, the rest cost $0.011 per 1,000."""
    return max(0.0, float(neurons) - FREE_DAILY_NEURONS) / 1000 * USD_PER_1000_NEURONS


class Budget:
    """The neurons used on each UTC day, in site/images/budget.json as {"YYYY-MM-DD": neurons}. Saved after every
    change, so every run of the day counts toward the same total, and a run that stops early loses no count."""

    def __init__(self, images_dir, day):
        self.path, self.day = Path(images_dir) / BUDGET_FILE, day
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            data = {}
        self.days = {str(k): float(v) for k, v in data.items() if isinstance(v, (int, float))} if isinstance(data, dict) else {}

    @property
    def used(self):
        return self.days.get(self.day, 0.0)

    def can_draw(self):
        """True while one more drawing keeps the day's total within DAILY_NEURON_BUDGET."""
        return self.used + NEURONS_PER_DRAWING <= DAILY_NEURON_BUDGET

    def add(self, usage=None):
        """Count one drawing: the neurons Cloudflare reported, or NEURONS_PER_DRAWING when it reported none."""
        self.days[self.day] = round(self.used + neurons_of(usage), 1)
        self.save()

    def status(self):
        """The day's neurons and their estimated cost, for the log."""
        return (f"{self.used:g} of {DAILY_NEURON_BUDGET} neurons, estimated cost ${cost_usd(self.used):.2f} of the "
                f"${DAILY_SPEND_CAP_USD:.2f} cap, on {self.day} UTC")

    def save(self):
        first = (datetime.strptime(self.day, "%Y-%m-%d") - timedelta(days=BUDGET_KEEP_DAYS)).strftime("%Y-%m-%d")
        self.days = {d: n for d, n in self.days.items() if d >= first}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.days, indent=2, sort_keys=True) + "\n")
        except OSError as e:
            log(f"could not write {BUDGET_FILE}: {e}")


def neurons_of(usage):
    """The neurons of one drawing, from what generate_image returned. NEURONS_PER_DRAWING when no number is there."""
    try:
        data = json.loads(usage)
        found = [float(v) for k, v in data.items() if "neuron" in k.lower()]
        if isinstance(data.get("usage"), dict) and "neurons" in data["usage"]:
            found.append(float(data["usage"]["neurons"]))
    except (TypeError, ValueError, AttributeError):
        found = []
    return max(found) if found and max(found) > 0 else float(NEURONS_PER_DRAWING)


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
    """The banned things a scene names, lower case, in the order they appear. Empty: the scene may be drawn.
    "face-down" names no face: a phone lying face-down shows its blank back."""
    scene = re.sub(r"\bface[- ]down\b", " ", str(scene or ""), flags=re.IGNORECASE)
    return list(dict.fromkeys(m.group(0).lower() for m in _BANNED_RE.finditer(scene)))


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
Plain objects with blank surfaces are fine: a sheet of blank paper, a phone lying face-down, a cup.
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
    if resp.status_code == 429 or resp.status_code >= 500:
        raise Outage(f"Cloudflare HTTP {resp.status_code}: {body.get('errors') or resp.text[:200]}")
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


def draw_clean(item, path, draws_left, budget):
    """Draw the card's image; if OCR reads text on it, draw it once more from a new scene. Every drawing is counted in
    the day's budget, and none starts when the budget has no room for it.
    Returns (scene, neurons of each drawing). scene is None when no clean image was made: the file is then gone."""
    scene, used = None, []
    for attempt in (1, 2):
        if len(used) >= draws_left or not budget.can_draw():
            break
        scene = describe_scene(item) if attempt == 1 else describe_scene(item, avoid=scene)
        try:
            usage = generate_image(scene, path)
        except Outage:
            raise  # Cloudflare drew nothing: no neurons to count
        except Exception:
            budget.add()  # the call can have used neurons before it failed: count one drawing
            raise
        budget.add(usage)
        used.append(usage)
        found = text_found(path)
        if len(found) < TEXT_LIMIT:
            return scene, used
        path.unlink(missing_ok=True)  # the cover is better than an image with fake writing
        log(f"text on {path.stem}, try {attempt} ({found[:40]!r}): {scene}")
    return None, used


def run(dry_run=False, images_dir=IMAGES_DIR, today=None):
    today = today or datetime.now(timezone.utc).replace(tzinfo=None)  # UTC: Cloudflare's free neurons reset at 00:00 UTC
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
    budget = Budget(images_dir, today.strftime("%Y-%m-%d"))
    if todo and not budget.can_draw():
        # checked before any Groq call: a day with no room left makes 0 API calls
        log(f"daily spend cap reached ({budget.status()}): {len(todo)} cards keep their cover, 0 API calls")
        todo = []
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
        if len(used) >= MAX_NEW_PER_RUN:  # every drawing counts, a second try too
            break
        if not dry_run and not budget.can_draw():
            log(f"daily spend cap reached ({budget.status()}): no more drawings today, the other cards keep their cover")
            break
        path = out / f"{slug}.webp"
        if dry_run:
            log(f"dry run, would generate: {slug}")
            used.append(None)  # counts toward the run's limit, as a real drawing does
            continue
        try:
            scene, neurons = draw_clean(item, path, MAX_NEW_PER_RUN - len(used), budget)
            used += neurons
            if scene is None:
                alts.pop(slug, None)
                log(f"no clean image for {slug}: the card keeps its cover this run")
                continue
            alts[slug] = {"alt": scene, "created": today.strftime("%Y-%m-%d"), "model": MODEL,
                          "provider": "cloudflare-workers-ai", "neurons": neurons[-1], "style": STYLE_ID}
            made += 1
            (out / COVERS_DIR / f"{cover_slug(item.get('title', ''))}.webp").unlink(missing_ok=True)  # the AI image replaces the cover
            log(f"generated {slug} ({path.stat().st_size / 1024:.1f} KB, neurons {neurons[-1]}): {scene}")
        except Outage as e:
            # this run only: nothing is stored about the day, so the next run draws again
            log(f"Cloudflare did not draw {slug}, stopping this run, cards keep their cover until the next run: {e}")
            break
        except Exception as e:
            # stop the whole run: no more Groq or Cloudflare calls after the first error
            log(f"error for {slug}, stopping the run, cards keep their cover: {type(e).__name__}: {e}")
            break
    if not dry_run:
        for slug in [k for k in alts if not (out / f"{k}.webp").exists()]:
            del alts[slug]  # an entry with no file (image deleted or rejected) describes nothing
        try:
            with open(out / ALT_FILE, "w") as f:
                json.dump(alts, f, indent=2, sort_keys=True)
        except Exception as e:
            log(f"could not write {ALT_FILE}: {e}")
    log(f"done: {made} generated, {api_calls} API calls, neurons {neuron_total(used)}; day total {budget.status()}")
    return made


def main(argv=None, images_dir=IMAGES_DIR):
    argv = sys.argv[1:] if argv is None else argv
    dry_run = "--dry-run" in argv
    if os.environ.get("IMAGE_GEN_ENABLED", "").strip().lower() != "true":
        log("IMAGE_GEN_ENABLED is not \"true\": no images generated, 0 API calls")
    else:
        try:
            run(dry_run=dry_run, images_dir=images_dir)
        except Exception as e:  # never fail the pipeline
            log(f"unexpected error, continuing with no new images: {type(e).__name__}: {e}")
    try:
        # after the drawings, so a new AI image replaces its cover and a card with no AI image has one. Always runs.
        items = [] if dry_run else top_items()  # a dry run writes no file
        if items:  # no card at all is a load problem, not a reason to delete every cover
            sync_covers(items, images_dir)
    except Exception as e:
        log(f"covers not updated, cards show an inline cover: {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
