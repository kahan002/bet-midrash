"""
conversation.py — in-memory conversation store.

Stores a SHARED timeline per session — all agents and the user appear
in one sequence, with each message attributed to its speaker.

Message format in the shared store:
  {"role": "user"|"agent", "agent_id": str|None, "content": str}

When building messages for the LLM API (which expects only "user"/"assistant"
roles), the orchestrator converts the shared timeline into a two-role format
with agent attribution injected into the content.

For persistence, replace the dict with Redis or SQLite — the interface is the same.
"""
from collections import defaultdict

# { session_id: [{"role": "user"|"agent", "agent_id": str|None, "content": str}, ...] }
_store: dict[str, list[dict]] = defaultdict(list)


def get_shared_history(session_id: str) -> list[dict]:
    """Return a copy of the full shared conversation timeline for this session."""
    return list(_store[session_id])


def append_user(session_id: str, content: str) -> None:
    """Append a user message to the shared timeline."""
    _store[session_id].append({
        "role": "user",
        "agent_id": None,
        "content": content,
    })


def append_agent(session_id: str, agent_id: str, content: str) -> None:
    """Append an agent response to the shared timeline."""
    _store[session_id].append({
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

    User messages stay as {"role": "user", "content": ...}.
    Agent messages become {"role": "assistant", "content": ...} when from the
    current agent, or {"role": "user", "content": "[AgentName]: ..."} when
    from another agent — so the current agent sees other agents' words as
    part of the conversation context.

    This way each agent sees the full shared timeline with clear attribution.
    """
    history = _store[session_id]
    messages = []

    for msg in history:
        if msg["role"] == "user":
            messages.append({"role": "user", "content": msg["content"]})
        elif msg["role"] == "agent":
            aid = msg["agent_id"]
            name = agent_names.get(aid, aid)
            if aid == current_agent_id:
                messages.append({"role": "assistant", "content": msg["content"]})
            else:
                # Another agent's words appear as user-turn context with attribution
                messages.append({
                    "role": "user",
                    "content": f"[{name} said]: {msg['content']}",
                })

    return messages


def clear_session(session_id: str) -> None:
    """Clear all history for a session."""
    _store[session_id] = []
