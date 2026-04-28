import os

case_study_code = """#!/usr/bin/env python3
import os, json, requests, time
from datetime import datetime, timedelta
from typing import TypedDict, Annotated
import operator
import xml.etree.ElementTree as ET
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver
from groq import Groq
from dotenv import load_dotenv
load_dotenv()

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

def groq_complete(messages, max_tokens=500):
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
            return resp.choices[0].message.content.strip()
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
            "Evaluate this academic AI case study.\\n"
            "Title: " + paper["title"] + "\\n"
            "Authors: " + ", ".join(paper["authors"]) + "\\n"
            "Institution: " + paper["institution"] + "\\n"
            "Published: " + paper["pub_date"] + "\\n"
            "Abstract: " + paper["abstract"] + "\\n\\n"
            "Score each 0-25:\\n"
            "1. real_world_impact: measurable outcomes. 25=clear metrics at scale, 15=some metrics, 5=theoretical\\n"
            "2. ai_maturity: 25=live in production, 15=pilot, 5=concept\\n"
            "3. monetisation_potential: 25=clear product opportunity, 15=adaptable, 5=purely academic\\n\\n"
            "Write:\\n"
            "- summary: 3 sentences. Problem, AI approach, outcome\\n"
            "- how_to_apply: 2 sentences. Practical lesson for AI practitioners\\n"
            "- monetisation: 2 sentences. Concrete business opportunity\\n"
            "- relevance_tag: AI Agents, LLMs, Automation, Safety, Computer Vision, NLP, MLOps, Healthcare AI, or Other\\n"
            "- author_built_product: true if authors founded a company or shipped a product, false otherwise\\n\\n"
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
    archive.insert(0, {"date": today, "total": len(state["scored_papers"]), "case_studies": state["scored_papers"]})
    archive = archive[:90]
    with open(ARCHIVE_FILE, "w") as f:
        json.dump(archive, f, indent=2)
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
"""

