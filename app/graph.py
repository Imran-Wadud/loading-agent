# app/graph.py
import logging
from typing import TypedDict, List, Dict, Any, Optional
from datetime import date, timedelta
from langgraph.graph import StateGraph, END

from app.agents.consensus import extract_with_consensus
from app.agents.audit import audit_session_safety, calculate_recovery_windows
from app.agents.extractor import fetch_canonical_body_parts, get_db_connection
from app.llm import get_llm

logger = logging.getLogger("loading_agent.graph")

# ==========================================
# 1. State Schema Definition
# ==========================================
class WorkloadState(TypedDict):
    # Input Context
    athlete_id: str
    session_date: date
    raw_input: str
    session_rpe: Optional[float]
    
    # Consensus Stage
    session_title: Optional[str]
    raw_consensus_passes: Optional[int]
    consensus_loads: List[Dict[str, Any]]
    
    # Audit Stage
    session_id: Optional[str]
    is_approved: bool
    audit_flags: List[Dict[str, Any]]
    
    # Non-Linear Remediation Loop
    remediation_suggestion: Optional[str]
    evaluator_approved: bool
    evaluator_feedback: Optional[str]
    remediation_attempts: int
    
    # Final Outcome
    final_status: str


# ==========================================
# 2. Graph Node Implementations
# ==========================================

def consensus_node(state: WorkloadState) -> Dict[str, Any]:
    """Node 1: Executes Meterless Consensus Engine across N parallel passes."""
    logger.info("--- [LangGraph] Entering Consensus Node ---")
    res = extract_with_consensus(
        athlete_id=state["athlete_id"],
        session_date=state["session_date"],
        raw_text=state["raw_input"],
        session_rpe=state.get("session_rpe"),
        num_passes=3
    )
    return {
        "session_id": res["session_id"],
        "session_title": res["session_title"],
        "raw_consensus_passes": res["consensus_passes"],
        "consensus_loads": res["consensus_loads"]
    }


def audit_node(state: WorkloadState) -> Dict[str, Any]:
    """Node 2: Evaluates 72h Tendon / 48h Muscle recovery windows against DB logs."""
    logger.info("--- [LangGraph] Entering Biomechanical Audit Node ---")
    audit = audit_session_safety(
        athlete_id=state["athlete_id"],
        target_date=state["session_date"],
        proposed_session_id=state["session_id"]
    )
    
    status = "APPROVED" if audit["approved"] else "NEEDS_REMEDIATION"
    return {
        "is_approved": audit["approved"],
        "audit_flags": audit["safety_flags"],
        "final_status": status
    }


def remediation_node(state: WorkloadState) -> Dict[str, Any]:
    """Node 3: Generates an alternative session plan addressing active audit flags."""
    attempts = state.get("remediation_attempts", 0) + 1
    logger.info(f"--- [LangGraph] Entering Remediation Generator Node (Attempt {attempts}) ---")
    
    flags_text = "\n".join([f"- {f['message']}" for f in state["audit_flags"]])
    feedback_context = f"\nPrevious Evaluator Feedback:\n{state['evaluator_feedback']}" if state.get("evaluator_feedback") else ""

    system_prompt = (
        "You are an elite S&C Biomechanics Specialist.\n"
        "The proposed training session caused recovery safety violations.\n"
        "Your goal is to suggest an ALTERNATIVE training session or modification "
        "that avoids overloading the flagged tissues while preserving training stimulus.\n"
    )
    user_prompt = (
        f"Original Workout:\n{state['raw_input']}\n\n"
        f"Safety Violations Detected:\n{flags_text}\n"
        f"{feedback_context}\n"
        "Provide a clear, modified workout prescription and briefly explain why it is safe."
    )

    llm = get_llm()
    response = llm.invoke([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ])

    return {
        "remediation_suggestion": response.content.strip(),
        "remediation_attempts": attempts
    }


