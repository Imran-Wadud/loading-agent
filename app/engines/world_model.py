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
    and handles 3-tier temporal logic (Future/Today, Recent Past, Distant Past).
    """
    def __init__(self, db_adapter):
        self.db_adapter = db_adapter

    def _determine_temporal_tier(self, session_date: date, today: Optional[date] = None) -> str:
        if today is None:
            today = date.today()
        
        delta_days = (session_date - today).days
        if delta_days >= 0:
            return "FUTURE_OR_TODAY"
        elif delta_days >= -3:
            return "RECENT_PAST"
        else:
            return "DISTANT_PAST"

    def simulate_state_delta(
        self,
        athlete_id: str,
        session_date: date,
        extracted_loads: List[Dict[str, Any]],
        session_rpe: float = 8.0,
        today: Optional[date] = None
    ) -> Dict[str, Any]:
        """
        Simulates state deltas, categorizing the request into 3 temporal tiers:
        1. FUTURE_OR_TODAY: Proactive workout modification.
        2. RECENT_PAST (1-3 days ago): Active recovery protocols & future session shielding.
        3. DISTANT_PAST (>3 days ago): Pure data logging for ACWR baseline recalibration.
        """
        if today is None:
            today = date.today()

        temporal_tier = self._determine_temporal_tier(session_date, today)
        logger.info(f"[World Model Engine] Workload state delta for {athlete_id} on {session_date} (Tier: {temporal_tier})")

        historical_loads = self.db_adapter.get_active_recovery_window(athlete_id, session_date)
        
        provenance_tracked = []
        violations = []
        candidate_session_load = 0.0

        # Track entities & provenance
        for item in extracted_loads:
            body_part_id = item["body_part_id"]
            body_part_name = item.get("name", f"BodyPart_{body_part_id}")
            tissue_type = item.get("tissue_type", "MUSCLE")
            stress_score = float(item["stress_score"])
            candidate_session_load += stress_score
            votes = item.get("consensus_votes", 3)
            confidence = round(votes / 3.0, 2)
            
            provenance_tracked.append({
                "body_part_id": body_part_id,
                "name": body_part_name,
                "tissue_type": tissue_type,
                "proposed_stress": stress_score,
                "consensus_votes": votes,
                "confidence_score": confidence,
                "provenance_source": "Meterless Consensus Engine"
            })
            
            # Skip 48h/72h lockout evaluation for Distant Past (window already expired)
            if temporal_tier == "DISTANT_PAST":
                continue

            # Evaluate dynamic tissue recovery windows
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
                        severity = "CRITICAL" if temporal_tier == "FUTURE_OR_TODAY" else "WARNING"
                        msg_prefix = "PROACTIVE CONFLICT" if temporal_tier == "FUTURE_OR_TODAY" else "REACTIVE OVERUSE DETECTED"
                        
                        violations.append({
                            "type": "TISSUE_RECOVERY_VIOLATION",
                            "severity": severity,
                            "temporal_tier": temporal_tier,
                            "entity": body_part_name,
                            "tissue_type": tissue_type,
                            "proposed_stress": stress_score,
                            "prior_session_date": str(prev_date),
                            "hours_since_last_load": delta_hours,
                            "required_recovery_hours": required_window_hours,
                            "message": (
                                f"{msg_prefix}: High {tissue_type} strain on '{body_part_name}' (Score {stress_score:.1f}) "
                                f"within {delta_hours:.0f}h of prior load on {prev_date}. Requires {required_window_hours}h recovery."
                            )
                        })

        # Calculate ACWR for past 28 days
        history_map = self.db_adapter.fetch_historical_daily_loads(athlete_id, session_date, days=28)
        history_map[session_date] = history_map.get(session_date, 0.0) + candidate_session_load

        past_28_days = [session_date - timedelta(days=i) for i in range(28)]
        past_7_days = past_28_days[:7]

        acute_load = sum(history_map.get(d, 0.0) for d in past_7_days) / 7.0
        chronic_load = sum(history_map.get(d, 0.0) for d in past_28_days) / 28.0
        acwr = round(acute_load / chronic_load, 2) if chronic_load > 0.0 else 1.0

        if acwr > 1.5 and temporal_tier != "DISTANT_PAST":
            violations.append({
                "type": "ACWR_SPIKE",
                "severity": "CRITICAL" if temporal_tier == "FUTURE_OR_TODAY" else "WARNING",
                "temporal_tier": temporal_tier,
                "entity": "SYSTEM_ACWR",
                "message": f"ACWR spike ({acwr} > 1.5). 7-day acute workload rapidly exceeding 28-day chronic capacity."
            })
        elif acwr > 1.5 and temporal_tier == "DISTANT_PAST":
            violations.append({
                "type": "HISTORICAL_ACWR_RECALIBRATION",
                "severity": "INFO",
                "temporal_tier": temporal_tier,
                "entity": "SYSTEM_ACWR",
                "message": f"Retroactive session logging updated chronic baseline. Calculated past ACWR was {acwr}."
            })

        simulation_approved = not any(v.get("severity") == "CRITICAL" for v in violations)
        
        return {
            "simulation_approved": simulation_approved,
            "temporal_tier": temporal_tier,
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
                "temporal_tier": temporal_tier,
                "acwr": acwr,
                "simulation_status": "APPROVED" if simulation_approved else "NEEDS_REMEDIATION"
            }
        }