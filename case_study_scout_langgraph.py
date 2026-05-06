#!/usr/bin/env python3
import os, json, requests, time
from pathlib import Path
from datetime import datetime, timedelta
from typing import TypedDict, Annotated
import operator
import xml.etree.ElementTree as ET
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver
from groq import Groq
from dotenv import load_dotenv
import google.genai as genai_client
from langfuse import Langfuse, observe
load_dotenv()

os.environ.setdefault("LANGFUSE_PUBLIC_KEY", os.getenv("LANGFUSE_PUBLIC_KEY", ""))
os.environ.setdefault("LANGFUSE_SECRET_KEY", os.getenv("LANGFUSE_SECRET_KEY", ""))
os.environ.setdefault("LANGFUSE_HOST", os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"))
langfuse = Langfuse()

GROQ_TOKEN_STATS_FILE = "site/groq_token_stats.json"

# Accumulates real token counts from resp.usage across this run
_groq_token_stats = {
    "key_1": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0},
    "key_2": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0},
    "tokens_per_call": [],
    "header_miss_count": 0,
}

OUTPUT_DIR = "case_studies"
ARCHIVE_FILE = "case_studies/archive.json"
MAX_PAPERS = 20
FOUR_MONTHS_AGO = datetime.now() - timedelta(days=120)
ARXIV_NS = "http://www.w3.org/2005/Atom"

QUERIES = [
    "ti:case+study+AND+(cat:cs.AI+OR+cat:cs.LG+OR+cat:cs.CL+OR+cat:cs.CV+OR+cat:cs.NI+OR+cat:cs.SE+OR+cat:cs.DC)",
    "ti:deployment+AND+abs:real+world+AND+(cat:cs.AI+OR+cat:cs.LG+OR+cat:cs.CL+OR+cat:cs.CV+OR+cat:cs.NI+OR+cat:cs.SE+OR+cat:cs.DC)",
    "abs:enterprise+AND+abs:implementation+AND+(cat:cs.AI+OR+cat:cs.LG+OR+cat:cs.CL+OR+cat:cs.SE+OR+cat:cs.DC)",
    "abs:production+system+AND+(cat:cs.AI+OR+cat:cs.LG+OR+cat:cs.CL+OR+cat:cs.NI+OR+cat:cs.SE+OR+cat:cs.DC)",
    "ti:real+world+AND+abs:machine+learning+AND+(cat:cs.AI+OR+cat:cs.LG+OR+cat:cs.CV+OR+cat:cs.NI)",
    "abs:automation+AND+abs:large+language+model+AND+(cat:cs.AI+OR+cat:cs.CL+OR+cat:cs.SE+OR+cat:cs.DC)",
]

BLOCKED_TAGS = [
    # Healthcare / medical
    "healthcare", "medical", "clinical", "drug", "patient",
    "hospital", "diagnosis", "radiology", "pathology", "genomic",
    "biomedical", "epidemiology", "pharmaceutical",
    # Policy / ethics / social science
    "ai policy", "ai governance", "ai regulation",
    "data governance", "content moderation",
    "electoral", "legislative", "regulatory compliance",
    "ethics committee",
    "social media manipulation", "social media toxicity", "misinformation", "disinformation",
    "political science", "political party", "political campaign", "election", "democracy", "hate speech",
    # Pure mathematics
    "topology", "algebraic", "theorem", "mathematical proof", "proof of theorem", "proof of lemma", "conjecture",
    "manifold", "homology", "sheaf", "lattice theory",
    # Natural sciences
    "biology", "chemistry", "physics", "neuroscience",
    "quantum", "molecular", "protein", "genomics",
    "climate", "meteorology", "geospatial", "reservoir",
    "seismic", "geological", "hydrology",
    # Other irrelevant domains
    "education", "pedagogy", "curriculum", "e-learning",
    "agriculture", "crop", "livestock", "farming",
    "legal proceedings", "court ruling", "litigation",
    "law enforcement"
]