def evaluator_node(state: WorkloadState) -> Dict[str, Any]:
    """Node 4: Evaluator Node re-checks proposed candidate fix against recovery rules."""
    logger.info("--- [LangGraph] Entering Evaluator Node ---")
    
    # Ask Nemotron to parse the candidate fix into candidate tissue loads
    body_parts = fetch_canonical_body_parts()
    body_parts_text = "\n".join([f"- ID {bp['body_part_id']}: {bp['name']} ({bp['tissue_type']})" for bp in body_parts])
    
    system_prompt = (
        "You are an S&C Evaluator. Parse the proposed modified workout text and map it "
        f"ONLY to these body parts:\n{body_parts_text}\n"
        "Return raw JSON format: {\"loads\": [{\"body_part_id\": 1, \"stress_score\": 7.0}]}"
    )
    
    llm = get_llm()
    eval_resp = llm.invoke([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Candidate Workout:\n{state['remediation_suggestion']}"}
    ])
    
    import json, re
    json_match = re.search(r"\{.*\}", eval_resp.content.strip(), re.DOTALL)
    if not json_match:
        return {
            "evaluator_approved": False,
            "evaluator_feedback": "Could not verify candidate workout tissue safety structure."
        }
    
    candidate_loads = json.loads(json_match.group(0)).get("loads", [])
    
    # Check candidate loads against the active audit flags
    flagged_ids = set()
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            for flag in state["audit_flags"]:
                cur.execute("SELECT body_part_id FROM body_parts WHERE name = %s;", (flag["tissue_name"],))
                res = cur.fetchone()
                if res:
                    flagged_ids.add(res["body_part_id"])
    finally:
        conn.close()

    new_conflicts = [item for item in candidate_loads if item.get("body_part_id") in flagged_ids and item.get("stress_score", 0) >= 6.0]

    if not new_conflicts:
        logger.info("Evaluator Node: Candidate fix APPROVED!")
        return {
            "evaluator_approved": True,
            "evaluator_feedback": None,
            "final_status": "REMEDIATED_APPROVED"
        }
    else:
        logger.warning(f"Evaluator Node: Candidate fix REJECTED ({len(new_conflicts)} lingering conflicts).")
        return {
            "evaluator_approved": False,
            "evaluator_feedback": f"Candidate still loads flagged tissue IDs {flagged_ids} with high score >= 6.0.",
            "final_status": "REMEDIATION_REJECTED"
        }


def commit_node(state: WorkloadState) -> Dict[str, Any]:
    """Node 5: Finalizing execution state."""
    logger.info(f"--- [LangGraph] Final Commit Node. Outcome: {state['final_status']} ---")
    return {}


# ==========================================
# 3. Dynamic Conditional Routing Edges
# ==========================================

def route_after_audit(state: WorkloadState) -> str:
    """Decides if session goes straight to commit or triggers remediation."""
    if state["is_approved"]:
        return "commit"
    return "remediate"


def route_after_evaluation(state: WorkloadState) -> str:
    """Routes back to remediation loop if evaluation failed and attempts < 3."""
    if state["evaluator_approved"]:
        return "commit"
    
    if state["remediation_attempts"] >= 3:
        logger.warning("Max remediation attempts reached (3). Escalating to Coach Review.")
        return "commit"
        
    return "remediate"


# ==========================================
# 4. Constructing the LangGraph Workflow
# ==========================================

builder = StateGraph(WorkloadState)

# Add Nodes
builder.add_node("consensus", consensus_node)
builder.add_node("audit", audit_node)
builder.add_node("remediate", remediation_node)
builder.add_node("evaluator", evaluator_node)
builder.add_node("commit", commit_node)

# Add Direct Edges
builder.set_entry_point("consensus")
builder.add_edge("consensus", "audit")

# Add Conditional Edge 1 (Audit -> Commit or Remediate)
builder.add_conditional_edges(
    "audit",
    route_after_audit,
    {
        "commit": "commit",
        "remediate": "remediate"
    }
)

# Add Direct Edge (Remediate -> Evaluator)
builder.add_edge("remediate", "evaluator")

# Add Conditional Edge 2 (Evaluator Loop -> Remediate or Commit)
builder.add_conditional_edges(
    "evaluator",
    route_after_evaluation,
    {
        "remediate": "remediate",  # Loop back!
        "commit": "commit"
    }
)

builder.add_edge("commit", END)

# Compile Graph
workload_graph = builder.compile()