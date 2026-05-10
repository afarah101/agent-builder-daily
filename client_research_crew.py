#!/usr/bin/env python3
import os, smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from dotenv import load_dotenv
from crewai import Agent, Task, Crew, Process, LLM
from crewai.tools import BaseTool
from duckduckgo_search import DDGS

load_dotenv()


# ─────────────────────────────────────────────
# LLM SETUP — Gemini 2.0 Flash
# ─────────────────────────────────────────────

llm = LLM(
    model="groq/llama-3.3-70b-versatile",
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
        "Search the web for real firsthand accounts of how people landed their "
        "first client as an AI automation or agentic AI solutions provider. "
        "Run these specific searches:\n"
        "1. 'how I got my first AI automation client reddit'\n"
        "2. 'first client AI solutions business linkedin'\n"
        "3. 'getting clients AI consulting freelance forum'\n"
        "4. 'agentic AI freelancer first contract how'\n"
        "5. 'AI automation agency first client uk'\n\n"
        "For each search, extract: the channel used, the specific tactic, "
        "the pitch or message that worked, any objections raised, and the outcome. "
        "Return raw findings with source URLs."
    ),
    expected_output=(
        "A structured list of 10-15 real findings from web sources, each with: "
        "source URL, channel used, tactic described, outcome mentioned."
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
        "Write a plain text weekly briefing email based on the analysis. "
        "Use this exact structure:\n\n"
        "HOW TO GET YOUR FIRST AI CLIENT - Weekly Intelligence\n"
        "[date]\n\n"
        "TOP CHANNELS THAT ACTUALLY WORK\n"
        "1. [channel] - [specific tactic in one sentence]\n"
        "2. [channel] - [specific tactic in one sentence]\n"
        "3. [channel] - [specific tactic in one sentence]\n\n"
        "PITCH ANGLES THAT CONVERT\n"
        "1. [angle] - [why it works]\n"
        "2. [angle] - [why it works]\n"
        "3. [angle] - [why it works]\n\n"
        "OBJECTIONS AND HOW TO HANDLE THEM\n"
        "1. [objection] - [response that worked]\n"
        "2. [objection] - [response that worked]\n\n"
        "WHAT DOES NOT WORK\n"
        "1. [tactic] - [why it fails]\n\n"
        "THIS WEEK'S ACTION\n"
        "One specific thing to do this week based on the research.\n\n"
        "Keep total length under 400 words. Plain text only."
    ),
    expected_output=(
        "A complete plain text email body ready to send, "
        "following the exact structure above, under 400 words."
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
