# app/ui.py
import streamlit as st
import pandas as pd
from datetime import date, timedelta
import json

# Import internal modules
from app.schemas import AgentState, DailyWorkout, ExerciseSet
from app.agents import build_workflow

# Set page config
st.set_page_config(
    page_title="loading... | Biomechanical Workload Agent",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for horizontal scrolling tile deck
st.markdown("""
<style>
    .tile-container {
        display: flex;
        overflow-x: auto;
        padding: 10px 0px;
        gap: 15px;
        scroll-behavior: smooth;
    }
    .tile-container::-webkit-scrollbar {
        height: 8px;
    }
    .tile-container::-webkit-scrollbar-thumb {
        background: #4A5568;
        border-radius: 4px;
    }
    .day-tile {
        min-width: 170px;
        max-width: 170px;
        background-color: #1A202C;
        border: 1px solid #2D3748;
        border-radius: 12px;
        padding: 15px;
        text-align: center;
        color: white;
        transition: transform 0.2s, border-color 0.2s;
    }
    .day-tile:hover {
        transform: translateY(-4px);
        border-color: #6366F1;
    }
    .status-badge {
        font-size: 0.75rem;
        font-weight: bold;
        padding: 3px 8px;
        border-radius: 12px;
        display: inline-block;
        margin-top: 5px;
    }
    .badge-green { background-color: #166534; color: #4ADE80; }
    .badge-red { background-color: #991B1B; color: #FCA5A5; }
    .badge-amber { background-color: #92400E; color: #FCD34D; }
</style>
""", unsafe_allow_html=True)


# Initialize Session State
if "selected_date" not in st.session_state:
    st.session_state.selected_date = date.today()
if "workout_history" not in st.session_state:
    st.session_state.workout_history = {}


# ==========================================
# SIDEBAR: SETTINGS & TIMETABLE CSV IMPORT
# ==========================================
with st.sidebar:
    st.title("⚡ loading...")
    st.caption("Biomechanical Load & Safety Copilot")
    st.divider()

    st.subheader("📅 Schedule Import")
    uploaded_file = st.file_uploader("Import Timetable CSV", type=["csv"])
    if uploaded_file is not None:
        try:
            df = pd.read_csv(uploaded_file)
            st.success(f"Loaded {len(df)} sessions!")
            st.dataframe(df.head(3), use_container_width=True)
        except Exception as e:
            st.error(f"Failed to read CSV: {e}")

    st.divider()
    st.subheader("👤 Athlete Profile")
    st.text("ID: athlete_001")
    st.text("Primary Sport: Powerlifting / Track")


# ==========================================
# MAIN INTERFACE: CAROUSEL DAY TILES
# ==========================================
st.title("Athletic Recovery & Workload Deck")

# Generate 14-day rolling window
start_date = date.today() - timedelta(days=3)
days_window = [start_date + timedelta(days=i) for i in range(14)]

st.subheader("📆 Select Day (Horizontal Scroll)")

# Render horizontal tile selector columns
cols = st.columns(len(days_window))

for idx, day in enumerate(days_window):
    with cols[idx]:
        is_today = day == date.today()
        day_str = day.strftime("%b %d")
        day_name = day.strftime("%a")
        
        # Mock status logic (replace with Zetaris query result)
        if idx % 5 == 0:
            badge_class = "badge-red"
            badge_text = "🔴 Tendon Lockout"
        elif idx % 3 == 0:
            badge_class = "badge-amber"
            badge_text = "🟡 ACWR Caution"
        else:
            badge_class = "badge-green"
            badge_text = "🟢 Optimal"

        tile_html = f"""
        <div class="day-tile" style="border: {'2px solid #6366F1' if day == st.session_state.selected_date else '1px solid #2D3748'};">
            <div style="font-size: 0.8rem; color: #A0AEC0;">{day_name}</div>
            <div style="font-size: 1.2rem; font-weight: bold;">{day_str}</div>
            <div class="status-badge {badge_class}">{badge_text}</div>
        </div>
        """
        st.markdown(tile_html, unsafe_allow_html=True)
        if st.button(f"Inspect", key=f"btn_{day.isoformat()}"):
            st.session_state.selected_date = day


# ==========================================
# EXPANDED DAY DETAIL & AGENT GENERATOR
# ==========================================
st.divider()

selected_day = st.session_state.selected_date
st.header(f"🔍 Session Inspection: {selected_day.strftime('%A, %B %d, %Y')}")

col_left, col_right = st.columns([1, 1])

with col_left:
    st.subheader("🤖 Copilot Workout Generation")
    user_prompt = st.text_area(
        "Enter training intent or constraints:",
        value="Generate a heavy upper body push workout focusing on chest strength."
    )
    
    if st.button("Generate & Audit Workout", type="primary"):
        with st.spinner("Collaborating: Planner Agent ↔ Evaluator Agent..."):
            app = build_workflow()
            initial_state = AgentState(
                target_date=selected_day,
                user_prompt=user_prompt
            )
            final_state = app.invoke(initial_state)
            
            # Save into session state
            st.session_state.workout_history[selected_day.isoformat()] = final_state
            st.success("Plan generated and audited!")

with col_right:
    st.subheader("📊 Biomechanical Safety Report")
    
    saved_state = st.session_state.workout_history.get(selected_day.isoformat())
    
    if saved_state:
        workout: DailyWorkout = saved_state.get("proposed_workout")
        audit = saved_state.get("audit_report")
        
        if workout:
            st.markdown(f"**Title:** {workout.title}")
            sets_df = pd.DataFrame([s.model_dump() for s in workout.sets])
            st.dataframe(sets_df, use_container_width=True)
            
        if audit:
            st.divider()
            m1, m2, m3 = st.columns(3)
            m1.metric("ACWR Ratio", f"{audit.acwr_value}", delta=audit.acwr_status)
            m2.metric("Tendon Recovery", "72h OK" if audit.collagen_synthesis_ok else "VIOLATION")
            m3.metric("Push/Pull Ratio", f"{audit.push_pull_ratio}:1")
            
            if audit.approved:
                st.success("✅ AUDIT APPROVED: No critical safety flags.")
            else:
                st.error("❌ AUDIT REJECTED / CAUTION: Safety flags detected.")
                for flag in audit.safety_flags:
                    st.warning(f"**{flag.flag_type}** ({flag.severity}): {flag.message}\n\n👉 *{flag.recommendation}*")
    else:
        st.info("No session generated yet for this date. Enter intent on the left to run the copilot.")