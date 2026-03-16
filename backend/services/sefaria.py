"""
sefaria.py — all Sefaria API interactions.

Key fix: always request ?version=english&version=hebrew so versions[]
contains both languages. Without this, Sefaria returns only the primary
version (Hebrew for Rashbam), making English invisible.
"""
import re
import httpx
from typing import Optional

SEFARIA_BASE = "https://www.sefaria.org/api/v3/texts/"

BOOK_MAP = {
    "genesis": "Genesis", "gen": "Genesis", "bereshit": "Genesis",
    "exodus": "Exodus", "exod": "Exodus", "shemot": "Exodus",
    "leviticus": "Leviticus", "lev": "Leviticus", "vayikra": "Leviticus",
    "numbers": "Numbers", "num": "Numbers", "bamidbar": "Numbers",
    "deuteronomy": "Deuteronomy", "deut": "Deuteronomy", "devarim": "Deuteronomy",
}


def normalize_ref(ref: str) -> str:
    """Ensure the book name is correctly capitalized for Sefaria."""
    m = re.match(r'^([a-z]+)([\s\d:.\-].*)$', ref.strip(), re.IGNORECASE)
    if not m:
        return ref
    book = BOOK_MAP.get(m.group(1).lower())
    return f"{book}{m.group(2)}" if book else ref


def parse_ref(text: str) -> Optional[str]:
    """
    Extract a biblical reference from free text.
    e.g. "what does Genesis 32:25 say" -> "Genesis 32:25"

    Currently recognises Torah only (Genesis–Deuteronomy) — this is an
    early prototype. Nevi'im and Ketuvim support is a planned extension.
    Returns None if no reference found.
    """
    pattern = (
        r"\b(genesis|gen|bereshit|exodus|exod|shemot|"
        r"leviticus|lev|vayikra|numbers|num|bamidbar|"
        r"deuteronomy|deut|devarim)"
        r"\b[\s.]*(\d+)[:\s]+(\d+(?:-\d+)?)"
    )
    m = re.search(pattern, text, re.IGNORECASE)
    if not m:
        return None
    book = BOOK_MAP.get(m.group(1).lower())
    return f"{book} {m.group(2)}:{m.group(3)}" if book else None


def _to_sefaria_url(ref: str) -> str:
    """Convert "Genesis 32:24-33" -> "Genesis.32.24-33" (Sefaria dot notation)."""
    return normalize_ref(ref).replace(" ", ".").replace(":", ".")


def _strip_html(s: str) -> str:
    if not s:
        return ""
    # Remove Sefaria inline footnotes
    s = re.sub(r'<sup[^>]*>.*?</sup>', '', s, flags=re.IGNORECASE | re.DOTALL)
    s = re.sub(r'<i class="footnote"[^>]*>.*?</i>', '', s, flags=re.IGNORECASE | re.DOTALL)
    s = re.sub(r"<[^>]+>", "", s)
    for entity, char in [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                          ("&quot;", '"'), ("&#39;", "'")]:
        s = s.replace(entity, char)
    return re.sub(r'\s+', ' ', s).strip()


def _extract_verse_array(text) -> list:
    """
    Returns an index-aligned list where each slot is one verse string or None.
    Preserves verse positions so sparse commentary arrays stay aligned.

    Sefaria's JaggedArray structure for commentaries:
    - Flat list of strings: ["verse1", "verse2"] → one entry per verse
    - List of lists: [["seg1", "seg2"], "verse2"] → sub-segments joined per verse
    - Single wrapped list: [["seg1", "seg2", "seg3"]] → one verse, join all segments
    - Deeply nested single verse: ["seg1", "seg2", "seg3"] where all items are
      strings and represent one commentary entry split into paragraphs → join all
    """
    if isinstance(text, str):
        return [text.strip() or None]
    if not isinstance(text, list) or not text:
        return []

    # If it's a list where the single element is itself a list, unwrap one level
    if len(text) == 1 and isinstance(text[0], list):
        return _extract_verse_array(text[0])

    # If ALL items are strings, this is either:
    # (a) multiple verses as flat strings, OR
    # (b) multiple sub-segments of a single verse
    # We detect (b) by checking if this looks like a JaggedArray at verse level
    # (which would have been unwrapped already) vs segment level.
    # Since we can't distinguish reliably, join sub-segments when the parent
    # was a single-element list (already handled above via recursion).
    # For multi-element all-string lists, treat each as a separate verse.
    result = []
    for item in text:
        if isinstance(item, str):
            result.append(item.strip() or None)
        elif isinstance(item, list):
            # Sub-segments of one verse — recursively flatten and join
            def _flatten(lst):
                for x in lst:
                    if isinstance(x, list):
                        yield from _flatten(x)
                    elif isinstance(x, str) and x.strip():
                        yield x.strip()
            parts = list(_flatten(item))
            result.append(" ".join(parts) if parts else None)
        else:
            result.append(None)
    return result


