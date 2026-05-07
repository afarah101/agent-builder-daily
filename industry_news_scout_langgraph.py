#!/usr/bin/env python3
import os, json, re, feedparser, requests
from pathlib import Path
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from bs4 import BeautifulSoup
from typing import TypedDict, Annotated
from collections import defaultdict
import operator
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver
from groq import Groq
from dotenv import load_dotenv
load_dotenv()

from langfuse import Langfuse, observe
langfuse = Langfuse()

OUTPUT_DIR = "logs"
ARCHIVE_FILE = "logs/archive.json"
MAX_ITEMS = 25
MAX_PER_SOURCE = 3
EIGHT_WEEKS_AGO = datetime.now() - timedelta(weeks=8)
FOUR_WEEKS_AGO = datetime.now() - timedelta(weeks=4)

RSS_SOURCES = [
    {"url": "https://openai.com/news/rss.xml", "name": "OpenAI", "tier": "ai_labs", "window": "4w"},
    {"url": "https://blogs.microsoft.com/feed/", "name": "Microsoft", "tier": "ai_labs", "window": "4w"},
    {"url": "https://www.microsoft.com/en-us/research/feed/", "name": "Microsoft Research", "tier": "ai_labs", "window": "4w"},
    {"url": "https://research.google/blog/rss/", "name": "Google AI", "tier": "ai_labs", "window": "4w"},
    {"url": "https://deepmind.com/blog/feed/basic/", "name": "Google DeepMind", "tier": "ai_labs", "window": "4w"},
    {"url": "https://huggingface.co/blog/feed.xml", "name": "Hugging Face", "tier": "ai_labs", "window": "4w"},
    {"url": "https://sloanreview.mit.edu/feed/", "name": "MIT Sloan", "tier": "consulting", "window": "8w"},
    {"url": "https://hbr.org/rss/topic/ai", "name": "Harvard Business Review", "tier": "consulting", "window": "8w"},
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

@observe(as_type="generation", name="groq-complete")
def groq_complete(messages, max_tokens=300):
    keys = [os.getenv("GROQ_API_KEY"), os.getenv("GROQ_API_KEY_2")]
    keys = [k for k in keys if k]
    last_error = None
    for i, key in enumerate(keys):
        try:
            client = Groq(api_key=key)
            resp = client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=messages,
                max_tokens=max_tokens,
            )
            if i > 0:
                print("[DEBUG] Using Key 2")
            result = resp.choices[0].message.content.strip()
            usage = getattr(resp, "usage", None)
            if usage:
                langfuse.update_current_generation(
                    model="llama-3.1-8b-instant",
                    input=messages,
                    output=result,
                    usage={
                        "input": usage.prompt_tokens,
                        "output": usage.completion_tokens,
                        "total": usage.total_tokens,
                        "unit": "TOKENS",
                    },
                    metadata={"groq_key": f"key_{i+1}"},
                )
            else:
                langfuse.update_current_generation(
                    model="llama-3.1-8b-instant",
                    input=messages,
                    output=result,
                )
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
    cutoff = EIGHT_WEEKS_AGO if window == "8w" else FOUR_WEEKS_AGO
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
            title = link.get_text(strip=True)
            if not title or len(title) < 10 or href in seen:
                continue
            seen.add(href)
            articles.append({
                "id": href,
                "title": title,
                "summary_raw": "",
                "source": source["name"],
                "tier": source["tier"],
                "pub_date": datetime.now().strftime("%d %b %Y"),
                "url": href,
            })
            count += 1
            if count >= MAX_PER_SOURCE:
                break
        print("[DEBUG] " + source["name"] + " (scraped): " + str(count) + " items")
    except Exception as e:
        print("[DEBUG] Scrape error " + source["name"] + ": " + str(e))
    return articles

