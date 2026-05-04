#!/usr/bin/env python3
import os, json, re, argparse
from typing import TypedDict, Annotated
import operator
from youtube_transcript_api import YouTubeTranscriptApi
from groq import Groq
from langgraph.graph import StateGraph, START, END
from dotenv import load_dotenv

load_dotenv()

from pytube import YouTube

ELITE_CHANNELS = [
    "Andrej Karpathy",
    "DeepLearning.AI",
    "OpenAI",
    "Anthropic",
    "Google DeepMind",
    "Two Minute Papers"
]

class VideoState(TypedDict):
    url: str
    video_id: str
    transcript: str
    analysis: str
    output: dict
    bouncer_verdict: str
    metadata: dict

def get_video_id(url):
    pattern = r'(?:v=|\/)([0-9A-Za-z_-]{11}).*'
    match = re.search(pattern, url)
    return match.group(1) if match else None

def fetch_metadata_and_transcript(state: VideoState):
    video_id = get_video_id(state["url"])
    if not video_id:
        raise ValueError("Invalid YouTube URL")
    
    print(f"[DEBUG] Fetching metadata and transcript for {video_id}...")
    metadata = {"title": "Unknown", "author": "Unknown"}
    try:
        yt = YouTube(state["url"])
        metadata["title"] = yt.title
        metadata["author"] = yt.author
    except Exception as e:
        print(f"[DEBUG] Metadata error: {e}")

    try:
        try:
            transcript_obj = YouTubeTranscriptApi().fetch(video_id)
            transcript = " ".join([t.text for t in transcript_obj.snippets])
        except (AttributeError, TypeError):
            transcript_list = YouTubeTranscriptApi.get_transcript(video_id)
            transcript = " ".join([t["text"] for t in transcript_list])
            
        return {"video_id": video_id, "transcript": transcript, "metadata": metadata}
    except Exception as e:
        print(f"[DEBUG] Transcript error: {e}")
        return {"transcript": "ERROR: Could not fetch transcript", "metadata": metadata}

def bouncer_node(state: VideoState):
    if state["transcript"].startswith("ERROR"):
        return {"bouncer_verdict": "FAIL"}
        
    author = state.get("metadata", {}).get("author", "")
    is_elite = any(elite in author for elite in ELITE_CHANNELS)
    
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    
    if is_elite:
        rule = "This creator is on the ELITE WATCHLIST. Focus the summary on Foundational Principles and technical innovations."
    else:
        rule = (
            "This is an unknown creator. Strictly reject (FAIL) anything that is primarily marketing fluff, "
            "engagement farming, 'how to make money' content, or low-technical overview."
        )

    prompt = (
        "You are a Senior Tech Scout filtering for deep technical AI architecture. "
        f"Rule: {rule}\n\n"
        "Analyze this transcript. Is this a high-signal technical breakdown of an AI system, model, or architecture? "
        "Reply ONLY with PASS or FAIL.\n\n"
        f"Transcript: {state['transcript'][:8000]}"
    )
    
    print(f"[DEBUG] Running Bouncer (Elite: {is_elite})...")
    resp = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=10,
    )
    verdict = resp.choices[0].message.content.strip().upper()
    return {"bouncer_verdict": verdict}

def check_verdict(state: VideoState):
    if "PASS" in state["bouncer_verdict"]:
        return "pass"
    return "fail"

def analyst_node(state: VideoState):
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    author = state.get("metadata", {}).get("author", "")
    is_elite = any(elite in author for elite in ELITE_CHANNELS)
    
    persona = "Senior Tech Analyst"
    if is_elite:
        persona = "Principal AI Researcher"

    prompt = (
        f"You are a {persona}. Extract the deep technical architecture, key innovations, "
        "measurable performance results, and concrete business opportunities from this video transcript. "
        "Focus on 'how it works' rather than 'what it can do'.\n\n"
        f"Transcript: {state['transcript'][:12000]}"
    )
    
    print("[DEBUG] Running Analyst...")
    resp = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1000,
    )
    return {"analysis": resp.choices[0].message.content.strip()}

def editor_node(state: VideoState):
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    author = state.get("metadata", {}).get("author", "")
    is_elite = any(elite in author for elite in ELITE_CHANNELS)
    is_karpathy = "Karpathy" in author

    prompt = (
        "Format the following analysis into a highly structured JSON object. "
        "Required fields: 'title', 'summary', 'it_breakdown', 'monetisation', 'total_score', 'real_world_impact', 'ai_maturity', 'monetisation_potential', 'recency_score', 'score_reasoning', 'relevance_tag', 'authors'.\n\n"
        "Authors should be an array of strings.\n"
        "Scores should be integers (Impact/Maturity/Monetisation/Recency out of 25, total_score out of 100).\n"
        "Relevance tag should be one of: AI Agents, LLMs, Automation, Computer Vision, MLOps, or Other.\n\n"
        f"Analysis: {state['analysis']}\n\n"
        "JSON only."
    )
    
    print("[DEBUG] Running Editor...")
    resp = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"}
    )
    output = json.loads(resp.choices[0].message.content)
    output["source_type"] = "youtube_agent"
    output["url"] = state["url"]
    output["pub_date"] = datetime.now().strftime("%Y-%m-%d")
    
    if is_elite:
        output["is_evergreen"] = True
        
    if is_karpathy:
        # Karpathy Clause: floor of 90
        if output.get("total_score", 0) < 90:
            output["total_score"] = 90
            output["score_reasoning"] = "[KARPATHY CLAUSE] High-signal foundational content automatically prioritized. " + output.get("score_reasoning", "")

    return {"output": output}

def build_graph():
    builder = StateGraph(VideoState)
    
    builder.add_node("fetch", fetch_metadata_and_transcript)
    builder.add_node("bouncer", bouncer_node)
    builder.add_node("analyst", analyst_node)
    builder.add_node("editor", editor_node)
    
    builder.add_edge(START, "fetch")
    builder.add_edge("fetch", "bouncer")
    
    builder.add_conditional_edges(
        "bouncer",
        check_verdict,
        {
            "pass": "analyst",
            "fail": END
        }
    )
    
    builder.add_edge("analyst", "editor")
    builder.add_edge("editor", END)
    
    return builder.compile()

if __name__ == "__main__":
    from datetime import datetime
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="YouTube video URL")
    args = parser.parse_args()
    
    graph = build_graph()
    
    print(f"--- Starting Video Scout for: {args.url} ---")
    initial_state = {"url": args.url, "transcript": "", "analysis": "", "output": {}, "bouncer_verdict": ""}
    
    # Run the graph
    result = graph.invoke(initial_state)
    
    if "FAIL" in result.get("bouncer_verdict", ""):
        print("Video rejected: Shill content detected")
    elif result.get("output"):
        print("\n[RESULT] High-signal content found.")
        out_data = result["output"]
        video_id = result["video_id"]
        today = datetime.now().strftime("%Y%m%d")
        filename = f"v_{today}_{video_id}.json"
        
        # Ensure we write as a single case study wrapped in the standard daily format
        # or just as a single study if the generator can handle it.
        # Actually, the user asked for individual files in case_studies/.
        # Standard daily files look like {"case_studies": [...]}.
        final_output = {"case_studies": [out_data]}
        
        os.makedirs("case_studies", exist_ok=True)
        filepath = os.path.join("case_studies", filename)
        with open(filepath, "w") as f:
            json.dump(final_output, f, indent=2)
            
        print(f"Saved to {filepath}")
    
    print("\n--- Complete ---")