def is_blocked(paper):
    text = (paper.get("title", "") + " " + paper.get("abstract", "")).lower()
    return any(tag in text for tag in BLOCKED_TAGS)

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
            # Capture real token usage from SDK response
            usage = getattr(resp, "usage", None)
            if usage:
                key_slot = "key_1" if i == 0 else "key_2"
                _groq_token_stats[key_slot]["prompt_tokens"]     += usage.prompt_tokens
                _groq_token_stats[key_slot]["completion_tokens"] += usage.completion_tokens
                _groq_token_stats[key_slot]["total_tokens"]      += usage.total_tokens
                _groq_token_stats[key_slot]["calls"]             += 1
                _groq_token_stats["tokens_per_call"].append(usage.total_tokens)
                print(f"[USAGE] Groq Tokens: {usage.total_tokens} (Key {i+1})")
            else:
                _groq_token_stats["header_miss_count"] += 1
                print("[WARN] Groq resp.usage missing — token count not captured")
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
    seen_ids = set()
    archive_path = Path("case_studies/archive.json")
    if archive_path.exists():
        with open(archive_path) as f:
            archive = json.load(f)
        for entry in archive:
            for cs in entry.get("case_studies", []):
                url = cs.get("id") or cs.get("url", "")
                if url:
                    seen_ids.add(url.strip())
    for f in Path("case_studies").glob("*.json"):
        if "archive" not in f.name:
            data = json.load(open(f))
            for cs in data.get("case_studies", []):
                url = cs.get("id") or cs.get("url", "")
                if url:
                    seen_ids.add(url.strip())
    print(f"[DEBUG] Cross-run dedup: {len(seen_ids)} known IDs loaded")
    cutoff_str = FOUR_MONTHS_AGO.strftime("%Y%m%d")
    cutoff_date = FOUR_MONTHS_AGO.strftime("%Y-%m-%d")

    for query in QUERIES:
        try:
            date_filter = "+AND+submittedDate:[" + cutoff_str + "+TO+99991231]"
            url = "http://export.arxiv.org/api/query?search_query=" + query + date_filter + "&start=0&max_results=15&sortBy=submittedDate&sortOrder=descending"
            RETRY_DELAYS = [10, 30, 60]
            resp = None
            for attempt, delay in enumerate(RETRY_DELAYS, 1):
                try:
                    resp = requests.get(url, timeout=45)
                    resp.raise_for_status()
                    break
                except Exception as e:
                    print(f"[ARXIV] Attempt {attempt} failed: {e}")
                    if attempt < len(RETRY_DELAYS):
                        print(f"[ARXIV] Retrying in {delay}s...")
                        time.sleep(delay)
                    else:
                        print(f"[ARXIV] All {len(RETRY_DELAYS)} attempts failed — skipping query")
                        resp = None
            if resp is None:
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
                if pid in seen_ids:
                    print(f"[DEBUG] Skipping duplicate: {pid}")
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

def gemini_evaluate(abstract, groq_output):
    try:
        client = genai_client.Client(api_key=os.getenv("GEMINI_API_KEY"))
        time.sleep(6)
        eval_prompt = (
            "You are a strict fact checker evaluating an AI-generated summary of an academic paper.\n"
            "Your job is to check if the summary is accurate and if the scores are reasonable.\n\n"
            "ORIGINAL ABSTRACT:\n" + abstract + "\n\n"
            "GENERATED SUMMARY: " + groq_output.get("summary", "") + "\n"
            "SCORES: impact=" + str(groq_output.get("real_world_impact", 0)) +
            ", maturity=" + str(groq_output.get("ai_maturity", 0)) +
            ", monetisation=" + str(groq_output.get("monetisation_potential", 0)) + "\n\n"
            "Answer in JSON only:\n"
            '{"hallucination_detected": false, "hallucination_detail": "", "scores_reasonable": true, "score_feedback": "", "verdict": "pass"}'
        )
        response = client.models.generate_content(model="gemini-2.0-flash-lite", contents=eval_prompt)
        text = response.text.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text.strip())
        print("[DEBUG] Gemini eval: " + result.get("verdict", "unknown"))
        time.sleep(4)
        return result
    except Exception as e:
        print("[DEBUG] Gemini eval error: " + str(e))
        return {"hallucination_detected": False, "verdict": "pass", "score_feedback": ""}

