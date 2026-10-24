# app/adapters/zetaris.py
import os
import logging
from datetime import date, timedelta
from typing import Dict, Any, List, Optional
import psycopg2
from psycopg2.extras import RealDictCursor
from pathlib import Path

logger = logging.getLogger("loading_agent.zetaris")

# Load .env file from project root (2 levels up from app/adapters/)
_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
if _ENV_PATH.exists():
    for _line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _key, _, _value = _line.partition("=")
        os.environ.setdefault(_key.strip(), _value.strip().strip('"').strip("'"))


def get_db_connection():
    """Returns a PostgreSQL/Zetaris connection with dictionary cursor support."""
    host = os.getenv("POSTGRES_HOST") or os.getenv("DB_HOST", "localhost")
    port = int(os.getenv("POSTGRES_PORT") or os.getenv("DB_PORT", "5432"))
    dbname = os.getenv("POSTGRES_DB") or os.getenv("DB_NAME", "postgres")
    user = os.getenv("POSTGRES_USER") or os.getenv("DB_USER", "postgres")
    password = os.getenv("POSTGRES_PASSWORD") or os.getenv("DB_PASSWORD", "postgres")

    return psycopg2.connect(
        host=host,
        port=port,
        dbname=dbname,
        user=user,
        password=password,
        cursor_factory=RealDictCursor
    )


def fetch_canonical_body_parts() -> List[Dict[str, Any]]:
    """Fetches valid canonical body parts dictionary from PostgreSQL."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT body_part_id, name, tissue_type FROM body_parts ORDER BY body_part_id;")
            parts = cur.fetchall()
            logger.info(f"Fetched {len(parts)} canonical body parts from database.")
            return parts
    finally:
        conn.close()


def persist_session_and_loads(
    athlete_id: str,
    session_date: date,
    session_title: str,
    raw_text: str,
    session_rpe: Optional[float],
    consensus_loads: List[Dict[str, Any]]
) -> str:
    """Persists approved session and corresponding tissue loads into Zetaris PostgreSQL."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # 1. Insert session
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

            # 2. Insert tissue loads
            for item in consensus_loads:
                cur.execute(
                    """
                    INSERT INTO tissue_loads_logged (session_id, body_part_id, stress_score, volume_mins)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (session_id, body_part_id) DO UPDATE 
                    SET stress_score = EXCLUDED.stress_score;
                    """,
                    (session_id, item["body_part_id"], item["stress_score"], item.get("volume_mins", 0))
                )

            conn.commit()
            logger.info(f"Successfully persisted session {session_id} with {len(consensus_loads)} tissue loads.")
            return str(session_id)
    except Exception as e:
        conn.rollback()
        logger.error(f"Database error during session persistence: {e}")
        raise e
    finally:
        conn.close()


class ZetarisDBAdapter:
    """Adapter class providing structured data access for engines and nodes."""

    def get_canonical_body_parts(self) -> List[Dict[str, Any]]:
        return fetch_canonical_body_parts()

    def get_active_recovery_window(self, athlete_id: str, session_date: date) -> List[Dict[str, Any]]:
        """Queries historical sessions logged within the 3-day (72h) lookback window."""
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                start_date = session_date - timedelta(days=3)
                cur.execute(
                    """
                    SELECT s.session_id, s.session_date, s.session_title, 
                           tl.body_part_id, bp.name, bp.tissue_type, tl.stress_score
                    FROM tissue_loads_logged tl
                    JOIN athlete_sessions s ON tl.session_id = s.session_id
                    JOIN body_parts bp ON tl.body_part_id = bp.body_part_id
                    WHERE s.athlete_id = %s
                      AND s.session_date BETWEEN %s AND %s;
                    """,
                    (athlete_id, start_date, session_date)
                )
                return cur.fetchall()
        finally:
            conn.close()

    def save_approved_session(
        self,
        athlete_id: str,
        session_date: date,
        session_title: str,
        raw_text: str,
        session_rpe: Optional[float],
        consensus_loads: List[Dict[str, Any]]
    ) -> str:
        return persist_session_and_loads(
            athlete_id, session_date, session_title, raw_text, session_rpe, consensus_loads
        )