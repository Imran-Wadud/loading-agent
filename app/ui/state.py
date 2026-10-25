# app/ui/state.py
import asyncio
from datetime import date, datetime, timedelta
from typing import Dict, List, Any, Optional
from pydantic import BaseModel
import reflex as rx

from app.graph import workload_graph
from app.adapters.zetaris import get_db_connection, ZetarisDBAdapter


class CalendarDay(BaseModel):
    date_str: str
    day_number: int
    is_current_month: bool
    session_title: Optional[str] = None
    session_id: Optional[str] = None
    status: Optional[str] = None  # "APPROVED", "REMEDIATED", "HIGH RISK", "LOGGED", "PROPOSED_RISK_WARNING"
    has_violations: bool = False
    audit_flags: List[str] = []
    acwr: float = 1.0
    remediation_suggestion: Optional[str] = None
    raw_input: Optional[str] = None


class TissueDayStatus(BaseModel):
    day_name: str
    date_str: str
    color: str
    status_text: str
    tooltip_text: str


class TissueFatigue(BaseModel):
    body_part_id: int
    tissue_name: str
    tissue_type: str
    days: List[TissueDayStatus]


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

    # Logged Tissue Loads for dynamic per-tissue fatigue bars
    logged_tissue_loads: List[Dict[str, Any]] = []

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
        """Hydrates calendar_data and logged_tissue_loads via Zetaris USL DB Adapter."""
        try:
            db_adapter = ZetarisDBAdapter()
            rows = db_adapter.load_usl_sessions(self.athlete_id)

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

            # Hydrate saved USL database records
            for row in rows:
                raw_s_date = row.get("session_date")
                s_id_val = str(row.get("session_id")) if row.get("session_id") else None
                title = row.get("session_title") or "Logged Session"
                status_val = row.get("status") or "APPROVED"
                raw_in = row.get("raw_input")
                acwr_val = row.get("acwr") or 1.0
                remediation = row.get("remediation_suggestion")

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

                self.calendar_data[d_str] = CalendarDay(
                    date_str=d_str,
                    day_number=day_num,
                    is_current_month=(month_num == 10 and year_num == 2026),
                    session_title=title,
                    session_id=s_id_val,
                    status=status_val,
                    has_violations=has_viol,
                    audit_flags=[f"Status: {status_val}"] if has_viol else [],
                    acwr=float(acwr_val if acwr_val is not None else 1.0),
                    remediation_suggestion=remediation,
                    raw_input=raw_in,
                )

            # Hydrate per-tissue fatigue breakdown via USL adapter
            t_rows = db_adapter.load_usl_tissue_loads(self.athlete_id)

            parsed_tissue_loads = []
            for r in t_rows:
                r_date = r["session_date"]
                s_date_str = r_date.strftime("%Y-%m-%d") if isinstance(r_date, (date, datetime)) else str(r_date).split(" ")[0].split("T")[0]
                parsed_tissue_loads.append({
                    "session_date": s_date_str,
                    "body_part_id": int(r["body_part_id"]),
                    "tissue_name": str(r["tissue_name"]),
                    "tissue_type": str(r["tissue_type"]),
                    "stress_score": float(r.get("stress_score") or 5.0),
                    "status": str(r.get("status") or "APPROVED"),
                })
            self.logged_tissue_loads = parsed_tissue_loads

            self.status_message = f"Loaded {len(rows)} sessions via Zetaris USL."
        except Exception as e:
            self.status_message = f"DB Load Warning: {str(e)}"

    def accept_remediation_plan(self, session_id: str):
        """Swaps a flagged workout with its suggested alternative routine."""
        db = ZetarisDBAdapter()
        
        # Locate target day in calendar_data matching session_id or selected_date_str
        target_day = None
        for day in self.calendar_data.values():
            if day.session_id and str(day.session_id) == str(session_id):
                target_day = day
                break

        if not target_day and self.selected_date_str in self.calendar_data:
            target_day = self.calendar_data[self.selected_date_str]

        if not target_day:
            return

        s_id = target_day.session_id or session_id
        remediation_text = target_day.remediation_suggestion or ""
        clean_title = (
            (target_day.session_title or "Workout")
            .replace("[RISK]", "")
            .replace("[ADVISORY WARNING]", "")
            .replace("[HIGH STRAIN]", "")
            .strip()
        )
        new_title = f"[ADOPTED] {clean_title}"

        # Execute DB update
        success = db.swap_remediated_workout(
            session_id=s_id,
            new_title=new_title,
            remediated_text=f"Accepted Remediation Plan:\n{remediation_text}",
            new_status="REMEDIATED_ACCEPTED"
        )

        if success:
            self.load_existing_sessions()
    
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

        await loop.run_in_executor(None, run_graph)

        self.load_existing_sessions()
        self.is_evaluating = False
        self.status_message = "Pipeline execution complete. Calendar synchronized."

    @rx.var
    def weekly_daily_fatigue(self) -> List[List[Dict[str, Any]]]:
        """Dynamically computes overall day-by-day tissue status (Green, Yellow, Red)."""
        import calendar

        cal = calendar.Calendar(firstweekday=0)
        month_weeks = cal.monthdatescalendar(2026, 10)
        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        all_weeks = []

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

                is_risk = False
                if has_active_session:
                    if session.has_violations or session.status in ("HIGH RISK", "REMEDIATED", "REJECTED_SAFETY_VIOLATION", "PROPOSED_RISK_WARNING"):
                        is_risk = True

                is_recovering = False
                if not is_risk:
                    for days_back in range(0, 4):
                        lookback_d = d - timedelta(days=days_back)
                        if lookback_d in active_session_dates:
                            is_recovering = True
                            break

                if is_risk:
                    color = "#ef4444"
                    status_text = "Risk / Violation Flagged"
                elif is_recovering:
                    color = "#f59e0b"
                    status_text = "Active Tissue Recovery"
                else:
                    color = "#22c55e"
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

    @rx.var
    def weekly_tissue_fatigue(self) -> List[List[TissueFatigue]]:
        """Calculates per-tissue fatigue bars across each week (Mon-Sun)."""
        import calendar

        cal = calendar.Calendar(firstweekday=0)
        month_weeks = cal.monthdatescalendar(2026, 10)
        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

        tissues_map = {}
        for load in self.logged_tissue_loads:
            b_id = load["body_part_id"]
            if b_id not in tissues_map:
                tissues_map[b_id] = {
                    "tissue_name": load["tissue_name"],
                    "tissue_type": load["tissue_type"],
                    "loads": {}
                }
            tissues_map[b_id]["loads"][load["session_date"]] = load

        all_weeks_tissues = []

        for week_dates in month_weeks:
            week_tissue_list = []

            for b_id, t_info in tissues_map.items():
                t_name = t_info["tissue_name"]
                t_type = t_info["tissue_type"]
                loads_dict = t_info["loads"]

                window_days = 3 if t_type == "TENDON" else 2
                days_status = []
                has_active_day_in_week = False

                for idx, d in enumerate(week_dates):
                    d_str = str(d)

                    worked_today = d_str in loads_dict

                    worked_prior = False
                    prior_date_str = None
                    for lookback in range(1, window_days + 1):
                        past_d_str = str(d - timedelta(days=lookback))
                        if past_d_str in loads_dict:
                            worked_prior = True
                            prior_date_str = past_d_str
                            break

                    is_risk = False
                    if worked_today and (worked_prior or (d_str in self.calendar_data and self.calendar_data[d_str].has_violations)):
                        is_risk = True

                    if is_risk:
                        color = "#ef4444"
                        status_text = f"{day_names[idx]}: {t_name} Lockout Collision"
                        has_active_day_in_week = True
                    elif worked_today:
                        color = "#f59e0b"
                        status_text = f"{day_names[idx]}: {t_name} Worked Today"
                        has_active_day_in_week = True
                    elif worked_prior:
                        color = "#f59e0b"
                        status_text = f"{day_names[idx]}: {t_name} Recovering (Worked {prior_date_str})"
                        has_active_day_in_week = True
                    else:
                        color = "#22c55e"
                        status_text = f"{day_names[idx]}: {t_name} Rested"

                    days_status.append(
                        TissueDayStatus(
                            day_name=day_names[idx],
                            date_str=d_str,
                            color=color,
                            status_text=status_text,
                            tooltip_text=status_text,
                        )
                    )

                if has_active_day_in_week:
                    week_tissue_list.append(
                        TissueFatigue(
                            body_part_id=b_id,
                            tissue_name=t_name,
                            tissue_type=t_type,
                            days=days_status,
                        )
                    )

            all_weeks_tissues.append(week_tissue_list)

        return all_weeks_tissues