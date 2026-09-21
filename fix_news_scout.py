import os

news_code = """#!/usr/bin/env python3
import os, json, re, feedparser, requests
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

def groq_complete(messages, max_tokens=300):
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
            return resp.choices[0].message.content.strip()
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

def classify_news(state):
    articles = state["raw_articles"]
    print("[DEBUG] Classifying " + str(len(articles)) + " articles...")
    classified = []
    limit = min(len(articles), MAX_ITEMS)
    for i in range(limit):
        article = articles[i]
        print("[DEBUG] " + str(i+1) + "/" + str(limit) + ": " + article["title"][:60])
        prompt = (
            "Classify this AI industry news item. Be concise.\\n"
            "Title: " + article["title"] + "\\n"
            "Source: " + article["source"] + "\\n"
            "Content: " + article["summary_raw"][:300] + "\\n\\n"
            'Return JSON only: {"summary": "2 sentences", "enterprise_use": "2 sentences", "relevance_score": 8, "relevance_signal": "high", "category": "Product Launch"}'
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
    with open(state["output_file"], "w") as f:
        json.dump(output, f, indent=2)
    archive = []
    if os.path.exists(ARCHIVE_FILE):
        try:
            with open(ARCHIVE_FILE) as f:
                archive = json.load(f)
        except:
            pass
    today = datetime.now().strftime("%Y-%m-%d")
    archive = [e for e in archive if e.get("date") != today]
    archive.insert(0, {"date": today, "total": len(state["classified_news"]), "news": state["classified_news"]})
    archive = archive[:60]
    with open(ARCHIVE_FILE, "w") as f:
        json.dump(archive, f, indent=2)
    print("Saved " + str(len(state["classified_news"])) + " news items.")
    return state

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

def run_industry_news_scout():
    print("[DEBUG] Starting Industry News Scout...")
    graph = build_graph()
    today = datetime.now().strftime("%Y-%m-%d")
    result = graph.invoke(
        {"raw_articles": [], "classified_news": [], "output_file": OUTPUT_DIR + "/news_" + today + ".json"},
        config={"configurable": {"thread_id": "news-" + today}},
    )
    print("[DEBUG] Complete")
    return result

if __name__ == "__main__":
    run_industry_news_scout()
"""

with open("industry_news_scout_langgraph.py", "w") as f:
    f.write(news_code)
print("Written: " + str(os.path.getsize("industry_news_scout_langgraph.py")) + " bytes")