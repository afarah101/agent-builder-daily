#!/usr/bin/env python3
import os, json, re, feedparser, requests, trafilatura
import ipaddress, socket, threading, time
from urllib.parse import urljoin, urlsplit
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from bs4 import BeautifulSoup
from typing import Annotated
from typing_extensions import TypedDict
from collections import defaultdict
import operator
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver
from groq import Groq
from dotenv import load_dotenv
load_dotenv()

os.environ["LANGCHAIN_TRACING_V2"] = os.getenv("LANGCHAIN_TRACING_V2", "")
os.environ["LANGCHAIN_API_KEY"] = os.getenv("LANGCHAIN_API_KEY", "")
os.environ["LANGCHAIN_PROJECT"] = os.getenv("LANGCHAIN_PROJECT", "")

from langsmith import Client
client = Client()

from langfuse import Langfuse, observe, propagate_attributes
langfuse = Langfuse()
import llm_cache
import quality_checks
import judge

_quality = {}
_judge = {}
_run = {}

OUTPUT_DIR = "data/news"
ARCHIVE_FILE = "data/news_archive.json"
MAX_ITEMS = 25
MAX_PER_SOURCE = 3
MIN_SNIPPET = 300   # shorter RSS/scrape snippets get the full article fetched
SOURCE_CAP = 3000   # characters of article text kept as source_text
# Feed gives headlines only (no article text), so its items skip the LLM summary and the judge and are saved under "headlines".
HEADLINE_SOURCE = "McKinsey"
EIGHT_WEEKS_AGO = datetime.now() - timedelta(weeks=8)
FOUR_WEEKS_AGO = datetime.now() - timedelta(weeks=4)
TWO_WEEKS_AGO = datetime.now() - timedelta(weeks=2)

RSS_SOURCES = [
    {"url": "https://openai.com/news/rss.xml", "name": "OpenAI", "tier": "ai_labs", "window": "4w"},
    {"url": "https://blogs.microsoft.com/feed/", "name": "Microsoft", "tier": "ai_labs", "window": "4w"},
    {"url": "https://www.microsoft.com/en-us/research/feed/", "name": "Microsoft Research", "tier": "ai_labs", "window": "4w"},
    {"url": "https://research.google/blog/rss/", "name": "Google AI", "tier": "ai_labs", "window": "4w"},
    {"url": "https://deepmind.com/blog/feed/basic/", "name": "Google DeepMind", "tier": "ai_labs", "window": "4w"},
    {"url": "https://huggingface.co/blog/feed.xml", "name": "Hugging Face", "tier": "ai_labs", "window": "4w"},
    {"url": "https://sloanreview.mit.edu/feed/", "name": "MIT Sloan", "tier": "consulting", "window": "8w"},
    {"url": "https://hbr.org/rss/topic/ai", "name": "Harvard Business Review", "tier": "consulting", "window": "8w"},
    # mckinsey.com has no reachable feed (times out), so Google News scoped to the site. Links stay news.google.com redirects.
    {"url": "https://news.google.com/rss/search?q=site:mckinsey.com+(AI+OR+%22artificial+intelligence%22+OR+%22gen+AI%22+OR+agentic)&hl=en-GB&gl=GB&ceid=GB:en", "name": "McKinsey", "tier": "consulting", "window": "2w"},
    {"url": "https://techcrunch.com/category/artificial-intelligence/feed/", "name": "TechCrunch", "tier": "news", "window": "4w"},
    {"url": "https://feeds.feedburner.com/venturebeat/SZYF", "name": "VentureBeat", "tier": "news", "window": "4w"},
    {"url": "https://www.technologyreview.com/feed/", "name": "MIT Tech Review", "tier": "news", "window": "4w"},
    {"url": "https://www.theverge.com/ai-artificial-intelligence/rss/index.xml", "name": "The Verge AI", "tier": "news", "window": "4w"},
    {"url": "https://www.wired.com/feed/tag/ai/latest/rss", "name": "Wired AI", "tier": "news", "window": "4w"},
    {"url": "https://hnrss.org/frontpage?points=100", "name": "Hacker News", "tier": "news", "window": "4w"},
    {"url": "https://feeds.arstechnica.com/arstechnica/technology-lab", "name": "Ars Technica", "tier": "news", "window": "4w"},
]

