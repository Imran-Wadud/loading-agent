# app/ui/dashboard.py
import calendar
from datetime import date
import reflex as rx

from app.ui.state import AppState, CalendarDay


def get_status_badge(day_data) -> rx.Component:
    """Renders temporal status badges using rx.cond for compilation safety."""
    today_str = str(date.today())
    is_past = day_data.date_str < today_str
    has_session = day_data & (day_data.session_title != None) & (day_data.session_title != "")

    return rx.cond(
        has_session,
        rx.cond(
            is_past,
            rx.cond(
                day_data.has_violations,
                rx.badge("LOGGED", color_scheme="amber", size="1", variant="soft"),
                rx.badge("LOGGED", color_scheme="green", size="1", variant="soft"),
            ),
            rx.cond(
                day_data.status == "APPROVED",
                rx.badge("APPROVED", color_scheme="green", size="1", variant="soft"),
                rx.cond(
                    day_data.status == "REMEDIATED",
                    rx.badge("REMEDIATED", color_scheme="amber", size="1", variant="soft"),
                    rx.badge("HIGH RISK", color_scheme="red", size="1", variant="soft"),
                ),
            ),
        ),
        rx.box(),
    )


def header_bar() -> rx.Component:
    return rx.hstack(
        rx.vstack(
            rx.heading("Athletic Workload & Recovery Dashboard", size="6", weight="bold"),
            rx.hstack(
                rx.text("October 2026", weight="bold", color="var(--accent-9)"),
                rx.text("| Biomechanical Consensus & World Model Audit Engine", color="gray"),
                spacing="2",
            ),
            spacing="1",
        ),
        rx.spacer(),
        rx.badge(
            rx.icon(tag="activity", size=16),
            AppState.status_message,
            color_scheme="blue",
            variant="soft",
            padding="0.5rem 0.8rem",
        ),
        width="100%",
        padding_bottom="1.5rem",
        border_bottom="1px solid var(--gray-4)",
    )


def day_tile(d: date) -> rx.Component:
    d_str = str(d)
    is_october = d.month == 10 and d.year == 2026
    day_data = AppState.calendar_data.get(d_str)
    has_session = day_data & (day_data.session_title != None) & (day_data.session_title != "")

    return rx.box(
        rx.vstack(
            rx.hstack(
                rx.text(
                    f"{d.day}",
                    weight="bold",
                    size="2",
                    color=rx.cond(is_october, "var(--gray-12)", "var(--gray-8)"),
                ),
                rx.spacer(),
                rx.cond(
                    has_session & day_data.has_violations,
                    rx.icon(tag="triangle-alert", size=14, color="var(--red-9)"),
                ),
                width="100%",
                align_items="center",
            ),
            rx.box(
                rx.cond(
                    has_session,
                    rx.text(
                        day_data.session_title,
                        size="1",
                        weight="medium",
                        color="var(--gray-11)",
                        style={
                            "display": "-webkit-box",
                            "-webkit-line-clamp": "2",
                            "-webkit-box-orient": "vertical",
                            "overflow": "hidden",
                            "text-overflow": "ellipsis",
                            "word-break": "break-word",
                        },
                    ),
                    rx.text("No session", color="var(--gray-8)", size="1"),
                ),
                width="100%",
                flex="1",
                overflow="hidden",
                margin_y="0.2rem",
            ),
            rx.box(
                get_status_badge(day_data),
                width="100%",
            ),
            height="100%",
            justify="between",
            align_items="start",
            spacing="1",
        ),
        height="110px",
        padding="0.6rem",
        border="1px solid var(--gray-4)",
        border_radius="8px",
        cursor="pointer",
        opacity=rx.cond(is_october, "1.0", "0.45"),
        background_color=rx.cond(
            AppState.selected_date_str == d_str,
            "var(--accent-3)",
            "var(--gray-2)",
        ),
        _hover={"background_color": "var(--gray-3)", "border_color": "var(--accent-8)"},
        on_click=AppState.select_day_for_logging(d_str),
        width="100%",
        overflow="hidden",
    )


