# app/engines/world_model.py
import logging
from typing import Dict, Any, List, Optional
from datetime import date, timedelta

logger = logging.getLogger(__name__)


class WorldModelEngine:
    """
    Meterless World Model Engine:
    Simulates proposed workload states against biomechanical constraints (72h Tendon / 48h Muscle),
    evaluates 28-day Acute-to-Chronic Workload Ratios (ACWR), tracks entity provenance & confidence,
    and logs state deltas before DB persistence.
    """
    def __init__(self, db_adapter):
        self.db_adapter = db_adapter

    def simulate_state_delta(
        self,
        athlete_id: str,
        session_date: date,
        extracted_loads: List[Dict[str, Any]],
        session_rpe: float = 8.0
    ) -> Dict[str, Any]:
        """
        Simulates the effect of adding candidate tissue loads into the athlete's environment state.
        Evaluates tissue recovery windows, calculates 28-day ACWR, and tracks entity provenance.
        """
        logger.info(f"[World Model Engine] Simulates workload state delta for {athlete_id} on {session_date}")
        
        # 1. Fetch active recovery window history (past 3 days)
        historical_loads = self.db_adapter.get_active_recovery_window(athlete_id, session_date)
        
        provenance_tracked = []
        violations = []
        
        # 2. Track entity provenance & confidence scores
        candidate_session_load = 0.0
        for item in extracted_loads:
            body_part_id = item["body_part_id"]
            body_part_name = item.get("name", f"BodyPart_{body_part_id}")
            tissue_type = item.get("tissue_type", "MUSCLE")
            stress_score = float(item["stress_score"])
            candidate_session_load += stress_score
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
            
            # 3. Simulate environmental constraints (Tissue Recovery Lockouts)
            rpe_factor = max(0.5, session_rpe / 10.0)
            base_window_hours = 72.0 if tissue_type == "TENDON" else 48.0
            required_window_hours = round(base_window_hours * rpe_factor, 1)
            
            for prev in historical_loads:
                if prev["body_part_id"] == body_part_id:
                    prev_date = prev["session_date"]
                    if isinstance(prev_date, str):
                        prev_date = date.fromisoformat(prev_date)
                        
                    delta_days = (session_date - prev_date).days
                    delta_hours = max(0.0, delta_days * 24.0)
                    
                    if delta_hours < required_window_hours and prev["stress_score"] >= 5.0 and stress_score >= 5.0:
                        violations.append({
                            "type": "TISSUE_RECOVERY_VIOLATION",
                            "severity": "CRITICAL",
                            "entity": body_part_name,
                            "tissue_type": tissue_type,
                            "proposed_stress": stress_score,
                            "prior_session_date": str(prev_date),
                            "prior_session_title": prev.get("session_title", "Prior Session"),
                            "hours_since_last_load": delta_hours,
                            "required_recovery_hours": required_window_hours,
                            "message": (
                                f"CONFLICT: High {tissue_type} load on '{body_part_name}' (Score {stress_score:.1f}) "
                                f"within {delta_hours:.0f}h of prior load on {prev_date}. "
                                f"Requires {required_window_hours}h recovery."
                            )
                        })

        # 4. Compute Acute-to-Chronic Workload Ratio (ACWR)
        history_map = self.db_adapter.fetch_historical_daily_loads(athlete_id, session_date, days=28)
        history_map[session_date] = history_map.get(session_date, 0.0) + candidate_session_load

        past_28_days = [session_date - timedelta(days=i) for i in range(28)]
        past_7_days = past_28_days[:7]

        acute_load = sum(history_map.get(d, 0.0) for d in past_7_days) / 7.0
        chronic_load = sum(history_map.get(d, 0.0) for d in past_28_days) / 28.0

        acwr = round(acute_load / chronic_load, 2) if chronic_load > 0.0 else 1.0

        if acwr > 1.5:
            violations.append({
                "type": "ACWR_SPIKE",
                "severity": "CRITICAL",
                "entity": "SYSTEM_ACWR",
                "message": f"ACWR spike detected ({acwr} > 1.5 threshold). 7-day acute volume rapidly exceeding 28-day chronic capacity."
            })

        simulation_approved = not any(v.get("severity") == "CRITICAL" for v in violations)
        
        return {
            "simulation_approved": simulation_approved,
            "acwr": acwr,
            "acute_load_7d": round(acute_load, 1),
            "chronic_load_28d": round(chronic_load, 1),
            "entities_tracked": provenance_tracked,
            "violations": violations,
            "state_delta_summary": {
                "total_proposed_entities": len(provenance_tracked),
                "total_conflict_flags": len(violations),
                "athlete_id": athlete_id,
                "session_date": str(session_date),
                "acwr": acwr,
                "simulation_status": "APPROVED" if simulation_approved else "REJECTED_NEEDS_REMEDIATION"
            }
        }