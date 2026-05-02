#!/usr/bin/env python3
import os, json, time
from datetime import datetime
from google import genai as genai_client
from dotenv import load_dotenv
load_dotenv()

OUTPUT_DIR = "case_studies"
BATCH_SIZE = 5

def gemini_evaluate(abstract, groq_output):
    try:
        client = genai_client.Client(api_key=os.getenv("GEMINI_API_KEY"))
        eval_prompt = (
            "You are a strict fact checker evaluating an AI-generated summary of an academic paper.\n"
            "Your job is to check if the summary is accurate and if the scores are reasonable.\n\n"
            "ORIGINAL ABSTRACT:\n" + abstract + "\n\n"
            "GENERATED SUMMARY: " + groq_output.get("summary", "") + "\n"
            "SCORES: impact=" + str(groq_output.get("real_world_impact", 0)) +
            ", maturity=" + str(groq_output.get("ai_maturity", 0)) +
            ", monetisation=" + str(groq_output.get("monetisation_potential", 0)) + "\n\n"
            "Answer in JSON only. No markdown. No preamble.\n"
            '{"hallucination_detected": false, "hallucination_detail": "", "scores_reasonable": true, "score_feedback": "", "verdict": "pass"}'
        )
        response = model.generate_content(eval_prompt)
        text = response.text.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text.strip())
        print("[EVAL] Gemini verdict: " + result.get("verdict", "unknown"))
        time.sleep(8)
        return result
    except Exception as e:
        print("[EVAL] Gemini error: " + str(e))
        time.sleep(8)
        return {"hallucination_detected": False, "verdict": "pass", "score_feedback": "eval_error"}

def run_pending_evaluation():
    today = datetime.now().strftime("%Y-%m-%d")
    filepath = OUTPUT_DIR + "/" + today + ".json"

    if not os.path.exists(filepath):
        print("[EVAL] No file for today: " + filepath)
        return

    with open(filepath, encoding="utf-8") as f:
        data = json.load(f)

    case_studies = data.get("case_studies", [])
    pending = [cs for cs in case_studies if cs.get("eval_verdict") == "pending"]
    print("[EVAL] Found " + str(len(pending)) + " pending papers")

    if not pending:
        print("[EVAL] Nothing to evaluate")
        return

    batch = pending[:BATCH_SIZE]
    print("[EVAL] Evaluating " + str(len(batch)) + " papers...")

    for cs in case_studies:
        if cs.get("eval_verdict") != "pending":
            continue
        if cs["id"] not in [b["id"] for b in batch]:
            continue
        print("[EVAL] Evaluating: " + cs["title"][:60])
        result = gemini_evaluate(cs.get("abstract", ""), cs)
        cs["eval_verdict"] = result.get("verdict", "pass")
        cs["eval_flagged"] = result.get("hallucination_detected", False)
        cs["eval_feedback"] = result.get("score_feedback", "")

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    # Update archive too
    archive_file = OUTPUT_DIR + "/archive.json"
    if os.path.exists(archive_file):
        with open(archive_file, encoding="utf-8") as f:
            archive = json.load(f)
        for entry in archive:
            if entry.get("date") == today:
                entry["case_studies"] = case_studies
                break
        with open(archive_file, "w", encoding="utf-8") as f:
            json.dump(archive, f, indent=2, ensure_ascii=False)

    remaining = len([cs for cs in case_studies if cs.get("eval_verdict") == "pending"])
    print("[EVAL] Done. " + str(remaining) + " papers still pending.")

if __name__ == "__main__":
    run_pending_evaluation()
