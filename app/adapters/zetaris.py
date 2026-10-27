import logging
import os
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg2
import requests
from psycopg2.extras import RealDictCursor

logger = logging.getLogger("loading_agent.zetaris")

# Load .env from the project root without overriding existing environment values.
_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
if _ENV_PATH.exists():
    for _line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _key, _, _value = _line.partition("=")
        os.environ.setdefault(_key.strip(), _value.strip().strip('"').strip("'"))

# All Zetaris reads use this namespace. Mutations go directly to PostgreSQL.
USL_PREFIX = "lightning.metastore.loading_agent.loading_agent_usl"

DEFAULT_TISSUE_KINETICS = {
    "MUSCLE": {
        "base_recovery_window_hours": 48.0,
        "disuse_threshold_days": 7,
        "decay_multiplier": 0.25,
    },
    "TENDON": {
        "base_recovery_window_hours": 72.0,
        "disuse_threshold_days": 4,
        "decay_multiplier": 0.15,
    },
}


def _get_db_connection():
    """Connect to PostgreSQL using the application's existing DB settings."""
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "5432")),
        dbname=os.getenv("DB_NAME", "postgres"),
        user=os.getenv("DB_USER", os.getenv("POSTGRES_USER", "postgres")),
        password=os.getenv(
            "DB_PASSWORD", os.getenv("POSTGRES_PASSWORD", "postgres")
        ),
        cursor_factory=RealDictCursor,
    )


@contextmanager
def _postgres_transaction():
    """Commit the transaction on success; roll back if an exception occurs."""
    conn = _get_db_connection()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _execute_postgres_write(query: str, params: tuple = None) -> int:
    """Execute a PostgreSQL mutation and return its affected-row count."""
    with _postgres_transaction() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, params or ())
            return cursor.rowcount


def _execute_usl_query(
    query: str,
    params: tuple = None,
    columns: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Execute a read-only query through Zetaris's REST SQL endpoint."""
    statement = query.lstrip().upper()
    if not (statement.startswith("SELECT") or statement.startswith("WITH")):
        raise ValueError(
            "Zetaris access is read-only. Use PostgreSQL for mutations."
        )

    # Zetaris endpoint accepts SQL text, so quote parameter values safely.
    if params:
        safe_params = []
        for value in params:
            if value is None:
                safe_params.append("NULL")
            elif isinstance(value, bool):
                safe_params.append("TRUE" if value else "FALSE")
            elif isinstance(value, (int, float)):
                safe_params.append(str(value))
            elif isinstance(value, datetime):
                safe_params.append(f"TIMESTAMP '{value.isoformat(sep=' ')}'")
            elif isinstance(value, date):
                safe_params.append(f"DATE '{value.isoformat()}'")
            else:
                safe_params.append("'" + str(value).replace("'", "''") + "'")

        parts = query.split("%s")
        if len(parts) != len(safe_params) + 1:
            raise ValueError("SQL parameter count does not match %s placeholders")
        query = parts[0]
        for value, part in zip(safe_params, parts[1:]):
            query += value + part

    base_url = os.getenv(
        "ZETARIS_REST_URL", "http://localhost:8888/api/v1.0"
    ).rstrip("/")
    api_key = os.getenv("ZETARIS_API_KEY", "").strip()
    org_id = os.getenv("ZETARIS_ORG_ID", "1").strip()

    if not api_key:
        raise RuntimeError("ZETARIS_API_KEY is not configured")
    if not org_id.isdigit():
        raise RuntimeError("ZETARIS_ORG_ID must be numeric")

    endpoint = f"{base_url}/sql-editor/sqls/run"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "X-Org-ID": org_id,
        "X-Request-ID": str(uuid.uuid4()),
        "Content-Type": "application/json",
    }
    payload = {
        "queryId": str(uuid.uuid4()),
        "sql": query,
        "source": "SqlEditor",
        "limit": 1000,
    }

    try:
        response = requests.post(
            endpoint, json=payload, headers=headers, timeout=60
        )
        response.raise_for_status()
        result = response.json()
    except requests.exceptions.RequestException as exc:
        logger.error("Zetaris REST query failed at %s: %s", endpoint, exc)
        if exc.response is not None:
            logger.error("Zetaris response body: %s", exc.response.text[:1000])
        raise

    if not result:
        return []
    if isinstance(result, list):
        rows, response_columns = result, None
    elif isinstance(result, dict):
        rows = result.get("data")
        if not isinstance(rows, list):
            return [result]
        response_columns = result.get("columns")
    else:
        return []

    if not rows:
        return []
    if isinstance(rows[0], dict):
        return rows

    column_names = response_columns or columns
    if not column_names:
        raise ValueError(
            "Zetaris returned positional rows without column metadata. "
            "Pass columns=... for this query."
        )

    names = [
        str(
            col.get("name", col.get("columnName", col))
            if isinstance(col, dict)
            else col
        ).split(".")[-1]
        for col in column_names
    ]
    if any(len(row) != len(names) for row in rows):
        raise ValueError(
            f"Zetaris returned {len(names)} columns but row data has a different shape."
        )
    return [dict(zip(names, row)) for row in rows]


