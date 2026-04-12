from .base import CommentatorAgent, AgentConfig
from .rashbam import RashbamAgent
from .rashi import RashiAgent

# ─── Agent registry ───────────────────────────────────────────────────────────
_REGISTRY: dict[str, CommentatorAgent] = {
    agent.id: agent
    for agent in [
        RashbamAgent(),
        RashiAgent(),
        # IbnEzraAgent(),
        # RambanAgent(),
    ]
}

# ─── Midrash source registry ──────────────────────────────────────────────────
# Keyed by source_id used in the fetch tool enum.
# sefaria_name is the exact Sefaria text title for URL construction.
# Adding a new midrash: add one entry here — the tool schema updates automatically.
MIDRASH_SOURCES: dict[str, dict] = {
    "bereishit_rabbah": {"sefaria_name": "Bereishit Rabbah", "display_name": "Bereishit Rabbah"},
    "shemot_rabbah":    {"sefaria_name": "Shemot Rabbah",    "display_name": "Shemot Rabbah"},
    "vayikra_rabbah":   {"sefaria_name": "Vayikra Rabbah",   "display_name": "Vayikra Rabbah"},
    "bamidbar_rabbah":  {"sefaria_name": "Bamidbar Rabbah",  "display_name": "Bamidbar Rabbah"},
    "devarim_rabbah":   {"sefaria_name": "Devarim Rabbah",   "display_name": "Devarim Rabbah"},
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
    Build the fetch_sefaria tool schema from the agent registry + midrash registry.
    Both are defined in this module — adding a new agent or midrash source
    updates the schema everywhere automatically.
    """
    configs        = get_all_configs()
    agent_sources  = [c.id for c in configs]
    midrash_ids    = list(MIDRASH_SOURCES.keys())
    sources        = ["bible"] + agent_sources + midrash_ids

    agent_desc = " ".join(
        f"'{c.id}' for {c.name}'s commentary."
        for c in configs
    )
    midrash_desc = " ".join(
        f"'{k}' for {v['display_name']}."
        for k, v in MIDRASH_SOURCES.items()
    )
    source_descriptions = (
        "'bible' for the biblical text itself. "
        + agent_desc + " "
        + midrash_desc
    )

    return {
        "name": "fetch_sefaria",
        "description": (
            "Fetch text from Sefaria to verify commentary or biblical text. "
            "Use this rather than relying on memory. "
            "Maximum 5 calls per response. "
            "Prefer range refs ('Exodus 21:1-5') over multiple single-verse calls."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ref": {
                    "type": "string",
                    "description": (
                        "For bible/commentators: biblical reference e.g. 'Exodus 3:11' "
                        "or range 'Exodus 21:1-10'. "
                        "For midrash: section and paragraph e.g. '1:1' or '3:4' "
                        "(the midrash name is prepended automatically)."
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
