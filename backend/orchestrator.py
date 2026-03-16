"""
orchestrator.py — routes questions to agents, manages multi-agent turns,
and runs self-directed retrieval before answering.

Process for ask():
  1. Extract relevant verse refs AND Rashi refs from the question (one LLM call)
  2. Fetch Rashbam + Rashi in parallel from Sefaria
  3. Build the agent response with all verified text injected
"""
import json
import asyncio
from typing import Optional
from .agents import get_agent
from .services import conversation as conv_store
from .services import sefaria as sefaria_svc
from .services import llm_client

# Updated to return both verses and rashi refs in one call.
# Rashi is fetched for every verse because ~50% of Rashbam is direct
# response to specific Rashi comments. Having the actual text lets the
# agent engage precisely rather than working from memory.
REF_EXTRACTION_SYSTEM = """You are a Torah reference extractor for a Rashbam commentary study tool.

Given a question or message about Torah, return a JSON object with:
1. "verses" — array of objects, each with:
   - "ref": the specific biblical passage (e.g. "Exodus 22:1-2")
   - "confidence": "high" if certain, "low" if uncertain
   - "ambiguity": (only if confidence is "low") brief note on what is unclear
2. "rashi" — array of strings: corresponding Rashi refs (prefixed "Rashi on ")
3. "words" — key Hebrew words worth concordance lookup (max 3, unvowelised)
4. "clarification_needed": (only if any verse has confidence "low" or question is ambiguous)
   A single focused question written as Rashbam would ask it — first person, warmly scholarly.
   Example: "Before I answer — when you ask about ben sorer u'moreh, do you mean the plain
   text of Deuteronomy 21, or the talmudic elaboration of this law?"

RABBINIC TERM GLOSSARY — these are HIGH confidence mappings:
- ba bamachteret / הבא במחתרת / tunneling burglar → Exodus 22:1-2
- ayin tachat ayin / eye for an eye → Exodus 21:24
- nefesh tachat nefesh / life for life → Exodus 21:23
- gid ha-nashe / גיד הנשה / sciatic nerve → Genesis 32:33
- eved ivri / עבד עברי / Hebrew slave → Exodus 21:2
- yibum / levirate marriage → Deuteronomy 25:5-6
- chalitza → Deuteronomy 25:7-10
- lo tivashel / do not boil a kid → Exodus 23:19, Exodus 34:26, Deuteronomy 14:21
- akeidat yitzchak / binding of Isaac / akeida → Genesis 22:1-19
- mishpatim (as a section) → Exodus 21:1
- marah / bitter waters → Exodus 15:23-25

Mark confidence "low" when:
- The term is not in the glossary and you are guessing the verse
- The question could refer to biblical text OR talmudic/rabbinic development
- The question spans many passages and you cannot confidently pick one

Example for clear question:
{"verses": [{"ref": "Exodus 22:1-2", "confidence": "high"}], "rashi": ["Rashi on Exodus 22:1"], "words": ["מַחְתֶּרֶת"]}

Example for ambiguous question:
{"verses": [{"ref": "Deuteronomy 21:18-21", "confidence": "low", "ambiguity": "mainly talmudic concept"}], "rashi": ["Rashi on Deuteronomy 21:18"], "words": ["סוֹרֵר"], "clarification_needed": "Before I answer — when you ask about ben sorer u'moreh, do you mean the plain text of Deuteronomy 21, or the talmudic elaboration?"}

Rules:
- Only include Torah passages (Genesis through Deuteronomy)
- Be specific — prefer "Exodus 22:1" over "Exodus 22"
- The rashi array should mirror the verses array (use ref strings)
- Return ONLY the JSON object, no other text"""


