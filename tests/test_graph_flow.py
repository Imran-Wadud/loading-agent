# tests/test_graph_flow.py
import sys
from pathlib import Path
from datetime import date

root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from app.graph import workload_graph
from app.agents.extractor import get_db_connection

def clear_athlete_history(athlete_id: str):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM athlete_sessions WHERE athlete_id = %s;", (athlete_id,))
            conn.commit()
    finally:
        conn.close()

def run_test():
    athlete_id = "athlete_001"
    clear_athlete_history(athlete_id)

    # 1. Log Day 1 Session
    day1_date = date(2026, 10, 19)
    print("--- Running Day 1 Session (Lower Body) ---")
    state1 = {
        "athlete_id": athlete_id,
        "session_date": day1_date,
        "raw_input": "Heavy Barbell Back Squats 4x8, Romanian Deadlifts 3x10, and Calf Raises",
        "session_rpe": 8.5,
        "remediation_attempts": 0
    }
    res1 = workload_graph.invoke(state1)
    print("Day 1 Result:", res1["final_status"])

    # 2. Log Day 2 Session on next day (triggers audit violation & remediation evaluator loop)
    day2_date = date(2026, 10, 20)
    print("\n--- Running Day 2 Session (Triggers Remediation Loop) ---")
    state2 = {
        "athlete_id": athlete_id,
        "session_date": day2_date,
        "raw_input": "Heavy Squat Jumps and Leg Extensions",
        "session_rpe": 8.5,
        "remediation_attempts": 0
    }
    res2 = workload_graph.invoke(state2)
    print("\n--- Final Graph Results ---")
    print(f"Final Status: {res2['final_status']}")
    print(f"Remediation Attempts: {res2.get('remediation_attempts')}")
    print(f"Evaluator Approved: {res2.get('evaluator_approved')}")
    print(f"\nAI Remediation Fix Suggestion:\n{res2.get('remediation_suggestion')}")

if __name__ == "__main__":
    run_test()