def fetch_news(state):
    print("[DEBUG] Fetching industry news...")
    raw_articles = []
    seen = set()

    # Cross-run deduplication: load known IDs from archive and active files
    archive_path = Path("logs/archive.json")
    if archive_path.exists():
        with open(archive_path) as f:
            archive = json.load(f)
        for entry in archive:
            for item in entry.get("news", []):
                uid = item.get("id") or item.get("url", "")
                if uid:
                    seen.add(uid.strip())
    for f in Path("logs").glob("news_*.json"):
        if "archive" not in f.name:
            try:
                data = json.load(open(f))
                for item in data.get("news", []):
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
                url = entry.get("link", "")
                uid = url or title
                if uid in seen or not title:
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
    limit = min(len(articles), MAX_ITEMS)
    for i in range(limit):
        article = articles[i]
        print("[DEBUG] " + str(i+1) + "/" + str(limit) + ": " + article["title"][:60])
        prompt = (
            "You are a UK IT consultant scoring AI industry news for immediate business relevance. "
            "Score this item on three dimensions, then compute relevance_score as their sum.\n\n"
            "Title: " + article["title"] + "\n"
            "Source: " + article["source"] + "\n"
            "Content: " + article["summary_raw"][:300] + "\n\n"
            "SCORING:\n"
            "1. recency (0-4): 4=breaking/today, 3=this week, 2=this month, 1=older\n"
            "2. stack_impact (0-3): 3=changes core AI stack (model release, API breaking change, new framework), "
            "2=significant tooling update, 1=minor or informational\n"
            "3. action_signal (0-3): 3=act this week (deploy, migrate, test), 2=schedule a review, 1=monitor only\n"
            "relevance_score = recency + stack_impact + action_signal  (0-10)\n\n"
            "- summary: 2 sentences. What happened and why it matters to an IT practitioner.\n"
            "- enterprise_use: 2 sentences. Specific use case for a UK SMB IT team or MSP.\n"
            "- relevance_signal: 'high' if score >= 7, 'medium' if 4-6, 'low' if <= 3\n"
            "- category: one of: Model Release, API Update, Product Launch, Research, Regulation, Tools, Other\n\n"
            "Return JSON only. No markdown.\n"
            '{"summary": "", "enterprise_use": "", "relevance_score": 0, "relevance_signal": "medium", "category": "Other"}'
        )
        try:
            text = groq_complete([{"role": "user", "content": prompt}], max_tokens=250)
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                text = text[start:end]
            g = json.loads(text.strip())
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
            classified.append({
                **article,
                "summary": article["summary_raw"][:200],
                "enterprise_use": "",
                "relevance_score": 5,
                "relevance_signal": "medium",
                "category": "Other",
            })
    classified.sort(key=lambda x: x["relevance_score"], reverse=True)
    print("[DEBUG] Classified " + str(len(classified)))
    return {"raw_articles": state["raw_articles"], "classified_news": classified, "output_file": state["output_file"]}

def save_and_archive(state):
    print("[DEBUG] Saving...")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output = {
        "timestamp": datetime.now().isoformat(),
        "filter": "last_4_to_8_weeks",
        "total_fetched": len(state["raw_articles"]),
        "total_classified": len(state["classified_news"]),
        "news": state["classified_news"],
    }
    with open(state["output_file"], "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    archive = []
    if os.path.exists(ARCHIVE_FILE):
        try:
            with open(ARCHIVE_FILE, encoding="utf-8") as f:
                archive = json.load(f)
        except:
            pass
    today = datetime.now().strftime("%Y-%m-%d")
    archive = [e for e in archive if e.get("date") != today]
    archive.insert(0, {"date": today, "total": len(state["classified_news"]), "news": state["classified_news"]})
    archive = archive[:60]
    with open(ARCHIVE_FILE, "w", encoding="utf-8") as f:
        json.dump(archive, f, indent=2, ensure_ascii=False)
    print("Saved " + str(len(state["classified_news"])) + " news items.")
    generate_daily_hook(state["classified_news"])
    return state

def generate_daily_hook(classified_news):
    """Write a one-line punchy hook from today's top 3 items to logs/daily_hook.txt."""
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
        hook = groq_complete([{"role": "user", "content": prompt}], max_tokens=60)
        os.makedirs("logs", exist_ok=True)
        with open("logs/daily_hook.txt", "w") as f:
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
    langfuse.update_current_span(
        input={"date": today, "sources": len(RSS_SOURCES) + len(SCRAPE_SOURCES)},
        tags=["news", "rss", "automated"],
        metadata={"max_items": MAX_ITEMS, "max_per_source": MAX_PER_SOURCE},
    )
    graph = build_graph()
    result = graph.invoke(
        {"raw_articles": [], "classified_news": [], "output_file": OUTPUT_DIR + "/news_" + today + ".json"},
        config={"configurable": {"thread_id": "news-" + today}},
    )
    classified = result.get("classified_news", [])
    langfuse.update_current_span(
        output={"items_saved": len(classified), "date": today},
    )
    print("[DEBUG] Complete")
    langfuse.flush()
    return result

if __name__ == "__main__":
    run_industry_news_scout()