async def extract_relevant_refs(user_message: str) -> dict:
    """
    Turn 1: Identify verse refs (with confidence), Rashi refs, key words,
    and optionally a clarification question if the term is ambiguous.
    Returns {"verses": [...], "rashi": [...], "words": [...], "clarification_needed": str|None}.
    """
    try:
        result = llm_client.complete(
            system=REF_EXTRACTION_SYSTEM,
            messages=[{"role": "user", "content": user_message}],
            max_tokens=500,
        )
        parsed = json.loads(result.strip())

        # Normalise verses — accept both string and {ref, confidence} format
        raw_verses = parsed.get("verses", [])
        verses = []
        for v in raw_verses:
            if isinstance(v, str):
                verses.append({"ref": v, "confidence": "high"})
            elif isinstance(v, dict) and v.get("ref"):
                verses.append({
                    "ref": v["ref"],
                    "confidence": v.get("confidence", "high"),
                    "ambiguity": v.get("ambiguity"),
                })

        return {
            "verses":                verses,
            "rashi":                 [r for r in parsed.get("rashi",  []) if isinstance(r, str)],
            "words":                 [w for w in parsed.get("words",  []) if isinstance(w, str)][:3],
            "clarification_needed":  parsed.get("clarification_needed") or None,
        }
    except Exception as e:
        print(f"[ref extraction] failed: {e}")
        return {"verses": [], "rashi": [], "words": [], "clarification_needed": None}


async def fetch_rashi(verse_ref: str) -> Optional[str]:
    """
    Fetch Rashi's commentary on a verse from Sefaria.
    Returns a formatted string for injection into the agent context, or None.
    """
    try:
        passage = await sefaria_svc.fetch_passage(
            verse_ref,
            sefaria_commentary_prefix="Rashi on "
        )
        comm = passage["commentary"]
        if not comm["found"]:
            return None
        lines = []
        he_verses = comm["he"]
        en_verses = comm["en"]
        for i, he in enumerate(he_verses):
            if not he:
                continue
            en = en_verses[i] if i < len(en_verses) else None
            if he:  lines.append(f"He: {he}")
            if en:  lines.append(f"En: {en}")
        return f"RASHI ON {verse_ref} (from Sefaria):\n" + "\n".join(lines) if lines else None
    except Exception as e:
        print(f"[fetch_rashi] failed for {verse_ref}: {e}")
        return None


