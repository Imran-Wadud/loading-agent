# app/tracing.py
import json
import logging
from datetime import datetime
from typing import Dict, Any

# Configure structured logging format
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    handlers=[logging.StreamHandler()]
)

logger = logging.getLogger("loading.agent")

def log_agent_step(agent_name: str, action: str, details: Dict[str, Any]) -> None:
    """
    Logs structured telemetry for agent transitions, evaluation triggers,
    and tool executions.
    """
    log_payload = {
        "timestamp": datetime.utcnow().isoformat(),
        "agent": agent_name,
        "action": action,
        "details": details
    }
    logger.info(f"TRACE_EVENT: {json.dumps(log_payload, default=str)}")

def log_audit_verdict(approved: bool, flags_count: int, acwr: float) -> None:
    """
    Logs scientific verification metrics from the Evaluator Agent.
    """
    status = "APPROVED" if approved else "REJECTED_SAFETY_VIOLATION"
    logger.info(
        f"AUDIT_SUMMARY: Verdict={status} | ActiveFlags={flags_count} | CurrentACWR={acwr}"
    )