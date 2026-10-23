# tests/test_valid_flow.py
import sys
from pathlib import Path
from datetime import date

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
    
    # 72+ hours between lower body and upper body sessions
    monday_date = date(2026, 10, 19)   # Monday: Lower Body
    thursday_date = date(2026, 10, 22) # Thursday: Upper Body (72h recovery)

    print("--- 1. Logging Monday Lower Body Session ---")
    monday_text = "Heavy Barbell Back Squats 4x8, Romanian Deadlifts 3x10, and Calf Raises"
    res1 = extract_and_log_session(athlete_id, monday_date, monday_text, session_rpe=8.5)
    print("Monday Logged:", res1)

    print("\n--- 2. Logging Thursday Upper Body Session (72h Later) ---")
    thursday_text = "Heavy Bench Press 4x6, Weighted Pull-Ups 4x8, Overhead Dumbbell Press, and Cable Rows"
    res2 = extract_and_log_session(athlete_id, thursday_date, thursday_text, session_rpe=8.0)
    print("Thursday Logged:", res2)

    print("\n--- 3. Auditing Thursday Session Safety ---")
    audit = audit_session_safety(athlete_id, thursday_date, res2["session_id"])
    print(f"\nAudit Verdict: {'APPROVED' if audit['approved'] else 'REJECTED'}")
    print(f"Safety Flags Count: {audit['flags_count']}")
    
    if audit['approved']:
        print("✅ Session Approved! All tissue recovery windows (72h Tendon / 48h Muscle) fully respected.")
    else:
        for flag in audit["safety_flags"]:
            print(f" ⚠️  [{flag['tissue_type']}] {flag['message']}")

if __name__ == "__main__":
    run_test()