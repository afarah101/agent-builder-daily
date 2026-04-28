#!/usr/bin/env python3
import os, json, requests
from datetime import datetime
from typing import TypedDict, List, Dict, Any, Annotated
import operator
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver
from groq import Groq

GROQ_MODEL = "llama-3.3-70b-versatile"
OUTPUT_DIR = "case_studies"
MAX_PAPERS = 20

SOURCES = {
    "universities": ["Stanford University","Massachusetts Institute of Technology","Carnegie Mellon University","University of California Berkeley","University of Oxford","University of Cambridge","National University of Singapore","Tsinghua University","ETH Zurich","University of Toronto"],
    "consulting": ["McKinsey","Deloitte","BCG","MIT Sloan Management Review","Harvard Business Review"],
    "ai_labs": ["Google DeepMind","Microsoft Research","Meta AI Research","Hugging Face","OpenAI"],
}
PRESTIGE_SCORES = {"universities": 25, "consulting": 20, "ai_labs": 15, "other": 10}

class CaseStudyState(TypedDict):
    raw_papers: Annotated[list, operator.add]
    scored_papers: Annotated[list, operator.add]
    output_file: str

def fetch_case_studies(state):
    print("[DEBUG] Fetching case studies from Semantic Scholar...")
    raw_papers = []
    queries = [
        "artificial intelligence real world deployment case study",
        "machine learning implementation enterprise results",
        "AI automation business outcomes measured",
        "large language model production deployment",
        "generative AI SMB small business implementation",
    ]
    seen_ids = set()
    for query in queries:
        try:
            params = {"query": query, "fields": "title,abstract,authors,year,publicationDate,url,publicationVenue,citationCount", "limit": 10, "sort": "publicationDate:desc"}
            resp = requests.get("https://api.semanticscholar.org/graph/v1/paper/search", params=params, timeout=15)
            if resp.status_code != 200:
                print(f"[DEBUG] Error {resp.status_code} for: {query}")
                continue
            for paper in resp.json().get("data", []):
                pid = paper.get("paperId", "")
                if pid in seen_ids or not pid: continue
                seen_ids.add(pid)
                abstract = paper.get("abstract") or ""
                if len(abstract) < 50: continue
                venue = (paper.get("publicationVenue") or {}).get("name", "Unknown")
                tier, prestige = "other", 10
                for tk, names in SOURCES.items():
                    if any(n.lower() in venue.lower() for n in names):
                        tier, prestige = tk, PRESTIGE_SCORES[tk]
                        break
                pub_date = paper.get("publicationDate") or ""
                recency = 5
                if pub_date:
                    try:
                        days = (datetime.now() - datetime.strptime(pub_date[:10], "%Y-%m-%d")).days
                        recency = 25 if days<=7 else 20 if days<=30 else 15 if days<=90 else 10 if days<=365 else 5
                    except: pass
                raw_papers.append({"id": pid, "title": paper.get("title",""), "abstract": abstract[:500], "venue": venue, "tier": tier, "prestige_score": prestige, "recency_score": recency, "pub_date": pub_date, "url": paper.get("url",""), "authors": [a.get("name","") for a in (paper.get("authors") or [])[:3]]})
        except Exception as e:
            print(f"[DEBUG] Error: {e}")
    print(f"[DEBUG] Total fetched: {len(raw_papers)}")
    return {"raw_papers": raw_papers, "scored_papers": [], "output_file": state.get("output_file", f"{OUTPUT_DIR}/{datetime.now().strftime('%Y-%m-%d')}.json")}

def score_and_classify(state):
    print(f"[DEBUG] Scoring {len(state['raw_papers'])} papers...")
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    scored = []
    for i, paper in enumerate(state["raw_papers"][:MAX_PAPERS]):
        print(f"[DEBUG] Scoring {i+1}/{min(len(state['raw_papers']),MAX_PAPERS)}: {paper['title'][:60]}...")
        prompt = f"""You are evaluating an AI case study.
Title: {paper["title"]}
Abstract: {paper["abstract"]}
Venue: {paper["venue"]}

Score (0-25 each):
1. real_world_impact: measurable outcomes (%, $, time saved)
2. ai_maturity: 25=production at scale, 15=pilot, 5=concept

Also write:
- summary: 2 sentence plain English summary
- how_to_use: 1 sentence for an AI consultant
- monetisation: 1 sentence business opportunity

JSON only, no extra text:
{{"real_world_impact": 0, "ai_maturity": 0, "summary": "", "how_to_use": "", "monetisation": ""}}"""
        try:
            resp = client.chat.completions.create(model=GROQ_MODEL, messages=[{"role":"user","content":prompt}], max_tokens=300)
            text = resp.choices[0].message.content.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"): text = text[4:]
            text = text.strip()
            g = json.loads(text)
            total = paper["prestige_score"] + paper["recency_score"] + g.get("real_world_impact",0) + g.get("ai_maturity",0)
            scored.append({**paper, "real_world_impact": g.get("real_world_impact",0), "ai_maturity": g.get("ai_maturity",0), "total_score": total, "summary": g.get("summary",""), "how_to_use": g.get("how_to_use",""), "monetisation": g.get("monetisation","")})
            print(f"[DEBUG] Score: {total}/100")
        except Exception as e:
            print(f"[DEBUG] Error: {e}")
    scored.sort(key=lambda x: x["total_score"], reverse=True)
    print(f"[DEBUG] Scored {len(scored)} papers")
    return {"raw_papers": state["raw_papers"], "scored_papers": scored, "output_file": state["output_file"]}

def save_json(state):
    print("[DEBUG] Saving...")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output = {"timestamp": datetime.now().isoformat(), "total_fetched": len(state["raw_papers"]), "total_scored": len(state["scored_papers"]), "case_studies": state["scored_papers"]}
    with open(state["output_file"], "w") as f:
        json.dump(output, f, indent=2)
    print(f"Saved {len(state['scored_papers'])} case studies to {state['output_file']}")
    return state

def build_graph():
    g = StateGraph(CaseStudyState)
    g.add_node("fetch", fetch_case_studies)
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
    initial_state = {"raw_papers": [], "scored_papers": [], "output_file": f"{OUTPUT_DIR}/{datetime.now().strftime('%Y-%m-%d')}.json"}
    result = graph.invoke(initial_state, config={"configurable": {"thread_id": f"cs-{datetime.now().isoformat()}"}})
    print("[DEBUG] Complete")
    return result

if __name__ == "__main__":
    run_case_study_scout()
