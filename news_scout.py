import os
import json
import requests
import feedparser
import time
from datetime import datetime, timedelta
from pathlib import Path
from groq import Groq

# Official company RSS feeds
RSS_FEEDS = [
    ("Anthropic", "https://www.anthropic.com/news/rss.xml"),
    ("OpenAI", "https://openai.com/news/rss.xml"),
    ("Google DeepMind", "https://deepmind.google/blog/rss.xml"),
    ("Hugging Face", "https://huggingface.co/blog/feed.xml"),
]

# HackerNews keywords — posts must mention one of these
HN_KEYWORDS = [
    "gpt", "claude", "gemini", "llama", "mistral", "qwen", "deepseek",
    "openai", "anthropic", "perplexity", "hugging face", "groq", "cerebras",
    "llm", "agent", "agentic", "rag", "fine-tun", "embedding",
    "ai model", "language model", "foundation model", "multimodal"
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


def call_groq_with_retry(messages, temperature=0.3, max_retries=MAX_RETRIES):
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


def fetch_blog_posts():
    """Fetch recent posts from official AI company blogs via RSS."""
    posts = []
    cutoff = datetime.now() - timedelta(days=7)  # Last 7 days only

    for name, url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:10]:
                # Parse publication date
                published = entry.get("published_parsed") or entry.get("updated_parsed")
                if published:
                    pub_date = datetime(*published[:6])
                    if pub_date < cutoff:
                        continue

                posts.append({
                    "source": name,
                    "source_type": "BLOG",
                    "title": entry.get("title", ""),
                    "summary": entry.get("summary", "")[:1000],
                    "url": entry.get("link", ""),
                    "published": pub_date.isoformat() if published else ""
                })
        except Exception as e:
            print(f"⚠️  Skipped {name} blog: {e}")

    return posts


def fetch_hackernews_posts():
    """Fetch AI-related stories from HackerNews front page."""
    posts = []
    headers = {"User-Agent": "news-scout/1.0"}

    try:
        # Get top story IDs
        top_url = "https://hacker-news.firebaseio.com/v0/topstories.json"
        top_ids = requests.get(top_url, headers=headers, timeout=10).json()[:50]

        for story_id in top_ids:
            story_url = f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json"
            story = requests.get(story_url, headers=headers, timeout=10).json()

            if not story or story.get("type") != "story":
                continue

            title = story.get("title", "").lower()
            text = story.get("text", "").lower()
            combined = title + " " + text

            if any(kw in combined for kw in HN_KEYWORDS):
                posts.append({
                    "source": "HackerNews",
                    "source_type": "HN",
                    "title": story.get("title", ""),
                    "summary": story.get("text", "")[:1000] or "",
                    "url": story.get("url", f"https://news.ycombinator.com/item?id={story_id}"),
                    "hn_discussion": f"https://news.ycombinator.com/item?id={story_id}",
                    "points": story.get("score", 0),
                    "comments": story.get("descendants", 0)
                })
    except Exception as e:
        print(f"⚠️  HackerNews fetch failed: {e}")

    # Sort by engagement
    posts.sort(key=lambda p: p.get("points", 0), reverse=True)
    return posts[:10]


