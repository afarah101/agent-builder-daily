#!/usr/bin/env python3
import os, smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from dotenv import load_dotenv
from crewai import Agent, Task, Crew, Process, LLM
from crewai.tools import BaseTool
try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS

load_dotenv()


# ─────────────────────────────────────────────
# LLM SETUP — Gemini 2.0 Flash
# ─────────────────────────────────────────────

llm = LLM(
    model="groq/llama-3.3-70b-versatile",
    max_tokens=2000,
    api_key=os.getenv("GROQ_API_KEY"),
    temperature=0.3
)


# ─────────────────────────────────────────────
# SEARCH TOOL — DuckDuckGo (no API key)
# ─────────────────────────────────────────────

class DuckDuckGoSearchTool(BaseTool):
    name: str = "web_search"
    description: str = "Search the web for real posts, threads, and discussions"

    def _run(self, query: str) -> str:
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=8))
            output = []
            for r in results:
                output.append(f"Title: {r.get('title','')}")
                output.append(f"URL: {r.get('href','')}")
                output.append(f"Snippet: {r.get('body','')}")
                output.append("---")
            return "\n".join(output) if output else "No results found"
        except Exception as e:
            return f"Search error: {e}"

search_tool = DuckDuckGoSearchTool()


# ─────────────────────────────────────────────
# AGENT 1 — RESEARCHER
# ─────────────────────────────────────────────

researcher = Agent(
    role="Senior Market Researcher",
    goal=(
        "Find real, firsthand accounts from people who have successfully "
        "landed their first client as an AI automation or agentic AI solutions provider. "
        "Search Reddit, LinkedIn, forums, and blog posts. "
        "Focus on concrete tactics, channels, and scripts that worked — not theory."
    ),
    backstory=(
        "You are an expert at finding signal in online communities. "
        "You know where practitioners share real experiences — not where marketers post fluff. "
        "You only report what you actually find, never fabricate sources."
    ),
    tools=[search_tool],
    llm=llm,
    verbose=True,
    max_iter=5
)


# ─────────────────────────────────────────────
# AGENT 2 — ANALYST
# ─────────────────────────────────────────────

analyst = Agent(
    role="Business Intelligence Analyst",
    goal=(
        "Analyse the raw research and identify repeatable patterns. "
        "What channels appear most frequently? What pitch angles actually converted? "
        "What objections came up and how were they overcome? "
        "Separate tactics that worked from tactics people tried but failed."
    ),
    backstory=(
        "You are a pattern recognition specialist with deep experience in B2B sales intelligence. "
        "You cut through noise and identify what actually drives results. "
        "You never pad reports with generic advice — only evidence-backed findings."
    ),
    llm=llm,
    verbose=True,
    max_iter=3
)


# ─────────────────────────────────────────────
# AGENT 3 — SYNTHESISER
# ─────────────────────────────────────────────

synthesiser = Agent(
    role="Strategic Communications Specialist",
    goal=(
        "Turn the analyst's findings into a clean, scannable weekly briefing "
        "for an IT engineer and AI solutions business owner based in the UK. "
        "Format must be plain text email — no markdown, no bullet symbols that "
        "break in email clients. Use numbered lists and clear section headers. "
        "Keep it under 400 words. Every point must be immediately actionable."
    ),
    backstory=(
        "You write for busy founders who have 90 seconds to read your output. "
        "You ruthlessly cut anything that isn't directly useful. "
        "Your output goes straight into a Gmail email — no formatting tricks."
    ),
    llm=llm,
    verbose=True,
    max_iter=3
)


# ─────────────────────────────────────────────
# TASKS
# ─────────────────────────────────────────────

research_task = Task(
    description=(
        "Search the web for real firsthand accounts of landing first AI "
        "automation clients. Run these exact searches:\n"
        "1. 'how I got my first AI agency client'\n"
        "2. 'site:reddit.com AI automation first client'\n"
        "3. 'first client AI consulting what worked'\n"
        "4. 'AI automation agency client acquisition strategy'\n"
        "5. 'how to get clients AI solutions UK freelance'\n\n"
        "For EACH result you find, extract and return:\n"
        "- Exact source URL\n"
        "- The specific channel they used (LinkedIn/Reddit/cold outreach etc)\n"
        "- The exact tactic or script they used — quote directly where possible\n"
        "- The outcome (did it work, how long did it take)\n"
        "- Any specific numbers mentioned (response rates, deal sizes, timelines)\n\n"
        "Do NOT summarise or paraphrase. Extract real quotes and specifics. "
        "If a source has no concrete tactic, skip it."
    ),
    expected_output=(
        "10-15 specific findings, each with: source URL, channel, "
        "exact tactic quoted directly from source, outcome with numbers where available."
    ),
    agent=researcher
)

