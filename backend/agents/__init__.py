from .base import CommentatorAgent, AgentConfig
from .rashbam import RashbamAgent

# ─── Agent registry ───────────────────────────────────────────────────────────
# Add new agents here. Everything that depends on the set of available agents
# — the fetch tool schema, the source enum, the UI agent list — is derived
# from this registry automatically.
_REGISTRY: dict[str, CommentatorAgent] = {
    agent.id: agent
    for agent in [
        RashbamAgent(),
        # RashiAgent(),
        # IbnEzraAgent(),
        # RambanAgent(),
    ]
}


def get_agent(agent_id: str) -> CommentatorAgent:
    if agent_id not in _REGISTRY:
        raise KeyError(
            f"Unknown agent: '{agent_id}'. "
            f"Available: {list(_REGISTRY.keys())}"
        )
    return _REGISTRY[agent_id]


def list_agents() -> list[dict]:
    return [agent.to_dict() for agent in _REGISTRY.values()]


def get_all_configs() -> list[AgentConfig]:
    """Return all AgentConfig objects — used to build the fetch tool schema."""
    return [agent.config for agent in _REGISTRY.values()]


def build_fetch_tool_schema() -> dict:
    """
    Build the fetch_sefaria tool schema from the agent registry.
    The 'source' enum is derived automatically — adding a new agent to the
    registry automatically expands what the tool can fetch.
    """
    configs = get_all_configs()
    sources = ["bible"] + [c.id for c in configs]

    source_descriptions = "'bible' for the biblical text itself. " + " ".join(
        f"'{c.id}' for {c.name}'s commentary on the verse."
        for c in configs
    )

    return {
        "name": "fetch_sefaria",
        "description": (
            "Fetch text from Sefaria to verify actual commentary or biblical "
            "text before making claims about it. Use this rather than relying "
            "on memory when you need to cite a specific passage. "
            "Maximum 5 calls per response. "
            "Prefer range refs ('Exodus 21:1-5') over multiple single-verse calls."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ref": {
                    "type": "string",
                    "description": (
                        "Biblical reference. Single verse ('Exodus 3:11') "
                        "or range ('Exodus 21:1-10'). Torah only "
                        "(Genesis through Deuteronomy)."
                    ),
                },
                "source": {
                    "type": "string",
                    "enum": sources,
                    "description": source_descriptions,
                },
            },
            "required": ["ref", "source"],
        },
    }