SCRAPE_SOURCES = [
    {"url": "https://www.anthropic.com/news", "name": "Anthropic", "tier": "ai_labs", "article_selector": "a[href*='/news/']", "base_url": "https://www.anthropic.com"},
    {"url": "https://www.deloitte.com/us/en/insights.html", "name": "Deloitte", "tier": "consulting", "article_selector": "a[href*='/insights/']", "base_url": "https://www.deloitte.com"},
]

# Sent with every classify prompt. quality_checks C7-C10 and the judge enforce it: a story that breaks it is held back.
GROUNDING_RULES = """GROUNDING RULES (a story that breaks one is not published):
- Use only facts that are in the Content below. If the Content does not say it, do not write it.
- Every number, price, percentage, date, version and name of a product, company or person that you write
  must appear in the Content or the Title. Write no other number.
- Never write a price, cost, benchmark, tier, feature, menu path or setting that the Content does not state.
- State no prediction as fact. A forward view (a prediction, an implication, why it matters) starts with
  "Our take:" and comes after the facts.
- In enterprise_use, name only tools from the Content or from the reader's stack listed above. A time frame
  for the reader's own work ("this week", "30 days") is allowed there.
- Never write a placeholder such as "xxxx", "TBD", "[insert" or "example.com".
"""
REGENERATE_NOTE = """

Your last answer broke the GROUNDING RULES: {problems}.
Write the JSON again. Remove every number, name and claim that is not in the Content. Start a forward view with "Our take:"."""


def _grounding_problems(article, g):
    """Why the generated summary and enterprise_use are not grounded in the article's source. [] means they pass."""
    return quality_checks.grounding_problems({**article, "summary": g.get("summary", ""),
                                              "enterprise_use": g.get("enterprise_use", ""), "relevance_score": 0})


def _parse_classify_output(text):
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        text = text[start:end]
    g = json.loads(text.strip())
    int(g.get("relevance_score", 5))
    return g

def _passes(validate, text):
    try:
        validate(text)
        return True
    except Exception:
        return False

@observe(as_type="generation", name="groq-complete")
def groq_complete(messages, max_tokens=300, validate=None, call_site=""):
    # validate: parser that raises on bad output. Only calls with a validator use the cache,
    # and a response is saved only if it passes.
    model = "openai/gpt-oss-20b"
    cache_key = None
    if validate is not None:
        cache_key = llm_cache.key(model, messages, None, max_tokens, "low", "hidden")
        cached = llm_cache.get(cache_key)
        if cached is not None:
            langfuse.update_current_generation(
                model=model, input=messages, output=cached,
                usage_details={"input": 0, "output": 0, "total": 0},
                metadata={"cache_hit": True, "call_site": call_site},
            )
            return cached
    keys = [os.getenv("GROQ_API_KEY"), os.getenv("GROQ_API_KEY_2")]
    keys = [k for k in keys if k]
    last_error = None
    for i, key in enumerate(keys):
        try:
            client = Groq(api_key=key)
            resp = client.chat.completions.create(
                model="openai/gpt-oss-20b",
                messages=messages,
                max_tokens=max_tokens,
                reasoning_effort="low",
                reasoning_format="hidden",
            )
            if i > 0:
                print("[DEBUG] Using Key 2")
            result = resp.choices[0].message.content.strip()
            usage = getattr(resp, "usage", None)
            if usage:
                langfuse.update_current_generation(
                    model="openai/gpt-oss-20b",
                    input=messages,
                    output=result,
                    usage_details={
                        "input": usage.prompt_tokens,
                        "output": usage.completion_tokens,
                        "total": usage.total_tokens,
                    },
                    metadata={"groq_key": f"key_{i+1}", "cache_hit": False, "call_site": call_site},
                )
            else:
                langfuse.update_current_generation(
                    model="openai/gpt-oss-20b",
                    input=messages,
                    output=result,
                    metadata={"cache_hit": False, "call_site": call_site},
                )
            if cache_key and _passes(validate, result):
                llm_cache.put(cache_key, result, model, call_site)
            return result
        except Exception as e:
            if "429" in str(e) or "rate" in str(e).lower():
                print("[DEBUG] Key " + str(i+1) + " rate limited, trying next...")
                last_error = e
                continue
            raise e
    raise Exception("All keys exhausted: " + str(last_error))

