"""Scout Daily email header: one Groq call for the words, header/template.html for the look, header/gates.py for the checks.

email_header(top, date) returns PNG bytes, or None with the reason printed. It never raises: a failed gate, a Groq
error or a missing browser sends the email without a header.

Samples from the 3 newest issues: python -m header.render_header
"""
import html
import json
import os
from datetime import datetime
from pathlib import Path

from header import gates

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "template.html"
MODEL = "openai/gpt-oss-20b"  # the Groq model Scout Daily uses (industry_news_scout_langgraph.py groq_complete)
LOGO = 'Scout Daily<span class="cursor"></span>'  # the site wordmark; the repo has no logo file
KICKER_MAX = 24
_DANGLING = {"a", "an", "and", "as", "at", "but", "by", "for", "from", "her", "his", "in", "into", "its", "of", "on",
             "or", "our", "than", "that", "the", "their", "this", "to", "with", "your"}

# Reads every [data-slot]: computed colour, the first opaque background behind it, and its rendered line count.
_MEASURE = """() => [...document.querySelectorAll('[data-slot]')].map(el => {
  const cs = getComputedStyle(el);
  let node = el, bg = null;
  while (node && !bg) { const c = getComputedStyle(node).backgroundColor; if (c !== 'rgba(0, 0, 0, 0)') bg = c; node = node.parentElement; }
  return {slot: el.dataset.slot, color: cs.color, bg: bg || 'rgb(255, 255, 255)',
          lines: Math.round(el.getBoundingClientRect().height / parseFloat(cs.lineHeight))};
})"""


def cut(text, limit):
    """Trim to limit chars at the last word boundary; one word longer than limit is cut hard."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    head = text[:limit + 1].rsplit(" ", 1)[0]
    words_ = (head if len(head) <= limit else text[:limit]).rstrip(" ,;:-–—").split(" ")
    while len(words_) > 1 and words_[-1].lower() in _DANGLING:  # "...for $7.5B as" -> "...for $7.5B"
        words_.pop()
    return " ".join(words_).rstrip(" ,;:-–—")


def _fallback(top):
    cat = (top.get("category") or "").strip()
    return cut(cat if cat and cat.lower() != "other" else "Top story", KICKER_MAX), cut(top.get("title"), gates.TITLE_MAX)


def groq_json(prompt):
    """ONE Groq call (no retries, no second key). Raises on any failure; words() falls back."""
    from groq import Groq
    key = os.getenv("GROQ_API_KEY")
    if not key:
        raise RuntimeError("GROQ_API_KEY not set")
    resp = Groq(api_key=key, max_retries=0, timeout=30).chat.completions.create(
        model=MODEL, messages=[{"role": "user", "content": prompt}], max_tokens=300,
        reasoning_effort="low", reasoning_format="hidden", response_format={"type": "json_object"})
    return resp.choices[0].message.content


def words(top, complete=groq_json):
    """(kicker, title, source) for the top story. source is "groq" or "fallback"."""
    prompt = ("Write the words for an email header image about this news story. Reply with JSON only, in this shape: "
              '{"kicker": "a 1-3 word topic label, at most 20 characters", '
              '"title": "a plain, factual headline, at most 50 characters"}. Use only facts from the story.\n\n'
              f"Headline: {top.get('title', '')}\nSummary: {str(top.get('summary') or '')[:600]}")
    try:
        d = json.loads(complete(prompt))
        kicker, title = " ".join(str(d["kicker"]).split()), " ".join(str(d["title"]).split())
        if 0 < len(kicker) <= KICKER_MAX and 0 < len(title) <= gates.TITLE_MAX:
            return kicker, title, "groq"
        print(f"[HEADER] Groq reply out of limits (kicker {len(kicker)}, title {len(title)} chars), using the headline.")
    except Exception as e:  # a bad reply or a failed call never stops the header
        print(f"[HEADER] Groq call failed, using the headline: {type(e).__name__}: {e}")
    return (*_fallback(top), "fallback")


def fill(kicker, title, date):
    page = TEMPLATE.read_text(encoding="utf-8")
    for slot, value in (("kicker", kicker), ("title", title), ("date", date)):
        # &num; so a "#" plus digits in a headline is text, not a colour, to colour_gate.
        page = page.replace("{{" + slot + "}}", html.escape(value).replace("#", "&num;"))
    return page.replace("{{logo}}", LOGO)


def render(page_html):
    """(png bytes, measurements) from headless Chromium at 1200x630, scale 1, after the web fonts load."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": gates.SIZE[0], "height": gates.SIZE[1]}, device_scale_factor=1)
            page.set_content(page_html, wait_until="networkidle", timeout=30000)
            page.evaluate("document.fonts.ready.then(() => true)")
            return page.screenshot(type="png"), page.evaluate(_MEASURE)
        finally:
            browser.close()


def email_header(top, date, complete=groq_json, render_fn=render):
    """PNG bytes, or None with the reason printed. Never raises."""
    try:
        kicker, title, source = words(top, complete)
        page = fill(kicker, title, date)
        problems = gates.check_html(page, title)
        if not problems:
            png, measured = render_fn(page)
            problems = gates.check_render(measured, png)
        if problems:
            print(f"[HEADER] No header: {'; '.join(problems)}")
            return None
        print(f"[HEADER] OK ({source}): {kicker} | {title} | {len(png)} bytes")
        return png
    except Exception as e:
        print(f"[HEADER] No header: {type(e).__name__}: {e}")
        return None


def samples(n=3, out=HERE / "samples"):
    """One header per newest issue, from the same top story the email leads with."""
    from send_daily_email import NEWS_ARCHIVE, _score
    out.mkdir(exist_ok=True)
    with open(NEWS_ARCHIVE, encoding="utf-8") as f:
        days = json.load(f)[:n]
    for day in days:
        top = max(day["news"], key=lambda item: _score(item, "relevance_score"))
        png = email_header(top, datetime.strptime(day["date"], "%Y-%m-%d").strftime("%a %d %b %Y"))
        if png:
            (out / f"{day['date']}.png").write_bytes(png)


if __name__ == "__main__":
    samples()