def fatigue_summary_bar(week_idx: int) -> rx.Component:
    """Renders overall 7-day segment bar with an expandable scrollable per-tissue fatigue breakdown drawer."""
    week_days = AppState.weekly_daily_fatigue[week_idx]
    week_tissues = AppState.weekly_tissue_fatigue[week_idx]

    return rx.vstack(
        rx.vstack(
            # Header Row
            rx.hstack(
                rx.icon(tag="activity", size=15, color="var(--gray-11)"),
                rx.text(f"Week {week_idx + 1} Cumulative Fatigue Baseline", size="2", weight="medium"),
                rx.spacer(),
                rx.text("Mon - Sun Load Progression", size="2", color="var(--gray-10)"),
                rx.icon(
                    tag=rx.cond(AppState.expanded_week_index == week_idx, "chevron-up", "chevron-down"),
                    size=15,
                    color="var(--gray-10)",
                ),
                width="100%",
                align_items="center",
            ),
            # 7-Segment Dynamic Daily Fatigue Bar
            rx.grid(
                rx.foreach(
                    week_days,
                    lambda day: rx.tooltip(
                        rx.box(
                            height="6px",
                            background_color=day["color"],
                            border_radius="3px",
                            transition="background-color 0.3s ease",
                            width="100%",
                        ),
                        content=day["tooltip_text"],
                    ),
                ),
                columns="7",
                spacing="1",
                width="100%",
                margin_top="0.4rem",
            ),
            width="100%",
            padding="0.6rem 0.9rem",
            background_color="var(--gray-2)",
            border="1px solid var(--gray-4)",
            border_radius="6px",
            cursor="pointer",
            _hover={"border_color": "var(--accent-8)", "background_color": "var(--gray-3)"},
            on_click=AppState.toggle_week_fatigue(week_idx),
        ),
        # Expandable Biomechanical Tissue Fatigue Drawer (Scrollable)
        rx.cond(
            AppState.expanded_week_index == week_idx,
            rx.box(
                rx.vstack(
                    rx.hstack(
                        rx.text("Active Biomechanical Tissue Recovery", weight="bold", size="2"),
                        rx.spacer(),
                        rx.text("Individual Mon-Sun Fatigue Progression", size="1", color="var(--gray-10)"),
                        width="100%",
                        align_items="center",
                    ),
                    rx.cond(
                        week_tissues.length() == 0,
                        rx.text("All tissues fully rested. No active tissue recovery lockouts for this week.", size="2", color="var(--gray-10)"),
                        rx.box(
                            rx.vstack(
                                rx.foreach(
                                    week_tissues,
                                    lambda t: rx.hstack(
                                        rx.hstack(
                                            rx.text(t.tissue_name, weight="bold", size="2", style={"minWidth": "140px"}),
                                            rx.badge(t.tissue_type, size="1", variant="soft", color_scheme="gray"),
                                            spacing="2",
                                            align_items="center",
                                            width="210px",
                                        ),
                                        rx.grid(
                                            rx.foreach(
                                                t.days,
                                                lambda d: rx.tooltip(
                                                    rx.box(
                                                        height="8px",
                                                        background_color=d.color,
                                                        border_radius="3px",
                                                        width="100%",
                                                    ),
                                                    content=d.tooltip_text,
                                                ),
                                            ),
                                            columns="7",
                                            spacing="1",
                                            flex="1",
                                        ),
                                        width="100%",
                                        align_items="center",
                                        spacing="3",
                                    ),
                                ),
                                spacing="2",
                                width="100%",
                            ),
                            max_height="180px",
                            overflow_y="auto",
                            padding_right="0.5rem",
                            width="100%",
                        ),
                    ),
                    spacing="3",
                    padding="0.8rem 1rem",
                ),
                background_color="var(--gray-3)",
                border_x="1px solid var(--gray-4)",
                border_b="1px solid var(--gray-4)",
                border_radius="0 0 6px 6px",
                width="100%",
                margin_top="-0.4rem",
            ),
        ),
        width="100%",
        margin_y="0.3rem",
    )


def calendar_grid_view() -> rx.Component:
    days_header = rx.grid(
        *[
            rx.center(rx.text(day, weight="bold", size="2", color="gray"))
            for day in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        ],
        columns="7",
        width="100%",
        padding_y="0.5rem",
    )

    cal = calendar.Calendar(firstweekday=0)
    month_weeks = cal.monthdatescalendar(2026, 10)

    weeks_components = []
    for week_idx, week_dates in enumerate(month_weeks):
        week_tiles = [day_tile(d) for d in week_dates]
        week_grid = rx.grid(*week_tiles, columns="7", spacing="2", width="100%")
        weeks_components.extend([week_grid, fatigue_summary_bar(week_idx)])

    return rx.vstack(
        days_header,
        *weeks_components,
        width="100%",
        spacing="3",
    )


