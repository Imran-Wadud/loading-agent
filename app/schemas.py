# app/schemas.py
from datetime import date
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, ConfigDict


# ==========================================
# 1. BIOMECHANICAL & EXERCISE DOMAIN SCHEMAS
# ==========================================

class MovementType(BaseModel):
    model_config = ConfigDict(frozen=True)
    
    PUSH: str = "PUSH"
    PULL: str = "PULL"
    LEGS: str = "LEGS"
    CORE: str = "CORE"


class Exercise(BaseModel):
    """
    Schema representing an exercise with biomechanical strain markers.
    """
    exercise_id: str = Field(..., description="Unique identifier or slug for the exercise")
    name: str = Field(..., description="Human-readable name of the exercise")
    movement_category: str = Field(..., description="PUSH, PULL, LEGS, or CORE")
    target_muscle: str = Field(..., description="Primary muscle targeted (e.g., Pectoralis Major, Patellar Tendon)")
    
    # Scientific Safety Indicators
    spinal_axial_load_score: float = Field(
        ..., ge=0.0, le=10.0, 
        description="Axial load on spine (0-10 scale). E.g., Heavy Back Squat = 8.5"
    )
    tendon_stress_score: float = Field(
        ..., ge=0.0, le=10.0, 
        description="Tendinous loading intensity requiring collagen recovery windows"
    )
    cns_fatigue_index: float = Field(
        ..., ge=0.0, le=10.0, 
        description="Central Nervous System fatigue index"
    )


class ExerciseSet(BaseModel):
    """
    Single set execution or planned set.
    """
    exercise_id: str
    reps: int = Field(..., gt=0)
    weight_kg: float = Field(..., ge=0.0)
    rpe: Optional[float] = Field(None, ge=1.0, le=10.0, description="Rate of Perceived Exertion (1-10)")


class DailyWorkout(BaseModel):
    """
    Complete workout for a given calendar day.
    """
    workout_id: str = Field(default_factory=lambda: "w_" + date.today().isoformat())
    workout_date: date
    title: str = Field("Session Plan", description="Summary title for calendar view")
    sets: List[ExerciseSet] = Field(default_factory=list)
    total_volume_kg: float = Field(0.0, description="Calculated total volume (reps * weight)")
    session_rpe: Optional[float] = Field(None, ge=1.0, le=10.0)


class RecoveryWindow(BaseModel):
    """
    Represents an active recovery lockout window following heavy tissue loading.
    """
    window_type: str = Field(..., description="TENDON_COLLAGEN (72h), SPINAL_AXIAL (48h), MPS (48h)")
    trigger_date: date
    safe_date: date = Field(..., description="Date when full intensity is safe again")
    status_color: str = Field("RED", description="RED (Lockout), AMBER (Caution), GREEN (Safe)")
    exercise_cause: str
    message: str

# ==========================================
# 2. AUDIT & SCIENTIFIC EVALUATION SCHEMAS
# ==========================================

class SafetyFlag(BaseModel):
    """
    Individual scientific conflict flag triggered during auditing.
    """
    flag_type: str = Field(
        ..., 
        description="ACWR_OVERLOAD, ACWR_UNDERLOAD, COLLAGEN_WINDOW_VIOLATION, SPINAL_AXIAL_OVERLOAD, PUSH_PULL_IMBALANCE"
    )
    severity: str = Field(..., description="CRITICAL, WARNING, or INFO")
    exercise_involved: Optional[str] = None
    message: str = Field(..., description="Detailed explanation of the biomechanical conflict")
    recommendation: str = Field(..., description="Suggested adjustment")


class WorkoutAuditReport(BaseModel):
    """
    Comprehensive scientific audit report output by the Evaluator Agent.
    """
    workout_date: date
    acwr_value: float = Field(..., description="Current Acute-to-Chronic Workload Ratio")
    acwr_status: str = Field(..., description="OPTIMAL (0.8-1.3), HIGH_RISK (>1.5), UNDERLOAD (<0.8)")
    
    # Biomechanical Checks
    collagen_synthesis_ok: bool = Field(True, description="False if 72h tendon window is violated")
    push_pull_ratio: float = Field(1.0, description="Push volume divided by Pull volume ratio")
    axial_load_total: float = Field(0.0, description="Cumulative spinal loading score for session")
    
    # Verdict
    approved: bool = Field(False, description="True if no CRITICAL safety flags triggered")
    safety_flags: List[SafetyFlag] = Field(default_factory=list)
    suggested_modifications: List[Dict[str, Any]] = Field(
        default_factory=list, 
        description="Key-value pairs of exercises to reduce, replace, or re-schedule"
    )


# ==========================================
# 3. LANGGRAPH AGENT STATE SCHEMA
# ==========================================

# app/schemas.py

class AgentState(BaseModel):
    athlete_id: str = "athlete_001"
    target_date: date
    user_prompt: str = ""
    
    proposed_workout: Optional[DailyWorkout] = None
    audit_report: Optional[WorkoutAuditReport] = None
    dynamic_catalog: Dict[str, Exercise] = Field(default_factory=dict)  # Add this line
    
    iteration_count: int = 0
    max_iterations: int = 3
    human_approved: Optional[bool] = None
    final_output_message: str = ""
    messages: List[Dict[str, Any]] = Field(default_factory=list)