async def ask(
    session_id: str,
    agent_id: str,
    user_message: str,
    loaded_ref: Optional[str] = None,
) -> dict:
    """
    Send a question to a single commentator agent.

    Step 1: Extract verse refs + Rashi refs (one LLM call)
    Step 2: Fetch Rashbam passages + Rashi in parallel from Sefaria
    Step 3: Build agent response with all verified text injected
    """
    agent = get_agent(agent_id)
    history = conv_store.get_history(session_id, agent_id)

    # ── Step 1: identify verses and Rashi refs ────────────────────────────────
    detected_ref = sefaria_svc.parse_ref(user_message)
    extracted = await extract_relevant_refs(user_message)

    # If clarification is needed, return early — the caller (frontend) will
    # display the question and resend with combined message on user reply.
    if extracted["clarification_needed"]:
        return {
            "agent_id": agent_id,
            "response": None,
            "clarification_needed": extracted["clarification_needed"],
            "retrieved_refs": [],
            "retrieved_rashi": [],
            "retrieved_words": [],
        }

    # Extract refs — prefer high-confidence; fall back to all if none are high
    verse_objects = extracted["verses"]
    high_conf = [v["ref"] for v in verse_objects if v.get("confidence") == "high"]
    all_extracted_refs = [v["ref"] for v in verse_objects]
    usable_refs = high_conf if high_conf else all_extracted_refs

    all_verse_refs = list(dict.fromkeys(
        r for r in ([detected_ref] if detected_ref else []) + usable_refs
        if r and r != loaded_ref
    ))

    all_rashi_refs = list(dict.fromkeys(
        r for r in extracted["rashi"]
        + ([f"Rashi on {detected_ref}"] if detected_ref else [])
        + ([f"Rashi on {loaded_ref}"]   if loaded_ref   else [])
        if r
    ))

    all_words = extracted.get("words", [])[:3]

    print(f"[self-retrieval] verses: {all_verse_refs}")
    print(f"[self-retrieval] rashi: {all_rashi_refs}")
    print(f"[self-retrieval] words: {all_words}")

    # ── Step 2: fetch everything in parallel ──────────────────────────────────
    # Primary verse: fetched thick and treated as the loaded context.
    # If the caller passed a loaded_ref, that takes precedence and the
    # identified primary verse becomes additional context instead.
    primary_ref = all_verse_refs[0] if all_verse_refs and not loaded_ref else None
    secondary_refs = (
        all_verse_refs[1:3] if not loaded_ref
        else [r for r in all_verse_refs if r != loaded_ref][:2]
    )

    async def safe_fetch_passage(ref: str) -> Optional[str]:
        try:
            p = await sefaria_svc.fetch_passage(ref, agent.config.sefaria_prefix)
            return p["context_string"]
        except Exception as e:
            print(f"[sefaria] passage fetch failed for {ref}: {e}")
            return None

    # Fetch loaded passage, primary auto-identified verse, secondaries, Rashi,
    # and concordance all in parallel — one round trip.
    (
        loaded_context,
        primary_ctx,
        secondary_results,
        rashi_results,
        concordance_results,
    ) = await asyncio.gather(
        safe_fetch_passage(loaded_ref) if loaded_ref else asyncio.sleep(0, result=None),
        safe_fetch_passage(primary_ref) if primary_ref else asyncio.sleep(0, result=None),
        asyncio.gather(*[safe_fetch_passage(r) for r in secondary_refs]),
        asyncio.gather(*[fetch_rashi(r.replace("Rashi on ", "")) for r in all_rashi_refs[:4]]),
        asyncio.gather(*[sefaria_svc.fetch_concordance(w) for w in all_words]),
    )

    # Effective primary context: manually loaded passage takes precedence,
    # otherwise the thick-fetched primary identified verse.
    effective_loaded_ctx = loaded_context or primary_ctx

    fetched_rashbam     = [r for r in secondary_results if r]
    fetched_rashi       = [r for r in rashi_results      if r]
    fetched_concordance = [r for r in concordance_results if r]

    # ── Step 3: build context and generate response ───────────────────────────
    rashi_block = (
        "RASHI'S COMMENTARY ON THESE VERSES (verified from Sefaria):\n" +
        "\n\n".join(fetched_rashi) +
        "\n\nNOTE ON RASHI: These are Rashi's actual words. Quote from this text "
        "directly when you engage with him. Engage precisely with what he wrote "
        "above, not with a general memory of his approach."
    ) if fetched_rashi else (
        "NOTE ON RASHI: Rashi's commentary could not be retrieved. Work from "
        "general knowledge of his approach but flag that you are doing so."
    )

    concordance_block = (
        "CONCORDANCE DATA (Torah occurrences, from Sefaria):\n" +
        "\n\n".join(fetched_concordance) +
        "\n\nNOTE: You may argue from word distribution ONLY if the data above supports the claim."
    ) if fetched_concordance else ""

    secondary_block = (
        "ADDITIONAL RASHBAM PASSAGES (verified from Sefaria):\n" +
        "\n\n".join(fetched_rashbam)
    ) if fetched_rashbam else (
        "NOTE: No Rashbam text retrieved. Be explicit about working from memory."
        if not effective_loaded_ctx else ""
    )

    auto_fetched_verse = "\n\n".join(filter(None, [
        rashi_block, concordance_block, secondary_block
    ]))

    system, messages = agent.build_messages(
        user_message=user_message,
        conversation_history=history,
        sefaria_context=effective_loaded_ctx,
        auto_fetched_verse=auto_fetched_verse,
    )

    response = llm_client.complete(system=system, messages=messages)

    conv_store.append(session_id, agent_id, "user", user_message)
    conv_store.append(session_id, agent_id, "assistant", response)

    return {
        "agent_id": agent_id,
        "response": response,
        "clarification_needed": None,
        "retrieved_refs": all_verse_refs,
        "retrieved_rashi": all_rashi_refs,
        "retrieved_words": all_words,
    }


async def debate_turn(
    session_id: str,
    responding_agent_id: str,
    previous_agent_id: str,
    previous_response: str,
    original_question: str,
    loaded_ref: Optional[str] = None,
) -> dict:
    """Ask one agent to respond to what another agent just said."""
    responding_agent = get_agent(responding_agent_id)
    previous_agent = get_agent(previous_agent_id)

    debate_prompt = (
        f"The student asked: \"{original_question}\"\n\n"
        f"{previous_agent.name} responded as follows:\n"
        f"\"{previous_response}\"\n\n"
        f"Please respond in your own voice as {responding_agent.name}, "
        f"engaging directly with what {previous_agent.name} said — "
        f"noting where you agree, where you differ, and why."
    )

    return await ask(
        session_id=session_id,
        agent_id=responding_agent_id,
        user_message=debate_prompt,
        loaded_ref=loaded_ref,
    )
