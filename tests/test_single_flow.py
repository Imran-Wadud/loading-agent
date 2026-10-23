# tests/test_single_flow.py
import sys
from pathlib import Path
from datetime import date, timedelta

root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from app.agents.extractor import extract_and_log_session, get_db_connection
from app.agents.audit import audit_session_safety

def clear_athlete_history(athlete_id: str):
    """Wipes historical sessions for clean isolated test runs."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM athlete_sessions WHERE athlete_id = %s;", (athlete_id,))
            conn.commit()
    finally:
        conn.close()

def run_test():
    athlete_id = "athlete_001"
    clear_athlete_history(athlete_id)  # Clean state before test execution
    
    day1_date = date(2026, 10, 19) # Monday
    day2_date = date(2026, 10, 20) # Tuesday

    print("--- 1. Logging Monday Session (Day 1) ---")
    monday_text = "Badminton 2hrs (heavy jumping / court movement) and heavy overcoming isometric leg press"
    res1 = extract_and_log_session(athlete_id, day1_date, monday_text, session_rpe=8.5)
    print("Monday Logged:", res1)

    print("\n--- 2. Logging Tuesday Session (Day 2) ---")
    tuesday_text = "Heavy squat jumps and leg extensions session"
    res2 = extract_and_log_session(athlete_id, day2_date, tuesday_text, session_rpe=8.0)
    print("Tuesday Logged:", res2)

    print("\n--- 3. Auditing Tuesday Session Safety ---")
    audit = audit_session_safety(athlete_id, day2_date, res2["session_id"])
    print(f"\nAudit Verdict: {'APPROVED' if audit['approved'] else 'REJECTED'}")
    print(f"Safety Flags ({audit['flags_count']}):")
    for flag in audit["safety_flags"]:
        print(f" ⚠️  [{flag['tissue_type']}] {flag['message']}")

if __name__ == "__main__":
    run_test()