class NewsScoutState(TypedDict):
    raw_articles: Annotated[list, operator.add]
    classified_news: Annotated[list, operator.add]
    output_file: str

def parse_date(s):
    if not s:
        return None
    try:
        return parsedate_to_datetime(s).replace(tzinfo=None)
    except:
        pass
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d")
    except:
        return None

def is_recent(s, window):
    dt = parse_date(s)
    if not dt:
        return False
    cutoff = {"8w": EIGHT_WEEKS_AGO, "2w": TWO_WEEKS_AGO}.get(window, FOUR_WEEKS_AGO)
    return dt >= cutoff

def format_date(s):
    dt = parse_date(s)
    if dt:
        return dt.strftime("%d %b %Y")
    return s[:10] if s else ""

def scrape_articles(source, seen):
    articles = []
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; ScoutBot/1.0)"}
        resp = requests.get(source["url"], headers=headers, timeout=15)
        if resp.status_code != 200:
            print("[DEBUG] Scrape error " + source["name"] + ": HTTP " + str(resp.status_code))
            return articles
        soup = BeautifulSoup(resp.text, "html.parser")
        links = soup.select(source["article_selector"])
        count = 0
        seen_hrefs = set()
        for link in links[:30]:
            href = link.get("href", "")
            if not href or href in seen_hrefs:
                continue
            seen_hrefs.add(href)
            if not href.startswith("http"):
                href = source["base_url"] + href
            if quality_checks.is_listing_url(href):  # /news/announcements is a list of stories, not a story
                continue
            heading = link.find(["h1", "h2", "h3", "h4"])  # the card's headline, without its teaser text
            title = (heading or link).get_text(" ", strip=True)
            if not title or len(title) < 10 or href in seen:
                continue
            seen.add(href)
            articles.append({
                "id": href,
                "title": title,
                "summary_raw": "",
                "source": source["name"],
                "tier": source["tier"],
                "pub_date": "",  # a listing page gives no date: fetch_source_text reads it from the article page
                "scraped": True,
                "url": href,
            })
            count += 1
            if count >= MAX_PER_SOURCE:
                break
        print("[DEBUG] " + source["name"] + " (scraped): " + str(count) + " items")
    except Exception as e:
        print("[DEBUG] Scrape error " + source["name"] + ": " + str(e))
    return articles

FETCH_DEADLINE = 10           # seconds, wall clock, for the whole fetch + extract
FETCH_MAX_BYTES = 2_000_000
MAX_REDIRECTS = 3
_FETCH_UA = {"User-Agent": "Mozilla/5.0 (compatible; ScoutBot/1.0)"}


def _resolve_ips(host, port):
    return {info[4][0] for info in socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)}


