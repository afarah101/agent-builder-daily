"""
Industry News Scout - LangGraph Version (Simplified)
"""

import os
import json
from datetime import datetime
from typing import TypedDict, Annotated
import operator

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver

import feedparser
from groq import Groq

class NewsScoutState(TypedDict):
    sources: Annotated[list, operator.add]
    raw_articles: Annotated[list, operator.add]
    classified_news: Annotated[list, operator.add]
    output_file: str

def fetch_news_articles(state: NewsScoutState) -> NewsScoutState:
    print("[DEBUG] Fetching news from RSS feeds...")
    raw_articles = []
    feed_urls = [
        "https://openai.com/news/rss.xml",
        "https://techcrunch.com/category/artificial-intelligence/feed/",
        "https://feeds.feedburner.com/venturebeat/SZYF",
    ]
    for url in feed_urls:
        try:
            feed = feedparser.parse(url)
            entries = feed.entries[:5] if feed.entries else []
            print(f"[DEBUG] Got {len(entries)} entries from {url}")
            for entry in entries:
                raw_articles.append({
                    "source": feed.feed.get("title", url),
                    "title": entry.get("title", ""),
                    "url": entry.get("link", ""),
                    "summary": entry.get("summary", "")[:300],
                    "published": entry.get("published", ""),
                })
        except Exception as e:
            print(f"[DEBUG] Error fetching {url}: {e}")
    print(f"[DEBUG] Total articles fetched: {len(raw_articles)}")
    return {
        "sources": state.get("sources", ["OpenAI", "Anthropic"]),
        "raw_articles": raw_articles,
        "classified_news": [],
        "output_file": state.get("output_file", f"logs/news_{datetime.now().strftime('%Y-%m-%d')}.json")
    }

def classify_news(state: NewsScoutState) -> NewsScoutState:
    print(f"[DEBUG] Classifying {len(state['raw_articles'])} articles...")
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    classified = []
    for i, article in enumerate(state["raw_articles"]):
        print(f"[DEBUG] Classifying {i+1}/{len(state['raw_articles'])}: {article['title'][:50]}...")
        prompt = f"""Rate this news for a UK-based AI consultancy targeting SMBs.
Title: {article['title']}
Source: {article['source']}
Respond ONLY in this JSON format:
{{"relevance_score": 0-10, "category": "Research|Product|Tool|Market", "suitability": "high|medium|low"}}"""
        try:
            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=100,
            )
            text = response.choices[0].message.content.strip()
            print(f"[DEBUG] Response: {text}")
            try:
                classified_item = json.loads(text)
                classified_item["article_title"] = article["title"]
                classified_item["source"] = article["source"]
                classified.append(classified_item)
            except json.JSONDecodeError as e:
                print(f"[DEBUG] JSON parse error: {e}")
        except Exception as e:
            print(f"[DEBUG] Groq error: {e}")
    print(f"[DEBUG] Total classified: {len(classified)}")
    return {
        "sources": state["sources"],
        "raw_articles": state["raw_articles"],
        "classified_news": classified,
        "output_file": state["output_file"]
    }

def save_news_json(state: NewsScoutState) -> NewsScoutState:
    print("[DEBUG] Saving to JSON...")
    os.makedirs(os.path.dirname(state["output_file"]), exist_ok=True)
    output_data = {
        "timestamp": datetime.now().isoformat(),
        "sources": state["sources"],
        "total_articles_fetched": len(state["raw_articles"]),
        "articles_classified": len(state["classified_news"]),
        "news": state["classified_news"]
    }
    with open(state["output_file"], "w") as f:
        json.dump(output_data, f, indent=2)
    print(f"✓ Saved {len(state['classified_news'])} items to {state['output_file']}")
    return state

def build_graph():
    graph = StateGraph(NewsScoutState)
    graph.add_node("fetch", fetch_news_articles)
    graph.add_node("classify", classify_news)
    graph.add_node("save", save_news_json)
    graph.add_edge(START, "fetch")
    graph.add_edge("fetch", "classify")
    graph.add_edge("classify", "save")
    graph.add_edge("save", END)
    return graph.compile(checkpointer=InMemorySaver())

def run_industry_news_scout():
    print("[DEBUG] Starting run_industry_news_scout...")
    graph = build_graph()
    initial_state = {
        "sources": ["OpenAI", "Anthropic"],
        "raw_articles": [],
        "classified_news": [],
        "output_file": f"logs/news_{datetime.now().strftime('%Y-%m-%d')}.json"
    }
    result = graph.invoke(initial_state, config={"configurable": {"thread_id": f"news-scout-{datetime.now().isoformat()}"}})
    print("[DEBUG] Completed")
    return result

if __name__ == "__main__":
    run_industry_news_scout()
