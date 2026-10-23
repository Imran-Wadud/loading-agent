# app/ui.py
import sys
from pathlib import Path

# Resolve project root directory
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

import pandas as pd
from datetime import date
import streamlit as st

from app.agents import build_workflow
from app.schemas import AgentState, DailyWorkout

# Page Config
st.set_page_config(
    page_title="loading... | Biomechanical Copilot",
    page_icon="⚡",
    layout="wide"
)

st.title("⚡ loading... Biomechanical Copilot")
st.caption("Dynamic LLM Exercise Extraction & Safety Evaluation")

DEFAULT_TIMETABLE = """Monday: Badminton 2hrs (multifeed / technical), no gym
Tuesday: Badminton 2hrs (One-shuttle defense / match-specific routines), leg session
Wednesday: Badminton 2hrs (One-shuttle attack / match-specific routines), push session
Thursday: Badminton 2hrs (Technical), pull session and tendon session (overcoming isometrics and slow rep leg press)
Friday: Badminton 2hrs (Matchplay), anti rotation session
Saturday: Badminton 2hrs (One shuttle rallying/consistency), hip abductor/adductor session
Sunday: Badminton 2hrs (Technical), ab session"""

col_left, col_right = st.columns(2)

with col_left:
    st.subheader("1. Weekly Schedule / Input Prompt")
    schedule_input = st.text_area("Paste Timetable or Routines:", value=DEFAULT_TIMETABLE, height=220)

with col_right:
    st.subheader("2. Audit Query / Target Focus")
    query_input = st.text_area(
        "Enter Target Question:",
        value="Evaluate my Thursday session (Badminton + Pull + Overcoming Isometrics). Are my tendon stress levels and recovery windows safe?",
        height=220
    )

st.divider()

if st.button("🚀 Evaluate Schedule & Safety Windows", type="primary", use_container_width=True):
    with st.status("LLM Extracting Movements & Auditing Biomechanics...", expanded=True) as status:
        app = build_workflow()
        
        full_prompt = f"SCHEDULE:\n{schedule_input}\n\nTARGET QUERY:\n{query_input}"
        initial_state = AgentState(
            target_date=date.today(),
            user_prompt=full_prompt
        )
        
        final_state = app.invoke(initial_state)
        status.update(label="Evaluation Complete!", state="complete", expanded=False)

    st.header("📊 Results & Biomechanical Breakdown")
    
    workout: DailyWorkout = getattr(final_state, "proposed_workout", None)
    audit = getattr(final_state, "audit_report", None)
    catalog = getattr(final_state, "dynamic_catalog", {})

    c1, c2 = st.columns(2)

    with c1:
        st.subheader("🤖 LLM Extracted Movements & Worked Muscles")
        if workout and workout.sets:
            table_data = []
            for s in workout.sets:
                ex_info = catalog.get(s.exercise_id)
                table_data.append({
                    "Movement / Sport": ex_info.name if ex_info else s.exercise_id,
                    "Target Muscle / Tendon": ex_info.target_muscle if ex_info else "N/A",
                    "Category": ex_info.movement_category if ex_info else "N/A",
                    "Tendon Stress (0-10)": ex_info.tendon_stress_score if ex_info else "N/A",
                    "Spinal Load (0-10)": ex_info.spinal_axial_load_score if ex_info else "N/A",
                    "Volume/Duration": f"{s.reps} reps/mins @ {s.weight_kg}kg"
                })
            st.dataframe(pd.DataFrame(table_data), use_container_width=True)

    with c2:
        st.subheader("🧪 Biomechanical Safety Verdict")
        if audit:
            m1, m2 = st.columns(2)
            m1.metric("ACWR Score", f"{audit.acwr_value}", delta=audit.acwr_status)
            m2.metric("Push:Pull Ratio", f"{audit.push_pull_ratio}:1")
            
            m3, m4 = st.columns(2)
            m3.metric("Tendon Recovery (72h)", "PASS" if audit.collagen_synthesis_ok else "VIOLATION")
            m4.metric("Spinal Axial Volume", f"{audit.axial_load_total} pts")

            st.divider()
            if audit.approved:
                st.success("✅ **AUDIT APPROVED:** Routine is within safe recovery limits.")
            else:
                st.error("❌ **AUDIT REJECTED / SAFETY CAUTION**")
                for flag in audit.safety_flags:
                    st.warning(f"**{flag.flag_type}** ({flag.severity}):\n{flag.message}\n\n👉 **Suggested Fix:** {flag.recommendation}")