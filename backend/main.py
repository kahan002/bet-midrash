"""
main.py — FastAPI application entry point.

Run locally:
    uvicorn main:app --reload --port 8000

Then open frontend/index.html in your browser.
"""
import uuid
import os
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Cookie, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from dotenv import load_dotenv
load_dotenv()

from .agents import list_agents, get_agent
from .orchestrator import ask, debate_turn
from .services import sefaria as sefaria_svc
from .services import conversation as conv_store


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Verify API key is present on startup.
    # Accepts LLM_API_KEY (provider-agnostic) or ANTHROPIC_API_KEY (legacy).
    if not os.environ.get("LLM_API_KEY") and not os.environ.get("ANTHROPIC_API_KEY"):
        raise EnvironmentError(
            "No LLM API key found. "
            "Set LLM_API_KEY in your .env file. "
            "See llm_client.py to configure which provider to use."
        )
    yield


app = FastAPI(title="Torah Study — Commentator Agents", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Session management ────────────────────────────────────────────────────────

def get_or_create_session(
    response: Response,
    session_id: Optional[str] = Cookie(default=None),
) -> str:
    if not session_id:
        session_id = str(uuid.uuid4())
        response.set_cookie("session_id", session_id, max_age=86400 * 30)
    return session_id


# ── Agents ────────────────────────────────────────────────────────────────────

@app.get("/api/agents")
def get_agents():
    """List all available commentator agents."""
    return {"agents": list_agents()}


# ── Sefaria ───────────────────────────────────────────────────────────────────

class PassageRequest(BaseModel):
    ref: str
    agent_id: str


@app.post("/api/passage")
async def get_passage(req: PassageRequest):
    """
    Fetch a passage from Sefaria for a given agent.
    Returns both the biblical text and the agent's commentary.
    """
    try:
        agent = get_agent(req.agent_id)
        result = await sefaria_svc.fetch_passage(
            req.ref, agent.config.sefaria_prefix
        )
        return result
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Chat ──────────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    agent_id: str
    message: str
    loaded_ref: Optional[str] = None


@app.post("/api/chat")
async def chat(
    req: ChatRequest,
    response: Response,
    session_id: Optional[str] = Cookie(default=None),
):
    """Send a message to a single commentator agent."""
    sid = get_or_create_session(response, session_id)
    try:
        result = await ask(
            session_id=sid,
            agent_id=req.agent_id,
            user_message=req.message,
            loaded_ref=req.loaded_ref,
        )
        return result
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Debate ────────────────────────────────────────────────────────────────────

class DebateRequest(BaseModel):
    responding_agent_id: str
    previous_agent_id: str
    previous_response: str
    original_question: str
    loaded_ref: Optional[str] = None


@app.post("/api/debate")
async def debate(
    req: DebateRequest,
    response: Response,
    session_id: Optional[str] = Cookie(default=None),
):
    """Ask one agent to respond to another's answer."""
    sid = get_or_create_session(response, session_id)
    try:
        result = await debate_turn(
            session_id=sid,
            responding_agent_id=req.responding_agent_id,
            previous_agent_id=req.previous_agent_id,
            previous_response=req.previous_response,
            original_question=req.original_question,
            loaded_ref=req.loaded_ref,
        )
        return result
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── History ───────────────────────────────────────────────────────────────────

@app.delete("/api/history/{agent_id}")
def clear_history(
    agent_id: str,
    response: Response,
    session_id: Optional[str] = Cookie(default=None),
):
    """Clear conversation history for one agent in this session."""
    sid = get_or_create_session(response, session_id)
    conv_store.clear(sid, agent_id)
    return {"cleared": True}


# ── Serve frontend ────────────────────────────────────────────────────────────
# Serves frontend/index.html at / in both local dev and production.
import os as _os
_frontend_dir = _os.path.join(_os.path.dirname(__file__), '..', 'frontend')
if _os.path.isdir(_frontend_dir):
    app.mount("/", StaticFiles(directory=_frontend_dir, html=True), name="frontend")
