#!/usr/bin/env python3
"""AI editorial images for the top Daily Edge AI cards. Off unless IMAGE_GEN_ENABLED is "true".

For the lead case study and the lead news item (one per tab), Groq writes a scene description from the title and summary,
and FLUX.1-schnell (Apache-2.0) on Cloudflare Workers AI draws it in one house style. Each image is saved as site/images/<slug>.webp;
site/images/alt.json keeps the scene description (the alt text) and the date it was made.
Never fails the pipeline: the first error is logged, the run stops (no more API calls) and cards keep no image.
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
HOUSE_STYLE = "clean editorial 3D illustration, soft studio light, near-black and indigo #4f46e5 accents, minimal, no text"
RULES = ("no text, no letters, no numbers, no digits, no logos, no people, no faces, no hands, no brands, "
         "no watermark, not a photograph")  # the model takes no negative prompt
WIDTH, HEIGHT = 1200, 675
STEPS = 4  # flux-1-schnell default; the model returns a square image, fitted to 1200x675
QUALITY = 70
TOP_N = 1  # lead story of each tab only: the page shows an image on the first card alone
MAX_AGE_DAYS = 120  # 4 months, the site's window
FALLBACK_ALT = "Abstract editorial illustration"

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


def scene_prompt(item):
    names = names_in(item)
    title = strip_names(str(item.get("title", "")), names)
    summary = strip_names(str(item.get("summary", ""))[:600], names)
    return f"""Describe ONE scene for an editorial illustration, in one sentence of at most 30 words.
Topic: {title}
Summary: {summary}
Rules: show the story's own subject as one concrete, specific object or setting a reader would link to this topic
(for a story about searching for planets: a telescope under a starfield; about a chip ban: a single chip on a
customs scale). Never use generic filler: no gears, cubes, circuit boards, data streams, code, screens, charts or
holograms unless the story is about that exact thing. No people, no faces, no hands. No company, product or person
names. No logos, brands, text, letters, numbers or digits. Not a photo of a real event.
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


def top_items():
    """The lead case study and the lead news item by the scores the page sorts by. Reads only: archives nothing."""
    import generate_html as gh
    cutoff = (datetime.now() - timedelta(days=gh.CASE_STUDY_WINDOW_DAYS)).strftime("%Y-%m-%d")
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
    missing = [k for k in CF_SECRETS if not os.environ.get(k)]
    if not dry_run and missing:
        # checked once, before any Groq call, so a missing secret wastes no API calls
        log(f"{', '.join(missing)} not set: add under Settings > Secrets and variables > Actions. "
            "No images generated, 0 API calls")
        return 0
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
            neurons = generate_image(scene, path)
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
