import sys
import argparse
from datetime import date
from app.graph import workload_graph

def print_banner():
    print("=" * 65)
    print(" ATHLETIC WORKLOAD & RECOVERY ENGINE - INTERACTIVE CLI ")
    print(" Enter training sessions interactively. Type 'exit' or Ctrl+C to quit.")
    print("=" * 65)

def run_session_audit(athlete_id: str, session_date_str: str, rpe: float, text: str):
    try:
        session_date = date.fromisoformat(session_date_str)
    except ValueError:
        print(f"\nError: Invalid date format '{session_date_str}'. Use YYYY-MM-DD.\n")
        return

    initial_state = {
        "athlete_id": athlete_id,
        "session_date": session_date,
        "raw_input": text,
        "session_rpe": rpe,
        "remediation_attempts": 0,
    }

    print(f"\nRunning Audit Pipeline [{athlete_id} | {session_date_str} | RPE: {rpe}]...\n")

    final_output = {}
    for event in workload_graph.stream(initial_state):
        for node_name, output in event.items():
            print(f"  └─> [Completed Node: {node_name}]")
            if isinstance(output, dict):
                final_output.update(output)

    print("\n" + "─" * 65)
    print(" AUDIT RESULTS")
    print(f"  * Status:               {final_output.get('final_status', 'UNKNOWN')}")
    print(f"  * Session ID:           {final_output.get('session_id', 'N/A')}")
    
    if final_output.get("remediation_suggestion"):
        print("\n BIOMECHANICAL ADVISORY PLAN:")
        for line in final_output["remediation_suggestion"].splitlines():
            print(f"    {line}")
    print("─" * 65 + "\n")

def interactive_loop(default_athlete: str, default_date: str, default_rpe: float):
    print_banner()

    athlete_id = default_athlete
    current_date = default_date
    current_rpe = default_rpe

    try:
        while True:
            print(f"Current Session Context: Athlete={athlete_id} | Date={current_date} | RPE={current_rpe}")
            print("Commands: ':set athlete <id>' | ':set date <YYYY-MM-DD>' | ':set rpe <val>' | 'exit'")
            
            raw_text = input("\nEnter Workout Text > ").strip()

            if not raw_text:
                continue

            if raw_text.lower() in ("exit", "quit", ":q"):
                print("\nExiting CLI session engine. Goodbye!\n")
                break

            # Handle quick inline commands
            if raw_text.startswith(":set athlete "):
                athlete_id = raw_text.replace(":set athlete ", "").strip()
                print(f"Active athlete updated to: {athlete_id}\n")
                continue
            elif raw_text.startswith(":set date "):
                current_date = raw_text.replace(":set date ", "").strip()
                print(f"Active date updated to: {current_date}\n")
                continue
            elif raw_text.startswith(":set rpe "):
                try:
                    current_rpe = float(raw_text.replace(":set rpe ", "").strip())
                    print(f"Active RPE updated to: {current_rpe}\n")
                except ValueError:
                    print("Invalid RPE numeric value.")
                continue

            # Run graph execution for current workout input
            run_session_audit(athlete_id, current_date, current_rpe, raw_text)

    except KeyboardInterrupt:
        print("\n\nSession terminated via Ctrl+C. Goodbye!\n")
        sys.exit(0)

def main():
    parser = argparse.ArgumentParser(description="Athletic Workload Agent Interactive CLI")
    parser.add_argument("--athlete", type=str, default="athlete_001", help="Athlete UUID")
    parser.add_argument("--date", type=str, default=str(date.today()), help="Session date (YYYY-MM-DD)")
    parser.add_argument("--rpe", type=float, default=8.0, help="Session RPE (1.0 - 10.0)")

    args = parser.parse_args()
    interactive_loop(args.athlete, args.date, args.rpe)

if __name__ == "__main__":
    main()