# app/agents/consensus.py
import json
import re
import os
import logging
from datetime import date
from typing import Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import statistics

from app.llm import get_llm
from app.agents.extractor import fetch_canonical_body_parts, get_db_connection

logger = logging.getLogger("loading_agent.consensus")

def _single_llm_pass(pass_id: int, user_prompt: str, system_prompt: str) -> Optional[Dict[str, Any]]:
    """Executes a single extraction pass using Nemotron."""
    try:
        logger.info(f"[Pass {pass_id}] Initiating Nemotron extraction...")
        llm = get_llm()
        response = llm.invoke([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ])
        raw_content = response.content.strip()
        json_match = re.search(r"\{.*\}", raw_content, re.DOTALL)
        if not json_match:
            logger.warning(f"[Pass {pass_id}] Failed to locate valid JSON block.")
            return None
        
        parsed = json.loads(json_match.group(0))
        logger.info(f"[Pass {pass_id}] Successfully extracted {len(parsed.get('loads', []))} raw tissue items.")
        return parsed
    except Exception as e:
        logger.error(f"[Pass {pass_id}] Error during execution: {e}")
        return None


def extract_with_consensus(
    athlete_id: str,
    session_date: date,
    raw_text: str,
    session_rpe: Optional[float] = None,
    num_passes: int = 3
) -> Dict[str, Any]:
    """
    Meterless Consensus Engine:
    Executes N parallel LLM extraction passes, applies majority presence voting,
    filters score outliers, and persists the agreed tissue loads to PostgreSQL.
    """
    logger.info(f"--- Meterless Consensus Engine Started ({num_passes} Passes) ---")
    body_parts = fetch_canonical_body_parts()
    valid_ids = {bp["body_part_id"] for bp in body_parts}
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

    # 1. Concurrent Execution
    raw_results = []
    with ThreadPoolExecutor(max_workers=num_passes) as executor:
        futures = [
            executor.submit(_single_llm_pass, i + 1, user_prompt, system_prompt)
            for i in range(num_passes)
        ]
        for future in as_completed(futures):
            res = future.result()
            if res:
                raw_results.append(res)

    if not raw_results:
        raise RuntimeError("All parallel LLM extraction passes failed.")

    logger.info(f"Completed {len(raw_results)} / {num_passes} successful LLM passes.")

    # 2. Extract Session Title (Majority or First non-empty)
    titles = [r.get("session_title") for r in raw_results if r.get("session_title")]
    session_title = titles[0] if titles else "Logged Training Session"

    # 3. Aggregate Tissue Loads across Passes
    # Group scores and volumes by body_part_id
    id_scores: Dict[int, List[float]] = {}
    id_volumes: Dict[int, List[int]] = {}

    for pass_data in raw_results:
        for load in pass_data.get("loads", []):
            bp_id = load.get("body_part_id")
            score = load.get("stress_score")
            vol = load.get("volume_mins", 0)

            # Filter out invalid canonical IDs (Hallucination Prevention)
            if bp_id not in valid_ids:
                logger.warning(f"Discarding invalid body_part_id: {bp_id}")
                continue

            if isinstance(score, (int, float)):
                id_scores.setdefault(bp_id, []).append(float(score))
                id_volumes.setdefault(bp_id, []).append(int(vol))

    # 4. Apply Consensus Voting & Score Averaging
    min_votes = (len(raw_results) // 2) + 1  # Majority threshold (e.g. 2 out of 3)
    consensus_loads = []

    for bp_id, scores in id_scores.items():
        votes = len(scores)
        if votes < min_votes:
            logger.info(f"Body Part ID {bp_id} rejected by consensus (Votes: {votes}/{len(raw_results)}, required: {min_votes}).")
            continue

        # Outlier filtering & score averaging
        avg_score = round(statistics.mean(scores), 1)
        max_vol = max(id_volumes[bp_id]) if id_volumes.get(bp_id) else 0

        consensus_loads.append({
            "body_part_id": bp_id,
            "stress_score": avg_score,
            "volume_mins": max_vol,
            "consensus_votes": votes
        })

    logger.info(f"Consensus Aggregation Finished: Approved {len(consensus_loads)} tissue loads.")

    # 5. Persist Consensus Result into PostgreSQL
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO athlete_sessions (athlete_id, session_date, session_title, raw_input, session_rpe)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING session_id;
                """,
                (athlete_id, session_date, session_title, raw_text, session_rpe)
            )
            session_id = cur.fetchone()["session_id"]

            for item in consensus_loads:
                cur.execute(
                    """
                    INSERT INTO tissue_loads_logged (session_id, body_part_id, stress_score, volume_mins)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (session_id, body_part_id) DO UPDATE 
                    SET stress_score = EXCLUDED.stress_score;
                    """,
                    (session_id, item["body_part_id"], item["stress_score"], item["volume_mins"])
                )

            conn.commit()
            logger.info(f"Persisted consensus session '{session_id}' to PostgreSQL.")

            return {
                "session_id": str(session_id),
                "athlete_id": athlete_id,
                "session_date": str(session_date),
                "session_title": session_title,
                "session_rpe": session_rpe,
                "consensus_passes": len(raw_results),
                "loads_count": len(consensus_loads),
                "consensus_loads": consensus_loads
            }
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to persist consensus session to DB: {e}")
        raise e
    finally:
        conn.close()