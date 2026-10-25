# app/graph.py
import json
import re
import os
import logging
from typing import TypedDict, List, Dict, Any, Optional
from datetime import date
from langgraph.graph import StateGraph, END

# Adapters & Engines
from app.adapters.meterless import get_meterless_llm
from app.adapters.zetaris import ZetarisDBAdapter, get_db_connection
from app.engines.consensus import run_meterless_consensus
from app.engines.world_model import WorldModelEngine

# Telemetry Tracing
from app.tracing import log_agent_step, log_audit_verdict

logger = logging.getLogger("loading_agent.graph")

# =====================================================================
# 1. State Definition
# =====================================================================
class WorkloadState(TypedDict):
    athlete_id: str
    session_date: date
    raw_input: str
    session_rpe: Optional[float]
    
    # Consensus Outputs
    session_title: Optional[str]
    consensus_loads: Optional[List[Dict[str, Any]]]
    
    # World Model / Audit Outputs
    audit_approved: Optional[bool]
    audit_flags: Optional[List[Dict[str, Any]]]
    world_model_state: Optional[Dict[str, Any]]
    
    # Remediation Loop
    remediation_suggestion: Optional[str]
    remediation_attempts: int
    evaluator_approved: Optional[bool]
    
    # Cascade Audit Outputs
    downstream_conflicts: Optional[List[Dict[str, Any]]]
    
    # Final Outcome
    final_status: Optional[str]


# =====================================================================
# 2. Graph Node Functions with Tracing Telemetry
# =====================================================================

def consensus_node(state: WorkloadState) -> Dict[str, Any]:
    """Node 1: Executes 3-Pass Meterless Consensus Engine."""
    logger.info("--- [LangGraph] Entering Consensus Node ---")
    
    res = run_meterless_consensus(
        athlete_id=state["athlete_id"],
        session_date=state["session_date"],
        raw_text=state["raw_input"],
        session_rpe=state.get("session_rpe")
    )
    
    # Trace step event
    log_agent_step(
        agent_name="ConsensusEngine",
        action="3_pass_extraction_completed",
        details={
            "athlete_id": state["athlete_id"],
            "session_title": res["session_title"],
            "extracted_tissues_count": len(res["consensus_loads"])
        }
    )
    
    return {
        "session_title": res["session_title"],
        "consensus_loads": res["consensus_loads"]
    }


def audit_node(state: WorkloadState) -> Dict[str, Any]:
    """Node 2: World Model Engine Simulation & Biomechanical Audit."""
    logger.info("--- [LangGraph] Entering World Model Simulation / Audit Node ---")
    
    db_adapter = ZetarisDBAdapter()
    world_model = WorldModelEngine(db_adapter)
    
    simulation_result = world_model.simulate_state_delta(
        athlete_id=state["athlete_id"],
        session_date=state["session_date"],
        extracted_loads=state["consensus_loads"],
        session_rpe=state.get("session_rpe", 8.0)
    )
    
    approved = simulation_result["simulation_approved"]
    violations = simulation_result["violations"]
    
    # Log structured step & audit verdict telemetry
    log_agent_step(
        agent_name="WorldModelEngine",
        action="state_delta_simulation",
        details={
            "athlete_id": state["athlete_id"],
            "simulation_approved": approved,
            "violations_count": len(violations)
        }
    )
    
    # Log audit summary metric (defaulting ACWR baseline to 1.0 if not computed)
    log_audit_verdict(
        approved=approved,
        flags_count=len(violations),
        acwr=simulation_result.get("acwr", 1.0)
    )
    
    return {
        "audit_approved": approved,
        "audit_flags": violations,
        "world_model_state": simulation_result["state_delta_summary"]
    }


def remediate_node(state: WorkloadState) -> Dict[str, Any]:
    """Node 3: Proposes alternative workout plan resolving audit flags."""
   
    athlete_id = state["athlete_id"]
    session_date = state["session_date"]
    audit_flags = state.get("audit_flags", [])
    temporal_tier = state.get("temporal_tier", "FUTURE_OR_TODAY")
    attempt = state.get("remediation_attempts", 0) + 1

    llm = get_meterless_llm()

    if temporal_tier == "RECENT_PAST":
        system_prompt = (
            "You are an expert Sports Physical Therapist & Recovery Specialist.\n"
            "This workout ALREADY OCCURRED in the past 1 to 3 days. DO NOT suggest modifying or replacing the past workout.\n"
            "Instead, generate a Targeted Recovery Protocol & Future Shielding Plan containing:\n"
            "1. Localized Therapy Protocols (e.g., isometric holds for tendon relief, contrast showers, active mobility work).\n"
            "2. Nutritional & Sleep Targets (protein pacing, anti-inflammatory guidelines).\n"
            "3. Next-Session Shielding Warnings (specific guidance on what exercises/loads to reduce in the upcoming 48-72 hours).\n"
            "Provide clear, practical, bulleted recommendations."
        )
    else:
        system_prompt = (
            "You are an expert Strength & Conditioning Biomechanics Agent.\n"
            "The proposed future/today workout caused biomechanical tissue recovery or ACWR safety violations.\n"
            "Generate an alternative, modified training session that:\n"
            "1. Reduces mechanical stress on the flagged body parts.\n"
            "2. Replaces high-impact movements with safer alternatives.\n"
            "3. Keeps the overall training stimulus effective while lowering RPE/volume.\n"
            "Format the output clearly as a structured workout session."
        )

    user_prompt = (
        f"Athlete: {athlete_id}\n"
        f"Session Date: {session_date}\n"
        f"Temporal Tier: {temporal_tier}\n"
        f"Flagged Safety Violations:\n" + "\n".join([f"- {v.get('message', v)}" for v in audit_flags])
    )

    response = llm.invoke([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ])

    return {
        "remediation_suggestion": response.content,
        "remediation_attempts": attempt
    }


