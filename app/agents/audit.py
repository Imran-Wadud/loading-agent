# app/agents/audit.py
import logging
from datetime import date, timedelta
from typing import Dict, Any, List, Optional
import psycopg2
from psycopg2.extras import RealDictCursor

# Import connection helper and logger context from extractor
from app.agents.extractor import get_db_connection

logger = logging.getLogger("loading_agent.audit")

def calculate_recovery_windows(rpe: Optional[float], tissue_type: str) -> int:
    """Calculates hours required for safe recovery based on RPE and tissue type."""
    if rpe is None or rpe >= 8.0:
        return 72 if tissue_type == 'TENDON' else 48
    elif rpe >= 7.0:
        return 48 if tissue_type == 'TENDON' else 36
    else: # RPE <= 6.0
        return 36 if tissue_type == 'TENDON' else 24


def audit_session_safety(athlete_id: str, target_date: date, proposed_session_id: str) -> Dict[str, Any]:
    """
    Audits tissue loads logged for proposed_session_id against historic sessions
    within calculated recovery windows (72h Tendon / 48h Muscle).
    """
    logger.info(f"Auditing safety for session {proposed_session_id} on date {target_date}...")
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Fetch proposed session details
            cur.execute(
                "SELECT session_rpe FROM athlete_sessions WHERE session_id = %s;",
                (proposed_session_id,)
            )
            proposed_session = cur.fetchone()
            proposed_rpe = proposed_session["session_rpe"] if proposed_session else None

            # Fetch tissue loads for the proposed session
            cur.execute(
                """
                SELECT tl.body_part_id, bp.name, bp.tissue_type, tl.stress_score
                FROM tissue_loads_logged tl
                JOIN body_parts bp ON tl.body_part_id = bp.body_part_id
                WHERE tl.session_id = %s;
                """,
                (proposed_session_id,)
            )
            proposed_loads = cur.fetchall()

            safety_flags = []

            for load in proposed_loads:
                bp_id = load["body_part_id"]
                bp_name = load["name"]
                t_type = load["tissue_type"]
                current_stress = float(load["stress_score"])

                # Skip low-stress auxiliary engagements (< 5.0)
                if current_stress < 5.0:
                    continue

                window_hours = calculate_recovery_windows(proposed_rpe, t_type)
                lookback_days = (window_hours + 23) // 24 # Convert hours to day window

                start_date = target_date - timedelta(days=lookback_days)

                # Query prior logs within recovery window
                cur.execute(
                    """
                    SELECT s.session_date, s.session_title, s.session_rpe, tl.stress_score
                    FROM tissue_loads_logged tl
                    JOIN athlete_sessions s ON tl.session_id = s.session_id
                    WHERE s.athlete_id = %s
                      AND tl.body_part_id = %s
                      AND s.session_id != %s
                      AND s.session_date BETWEEN %s AND %s;
                    """,
                    (athlete_id, bp_id, proposed_session_id, start_date, target_date)
                )
                prior_conflicts = cur.fetchall()

                for conflict in prior_conflicts:
                    prior_stress = float(conflict["stress_score"])
                    if prior_stress >= 6.0:
                        flag_msg = (
                            f"High {t_type} load on '{bp_name}' (Score {current_stress}) "
                            f"within {window_hours}h of prior session on {conflict['session_date']} ('{conflict['session_title']}')."
                        )
                        logger.warning(f"SAFETY VIOLATION DETECTED: {flag_msg}")
                        safety_flags.append({
                            "tissue_name": bp_name,
                            "tissue_type": t_type,
                            "current_date": str(target_date),
                            "conflict_date": str(conflict["session_date"]),
                            "prior_session": conflict["session_title"],
                            "required_window_hours": window_hours,
                            "message": flag_msg
                        })

            approved = len(safety_flags) == 0
            logger.info(f"Audit completed for session {proposed_session_id}. Approved: {approved} (Flags: {len(safety_flags)})")

            return {
                "approved": approved,
                "flags_count": len(safety_flags),
                "safety_flags": safety_flags
            }
    finally:
        conn.close()