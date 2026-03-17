from dataclasses import dataclass, field
from typing import Optional
from abc import ABC, abstractmethod


@dataclass
class AgentConfig:
    """
    All static metadata for a commentator agent.

    This is the single source of truth for everything commentator-specific:
    - Sefaria fetch prefix (used by the fetch tool)
    - Translation preferences (which English translator to prefer)
    - UI metadata (name, color, etc.)

    When you add a new agent, you add its AgentConfig here and everything
    else — the fetch tool schema, the UI caveat, the translation picker —
    updates automatically.
    """
    id: str                              # e.g. 'rashbam'
    name: str                            # e.g. 'Rashbam'
    hebrew_name: str                     # e.g. 'רשב"ם'
    full_name: str                       # e.g. 'Rabbi Shmuel ben Meir'
    dates: str                           # e.g. 'c.1080–c.1160'
    tradition: str                       # e.g. 'Franco-Jewish Tosafist'
    color: str                           # hex for UI theming, e.g. '#8b3a2a'
    sefaria_prefix: str                  # e.g. 'Rashbam on '
    coverage_notes: str                  # what books/sections are preserved
    en_translation_prefs: list[str]      # ordered preferred English translators
                                         # (lowercase substrings of versionTitle)
    en_translation_label: str            # human-readable label, e.g. 'Munk'
    show_translation_caveat: bool        # True if translation sometimes paraphrases/omits


class CommentatorAgent(ABC):
    """
    Abstract base class that every commentator agent must subclass.

    ABC (Abstract Base Class) from Python's `abc` module does two things here:

    1. INTERFACE ENFORCEMENT — any method decorated with @abstractmethod
       *must* be implemented by subclasses. If you create a RashiAgent that
       forgets to implement system_prompt(), Python raises a TypeError at
       instantiation time rather than silently failing at runtime. This is
       Python's way of declaring an interface (like Java's `interface` keyword
       or TypeScript's `interface`).

    2. ISINSTANCE CHECKS — `isinstance(agent, CommentatorAgent)` returns True
       for any subclass, which lets the orchestrator accept any commentator
       without knowing its concrete type.

    The only method subclasses *must* implement is system_prompt(). Everything
    else — sefaria_ref(), build_messages(), to_dict() — is provided here and
    can be overridden if a specific commentator needs different behaviour.
    """

    def __init__(self, config: AgentConfig):
        self.config = config

    @property
    def id(self) -> str:
        return self.config.id

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def color(self) -> str:
        return self.config.color

    @abstractmethod
    def system_prompt(self) -> str:
        """
        Return the full system prompt for this agent.
        Subclasses encode all persona, method, and scholarship here.
        """
        ...

    def sefaria_ref(self, ref: str) -> str:
        """
        Convert a plain biblical reference to this agent's Sefaria key.
        e.g. 'Genesis 1:1' -> 'Rashbam on Genesis 1:1'
        """
        return f"{self.config.sefaria_prefix}{ref}"

    def build_messages(
        self,
        user_message: str,
        conversation_history: list[dict],
        sefaria_context: Optional[str] = None,
        auto_fetched_verse: Optional[str] = None,
    ) -> tuple[str, list[dict[str, str]]]:
        """
        Assemble the system prompt (with any injected context) and
        the messages list ready for the LLM client.

        The message dicts have shape {"role": "user"|"assistant", "content": str}.
        A TypedDict would be more precise but adds boilerplate; the shape is
        documented here and enforced by llm_client.py's call to the SDK.

        Returns (system_prompt_with_context, messages_list).
        """
        system = self.system_prompt()

        if sefaria_context:
            system += (
                f"\n\n══ CURRENTLY LOADED PASSAGE (verified from Sefaria) ══\n"
                f"{sefaria_context}"
            )

        if auto_fetched_verse:
            system += (
                f"\n\n══ AUTO-FETCHED CONTEXT (verified from Sefaria) ══\n"
                f"{auto_fetched_verse}"
            )

        messages = list(conversation_history) + [
            {"role": "user", "content": user_message}
        ]

        return system, messages

    def to_dict(self) -> dict[str, str]:
        """
        Serialise agent metadata for the frontend.
        All values are strings — this is safe to JSON-serialise directly.
        """
        return {
            "id": self.config.id,
            "name": self.config.name,
            "hebrew_name": self.config.hebrew_name,
            "full_name": self.config.full_name,
            "dates": self.config.dates,
            "tradition": self.config.tradition,
            "color": self.config.color,
            "coverage_notes": self.config.coverage_notes,
            "en_translation_label": self.config.en_translation_label,
            "show_translation_caveat": str(self.config.show_translation_caveat),
        }
