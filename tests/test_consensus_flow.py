# tests/test_consensus_flow.py
import sys
from pathlib import Path
from datetime import date

root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from app.agents.consensus import extract_with_consensus
from app.agents.audit import audit_session_safety

def run_test():
    athlete_id = "athlete_001"
    target_date = date(2026, 10, 23)
    raw_text = "Plyometric box jumps, heavy isometric squat holds, and sprint intervals on turf"

    print("--- 1. Running Meterless Consensus Extraction (3 Passes) ---")
    consensus_res = extract_with_consensus(
        athlete_id=athlete_id,
        session_date=target_date,
        raw_text=raw_text,
        session_rpe=8.5,
        num_passes=3
    )

    print("\nConsensus Result Output:")
    print(f"Session Title: {consensus_res['session_title']}")
    print(f"Passes Completed: {consensus_res['consensus_passes']}")
    print(f"Approved Loads Count: {consensus_res['loads_count']}")
    
    print("\nValidated Tissue Loads:")
    for load in consensus_res["consensus_loads"]:
        print(f" - Body Part ID {load['body_part_id']}: Score {load['stress_score']} (Votes: {load['consensus_votes']}/3)")

    print("\n--- 2. Auditing Consensus Session ---")
    audit = audit_session_safety(athlete_id, target_date, consensus_res["session_id"])
    print(f"\nAudit Verdict: {'APPROVED' if audit['approved'] else 'REJECTED'}")
    print(f"Safety Flags Count: {audit['flags_count']}")

if __name__ == "__main__":
    run_test()