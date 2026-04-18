import os
import json
import requests
import time
from datetime import datetime
from pathlib import Path
from groq import Groq

# SMB subreddits — non-technical business owners with real operational pain
SUBREDDITS = ["smallbusiness", "Entrepreneur", "sweatystartup", "ecommerce"]

# Pain keywords — business operations frustrations
PAIN_KEYWORDS = [
    "struggle", "problem", "issue", "wish", "frustrat",
    "help", "how do i", "cant", "can't", "doesn't work",
    "difficult", "stuck", "broken", "annoying", "hate when",
    "waste time", "manual", "spreadsheet", "overwhelm", "behind",
    "no-show", "churn", "losing", "burnout", "hiring",
    "invoice", "scheduling", "bookkeeping", "payroll", "inventory",
    "customer service", "reviews", "leads", "follow up", "follow-up",
    "onboarding", "quoting", "estimat", "too long", "hours spent",
    "automate", "automation", "repetitive", "tedious", "bottleneck"
]

llm = Groq(api_key=os.environ["GROQ_API_KEY"])
MODEL = "llama-3.3-70b-versatile"
CACHE_FILE = Path("seen_posts.json")

# Retry config
MAX_RETRIES = 3
INITIAL_BACKOFF = 5  # seconds
BACKOFF_MULTIPLIER = 2


def load_seen_posts():
    """Load the set of URLs we've already processed."""
    if CACHE_FILE.exists():
        with open(CACHE_FILE, "r") as f:
            return set(json.load(f).get("seen_urls", []))
    return set()


def save_seen_posts(seen_urls):
    """Save the set of seen URLs to cache."""
    with open(CACHE_FILE, "w") as f:
        json.dump({"seen_urls": list(seen_urls)}, f, indent=2)


def call_groq_with_retry(messages, temperature=0.4, max_retries=MAX_RETRIES):
    """Call Groq API with exponential backoff retry on 429."""
    backoff = INITIAL_BACKOFF
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            resp = llm.chat.completions.create(
                model=MODEL,
                messages=messages,
                temperature=temperature,
                response_format={"type": "json_object"}
            )
            return resp.choices[0].message.content
        except Exception as e:
            error_str = str(e)
            last_error = e
            
            # Check if it's a rate-limit error
            if "429" in error_str or "rate_limit" in error_str.lower():
                if attempt < max_retries:
                    wait_time = backoff * (BACKOFF_MULTIPLIER ** (attempt - 1))
                    print(f"\n    ⏳ Rate limited. Waiting {wait_time}s before retry {attempt}/{max_retries}...")
                    time.sleep(wait_time)
                    continue
            
            # Non-rate-limit error or final retry exhausted
            raise e
    
    # All retries exhausted
    raise last_error


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
    return posts[:15]


def classify_and_write(post, seen_urls):
    """Ask LLM: is this a real SMB pain point that AI/automation could solve?"""
    
    # Skip if we've seen this URL before
    if post["url"] in seen_urls:
        return None
    
    prompt = f"""You are a journalist for "The Agent Builder Daily", a newspaper that finds real business problems that AI and automation could solve for small business owners.

Analyse this Reddit post from a small business / entrepreneur forum. Decide: does it describe a CONCRETE, ACTIONABLE operational pain point that an AI agent, automation tool, or AI-powered service could solve?

You are looking for NON-TECHNICAL business owners who are struggling with day-to-day operations — scheduling, invoicing, customer service, hiring, marketing, inventory, quoting, bookkeeping, lead management, etc.

IGNORE posts that are:
- Asking for general business advice with no specific pain
- Promotional or self-promotional
- About AI/tech industry news (that's not what this page covers)
- Vague venting with no concrete problem
- About investment, crypto, or stock trading

Return ONLY valid JSON. No markdown, no prose.

IF the post IS a strong pain point, return:
{{
  "is_article": true,
  "headline": "Newspaper-style headline (max 12 words)",
  "category": "Pick the SINGLE best match from the list below",
  "importance": integer 1-10,
  "problem": "2-3 sentences describing the pain concretely — what is the business owner struggling with?",
  "solution": "2-3 sentences describing what an AI agent or automation could do to solve this",
  "monetisation": "One sentence on the business model (SaaS subscription, done-for-you service, etc.)",
  "trend_signal": "One sentence on whether this is growing, stable, or niche",
  "opportunity_type": "SAAS | FREELANCE | OPEN_SOURCE | CONSULTANCY | PRODUCT",
  "industry": "The specific industry or business type affected (e.g. Restaurants, Cleaning, Retail, Professional Services, etc.)"
}}

CATEGORY DEFINITIONS — pick the most specific match:
- SCHEDULING → appointment booking, no-shows, calendar management, shift planning
- INVOICING → billing, payments, quoting, estimates, chasing invoices
- CUSTOMER_SERVICE → reviews, complaints, response time, support tickets, chatbots
- MARKETING → lead gen, SEO, social media, email campaigns, ad spend
- HIRING → recruitment, onboarding, training, staff retention, HR admin
- OPERATIONS → inventory, supply chain, logistics, order management, workflow
- BOOKKEEPING → accounting, tax prep, expense tracking, payroll, cashflow
- SALES → CRM, follow-ups, proposals, pipeline management, closing
- ADMIN → data entry, document handling, compliance, forms, reporting

IMPORTANCE RUBRIC — use the full 1-10 scale:
- 10 → affects most small businesses across industries, no good AI solution exists yet, clear willingness to pay
- 8-9 → affects many businesses, current solutions are manual or expensive
- 6-7 → real pain for a specific industry segment, some workarounds exist
- 4-5 → niche but real, narrow audience
- 1-3 → minor gripe, edge case, or already well-solved by existing tools
DO NOT default to 8. Score honestly. Most posts should land 4-7.

IF the post is NOT a clear pain point (vague, unrelated, promotional, question-only, news/announcement), return:
{{"is_article": false, "reason": "short reason"}}

POST:
[r/{post['sub']} · {post['score']} upvotes · {post['comments']} comments]
Title: {post['title']}
Body: {post['body']}"""

    try:
        content = call_groq_with_retry([{"role": "user", "content": prompt}], temperature=0.4)
        result = json.loads(content)
        
        if result.get("is_article"):
            result["source_url"] = post["url"]
            result["source_sub"] = post["sub"]
            # Mark as seen
            seen_urls.add(post["url"])
            return result
        return None
    except Exception as e:
        raise e


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
        print(f"  INDUSTRY:      {a.get('industry', 'General')}")
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
    print("🔍 Scouting for SMB pain points AI could solve...\n")
    
    # Load seen URLs
    seen_urls = load_seen_posts()
    print(f"Loaded {len(seen_urls)} previously seen articles\n")
    
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
            article = classify_and_write(post, seen_urls)
            if article:
                print(f"✓ article ({article['category']} · {article['importance']}/10)")
                articles.append(article)
            else:
                print("✗ skipped")
        except Exception as e:
            print(f"⚠️  Error code: {e}")

    if not articles:
        print("\nNo articles generated. Try again later.")
        # Still save the seen URLs even if no new articles
        save_seen_posts(seen_urls)
        exit()

    issue = build_issue(articles)
    print_issue(issue)
    save_issue(issue)
    
    # Save updated seen URLs
    save_seen_posts(seen_urls)
