
from datetime import date
from unittest.mock import patch

from app.adapters.zetaris import persist_session_and_loads

with patch("app.adapters.zetaris._execute_usl_query") as mock_query:
    try:
        persist_session_and_loads(
            athlete_id="athlete_001",
            session_date=date.today(),
            session_title="Validation test",
            raw_text="Test workout",
            session_rpe=5.0,
            consensus_loads=[{"body_part_id": 1}],  # Missing stress_score
        )
    except ValueError:
        print("PASS: Invalid load rejected")
    else:
        raise AssertionError("Expected ValueError")

    mock_query.assert_not_called()
    print("PASS: No database writes attempted")
