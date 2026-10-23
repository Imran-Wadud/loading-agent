# app/ui.py
import os
import sys
from pathlib import Path
from datetime import date, timedelta
import streamlit as st

# Ensure root directory is in sys.path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from app.graph import workload_graph
from app.agents.extractor import get_db_connection, fetch_canonical_body_parts

# Page Config
st.set_page_config(
    page_title="Athletic Workload & Recovery Engine",
    page_icon="⚡",
    layout="wide"
)

# Custom CSS
st.markdown("""
<style>
    .main-header { font-size: 2.2rem; font-weight: 700; color: #1E293B; margin-bottom: 0.2rem; }
    .sub-header { font-size: 1.0rem; color: #64748B; margin-bottom: 1.5rem; }
    .status-approved { color: #15803D; font-weight: bold; background-color: #DCFCE7; padding: 0.3rem 0.6rem; border-radius: 0.25rem; }
    .status-remediated { color: #1D4ED8; font-weight: bold; background-color: #DBEAFE; padding: 0.3rem 0.6rem; border-radius: 0.25rem; }
    .status-rejected { color: #B91C1C; font-weight: bold; background-color: #FEE2E2; padding: 0.3rem 0.6rem; border-radius: 0.25rem; }
</style>
""", unsafe_allow_html=True)