# Translation preferences
# Lockshin ("Rashbam's Commentary on the Torah by Prof. Rabbi Marty Lockshin")
# is the gold-standard scholarly translation but is not currently in Sefaria's
# API-accessible version library — only Munk (HaChut Hameshulash) is available.
# The Lockshin entries are kept intentionally: if Sefaria adds it, the preference
# list will pick it up automatically with no code changes needed.
_RASHBAM_EN_PREF = [
    "rashbam's commentary on the torah",  # Lockshin — not yet on Sefaria API
    "lockshin",                            # Lockshin — not yet on Sefaria API
    "hachut hameshulash",                  # Munk — current best available
    "eliyahu munk",                        # Munk alternate title form on Sefaria
    "munk",
]

# Bible translation preferences — JPS 1917 and Sefaria Community are footnote-free.
# The Contemporary Torah (JPS 2006) embeds footnotes as literal text in the string
# which cannot be reliably stripped, so it is de-prioritised.
_BIBLE_EN_PREF = [
    "Jewish Publication Society",    # JPS 1917 — clean, no inline footnotes
    "Sefaria Community Translation", # clean modern translation
    "JPS 1985 Tanakh",               # some footnotes but fewer
    "The Contemporary Torah",        # avoid if possible — footnotes embedded in text
]


def _pick_best_version(versions: list[dict], language: str,
                       preferences: list[str] | None = None) -> Optional[dict]:
    lang_versions = [v for v in versions if v.get("language") == language]
    if not lang_versions:
        return None
    if language == "he":
        return lang_versions[0]
    if preferences:
        for pref in preferences:
            for v in lang_versions:
                if pref.lower() in v.get("versionTitle", "").lower():
                    return v
    return lang_versions[0]


def _translation_label(version_title: str) -> str:
    t = (version_title or "").lower()
    if "lockshin" in t:
        return "Lockshin translation"
    if "hachut" in t or "munk" in t:
        return "Munk translation (HaChut Hameshulash)"
    return version_title or "translation"


async def fetch_text(sefaria_ref: str,
                     en_preferences: list[str] | None = None) -> dict:
    """
    Fetch a text from Sefaria.
    Uses ?version=english&version=hebrew to guarantee both languages
    appear in versions[] when available.
    """
    url = SEFARIA_BASE + sefaria_ref + "?version=english&version=hebrew"

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()

    versions = data.get("versions", [])
    en_ver = _pick_best_version(versions, "en", en_preferences)
    he_ver = _pick_best_version(versions, "he")

    en_texts = [_strip_html(t) if t else None
                for t in _extract_verse_array(en_ver.get("text", []) if en_ver else [])]
    he_texts = [_strip_html(t) if t else None
                for t in _extract_verse_array(he_ver.get("text", []) if he_ver else [])]

    en_clean = [t for t in en_texts if t]
    he_clean = [t for t in he_texts if t]
    en_title = en_ver.get("versionTitle", "") if en_ver else ""

    return {
        "ref": sefaria_ref,
        "en": en_texts,
        "he": he_texts,
        "en_clean": en_clean,
        "he_clean": he_clean,
        "en_version_title": en_title,
        "en_translation_label": _translation_label(en_title),
        "found": bool(en_clean or he_clean),
    }


