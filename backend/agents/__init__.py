from .base import CommentatorAgent, AgentConfig
from .rashbam import RashbamAgent
from .rashi import RashiAgent
from .ibn_ezra import IbnEzraAgent

# ─── Agent registry ───────────────────────────────────────────────────────────
# Agents are sources that also have system prompts and appear in the UI.
# Registered in chronological order.
_REGISTRY: dict[str, CommentatorAgent] = {
    agent.id: agent
    for agent in [
        RashiAgent(),
        RashbamAgent(),
        IbnEzraAgent(),
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
    return [agent.config for agent in _REGISTRY.values()]


# ─── Unified source registry ──────────────────────────────────────────────────
# Every fetchable source lives here — agents, variant commentaries, midrashim,
# and the biblical text. The fetch tool enum is derived entirely from this dict.
#
# Entry shape:
#   sefaria_prefix  — prepended to the biblical ref to form the Sefaria URL key
#                     (empty string for bible; for midrash, use sefaria_name instead)
#   sefaria_name    — for midrash sources where the ref IS the work title + section
#   display_name    — human-readable label shown in UI and ctx-bar
#   is_agent        — True if this source has a CommentatorAgent in _REGISTRY
#   is_midrash      — True if ref format is section:paragraph, not book chapter:verse
#   en_translation_prefs — ordered list of preferred English translator substrings
#   en_translation_label — human-readable translator label
#   show_caveat     — True if the English translation sometimes paraphrases/omits
#
# Agents are also sources — they appear here AND in _REGISTRY.
# Variant commentaries (e.g. Ibn Ezra's short Exodus commentary) appear here only.
# Midrashim appear here only.

SOURCES: dict[str, dict] = {

    # ── Biblical text ──────────────────────────────────────────────────────────
    "bible": {
        "sefaria_prefix":       "",
        "display_name":         "Bible",
        "is_agent":             False,
        "is_midrash":           False,
        "en_translation_prefs": ["jewish publication society", "jps", "mechon mamre"],
        "en_translation_label": "JPS",
        "show_caveat":          False,
    },

    # ── Active commentator agents ──────────────────────────────────────────────
    "rashi": {
        "sefaria_prefix":       "Rashi on ",
        "display_name":         "Rashi",
        "is_agent":             True,
        "is_midrash":           False,
        "en_translation_prefs": ["silbermann", "rosenbaum", "sefaria community translation"],
        "en_translation_label": "Silbermann",
        "show_caveat":          False,
    },
    "rashbam": {
        "sefaria_prefix":       "Rashbam on ",
        "display_name":         "Rashbam",
        "is_agent":             True,
        "is_midrash":           False,
        "en_translation_prefs": [
            "rashbam's commentary on the torah",  # Lockshin — not yet on Sefaria
            "lockshin",
            "hachut hameshulash",                  # Munk — current best available
            "eliyahu munk",
            "munk",
        ],
        "en_translation_label": "Munk",
        "show_caveat":          True,   # Munk occasionally paraphrases or omits
    },
    "ibn_ezra": {
        "sefaria_prefix":       "Ibn Ezra on ",
        "display_name":         "Ibn Ezra",
        "is_agent":             True,
        "is_midrash":           False,
        "en_translation_prefs": ["strickman", "silver", "english"],
        "en_translation_label": "Strickman/Silver",
        "show_caveat":          False,
    },
    # "ramban": { ... },

    # ── Variant commentaries (not active agents) ───────────────────────────────
    # Ibn Ezra's first (short) commentary on Exodus — distinct Sefaria text
    "ibn_ezra_hakatzar": {
        "sefaria_prefix":       "Ibn Ezra HaKatzar on ",
        "display_name":         "Ibn Ezra (Short Commentary on Exodus)",
        "is_agent":             False,
        "is_midrash":           False,
        "en_translation_prefs": ["strickman", "silver", "english"],
        "en_translation_label": "Strickman/Silver",
        "show_caveat":          False,
    },

    # ── Midrash sources ────────────────────────────────────────────────────────
    "bereishit_rabbah": {
        "sefaria_name":         "Bereishit Rabbah",
        "display_name":         "Bereishit Rabbah",
        "is_agent":             False,
        "is_midrash":           True,
        "en_translation_prefs": [],
        "en_translation_label": "",
        "show_caveat":          False,
    },
    "shemot_rabbah": {
        "sefaria_name":         "Shemot Rabbah",
        "display_name":         "Shemot Rabbah",
        "is_agent":             False,
        "is_midrash":           True,
        "en_translation_prefs": [],
        "en_translation_label": "",
        "show_caveat":          False,
    },
    "vayikra_rabbah": {
        "sefaria_name":         "Vayikra Rabbah",
        "display_name":         "Vayikra Rabbah",
        "is_agent":             False,
        "is_midrash":           True,
        "en_translation_prefs": [],
        "en_translation_label": "",
        "show_caveat":          False,
    },
    "bamidbar_rabbah": {
        "sefaria_name":         "Bamidbar Rabbah",
        "display_name":         "Bamidbar Rabbah",
        "is_agent":             False,
        "is_midrash":           True,
        "en_translation_prefs": [],
        "en_translation_label": "",
        "show_caveat":          False,
    },
    "devarim_rabbah": {
        "sefaria_name":         "Devarim Rabbah",
        "display_name":         "Devarim Rabbah",
        "is_agent":             False,
        "is_midrash":           True,
        "en_translation_prefs": [],
        "en_translation_label": "",
        "show_caveat":          False,
    },
}

# Keep MIDRASH_SOURCES for backward compat with execute_fetch_tool
# — derived automatically from SOURCES
MIDRASH_SOURCES = {
    k: {"sefaria_name": v["sefaria_name"], "display_name": v["display_name"]}
    for k, v in SOURCES.items()
    if v.get("is_midrash")
}


def build_fetch_tool_schema() -> dict:
    """
    Build the fetch_sefaria tool schema from the unified SOURCES registry.
    The enum is all keys in SOURCES. Adding a new source updates the tool
    schema everywhere automatically.
    """
    source_ids = list(SOURCES.keys())

    desc_parts = []
    for sid, s in SOURCES.items():
        name = s["display_name"]
        if s.get("is_midrash"):
            desc_parts.append(
                f"'{sid}' for {name} (ref format: section:paragraph e.g. '1:1')."
            )
        else:
            desc_parts.append(f"'{sid}' for {name}.")

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
                        "For midrash: section and paragraph e.g. '1:1' "
                        "(the work name is prepended automatically)."
                    ),
                },
                "source": {
                    "type": "string",
                    "enum": source_ids,
                    "description": " ".join(desc_parts),
                },
            },
            "required": ["ref", "source"],
        },
    }