@st.cache_data(ttl=300)
def get_active_tissue_status(athlete_id: str, target_date: date):
    """Queries PostgreSQL for tissue loads logged within active recovery windows."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            start_date = target_date - timedelta(days=3)
            cur.execute(
                """
                SELECT bp.name, bp.tissue_type, MAX(tl.stress_score) as max_stress, MAX(s.session_date) as last_date
                FROM tissue_loads_logged tl
                JOIN athlete_sessions s ON tl.session_id = s.session_id
                JOIN body_parts bp ON tl.body_part_id = bp.body_part_id
                WHERE s.athlete_id = %s
                  AND s.session_date BETWEEN %s AND %s
                GROUP BY bp.name, bp.tissue_type
                ORDER BY max_stress DESC;
                """,
                (athlete_id, start_date, target_date)
            )
            return cur.fetchall()
    finally:
        conn.close()


# Layout Header
st.markdown('<div class="main-header">⚡ Athletic Workload & Recovery Engine</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">LangGraph Non-Linear Agent | 72h Tendon & 48h Muscle Remodeling</div>', unsafe_allow_html=True)

# Top Navigation Tabs
tab_evaluator, tab_matrix, tab_profile = st.tabs([
    "🏋️ Workout Evaluation Engine", 
    "📊 Active Recovery Matrix", 
    "👤 Athlete Profile"
])

# ==========================================
# TAB 1: LangGraph Live Execution Engine
# ==========================================
with tab_evaluator:
    col_left, col_right = st.columns([5, 7])
    
    with col_left:
        st.subheader("🏋️ Log Training Session")
        
        with st.form("session_form"):
            athlete_id = st.text_input("Athlete ID", value="athlete_001")
            session_date = st.date_input("Session Date", value=date(2026, 10, 20))
            session_rpe = st.slider("Session RPE (1.0 - 10.0)", min_value=1.0, max_value=10.0, value=8.5, step=0.5)
            
            raw_text = st.text_area(
                "Raw Workout Description",
                height=140,
                value="Heavy Barbell Back Squats 4x8, Romanian Deadlifts 3x10, and Plyometric Box Jumps",
                help="Enter plaintext description of exercises, sets, reps, or sports activity."
            )
            
            submit_btn = st.form_submit_button("🚀 Run Evaluation Pipeline", use_container_width=True)

    with col_right:
        st.subheader("🤖 Real-Time Node Breakdown")
        
        if submit_btn:
            initial_state = {
                "athlete_id": athlete_id,
                "session_date": session_date,
                "raw_input": raw_text,
                "session_rpe": session_rpe,
                "remediation_attempts": 0
            }
            
            # Live Status Container
            status_box = st.status("Executing LangGraph Nodes...", expanded=True)
            
            # Streaming node events from workload_graph
            # app/ui.py

            # Replace the loop inside 'if submit_btn:' with this guarded version:
            final_state = {}
            for event in workload_graph.stream(initial_state):
                for node_name, node_output in event.items():
                    if isinstance(node_output, dict):
                        final_state.update(node_output)
                    else:
                        continue
                        
                    if node_name == "consensus":
                        status_box.write("✅ **Consensus Node Complete:** 3-Pass Parallel Extracted Tissue Loads")
                        loads = node_output.get("consensus_loads", [])
                        if loads:
                            st.write(f"**Extracted Session Title:** `{node_output.get('session_title')}`")
                            st.dataframe([
                                {
                                    "Body Part ID": item["body_part_id"],
                                    "Consensus Score": f"{item['stress_score']:.1f} / 10.0",
                                    "Pass Votes": f"{item.get('consensus_votes', 3)} / 3"
                                }
                                for item in loads
                            ], use_container_width=True, hide_index=True)
                            
                    elif node_name == "audit":
                        status_box.write("✅ **Biomechanical Audit Node Complete:** Queried Database")
                        flags = node_output.get("audit_flags", [])
                        if flags:
                            st.error(f"⚠️ **{len(flags)} Safety Violations Detected!**")
                            for flag in flags:
                                st.caption(f"• {flag['message']}")
                        else:
                            st.success("✅ **0 Safety Violations Detected! Session Approved.**")
                            
                    elif node_name == "remediate":
                        status_box.write("🔄 **Remediation Node Triggered:** Generating Alternative Session Plan...")
                        st.info("AI Candidate Fix Proposed. Forwarding to Evaluator Node...")
                        
                    elif node_name == "evaluator":
                        status_box.write("🔍 **Evaluator Node Complete:** Validating Candidate Fix against Rules")
                        if node_output.get("evaluator_approved"):
                            st.success("🎯 **Evaluator Approved Fix!** Zero lingering tissue conflicts.")
                            st.markdown("### 💡 Proposed Safe Workout Alternative")
                            st.markdown(final_state.get("remediation_suggestion"))
                        else:
                            st.warning("Evaluator rejected fix. Attempting retry loop...")

            status_box.update(label="Pipeline Execution Finished!", state="complete", expanded=False)
            
            # Final Status Banner
            status = final_state.get("final_status", "UNKNOWN")
            if status == "APPROVED":
                st.markdown('### Outcome: <span class="status-approved">APPROVED - 0 Flags</span>', unsafe_allow_html=True)
            elif status == "REMEDIATED_APPROVED":
                st.markdown('### Outcome: <span class="status-remediated">REMEDIATED & EVALUATED SAFE</span>', unsafe_allow_html=True)

# ==========================================
# TAB 2: Active Recovery Matrix
# ==========================================
with tab_matrix:
    st.subheader("📊 Active Tissue Recovery State")
    active_loads = get_active_tissue_status("athlete_001", date(2026, 10, 20))
    
    if active_loads:
        cols = st.columns(3)
        cols[0].metric("Active Tissues Loaded", len(active_loads))
        cols[1].metric("Tendons (72h Window)", sum(1 for item in active_loads if item["tissue_type"] == "TENDON"))
        cols[2].metric("Muscles (48h Window)", sum(1 for item in active_loads if item["tissue_type"] == "MUSCLE"))
        
        st.dataframe([
            {
                "Tissue Name": item["name"],
                "Type": item["tissue_type"],
                "Peak Stress": f"{item['max_stress']:.1f} / 10.0",
                "Last Loaded": str(item["last_date"]),
                "Recovery Window": "72h Remodeling" if item["tissue_type"] == "TENDON" else "48h Synthesis"
            }
            for item in active_loads
        ], use_container_width=True, hide_index=True)
    else:
        st.info("No active tissue load history logged in the past 72 hours.")

# ==========================================
# TAB 3: Athlete Profile Settings
# ==========================================
with tab_profile:
    st.subheader("👤 Athlete Profile & Baseline Constraints")
    st.text_input("Athlete ID", value="athlete_001")
    st.number_input("Baseline Sleep (Hours)", value=8.0)
    st.multiselect("Active Tendon Vulnerabilities", ["Patellar Tendon", "Achilles Tendon", "Hamstring Tendon"], default=["Patellar Tendon"])
    st.button("Save Profile Settings")