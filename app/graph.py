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
    temporal_tier: Optional[str]
    cache_hit: Optional[bool]
    
    # Consensus Outputs
    session_title: Optional[str]
    consensus_loads: Optional[List[Dict[str, Any]]]
    
    # World Model / Audit Outputs
    audit_approved: Optional[bool]
    audit_flags: Optional[List[Dict[str, Any]]]
    world_model_state: Optional[Dict[str, Any]]
    acwr: Optional[float]
    
    # Remediation Loop
    remediation_suggestion: Optional[str]
    remediation_attempts: int
    evaluator_approved: Optional[bool]
    remediated_loads: Optional[List[Dict[str, Any]]]
    
    # Cascade Audit & Persistence
    session_id: Optional[str]
    downstream_conflicts: Optional[List[Dict[str, Any]]]
    final_status: Optional[str]


# =====================================================================
# 2. Graph Node Functions
# =====================================================================

def cache_check_node(state: WorkloadState):
    """Checks the database for an exact string match of the workout input."""
    raw_input = state.get("raw_input", "")
    db = ZetarisDBAdapter()
    
    cached_data = db.get_cached_tissue_loads(raw_input)
    
    if cached_data and cached_data.get("loads"):
        print(f"Cache Hit: Bypassing LLM Consensus for '{raw_input[:30]}...'")
        return {
            "consensus_loads": cached_data["loads"], 
            "session_title": cached_data["session_title"],
            "cache_hit": True
        }
    
    print(f"Cache Miss: Routing to Consensus Engine for '{raw_input[:30]}...'")
    return {"cache_hit": False}

def route_after_cache(state: dict) -> str:
    """Routes to world model if cache hit, otherwise to consensus extraction."""
    if state.get("cache_hit"):
        return "audit"
    return "consensus"

def consensus_node(state: WorkloadState) -> Dict[str, Any]:
    """Node 1: Executes 3-Pass Meterless Consensus Engine."""
    logger.info("--- [LangGraph] Entering Consensus Node ---")
    
    res = run_meterless_consensus(
        athlete_id=state["athlete_id"],
        session_date=state["session_date"],
        raw_text=state["raw_input"],
        session_rpe=state.get("session_rpe")
    )
    
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
    computed_acwr = simulation_result.get("acwr", 1.0)
    
    log_agent_step(
        agent_name="WorldModelEngine",
        action="state_delta_simulation",
        details={
            "athlete_id": state["athlete_id"],
            "simulation_approved": approved,
            "violations_count": len(violations)
        }
    )
    
    log_audit_verdict(
        approved=approved,
        flags_count=len(violations),
        acwr=computed_acwr
    )
    
    return {
        "audit_approved": approved,
        "audit_flags": violations,
        "world_model_state": simulation_result["state_delta_summary"],
        "acwr": computed_acwr
    }