def fetch_canonical_body_parts() -> List[Dict[str, Any]]:
    """Fetch canonical body parts from USL."""
    return _execute_usl_query(
        f"""
        SELECT body_part_id, name, tissue_type
        FROM {USL_PREFIX}.body_parts
        ORDER BY body_part_id
        """,
        columns=["body_part_id", "name", "tissue_type"],
    )


def fetch_tissue_kinetics_constants() -> Dict[str, Dict[str, Any]]:
    """Fetch tissue kinetics parameters from USL."""
    columns = [
        "tissue_type",
        "base_recovery_window_hours",
        "disuse_threshold_days",
        "decay_multiplier",
    ]
    rows = _execute_usl_query(
        f"""
        SELECT tissue_type, base_recovery_window_hours,
               disuse_threshold_days, decay_multiplier
        FROM {USL_PREFIX}.tissue_kinetics
        """,
        columns=columns,
    )

    kinetics = {}
    for row in rows:
        tissue_type = str(row["tissue_type"]).upper()
        kinetics[tissue_type] = {
            "base_recovery_window_hours": float(row["base_recovery_window_hours"]),
            "disuse_threshold_days": int(row["disuse_threshold_days"]),
            "decay_multiplier": float(row["decay_multiplier"]),
        }

    if not kinetics:
        logger.warning("No tissue kinetics rows returned by USL; using defaults.")
        return DEFAULT_TISSUE_KINETICS.copy()

    logger.info("Loaded tissue kinetics for %s.", list(kinetics))
    return kinetics


def persist_session_and_loads(
    athlete_id: str,
    session_date: date,
    session_title: str,
    raw_text: str,
    session_rpe: Optional[float],
    consensus_loads: List[Dict[str, Any]],
    status: str = "APPROVED",
    acwr: float = 1.0,
    remediation_suggestion: Optional[str] = None,
) -> str:
    """Persist a session and its tissue loads atomically in PostgreSQL."""
    for item in consensus_loads:
        if "body_part_id" not in item or "stress_score" not in item:
            raise ValueError(
                "Each consensus load must include body_part_id and stress_score"
            )

    session_id = str(uuid.uuid4())
    try:
        with _postgres_transaction() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO public.athlete_sessions (
                        session_id, athlete_id, session_date, session_title,
                        raw_input, session_rpe, status, acwr,
                        remediation_suggestion
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        session_id, athlete_id, session_date, session_title,
                        raw_text, session_rpe, status, acwr,
                        remediation_suggestion,
                    ),
                )
                for item in consensus_loads:
                    cursor.execute(
                        """
                        INSERT INTO public.tissue_loads_logged (
                            session_id, body_part_id, stress_score, volume_mins
                        )
                        VALUES (%s, %s, %s, %s)
                        """,
                        (
                            session_id, item["body_part_id"],
                            item["stress_score"], item.get("volume_mins", 0),
                        ),
                    )
        logger.info(
            "Persisted session %s and %s tissue loads via PostgreSQL.",
            session_id, len(consensus_loads),
        )
        return session_id
    except Exception:
        logger.exception("PostgreSQL error persisting session %s", session_id)
        raise