news_code = """#!/usr/bin/env python3
import os, json, re, feedparser
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import TypedDict, Annotated
import operator
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver
from groq import Groq
from dotenv import load_dotenv
load_dotenv()

OUTPUT_DIR = "logs"
ARCHIVE_FILE = "logs/archive.json"
MAX_ITEMS = 25
EIGHT_WEEKS_AGO = datetime.now() - timedelta(weeks=8)
FOUR_WEEKS_AGO = datetime.now() - timedelta(weeks=4)

RSS_SOURCES = [
    {"url": "https://openai.com/news/rss.xml", "name": "OpenAI", "tier": "ai_labs", "window": "4w"},
    {"url": "https://www.anthropic.com/rss.xml", "name": "Anthropic", "tier": "ai_labs", "window": "4w"},
    {"url": "https://blog.google/technology/ai/rss/", "name": "Google AI", "tier": "ai_labs", "window": "4w"},
    {"url": "https://deepmind.google/blog/rss/", "name": "Google DeepMind", "tier": "ai_labs", "window": "4w"},
    {"url": "https://huggingface.co/blog/feed.xml", "name": "Hugging Face", "tier": "ai_labs", "window": "4w"},
    {"url": "https://blogs.microsoft.com/ai/feed/", "name": "Microsoft AI", "tier": "ai_labs", "window": "4w"},
    {"url": "https://sloanreview.mit.edu/feed/", "name": "MIT Sloan", "tier": "consulting", "window": "8w"},
    {"url": "https://hbr.org/rss/topic/ai", "name": "Harvard Business Review", "tier": "consulting", "window": "8w"},
    {"url": "https://www.mckinsey.com/capabilities/quantumblack/rss", "name": "McKinsey", "tier": "consulting", "window": "8w"},
    {"url": "https://www2.deloitte.com/us/en/rss/home.html", "name": "Deloitte", "tier": "consulting", "window": "8w"},
    {"url": "https://techcrunch.com/category/artificial-intelligence/feed/", "name": "TechCrunch", "tier": "news", "window": "4w"},
    {"url": "https://feeds.feedburner.com/venturebeat/SZYF", "name": "VentureBeat", "tier": "news", "window": "4w"},
    {"url": "https://www.technologyreview.com/feed/", "name": "MIT Tech Review", "tier": "news", "window": "4w"},
    {"url": "https://www.theverge.com/ai-artificial-intelligence/rss/index.xml", "name": "The Verge AI", "tier": "news", "window": "4w"},
    {"url": "https://www.wired.com/feed/tag/ai/latest/rss", "name": "Wired AI", "tier": "news", "window": "4w"},
]

def groq_complete(messages, max_tokens=400):
    keys = [os.getenv("GROQ_API_KEY"), os.getenv("GROQ_API_KEY_2")]
    keys = [k for k in keys if k]
    last_error = None
    for i, key in enumerate(keys):
        try:
            client = Groq(api_key=key)
            resp = client.chat.completions.create(
                model="llama3-8b-8192",
                messages=messages,
                max_tokens=max_tokens,
            )
            if i > 0:
                print("[DEBUG] Using Key 2")
            return resp.choices[0].message.content.strip()
        except Exception as e:
            if "429" in str(e):
                print("[DEBUG] Key " + str(i+1) + " rate limited, trying Key " + str(i+2) + "...")
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
                title = entry.get("title", "")
                url = entry.get("link", "")
                uid = url or title
                if uid in seen:
                    continue
                seen.add(uid)
                summary = re.sub(r"<[^>]+>", "", entry.get("summary", "") or entry.get("description", ""))[:600]
                raw_articles.append({
                    "id": uid, "title": title, "summary_raw": summary,
                    "source": source["name"], "tier": source["tier"],
                    "pub_date": pub[:10] if pub else "", "url": url,
                })
                count += 1
            print("[DEBUG] " + source["name"] + ": " + str(count) + " items")
        except Exception as e:
            print("[DEBUG] RSS error " + source["name"] + ": " + str(e))
    print("[DEBUG] Total: " + str(len(raw_articles)))
    today = datetime.now().strftime("%Y-%m-%d")
    return {"raw_articles": raw_articles, "classified_news": [], "output_file": state.get("output_file", OUTPUT_DIR + "/news_" + today + ".json")}

def classify_news(state):
    articles = state["raw_articles"]
    print("[DEBUG] Classifying " + str(len(articles)) + " articles...")
    classified = []
    limit = min(len(articles), MAX_ITEMS)
    for i in range(limit):
        article = articles[i]
        print("[DEBUG] " + str(i+1) + "/" + str(limit) + ": " + article["title"][:60])
        prompt = (
            "Classify this AI industry news item.\\n"
            "Title: " + article["title"] + "\\n"
            "Source: " + article["source"] + "\\n"
            "Published: " + article["pub_date"] + "\\n"
            "Content: " + article["summary_raw"] + "\\n\\n"
            "Write:\\n"
            "- summary: 2 sentences. What happened and why it matters\\n"
            "- enterprise_use: 2 sentences. How an enterprise or AI consultant can act on this now\\n"
            "- relevance_score: 1-10\\n"
            "- relevance_signal: high if >=7, medium if 4-6, low if <=3\\n"
            "- category: Product Launch, Research, Partnership, Market Move, Tool, Policy, or Other\\n\\n"
            'JSON only: {"summary": "", "enterprise_use": "", "relevance_score": 0, "relevance_signal": "", "category": ""}'
        )
        try:
            text = groq_complete([{"role": "user", "content": prompt}], max_tokens=300)
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

with open("case_study_scout_langgraph.py", "w") as f:
    f.write(case_study_code)

with open("industry_news_scout_langgraph.py", "w") as f:
    f.write(news_code)

print("case_study_scout_langgraph.py: " + str(os.path.getsize("case_study_scout_langgraph.py")) + " bytes")
print("industry_news_scout_langgraph.py: " + str(os.path.getsize("industry_news_scout_langgraph.py")) + " bytes")