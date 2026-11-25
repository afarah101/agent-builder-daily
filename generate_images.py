#!/usr/bin/env python3
"""AI editorial images for the top Scout Daily cards. Off unless IMAGE_GEN_ENABLED is "true".

For the top 5 case studies and top 5 news items, Groq writes a scene description from the title and summary,
and FLUX.1-schnell (Apache-2.0) draws it in one house style. Each image is saved as site/images/<slug>.webp;
site/images/alt.json keeps the scene description (the alt text) and the date it was made.
Never fails the pipeline: any error is logged and the card simply has no image.
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

IMAGES_DIR = "site/images"
ALT_FILE = "alt.json"
MODEL = "black-forest-labs/FLUX.1-schnell"  # Apache-2.0. Never FLUX.1-dev.
HOUSE_STYLE = "clean editorial 3D illustration, soft studio light, near-black and indigo #4f46e5 accents, minimal, no text"
NEGATIVE = "people, person, face, hands, crowd, logo, brand, text, letters, words, watermark, photo, photograph"
WIDTH, HEIGHT = 1200, 675
GEN_HEIGHT = 672  # FLUX works in multiples of 16; the result is fitted to 1200x675
QUALITY = 70
TOP_N = 5
MAX_AGE_DAYS = 120  # 4 months, the site's window
FALLBACK_ALT = "Abstract editorial illustration"

api_calls = 0  # every Groq or Hugging Face request adds 1


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


def scene_prompt(item):
    names = names_in(item)
    title = strip_names(str(item.get("title", "")), names)
    summary = strip_names(str(item.get("summary", ""))[:600], names)
    return f"""Describe ONE scene for an editorial illustration, in one sentence of at most 30 words.
Topic: {title}
Summary: {summary}
Rules: an abstract or symbolic scene of objects, shapes or machines only. No people, no faces, no hands.
No company, product or person names. No logos, brands, text, letters or numbers. Not a photo of a real event.
Answer with the sentence only."""


def describe_scene(item):
    global api_calls
    from generate_breakdowns import call_groq  # the existing Groq client, with its key fallback
    api_calls += 1
    raw = call_groq(scene_prompt(item))
    if not raw or raw == "DAILY_LIMIT":
        raise RuntimeError(f"Groq returned {raw!r}")
    scene = strip_names(raw.strip().strip('"').splitlines()[0], names_in(item))
    if not scene:
        raise RuntimeError("empty scene description")
    return scene[:300]


def generate_image(scene, out_path):
    """Draw the scene with FLUX.1-schnell and save it as WebP at quality 70. Returns the provider name."""
    global api_calls
    from huggingface_hub import InferenceClient
    from PIL import ImageOps
    token = os.environ.get("HF_TOKEN")
    if not token:
        raise RuntimeError("HF_TOKEN is not set")
    provider = os.environ.get("HF_PROVIDER", "auto")
    client = InferenceClient(provider=provider, api_key=token)
    api_calls += 1
    image = client.text_to_image(f"{scene}. {HOUSE_STYLE}", model=MODEL, negative_prompt=NEGATIVE,
                                 width=WIDTH, height=GEN_HEIGHT)
    image = ImageOps.fit(image.convert("RGB"), (WIDTH, HEIGHT))
    tmp = out_path.with_suffix(".tmp.webp")
    image.save(tmp, "WEBP", quality=QUALITY, method=6)
    tmp.replace(out_path)
    return provider


def top_items():
    """Top 5 case studies and top 5 news items by the scores the page sorts by. Reads only: archives nothing."""
    import generate_html as gh
    cutoff = (datetime.now() - timedelta(days=gh.CASE_STUDY_WINDOW_DAYS)).strftime("%Y-%m-%d")
    studies = []
    for f in sorted(Path(gh.CASE_STUDIES_DIR).glob("*.json")):
        if "archive" in f.name:
            continue
        data = gh.load_json(str(f))
        studies += (data.get("case_studies") or []) if isinstance(data, dict) else []
    studies = sorted((s for s in studies if gh.in_window(s, cutoff)), key=lambda x: gh.by_score(x, "total_score"), reverse=True)
    news_files = sorted(Path(gh.NEWS_DIR).glob("news_*.json"))
    news_data = gh.load_json(str(news_files[-1])) if news_files else {}
    news = sorted((news_data or {}).get("news") or [], key=lambda x: gh.by_score(x, "relevance_score"), reverse=True)
    return studies[:TOP_N] + news[:TOP_N]


def prune(images_dir, alts, today):
    """Delete images made more than 4 months ago (date from alt.json; file age only when no date is stored)."""
    cutoff = today - timedelta(days=MAX_AGE_DAYS)
    for path in Path(images_dir).glob("*.webp"):
        slug = path.stem
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
        prune(images_dir, alts, today)
    except Exception as e:
        log(f"prune error: {e}")
    try:
        items = top_items()
    except Exception as e:
        log(f"could not load stories: {e}")
        items = []
    made = 0
    for item in items:
        slug = slugify(item.get("title", ""))
        if not slug:
            continue
        path = out / f"{slug}.webp"
        if path.exists():
            log(f"skip (exists): {slug}")
            continue
        if dry_run:
            log(f"dry run, would generate: {slug}")
            continue
        try:
            scene = describe_scene(item)
            provider = generate_image(scene, path)
            alts[slug] = {"alt": scene, "created": today.strftime("%Y-%m-%d"), "model": MODEL, "provider": provider}
            made += 1
            log(f"generated {slug} ({path.stat().st_size // 1024} KB, provider {provider}): {scene}")
        except Exception as e:
            log(f"error for {slug}, card keeps no image: {type(e).__name__}: {e}")
    if not dry_run:
        try:
            with open(out / ALT_FILE, "w") as f:
                json.dump(alts, f, indent=2, sort_keys=True)
        except Exception as e:
            log(f"could not write {ALT_FILE}: {e}")
    log(f"done: {made} generated, {api_calls} API calls")
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