class ZetarisDBAdapter:
    """Read through Zetaris; perform application mutations through PostgreSQL."""

    def get_canonical_body_parts(self) -> List[Dict[str, Any]]:
        return fetch_canonical_body_parts()

    def get_tissue_kinetics_constants(self) -> Dict[str, Dict[str, Any]]:
        return fetch_tissue_kinetics_constants()

    def get_athlete_profile(self, athlete_id: str) -> Optional[Dict[str, Any]]:
        rows = _execute_usl_query(
            f"""
            SELECT athlete_id, name, experience_level, primary_goal,
                   capacity_scalar, rpe_bias, injury_notes
            FROM {USL_PREFIX}.athletes
            WHERE athlete_id = %s
            """,
            (athlete_id,),
            columns=[
                "athlete_id", "name", "experience_level", "primary_goal",
                "capacity_scalar", "rpe_bias", "injury_notes",
            ],
        )
        if rows:
            return rows[0]
        return {
            "athlete_id": athlete_id,
            "name": athlete_id,
            "experience_level": "INTERMEDIATE",
            "primary_goal": "General Conditioning",
            "capacity_scalar": 1.0,
            "rpe_bias": 0.0,
            "injury_notes": "",
        }

    def load_usl_sessions(self, athlete_id: str) -> List[Dict[str, Any]]:
        return _execute_usl_query(
            f"""
            SELECT session_id, athlete_id, session_date, session_title,
                   session_rpe, raw_input, status, acwr, remediation_suggestion
            FROM {USL_PREFIX}.athlete_sessions
            WHERE athlete_id = %s
            ORDER BY session_date ASC
            """,
            (athlete_id,),
            columns=[
                "session_id", "athlete_id", "session_date", "session_title",
                "session_rpe", "raw_input", "status", "acwr",
                "remediation_suggestion",
            ],
        )

    def load_usl_tissue_loads(self, athlete_id: str) -> List[Dict[str, Any]]:
        return _execute_usl_query(
            f"""
            SELECT s.session_date, s.status, bp.body_part_id,
                   bp.name AS tissue_name, bp.tissue_type, tl.stress_score
            FROM {USL_PREFIX}.athlete_sessions s
            JOIN {USL_PREFIX}.tissue_loads_logged tl
              ON s.session_id = tl.session_id
            JOIN {USL_PREFIX}.body_parts bp
              ON tl.body_part_id = bp.body_part_id
            WHERE s.athlete_id = %s
            ORDER BY s.session_date ASC
            """,
            (athlete_id,),
            columns=[
                "session_date", "status", "body_part_id", "tissue_name",
                "tissue_type", "stress_score",
            ],
        )

    def load_sessions(self, athlete_id: str) -> List[Dict[str, Any]]:
        return self.load_usl_sessions(athlete_id)

    def get_cached_tissue_loads(self, raw_input: str) -> Optional[Dict[str, Any]]:
        if not raw_input or not raw_input.strip():
            return None

        try:
            rows = _execute_usl_query(
                f"""
                WITH latest_session AS (
                    SELECT session_id, session_title, session_date
                    FROM {USL_PREFIX}.athlete_sessions
                    WHERE raw_input = %s
                    ORDER BY session_date DESC
                    LIMIT 1
                )
                SELECT
                    s.session_title,
                    bp.body_part_id,
                    bp.name,
                    bp.tissue_type,
                    tl.stress_score
                FROM latest_session s
                JOIN {USL_PREFIX}.tissue_loads_logged tl
                ON s.session_id = tl.session_id
                JOIN {USL_PREFIX}.body_parts bp
                ON tl.body_part_id = bp.body_part_id
                ORDER BY bp.body_part_id
                """,
                (raw_input.strip(),),
                columns=[
                    "session_title",
                    "body_part_id",
                    "name",
                    "tissue_type",
                    "stress_score",
                ],
            )

            if not rows:
                return None

            return {
                "session_title": rows[0]["session_title"],
                "loads": [
                    {
                        "body_part_id": row["body_part_id"],
                        "name": row["name"],
                        "tissue_type": row["tissue_type"],
                        "stress_score": float(row["stress_score"]),
                    }
                    for row in rows
                ],
            }

        except Exception:
            logger.exception("Zetaris cache lookup failed")
            return None

    def get_session_tissue_loads(self, session_id: str) -> List[Dict[str, Any]]:
        return _execute_usl_query(
            f"""
            SELECT tl.body_part_id, bp.name, bp.tissue_type,
                   tl.stress_score, tl.volume_mins
            FROM {USL_PREFIX}.tissue_loads_logged tl
            JOIN {USL_PREFIX}.body_parts bp
              ON tl.body_part_id = bp.body_part_id
            WHERE tl.session_id = %s
            """,
            (session_id,),
            columns=[
                "body_part_id", "name", "tissue_type",
                "stress_score", "volume_mins",
            ],
        )

    def update_session_remediation(
        self,
        session_id: str,
        status: str,
        remediation_suggestion: str,
    ) -> None:
        affected = _execute_postgres_write(
            """
            UPDATE public.athlete_sessions
            SET status = %s, remediation_suggestion = %s
            WHERE session_id = %s
            """,
            (status, remediation_suggestion, session_id),
        )
        if affected == 0:
            logger.warning("No session found to update: %s", session_id)
        else:
            logger.info(
                "Updated session %s status to %s via PostgreSQL.",
                session_id, status,
            )

    def fetch_historical_daily_loads(
        self, athlete_id: str, target_date: date, days: int = 28
    ) -> Dict[date, float]:
        start_date = target_date - timedelta(days=days - 1)
        rows = _execute_usl_query(
            f"""
            SELECT s.session_date, COALESCE(SUM(tl.stress_score), 0.0) AS daily_load
            FROM {USL_PREFIX}.athlete_sessions s
            JOIN {USL_PREFIX}.tissue_loads_logged tl
              ON s.session_id = tl.session_id
            WHERE s.athlete_id = %s
              AND s.session_date BETWEEN %s AND %s
            GROUP BY s.session_date
            """,
            (athlete_id, start_date, target_date),
            columns=["session_date", "daily_load"],
        )
        return {row["session_date"]: float(row["daily_load"]) for row in rows}

    def get_active_recovery_window(
        self, athlete_id: str, session_date: date
    ) -> List[Dict[str, Any]]:
        start_date = session_date - timedelta(days=3)
        return _execute_usl_query(
            f"""
            SELECT s.session_id, s.session_date, s.session_title,
                   tl.body_part_id, bp.name, bp.tissue_type, tl.stress_score
            FROM {USL_PREFIX}.tissue_loads_logged tl
            JOIN {USL_PREFIX}.athlete_sessions s
              ON tl.session_id = s.session_id
            JOIN {USL_PREFIX}.body_parts bp
              ON tl.body_part_id = bp.body_part_id
            WHERE s.athlete_id = %s
              AND s.session_date BETWEEN %s AND %s
            """,
            (athlete_id, start_date, session_date),
            columns=[
                "session_id", "session_date", "session_title",
                "body_part_id", "name", "tissue_type", "stress_score",
            ],
        )

    def swap_remediated_workout(
        self,
        session_id: str,
        new_title: str,
        remediated_text: str,
        new_loads: List[Dict[str, Any]],
        new_status: str = "REMEDIATED_ACCEPTED",
    ) -> bool:
        try:
            # Validate everything before modifying the database.
            for item in new_loads:
                if "body_part_id" not in item or "stress_score" not in item:
                    raise ValueError(
                        "Each new load must include body_part_id and stress_score"
                    )

            with _postgres_transaction() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE public.athlete_sessions
                        SET session_title = %s,
                            raw_input = %s,
                            status = %s,
                            remediation_suggestion = NULL
                        WHERE session_id = %s
                        """,
                        (new_title, remediated_text, new_status, session_id),
                    )

                    if cursor.rowcount == 0:
                        logger.warning(
                            "Cannot remediate missing session %s", session_id
                        )
                        return False

                    # Replace, rather than append to, the original tissue loads.
                    cursor.execute(
                        """
                        DELETE FROM public.tissue_loads_logged
                        WHERE session_id = %s
                        """,
                        (session_id,),
                    )

                    for item in new_loads:
                        cursor.execute(
                            """
                            INSERT INTO public.tissue_loads_logged (
                                session_id, body_part_id, stress_score, volume_mins
                            )
                            VALUES (%s, %s, %s, %s)
                            """,
                            (
                                session_id,
                                item["body_part_id"],
                                item["stress_score"],
                                item.get("volume_mins", 0),
                            ),
                        )

            logger.info(
                "Applied remediation and replaced tissue loads for session %s.",
                session_id,
            )
            return True

        except Exception:
            logger.exception(
                "Error applying remediation swap for session %s", session_id
            )
            return False

    def save_approved_session(
        self,
        athlete_id: str,
        session_date: date,
        session_title: str,
        raw_text: str,
        session_rpe: Optional[float],
        consensus_loads: List[Dict[str, Any]],
        status: str = "APPROVED",
        acwr: float = 1.0,
        remediation_suggestion: Optional[str] = None,
    ) -> str:
        return persist_session_and_loads(
            athlete_id=athlete_id,
            session_date=session_date,
            session_title=session_title,
            raw_text=raw_text,
            session_rpe=session_rpe,
            consensus_loads=consensus_loads,
            status=status,
            acwr=acwr,
            remediation_suggestion=remediation_suggestion,
        )
