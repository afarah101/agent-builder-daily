#!/usr/bin/env python3
import os, json, re, time
from datetime import datetime
from typing import TypedDict, Annotated
import operator
from youtube_transcript_api import YouTubeTranscriptApi
from groq import Groq
from langgraph.graph import StateGraph, START, END
from dotenv import load_dotenv

load_dotenv()

ELITE_CHANNELS = [
    "Andrej Karpathy",
    "DeepLearning.AI",
    "OpenAI",
    "Anthropic",
    "Google DeepMind",
    "Two Minute Papers"
]

QUEUE_FILE = "video_queue.json"
MAX_TRANSCRIPT_CHARS = 40000  # ~15,000 tokens

def groq_call(**kwargs):
    """Try Key 1 then Key 2 on 429. Raises RuntimeError if both exhausted."""
    keys = [os.getenv("GROQ_API_KEY"), os.getenv("GROQ_API_KEY_2")]
    keys = [k for k in keys if k]
    for i, key in enumerate(keys):
        try:
            client = Groq(api_key=key)
            return client.chat.completions.create(**kwargs)
        except Exception as e:
            if "429" in str(e):
                print(f"[VIDEO] Key {i+1} rate limited — trying next key...")
                continue
            raise
    raise RuntimeError("[VIDEO] All Groq keys rate limited — deferring video")

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

    metadata = {"title": "Unknown", "author": "Unknown", "channel": "Unknown"}
    try:
        import urllib.request
        api_url = f"https://www.youtube.com/oembed?url={state['url']}&format=json"
        with urllib.request.urlopen(api_url, timeout=5) as r:
            data = json.loads(r.read())
            metadata["title"] = data.get("title", "Unknown")
            metadata["author"] = data.get("author_name", "Unknown")
            metadata["channel"] = data.get("author_name", "Unknown")
    except Exception as e:
        print(f"[DEBUG] Metadata fallback error: {e}")

    try:
        try:
            transcript_obj = YouTubeTranscriptApi().fetch(video_id)
            transcript = " ".join([t.text for t in transcript_obj.snippets])
        except (AttributeError, TypeError):
            transcript_list = YouTubeTranscriptApi.get_transcript(video_id)
            transcript = " ".join([t["text"] for t in transcript_list])

        if len(transcript) > MAX_TRANSCRIPT_CHARS:
            transcript = transcript[:MAX_TRANSCRIPT_CHARS]
            print(f"[DEBUG] Transcript truncated to {MAX_TRANSCRIPT_CHARS} chars")

        return {"video_id": video_id, "transcript": transcript, "metadata": metadata}
    except Exception as e:
        print(f"[DEBUG] Transcript error: {e}")
        return {"transcript": "ERROR: Could not fetch transcript", "metadata": metadata}

def bouncer_node(state: VideoState):
    if state["transcript"].startswith("ERROR"):
        return {"bouncer_verdict": "FAIL"}

    author = state.get("metadata", {}).get("author", "")
    is_elite = any(elite in author for elite in ELITE_CHANNELS)
    if is_elite:
        print(f"[DEBUG] Elite creator detected: {author} — auto-PASS")
        return {"bouncer_verdict": "PASS"}

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
    resp = groq_call(
        model="openai/gpt-oss-20b",
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
    resp = groq_call(
        model="openai/gpt-oss-20b",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1000,
    )
    return {"analysis": resp.choices[0].message.content.strip()}

def editor_node(state: VideoState):
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
    resp = groq_call(
        model="openai/gpt-oss-20b",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"}
    )
    output = json.loads(resp.choices[0].message.content)
    output["source_type"] = "youtube_agent"
    output["url"] = state["url"]
    output["pub_date"] = datetime.now().strftime("%Y-%m-%d")

    # Always use real metadata, never LLM-generated
    output["title"] = state["metadata"].get("title", output.get("title", "Untitled"))
    output["authors"] = [state["metadata"].get("author", "Unknown")]
    output["institution"] = state["metadata"].get("channel", "YouTube")

    # Force it_breakdown to always be a string
    if isinstance(output.get("it_breakdown"), dict):
        output["it_breakdown"] = " ".join(str(v) for v in output["it_breakdown"].values())
    elif not isinstance(output.get("it_breakdown"), str):
        output["it_breakdown"] = str(output.get("it_breakdown", ""))

    # Same for monetisation
    if isinstance(output.get("monetisation"), dict):
        output["monetisation"] = " ".join(str(v) for v in output["monetisation"].values())
    elif not isinstance(output.get("monetisation"), str):
        output["monetisation"] = str(output.get("monetisation", ""))

    if is_elite:
        output["is_evergreen"] = True

    if is_karpathy:
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

def load_queue():
    if os.path.exists(QUEUE_FILE):
        with open(QUEUE_FILE) as f:
            return json.load(f)
    return []

def save_queue(queue):
    with open(QUEUE_FILE, "w") as f:
        json.dump(queue, f, indent=2)

def run_queue():
    queue = load_queue()
    if not queue:
        print("[VIDEO] Queue is empty — nothing to process")
        return

    pending = [v for v in queue if v.get("status") == "pending"]
    print(f"[VIDEO] {len(pending)} pending videos in queue")

    graph = build_graph()
    processed = 0
    MAX_PER_RUN = 5

    for video in queue:
        if video.get("status") != "pending":
            continue
        if processed >= MAX_PER_RUN:
            print(f"[VIDEO] Cap reached ({MAX_PER_RUN} videos) — stopping")
            break

        url = video["url"]
        print(f"\n[VIDEO] Processing: {url}")

        try:
            initial_state = {
                "url": url,
                "transcript": "",
                "analysis": "",
                "output": {},
                "bouncer_verdict": "",
                "metadata": {}
            }
            result = graph.invoke(initial_state)

            if "FAIL" in result.get("bouncer_verdict", ""):
                print(f"[VIDEO] Rejected by Bouncer: {url}")
                video["status"] = "rejected"
            elif result.get("output"):
                out_data = result["output"]
                video_id = result.get("video_id", get_video_id(url))
                today = datetime.now().strftime("%Y%m%d")
                filename = f"v_{today}_{video_id}.json"
                final_output = {"case_studies": [out_data]}
                os.makedirs("case_studies", exist_ok=True)
                filepath = os.path.join("case_studies", filename)
                with open(filepath, "w") as f:
                    json.dump(final_output, f, indent=2)
                print(f"[VIDEO] Saved: {filepath}")
                video["status"] = "processed"
                video["processed_date"] = datetime.now().strftime("%Y-%m-%d")
            else:
                video["status"] = "error"
                video["error"] = "No output produced"

        except RuntimeError as e:
            print(f"[VIDEO] Rate limited — stopping run, will retry next time")
            save_queue(queue)
            return
        except Exception as e:
            print(f"[VIDEO] Error processing {url}: {e}")
            video["status"] = "error"
            video["error"] = str(e)

        save_queue(queue)
        if processed < MAX_PER_RUN - 1:
            print("[VIDEO] Waiting 45s before next video to avoid rate limits...")
            time.sleep(45)
        processed += 1

    print(f"\n[VIDEO] Run complete. Processed: {processed} videos")

if __name__ == "__main__":
    run_queue()
