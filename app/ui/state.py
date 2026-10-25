# app/ui/state.py
import asyncio
from datetime import date, datetime
from typing import Dict, List, Any, Optional
from pydantic import BaseModel
import reflex as rx

from app.graph import workload_graph
from app.adapters.zetaris import get_db_connection


class CalendarDay(BaseModel):
    date_str: str
    day_number: int
    is_current_month: bool
    session_title: Optional[str] = None
    status: Optional[str] = None  # "APPROVED", "REMEDIATED", "HIGH RISK", "LOGGED"
    has_violations: bool = False
    audit_flags: List[str] = []
    acwr: float = 1.0
    remediation_suggestion: Optional[str] = None
    raw_input: Optional[str] = None


class AppState(rx.State):
    """Main Reflex Application State (UI Presenter Layer)."""

    # Navigation & View Control
    current_view: str = "calendar"  # "calendar" | "logger"
    selected_date_str: str = str(date.today())

    # Form Inputs
    session_title_input: str = ""
    workout_description_input: str = (
        "Upper body hypertrophy: Dumbbell Bench Press 4x10, Lat Pulldowns 4x12, Bicep Curls 3x15"
    )
    session_rpe_input: float = 8.5
    athlete_id: str = "athlete_001"

    # Processing State
    is_evaluating: bool = False
    status_message: str = "System Ready"

    # Expanded Fatigue Bar Index (-1 = closed)
    expanded_week_index: int = -1

    # Calendar Storage (Date string -> CalendarDay)
    calendar_data: Dict[str, CalendarDay] = {}

    def set_athlete_id(self, val: str):
        self.athlete_id = val

    def set_workout_description_input(self, val: str):
        self.workout_description_input = val

    def set_session_rpe_slider(self, val: Any):
        if isinstance(val, list) and len(val) > 0:
            self.session_rpe_input = float(val[0])
        elif isinstance(val, (int, float)):
            self.session_rpe_input = float(val)

    def select_day_for_logging(self, date_str: str):
        """Navigate to workout logger/detail split view for selected date."""
        self.selected_date_str = date_str
        self.current_view = "logger"

        if date_str in self.calendar_data:
            existing = self.calendar_data[date_str]
            if existing.raw_input:
                self.workout_description_input = existing.raw_input

    def nav_to_calendar(self):
        """Return to main calendar view."""
        self.current_view = "calendar"

    def toggle_week_fatigue(self, week_idx: int):
        """Expand or collapse weekly fatigue breakdown drawer."""
        if self.expanded_week_index == week_idx:
            self.expanded_week_index = -1
        else:
            self.expanded_week_index = week_idx

    def load_existing_sessions(self):
        """Hydrates calendar_data from Zetaris PostgreSQL on app mount or after graph execution."""
        try:
            db = get_db_connection()
            query = """
                SELECT session_id, athlete_id, session_date, session_title, 
                       session_rpe, raw_input, status, acwr, remediation_suggestion
                FROM athlete_sessions
                WHERE athlete_id = %s
                ORDER BY session_date ASC;
            """
            
            if hasattr(db, "fetch_all"):
                rows = db.fetch_all(query, (self.athlete_id,))
            else:
                from psycopg2.extras import RealDictCursor
                with db.cursor(cursor_factory=RealDictCursor) as cursor:
                    cursor.execute(query, (self.athlete_id,))
                    rows = cursor.fetchall()

            # Initialize base grid structure for October 2026
            import calendar
            cal = calendar.Calendar(firstweekday=0)
            month_weeks = cal.monthdatescalendar(2026, 10)
            for week in month_weeks:
                for d in week:
                    d_str = str(d)
                    if d_str not in self.calendar_data:
                        self.calendar_data[d_str] = CalendarDay(
                            date_str=d_str,
                            day_number=d.day,
                            is_current_month=(d.month == 10 and d.year == 2026),
                        )

            # Hydrate saved database records
            for row in rows:
                raw_s_date = row.get("session_date")
                title = row.get("session_title") or "Logged Session"
                status_val = row.get("status") or "APPROVED"
                raw_in = row.get("raw_input")
                acwr_val = row.get("acwr") or 1.0
                remediation = row.get("remediation_suggestion")

                # Standardize date to YYYY-MM-DD string
                if isinstance(raw_s_date, (date, datetime)):
                    d_str = raw_s_date.strftime("%Y-%m-%d")
                    day_num = raw_s_date.day
                    month_num = raw_s_date.month
                    year_num = raw_s_date.year
                else:
                    d_str = str(raw_s_date).split(" ")[0].split("T")[0]
                    parsed_d = date.fromisoformat(d_str)
                    day_num = parsed_d.day
                    month_num = parsed_d.month
                    year_num = parsed_d.year

                has_viol = status_val not in ("APPROVED", "LOGGED")

                day_entry = CalendarDay(
                    date_str=d_str,
                    day_number=day_num,
                    is_current_month=(month_num == 10 and year_num == 2026),
                    session_title=title,
                    status=status_val,
                    has_violations=has_viol,
                    audit_flags=[f"Status: {status_val}"] if has_viol else [],
                    acwr=float(acwr_val if acwr_val is not None else 1.0),
                    remediation_suggestion=remediation,
                    raw_input=raw_in,
                )
                
                self.calendar_data[d_str] = day_entry

            self.status_message = f"Loaded {len(rows)} sessions from database."
        except Exception as e:
            self.status_message = f"DB Load Warning: {str(e)}"

    async def submit_workout_pipeline(self):
        """Delegates evaluation & downstream cascade strictly to LangGraph agent and refreshes UI state."""
        self.is_evaluating = True
        self.status_message = "Running consensus, world model audit, and downstream cascade..."

        yield

        target_date = date.fromisoformat(self.selected_date_str)
        initial_state = {
            "athlete_id": self.athlete_id,
            "session_date": target_date,
            "raw_input": self.workout_description_input,
            "session_rpe": self.session_rpe_input,
            "remediation_attempts": 0,
        }

        loop = asyncio.get_event_loop()

        def run_graph():
            res = {}
            for event in workload_graph.stream(initial_state):
                for _, output in event.items():
                    if isinstance(output, dict):
                        res.update(output)
            return res

        # Run complete graph (consensus -> audit -> commit -> cascade_audit)
        await loop.run_in_executor(None, run_graph)

        # Hydrate UI directly from updated PostgreSQL records
        self.load_existing_sessions()
        self.is_evaluating = False
        self.status_message = "Pipeline execution complete. Calendar synchronized."

    @rx.var
    def weekly_daily_fatigue(self) -> List[List[Dict[str, Any]]]:
        """Dynamically computes day-by-day tissue status (Green: Rested, Yellow: Recovering, Red: Risk)."""
        import calendar
        from datetime import date, timedelta

        cal = calendar.Calendar(firstweekday=0)
        month_weeks = cal.monthdatescalendar(2026, 10)

        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        all_weeks = []

        # Index all dates that have active sessions recorded
        active_session_dates = set()
        for d_str, day_data in self.calendar_data.items():
            if day_data.session_title and day_data.session_title != "No session":
                try:
                    active_session_dates.add(date.fromisoformat(d_str))
                except ValueError:
                    pass

        for week_dates in month_weeks:
            week_days = []
            for idx, d in enumerate(week_dates):
                d_str = str(d)
                session = self.calendar_data.get(d_str)

                has_active_session = session and session.session_title and session.session_title != "No session"

                # 1. RED: Biomechanical Risk / Safety Violation on this day
                is_risk = False
                if has_active_session:
                    if session.has_violations or session.status in ("HIGH RISK", "REMEDIATED", "REJECTED_SAFETY_VIOLATION"):
                        is_risk = True

                # 2. YELLOW: Active Tissue Recovery Window (Workout today OR within 1-3 days after a prior workout)
                is_recovering = False
                if not is_risk:
                    for days_back in range(0, 4):
                        lookback_d = d - timedelta(days=days_back)
                        if lookback_d in active_session_dates:
                            is_recovering = True
                            break

                # Assign color badge and tooltip label
                if is_risk:
                    color = "#ef4444"  # Red: High Risk / Lockout Collision
                    status_text = "Risk / Violation Flagged"
                elif is_recovering:
                    color = "#f59e0b"  # Yellow: Active Tissue Remodeling / Recovery Window
                    status_text = "Active Tissue Recovery"
                else:
                    color = "#22c55e"  # Green: Fully Rested & Clear
                    status_text = "Completely Rested"

                week_days.append({
                    "day_name": day_names[idx],
                    "date_str": d_str,
                    "color": color,
                    "status_text": status_text,
                    "tooltip_text": f"{day_names[idx]} ({d_str}): {status_text}",
                })
            all_weeks.append(week_days)

        return all_weeks