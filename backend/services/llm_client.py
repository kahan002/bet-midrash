"""
llm_client.py — thin provider abstraction with tool call support.

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
import json
import asyncio
from typing import Optional, Callable, Awaitable
import anthropic

_client: Optional[anthropic.Anthropic] = None

DEFAULT_MODEL       = "claude-sonnet-4-20250514"
MAX_TOKENS          = 1200
DEFAULT_TEMPERATURE = 0.3
MAX_TOOL_CALLS      = 5  # per Andrew Ng: beyond 5 you're in a loop


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
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
    Simple completion with no tools. Used for Turn 1 (ref extraction)
    and any other call that doesn't need tool use.
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


async def complete_with_tools(
    system: str,
    messages: list[dict],
    tools: list[dict],
    tool_executor: Callable[[str, dict], Awaitable[dict]],
    max_tokens: int = MAX_TOKENS,
) -> tuple[str, list[dict]]:
    """
    Completion with tool use support. Handles the agentic loop:
    the model may call tools multiple times before giving a final response.
    Capped at MAX_TOOL_CALLS (5) to prevent loops.

    Args:
        system:        The system prompt.
        messages:      Conversation history.
        tools:         List of tool schema dicts (Anthropic format).
        tool_executor: Async function (tool_name, tool_input) -> result_dict.
                       Called once per tool invocation.
        max_tokens:    Max tokens per response turn.

    Returns:
        (final_text, tool_calls_made)
        where tool_calls_made is a list of {"ref": ..., "source": ..., "status": ...}
        for the frontend to use (e.g. to update the text pane).
    """
    client = _get_client()
    working_messages = list(messages)
    tool_calls_made = []
    call_count = 0

    while call_count < MAX_TOOL_CALLS:
        response = client.messages.create(
            model=_model(),
            max_tokens=max_tokens,
            temperature=_temperature(),
            system=system,
            messages=working_messages,
            tools=tools,
        )

        # If the model stopped naturally (no tool call), we're done
        if response.stop_reason == "end_turn":
            text = "".join(
                block.text for block in response.content
                if hasattr(block, "text")
            )
            return text, tool_calls_made

        # If stop_reason is "tool_use", process each tool call
        if response.stop_reason == "tool_use":
            # Add the assistant's response (including tool_use blocks) to history
            working_messages.append({
                "role": "assistant",
                "content": response.content,
            })

            # Execute each tool call and collect results
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue

                call_count += 1
                tool_input = block.input

                # Execute the tool
                result = await tool_executor(block.name, tool_input)

                # Track for frontend use
                tool_calls_made.append({
                    "ref":    tool_input.get("ref", ""),
                    "source": tool_input.get("source", ""),
                    "status": result.get("status", "unknown"),
                })

                # Format result for the API
                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": block.id,
                    "content":     json.dumps(result),
                })

                if call_count >= MAX_TOOL_CALLS:
                    # Add a note so the model knows it's hit the limit
                    tool_results.append({
                        "type":    "tool_result",
                        "tool_use_id": block.id + "_limit",
                        "content": json.dumps({
                            "status":  "error",
                            "message": "Tool call limit reached. Please proceed with available information.",
                        }),
                    })
                    break

            # Add tool results to working messages and loop
            working_messages.append({
                "role":    "user",
                "content": tool_results,
            })

            if call_count >= MAX_TOOL_CALLS:
                # Force a final response with no more tools
                final = client.messages.create(
                    model=_model(),
                    max_tokens=max_tokens,
                    temperature=_temperature(),
                    system=system,
                    messages=working_messages,
                    # No tools — forces end_turn
                )
                text = "".join(
                    block.text for block in final.content
                    if hasattr(block, "text")
                )
                return text, tool_calls_made

        else:
            # Unexpected stop reason — extract whatever text we have
            text = "".join(
                block.text for block in response.content
                if hasattr(block, "text")
            )
            return text, tool_calls_made

    # Fell out of loop (shouldn't happen given the logic above, but be safe)
    return "", tool_calls_made
