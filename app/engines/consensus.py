# app/engines/consensus.py
import json
import re
import logging
from datetime import date
from typing import Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import statistics

from app.adapters.meterless import get_meterless_llm
from app.adapters.zetaris import fetch_canonical_body_parts

logger = logging.getLogger("loading_agent.consensus")


def extract_single_pass(
    pass_num: int,
    raw_text: str,
    session_rpe: Optional[float],
    body_parts: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Executes a single extraction pass using the configured LLM adapter."""
    logger.info(f"[Pass {pass_num}] Initiating extraction...")
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
        "  ]\n"
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
            logger.error(
                "[Pass %s] Failed to parse JSON: %s",
                pass_num, raw_content[:200],
            )
            return {
                "ok": False,
                "session_title": "Extraction Failed",
                "loads": [],
            }

        parsed = json.loads(json_match.group(0))

        if not isinstance(parsed, dict) or not isinstance(parsed.get("loads"), list):
            logger.error("[Pass %s] Invalid extraction structure", pass_num)
            return {
                "ok": False,
                "session_title": "Extraction Failed",
                "loads": [],
            }

        logger.info(
            "[Pass %s] Successfully parsed response containing %s tissue items.",
            pass_num, len(parsed["loads"]),
        )

        return {
            "ok": True,
            "session_title": parsed.get("session_title", "Logged Session"),
            "loads": parsed["loads"],
        }
    
    except Exception as e:
        logger.error(
            "[Pass %s] LLM Invoke Exception: %s",
            pass_num,
            e,
            exc_info=True,
        )
        return {
            "ok": False,
            "session_title": "Extraction Failed",
            "loads": [],
        }


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

    successful_passes = [r for r in results if r.get("ok")]

    logger.info(
        "Completed %s / %s successful LLM passes.",
        len(successful_passes), num_passes,
    )

    if not successful_passes:
        raise ValueError("All consensus extraction passes failed!")

    session_titles = [
        r.get("session_title", "Logged Session")
        for r in successful_passes
    ]
    consensus_session_title = max(
        set(session_titles), key=session_titles.count
    )

    # A rest session is valid only when the model explicitly identifies
    # the activity as rest/recovery. Don't interpret arbitrary empty
    # outputs as a rest day.
    is_rest_session = bool(re.search(
        r"\b(rest|rest day|recovery day|complete rest)\b",
        raw_text,
        re.IGNORECASE,
    ))

    if is_rest_session:
        if any(r["loads"] for r in successful_passes):
            logger.warning(
                "Rest-like input produced tissue loads; retaining normal "
                "consensus validation."
            )
        else:
            return {
                "athlete_id": athlete_id,
                "session_date": session_date,
                "session_title": consensus_session_title,
                "consensus_loads": [],
                "raw_text": raw_text,
                "session_rpe": session_rpe,
            }

    # Continue with your existing body_part_votes aggregation below.
    successful_passes = [r for r in successful_passes if r["loads"]]

    if not successful_passes:
        raise ValueError(
            "No tissue loads extracted from a non-rest workout."
        )

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