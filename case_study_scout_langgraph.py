#!/usr/bin/env python3
import os, json, requests, time
from datetime import datetime, timedelta
from typing import TypedDict, Annotated
import operator
import xml.etree.ElementTree as ET
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver
from groq import Groq
from dotenv import load_dotenv
from langfuse import Langfuse, observe
load_dotenv()

os.environ.setdefault("LANGFUSE_PUBLIC_KEY", os.getenv("LANGFUSE_PUBLIC_KEY", ""))
os.environ.setdefault("LANGFUSE_SECRET_KEY", os.getenv("LANGFUSE_SECRET_KEY", ""))
os.environ.setdefault("LANGFUSE_HOST", os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"))
langfuse = Langfuse()

OUTPUT_DIR = "case_studies"
ARCHIVE_FILE = "case_studies/archive.json"
MAX_PAPERS = 20
FOUR_MONTHS_AGO = datetime.now() - timedelta(days=120)
ARXIV_NS = "http://www.w3.org/2005/Atom"

QUERIES = [
    "ti:case+study+AND+cat:cs.AI",
    "ti:deployment+AND+abs:real+world+AND+cat:cs.AI",
    "abs:enterprise+AND+abs:implementation+AND+cat:cs.AI",
    "ti:case+study+AND+cat:cs.LG",
    "abs:production+system+AND+abs:AI+AND+cat:cs.CL",
    "ti:real+world+AND+abs:machine+learning+AND+cat:cs.AI",
]

UNIVERSITIES = [
    "Stanford", "Berkeley", "MIT", "Carnegie Mellon", "Toronto",
    "Oxford", "Cambridge", "ETH", "Singapore", "Tsinghua",
    "Peking", "Imperial", "UCL", "Washington", "Princeton",
    "NYU", "Harvard", "KAIST", "Nanyang", "Zhejiang",
]

@observe()
def groq_complete(messages, max_tokens=500, trace_name="groq_call"):
    keys = [os.getenv("GROQ_API_KEY"), os.getenv("GROQ_API_KEY_2")]
    keys = [k for k in keys if k]
    last_error = None
    for i, key in enumerate(keys):
        try:
            client = Groq(api_key=key)
            resp = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=messages,
                max_tokens=max_tokens,
            )
            if i > 0:
                print("[DEBUG] Using Key 2")
            result = resp.choices[0].message.content.strip()
            langfuse.update_current_generation(
                model="llama-3.3-70b-versatile",
                input=messages,
                output=result,
            )
            return result
        except Exception as e:
            if "429" in str(e):
                print("[DEBUG] Key " + str(i+1) + " rate limited, trying Key " + str(i+2) + "...")
                last_error = e
                continue
            raise e
    raise Exception("All keys exhausted: " + str(last_error))

class CaseStudyState(TypedDict):
    raw_papers: Annotated[list, operator.add]
    scored_papers: Annotated[list, operator.add]
    output_file: str

def fetch_papers(state):
    print("[DEBUG] Fetching from arXiv...")
    raw_papers = []
    seen = set()
    cutoff_str = FOUR_MONTHS_AGO.strftime("%Y%m%d")
    cutoff_date = FOUR_MONTHS_AGO.strftime("%Y-%m-%d")

    for query in QUERIES:
        try:
            date_filter = "+AND+submittedDate:[" + cutoff_str + "+TO+99991231]"
            url = "http://export.arxiv.org/api/query?search_query=" + query + date_filter + "&start=0&max_results=15&sortBy=submittedDate&sortOrder=descending"
            resp = requests.get(url, timeout=30)
            if resp.status_code != 200:
                print("[DEBUG] arXiv status: " + str(resp.status_code))
                time.sleep(3)
                continue

            root = ET.fromstring(resp.text)
            entries = root.findall("{" + ARXIV_NS + "}entry")
            print("[DEBUG] Query got " + str(len(entries)) + " entries")

            for entry in entries:
                pid_el = entry.find("{" + ARXIV_NS + "}id")
                title_el = entry.find("{" + ARXIV_NS + "}title")
                summary_el = entry.find("{" + ARXIV_NS + "}summary")
                published_el = entry.find("{" + ARXIV_NS + "}published")
                if pid_el is None or title_el is None or summary_el is None:
                    continue
                pid = pid_el.text.strip()
                if pid in seen:
                    continue
                seen.add(pid)
                title_text = " ".join((title_el.text or "").split())
                abstract_text = " ".join((summary_el.text or "").split())[:600]
                pub_date = (published_el.text or "")[:10] if published_el is not None else ""
                if len(abstract_text) < 80:
                    continue
                if pub_date and pub_date < cutoff_date:
                    continue
                authors = []
                for author in entry.findall("{" + ARXIV_NS + "}author")[:4]:
                    name_el = author.find("{" + ARXIV_NS + "}name")
                    if name_el is not None:
                        authors.append(name_el.text or "")
                institution = ""
                author_str = " ".join(authors).lower()
                for uni in UNIVERSITIES:
                    if uni.lower() in author_str:
                        institution = uni
                        break
                if not institution:
                    institution = "Academic Institution"
                link_url = pid
                for link_el in entry.findall("{" + ARXIV_NS + "}link"):
                    if link_el.get("type") == "text/html":
                        link_url = link_el.get("href", pid)
                        break
                try:
                    days = (datetime.now() - datetime.strptime(pub_date, "%Y-%m-%d")).days
                    if days <= 7: recency = 25
                    elif days <= 30: recency = 20
                    elif days <= 60: recency = 15
                    elif days <= 90: recency = 10
                    else: recency = 5
                except:
                    recency = 5
                raw_papers.append({
                    "id": pid, "title": title_text, "abstract": abstract_text,
                    "authors": authors, "institution": institution, "pub_date": pub_date,
                    "url": link_url, "recency_score": recency, "source_type": "arxiv",
                })
            time.sleep(3)
        except Exception as e:
            print("[DEBUG] Error: " + str(e))

    print("[DEBUG] Total fetched: " + str(len(raw_papers)))
    today = datetime.now().strftime("%Y-%m-%d")
    return {"raw_papers": raw_papers, "scored_papers": [], "output_file": state.get("output_file", OUTPUT_DIR + "/" + today + ".json")}