def workout_logger_view() -> rx.Component:
    selected_day = AppState.calendar_data.get(AppState.selected_date_str)
    has_session = selected_day & (selected_day.session_title != None) & (selected_day.session_title != "")

    left_form_card = rx.card(
        rx.vstack(
            rx.hstack(
                rx.icon(tag="calendar-plus", size=20),
                rx.heading("Log / Plan Training Session", size="4"),
                spacing="2",
            ),
            rx.divider(),
            rx.vstack(
                rx.text("Athlete ID", weight="bold", size="2"),
                rx.input(value=AppState.athlete_id, on_change=AppState.set_athlete_id, width="100%"),
                width="100%",
            ),
            rx.vstack(
                rx.text("Session RPE (1.0 - 10.0)", weight="bold", size="2"),
                rx.slider(
                    value=[AppState.session_rpe_input],
                    min=1.0,
                    max=10.0,
                    step=0.5,
                    on_change=AppState.set_session_rpe_slider,
                    width="100%",
                ),
                rx.text(f"Selected RPE: {AppState.session_rpe_input}", color="gray", size="2"),
                width="100%",
            ),
            rx.vstack(
                rx.text("Workout Description", weight="bold", size="2"),
                rx.text_area(
                    value=AppState.workout_description_input,
                    on_change=AppState.set_workout_description_input,
                    rows="5",
                    width="100%",
                ),
                width="100%",
            ),
            rx.button(
                rx.icon(tag="zap", size=16),
                "Submit & Run Agent Evaluation",
                color_scheme="blue",
                width="100%",
                loading=AppState.is_evaluating,
                on_click=AppState.submit_workout_pipeline,
            ),
            spacing="4",
            width="100%",
        ),
        width="100%",
        padding="1.5rem",
    )

    right_details_card = rx.card(
        rx.vstack(
            rx.hstack(
                rx.icon(tag="file-text", size=20),
                rx.heading("Session Audit & Details", size="4"),
                rx.spacer(),
                get_status_badge(selected_day),
                width="100%",
            ),
            rx.divider(),
            rx.cond(
                has_session,
                rx.vstack(
                    rx.hstack(
                        rx.text("Title:", weight="bold", size="2"),
                        rx.text(selected_day.session_title, size="2"),
                        spacing="2",
                    ),
                    rx.hstack(
                        rx.text("Calculated 28-day ACWR:", weight="bold", size="2"),
                        rx.badge(f"{selected_day.acwr}", color_scheme="blue"),
                        spacing="2",
                    ),
                    rx.divider(),
                    rx.heading("Biomechanical Audit Flags", size="3"),
                    rx.cond(
                        selected_day.has_violations,
                        rx.vstack(
                            rx.foreach(
                                selected_day.audit_flags,
                                lambda flag: rx.callout(
                                    flag,
                                    icon="triangle-alert",
                                    color_scheme="red",
                                    width="100%",
                                ),
                            ),
                            width="100%",
                        ),
                        rx.callout(
                            "No tissue lockouts or ACWR safety violations detected.",
                            icon="check-circle",
                            color_scheme="green",
                            width="100%",
                        ),
                    ),
                    rx.cond(
                        selected_day.remediation_suggestion,
                        rx.vstack(
                            rx.heading("Agent Remediation Plan", size="3"),
                            rx.box(
                                rx.markdown(selected_day.remediation_suggestion),
                                padding="1rem",
                                background_color="var(--gray-3)",
                                border_radius="6px",
                                width="100%",
                            ),
                            width="100%",
                            spacing="2",
                        ),
                    ),
                    spacing="3",
                    width="100%",
                ),
                rx.callout(
                    "No workout evaluated for this date yet. Submit a session on the left to trigger the consensus and world model evaluation.",
                    icon="info",
                    color_scheme="blue",
                    width="100%",
                ),
            ),
            spacing="4",
            width="100%",
        ),
        width="100%",
        padding="1.5rem",
    )

    return rx.vstack(
        rx.hstack(
            rx.button(
                rx.icon(tag="arrow-left", size=16),
                "Back to Calendar",
                variant="soft",
                on_click=AppState.nav_to_calendar,
            ),
            rx.heading(f"Date Selected: {AppState.selected_date_str}", size="4"),
            spacing="3",
            width="100%",
            margin_bottom="1rem",
        ),
        rx.grid(
            left_form_card,
            right_details_card,
            columns="2",
            spacing="4",
            width="100%",
        ),
        width="100%",
    )


def index() -> rx.Component:
    return rx.container(
        rx.vstack(
            header_bar(),
            rx.cond(
                AppState.current_view == "calendar",
                calendar_grid_view(),
                workout_logger_view(),
            ),
            spacing="4",
            width="100%",
        ),
        size="4",
        padding="2rem",
    )


app = rx.App()
app.add_page(
    index,
    title="Athletic Workload & Recovery Engine",
    on_load=AppState.load_existing_sessions,
)