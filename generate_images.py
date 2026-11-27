#!/usr/bin/env python3
"""AI editorial images for every Daily Edge AI card. Off unless IMAGE_GEN_ENABLED is "true".

For every card on the Case Studies and Industry News tabs (archive tabs get none), Groq writes a scene description from the title and summary,
and FLUX.1-schnell (Apache-2.0) on Cloudflare Workers AI draws it in one house style. Each image is saved as site/images/<slug>.webp;
site/images/alt.json keeps the scene description (the alt text) and the date it was made.
A card keeps its image until prune; a run draws at most MAX_NEW_PER_RUN new ones, newest cards first.
Never fails the pipeline: the first error is logged, the run stops (no more API calls) and cards keep no image.
No text in images: the scene is built from shapes, light, materials, architecture and nature only, a scene with a
banned word is asked for once more and then replaced by a safe fallback, and every image is read by OCR (tesseract).
An image with text is drawn once more from a new scene; if it still has text it is deleted ("skipped: text").
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
HOUSE_STYLE = "clean editorial 3D illustration, soft studio light, near-black and emerald #10b981 accents, minimal, no text"
RULES = ("no text, no letters, no numbers, no digits, no logos, no people, no faces, no hands, no brands, "
         "no watermark, not a photograph")  # the model takes no negative prompt
WIDTH, HEIGHT = 1200, 675
STEPS = 4  # flux-1-schnell default; the model returns a square image, fitted to 1200x675
QUALITY = 70
MAX_NEW_PER_RUN = 25  # 2 runs a day x 25 x ~173 neurons stays under Cloudflare's free 10,000 neurons a day
MAX_AGE_DAYS = 120  # 4 months, the site's window
FALLBACK_ALT = "Abstract editorial illustration"
# objects that carry text or digits; a scene that names one is asked for once more, then replaced by a fallback
BANNED_WORDS = {
    "code", "digit", "digits", "number", "numbers", "numeral", "binary", "text", "letter", "letters", "word", "words",
    "screen", "monitor", "display", "phone", "smartphone", "camera", "lens", "lenses", "sign", "signage", "signpost",
    "label", "tag", "book", "notebook", "handbook", "page", "paper", "document", "newspaper", "magazine", "poster",
    "billboard", "banner", "chart", "graph", "dashboard", "keyboard", "laptop", "computer", "tablet", "terminal",
    "logo", "brand", "barcode", "qr", "map", "calendar", "clock", "scroll", "note", "envelope", "stamp", "coin",
    "badge", "ticket", "receipt", "menu", "plaque", "certificate", "typewriter", "whiteboard", "blackboard",
    "chalkboard", "spreadsheet", "server", "hologram", "interface", "scale", "flag", "card", "packaging", "license",
}
FALLBACK_SCENES = (
    "smooth emerald glass spheres resting on dark stone steps under a single soft beam of light",
    "a curved concrete archway opening onto calm misty hills at dawn",
    "folded ribbons of brushed metal forming a wave above still dark water",
    "a lone tree of pale crystal on a black basalt plateau under a soft green glow",
)
OCR_MIN_CONF = 60  # tesseract confidence, 0-100
OCR_MIN_LETTERS = 3  # a word of 3+ letters at OCR_MIN_CONF or more counts as text

api_calls = 0  # every Groq or Cloudflare request adds 1


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


def scene_prompt(item, avoid=""):
    names = names_in(item)
    title = strip_names(str(item.get("title", "")), names)
    summary = strip_names(str(item.get("summary", ""))[:600], names)
    retry = f"\nYour earlier answer was rejected: {avoid}. Write a different scene.\n" if avoid else ""
    return f"""Describe ONE scene for an editorial illustration, in one sentence of at most 30 words.
Topic: {title}
Summary: {summary}
Rules: build the scene ONLY from abstract shapes, light, materials (glass, stone, metal, wood, water, fabric),
architecture and nature, as a calm visual metaphor for the story (for a story about searching for planets: a
glowing sphere above a dark mountain lake; about a chip ban: a small emerald crystal behind a tall stone gate).
Never show: code, digits, numbers, binary, screens, monitors, phones, cameras, lenses, signs, labels, tags, books,
paper, documents, charts, keyboards, computers, servers, logos, maps, clocks, coins, stamps, posters, flags, or any
other object that normally carries text. No people, no faces, no hands. No company, product or person names.
No text, letters, numbers or digits. Not a photo of a real event.{retry}
Answer with the sentence only."""


def ask_groq(prompt):
    """One Groq call through the existing client (with its key fallback). Raises on no answer."""
    global api_calls
    from generate_breakdowns import call_groq
    api_calls += 1
    raw = call_groq(prompt)
    if not raw or raw == "DAILY_LIMIT":
        raise RuntimeError(f"Groq returned {raw!r}")
    return raw


def banned_in(scene):
    """Banned words (and digits) the scene names; plural forms count."""
    found = {"digits"} if re.search(r"\d", scene) else set()
    for word in re.findall(r"[a-z]+", scene.lower()):
        stems = {word, word[:-1] if word.endswith("s") else word, word[:-2] if word.endswith("es") else word}
        found |= stems & BANNED_WORDS
    return sorted(found)


def fallback_scene(item):
    """A safe scene with no text-carrying object, picked by slug so cards do not all share one."""
    slug = slugify(item.get("title", ""))
    return FALLBACK_SCENES[sum(map(ord, slug)) % len(FALLBACK_SCENES)]


def describe_scene(item, avoid=""):
    """Groq's scene for the card. A scene with a banned word is asked for once more; then a safe fallback."""
    for attempt in (1, 2):
        raw = ask_groq(scene_prompt(item, avoid))
        scene = strip_names(raw.strip().strip('"').splitlines()[0], names_in(item))[:300]
        if not scene:
            raise RuntimeError("empty scene description")
        bad = banned_in(scene)
        if not bad:
            return scene
        log(f"scene attempt {attempt} has banned word(s) {', '.join(bad)}: {scene}")
        avoid = f"it named {', '.join(bad)}, objects that carry text"
    log("using a safe fallback scene")
    return fallback_scene(item)


