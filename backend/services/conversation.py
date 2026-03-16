"""
conversation.py — in-memory conversation store.

Keyed by (session_id, agent_id) so each student has independent
history with each commentator. For a small cohort this is fine in
memory. If you need persistence, replace the dict with Redis or
a simple SQLite table — the interface stays the same.
"""
from collections import defaultdict

# { (session_id, agent_id): [{"role": ..., "content": ...}, ...] }
_store: dict[tuple[str, str], list[dict]] = defaultdict(list)


def get_history(session_id: str, agent_id: str) -> list[dict]:
    """Return a copy of conversation history for this session+agent."""
    return list(_store[(session_id, agent_id)])


def append(session_id: str, agent_id: str, role: str, content: str) -> None:
    """Append a single message to the conversation history."""
    assert role in ("user", "assistant"), f"Invalid role: {role}"
    _store[(session_id, agent_id)].append({"role": role, "content": content})


def clear(session_id: str, agent_id: str) -> None:
    """Clear history for a specific session+agent pair."""
    _store[(session_id, agent_id)] = []


def clear_session(session_id: str) -> None:
    """Clear all history for a session (all agents)."""
    keys = [k for k in _store if k[0] == session_id]
    for k in keys:
        del _store[k]
