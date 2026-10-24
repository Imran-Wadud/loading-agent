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
    attempts = state.get("remediation_attempts", 0) + 1
    logger.info(f"--- [LangGraph] Entering Remediation Generator Node (Attempt {attempts}) ---")
    
    flags_summary = "\n".join([f"- {f['message']}" for f in state.get("audit_flags", [])])
    
    log_agent_step(
        agent_name="RemediationAgent",
        action="generating_workout_fix",
        details={
            "attempt": attempts,
            "flagged_issues": len(state.get("audit_flags", []))
        }
    )
    
    system_prompt = (
        "You are an expert Athletic Performance & Rehabilitation Coach.\n"
        "The proposed workout has safety/overuse violations based on active tissue recovery windows:\n"
        f"{flags_summary}\n\n"
        "Propose a modified workout for today that COMPLETELY AVOIDS overusing these compromised body parts.\n"
        "Shift focus to unaffected muscle groups or active recovery exercises.\n"
        "Provide a clear, structured alternative workout description."
    )
    
    llm = get_meterless_llm()
    response = llm.invoke([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Original Input: {state['raw_input']}"}
    ])
    
    return {
        "remediation_suggestion": response.content.strip(),
        "remediation_attempts": attempts
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


def commit_node(state: WorkloadState) -> Dict[str, Any]:
    """Node 5: Commits approved state to PostgreSQL / Zetaris."""
    outcome = "APPROVED" if state.get("audit_approved") else "REMEDIATED_APPROVED"
    logger.info(f"--- [LangGraph] Final Commit Node. Outcome: {outcome} ---")
    
    log_agent_step(
        agent_name="CommitAdapter",
        action="database_persistence",
        details={
            "athlete_id": state["athlete_id"],
            "session_title": state.get("session_title"),
            "final_status": outcome
        }
    )
    
    db_adapter = ZetarisDBAdapter()
    db_adapter.save_approved_session(
        athlete_id=state["athlete_id"],
        session_date=state["session_date"],
        session_title=state.get("session_title", "Logged Session"),
        raw_text=state["raw_input"],
        session_rpe=state.get("session_rpe"),
        consensus_loads=state.get("consensus_loads", [])
    )
    
    return {"final_status": outcome}


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
        logger.warning("Max remediation retry attempts reached (3). Route to END.")
        return "max_retries_reached"
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
        "max_retries_reached": END
    }
)

builder.add_edge("commit", END)

workload_graph = builder.compile()