def classify_news(post, seen_urls):
    """Ask LLM: is this a real AI news story? If yes, write an article."""
    
    # Skip if we've seen this URL before
    if post["url"] in seen_urls:
        return None
    
    source_info = f"[{post['source']} · {post['source_type']}]"
    if post.get("points"):
        source_info += f" {post['points']} points · {post['comments']} comments"

    prompt = f"""You are an editor for "The Agent Builder Daily", a newspaper for AI developers.

Analyse this item from an AI source and decide: is it a SIGNIFICANT news story about AI models, tools, releases, or research that developers should know about?

Return ONLY valid JSON. No markdown, no prose.

IF the item IS newsworthy, return:
{{
  "is_news": true,
  "headline": "Newspaper-style headline (max 12 words)",
  "category": "Pick the SINGLE best match",
  "importance": integer 1-10,
  "summary": "3-4 sentences summarising what happened and why it matters",
  "impact": "1-2 sentences on what this means for AI agent builders specifically",
  "news_type": "MODEL_RELEASE | PRODUCT_LAUNCH | RESEARCH | PRICING | PARTNERSHIP | POLICY | BENCHMARK"
}}

CATEGORY DEFINITIONS:
- MODELS → new model announcements, updates, benchmarks
- TOOLS → new SDKs, APIs, frameworks, dev tools
- RESEARCH → papers, technical breakthroughs, new techniques
- BUSINESS → funding, acquisitions, pricing changes, partnerships
- SAFETY → alignment, security, policy, regulation
- INFRASTRUCTURE → hosting, inference providers, hardware

IMPORTANCE RUBRIC:
- 10 → industry-changing (new frontier model, major acquisition)
- 8-9 → significant (new model capability, major tool release)
- 6-7 → notable (incremental update, useful new tool)
- 4-5 → niche interest
- 1-3 → minor or repetitive news
DO NOT default to 8. Score honestly.

IF the item is NOT newsworthy (minor blog post, promotional, off-topic, opinion-only), return:
{{"is_news": false, "reason": "short reason"}}

ITEM:
{source_info}
Title: {post['title']}
Summary: {post['summary']}"""

    try:
        content = call_groq_with_retry([{"role": "user", "content": prompt}], temperature=0.3)
        result = json.loads(content)
        
        if result.get("is_news"):
            result["source"] = post["source"]
            result["source_url"] = post["url"]
            if post.get("hn_discussion"):
                result["discussion_url"] = post["hn_discussion"]
            # Mark as seen
            seen_urls.add(post["url"])
            return result
        return None
    except Exception as e:
        raise e


def build_news_issue(articles):
    """Sort, flag top story, return as news issue."""
    articles.sort(key=lambda a: a["importance"], reverse=True)
    articles = articles[:7]  # Keep top 7 news items

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


def print_news(issue):
    print("\n" + "=" * 70)
    print(f"  INDUSTRY NEWS · {issue['issue_date']}")
    print(f"  {issue['article_count']} stories")
    print("=" * 70 + "\n")

    for i, a in enumerate(issue["articles"], 1):
        marker = "🔥 TOP" if a.get("top_story") else f"#{i}"
        print(f"{marker} · {a['category']} · {a['news_type']} · {a['importance']}/10")
        print(f"\n  {a['headline']}\n")
        print(f"  SUMMARY: {a['summary']}")
        print(f"  IMPACT:  {a['impact']}")
        print(f"  SOURCE:  {a['source']} - {a['source_url']}")
        print("\n" + "-" * 70 + "\n")


def save_news(issue):
    """Save news issue as JSON to news/YYYY-MM-DD.json"""
    news_dir = Path("news")
    news_dir.mkdir(exist_ok=True)

    filename = news_dir / f"{issue['issue_date']}.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(issue, f, indent=2, ensure_ascii=False)

    print(f"💾 Saved to {filename}")
    return filename


if __name__ == "__main__":
    print("📡 Scanning AI news sources...\n")

    # Load seen URLs
    seen_urls = load_seen_posts()
    print(f"Loaded {len(seen_urls)} previously seen articles\n")

    print("  Fetching official blogs...")
    blog_posts = fetch_blog_posts()
    print(f"  Found {len(blog_posts)} recent blog posts")

    print("  Fetching HackerNews front page...")
    hn_posts = fetch_hackernews_posts()
    print(f"  Found {len(hn_posts)} AI-related HN stories\n")

    all_posts = blog_posts + hn_posts

    if not all_posts:
        print("No news items found. Try again later.")
        exit()

    print(f"✍️  Analysing {len(all_posts)} items individually...\n")
    articles = []
    for i, post in enumerate(all_posts, 1):
        label = f"[{post['source']}] {post['title'][:50]}"
        print(f"  [{i}/{len(all_posts)}] {label}...", end=" ")
        try:
            article = classify_news(post, seen_urls)
            if article:
                print(f"✓ news ({article['category']} · {article['importance']}/10)")
                articles.append(article)
            else:
                print("✗ skipped")
        except Exception as e:
            print(f"⚠️  Error code: {e}")

    if not articles:
        print("\nNo news articles generated.")
        # Still save the seen URLs even if no new articles
        save_seen_posts(seen_urls)
        exit()

    issue = build_news_issue(articles)
    print_news(issue)
    save_news(issue)
    
    # Save updated seen URLs
    save_seen_posts(seen_urls)
