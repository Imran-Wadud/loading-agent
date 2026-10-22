import os
from langchain_openai import ChatOpenAI

def get_llm() -> ChatOpenAI:
    """
    Returns a ChatOpenAI client connected to local Meterless Nightshift.
    Falls back to direct NVIDIA API or local testing if Nightshift token isn't present.
    """
    base_url = os.getenv("METERLESS_BASE_URL", "http://127.0.0.1:8088/v1")
    token = os.getenv("METERLESS_RUNTIME_TOKEN", "mock-token")
    model_name = os.getenv("NEMOTRON_MODEL", "nvidia/nemotron-3.5-lightning-30b-a3b")

    return ChatOpenAI(
        base_url=base_url,
        api_key=token,
        model=model_name,
        temperature=0.2,
        default_headers={
            "meterless-app": "loading-agent",
            "meterless-scope": "user"
        }
    )