def remediate_node(state: WorkloadState) -> Dict[str, Any]:
    """Node 3: Formulates an optional advisory alternative plan."""
    athlete_id = state["athlete_id"]
    session_date = state["session_date"]
    audit_flags = state.get("audit_flags", [])
    today = date.today()
    is_historical = session_date < today
    attempt = state.get("remediation_attempts", 0) + 1

    llm = get_meterless_llm()

    if is_historical:
        system_prompt = (
            "You are an expert Sports Physical Therapist & Recovery Advisor.\n"
            "This workout ALREADY OCCURRED and had high cumulative tissue strain.\n"
            "Generate a supportive Recovery Guidance Plan for the athlete.\n\n"
            "Format your output EXACTLY as follows (no markdown headers, no fluff):\n"
            "Strain Notice: [1-line summary of historical strain logged]\n"
            "Suggested Alternative: N/A - Workout completed (Focus on baseline recovery)\n"
            "Recovery Tip: [Targeted recovery actions, hydration, sleep, or light mobility advice]"
        )
    else:
        system_prompt = (
            "You are a Strength & Conditioning Biomechanics Advisor.\n"
            "An upcoming workout presents potential tissue strain or ACWR elevation.\n"
            "Formulate an OPTIONAL alternative training suggestion that the athlete can choose to accept or disregard.\n\n"
            "Format your output EXACTLY as follows (no markdown headers, no fluff):\n"
            "Strain Notice: [1-line summary of risk]\n"
            "Suggested Alternative: [Specific modified exercise or load drop option]\n"
            "Recovery Tip: [Targeted recovery or light mobility suggestion]"
        )

    user_prompt = (
        f"Athlete: {athlete_id}\n"
        f"Session Date: {session_date}\n"
        f"Active Safety Flags:\n" + "\n".join([f"- {v.get('message', str(v))}" for v in audit_flags])
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
    """
    Node 4: Evaluates candidate remediation alternative.
    Verifies alternative safety, but does NOT overwrite the submitted workout's risk status.
    """
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
        
        return {
            "evaluator_approved": is_approved,
            "remediated_loads": candidate_loads if is_approved else []
        }
        
    except Exception as e:
        logger.error(f"Evaluator parsing error: {e}")
        return {"evaluator_approved": False}


def commit_node(state: WorkloadState) -> Dict[str, Any]:
    """
    Persists submitted workout to Zetaris DB.
    Determines status based strictly on original submission flags:
    - Clean -> APPROVED (Green)
    - Past with flags -> HISTORICAL_WARNING (Yellow)
    - Future/Today with flags -> PROPOSED_RISK_WARNING (Risk Badge)
    """
    logger.info("--- [LangGraph] Entering Commit Node ---")
    athlete_id = state["athlete_id"]
    session_date = state["session_date"]
    session_title = state.get("session_title", "Logged Workout")
    raw_input = state["raw_input"]
    session_rpe = state.get("session_rpe")
    consensus_loads = state.get("consensus_loads", [])
    acwr = state.get("acwr", 1.0)
    remediation_suggestion = state.get("remediation_suggestion")

    is_historical = session_date < date.today()

    # Determine status & title prefix based on original submission audit
    if state.get("audit_approved"):
        status = "APPROVED"
        final_title = session_title
    elif is_historical:
        status = "HISTORICAL_WARNING"
        final_title = f"[HIGH STRAIN] {session_title}"
    else:
        status = "PROPOSED_RISK_WARNING"
        final_title = f"[RISK] {session_title}"

    db_adapter = ZetarisDBAdapter()
    session_id = db_adapter.save_approved_session(
        athlete_id=athlete_id,
        session_date=session_date,
        session_title=final_title,
        raw_text=raw_input,
        session_rpe=session_rpe,
        consensus_loads=consensus_loads,
        status=status,
        acwr=acwr,
        remediation_suggestion=remediation_suggestion
    )

    return {"session_id": session_id, "final_status": status}


def cascade_audit_node(state: WorkloadState) -> Dict[str, Any]:
    """Node 5: Handles unapproved candidate persistence (after 3 failed retries) and audits downstream timeline."""
    logger.info("--- [LangGraph] Entering Cascade Audit Node ---")
    
    athlete_id = state["athlete_id"]
    session_date = state["session_date"]
    today = date.today()
    is_historical = session_date < today
    
    db_adapter = ZetarisDBAdapter()
    world_model = WorldModelEngine(db_adapter)
    
    current_session_id = state.get("session_id")
    current_remediation = state.get("remediation_suggestion") or ""

    # ONLY persist here if candidate was NOT saved by commit_node
    if not current_session_id:
        if is_historical:
            status = "HISTORICAL_WARNING"
            title = f"[HIGH STRAIN] {state.get('session_title', 'Workout')}"
            if not current_remediation:
                current_remediation = "⚠️ HIGH HISTORICAL STRAIN LOGGED\nWorkout exceeded standard fatigue limits."
        else:
            status = "PROPOSED_RISK_WARNING"
            title = f"[RISK] {state.get('session_title', 'Workout')}"
            if not current_remediation:
                violations = state.get("audit_flags", [])
                flags_str = "\n".join([f"- {v.get('message', str(v))}" for v in violations]) or "- Cumulative ACWR strain"
                current_remediation = f"⚠️ ADVISORY LOAD WARNING\n{flags_str}\n\n💡 Consider complete rest or light recovery."

        try:
            current_session_id = db_adapter.save_approved_session(
                athlete_id=athlete_id,
                session_date=session_date,
                session_title=title,
                raw_text=state.get("raw_input", ""),
                session_rpe=state.get("session_rpe"),
                consensus_loads=state.get("consensus_loads", []),
                status=status,
                acwr=float(state.get("acwr", 1.0)),
                remediation_suggestion=current_remediation
            )
            logger.info(f"[Cascade Audit Node] Persisted unapproved session ({status}): {current_session_id}")
        except Exception as e:
            logger.error(f"[Cascade Audit Node] Error saving unapproved session: {e}")

    # Re-audit downstream timeline
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
            
            system_prompt = (
                "You are a Strength & Conditioning Biomechanics Advisor.\n"
                "A backfilled prior workout has updated cumulative fatigue, creating potential tissue strain for this session.\n"
                "Formulate an OPTIONAL alternative training suggestion that the athlete can choose to accept or disregard.\n\n"
                "Format your output EXACTLY as follows (no markdown headers, no fluff):\n"
                "Strain Notice: [1-line summary of risk]\n"
                "Suggested Alternative: [Specific modified exercise or load drop option]\n"
                "Recovery Tip: [Targeted recovery or light mobility suggestion]"
            )
            user_prompt = (
                f"Athlete: {athlete_id}\n"
                f"Session Date: {s_date}\n"
                f"Title: {item.get('session_title')}\n"
                f"Original Input: {item.get('raw_input')}\n"
                f"Active Flags:\n" + "\n".join([f"- {f}" for f in flags])
            )
            
            try:
                response = llm.invoke([
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ])
                db_adapter.update_session_remediation(
                    session_id=s_id,
                    status="PROPOSED_RISK_WARNING",
                    remediation_suggestion=response.content
                )
            except Exception as e:
                logger.error(f"[Cascade Audit Node] Downstream remediation error for session {s_id}: {e}")

    return {
        "session_id": current_session_id or state.get("session_id"),
        "final_status": state.get("final_status") or ("PROPOSED_RISK_WARNING" if not state.get("audit_approved") else "APPROVED"),
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
    # Always route to commit so the submitted workout is saved with its RISK status
    # and attached evaluated remediation plan.
    if state.get("evaluator_approved") or state.get("remediation_attempts", 0) >= 3:
        return "commit"
    return "remediate"


# =====================================================================
# 4. Building the Graph
# =====================================================================

builder = StateGraph(WorkloadState)

# Add all nodes
builder.add_node("cache_check", cache_check_node)
builder.add_node("consensus", consensus_node)
builder.add_node("audit", audit_node)
builder.add_node("remediate", remediate_node)
builder.add_node("evaluator", evaluator_node)
builder.add_node("commit", commit_node)
builder.add_node("cascade_audit", cascade_audit_node)

# Set the new entry point
builder.set_entry_point("cache_check")

# Route based on cache hit/miss
builder.add_conditional_edges(
    "cache_check",
    route_after_cache,
    {
        "audit": "audit",
        "consensus": "consensus"
    }
)

# Standard flow continues
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
        "remediate": "remediate"
    }
)

builder.add_edge("commit", "cascade_audit")
builder.add_edge("cascade_audit", END)

workload_graph = builder.compile()