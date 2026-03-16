"""
llm_client.py — thin provider abstraction.

Currently wraps the Anthropic SDK. To switch providers (OpenAI, Gemini,
local Ollama, etc.), replace only this file. The rest of the codebase
calls complete() and never imports anthropic directly.

Configuration via environment variables:
  LLM_API_KEY        — your API key (provider-agnostic name, preferred)
  ANTHROPIC_API_KEY  — accepted as fallback for backward compatibility
  LLM_MODEL         — override the default model (optional)
  LLM_TEMPERATURE   — override temperature (optional, default 0.3)
"""
import os
from typing import Optional
import anthropic

_client: Optional[anthropic.Anthropic] = None

DEFAULT_MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS    = 1200

# Low temperature gives consistent, grounded scholarly responses.
# 0.0 = fully deterministic, 1.0 = default creative/variable.
# 0.3 is appropriate for a commentator persona.
# Override with LLM_TEMPERATURE env var if needed.
DEFAULT_TEMPERATURE = 0.3


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        # Accept either name; LLM_API_KEY preferred
        api_key = os.environ.get("LLM_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "No LLM API key found. Set LLM_API_KEY in your .env file."
            )
        _client = anthropic.Anthropic(api_key=api_key)
    return _client


def _model() -> str:
    return os.environ.get("LLM_MODEL", DEFAULT_MODEL)


def _temperature() -> float:
    try:
        return float(os.environ.get("LLM_TEMPERATURE", DEFAULT_TEMPERATURE))
    except ValueError:
        return DEFAULT_TEMPERATURE


def complete(
    system: str,
    messages: list[dict],
    max_tokens: int = MAX_TOKENS,
) -> str:
    """
    Send a completion request and return the assistant's text response.

    Args:
        system:     The system prompt string.
        messages:   List of {"role": "user"|"assistant", "content": str}.
        max_tokens: Maximum tokens in the response.

    Returns:
        The assistant's text response as a string.
    """
    client = _get_client()
    response = client.messages.create(
        model=_model(),
        max_tokens=max_tokens,
        temperature=_temperature(),
        system=system,
        messages=messages,
    )
    return "".join(
        block.text for block in response.content
        if hasattr(block, "text")
    )
