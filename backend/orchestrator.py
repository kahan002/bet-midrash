"""
orchestrator.py — routes questions to agents, manages multi-agent turns,
and runs self-directed retrieval before answering.

Process for ask():
  1. Extract relevant verse refs AND Rashi refs from the question (one LLM call)
  2. Fetch Rashbam + Rashi in parallel from Sefaria
  3. Build the agent response with all verified text injected + tool access
"""
import json
import asyncio
from typing import Optional
from .agents import get_agent, build_fetch_tool_schema, get_all_configs, list_agents as list_all_agents, MIDRASH_SOURCES
from .services import conversation as conv_store
from .services import sefaria as sefaria_svc
from .services import llm_client

# Updated to return both verses and rashi refs in one call.
# Rashi is fetched for every verse because ~50% of Rashbam is direct
# response to specific Rashi comments. Having the actual text lets the
# agent engage precisely rather than working from memory.
REF_EXTRACTION_SYSTEM = """You are a Torah reference extractor for a multi-commentator Torah study tool.

Given a question or message about Torah, return a JSON object with:
1. "verses" — array of objects, each with:
   - "ref": the specific biblical passage (e.g. "Exodus 22:1-2")
   - "confidence": "high" if certain, "low" if uncertain
   - "ambiguity": (only if confidence is "low") brief note on what is unclear
2. "rashi" — array of strings: corresponding Rashi refs (prefixed "Rashi on ")
3. "words" — key Hebrew words worth concordance lookup (max 3, unvowelised)
4. "clarification_needed": (only if any verse has confidence "low" or question is ambiguous)
   A single focused question asking the student to clarify — first person, warmly scholarly.
   Example: "Before I answer — when you ask about ben sorer u'moreh, do you mean the plain
   text of Deuteronomy 21, or the talmudic elaboration of this law?"

If the message is NOT a Torah question (e.g. a greeting, an introduction request, or
a meta question about the tool), return an empty result with no verses:
{"verses": [], "rashi": [], "words": []}

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

Example for non-Torah message (greeting, intro request, etc.):
{"verses": [], "rashi": [], "words": []}

Rules:
- Only include Torah passages (Genesis through Deuteronomy)
- Be specific — prefer "Exodus 22:1" over "Exodus 22"
- The rashi array should mirror the verses array (use ref strings)
- Return ONLY the JSON object, no other text"""

SUMMARIZATION_SYSTEM = """You are summarizing a Torah study conversation for context compression.
The conversation involves medieval Jewish commentators (Rashi, Rashbam, etc.) and a student.

Produce a concise summary that preserves:
- Which biblical passages were discussed, with exact references
- The specific scholarly position each commentator took on each passage
- Any explicit disagreements between commentators, and what they turned on
- The student's questions and the thread of inquiry

Do NOT preserve:
- Greetings, pleasantries, or meta-conversation about the tool
- Verbatim quotes (paraphrase positions instead)
- Repetition of points already well-established

Format: 2-4 short paragraphs. Be precise about verse references and attributions.
Write in third person: "Rashi argued that...", "Rashbam disagreed, reading...", "The student asked about..."."""


