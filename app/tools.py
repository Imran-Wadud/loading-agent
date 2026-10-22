# app/tools.py
from datetime import date, timedelta
from typing import List, Dict, Any
from app.schemas import DailyWorkout, WorkoutAuditReport, SafetyFlag, Exercise


# ==========================================
# 1. ACWR MATHEMATICAL CALCULATIONS
# ==========================================

def calculate_acwr(daily_loads: List[float], acute_days: int = 7, chronic_days: int = 28) -> float:
    """
    Calculates Acute-to-Chronic Workload Ratio (ACWR) based on Gabbett et al.
    Uses uncoupled rolling average: Acute (7 days) / Chronic (28 days).
    """
    if len(daily_loads) < chronic_days:
        # Pad missing initial days with baseline minimum
        padding = [0.0] * (chronic_days - len(daily_loads))
        daily_loads = padding + daily_loads

    acute_load = sum(daily_loads[-acute_days:]) / acute_days
    chronic_load = sum(daily_loads[-chronic_days:]) / chronic_days

    if chronic_load <= 0.0:
        return 1.0  # Neutral baseline if no historical data

    return round(acute_load / chronic_load, 2)


# ==========================================
# 2. BIOMECHANICAL AUDITING TOOLS
# ==========================================

def check_collagen_synthesis_window(
    historical_workouts: List[Dict[str, Any]], 
    target_date: date,
    current_tendon_score: float
) -> bool:
    """
    Checks Miller et al. 72-hour Tendon Collagen Synthesis window.
    Triggers conflict if high tendon stress (>7.0) occurred within last 72 hours.
    """
    window_start = target_date - timedelta(days=3)
    
    for prev in historical_workouts:
        prev_date = prev.get("workout_date")
        if isinstance(prev_date, str):
            prev_date = date.fromisoformat(prev_date)
            
        if window_start <= prev_date < target_date:
            if prev.get("max_tendon_stress", 0.0) >= 7.0 and current_tendon_score >= 7.0:
                return False  # Violation: High tendon load within 72h window
    return True


def calculate_push_pull_ratio(exercise_catalog: Dict[str, Exercise], workout: DailyWorkout) -> float:
    """
    Calculates Push-to-Pull volume ratio for shoulder/joint health.
    """
    push_vol = 0.0
    pull_vol = 0.0
    
    for set_item in workout.sets:
        ex = exercise_catalog.get(set_item.exercise_id)
        if ex:
            vol = set_item.reps * set_item.weight_kg
            if ex.movement_category == "PUSH":
                push_vol += vol
            elif ex.movement_category == "PULL":
                pull_vol += vol
                
    if pull_vol == 0.0:
        return 2.0 if push_vol > 0 else 1.0
    return round(push_vol / pull_vol, 2)


# ==========================================
# 3. COMPREHENSIVE WORKOUT AUDITOR TOOL
# ==========================================

def audit_daily_workout(
    workout: DailyWorkout,
    historical_loads: List[float],
    historical_workouts: List[Dict[str, Any]],
    exercise_catalog: Dict[str, Exercise]
) -> WorkoutAuditReport:
    """
    Deterministic auditor function combining ACWR, Collagen Window, Axial Loading,
    and Movement Balance checks.
    """
    flags: List[SafetyFlag] = []
    
    # 1. ACWR Calculation
    today_volume = sum(s.reps * s.weight_kg for s in workout.sets)
    loads_with_today = historical_loads + [today_volume]
    acwr = calculate_acwr(loads_with_today)
    
    acwr_status = "OPTIMAL"
    if acwr > 1.5:
        acwr_status = "HIGH_RISK"
        flags.append(SafetyFlag(
            flag_type="ACWR_OVERLOAD",
            severity="CRITICAL",
            message=f"ACWR spike detected ({acwr} > 1.5). High injury risk zone (Gabbett et al.).",
            recommendation="Reduce overall session volume by at least 20-30%."
        ))
    elif acwr < 0.8:
        acwr_status = "UNDERLOAD"
        flags.append(SafetyFlag(
            flag_type="ACWR_UNDERLOAD",
            severity="INFO",
            message=f"ACWR is low ({acwr} < 0.8). Risk of detraining.",
            recommendation="Consider a progressive volume ramp up."
        ))

    # 2. Biomechanical Load Aggregation
    total_axial_load = 0.0
    max_tendon_stress = 0.0
    
    for set_item in workout.sets:
        ex = exercise_catalog.get(set_item.exercise_id)
        if ex:
            total_axial_load += ex.spinal_axial_load_score * set_item.reps
            if ex.tendon_stress_score > max_tendon_stress:
                max_tendon_stress = ex.tendon_stress_score

    # 3. Collagen Synthesis Window Check (72h)
    collagen_ok = check_collagen_synthesis_window(historical_workouts, workout.workout_date, max_tendon_stress)
    if not collagen_ok:
        flags.append(SafetyFlag(
            flag_type="COLLAGEN_WINDOW_VIOLATION",
            severity="CRITICAL",
            message="Violates 72h collagen synthesis window (Miller et al.) following a high tendon load session.",
            recommendation="Swap heavy tendinous movement for lower intensity or non-impact alternative."
        ))

    # 4. Push:Pull Movement Ratio
    ratio = calculate_push_pull_ratio(exercise_catalog, workout)
    if ratio > 1.5:
        flags.append(SafetyFlag(
            flag_type="PUSH_PULL_IMBALANCE",
            severity="WARNING",
            message=f"Push-to-Pull volume ratio is excessive ({ratio}:1). Risks anterior shoulder impingement.",
            recommendation="Add supplementary pulling volume (e.g., Facepulls, Cable Rows)."
        ))

    approved = not any(f.severity == "CRITICAL" for f in flags)
    
    return WorkoutAuditReport(
        workout_date=workout.workout_date,
        acwr_value=acwr,
        acwr_status=acwr_status,
        collagen_synthesis_ok=collagen_ok,
        push_pull_ratio=ratio,
        axial_load_total=round(total_axial_load, 1),
        approved=approved,
        safety_flags=flags
    )