def score_papers(state):
    papers = state["raw_papers"]
    print("[DEBUG] Scoring " + str(len(papers)) + " papers...")
    scored = []
    limit = min(len(papers), MAX_PAPERS)
    for i in range(limit):
        paper = papers[i]
        print("[DEBUG] " + str(i+1) + "/" + str(limit) + ": " + paper["title"][:60])
        prompt = (
            "Evaluate this academic AI case study.\n"
            "Title: " + paper["title"] + "\n"
            "Authors: " + ", ".join(paper["authors"]) + "\n"
            "Institution: " + paper["institution"] + "\n"
            "Published: " + paper["pub_date"] + "\n"
            "Abstract: " + paper["abstract"] + "\n\n"
            "Score each 0-25:\n"
            "1. real_world_impact: measurable outcomes. 25=clear metrics at scale, 15=some metrics, 5=theoretical\n"
            "2. ai_maturity: 25=live in production, 15=pilot, 5=concept\n"
            "3. monetisation_potential: 25=clear product opportunity, 15=adaptable, 5=purely academic\n\n"
            "Write:\n"
            "- summary: 3 sentences. Problem, AI approach, outcome\n"
            "- how_to_apply: 2 sentences. Practical lesson for AI practitioners\n"
            "- monetisation: 2 sentences. Concrete business opportunity\n"
            "- relevance_tag: AI Agents, LLMs, Automation, Safety, Computer Vision, NLP, MLOps, Healthcare AI, or Other\n"
            "- author_built_product: true if authors founded a company or shipped a product, false otherwise\n\n"
            'JSON only: {"real_world_impact": 0, "ai_maturity": 0, "monetisation_potential": 0, "summary": "", "how_to_apply": "", "monetisation": "", "relevance_tag": "", "author_built_product": false}'
        )
        try:
            text = groq_complete([{"role": "user", "content": prompt}], max_tokens=500)
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            g = json.loads(text.strip())
            total = paper["recency_score"] + g.get("real_world_impact", 0) + g.get("ai_maturity", 0) + g.get("monetisation_potential", 0)
            scored.append({
                **paper,
                "real_world_impact": g.get("real_world_impact", 0),
                "ai_maturity": g.get("ai_maturity", 0),
                "monetisation_potential": g.get("monetisation_potential", 0),
                "total_score": total,
                "summary": g.get("summary", ""),
                "how_to_apply": g.get("how_to_apply", ""),
                "monetisation": g.get("monetisation", ""),
                "relevance_tag": g.get("relevance_tag", "Other"),
                "author_built_product": g.get("author_built_product", False),
            })
            print("[DEBUG] Score: " + str(total) + "/100")
        except Exception as e:
            print("[DEBUG] Error: " + str(e))
    scored.sort(key=lambda x: x["total_score"], reverse=True)
    print("[DEBUG] Scored " + str(len(scored)) + " papers")
    return {"raw_papers": state["raw_papers"], "scored_papers": scored, "output_file": state["output_file"]}

def save_and_archive(state):
    print("[DEBUG] Saving...")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output = {
        "timestamp": datetime.now().isoformat(),
        "filter": "last_4_months",
        "source": "arxiv",
        "total_fetched": len(state["raw_papers"]),
        "total_scored": len(state["scored_papers"]),
        "case_studies": state["scored_papers"],
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
    archive.insert(0, {"date": today, "total": len(state["scored_papers"]), "case_studies": state["scored_papers"]})
    archive = archive[:90]
    with open(ARCHIVE_FILE, "w", encoding="utf-8") as f:
        json.dump(archive, f, indent=2, ensure_ascii=False)
    print("Saved " + str(len(state["scored_papers"])) + " case studies.")
    return state

def build_graph():
    g = StateGraph(CaseStudyState)
    g.add_node("fetch", fetch_papers)
    g.add_node("score", score_papers)
    g.add_node("save", save_and_archive)
    g.add_edge(START, "fetch")
    g.add_edge("fetch", "score")
    g.add_edge("score", "save")
    g.add_edge("save", END)
    return g.compile(checkpointer=InMemorySaver())

def run_case_study_scout():
    print("[DEBUG] Starting Case Study Scout (arXiv)...")
    graph = build_graph()
    today = datetime.now().strftime("%Y-%m-%d")
    result = graph.invoke(
        {"raw_papers": [], "scored_papers": [], "output_file": OUTPUT_DIR + "/" + today + ".json"},
        config={"configurable": {"thread_id": "cs-" + today}},
    )
    print("[DEBUG] Complete")
    return result

if __name__ == "__main__":
    run_case_study_scout()
