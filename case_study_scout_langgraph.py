#!/usr/bin/env python3
"""
Case Study Scout — LangGraph Workflow
Pulls RECENT (last 4 weeks) AI case studies from RSS feeds + Semantic Scholar.
Scores and ranks by prestige, recency, impact, maturity.
"""

import os
import json
import re
import requests
import feedparser
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import TypedDict, Annotated
import operator
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver
from groq import Groq

GROQ_MODEL = "llama-3.3-70b-versatile"
OUTPUT_DIR = "case_studies"
MAX_PAPERS = 20
FOUR_WEEKS_AGO = datetime.now() - timedelta(weeks=4)

RSS_SOURCES = [
    {"url": "https://openai.com/news/rss.xml", "name": "OpenAI", "tier": "ai_labs", "prestige": 25},
    {"url": "https://deepmind.google/blog/rss/", "name": "Google DeepMind", "tier": "ai_labs", "prestige": 25},
    {"url": "https://huggingface.co/blog/feed.xml", "name": "Hugging Face", "tier": "ai_labs", "prestige": 20},
    {"url": "https://blogs.microsoft.com/ai/feed/", "name": "Microsoft AI", "tier": "ai_labs", "prestige": 20},
    {"url": "https://sloanreview.mit.edu/feed/", "name": "MIT Sloan", "tier": "consulting", "prestige": 20},
    {"url": "https://hbr.org/rss/topic/ai", "name": "Harvard Business Review", "tier": "consulting", "prestige": 20},
    {"url": "https://techcrunch.com/category/artificial-intelligence/feed/", "name": "TechCrunch", "tier": "news", "prestige": 15},
    {"url": "https://feeds.feedburner.com/venturebeat/SZYF", "name": "VentureBeat", "tier": "news", "prestige": 15},
    {"url": "https://www.technologyreview.com/feed/", "name": "MIT Tech Review", "tier": "news", "prestige": 18},
]

class CaseStudyState(TypedDict):
    raw_items: Annotated[list, operator.add]
    scored_items: Annotated[list, operator.add]
    output_file: str

def parse_date(pub_date_str):
    if not pub_date_str:
        return None
    try:
        return parsedate_to_datetime(pub_date_str).replace(tzinfo=None)
    except:
        pass
    try:
        return datetime.strptime(pub_date_str[:10], "%Y-%m-%d")
    except:
        pass
    return None

def is_within_4_weeks(pub_date_str):
    dt = parse_date(pub_date_str)
    if not dt:
        return False
    return dt >= FOUR_WEEKS_AGO

def calc_recency_score(pub_date_str):
    dt = parse_date(pub_date_str)
    if not dt:
        return 5
    days = (datetime.now() - dt).days
    if days <= 3: return 25
    if days <= 7: return 22
    if days <= 14: return 18
    if days <= 21: return 14
    if days <= 28: return 10
    return 0

def fetch_items(state: CaseStudyState) -> CaseStudyState:
    print("[DEBUG] Fetching from RSS sources...")
    raw_items = []
    seen = set()

    for source in RSS_SOURCES:
        try:
            feed = feedparser.parse(source["url"])
            count = 0
            for entry in feed.entries[:15]:
                pub = entry.get("published") or entry.get("updated") or ""
                if not is_within_4_weeks(pub):
                    continue
                title = entry.get("title", "")
                url = entry.get("link", "")
                uid = url or title
                if uid in seen:
                    continue
                seen.add(uid)
                summary = entry.get("summary", "") or entry.get("description", "")
                summary = re.sub(r'<[^>]+>', '', summary)[:400]
                raw_items.append({
                    "id": uid,
                    "title": title,
                    "abstract": summary,
                    "venue": source["name"],
                    "tier": source["tier"],
                    "prestige_score": source["prestige"],
                    "recency_score": calc_recency_score(pub),
                    "pub_date": pub[:10] if pub else "",
                    "url": url,
                    "authors": [],
                    "source_type": "rss",
                })
                count += 1
            print(f"[DEBUG] {source['name']}: {count} recent items")
        except Exception as e:
            print(f"[DEBUG] RSS error {source['name']}: {e}")

    print("[DEBUG] Fetching from Semantic Scholar (last 4 weeks)...")
    cutoff = FOUR_WEEKS_AGO.strftime("%Y-%m-%d")
    queries = [
        "artificial intelligence enterprise deployment results 2025",
        "large language model business implementation case study 2025",
        "generative AI production outcomes measured 2025",
    ]
    for query in queries:
        try:
            params = {
                "query": query,
                "fields": "title,abstract,authors,publicationDate,url,publicationVenue",
                "limit": 10,
                "sort": "publicationDate:desc",
            }
            resp = requests.get("https://api.semanticscholar.org/graph/v1/paper/search", params=params, timeout=15)
            if resp.status_code != 200:
                continue
            for paper in resp.json().get("data", []):
                pid = paper.get("paperId", "")
                pub_date = paper.get("publicationDate") or ""
                if not pub_date or pub_date < cutoff:
                    continue
                if pid in seen or not pid:
                    continue
                seen.add(pid)
                abstract = paper.get("abstract") or ""
                if len(abstract) < 50:
                    continue
                venue = (paper.get("publicationVenue") or {}).get("name", "Academic")
                raw_items.append({
                    "id": pid,
                    "title": paper.get("title", ""),
                    "abstract": abstract[:400],
                    "venue": venue,
                    "tier": "universities",
                    "prestige_score": 20,
                    "recency_score": calc_recency_score(pub_date),
                    "pub_date": pub_date[:10],
                    "url": paper.get("url", ""),
                    "authors": [a.get("name", "") for a in (paper.get("authors") or [])[:3]],
                    "source_type": "academic",
                })
        except Exception as e:
            print(f"[DEBUG] Semantic Scholar error: {e}")

    print(f"[DEBUG] Total recent items: {len(raw_items)}")
    return {
        "raw_items": raw_items,
        "scored_items": [],
        "output_file": state.get("output_file", f"{OUTPUT_DIR}/{datetime.now().strftime('%Y-%m-%d')}.json"),
    }

