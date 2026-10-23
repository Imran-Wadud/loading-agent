# app/agents/extractor.py
import json
import re
import os
import logging
from datetime import date
from pathlib import Path
from typing import Dict, Any, List, Optional
import psycopg2
from psycopg2.extras import RealDictCursor

from app.llm import get_llm

# Set up logger for extraction tracking
logger = logging.getLogger("loading_agent.extractor")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

# Ensure .env variables are available to process os.environ
_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
if _ENV_PATH.exists():
    for _line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _key, _, _value = _line.partition("=")
        os.environ.setdefault(_key.strip(), _value.strip().strip('"').strip("'"))

# Database connection helper using Zetaris credentials
DB_CONFIG = {
    "dbname": os.getenv("POSTGRES_DB", "postgres"),
    "user": os.getenv("POSTGRES_USER", "zetaris"),
    "password": os.getenv("POSTGRES_PASSWORD", "zetaris"),
    "host": os.getenv("POSTGRES_HOST", "localhost"),
    "port": int(os.getenv("POSTGRES_PORT", "5432"))
}

def get_db_connection():
    return psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)


def fetch_canonical_body_parts() -> List[Dict[str, Any]]:
    """Fetches valid anatomical dictionary from PostgreSQL."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT body_part_id, name, tissue_type FROM body_parts ORDER BY body_part_id;")
            parts = cur.fetchall()
            logger.info(f"Fetched {len(parts)} canonical body parts from database.")
            return parts
    finally:
        conn.close()


def extract_and_log_session(
    athlete_id: str,
    session_date: date,
    raw_text: str,
    session_rpe: Optional[float] = None
) -> Dict[str, Any]:
    """
    Uses Nemotron to analyze raw timetable text, match canonical body parts,
    and persist session + tissue loads into Zetaris PostgreSQL.
    """
    logger.info(f"Starting extraction for Athlete '{athlete_id}' on {session_date} (RPE: {session_rpe})")
    body_parts = fetch_canonical_body_parts()
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

    llm = get_llm()
    logger.info("Invoking Nemotron LLM for anatomical extraction...")
    
    response = llm.invoke([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ])

    raw_content = response.content.strip()
    logger.info("Received LLM response successfully.")

    json_match = re.search(r"\{.*\}", raw_content, re.DOTALL)
    if not json_match:
        logger.error("Failed to parse valid JSON from LLM response!")
        raise ValueError("Failed to extract valid JSON from Nemotron output.")

    parsed = json.loads(json_match.group(0))
    session_title = parsed.get("session_title", "Logged Training Session")
    loads = parsed.get("loads", [])
    logger.info(f"Extracted session title: '{session_title}' with {len(loads)} tissue loads.")

    # Persist into PostgreSQL / Zetaris
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # 1. Insert into athlete_sessions
            cur.execute(
                """
                INSERT INTO athlete_sessions (athlete_id, session_date, session_title, raw_input, session_rpe)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING session_id;
                """,
                (athlete_id, session_date, session_title, raw_text, session_rpe)
            )
            session_id = cur.fetchone()["session_id"]
            logger.info(f"Created athlete_sessions record UUID: {session_id}")

            # 2. Insert into tissue_loads_logged
            inserted_loads = []
            for item in loads:
                cur.execute(
                    """
                    INSERT INTO tissue_loads_logged (session_id, body_part_id, stress_score, volume_mins)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (session_id, body_part_id) DO UPDATE 
                    SET stress_score = EXCLUDED.stress_score;
                    """,
                    (session_id, item["body_part_id"], item["stress_score"], item.get("volume_mins", 0))
                )
                inserted_loads.append(item)

            conn.commit()
            logger.info(f"Successfully logged {len(inserted_loads)} tissue loads to DB.")

            return {
                "session_id": str(session_id),
                "athlete_id": athlete_id,
                "session_date": str(session_date),
                "session_title": session_title,
                "session_rpe": session_rpe,
                "loads_count": len(inserted_loads)
            }
    except Exception as e:
        conn.rollback()
        logger.error(f"Database error during session persistence: {e}")
        raise e
    finally:
        conn.close()