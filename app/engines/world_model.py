# app/engines/world_model.py
import logging
import math
from typing import Dict, Any, List, Optional
from datetime import date, timedelta
from app.adapters.meterless_world_model import persist_workload_assessment


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

    def audit_downstream_timeline(
        self, athlete_id: str, from_date: date
    ) -> List[Dict[str, Any]]:
        logger.info(
            "[Cascade] Starting downstream audit: athlete=%s from_date=%s",
            athlete_id, from_date
        )

        sessions = self.db_adapter.load_sessions(athlete_id)
        logger.info(
            "[Cascade] Loaded %d total sessions for athlete=%s",
            len(sessions), athlete_id
        )

        downstream = []
        for s in sessions:
            s_date = s["session_date"]
            if isinstance(s_date, str):
                s_date = date.fromisoformat(s_date)

            if s_date > from_date:
                downstream.append((s_date, s))

        downstream.sort(key=lambda x: x[0])

        logger.info(
            "[Cascade] Found %d downstream sessions after %s",
            len(downstream), from_date
        )

        flagged_sessions = []

        for s_date, s in downstream:
            session_id = s["session_id"]
            logger.info(
                "[Cascade] Re-auditing session=%s date=%s title=%s",
                session_id, s_date, s.get("session_title")
            )

            tissue_loads = self.db_adapter.get_session_tissue_loads(session_id)

            if not tissue_loads:
                logger.warning(
                    "[Cascade] Skipping session=%s: no stored tissue loads",
                    session_id
                )
                continue

            sim_res = self.simulate_state_delta(
                athlete_id=athlete_id,
                session_date=s_date,
                extracted_loads=tissue_loads,
                session_rpe=float(s.get("session_rpe") or 8.0),
            )

            violations = sim_res.get("violations", [])
            logger.info(
                "[Cascade] Result session=%s approved=%s violations=%d ACWR=%s",
                session_id,
                sim_res.get("simulation_approved"),
                len(violations),
                sim_res.get("acwr"),
            )

            # Flag violations independently of the critical-only approval result.
            if violations:
                flagged_sessions.append({
                    "session_id": session_id,
                    "session_date": s_date,
                    "session_title": s.get("session_title"),
                    "raw_input": s.get("raw_input"),
                    "sim_result": sim_res,
                })
                logger.info(
                    "[Cascade] Flagged session=%s date=%s violations=%d approved=%s",
                    s["session_id"],
                    s_date,
                    len(violations),
                    sim_res["simulation_approved"],
                )

        logger.info(
            "[Cascade] Audit complete: checked=%d flagged=%d",
            len(downstream), len(flagged_sessions)
        )

        return flagged_sessions

    def simulate_state_delta(
        self,
        athlete_id: str,
        session_date: date,
        extracted_loads: List[Dict[str, Any]],
        session_rpe: float = 8.0,
        exclude_session_id=None,
        today: Optional[date] = None,
        athlete_name: Optional[str] = None,
        session_id: Optional[str] = None,
        session_name: Optional[str] = None,
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
                if prev["body_part_id"] != body_part_id:
                    continue

                prev_date = prev["session_date"]
                if isinstance(prev_date, str):
                    prev_date = date.fromisoformat(prev_date)

                delta_days = (session_date - prev_date).days
                if delta_days <= 0:
                    continue

                delta_hours = delta_days * 24.0

                try:
                    previous_stress = float(prev["stress_score"])
                    current_stress = float(stress_score)
                except (TypeError, ValueError):
                    logger.warning(
                        "[World Model] Skipping recovery comparison: "
                        "invalid stress score for tissue=%s, previous=%r, current=%r",
                        body_part_id,
                        prev.get("stress_score"),
                        stress_score,
                    )
                    continue

                logger.info(
                    "[World Model Debug] Tissue=%s (%s), "
                    "previous_date=%s, current_date=%s, "
                    "previous_stress=%.2f, current_stress=%.2f, "
                    "gap_hours=%.1f, required_hours=%.1f",
                    body_part_name,
                    body_part_id,
                    prev_date,
                    session_date,
                    previous_stress,
                    current_stress,
                    delta_hours,
                    required_window_hours,
                )

                if (
                    delta_hours < required_window_hours
                    and previous_stress >= 5.0
                    and current_stress >= 5.0
                ):
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

        # Count active training days in the 28-day window
        active_days_count = sum(1 for d in past_28_days if history_map.get(d, 0.0) > 0.0)

        acute_load = sum(history_map.get(d, 0.0) for d in past_7_days) / 7.0
        chronic_load = sum(history_map.get(d, 0.0) for d in past_28_days) / 28.0

        # Cold-Start Protection: Require >= 3 active days to calculate ACWR spike
        if active_days_count < 3:
            acwr = 1.0
            logger.info(f"[World Model Engine] Baseline building ({active_days_count}/3 active days). ACWR defaulted to 1.0.")
        else:
            acwr = round(acute_load / chronic_load, 2) if chronic_load > 0.0 else 1.0

        if acwr > 1.5 and temporal_tier != "DISTANT_PAST" and active_days_count >= 3:
            violations.append({
                "type": "ACWR_SPIKE",
                "severity": "CRITICAL" if temporal_tier == "FUTURE_OR_TODAY" else "WARNING",
                "temporal_tier": temporal_tier,
                "entity": "SYSTEM_ACWR",
                "message": f"ACWR spike ({acwr} > 1.5). 7-day acute workload rapidly exceeding 28-day chronic capacity."
            })

        simulation_approved = not any(v.get("severity") == "CRITICAL" for v in violations)
        
        result = {
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
                "simulation_status": (
                    "APPROVED" if simulation_approved
                    else "NEEDS_REMEDIATION"
                ),
            },
        }

        # Persist the completed result through the Meterless World Model.
        # This does not recalculate or override the workload decision.
        wm_result = persist_workload_assessment(
            athlete_id=str(athlete_id),
            athlete_name=str(athlete_name or athlete_id),
            session_id=str(
                session_id or f"{athlete_id}:{session_date.isoformat()}"
            ),
            session_name=str(
                session_name or f"Training session {session_date.isoformat()}"
            ),
            session_date=session_date.isoformat(),
            assessment={
                "simulation_approved": result["simulation_approved"],
                "temporal_tier": result["temporal_tier"],
                "acwr": result["acwr"],
                "acute_load_7d": result["acute_load_7d"],
                "chronic_load_28d": result["chronic_load_28d"],
                "entities_tracked": result["entities_tracked"],
                "violations": result["violations"],
                "state_delta_summary": result["state_delta_summary"],
            },
        )

        result["meterless_world_model"] = wm_result
        return result