def evaluator_node(state: WorkloadState) -> Dict[str, Any]:
    """Node 4: Evaluator Node validating candidate remediation plan."""
    logger.info("--- [LangGraph] Entering Evaluator Node ---")
    
    db_adapter = ZetarisDBAdapter()
    body_parts = db_adapter.get_canonical_body_parts()
    body_parts_text = "\n".join([f"- ID {bp['body_part_id']}: {bp['name']} ({bp['tissue_type']})" for bp in body_parts])
    
    system_prompt = (
        "You are a Biomechanical Compliance Inspector.\n"
        "Analyze the proposed Candidate Workout fix below.\n"
        "Extract the engaged body part IDs and estimated stress scores (1.0 - 10.0).\n"
        "Map ONLY to these canonical body parts:\n"
        f"{body_parts_text}\n\n"
        "Return ONLY a raw JSON object with key 'loads': [{'body_part_id': int, 'stress_score': float}]"
    )
    
    llm = get_meterless_llm()
    eval_resp = llm.invoke([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Candidate Workout:\n{state['remediation_suggestion']}"}
    ])
    
    try:
        json_match = re.search(r"\{.*\}", eval_resp.content.strip(), re.DOTALL)
        candidate_loads = json.loads(json_match.group(0)).get("loads", []) if json_match else []
        
        world_model = WorldModelEngine(db_adapter)
        eval_simulation = world_model.simulate_state_delta(
            athlete_id=state["athlete_id"],
            session_date=state["session_date"],
            extracted_loads=candidate_loads,
            session_rpe=state.get("session_rpe", 8.0)
        )
        
        is_approved = eval_simulation["simulation_approved"]
        
        log_agent_step(
            agent_name="EvaluatorAgent",
            action="remediation_validation",
            details={
                "candidate_approved": is_approved,
                "remediation_attempt": state.get("remediation_attempts", 1)
            }
        )
        log_audit_verdict(
            approved=is_approved,
            flags_count=len(eval_simulation["violations"]),
            acwr=eval_simulation.get("acwr", 1.0)
        )
        
        return {"evaluator_approved": is_approved}
        
    except Exception as e:
        logger.error(f"Evaluator parsing error: {e}")
        return {"evaluator_approved": False}


