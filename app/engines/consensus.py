# app/engines/consensus.py
import json
import re
import os
import logging
from datetime import date
from typing import Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import statistics

# Make sure this points to adapters!
from app.adapters.meterless import get_meterless_llm
from app.adapters.zetaris import fetch_canonical_body_parts, get_db_connection

logger = logging.getLogger("loading_agent.consensus")


def extract_single_pass(
    pass_num: int,
    raw_text: str,
    session_rpe: Optional[float],
    body_parts: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Executes a single extraction pass using the Meterless Nemotron LLM."""
    logger.info(f"[Pass {pass_num}] Initiating Nemotron extraction...")
    body_parts_text = "\n".join([f"- ID {bp['body_part_id']}: {bp['name']} ({bp['tissue_type']})" for bp in body_parts])

    system_prompt = (
        "You are an expert Strength & Conditioning S&C Biomechanics Agent.\n"
        "Analyze the athlete's input text describing their workout or activity.\n"
        "Map the activity ONLY to the canonical body parts listed below:\n"
        f"{body_parts_text}\n\n"
        "For EACH engaged body part, assign a localized stress_score from 1.0 to 10.0 representing mechanical/tissue strain.\n"
        "Return ONLY a raw JSON object with this exact structure:\n"
        "{\n"
        '  "session_title": "Descriptive summary of the session",\n'
        '  "loads": [\n'
        '    {"body_part_id": 1, "stress_score": 8.5, "volume_mins": 60},\n'
        '    {"body_part_id": 11, "stress_score": 7.0, "volume_mins": 45}\n'
        '  ]\n'
        "}\n"
    )

    rpe_context = f"\nOverall Session RPE reported by athlete: {session_rpe}" if session_rpe else "\nNo overall RPE reported."
    user_prompt = f"Athlete Input Text:\n{raw_text}{rpe_context}"

    try:
        llm = get_meterless_llm()
        
        response = llm.invoke([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ])

        raw_content = response.content.strip()
        json_match = re.search(r"\{.*\}", raw_content, re.DOTALL)
        if not json_match:
            logger.error(f"[Pass {pass_num}] Failed to parse valid JSON from LLM output: {raw_content[:100]}")
            return {"session_title": "Extraction Failed", "loads": []}

        parsed = json.loads(json_match.group(0))
        loads = parsed.get("loads", [])
        logger.info(f"[Pass {pass_num}] Successfully extracted {len(loads)} raw tissue items.")
        return parsed
    except Exception as e:
        logger.error(f"[Pass {pass_num}] LLM Invoke Exception: {e}", exc_info=True)
        return {"session_title": "Exception", "loads": []}

def run_meterless_consensus(
    athlete_id: str,
    session_date: date,
    raw_text: str,
    session_rpe: Optional[float] = None,
    num_passes: int = 3,
    min_majority_votes: int = 2
) -> Dict[str, Any]:
    """Meterless Consensus Engine: Executes N parallel extraction passes and applies majority voting."""
    logger.info(f"--- Meterless Consensus Engine Started ({num_passes} Passes) ---")
    body_parts = fetch_canonical_body_parts()

    results = []
    with ThreadPoolExecutor(max_workers=num_passes) as executor:
        futures = [
            executor.submit(extract_single_pass, i + 1, raw_text, session_rpe, body_parts)
            for i in range(num_passes)
        ]
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as e:
                logger.error(f"Extraction pass encountered an error: {e}")

    successful_passes = [r for r in results if r.get("loads")]
    logger.info(f"Completed {len(successful_passes)} / {num_passes} successful LLM passes.")

    if not successful_passes:
        raise ValueError("All consensus extraction passes failed!")

    session_titles = [r.get("session_title", "Logged Session") for r in successful_passes]
    consensus_session_title = max(set(session_titles), key=session_titles.count)

    body_part_votes: Dict[int, List[float]] = {}
    for pass_result in successful_passes:
        for load_item in pass_result.get("loads", []):
            bp_id = load_item["body_part_id"]
            score = float(load_item["stress_score"])
            if bp_id not in body_part_votes:
                body_part_votes[bp_id] = []
            body_part_votes[bp_id].append(score)

    approved_consensus_loads = []
    for bp_id, scores in body_part_votes.items():
        vote_count = len(scores)
        if vote_count >= min_majority_votes:
            avg_score = round(statistics.mean(scores), 1)
            approved_consensus_loads.append({
                "body_part_id": bp_id,
                "stress_score": avg_score,
                "consensus_votes": vote_count,
                "volume_mins": 0
            })
        else:
            logger.info(f"Body Part ID {bp_id} rejected by consensus (Votes: {vote_count}/{num_passes}, required: {min_majority_votes}).")

    logger.info(f"Consensus Aggregation Finished: Approved {len(approved_consensus_loads)} tissue loads.")

    return {
        "athlete_id": athlete_id,
        "session_date": session_date,
        "session_title": consensus_session_title,
        "consensus_loads": approved_consensus_loads,
        "raw_text": raw_text,
        "session_rpe": session_rpe
    }
