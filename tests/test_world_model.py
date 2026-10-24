# tests/test_world_model.py
import sys
from pathlib import Path

# Add project root directory to sys.path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from datetime import date
from app.adapters.zetaris import ZetarisDBAdapter
from app.engines.world_model import WorldModelEngine

def test_world_model_simulation():
    db_adapter = ZetarisDBAdapter()
    engine = WorldModelEngine(db_adapter)
    
    mock_extracted_loads = [
        {"body_part_id": 1, "name": "Patellar Tendon", "tissue_type": "TENDON", "stress_score": 7.5, "consensus_votes": 3},
        {"body_part_id": 2, "name": "Quadriceps Femoris", "tissue_type": "MUSCLE", "stress_score": 8.0, "consensus_votes": 2}
    ]
    
    result = engine.simulate_state_delta(
        athlete_id="athlete_001",
        session_date=date(2026, 10, 20),
        extracted_loads=mock_extracted_loads,
        session_rpe=8.5
    )
    
    print("\n--- World Model Test Result ---")
    print("Simulation Status:", result["state_delta_summary"]["simulation_status"])
    print("Tracked Entities:", len(result["entities_tracked"]))
    print("Violations Flagged:", len(result["violations"]))
    
    assert "simulation_approved" in result
    assert "entities_tracked" in result
    assert result["entities_tracked"][0]["confidence_score"] == 1.0

if __name__ == "__main__":
    test_world_model_simulation()