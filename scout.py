import os
import json
import requests
from datetime import datetime
from pathlib import Path
from groq import Groq

SUBREDDITS = ["AI_Agents", "LocalLLaMA", "ClaudeAI", "OpenAI"]
PAIN_KEYWORDS = [
    "struggle", "problem", "issue", "wish", "frustrat",
    "help", "how do i", "cant", "can't", "doesn't work",
    "difficult", "stuck", "broken", "annoying", "hate when"
]

llm = Groq(api_key=os.environ["GROQ_API_KEY"])
MODEL = "llama-3.3-70b-versatile"


def fetch_reddit_posts():
    """Fetch recent posts from target subreddits and filter for pain signals."""
    posts = []
    headers = {"User-Agent": "scout-agent/1.0 by Ahmed"}

    for sub in SUBREDDITS:
        url = f"https://www.reddit.com/r/{sub}/new.json?limit=25"
        try:
            r = requests.get(url, headers=headers, timeout=10)
            r.raise_for_status()
            for item in r.json()["data"]["children"]:
                d = item["data"]
                text = (d["title"] + " " + d.get("selftext", "")).lower()
                if any(kw in text for kw in PAIN_KEYWORDS):
                    posts.append({
                        "title": d["title"],
                        "body": d.get("selftext", "")[:800],
                        "url": f"https://reddit.com{d['permalink']}",
                        "sub": sub,
                        "score": d.get("score", 0),
                        "comments": d.get("num_comments", 0)
                    })
        except Exception as e:
            print(f"⚠️  Skipped r/{sub}: {e}")

    posts.sort(key=lambda p: p["score"] + p["comments"], reverse=True)
    return posts[:12]


def classify_and_write(post):
    """Ask LLM: is this a real pain point? If yes, write an article."""
    prompt = f"""You are a journalist for "The Agent Builder Daily", a newspaper about AI agent opportunities.

Analyse this Reddit post and decide: does it describe a CONCRETE, ACTIONABLE pain point someone could build an AI agent to solve?

Return ONLY valid JSON. No markdown, no prose.

IF the post IS a strong pain point, return:
{{
  "is_article": true,
  "headline": "Newspaper-style headline (max 12 words)",
  "category": "Pick the SINGLE best match",
  "importance": integer 1-10,
  "problem": "2-3 sentences describing the pain concretely",
  "solution": "2-3 sentences describing what an AI agent could do",
  "monetisation": "One sentence on the business model",
  "trend_signal": "One sentence on whether this is growing, stable, or niche",
  "opportunity_type": "SAAS | FREELANCE | OPEN_SOURCE | CONSULTANCY | PRODUCT"
}}

CATEGORY DEFINITIONS — pick the most specific match:
- INFRASTRUCTURE → hosting, deployment, scaling, latency, cold starts, rate limits
- MEMORY → context loss, session persistence, long context handling, RAG
- TOOLING → debugging, observability, testing, dev experience, SDKs
- UX → end-user interface, conversation flow, voice, output quality
- INTEGRATION → connecting services, APIs, auth, interop between tools (USE SPARINGLY — only when it's primarily about connecting things)
- SECURITY → auth, permissions, data privacy, prompt injection, safety
- OBSERVABILITY → logging, monitoring, tracing, analytics, cost tracking
- DATA → ingestion, ETL, quality, labelling, embeddings, vector stores
- ORCHESTRATION → multi-agent coordination, workflows, task delegation

IMPORTANCE RUBRIC — use the full 1-10 scale:
- 10 → affects most agent builders, no good solution exists, clear business model
- 8-9 → affects many builders, solutions are poor/expensive
- 6-7 → real pain for a specific segment, some workarounds exist
- 4-5 → niche but real, narrow audience
- 1-3 → minor gripe, edge case, or already well-solved
DO NOT default to 8. Score honestly. Most posts should land 4-7.

IF the post is NOT a clear pain point (vague, unrelated, promotional, question-only, news/announcement), return:
{{"is_article": false, "reason": "short reason"}}

POST:
[r/{post['sub']} · {post['score']} upvotes · {post['comments']} comments]
Title: {post['title']}
Body: {post['body']}"""

    resp = llm.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4,
        response_format={"type": "json_object"}
    )

    result = json.loads(resp.choices[0].message.content)
    if result.get("is_article"):
        result["source_url"] = post["url"]
        result["source_sub"] = post["sub"]
        return result
    return None


def build_issue(articles):
    """Sort, flag top story, return as issue."""
    articles.sort(key=lambda a: a["importance"], reverse=True)
    articles = articles[:5]

    if articles:
        articles[0]["top_story"] = True
        for a in articles[1:]:
            a["top_story"] = False

    return {
        "issue_date": datetime.now().strftime("%Y-%m-%d"),
        "issue_datetime": datetime.now().isoformat(),
        "article_count": len(articles),
        "articles": articles
    }


def print_issue(issue):
    print("\n" + "=" * 70)
    print(f"  THE AGENT BUILDER DAILY · {issue['issue_date']}")
    print(f"  {issue['article_count']} articles")
    print("=" * 70 + "\n")

    for i, a in enumerate(issue["articles"], 1):
        marker = "🔥 TOP STORY" if a.get("top_story") else f"#{i}"
        print(f"{marker} · {a['category']} · Importance: {a['importance']}/10")
        print(f"\n  {a['headline']}\n")
        print(f"  PROBLEM:       {a['problem']}")
        print(f"  SOLUTION:      {a['solution']}")
        print(f"  MONETISATION:  {a['monetisation']} ({a['opportunity_type']})")
        print(f"  TREND:         {a['trend_signal']}")
        print(f"  SOURCE:        {a['source_url']}")
        print("\n" + "-" * 70 + "\n")


def save_issue(issue):
    """Save issue as JSON to issues/YYYY-MM-DD.json"""
    issues_dir = Path("issues")
    issues_dir.mkdir(exist_ok=True)

    filename = issues_dir / f"{issue['issue_date']}.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(issue, f, indent=2, ensure_ascii=False)

    print(f"💾 Saved to {filename}")
    return filename


if __name__ == "__main__":
    print("🔍 Scouting for AI agent opportunities...\n")
    posts = fetch_reddit_posts()
    print(f"Found {len(posts)} pain-signal posts\n")

    if not posts:
        print("No relevant posts found. Try again later.")
        exit()

    print(f"✍️  Analysing {len(posts)} posts individually...\n")
    articles = []
    for i, post in enumerate(posts, 1):
        print(f"  [{i}/{len(posts)}] {post['title'][:60]}...", end=" ")
        try:
            article = classify_and_write(post)
            if article:
                print(f"✓ article ({article['category']} · {article['importance']}/10)")
                articles.append(article)
            else:
                print("✗ skipped")
        except Exception as e:
            print(f"⚠️  {e}")

    if not articles:
        print("\nNo articles generated. Try again later.")
        exit()

    issue = build_issue(articles)
    print_issue(issue)
    save_issue(issue)
