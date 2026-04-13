"""
conversation.py — in-memory conversation store with context summarization.

Stores a SHARED timeline per session — all agents and the user appear
in one sequence, with each message attributed to its speaker.

Message format in the shared store:
  {"role": "user"|"agent", "agent_id": str|None, "content": str}

When the timeline grows long, older messages are compressed into a summary
block. The summary is prepended as context when building LLM messages.

For persistence, replace the dict with Redis or SQLite — the interface stays the same.
"""
from collections import defaultdict

# { session_id: {"summary": str|None, "messages": [...]} }
_store: dict[str, dict] = defaultdict(lambda: {"summary": None, "messages": []})

# Token estimation — rough but sufficient for triggering summarization.
# ~4 chars per token for English; Hebrew is denser but this is conservative.
TOKEN_THRESHOLD  = 6000   # estimate triggering summarization
RECENT_TO_KEEP   = 6      # number of recent exchanges (user+agent pairs) to keep raw


def _estimate_tokens(messages: list[dict]) -> int:
    total = sum(len(m.get("content", "")) for m in messages)
    return total // 4


def get_shared_history(session_id: str) -> list[dict]:
    """Return a copy of the full raw message list (not including summary)."""
    return list(_store[session_id]["messages"])


def get_summary(session_id: str) -> str | None:
    """Return the current summary for this session, if any."""
    return _store[session_id]["summary"]


def set_summary(session_id: str, summary: str) -> None:
    """
    Replace older messages with a summary, keeping only the most recent exchanges.
    Called by the orchestrator after the summarization LLM call completes.
    """
    messages = _store[session_id]["messages"]

    # Keep the last RECENT_TO_KEEP*2 messages (each exchange = user + agent)
    keep_count = RECENT_TO_KEEP * 2
    if len(messages) > keep_count:
        _store[session_id]["summary"] = summary
        _store[session_id]["messages"] = messages[-keep_count:]


def needs_summarization(session_id: str) -> bool:
    """
    Return True if the conversation is long enough to warrant summarization.
    Estimates token count of raw messages (not including existing summary).
    """
    messages = _store[session_id]["messages"]
    if len(messages) < RECENT_TO_KEEP * 2:
        return False
    return _estimate_tokens(messages) > TOKEN_THRESHOLD


def append_user(session_id: str, content: str) -> None:
    """Append a user message to the shared timeline."""
    _store[session_id]["messages"].append({
        "role": "user",
        "agent_id": None,
        "content": content,
    })


def append_agent(session_id: str, agent_id: str, content: str) -> None:
    """Append an agent response to the shared timeline."""
    _store[session_id]["messages"].append({
        "role": "agent",
        "agent_id": agent_id,
        "content": content,
    })


def build_llm_messages(
    session_id: str,
    current_agent_id: str,
    agent_names: dict[str, str],  # {agent_id: display_name}
) -> list[dict]:
    """
    Convert the shared timeline into the two-role format the LLM API expects.

    If a summary exists, it is prepended as a user-turn context block so the
    agent has the full conversational history even after compression.

    User messages → {"role": "user", "content": ...}
    Current agent's messages → {"role": "assistant", "content": ...}
    Other agents' messages → {"role": "user", "content": "[Name said]: ..."}
    """
    session   = _store[session_id]
    summary   = session["summary"]
    messages  = session["messages"]
    result    = []

    # Prepend summary as context if it exists
    if summary:
        result.append({
            "role": "user",
            "content": (
                "══ EARLIER CONVERSATION SUMMARY ══\n"
                + summary
                + "\n══ END SUMMARY — recent exchanges follow ══"
            ),
        })
        # Acknowledge the summary so the message sequence is valid
        result.append({
            "role": "assistant",
            "content": "I have the summary of our earlier discussion. Let us continue.",
        })

    for msg in messages:
        if msg["role"] == "user":
            result.append({"role": "user", "content": msg["content"]})
        elif msg["role"] == "agent":
            aid  = msg["agent_id"]
            name = agent_names.get(aid, aid)
            if aid == current_agent_id:
                result.append({"role": "assistant", "content": msg["content"]})
            else:
                result.append({
                    "role": "user",
                    "content": f"[{name} said]: {msg['content']}",
                })

    return result


def clear_session(session_id: str) -> None:
    """Clear all history and summary for a session."""
    _store[session_id] = {"summary": None, "messages": []}
