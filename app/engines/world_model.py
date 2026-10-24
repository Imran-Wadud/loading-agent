# app/engines/world_model.py
import logging
from typing import Dict, Any, List
from datetime import date, timedelta

logger = logging.getLogger(__name__)

class WorldModelEngine:
    """
    Meterless World Model Engine:
    Simulates proposed workload states against biomechanical constraints (72h Tendon / 48h Muscle),
    tracks entity provenance & confidence, and logs change deltas before DB persistence.
    """
    def __init__(self, db_adapter):
        self.db_adapter = db_adapter

    def simulate_state_delta(
        self,
        athlete_id: str,
        session_date: date,
        extracted_loads: List[Dict[str, Any]],
        session_rpe: float
    ) -> Dict[str, Any]:
        """
        Simulates the effect of adding candidate tissue loads into the athlete's environment state.
        Returns detailed provenance, confidence metrics, and rule violation flags.
        """
        logger.info(f"[World Model Engine] Simulates workload state delta for {athlete_id} on {session_date}")
        
        # 1. Fetch current environment state (active tissue loads within lookback window)
        historical_loads = self.db_adapter.get_active_recovery_window(athlete_id, session_date)
        
        provenance_tracked = []
        violations = []
        
        # 2. Entity provenance & confidence tracking
        for item in extracted_loads:
            body_part_id = item["body_part_id"]
            body_part_name = item.get("name", f"BodyPart_{body_part_id}")
            tissue_type = item.get("tissue_type", "MUSCLE")
            stress_score = item["stress_score"]
            votes = item.get("consensus_votes", 3)
            
            # Compute confidence score based on consensus agreement
            confidence = round(votes / 3.0, 2)
            
            tracked_entity = {
                "body_part_id": body_part_id,
                "name": body_part_name,
                "tissue_type": tissue_type,
                "proposed_stress": stress_score,
                "consensus_votes": votes,
                "confidence_score": confidence,
                "provenance_source": "Meterless Consensus Engine (Nemotron 3-Pass)"
            }
            provenance_tracked.append(tracked_entity)
            
            # 3. Simulate environmental constraints
            # Calculate dynamic recovery window based on session RPE
            rpe_factor = max(0.5, session_rpe / 10.0)
            base_window_hours = 72.0 if tissue_type == "TENDON" else 48.0
            required_window_hours = round(base_window_hours * rpe_factor, 1)
            
            # Check against prior session occurrences in historical state
            for prev in historical_loads:
                if prev["body_part_id"] == body_part_id:
                    delta_hours = (session_date - prev["session_date"]).total_seconds() / 3600.0
                    
                    if delta_hours < required_window_hours and prev["stress_score"] >= 5.0 and stress_score >= 5.0:
                        violations.append({
                            "entity": body_part_name,
                            "tissue_type": tissue_type,
                            "proposed_stress": stress_score,
                            "prior_session_date": str(prev["session_date"]),
                            "prior_session_title": prev.get("session_title", "Prior Session"),
                            "hours_since_last_load": delta_hours,
                            "required_recovery_hours": required_window_hours,
                            "message": (
                                f"CONFLICT: High {tissue_type} load on '{body_part_name}' (Score {stress_score:.1f}) "
                                f"within {delta_hours:.0f}h of prior load on {prev['session_date']}. "
                                f"Requires {required_window_hours}h recovery."
                            )
                        })

        simulation_approved = len(violations) == 0
        
        return {
            "simulation_approved": simulation_approved,
            "entities_tracked": provenance_tracked,
            "violations": violations,
            "state_delta_summary": {
                "total_proposed_entities": len(provenance_tracked),
                "total_conflict_flags": len(violations),
                "athlete_id": athlete_id,
                "session_date": str(session_date),
                "simulation_status": "APPROVED" if simulation_approved else "REJECTED_NEEDS_REMEDIATION"
            }
        }