def ocr_ready():
    """True when pytesseract and the tesseract binary are installed."""
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def text_in_image(path):
    """Words of OCR_MIN_LETTERS+ letters that tesseract reads at OCR_MIN_CONF+ confidence; [] when clean.
    Reads grayscale, 2x upscaled and inverted copies with page modes 3 (blocks) and 11 (sparse text)."""
    import pytesseract
    from PIL import Image, ImageOps
    gray = ImageOps.grayscale(Image.open(path))
    big = gray.resize((gray.width * 2, gray.height * 2))
    for img in (gray, big, ImageOps.invert(big)):
        for psm in (3, 11):
            data = pytesseract.image_to_data(img, config=f"--psm {psm}", output_type=pytesseract.Output.DICT)
            words = [w for w, c in zip(data["text"], data["conf"])
                     if float(c) >= OCR_MIN_CONF and len(re.sub(r"[^A-Za-z]", "", w)) >= OCR_MIN_LETTERS]
            if words:
                return words
    return []


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
                         json={"prompt": f"{scene}. {HOUSE_STYLE}, {RULES}"[:2048], "steps": STEPS}, timeout=120)
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
    if not dry_run and not ocr_ready():
        # no OCR means no text check: draw nothing rather than publish images that may hold text
        log("OCR not available (pip install pytesseract; apt install tesseract-ocr). No images generated, 0 API calls")
        return 0
    made, regenerated, skipped, used = 0, 0, 0, []
    todo, seen = [], set()
    for item in newest_first(items):
        slug = slugify(item.get("title", ""))
        if not slug or slug in seen:
            continue
        seen.add(slug)
        if (out / f"{slug}.webp").exists():
            log(f"skip (exists): {slug}")
            continue
        todo.append((slug, item))
    if len(todo) > MAX_NEW_PER_RUN:
        log(f"{len(todo)} cards need an image; this run draws {MAX_NEW_PER_RUN}, the next run the rest")
    for slug, item in todo[:MAX_NEW_PER_RUN]:
        path = out / f"{slug}.webp"
        if dry_run:
            log(f"dry run, would generate: {slug}")
            continue
        if len(used) >= MAX_NEW_PER_RUN:  # a redraw counts too, so the neuron budget holds
            log(f"{MAX_NEW_PER_RUN} draws used, the next run draws the rest")
            break
        try:
            scene = describe_scene(item)
            for attempt in (1, 2):
                neurons = generate_image(scene, path)
                used.append(neurons)
                try:
                    text = text_in_image(path)
                except Exception as e:  # an image that cannot be read is not trusted
                    text = [f"OCR error {type(e).__name__}"]
                if not text:
                    break
                path.unlink(missing_ok=True)
                log(f"text in {slug} (attempt {attempt}): {', '.join(map(str, text[:5]))}")
                if attempt == 2 or len(used) >= MAX_NEW_PER_RUN:
                    break
                regenerated += 1
                scene = describe_scene(item, avoid=f"the image of '{scene}' showed text")
            if text:
                skipped += 1
                log(f"skipped: text ({slug})")
                continue
            alts[slug] = {"alt": scene, "created": today.strftime("%Y-%m-%d"), "model": MODEL,
                          "provider": "cloudflare-workers-ai", "neurons": neurons}
            made += 1
            log(f"generated {slug} ({path.stat().st_size / 1024:.1f} KB, neurons {neurons}): {scene}")
        except Exception as e:
            # stop the whole run: no more Groq or Cloudflare calls after the first error
            log(f"error for {slug}, stopping the run, cards keep no image: {type(e).__name__}: {e}")
            break
    if not dry_run:
        try:
            with open(out / ALT_FILE, "w") as f:
                json.dump(alts, f, indent=2, sort_keys=True)
        except Exception as e:
            log(f"could not write {ALT_FILE}: {e}")
    log(f"done: {made} generated, {regenerated} regenerated, {skipped} skipped (text), {len(used)} draws, "
        f"{api_calls} API calls, neurons {neuron_total(used)}")
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