analysis_task = Task(
    description=(
        "Analyse the research findings and identify the top patterns. "
        "Group findings into: (1) Channels that worked, (2) Pitch angles that converted, "
        "(3) Common objections and how they were overcome, (4) What did NOT work. "
        "Rank each by frequency of mention across sources. "
        "Be specific — name the actual tactics, not generalisations."
    ),
    expected_output=(
        "A ranked analysis with top 3-5 items in each category, "
        "each backed by at least one specific example from the research."
    ),
    agent=analyst,
    context=[research_task]
)

synthesis_task = Task(
    description=(
        "Write a plain text weekly briefing based strictly on the research findings. "
        "Every single point MUST include a specific example from a real source. "
        "No generalisations. No theory. Only what real people actually did.\n\n"
        "Use this exact structure:\n\n"
        "HOW TO GET YOUR FIRST AI CLIENT - Weekly Intelligence\n"
        "[date]\n\n"
        "TOP CHANNELS THAT ACTUALLY WORK\n"
        "1. [Channel] - [Exact tactic from research] (Source: [URL])\n"
        "2. [Channel] - [Exact tactic from research] (Source: [URL])\n"
        "3. [Channel] - [Exact tactic from research] (Source: [URL])\n\n"
        "WHAT THEY ACTUALLY SAID\n"
        "Direct quotes from real practitioners — minimum 3 quotes with source URLs\n\n"
        "PITCH ANGLES THAT CONVERTED\n"
        "1. [Specific angle with example of how it was used]\n"
        "2. [Specific angle with example of how it was used]\n\n"
        "OBJECTIONS AND EXACT RESPONSES THAT WORKED\n"
        "1. [Objection] - [Word-for-word or close response that worked]\n"
        "2. [Objection] - [Word-for-word or close response that worked]\n\n"
        "WHAT DOES NOT WORK\n"
        "1. [Specific tactic] - [Why, with evidence from research]\n\n"
        "THIS WEEK'S ONE ACTION\n"
        "[One hyper-specific action — not generic advice. "
        "Example: Post in r/Entrepreneur introducing yourself and offering "
        "a free 30-min workflow audit to the first 3 people who reply.]\n\n"
        "Keep under 500 words. Plain text only. No markdown symbols."
    ),
    expected_output=(
        "A complete plain text email body ready to send, "
        "following the exact structure above, under 500 words, "
        "with real source URLs cited for every point."
    ),
    agent=synthesiser,
    context=[research_task, analysis_task]
)


# ─────────────────────────────────────────────
# EMAIL FUNCTION
# ─────────────────────────────────────────────

def send_email(body: str):
    gmail_user = os.getenv("GMAIL_USER")
    gmail_password = os.getenv("GMAIL_APP_PASSWORD")
    recipient = os.getenv("REPORT_EMAIL", gmail_user)

    if not gmail_user or not gmail_password:
        print("[CREW] Gmail credentials not set — printing report instead:")
        print(body)
        return

    # Sanitise to ASCII to avoid SMTP codec issues
    body = (body
        .replace("—", "-")
        .replace("–", "-")
        .replace("•", "*")
        .replace(" ", " ")
        .encode("ascii", "replace").decode("ascii"))

    msg = MIMEMultipart()
    msg["From"] = gmail_user
    msg["To"] = recipient
    msg["Subject"] = f"How to Get Your First AI Client - Weekly Intel {datetime.now().strftime('%d %b %Y')}"
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_password)
            server.send_message(msg)
        print(f"[CREW] Email sent to {recipient}")
    except Exception as e:
        print(f"[CREW] Email failed: {e}")
        print(body)


# ─────────────────────────────────────────────
# CREW AND MAIN RUNNER
# ─────────────────────────────────────────────

def run_client_research_crew():
    print("[CREW] Starting Client Research Crew...")
    print(f"[CREW] Run time: {datetime.now().strftime('%d %b %Y, %H:%M')}")

    crew = Crew(
        agents=[researcher, analyst, synthesiser],
        tasks=[research_task, analysis_task, synthesis_task],
        process=Process.sequential,
        verbose=True
    )

    result = crew.kickoff()
    report = str(result)

    print("\n[CREW] Research complete. Sending email...")
    send_email(report)
    print("[CREW] Done.")
    return result

if __name__ == "__main__":
    run_client_research_crew()