def commit_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Persists evaluated session and tissue loads into Zetaris PostgreSQL."""
    athlete_id = state["athlete_id"]
    session_date = state["session_date"]
    session_title = state.get("session_title", "Logged Workout")
    raw_input = state["raw_input"]
    session_rpe = state.get("session_rpe")
    consensus_loads = state.get("consensus_loads", [])
    status = state.get("final_status", "APPROVED")
    acwr = state.get("acwr", 1.0)
    remediation_suggestion = state.get("remediation_suggestion")

    db_adapter = ZetarisDBAdapter()
    session_id = db_adapter.save_approved_session(
        athlete_id=athlete_id,
        session_date=session_date,
        session_title=session_title,
        raw_text=raw_input,
        session_rpe=session_rpe,
        consensus_loads=consensus_loads,
        status=status,
        acwr=acwr,
        remediation_suggestion=remediation_suggestion
    )

    return {"session_id": session_id}


def cascade_audit_node(state: WorkloadState) -> Dict[str, Any]:
    """Node 5: Persists terminal candidate rejection to DB and re-audits downstream sessions."""
    logger.info("--- [LangGraph] Entering Cascade Audit Node ---")
    
    athlete_id = state["athlete_id"]
    session_date = state["session_date"]
    
    db_adapter = ZetarisDBAdapter()
    world_model = WorldModelEngine(db_adapter)
    
    # 1. Handle primary candidate session rejection persistence
    candidate_approved = state.get("simulation_approved", False)
    current_session_id = state.get("session_id")
    current_status = state.get("status", "REJECTED_SAFETY_VIOLATION")
    current_remediation = state.get("remediation_suggestion") or ""

    if not candidate_approved and not current_session_id:
        if not current_remediation:
            violations = state.get("violations", [])
            flags_str = "\n".join([f"- {v.get('message', str(v))}" for v in violations]) or "- ACWR safety threshold breach"
            current_remediation = (
                f"🚨 SAFETY AUDIT REJECTION\n"
                f"This workout was rejected after 3 remediation attempts due to high cumulative fatigue:\n"
                f"{flags_str}\n\n"
                f"Recommended Action: Complete rest or low-intensity active mobility."
            )

        try:
            current_session_id = db_adapter.save_approved_session(
                athlete_id=athlete_id,
                session_date=session_date,
                session_title=f"[REJECTED] {state.get('session_title', 'Workout')}",
                raw_text=state.get("raw_text", ""),
                session_rpe=state.get("session_rpe"),
                consensus_loads=state.get("consensus_loads", []),
                status="REJECTED_SAFETY_VIOLATION",
                acwr=float(state.get("acwr", 1.0)),
                remediation_suggestion=current_remediation
            )
            logger.info(f"[Cascade Audit Node] Saved candidate rejection record: {current_session_id}")
        except Exception as e:
            logger.error(f"[Cascade Audit Node] Error saving candidate rejection: {e}")

    # 2. World Model identifies downstream sessions in violation
    flagged_sessions = []
    try:
        flagged_sessions = world_model.audit_downstream_timeline(athlete_id, session_date)
    except Exception as e:
        logger.error(f"[Cascade Audit Node] Error auditing downstream timeline: {e}")

    if flagged_sessions:
        llm = get_meterless_llm()
        
        for item in flagged_sessions:
            s_id = item["session_id"]
            s_date = item["session_date"]
            sim_res = item["sim_result"]
            flags = [v.get("message", str(v)) for v in sim_res.get("violations", [])]
            
            # Synthesize remediation plan for downstream conflict
            system_prompt = (
                "You are an expert Strength & Conditioning Biomechanics Agent.\n"
                "A backfilled workout on a prior date has created a downstream tissue lockout conflict for this session.\n"
                "Generate an alternative, modified training plan or targeted rest protocol that resolves these flags:"
            )
            user_prompt = (
                f"Athlete: {athlete_id}\n"
                f"Session Date: {s_date}\n"
                f"Title: {item.get('session_title')}\n"
                f"Original Workout: {item.get('raw_input')}\n"
                f"Active Recovery Flags:\n" + "\n".join([f"- {f}" for f in flags])
            )
            
            try:
                response = llm.invoke([
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ])
                
                # Persist remediated plan back to PostgreSQL
                db_adapter.update_session_remediation(
                    session_id=s_id,
                    status="REMEDIATED",
                    remediation_suggestion=response.content
                )
            except Exception as e:
                logger.error(f"[Cascade Audit Node] Downstream remediation error for session {s_id}: {e}")
            
        log_agent_step(
            agent_name="WorldModelEngine",
            action="downstream_cascade_remediation",
            details={
                "athlete_id": athlete_id,
                "remediated_downstream_count": len(flagged_sessions)
            }
        )
    else:
        log_agent_step(
            agent_name="WorldModelEngine",
            action="downstream_cascade_remediation",
            details={
                "athlete_id": athlete_id,
                "remediated_downstream_count": 0
            }
        )

    return {
        "session_id": current_session_id or state.get("session_id"),
        "status": "REJECTED_SAFETY_VIOLATION" if not candidate_approved else state.get("status"),
        "remediation_suggestion": current_remediation or state.get("remediation_suggestion"),
        "downstream_conflicts": flagged_sessions
    }


# =====================================================================
# 3. Conditional Routing Rules
# =====================================================================

def route_audit(state: WorkloadState) -> str:
    if state.get("audit_approved"):
        return "commit"
    return "remediate"


def route_remediation(state: WorkloadState) -> str:
    if state.get("evaluator_approved"):
        return "commit"
    if state.get("remediation_attempts", 0) >= 3:
        logger.warning("Max remediation retry attempts reached (3). Route to cascade_audit.")
        return "cascade_audit"
    return "remediate"


# =====================================================================
# 4. Building the Graph
# =====================================================================

builder = StateGraph(WorkloadState)

builder.add_node("consensus", consensus_node)
builder.add_node("audit", audit_node)
builder.add_node("remediate", remediate_node)
builder.add_node("evaluator", evaluator_node)
builder.add_node("commit", commit_node)
builder.add_node("cascade_audit", cascade_audit_node)

builder.set_entry_point("consensus")
builder.add_edge("consensus", "audit")

builder.add_conditional_edges(
    "audit",
    route_audit,
    {
        "commit": "commit",
        "remediate": "remediate"
    }
)

builder.add_edge("remediate", "evaluator")

builder.add_conditional_edges(
    "evaluator",
    route_remediation,
    {
        "commit": "commit",
        "remediate": "remediate",
        "cascade_audit": "cascade_audit"
    }
)

builder.add_edge("commit", "cascade_audit")
builder.add_edge("cascade_audit", END)

workload_graph = builder.compile()