def score_papers(state):
    papers = state["raw_papers"]
    print("[DEBUG] Scoring " + str(len(papers)) + " papers...")
    scored = []
    limit = min(len(papers), MAX_PAPERS)
    for i in range(limit):
        paper = papers[i]
        print("[DEBUG] " + str(i+1) + "/" + str(limit) + ": " + paper["title"][:60])
        if is_blocked(paper):
            print(f"[FILTER] Blocked irrelevant paper: {paper.get('title', '')[:60]}")
            continue
        paper_block = (
            "Title: " + paper["title"] + "\n"
            "Authors: " + ", ".join(paper["authors"]) + "\n"
            "Institution: " + paper["institution"] + "\n"
            "Published: " + paper["pub_date"] + "\n"
            "Abstract: " + paper["abstract"]
        )
        score_prompt = f"""You are scoring AI research papers for a daily briefing read by IT engineers and IT consultants in the UK. Your reader manages real infrastructure: Microsoft 365, Google Workspace, JumpCloud, Okta, AWS, Azure, endpoint management, SSO, security tooling.

They are NOT machine learning researchers. They are NOT in healthcare, finance, or academia. If a paper is not directly relevant to IT infrastructure, software engineering, AI deployment, or automation — score it low.

Score on THREE criteria:

NOVELTY (0-33): Would an IT engineer forward this to a colleague saying "have you seen this?" Does it reveal something surprising about tools or systems they use?
Pure academic novelty with no IT angle = 0-5.

IT_RELEVANCE (0-33): Does this directly affect how IT engineers work? Score high for: LLM APIs, agentic systems, MLOps, DevOps automation, security vulnerabilities, cloud infrastructure, endpoint management, SaaS tooling.
Score 0 for: medical imaging, geological modelling, social science, pure NLP benchmarks with no deployment angle.

VALUE_90_DAYS (0-34): Could an IT engineer or consultant use this to build something, automate something, flag a risk, or propose a service to a client within 90 days?
"Could theoretically" = 0. Specific and actionable = 30+.
Be brutal — most papers score 0-10 here.

IMPORTANT: If total score is below 35, set "low_signal": true in the output.

Return JSON only. No markdown. No preamble.
{{"recency_score": 25, "real_world_impact": 0, "ai_maturity": 0, "monetisation_potential": 0, "total_score": 0, "score_reasoning": "", "relevance_tag": "", "gemini_eval_status": "pending", "low_signal": false}}

Paper abstract:
{paper_block}"""
        summary_prompt = f"""You are writing for IT engineers and IT consultants in the UK. Plain English only. No academic language. No jargon.

Write for someone who manages cloud infrastructure and wants to know if this research affects their work or creates an opportunity.

SUMMARY: 2-3 sentences. What problem does it solve? What did they build? What was the result? If it's not relevant to IT work, say so in one sentence.

HOW TO APPLY THIS (it_breakdown): Specific and direct.
Either:
- "If you manage X, here is what to do: step 1, step 2, step 3"
- "This flags a risk: if your org uses X, check Y immediately"
- "Not directly applicable to IT infrastructure work"
Never vague. Never "could be used for". Max 3 sentences.

Return JSON only. No markdown. No preamble.
{{"summary": "", "it_breakdown": ""}}

Abstract:
{paper_block}"""
        max_retries = 3

        # --- Scoring prompt ---
        g = None
        last_text = ""
        for attempt in range(max_retries):
            try:
                text = groq_complete([{"role": "user", "content": score_prompt}], max_tokens=400)
                last_text = text
                if text is None:
                    continue
                clean = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
                g = json.loads(clean)
                break
            except json.JSONDecodeError:
                print(f"[DEBUG] Score JSON parse error on attempt {attempt+1}/3 — retrying...")
                time.sleep(2)
                continue
        if g is None:
            print(f"[DEBUG] Score retries exhausted for: {paper['title'][:60]}")
            print(f"[DEBUG] Raw output: {last_text[:200]}")
            continue

        # --- Summary prompt ---
        s = None
        for attempt in range(max_retries):
            try:
                text = groq_complete([{"role": "user", "content": summary_prompt}], max_tokens=350)
                if text is None:
                    continue
                clean = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
                s = json.loads(clean)
                break
            except json.JSONDecodeError:
                print(f"[DEBUG] Summary JSON parse error on attempt {attempt+1}/3 — retrying...")
                time.sleep(2)
                continue
        if s is None:
            print(f"[DEBUG] Summary retries exhausted — using empty fields for: {paper['title'][:60]}")
            s = {"summary": "", "it_breakdown": "", "monetisation": ""}

        g.update(s)
        if g.get("low_signal") and g.get("total_score", 0) < 35:
            print(f"[FILTER] Low signal paper dropped: {paper['title'][:60]}")
            continue
        if i < 5:
            eval_result = gemini_evaluate(paper["abstract"], g)
            if eval_result.get("hallucination_detected"):
                print("[DEBUG] Hallucination detected — flagging paper")
            eval_verdict = eval_result.get("verdict", "pass")
            eval_flagged = eval_result.get("hallucination_detected", False)
        else:
            eval_verdict = "pending"
            eval_flagged = False
            eval_result = {}
        total = g.get("real_world_impact", 0) + g.get("ai_maturity", 0) + g.get("monetisation_potential", 0)
        scored.append({
            **paper,
            "real_world_impact": g.get("real_world_impact", 0),
            "ai_maturity": g.get("ai_maturity", 0),
            "monetisation_potential": g.get("monetisation_potential", 0),
            "total_score": total,
            "recency_score": 25,
            "summary": g.get("summary", ""),
            "it_breakdown": g.get("it_breakdown", ""),
            "monetisation": g.get("monetisation", ""),
            "relevance_tag": g.get("relevance_tag", "Other"),
            "author_built_product": g.get("author_built_product", False),
            "score_reasoning": g.get("score_reasoning", ""),
            "gemini_eval_status": g.get("gemini_eval_status", "pending"),
            "eval_verdict": eval_verdict,
            "eval_flagged": eval_flagged,
        })
        print("[DEBUG] Score: " + str(total) + "/100")
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
    # Write real token stats for the dashboard
    tpc = _groq_token_stats["tokens_per_call"]
    snapshot = {
        "generated_at": datetime.now().isoformat(),
        "key_1": _groq_token_stats["key_1"],
        "key_2": _groq_token_stats["key_2"],
        "tokens_per_call": tpc,
        "avg_tokens_per_call": round(sum(tpc) / len(tpc)) if tpc else 0,
        "header_miss_count": _groq_token_stats["header_miss_count"],
        "daily_limit_per_key": 100000,
    }
    os.makedirs("site", exist_ok=True)
    with open(GROQ_TOKEN_STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2)
    print(f"[DEBUG] Groq token stats written to {GROQ_TOKEN_STATS_FILE}")
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