async def summarize_conversation(session_id: str, agent_names: dict[str, str]) -> str:
    """
    Compress the older portion of the conversation into a summary paragraph.
    Called when needs_summarization() returns True, before building the LLM messages.
    Returns the summary string (which is then stored via conv_store.set_summary).
    """
    messages = conv_store.get_shared_history(session_id)
    existing_summary = conv_store.get_summary(session_id)

    # Build a readable transcript for the summarizer
    lines = []
    if existing_summary:
        lines.append(f"[Earlier summary: {existing_summary}]")
        lines.append("")

    for msg in messages:
        if msg["role"] == "user":
            lines.append(f"Student: {msg['content']}")
        elif msg["role"] == "agent":
            name = agent_names.get(msg["agent_id"], msg["agent_id"])
            lines.append(f"{name}: {msg['content']}")

    transcript = "\n\n".join(lines)

    try:
        summary = llm_client.complete(
            system=SUMMARIZATION_SYSTEM,
            messages=[{"role": "user", "content": transcript}],
            max_tokens=600,
        )
        return summary.strip()
    except Exception as e:
        print(f"[summarization] failed: {e}")
        # Return a minimal fallback so the conversation can continue
        return f"[Summary unavailable — earlier conversation covered {len(messages)} exchanges]"


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
    silent: bool = False,
) -> dict:
    """
    Send a question to a single commentator agent.

    silent=True skips Turn 1 ref extraction — used for passage acknowledgments,
    agent introductions, and other system-generated messages that don't need
    verse identification. The agent answers directly from existing context.

    Step 1: Extract verse refs + Rashi refs (one LLM call) — skipped if silent
    Step 2: Fetch passages + Rashi in parallel from Sefaria — skipped if silent
    Step 3: Build agent response with all verified text injected
    """
    agent = get_agent(agent_id)

    # Build agent name map for conversation attribution
    all_agents = {c.id: c.name for c in get_all_configs()}

    # ── Summarize if conversation is getting long ─────────────────────────────
    summarized = False
    if conv_store.needs_summarization(session_id):
        summary = await summarize_conversation(session_id, all_agents)
        conv_store.set_summary(session_id, summary)
        summarized = True

    # Get shared conversation history formatted for this agent's LLM call
    history = conv_store.build_llm_messages(
        session_id,
        current_agent_id=agent_id,
        agent_names=all_agents,
    )

    # ── Silent path: skip extraction and fetching, answer directly ────────────
    if silent:
        system, messages = agent.build_messages(
            user_message=user_message,
            conversation_history=history,
            sefaria_context=None,
            auto_fetched_verse=None,
        )
        fetch_tool = build_fetch_tool_schema()
        all_configs = {c.id: c for c in get_all_configs()}
        async def tool_executor_silent(tool_name, tool_input):
            return await sefaria_svc.execute_fetch_tool(
                tool_input.get("ref", ""), tool_input.get("source", ""),
                all_configs, MIDRASH_SOURCES,
            )
        response, tool_calls = await llm_client.complete_with_tools(
            system=system, messages=messages,
            tools=[fetch_tool], tool_executor=tool_executor_silent,
        )
        conv_store.append_user(session_id, user_message)
        conv_store.append_agent(session_id, agent_id, response)
        return {
            "agent_id": agent_id,
            "response": response,
            "clarification_needed": None,
            "retrieved_refs": [],
            "retrieved_rashi": [],
            "retrieved_words": [],
            "tool_calls": tool_calls,
            "summarized": summarized,
        }

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
            p = await sefaria_svc.fetch_passage(
                ref,
                agent.config.sefaria_prefix,
                en_translation_prefs=agent.config.en_translation_prefs,
            )
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

    # ── Step 3: build context and generate response with tool access ──────────

    # Build the agent_configs dict for the tool executor
    all_configs = {c.id: c for c in get_all_configs()}

    async def tool_executor(tool_name: str, tool_input: dict) -> dict:
        """Execute a fetch_sefaria tool call from the agent."""
        if tool_name != "fetch_sefaria":
            return {"status": "error", "message": f"Unknown tool: {tool_name}"}
        ref    = tool_input.get("ref", "")
        source = tool_input.get("source", "")
        print(f"[tool] fetch_sefaria({ref!r}, {source!r})")
        return await sefaria_svc.execute_fetch_tool(ref, source, all_configs, MIDRASH_SOURCES)

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

    # ── Inject epistemic context about other agents in the conversation ───────
    # Tell each agent which other commentators it can historically have read,
    # so it frames cross-agent responses with appropriate epistemic humility.
    shared_history = conv_store.get_shared_history(session_id)
    other_agents_present = set(
        msg["agent_id"] for msg in shared_history
        if msg["role"] == "agent" and msg["agent_id"] != agent_id
    )
    if other_agents_present:
        can_read = set(agent.config.can_read)
        counterfactual = other_agents_present - can_read
        grounded = other_agents_present & can_read
        notes = []
        if grounded:
            names = [all_agents.get(a, a) for a in grounded]
            notes.append(
                f"You have historically read {', '.join(names)} — "
                f"you may engage with their comments directly."
            )
        if counterfactual:
            names = [all_agents.get(a, a) for a in counterfactual]
            notes.append(
                f"You could not have read {', '.join(names)} — "
                f"their commentary postdates you. When responding to their "
                f"comments in this conversation, acknowledge explicitly that "
                f"you are reasoning about what you would have said, not "
                f"claiming you actually read their work."
            )
        if notes:
            system += (
                "\n\n══ OTHER COMMENTATORS IN THIS CONVERSATION ══\n"
                + "\n".join(notes)
            )

    # Use tool-enabled completion for the agent response
    fetch_tool = build_fetch_tool_schema()
    response, tool_calls = await llm_client.complete_with_tools(
        system=system,
        messages=messages,
        tools=[fetch_tool],
        tool_executor=tool_executor,
    )

    conv_store.append_user(session_id, user_message)
    conv_store.append_agent(session_id, agent_id, response)

    # Collect refs fetched via tool calls for the frontend text pane
    tool_fetched_refs = [
        tc["ref"] for tc in tool_calls
        if tc.get("status") == "found" and tc.get("source") not in ("rashi", "bible")
    ]

    return {
        "agent_id": agent_id,
        "response": response,
        "clarification_needed": None,
        "retrieved_refs": all_verse_refs + tool_fetched_refs,
        "retrieved_rashi": all_rashi_refs,
        "retrieved_words": all_words,
        "tool_calls": tool_calls,
        "summarized": summarized,
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
