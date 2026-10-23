import os
from pathlib import Path

from langchain_openai import ChatOpenAI

_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
if _ENV_PATH.exists():
    for _line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _key, _, _value = _line.partition("=")
        os.environ.setdefault(_key.strip(), _value.strip().strip('"').strip("'"))


def get_llm() -> ChatOpenAI:
    """
    OpenAI-compatible client pointed at local Meterless Nightshift (127.0.0.1:8088).
    """
    base_url = os.getenv("METERLESS_BASE_URL", "http://127.0.0.1:8088/v1")
    token = os.getenv("METERLESS_RUNTIME_TOKEN", "mock-token")
    model_name = os.getenv("NEMOTRON_MODEL", "nvidia/nemotron-3.5-lightning-30b-a3b")

    return ChatOpenAI(
        base_url=base_url,
        api_key=token,
        model=model_name,
        temperature=0.2,
        timeout=180,
        max_retries=1,
        use_responses_api=False,
        default_headers={
            "meterless-app": "loading-agent",
            "meterless-scope": "user",
        },
    )