async def fetch_passage(ref: str, sefaria_commentary_prefix: str) -> dict:
    """
    Fetch both the biblical text and the commentary for a given ref.
    """
    ref = normalize_ref(ref)
    sefaria_ref = _to_sefaria_url(ref)
    prefix_url = sefaria_commentary_prefix.strip().replace(" ", "_") + "_"
    commentary_sefaria_ref = prefix_url + sefaria_ref

    import asyncio
    bible_data, commentary_data = await asyncio.gather(
        fetch_text(sefaria_ref, en_preferences=_BIBLE_EN_PREF),
        fetch_text(commentary_sefaria_ref, en_preferences=_RASHBAM_EN_PREF),
    )

    he_verses = commentary_data["he"]
    en_verses = commentary_data["en"]
    has_english = bool(commentary_data.get("en_clean"))
    parts = []

    if not commentary_data["found"]:
        parts.append(
            f"No commentary found on Sefaria for {commentary_sefaria_ref}. "
            f"Biblical text: "
            + " | ".join(f"v.{i+1}: \"{t}\""
                         for i, t in enumerate(bible_data["en_clean"]))
        )
    else:
        if not has_english:
            parts.append(
                f"Note: English translation not available on Sefaria for {ref}. "
                f"Hebrew commentary provided."
            )
        else:
            parts.append(
                f"Translation: {commentary_data['en_translation_label']} "
                f"(Note: Munk occasionally paraphrases or omits — check Hebrew where in doubt)"
            )
        for i, comm_he in enumerate(he_verses):
            if not comm_he:
                continue
            bible_en = bible_data["en"][i] if i < len(bible_data["en"]) else None
            comm_en = en_verses[i] if i < len(en_verses) else None
            label = f"Verse {i+1}"
            if comm_en:
                parts.append(f"{label} — Bible: \"{bible_en or ''}\" | Commentary: \"{comm_en}\"")
            else:
                parts.append(f"{label} — Bible: \"{bible_en or ''}\" | Commentary (Hebrew): \"{comm_he}\"")

    is_munk = "munk" in commentary_data.get("en_translation_label", "").lower() or \
              "hachut" in commentary_data.get("en_translation_label", "").lower()

    return {
        "ref": ref,
        "bible": {
            "en": bible_data["en"],
            "he": bible_data["he"],
            "en_version_title": bible_data.get("en_version_title", ""),
        },
        "commentary": {
            "en": commentary_data["en"],
            "he": commentary_data["he"],
            "has_english": has_english,
            "is_munk": is_munk,
            "en_translation_label": commentary_data.get("en_translation_label", ""),
            "found": commentary_data["found"],
        },
        "context_string": f"VERIFIED TEXT FROM SEFARIA for {ref}:\n"
                          + "\n".join(parts),
    }


# ─── Concordance (Torah only) ─────────────────────────────────────────────────
# No BDB (1906, uses Ugaritic/Akkadian — anachronistic for Rashbam).
# No Radak (13th c., after Rashbam).
# Just internal biblical usage: where does this word/form appear in Torah?
# That is Rashbam's authentic lexical method.

SEFARIA_WORDS_BASE = "https://www.sefaria.org/api/words/"

_TORAH_BOOKS = {"Genesis", "Exodus", "Leviticus", "Numbers", "Deuteronomy"}


async def fetch_concordance(word: str) -> Optional[str]:
    """
    Fetch all Torah occurrences of a Hebrew word form from Sefaria.
    Returns a concordance summary string for agent context, or None.

    Usage distribution data grounds arguments like "this form appears
    only in legal contexts" — but only if the data actually shows this.
    The agent is instructed not to make frequency claims unsupported by data.
    """
    url = SEFARIA_WORDS_BASE + word + "?lookup_ref=Torah&with_text=0"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()

        if not isinstance(data, list) or not data:
            return None

        all_refs: list[str] = []
        for entry in data:
            refs = entry.get("refs") or entry.get("content", {}).get("refs", [])
            if isinstance(refs, list):
                for r in refs:
                    ref_str = r if isinstance(r, str) else (r.get("ref") if isinstance(r, dict) else None)
                    if ref_str and any(ref_str.startswith(b) for b in _TORAH_BOOKS):
                        all_refs.append(ref_str)

        unique = list(dict.fromkeys(all_refs))
        if not unique:
            return None

        # Group by book
        by_book: dict[str, list[str]] = {}
        for ref in unique:
            book = ref.split(" ")[0]
            by_book.setdefault(book, []).append(ref)

        summary = " | ".join(
            f"{book}: {', '.join(refs)}"
            for book, refs in by_book.items()
        )

        return (
            f'CONCORDANCE — "{word}" appears {len(unique)} time'
            f'{"s" if len(unique) != 1 else ""} in Torah:\n{summary}'
        )

    except Exception as e:
        print(f"[fetch_concordance] failed for {word!r}: {e}")
        return None