def _refuse_reason(url):
    """None if url is http(s) and every address its host resolves to is public; otherwise why it is refused."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as e:
        return f"invalid URL: {e}"
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return f"scheme {parts.scheme!r} not allowed"
    try:
        ips = _resolve_ips(parts.hostname, port or (443 if parts.scheme == "https" else 80))
    except (OSError, UnicodeError) as e:
        return f"cannot resolve {parts.hostname}: {type(e).__name__}"
    for ip in ips:
        addr = ipaddress.ip_address(ip.split("%")[0])
        addr = getattr(addr, "ipv4_mapped", None) or addr
        if (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_reserved
                or addr.is_unspecified or not addr.is_global):
            return f"{parts.hostname} resolves to non-public address {addr}"
    return None


_DATE_META = ("article:published_time", "og:article:published_time", "datepublished", "date", "pubdate", "publish-date",
              "publish_date", "parsely-pub-date", "dc.date", "dc.date.issued", "sailthru.date")
_JSON_DATE = re.compile(r'"datePublished"\s*:\s*"([^"]+)"')


def page_facts(html):
    """What an article page says about itself: {"date": "YYYY-MM-DD", "title": "..."}, each "" when the page does not
    say. Only explicit publish markers count (meta tags, JSON-LD datePublished, the first <time datetime>): a date in
    the body text is never taken as the publish date."""
    soup = BeautifulSoup(html, "html.parser")
    found = []
    for tag in soup.find_all("meta"):
        key = str(tag.get("property") or tag.get("name") or tag.get("itemprop") or "").lower()
        if key in _DATE_META and tag.get("content"):
            found.append((_DATE_META.index(key), str(tag["content"])))
    found = [value for _, value in sorted(found)]
    found += _JSON_DATE.findall(html if isinstance(html, str) else html.decode("utf-8", "ignore"))
    stamp = soup.find("time", attrs={"datetime": True})
    if stamp:
        found.append(str(stamp["datetime"]))
    date = next((dt.strftime("%Y-%m-%d") for dt in map(parse_date, found) if dt), "")
    og = soup.find("meta", attrs={"property": "og:title"})
    return {"date": date, "title": " ".join(str(og.get("content") or "").split()) if og else ""}


def _fetch_article_text(url, deadline, facts=None):
    """Redirects followed by hand so every hop is address-checked; body streamed, capped and stopped at deadline.
    facts: a dict that gets the page's own publish date and title (page_facts)."""
    # shortcut: the host is resolved for the check and again by requests, so DNS rebinding can slip past; pin the
    # resolved IP in the connection if feeds start pointing at hostile hosts.
    for _ in range(MAX_REDIRECTS + 1):
        why = _refuse_reason(url)
        if why:
            raise PermissionError(why)
        resp = requests.get(url, headers=_FETCH_UA, timeout=(5, FETCH_DEADLINE), stream=True, allow_redirects=False)
        if resp.is_redirect:
            url = urljoin(url, resp.headers.get("Location", ""))
            resp.close()
            continue
        resp.raise_for_status()
        if "text/html" not in resp.headers.get("Content-Type", "").lower():
            raise ValueError("not text/html: " + resp.headers.get("Content-Type", "none"))
        body = bytearray()
        for chunk in resp.iter_content(65536):
            body += chunk
            if len(body) > FETCH_MAX_BYTES:
                resp.close()
                raise ValueError(f"page over {FETCH_MAX_BYTES} bytes")
            if time.monotonic() > deadline:  # a slow drip ends here on its next byte, after the caller has moved on
                resp.close()
                raise TimeoutError(f"over {FETCH_DEADLINE}s")
        resp.close()
        if facts is not None:
            facts.update(page_facts(bytes(body)))
        return (trafilatura.extract(bytes(body), include_comments=False, include_tables=False) or "").strip()
    raise ValueError(f"more than {MAX_REDIRECTS} redirects")


def fetch_source_text(article):
    """Full article text (up to SOURCE_CAP) when the snippet is thin; the snippet on any failure or limit.
    A scraped article also gets its pub_date and title from the article page itself. No date on the page leaves
    pub_date empty, and classify_news then holds the story back: the date shown is the source's own or none."""
    snippet = article.get("summary_raw") or ""
    if len(snippet) >= MIN_SNIPPET or not article.get("url"):
        return snippet[:SOURCE_CAP]
    box, deadline = {}, time.monotonic() + FETCH_DEADLINE
    facts = {} if article.get("scraped") else None

    def work():
        try:
            box["text"] = _fetch_article_text(article["url"], deadline, facts) if facts is not None \
                else _fetch_article_text(article["url"], deadline)
            if facts:
                article["pub_date"] = format_date(facts.get("date", ""))
                article["title"] = facts.get("title") or article["title"]
        except Exception as e:
            box["error"] = e

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(FETCH_DEADLINE)
    if worker.is_alive():  # hard wall-clock stop for the caller; the daemon worker stops itself at its next read
        box = {"error": TimeoutError(f"over {FETCH_DEADLINE}s")}
    if "error" in box or "text" not in box:
        e = box.get("error")
        print("[DEBUG] Full-text fetch skipped for " + article.get("title", "")[:50] + ": " + type(e).__name__ + ": " + str(e)[:100])
        return snippet
    text = box["text"]
    return text[:SOURCE_CAP] if len(text) > len(snippet) else snippet

