from .base import CommentatorAgent
from .rashbam import RashbamAgent

# Register all available agents here.
# When you add Rashi, Ibn Ezra, Nachmanides etc., import and add them.
_REGISTRY: dict[str, CommentatorAgent] = {
    agent.id: agent
    for agent in [
        RashbamAgent(),
        # RashiAgent(),
        # IbnEzraAgent(),
        # NachmanideAgent(),
    ]
}


def get_agent(agent_id: str) -> CommentatorAgent:
    if agent_id not in _REGISTRY:
        raise KeyError(f"Unknown agent: '{agent_id}'. "
                       f"Available: {list(_REGISTRY.keys())}")
    return _REGISTRY[agent_id]


def list_agents() -> list[dict]:
    return [agent.to_dict() for agent in _REGISTRY.values()]
