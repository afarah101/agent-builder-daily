#!/usr/bin/env python3
import os, json, re, feedparser
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import TypedDict, Annotated
import operator
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver
from groq import Groq

GROQ_MODEL = "llama-3.3-70b-versatile"
OUTPUT_DIR = "logs"
ARCHIVE_FILE = "logs/archive.json"
MAX_ITEMS = 20
FOUR_WEEKS_AGO = datetime.now() - timedelta(weeks=4)

RSS_SOURCES = [
    {"url": "https://openai.com/news/rss.xml", "name": "OpenAI", "tier": "ai_labs"},
    {"url": "https://deepmind.google/blog/rss/", "name": "Google DeepMind", "tier": "ai_labs"},
    {"url": "https://huggingface.co/blog/feed.xml", "name": "Hugging Face", "tier": "ai_labs"},
    {"url": "https://blogs.microsoft.com/ai/feed/", "name": "Microsoft AI", "tier": "ai_labs"},
    {"url": "https://sloanreview.mit.edu/feed/", "name": "MIT Sloan", "tier": "consulting"},
    {"url": "https://hbr.org/rss/topic/ai", "name": "Harvard Business Review", "tier": "consulting"},
    {"url": "https://techcrunch.com/category/artificial-intelligence/feed/", "name": "TechCrunch", "tier": "news"},
    {"url": "https://feeds.feedburner.com/venturebeat/SZYF", "name": "VentureBeat", "tier": "news"},
    {"url": "https://www.technologyreview.com/feed/", "name": "MIT Tech Review", "tier": "news"},
]

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

def is_recent(s):
    dt = parse_date(s)
    return dt >= FOUR_WEEKS_AGO if dt else False

def fetch_news(state):
    print("[DEBUG] Fetching industry news...")
    raw_articles = []
    seen = set()
    for source in RSS_SOURCES:
        try:
            feed = feedparser.parse(source["url"])
            count = 0
            for entry in feed.entries[:15]:
                pub = entry.get("published") or entry.get("updated") or ""
                if not is_recent(pub):
                    continue
                title = entry.get("title", "")
                url = entry.get("link", "")
                uid = url or title
                if uid in seen:
                    continue
                seen.add(uid)
                summary = re.sub(r"<[^>]+>", "", entry.get("summary", "") or entry.get("description", ""))[:500]
                raw_articles.append({
                    "id": uid,
                    "title": title,
                    "summary_raw": summary,
                    "source": source["name"],
                    "tier": source["tier"],
                    "pub_date": pub[:10] if pub else "",
                    "url": url,
                })
                count += 1
            print("[DEBUG] " + source["name"] + ": " + str(count) + " items")
        except Exception as e:
            print("[DEBUG] RSS error " + source["name"] + ": " + str(e))
    print("[DEBUG] Total: " + str(len(raw_articles)))
    today = datetime.now().strftime("%Y-%m-%d")
    return {
        "raw_articles": raw_articles,
        "classified_news": [],
        "output_file": state.get("output_file", OUTPUT_DIR + "/news_" + today + ".json"),
    }

def classify_news(state):
    articles = state["raw_articles"]
    print("[DEBUG] Classifying " + str(len(articles)) + " articles...")
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    classified = []
    limit = min(len(articles), MAX_ITEMS)
    for i in range(limit):
        article = articles[i]
        print("[DEBUG] " + str(i+1) + "/" + str(limit) + ": " + article["title"][:60])
        prompt = (
            "Classify this AI industry news item for a professional intelligence feed.\n"
            "Title: " + article["title"] + "\n"
            "Source: " + article["source"] + "\n"
            "Published: " + article["pub_date"] + "\n"
            "Content: " + article["summary_raw"] + "\n\n"
            "Write:\n"
            "- summary: 2 clear sentences. What happened and why it matters\n"
            "- enterprise_use: 2 concrete sentences. How an enterprise or AI consultant can act on this right now\n"
            "- relevance_score: 1-10. 10=must read for AI practitioners\n"
            "- relevance_signal: high if >=7, medium if 4-6, low if <=3\n"
            "- category: Product Launch, Research, Partnership, Market Move, Tool, Policy, or Other\n\n"
            'JSON only: {"summary": "", "enterprise_use": "", "relevance_score": 0, "relevance_signal": "", "category": ""}'
        )
        try:
            resp = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=400,
            )
            text = resp.choices[0].message.content.strip()
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            g = json.loads(text.strip())
            classified.append({
                **article,
                "summary": g.get("summary", ""),
                "enterprise_use": g.get("enterprise_use", ""),
                "relevance_score": g.get("relevance_score", 5),
                "relevance_signal": g.get("relevance_signal", "medium"),
                "category": g.get("category", "Other"),
            })
            print("[DEBUG] Relevance: " + str(g.get("relevance_score")) + "/10")
        except Exception as e:
            print("[DEBUG] Error: " + str(e))
    classified.sort(key=lambda x: x["relevance_score"], reverse=True)
    print("[DEBUG] Classified " + str(len(classified)))
    return {"raw_articles": state["raw_articles"], "classified_news": classified, "output_file": state["output_file"]}

def save_and_archive(state):
    print("[DEBUG] Saving...")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output = {
        "timestamp": datetime.now().isoformat(),
        "filter": "last_4_weeks",
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