def fetch_news(state):
    print("[DEBUG] Fetching industry news...")
    raw_articles = []
    seen = set()

    # Cross-run deduplication: load known IDs from archive and active files
    archive_path = Path(ARCHIVE_FILE)
    if archive_path.exists():
        with open(archive_path) as f:
            archive = json.load(f)
        for entry in archive:
            for item in entry.get("news", []) + entry.get("headlines", []):
                uid = item.get("id") or item.get("url", "")
                if uid:
                    seen.add(uid.strip())
    for f in Path(OUTPUT_DIR).glob("news_*.json"):
        if "archive" not in f.name:
            try:
                data = json.load(open(f))
                for item in data.get("news", []) + data.get("headlines", []):
                    uid = item.get("id") or item.get("url", "")
                    if uid:
                        seen.add(uid.strip())
            except Exception:
                pass
    print(f"[DEBUG] Cross-run dedup: {len(seen)} known news IDs loaded")

    for source in RSS_SOURCES:
        try:
            feed = feedparser.parse(source["url"])
            count = 0
            for entry in feed.entries[:20]:
                pub = entry.get("published") or entry.get("updated") or ""
                if not is_recent(pub, source["window"]):
                    continue
                title = entry.get("title", "").strip()
                if source["name"] == HEADLINE_SOURCE:
                    title = title.removesuffix(" - McKinsey & Company").strip()
                url = entry.get("link", "")
                uid = url or title
                if uid in seen or not title:
                    continue
                if quality_checks.is_listing_url(url):
                    print("[QUALITY] SKIPPED listing page, not an article: " + url)
                    continue
                seen.add(uid)
                summary = re.sub(r"<[^>]+>", "", entry.get("summary", "") or entry.get("description", ""))[:500].strip()
                raw_articles.append({
                    "id": uid,
                    "title": title,
                    "summary_raw": summary,
                    "source": source["name"],
                    "tier": source["tier"],
                    "pub_date": format_date(pub),
                    "url": url,
                })
                count += 1
            print("[DEBUG] " + source["name"] + ": " + str(count) + " items")
        except Exception as e:
            print("[DEBUG] RSS error " + source["name"] + ": " + str(e))

    for source in SCRAPE_SOURCES:
        scraped = scrape_articles(source, seen)
        raw_articles.extend(scraped)

    print("[DEBUG] Total before diversity filter: " + str(len(raw_articles)))

    # Apply diversity filter: max MAX_PER_SOURCE per source
    source_counts = defaultdict(int)
    diverse = []
    for article in raw_articles:
        if source_counts[article["source"]] < MAX_PER_SOURCE:
            diverse.append(article)
            source_counts[article["source"]] += 1

    print("[DEBUG] Total after diversity filter: " + str(len(diverse)))
    # Only the first MAX_ITEMS are classified, so only those get full text.
    with ThreadPoolExecutor(max_workers=8) as pool:
        texts = list(pool.map(fetch_source_text, diverse[:MAX_ITEMS]))
    for article, text in zip(diverse, texts):
        article["source_text"] = text
    print(f"[DEBUG] Full text fetched for {sum(len(t) > len(a.get('summary_raw') or '') for a, t in zip(diverse, texts))} thin snippets")
    today = datetime.now().strftime("%Y-%m-%d")
    return {
        "raw_articles": diverse,
        "classified_news": [],
        "output_file": state.get("output_file", OUTPUT_DIR + "/news_" + today + ".json"),
    }

