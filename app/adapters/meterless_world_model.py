"""Small HTTP bridge from Loading Agent (Python) to the Meterless TS World Model.

The TypeScript service owns Meterless World Model writes. This module does not
write directly to the PostgreSQL tables or route writes through read-only USL.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

logger = logging.getLogger("loading_agent.adapters.meterless_world_model")


def persist_workload_assessment(
    *,
    athlete_id: str,
    athlete_name: str,
    session_id: str,
    session_name: str | None,
    session_date: str | None,
    assessment: Mapping[str, Any],
) -> dict[str, Any]:
    """Persist a completed existing simulation through the Meterless World Model.

    If METERLESS_WORLD_MODEL_REQUIRED=true, unavailable/error responses raise so
    a demo cannot silently claim success without the sponsor engine. Otherwise,
    failures are logged and the existing workload flow can continue during dev.
    """
    if os.getenv("METERLESS_WORLD_MODEL_ENABLED", "true").strip().lower() != "true":
        return {"persisted": False, "disabled": True}

    base_url = os.getenv("METERLESS_WORLD_MODEL_URL", "http://127.0.0.1:8090").rstrip("/")
    timeout = float(os.getenv("METERLESS_WORLD_MODEL_TIMEOUT_SECONDS", "2.5"))
    required = os.getenv("METERLESS_WORLD_MODEL_REQUIRED", "false").strip().lower() == "true"
    run_id = str(uuid4())
    at = datetime.now(timezone.utc).isoformat()
    payload = {
        "runId": run_id,
        "at": at,
        "athlete": {"id": str(athlete_id), "name": str(athlete_name)},
        "session": {
            "id": str(session_id),
            "name": str(session_name or f"Session {session_id}"),
            "date": session_date,
        },
        "assessment": dict(assessment),
    }
    request = Request(
        f"{base_url}/assessments",
        data=json.dumps(payload, default=str).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
        if not isinstance(result, dict) or result.get("persisted") is not True:
            raise RuntimeError("Meterless World Model returned no persisted=true acknowledgement")
        logger.info(
            "Meterless World Model persisted assessment: run_id=%s namespace=%s observation=%s",
            result.get("run_id"), result.get("namespace"), result.get("observation_external_id"),
        )
        return result
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
        logger.warning(
            "Meterless World Model persistence failed (required=%s): %s",
            required, type(exc).__name__,
        )
        if required:
            raise RuntimeError(
                "Meterless World Model persistence is required but the service write failed. "
                "Check the Meterless World Model service and its logs."
            ) from exc
        return {"persisted": False, "error": type(exc).__name__}
