# app/agents.py
import json
from datetime import date, timedelta
from typing import Dict, Any, List, Literal

from langgraph.graph import StateGraph, END
from app.schemas import AgentState, DailyWorkout, ExerciseSet, WorkoutAuditReport
from app.llm import get_llm
from app.tools import audit_daily_workout
from app.tracing import log_agent_step, log_audit_verdict

# Baseline exercise mapping for prompt context
DEMO_EXERCISES = {
    "barbell_back_squat": "LEGS",
    "barbell_deadlift": "PULL",
    "barbell_bench_press": "PUSH",
    "overhead_press": "PUSH",
    "pull_ups": "PULL",
    "romanian_deadlift": "LEGS",
    "face_pulls": "PULL"
}

# ==========================================
# 1. PLANNER AGENT NODE
# ==========================================

def planner_node(state: AgentState) -> Dict[str, Any]:
    """
    Generates or adjusts a prospective daily workout plan based on athlete intent
    and feedback flags from the Evaluator Agent.
    """
    log_agent_step("PlannerAgent", "generating_workout", {"iteration": state.iteration_count})
    
    llm = get_llm()
    
    audit_feedback = ""
    if state.audit_report and not state.audit_report.approved:
        flags_text = "\n".join([f"- {f.flag_type}: {f.message}" for f in state.audit_report.safety_flags])
        audit_feedback = f"\nYOUR PREVIOUS PLAN WAS REJECTED FOR SAFETY VIOLATIONS:\n{flags_text}\nPlease adjust volume or substitute exercises to fix these issues."

    system_prompt = (
        "You are an elite Strength & Conditioning Planner Agent. "
        "Create a daily structured workout plan for the user in valid JSON format only.\n"
        "Return ONLY JSON matching this structure:\n"
        "{\n"
        '  "title": "Hypertrophy Push Session",\n'
        '  "sets": [\n'
        '    {"exercise_id": "barbell_bench_press", "reps": 8, "weight_kg": 85.0, "rpe": 8.0},\n'
        '    {"exercise_id": "overhead_press", "reps": 10, "weight_kg": 50.0, "rpe": 7.5}\n'
        '  ]\n'
        "}\n"
        f"Available exercise_ids: {list(DEMO_EXERCISES.keys())}\n"
        f"{audit_feedback}"
    )

    user_prompt = f"Athlete Goal: {state.user_prompt or 'Generate a balanced full body workout'}"
    
    try:
        response = llm.invoke([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ])
        
        cleaned_json = response.content.strip().strip("```json").strip("```").strip()
        data = json.loads(cleaned_json)
        
        sets = [ExerciseSet(**s) for s in data.get("sets", [])]
        workout = DailyWorkout(
            workout_date=state.target_date,
            title=data.get("title", "Planned Session"),
            sets=sets
        )
        
        log_agent_step("PlannerAgent", "plan_created", {"sets_count": len(sets)})
        return {"proposed_workout": workout, "iteration_count": state.iteration_count + 1}
        
    except Exception as e:
        log_agent_step("PlannerAgent", "error_fallback", {"error": str(e)})
        # Fallback safety workout
        fallback_workout = DailyWorkout(
            workout_date=state.target_date,
            title="Baseline Recovery Session",
            sets=[
                ExerciseSet(exercise_id="barbell_bench_press", reps=8, weight_kg=60.0, rpe=6.0),
                ExerciseSet(exercise_id="face_pulls", reps=15, weight_kg=20.0, rpe=5.0)
            ]
        )
        return {"proposed_workout": fallback_workout, "iteration_count": state.iteration_count + 1}


# ==========================================
# 2. EVALUATOR AGENT NODE
# ==========================================

def evaluator_node(state: AgentState) -> Dict[str, Any]:
    """
    Evaluates proposed workouts using deterministic biomechanical tools (ACWR, Collagen Windows).
    """
    log_agent_step("EvaluatorAgent", "auditing_workout", {"workout_date": str(state.target_date)})
    
    workout = state.proposed_workout
    if not workout:
        report = WorkoutAuditReport(
            workout_date=state.target_date,
            acwr_value=1.0,
            acwr_status="UNKNOWN",
            approved=False
        )
        return {"audit_report": report}

    # Dummy historical loads for audit calculation
    mock_historical_loads = [1200.0] * 27
    mock_history = [
        {"workout_date": state.target_date - timedelta(days=2), "max_tendon_stress": 8.5}
    ]
    
    from app.schemas import Exercise
    mock_catalog = {
        "barbell_back_squat": Exercise(exercise_id="barbell_back_squat", name="Squat", movement_category="LEGS", target_muscle="Quads", spinal_axial_load_score=8.5, tendon_stress_score=7.5, cns_fatigue_index=8.0),
        "barbell_deadlift": Exercise(exercise_id="barbell_deadlift", name="Deadlift", movement_category="PULL", target_muscle="Back", spinal_axial_load_score=9.5, tendon_stress_score=6.0, cns_fatigue_index=9.0),
        "barbell_bench_press": Exercise(exercise_id="barbell_bench_press", name="Bench", movement_category="PUSH", target_muscle="Chest", spinal_axial_load_score=3.0, tendon_stress_score=8.0, cns_fatigue_index=6.5),
        "overhead_press": Exercise(exercise_id="overhead_press", name="OHP", movement_category="PUSH", target_muscle="Delts", spinal_axial_load_score=7.0, tendon_stress_score=6.5, cns_fatigue_index=7.0),
        "pull_ups": Exercise(exercise_id="pull_ups", name="Pullups", movement_category="PULL", target_muscle="Lats", spinal_axial_load_score=2.0, tendon_stress_score=7.5, cns_fatigue_index=6.0),
        "face_pulls": Exercise(exercise_id="face_pulls", name="Facepulls", movement_category="PULL", target_muscle="Rear Delts", spinal_axial_load_score=1.0, tendon_stress_score=2.0, cns_fatigue_index=2.0)
    }

    report = audit_daily_workout(workout, mock_historical_loads, mock_history, mock_catalog)
    log_audit_verdict(report.approved, len(report.safety_flags), report.acwr_value)
    
    return {"audit_report": report}


# ==========================================
# 3. ROUTING LOGIC & GRAPH DEFINITION
# ==========================================

def should_continue(state: AgentState) -> Literal["planner", "__end__"]:
    """
    Determines whether to accept the workout or route back to the planner for revision.
    """
    if state.audit_report and state.audit_report.approved:
        return "__end__"
    if state.iteration_count >= state.max_iterations:
        log_agent_step("Graph", "max_iterations_reached", {"iterations": state.iteration_count})
        return "__end__"
    return "planner"


def build_workflow() -> Any:
    """
    Assembles the LangGraph execution workflow.
    """
    workflow = StateGraph(AgentState)
    
    workflow.add_node("planner", planner_node)
    workflow.add_node("evaluator", evaluator_node)
    
    workflow.set_entry_point("planner")
    workflow.add_edge("planner", "evaluator")
    
    workflow.add_conditional_edges(
        "evaluator",
        should_continue,
        {
            "planner": "planner",
            "__end__": END
        }
    )
    
    return workflow.compile()