#!/usr/bin/env python3
import os, json, requests, time
from pathlib import Path
from datetime import datetime, timedelta
from typing import Annotated
from typing_extensions import TypedDict
import operator
import xml.etree.ElementTree as ET
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver
from groq import Groq
from dotenv import load_dotenv
from langfuse import Langfuse, observe, propagate_attributes
import llm_cache
import quality_checks
import judge
load_dotenv()

os.environ["LANGCHAIN_TRACING_V2"] = os.getenv("LANGCHAIN_TRACING_V2", "")
os.environ["LANGCHAIN_API_KEY"] = os.getenv("LANGCHAIN_API_KEY", "")
os.environ["LANGCHAIN_PROJECT"] = os.getenv("LANGCHAIN_PROJECT", "")

from langsmith import Client
client = Client()

os.environ.setdefault("LANGFUSE_PUBLIC_KEY", os.getenv("LANGFUSE_PUBLIC_KEY", ""))
os.environ.setdefault("LANGFUSE_SECRET_KEY", os.getenv("LANGFUSE_SECRET_KEY", ""))
os.environ.setdefault("LANGFUSE_HOST", os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"))
langfuse = Langfuse()

GROQ_TOKEN_STATS_FILE = "site/groq_token_stats.json"

# Model split: 8b for high-volume scoring (500K TPD), 70b for quality summaries (100K TPD)
MODEL_SCORING = "openai/gpt-oss-20b"
MODEL_SUMMARY = "openai/gpt-oss-20b"

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
MAX_PUBLISHED = 5
MIN_TOTAL = 20
MAX_ABSTRACT_CHARS = 4000  # safety cap only: arXiv abstracts sampled 2026-10-26 ran 1,041-1,889 chars
FOUR_MONTHS_AGO = datetime.now() - timedelta(days=120)
ARXIV_NS = "http://www.w3.org/2005/Atom"

QUERIES = [
    "abs:LLM+agent+AND+abs:production+AND+(cat:cs.AI+OR+cat:cs.SE+OR+cat:cs.DC)",
    "abs:agentic+AND+abs:deployment+AND+(cat:cs.AI+OR+cat:cs.SE+OR+cat:cs.DC)",
    "ti:automation+AND+abs:enterprise+AND+(cat:cs.AI+OR+cat:cs.SE+OR+cat:cs.DC)",
    "abs:RAG+AND+abs:production+AND+(cat:cs.AI+OR+cat:cs.CL+OR+cat:cs.SE)",
    "abs:MLOps+AND+(cat:cs.AI+OR+cat:cs.SE+OR+cat:cs.DC+OR+cat:cs.LG)",
    "abs:AI+agent+AND+abs:security+AND+(cat:cs.AI+OR+cat:cs.CR+OR+cat:cs.SE)",
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

def _parse_json_output(text):
    clean = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(clean)

def _try_this_steps(steps):
    """Exactly 3 non-empty steps of 25 words or fewer, else []: the email shows all three or none.
    shortcut: verb-first, real-tool and under-1-hour rules are enforced by the prompt only; add a judge check if they drift."""
    if not isinstance(steps, list):
        return []
    steps = [" ".join(s.split()).rstrip(";,") for s in steps if isinstance(s, str) and s.strip(" ;,")]
    return steps if len(steps) == 3 and all(len(s.split()) <= 25 for s in steps) else []

def _all_zero(g):
    return all(g.get(f, 0) == 0 for f in quality_checks.CS_SCORES)

def _clamp_scores(g, title):
    for f in quality_checks.CS_SCORES:
        try:
            v = int(g.get(f, 0) or 0)
        except (TypeError, ValueError):
            print(f"[WARN] {title[:60]}: {f}={g.get(f)!r} is not a number, set to 0")
            v = 0
        if not 0 <= v <= quality_checks.CS_MAX:
            print(f"[WARN] {title[:60]}: {f}={v} outside 0-{quality_checks.CS_MAX}, clamped")
            v = max(0, min(quality_checks.CS_MAX, v))
        g[f] = v

def _parse_score_output(text):
    # Cache gate: never cache an all-zero score, so the one retry reaches Groq.
    g = _parse_json_output(text)
    if _all_zero(g):
        raise ValueError("all-zero scores")
    return g

_quality = {}
_judge = {}
_run = {}

def _passes(validate, text):
    try:
        validate(text)
        return True
    except Exception:
        return False

@observe(as_type="generation", name="groq-complete")
def groq_complete(messages, max_tokens=500, model=MODEL_SUMMARY, validate=None, call_site=""):
    # validate: parser that raises on bad output. Only calls with a validator use the cache,
    # and a response is saved only if it passes.
    cache_key = None
    if validate is not None:
        cache_key = llm_cache.key(model, messages, None, max_tokens, "low", "hidden")
        cached = llm_cache.get(cache_key)
        if cached is not None:
            langfuse.update_current_generation(
                model=model, input=messages, output=cached,
                usage_details={"input": 0, "output": 0, "total": 0},
                metadata={"cache_hit": True, "model": model, "call_site": call_site},
            )
            return cached
    keys = [os.getenv("GROQ_API_KEY"), os.getenv("GROQ_API_KEY_2")]
    keys = [k for k in keys if k]
    last_error = None
    for i, key in enumerate(keys):
        try:
            client = Groq(api_key=key)
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                reasoning_effort="low",
                reasoning_format="hidden",
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
                print(f"[USAGE] Groq Tokens: {usage.total_tokens} (Key {i+1}, model={model})")
                langfuse.update_current_generation(
                    model=model,
                    input=messages,
                    output=result,
                    usage_details={
                        "input": usage.prompt_tokens,
                        "output": usage.completion_tokens,
                        "total": usage.total_tokens,
                    },
                    metadata={"groq_key": f"key_{i+1}", "model": model, "cache_hit": False, "call_site": call_site},
                )
            else:
                _groq_token_stats["header_miss_count"] += 1
                print("[WARN] Groq resp.usage missing — token count not captured")
                langfuse.update_current_generation(
                    model=model,
                    input=messages,
                    output=result,
                    metadata={"cache_hit": False, "call_site": call_site},
                )
            if cache_key and _passes(validate, result):
                llm_cache.put(cache_key, result, model, call_site)
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

@observe(name="fetch-papers")
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

    ARXIV_HEADERS = {
        "User-Agent": "ScoutDaily/1.0 (https://github.com/afarah101/agent-builder-daily; research aggregator)"
    }
    RETRY_DELAYS = [30, 60, 120]

    for query in QUERIES:
        try:
            date_filter = "+AND+submittedDate:[" + cutoff_str + "+TO+99991231]"
            url = "https://export.arxiv.org/api/query?search_query=" + query + date_filter + "&start=0&max_results=15&sortBy=submittedDate&sortOrder=descending"
            resp = None
            for attempt, delay in enumerate(RETRY_DELAYS, 1):
                try:
                    resp = requests.get(url, headers=ARXIV_HEADERS, timeout=45)
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
                abstract_text = " ".join((summary_el.text or "").split())[:MAX_ABSTRACT_CHARS]
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
            time.sleep(12)
        except Exception as e:
            print("[DEBUG] Error: " + str(e))

    print("[DEBUG] Total fetched: " + str(len(raw_papers)))
    today = datetime.now().strftime("%Y-%m-%d")
    return {"raw_papers": raw_papers, "scored_papers": [], "output_file": state.get("output_file", OUTPUT_DIR + "/" + today + ".json")}

@observe(name="score-papers")
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

Score on THREE criteria. Use these exact JSON keys:

real_world_impact (0-25): Does this directly affect how IT engineers work? Score high for: LLM APIs, agentic systems, MLOps, DevOps automation, security vulnerabilities, cloud infrastructure, endpoint management, SaaS tooling.
Score 0 for: medical imaging, geological modelling, social science, pure NLP benchmarks with no deployment angle.

ai_maturity (0-25): Is it built, tested and usable now, not theory? Released code, a working system or real deployment scores high. A proposal or position paper scores low.

monetisation_potential (0-25): Could an IT engineer use it, or sell it as a service, within 90 days?
"Could theoretically" = 0. Specific and actionable = 20+.

Be brutal — most papers score 0-10 on each criterion.

total_score = real_world_impact + ai_maturity + monetisation_potential (0-75).

IMPORTANT: If total_score is below 30, set "low_signal": true in the output.

Return JSON only. No markdown. No preamble.
{{"recency_score": 25, "real_world_impact": <0-25>, "ai_maturity": <0-25>, "monetisation_potential": <0-25>, "total_score": <sum, 0-75>, "score_reasoning": "", "relevance_tag": "", "gemini_eval_status": "pending", "low_signal": <true or false>}}

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

TRY THIS TODAY (try_this): exactly 3 steps an IT engineer can do today, each taking under 1 hour.
Each step starts with a verb, names a real tool or setting (for example "Intune compliance policy", "Okta sign-on policy", "Azure Monitor alert rule", "GitHub Actions workflow"), and is 25 words or fewer.
If the paper gives an IT engineer no practical action, return "try_this": [] instead of inventing steps.

Return JSON only. No markdown. No preamble.
{{"summary": "", "it_breakdown": "", "try_this": ["", "", ""]}}

Abstract:
{paper_block}"""
        max_retries = 3

        # --- Scoring prompt (uses 8b-instant — high TPD, sufficient for structured numeric output) ---
        g = None
        last_text = ""
        score_call = lambda: groq_complete(
            [{"role": "user", "content": score_prompt}],
            max_tokens=400,
            model=MODEL_SCORING,
            validate=_parse_score_output,
            call_site="case_study.score_papers",
        )
        for attempt in range(max_retries):
            try:
                text = score_call()
                last_text = text
                if text is None:
                    continue
                g = _parse_json_output(text)
                break
            except json.JSONDecodeError:
                print(f"[DEBUG] Score JSON parse error on attempt {attempt+1}/3 — retrying...")
                time.sleep(2)
                continue
        if g is None:
            print(f"[DEBUG] Score retries exhausted for: {paper['title'][:60]}")
            print(f"[DEBUG] Raw output: {last_text[:200]}")
            continue

        _clamp_scores(g, paper["title"])
        if _all_zero(g):
            print(f"[WARN] All scores 0 for: {paper['title'][:60]} — retrying scoring once")
            try:
                # Call Groq directly (validate=None skips cache read and write): a cached
                # response that clamps to all-zero would otherwise come back unchanged.
                retry = _parse_json_output(groq_complete(
                    [{"role": "user", "content": score_prompt}],
                    max_tokens=400,
                    model=MODEL_SCORING,
                    call_site="case_study.score_papers.retry",
                ))
                _clamp_scores(retry, paper["title"])
                if not _all_zero(retry):
                    g = retry
            except (json.JSONDecodeError, AttributeError) as e:
                print(f"[DEBUG] Score retry failed: {e}")
        score_failed = _all_zero(g)
        if score_failed:
            print(f"[QUALITY] score_failed (all scores 0 after retry), will not publish: {paper['title'][:60]}")

        # --- Summary prompt (uses 70b — quality matters for reader-facing output) ---
        s = {"summary": "", "it_breakdown": "", "monetisation": ""} if score_failed else None
        for attempt in range(0 if score_failed else max_retries):
            try:
                text = groq_complete(
                    [{"role": "user", "content": summary_prompt}],
                    max_tokens=800,  # was 350; hidden reasoning used all of 500 in the N1 live test and returned empty
                    model=MODEL_SUMMARY,
                    validate=_parse_json_output,
                    call_site="case_study.summary",
                )
                if text is None:
                    continue
                s = _parse_json_output(text)
                s["try_this"] = _try_this_steps(s.get("try_this"))
                break
            except json.JSONDecodeError:
                print(f"[DEBUG] Summary JSON parse error on attempt {attempt+1}/3 — retrying...")
                time.sleep(2)
                continue
        if s is None:
            print(f"[DEBUG] Summary retries exhausted — using empty fields for: {paper['title'][:60]}")
            s = {"summary": "", "it_breakdown": "", "monetisation": ""}

        g.update(s)
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
            "try_this": g.get("try_this", []),
            "monetisation": g.get("monetisation", ""),
            "relevance_tag": g.get("relevance_tag", "Other"),
            "author_built_product": g.get("author_built_product", False),
            "score_reasoning": g.get("score_reasoning", ""),
            "gemini_eval_status": g.get("gemini_eval_status", "pending"),
            "eval_verdict": "pending",  # set to "pass" in save_and_archive once the judge passes it
            "eval_flagged": False,
            "low_signal": bool(g.get("low_signal", False)),
            **({"score_failed": True} if score_failed else {}),
        })
        print("[DEBUG] Score: " + str(total) + "/75")
    scored.sort(key=lambda x: x["total_score"], reverse=True)
    print("[DEBUG] Scored " + str(len(scored)) + " papers")
    return {"raw_papers": state["raw_papers"], "scored_papers": scored, "output_file": state["output_file"]}

def rank_for_publish(items):
    """Highest total first; drop totals below MIN_TOTAL; keep the top MAX_PUBLISHED."""
    ranked = sorted(items, key=lambda x: x.get("total_score", 0), reverse=True)
    eligible = [x for x in ranked if x.get("total_score", 0) >= MIN_TOTAL]
    return eligible[:MAX_PUBLISHED], len(ranked) - len(eligible), max(0, len(eligible) - MAX_PUBLISHED)

@observe(name="save-and-archive")
def save_and_archive(state):
    print("[DEBUG] Saving...")
    kept, held, results = quality_checks.gate(state["scored_papers"])
    _quality.clear(); _quality.update(quality_checks.summarize(results))
    print(f"[QUALITY] {len(kept)} kept, {len(held)} held back")
    # try_this rides in the same batch: no extra Gemini call. Any failed or unjudged step drops all three.
    kept, judge_held, judge_stats = judge.gate(kept, "title", "abstract", "summary", "it_breakdown", steps_key="try_this")
    _judge.clear(); _judge.update(judge_stats)
    kept = [{**x, "eval_verdict": "pass"} for x in kept]
    published, below_min, beyond_top = rank_for_publish(kept)
    print(f"[RANK] Publishing {len(published)}; cut {below_min} below {MIN_TOTAL}/75, {beyond_top} beyond top {MAX_PUBLISHED}")
    state = {**state, "scored_papers": published}
    _run["written"] = len(published)
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

    # Update monitoring/metrics.json with real article counts
    try:
        metrics_path = Path("monitoring/metrics.json")
        if metrics_path.exists():
            metrics = json.loads(metrics_path.read_text())
            if metrics and isinstance(metrics, list):
                metrics[-1]["article_count"] = len(state["scored_papers"])
                metrics[-1]["total_fetched"] = len(state["raw_papers"])
                metrics_path.write_text(json.dumps(metrics, indent=2))
                print(f"[METRICS] Updated article_count: {len(state['scored_papers'])}")
    except Exception as e:
        print(f"[METRICS] Update failed: {e}")

    try:
        print(f"[CACHE] Saved {llm_cache.save()} entries to {llm_cache.CACHE_FILE}")
    except Exception as e:
        print(f"[CACHE] Save failed: {e}")

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

@observe(name="case-study-scout")
def run_case_study_scout():
    print("[DEBUG] Starting Case Study Scout (arXiv)...")
    today = datetime.now().strftime("%Y-%m-%d")
    with propagate_attributes(tags=["arxiv", "case-studies", "automated"]):
        langfuse.update_current_span(
            input={"date": today, "source": "arxiv"},
            metadata={"queries": len(QUERIES), "max_papers": MAX_PAPERS},
        )
        graph = build_graph()
        result = graph.invoke(
            {"raw_papers": [], "scored_papers": [], "output_file": OUTPUT_DIR + "/" + today + ".json"},
            config={"configurable": {"thread_id": "cs-" + today}},
        )
        langfuse.update_current_span(
            output={"papers_saved": _run.get("written", 0), "date": today},
        )
        if _quality:
            quality_checks.send_scores(langfuse, _quality)
        if _judge:
            judge.send_scores(langfuse, _judge)
    print("[DEBUG] Complete")
    langfuse.flush()

    # Write trace count to snapshot so remediation agent can verify observability
    try:
        import requests, base64
        public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "")
        secret_key = os.getenv("LANGFUSE_SECRET_KEY", "")
        host = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")
        if public_key and secret_key:
            auth = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
            # v2 has cursor-based pagination only (no totalItems) — loop all pages
            total = 0
            cursor = None
            status_ok = False
            while True:
                params = {"isRootObservation": "true", "limit": 1000, "fields": "core"}
                if cursor:
                    params["cursor"] = cursor
                r = requests.get(
                    f"{host}/api/public/v2/observations",
                    headers={"Authorization": f"Basic {auth}"},
                    params=params,
                    timeout=30,
                )
                if r.status_code != 200:
                    break
                status_ok = True
                body = r.json()
                data = body.get("data", [])
                total += len(data)
                cursor = body.get("meta", {}).get("cursor")
                if not cursor or not data:
                    break
            if status_ok:
                snapshot = {"total_traces": total, "updated_at": datetime.now().isoformat()}
                os.makedirs("site", exist_ok=True)
                Path("site/langfuse_snapshot.json").write_text(
                    json.dumps(snapshot, indent=2)
                )
                print(f"[DEBUG] LangFuse snapshot updated: {total} total traces")
    except Exception as e:
        print(f"[DEBUG] LangFuse snapshot write failed: {e}")

    return result

if __name__ == "__main__":
    run_case_study_scout()
    from langsmith import Client
    client = Client()
    import time
    time.sleep(5)  # allow trace buffer to flush