def score_and_classify(state: CaseStudyState) -> CaseStudyState:
    print(f"[DEBUG] Scoring {len(state['raw_items'])} items...")
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    scored = []

    for i, item in enumerate(state["raw_items"][:MAX_PAPERS]):
        print(f"[DEBUG] {i+1}/{min(len(state['raw_items']), MAX_PAPERS)}: {item['title'][:60]}...")
        prompt = f"""Evaluate this AI case study for a professional intelligence feed.

Title: {item['title']}
Source: {item['venue']}
Published: {item['pub_date']}
Content: {item['abstract']}

Score (0-25 each):
1. real_world_impact: measurable outcomes (%, $, time saved, scale)
   25=clear metrics at scale, 15=some metrics, 5=theoretical only
2. ai_maturity: 25=live in production, 15=pilot tested, 5=concept only

Write:
- summary: 2 sentences, what was done and what happened
- how_to_use: 1 sentence for an AI consultant
- monetisation: 1 sentence business opportunity

JSON only, no markdown:
{{"real_world_impact": 0, "ai_maturity": 0, "summary": "", "how_to_use": "", "monetisation": ""}}"""

        try:
            resp = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=300,
            )
            text = resp.choices[0].message.content.strip()
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            text = text.strip()
            g = json.loads(text)
            total = item["prestige_score"] + item["recency_score"] + g.get("real_world_impact", 0) + g.get("ai_maturity", 0)
            scored.append({
                **item,
                "real_world_impact": g.get("real_world_impact", 0),
                "ai_maturity": g.get("ai_maturity", 0),
                "total_score": total,
                "summary": g.get("summary", ""),
                "how_to_use": g.get("how_to_use", ""),
                "monetisation": g.get("monetisation", ""),
            })
            print(f"[DEBUG] Score: {total}/100")
        except Exception as e:
            print(f"[DEBUG] Groq error: {e}")

    scored.sort(key=lambda x: x["total_score"], reverse=True)
    print(f"[DEBUG] Scored {len(scored)} items")
    return {"raw_items": state["raw_items"], "scored_items": scored, "output_file": state["output_file"]}

def save_json(state: CaseStudyState) -> CaseStudyState:
    print("[DEBUG] Saving...")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output = {
        "timestamp": datetime.now().isoformat(),
        "filter": "last_4_weeks",
        "total_fetched": len(state["raw_items"]),
        "total_scored": len(state["scored_items"]),
        "case_studies": state["scored_items"],
    }
    with open(state["output_file"], "w") as f:
        json.dump(output, f, indent=2)
    print(f"Saved {len(state['scored_items'])} items to {state['output_file']}")
    return state

def build_graph():
    g = StateGraph(CaseStudyState)
    g.add_node("fetch", fetch_items)
    g.add_node("score", score_and_classify)
    g.add_node("save", save_json)
    g.add_edge(START, "fetch")
    g.add_edge("fetch", "score")
    g.add_edge("score", "save")
    g.add_edge("save", END)
    return g.compile(checkpointer=InMemorySaver())

def run_case_study_scout():
    print("[DEBUG] Starting Case Study Scout...")
    graph = build_graph()
    initial_state = {
        "raw_items": [],
        "scored_items": [],
        "output_file": f"{OUTPUT_DIR}/{datetime.now().strftime('%Y-%m-%d')}.json",
    }
    result = graph.invoke(initial_state, config={"configurable": {"thread_id": f"cs-{datetime.now().isoformat()}"}})
    print("[DEBUG] Complete")
    return result

if __name__ == "__main__":
    run_case_study_scout()