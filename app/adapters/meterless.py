# app/adapters/meterless.py
import os
import logging
from pathlib import Path
from langchain_openai import ChatOpenAI

logger = logging.getLogger("loading_agent.adapters.meterless")

# Load .env from project root (2 levels up from app/adapters/)
_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
if _ENV_PATH.exists():
    for _line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _key, _, _value = _line.partition("=")
        os.environ.setdefault(_key.strip(), _value.strip().strip('"').strip("'"))


def get_meterless_llm():
    """
    Returns ChatOpenAI instance with primary & fallback configured.
    Toggle USE_GEMINI_PRIMARY=true in .env for ultra-fast dev/testing.
    """
    use_gemini_primary = os.getenv("USE_GEMINI_PRIMARY", "true").lower() == "true"

    # 1. Meterless / Nemotron Client
    nemotron_llm = ChatOpenAI(
        base_url=os.getenv("METERLESS_BASE_URL", "http://127.0.0.1:8088/v1"),
        api_key=os.getenv("METERLESS_RUNTIME_TOKEN", "mock-token"),
        model=os.getenv("NEMOTRON_MODEL", "nvidia/nemotron-3.5-lightning-30b-a3b"),
        temperature=0.2,
        timeout=60,
        max_retries=1,
        use_responses_api=False,
        default_headers={
            "meterless-app": "loading-agent",
            "meterless-scope": "user",
        },
    )

    # 2. Direct Gemini Client
    gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    gemini_llm = None
    if gemini_key:
        gemini_llm = ChatOpenAI(
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            api_key=gemini_key,
            model=os.getenv("GEMINI_MODEL", "gemini-1.5-flash"),
            temperature=0.2,
            timeout=30,
            max_retries=2,
        )

    # Fast Mode: Use Gemini first, Nemotron fallback
    if use_gemini_primary and gemini_llm:
        logger.info("⚡ Fast Mode Active: Using Gemini as primary LLM.")
        return gemini_llm.with_fallbacks([nemotron_llm])

    # Default / Submission Mode: Use Nemotron first, Gemini fallback
    if gemini_llm:
        return nemotron_llm.with_fallbacks([gemini_llm])

    return nemotron_llm