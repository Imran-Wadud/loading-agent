# app/adapters/meterless.py
import os
from pathlib import Path
from langchain_openai import ChatOpenAI

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
    Primary: OpenAI-compatible client pointed at local Meterless Nightshift proxy (127.0.0.1:8088).
    Fallback: Google Gemini API if Meterless throws 502/404 gateway or connectivity errors.
    """
    # 1. Primary LLM: Local Meterless Proxy
    base_url = os.getenv("METERLESS_BASE_URL", "http://127.0.0.1:8088/v1")
    token = os.getenv("METERLESS_RUNTIME_TOKEN", "mock-token")
    model_name = os.getenv("NEMOTRON_MODEL", "nvidia/nemotron-3.5-lightning-30b-a3b")

    primary_llm = ChatOpenAI(
        base_url=base_url,
        api_key=token,
        model=model_name,
        temperature=0.2,
        timeout=60,
        max_retries=1,
        use_responses_api=False,
        default_headers={
            "meterless-app": "loading-agent",
            "meterless-scope": "user",
        },
    )

    # 2. Fallback LLM: Google Gemini
    gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    gemini_model = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

    if gemini_key:
        fallback_llm = ChatOpenAI(
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            api_key=gemini_key,
            model=gemini_model,
            temperature=0.2,
            timeout=60,
            max_retries=2,
        )
        # Wrap primary LLM with automatic fallback
        return primary_llm.with_fallbacks([fallback_llm])

    return primary_llm