@observe(name="classify-news")
def classify_news(state):
    articles = state["raw_articles"]
    print("[DEBUG] Classifying " + str(len(articles)) + " articles...")
    classified = []
    _run["held_date"] = _run["regenerated"] = 0
    limit = min(len(articles), MAX_ITEMS)
    for i in range(limit):
        article = articles[i]
        print("[DEBUG] " + str(i+1) + "/" + str(limit) + ": " + article["title"][:60])
        if article.get("scraped") and not is_recent(article.get("pub_date"), "8w"):
            why = "is older than 8 weeks" if article.get("pub_date") else "has no publish date on its page"
            print("[QUALITY] HELD BACK " + article["title"][:70] + ": the source " + why)
            _run["held_date"] = _run.get("held_date", 0) + 1
            continue
        title = article["title"]
        source = article["source"]
        content = (article.get("source_text") or article["summary_raw"])[:SOURCE_CAP]
        classify_prompt = f"""
You are scoring AI industry news for an IT Engineer who manages
Google Workspace, JumpCloud, Freshservice, SSO, MDM, endpoint
security, and access management tools daily.

He reads this to:
1. Stay ahead of AI changes that affect his IT stack
2. Find AI tools he can use to optimise his own IT workflows
3. Bring high-value AI insights to his IT department

Score on THREE dimensions:

RECENCY (0-4):
4 = Breaking this week -- must know now
3 = This month -- still relevant
2 = Last month -- fading
1 = Older background context
0 = Stale or undated

STACK_IMPACT (0-3):
3 = Directly affects tools he uses: Google Workspace, JumpCloud,
    Freshservice, Microsoft 365, Okta, CrowdStrike, Cloudflare,
    AWS, Azure, MDM platforms, SSO providers, endpoint security
2 = Affects adjacent IT tooling or vendor ecosystem he monitors
1 = General AI news with indirect IT relevance
0 = No relevance to IT infrastructure

ACTION_SIGNAL (0-3):
3 = He should act THIS WEEK: check a setting, evaluate a tool,
    flag a risk to his team, update a config, test something new
2 = Worth reviewing in the next 30 days
1 = Note it, revisit in 90 days
0 = Interesting but no action needed

relevance_score = RECENCY + STACK_IMPACT + ACTION_SIGNAL (max 10)
relevance_signal = 'high' if >= 7, 'medium' if 4-6, 'low' if <= 3

CALIBRATION EXAMPLES:
- "JumpCloud adds AI-powered anomaly detection to MDM" = 4+3+3 = 10
- "Google Workspace gets Gemini AI in Admin Console" = 4+3+3 = 10
- "OpenAI releases GPT-5" = 3+1+1 = 5
- "AI startup raises Series B" = 1+0+0 = 1
- "Critical vulnerability in widely used SSO provider" = 4+3+3 = 10
- "Voice AI product launches in India" = 2+0+0 = 2

OUTPUT FIELDS:

summary: 2 sentences.
S1: What happened -- name the specific product, company, feature,
    as the Content states it. No vague language.
S2: Starts with "Our take:". Why it matters to an IT engineer specifically.
BAD: "An AI company released a new feature."
GOOD: "JumpCloud has added AI-driven conditional access policies
that flag anomalous login patterns in real time. Our take: this gives
IT engineers an automated first line of defence against credential
stuffing without manual rule-writing."

enterprise_use: How the reader specifically can use or act on this.
HIGH signal (score 7+): 3 sentences.
  S1: The specific action this week, with a tool the Content names
      or a tool from his stack. Do not invent a setting or menu path.
  S2: What risk or opportunity inaction creates
  S3: Which of his tools is most affected or most useful here
MEDIUM signal (score 4-6): 2 sentences.
  S1: What to watch or evaluate
  S2: Why it matters for his workflow
LOW signal (score 1-3): 1 sentence max.
BAD: "IT teams could consider implementing this."
GOOD: "Check your JumpCloud admin console this week for the new
AI detection feature and enable it if your plan has it. Missing
this means relying solely on manual log review for suspicious
login patterns."

{GROUNDING_RULES}
category: pick the MOST SPECIFIC from:
Security, API Update, Model Release, Product Launch,
Research, Regulation, Tools, Infrastructure, Other
Rules:
- Any vulnerability, breach, attack surface = Security
- New AI model announced = Model Release
- Pricing, API, or developer platform change = API Update
- New product or major feature shipped = Product Launch
- Only use Other if nothing else fits

Return JSON only. No markdown. No preamble.
{{
  "summary": "",
  "enterprise_use": "",
  "relevance_score": 0,
  "relevance_signal": "",
  "category": ""
}}

News item:
Title: {title}
Source: {source}
Content: {content}
"""
        try:
            text = groq_complete([{"role": "user", "content": classify_prompt}], max_tokens=400,
                                 validate=_parse_classify_output, call_site="news.classify")
            g = _parse_classify_output(text)
            problems = [] if source == HEADLINE_SOURCE else _grounding_problems(article, g)
            if problems:  # regenerate once; the quality gate holds the story back if the second answer fails too
                print("[QUALITY] REGENERATE " + title[:60] + ": " + "; ".join(problems))
                _run["regenerated"] = _run.get("regenerated", 0) + 1
                try:
                    retry = classify_prompt + REGENERATE_NOTE.format(problems="; ".join(problems))
                    g = _parse_classify_output(groq_complete([{"role": "user", "content": retry}], max_tokens=400,
                                                             validate=_parse_classify_output, call_site="news.classify.regenerate"))
                except Exception as e:
                    print("[QUALITY] Regenerate failed, first answer kept for the gate: " + str(e)[:120])
            if source == HEADLINE_SOURCE:  # keep the score, drop text written from the headline alone
                g["summary"] = g["enterprise_use"] = ""
            classified.append({
                **article,
                "summary": g.get("summary", ""),
                "enterprise_use": g.get("enterprise_use", ""),
                "relevance_score": int(g.get("relevance_score", 5)),
                "relevance_signal": g.get("relevance_signal", "medium"),
                "category": g.get("category", "Other"),
            })
            print("[DEBUG] Relevance: " + str(g.get("relevance_score")) + "/10")
        except Exception as e:
            print("[DEBUG] Error: " + str(e))
            print("[QUALITY] classify_failed, will not publish: " + article["title"][:60])
            classified.append({**article, "classify_failed": True, "relevance_score": 0})
    classified.sort(key=lambda x: x["relevance_score"], reverse=True)
    print("[DEBUG] Classified " + str(len(classified)))
    return {"raw_articles": state["raw_articles"], "classified_news": classified, "output_file": state["output_file"]}

