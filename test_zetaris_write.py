
from datetime import date
from uuid import uuid4

from app.adapters.zetaris import DB_PREFIX, _execute_usl_query

session_id = str(uuid4())
athlete_id = "athlete_001"

print(f"Testing Zetaris writes via {DB_PREFIX}")
print(f"Temporary session: {session_id}")

try:
    # 1. INSERT a uniquely identified test session.
    result = _execute_usl_query(
        f"""
        INSERT INTO {DB_PREFIX}.athlete_sessions (
            session_id, athlete_id, session_date, session_title,
            raw_input, session_rpe, status, acwr,
            remediation_suggestion
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            session_id,
            athlete_id,
            date.today(),
            "Zetaris adapter write test",
            "Temporary test record - safe to delete",
            1.0,
            "TEST_INSERTED",
            1.0,
            None,
        ),
    )
    print("INSERT response:", result)

    # 2. Verify the inserted row is readable.
    rows = _execute_usl_query(
        f"""
        SELECT session_id, status
        FROM {DB_PREFIX}.athlete_sessions
        WHERE session_id = %s
        """,
        (session_id,),
        columns=["session_id", "status"],
    )
    assert rows, "INSERT returned, but the row was not found on SELECT."
    assert rows[0]["status"] == "TEST_INSERTED", rows
    print("PASS: INSERT + SELECT")

    # 3. UPDATE the test row.
    result = _execute_usl_query(
        f"""
        UPDATE {DB_PREFIX}.athlete_sessions
        SET status = %s
        WHERE session_id = %s
        """,
        ("TEST_UPDATED", session_id),
    )
    print("UPDATE response:", result)

    # 4. Verify the update.
    rows = _execute_usl_query(
        f"""
        SELECT status
        FROM {DB_PREFIX}.athlete_sessions
        WHERE session_id = %s
        """,
        (session_id,),
        columns=["status"],
    )
    assert rows and rows[0]["status"] == "TEST_UPDATED", rows
    print("PASS: UPDATE + SELECT")

finally:
    # 5. Delete ONLY the uniquely identified test row.
    try:
        result = _execute_usl_query(
            f"""
            DELETE FROM {DB_PREFIX}.athlete_sessions
            WHERE session_id = %s
            """,
            (session_id,),
        )
        print("DELETE response:", result)

        remaining = _execute_usl_query(
            f"""
            SELECT session_id
            FROM {DB_PREFIX}.athlete_sessions
            WHERE session_id = %s
            """,
            (session_id,),
            columns=["session_id"],
        )
        assert not remaining, (
            "Cleanup failed: test row still exists. "
            f"Please delete session {session_id} manually."
        )
        print("PASS: DELETE + SELECT confirms cleanup")

    except Exception as exc:
        print(f"WARNING: Automatic cleanup failed: {exc}")
        print(f"Check for test session ID: {session_id}")
        raise