def save_and_archive(state):
    print("[DEBUG] Saving...")
    failed = [x for x in state["classified_news"] if x.get("classify_failed")]
    ok = [x for x in state["classified_news"] if not x.get("classify_failed")]
    # Headline items have no summary, so the summary-based quality checks and the judge cannot apply to them.
    headlines = [x for x in ok if x.get("source") == HEADLINE_SOURCE]
    print(f"[HEADLINES] {len(headlines)} {HEADLINE_SOURCE} headline items, judge skipped")
    kept, held, results = quality_checks.gate([x for x in ok if x.get("source") != HEADLINE_SOURCE])
    summary = quality_checks.summarize(results)
    if failed:  # count classify failures like score_failed: a failed check in the quality report
        summary["total"] += len(failed)
        summary["pass_rate"] = summary["passed"] / summary["total"]
        summary["fails"]["classify_failed"] = [f"{x['title'][:50]}: classify call failed" for x in failed]
    _quality.clear(); _quality.update(summary)
    print(f"[QUALITY] {len(kept)} kept, {len(held)} held back, {len(failed)} classify_failed")
    # log, steps_key, extra_rules by position: the judge also fails ungrounded claims and unlabelled forward views
    kept, judge_held, judge_stats = judge.gate(kept, "title", "summary_raw", "summary", "enterprise_use",
                                               print, None, judge.GROUNDING_RULES)
    print(f"[GROUNDING] news: {len(kept)} published, {len(held) + len(judge_held) + _run.get('held_date', 0)} held back "
          f"({len(held)} quality checks, {len(judge_held)} judge, {_run.get('held_date', 0)} source date), "
          f"{_run.get('regenerated', 0)} regenerated once")
    _judge.clear(); _judge.update(judge_stats)
    state = {**state, "classified_news": kept}
    _run["written"] = len(kept)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    archive = []
    if os.path.exists(ARCHIVE_FILE):
        try:
            with open(ARCHIVE_FILE, encoding="utf-8") as f:
                archive = json.load(f)
        except:
            pass
    today = datetime.now().strftime("%Y-%m-%d")
    daily, daily_h = [], []
    if os.path.exists(state["output_file"]):
        try:
            with open(state["output_file"], encoding="utf-8") as f:
                day = json.load(f)
            daily, daily_h = day.get("news", []), day.get("headlines", [])
        except Exception:
            pass
    # Same-day reruns add to today's items, never replace them (daily file and archive entry both count).
    entry = next((e for e in archive if e.get("date") == today), {})
    keys = set()

    def unseen(items):
        out = []
        for item in items:
            k = (item.get("id") or item.get("url") or "").strip()
            if k not in keys:
                keys.add(k)
                out.append(item)
        return out

    merged = unseen(daily + entry.get("news", []))
    added = unseen(state["classified_news"])
    merged += added
    merged_h = unseen(daily_h + entry.get("headlines", []))
    added_h = unseen(headlines)
    merged_h = sorted(merged_h + added_h, key=lambda x: x.get("relevance_score", 0), reverse=True)
    if added or added_h:
        output = {
            "timestamp": datetime.now().isoformat(),
            "filter": "last_4_to_8_weeks",
            "total_fetched": len(state["raw_articles"]),
            "total_classified": len(merged),
            "news": merged,
            "headlines": merged_h,
        }
        with open(state["output_file"], "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        archive = [e for e in archive if e.get("date") != today]
        archive.insert(0, {"date": today, "total": len(merged), "news": merged, "headlines": merged_h})
        archive = archive[:60]
        with open(ARCHIVE_FILE, "w", encoding="utf-8") as f:
            json.dump(archive, f, indent=2, ensure_ascii=False)
        print(f"Saved {len(added)} new news items ({len(merged)} today), {len(added_h)} new headlines ({len(merged_h)} today).")
        if added:
            generate_daily_hook(merged)
    else:
        print("No new news items; today's news left unchanged.")
    try:
        print(f"[CACHE] Saved {llm_cache.save()} entries to {llm_cache.CACHE_FILE}")
    except Exception as e:
        print(f"[CACHE] Save failed: {e}")
    return state

def generate_daily_hook(classified_news):
    """Write a one-line punchy hook from today's top 3 items to data/daily_hook.txt."""
    if not classified_news:
        return
    top = sorted(classified_news, key=lambda x: x.get("relevance_score", 0), reverse=True)[:3]
    titles = [t["title"] for t in top]
    prompt = (
        "You are a UK AI newsletter writer. Based on these top AI news headlines, write one punchy hook sentence "
        "(max 25 words) that would make a busy IT consultant stop scrolling. "
        "No fluff. No hashtags. No quotes around the result.\n\n"
        "Headlines:\n" + "\n".join(f"- {t}" for t in titles) + "\n\n"
        "Return the hook sentence only."
    )
    try:
        hook = groq_complete([{"role": "user", "content": prompt}], max_tokens=60, call_site="news.daily_hook")
        os.makedirs("data", exist_ok=True)
        with open("data/daily_hook.txt", "w") as f:
            f.write(hook.strip())
        print(f"[DEBUG] Daily hook saved: {hook.strip()}")
    except Exception as e:
        print(f"[DEBUG] Hook generation failed: {e}")

def build_graph():
    g = StateGraph(NewsScoutState)
    g.add_node("fetch", fetch_news)
    g.add_node("classify", classify_news)
    g.add_node("save", save_and_archive)
    g.add_edge(START, "fetch")
    g.add_edge("fetch", "classify")
    g.add_edge("classify", "save")
    g.add_edge("save", END)
    return g.compile(checkpointer=InMemorySaver())

@observe(name="industry-news-scout")
def run_industry_news_scout():
    print("[DEBUG] Starting Industry News Scout...")
    today = datetime.now().strftime("%Y-%m-%d")
    with propagate_attributes(tags=["news", "rss", "automated"]):
        langfuse.update_current_span(
            input={"date": today, "sources": len(RSS_SOURCES) + len(SCRAPE_SOURCES)},
            metadata={"max_items": MAX_ITEMS, "max_per_source": MAX_PER_SOURCE},
        )
        graph = build_graph()
        result = graph.invoke(
            {"raw_articles": [], "classified_news": [], "output_file": OUTPUT_DIR + "/news_" + today + ".json"},
            config={"configurable": {"thread_id": "news-" + today}},
        )
        langfuse.update_current_span(
            output={"items_saved": _run.get("written", 0), "date": today},
        )
        if _quality:
            quality_checks.send_scores(langfuse, _quality)
        if _judge:
            judge.send_scores(langfuse, _judge)
    print("[DEBUG] Complete")
    langfuse.flush()
    return result

if __name__ == "__main__":
    run_industry_news_scout()
    from langsmith import Client
    client = Client()
    import time
    time.sleep(5)  # allow trace buffer to flush
