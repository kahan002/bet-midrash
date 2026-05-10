# Torah Study — Commentator Agents
A multi-agent system for studying Torah with AI-powered commentators.
Each commentator is an object implementing a common interface; an
orchestrator manages routing and conversation history.

**Live:** https://bet-midrash.onrender.com

## Project structure
```
torah-study/
├── backend/
│   ├── agents/
│   │   ├── __init__.py      # Unified SOURCES registry + agent registry
│   │   ├── base.py          # CommentatorAgent interface + AgentConfig
│   │   ├── rashi.py         # RashiAgent
│   │   ├── rashbam.py       # RashbamAgent
│   │   └── ibn_ezra.py      # IbnEzraAgent (inc. HaKatzar variant)
│   ├── services/
│   │   ├── llm_client.py    # Thin LLM wrapper (swap providers here)
│   │   ├── sefaria.py       # Sefaria API + execute_fetch_tool
│   │   └── conversation.py  # Shared conversation store with summarization
│   ├── orchestrator.py      # ask() — extraction, fetch, agent response
│   └── main.py              # FastAPI app
├── frontend/
│   └── index.html           # Study UI (single-file vanilla JS)
├── render.yaml              # Render deployment config
├── requirements.txt
├── .env.example
└── README.md
```

## Local setup
```bash
# 1. Clone and enter the project
cd torah-study

# 2. Create a virtual environment
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set your API key
cp .env.example .env
# Edit .env and paste your Anthropic API key

# 5. Start the backend
uvicorn backend.main:app --reload --port 8000

# 6. Open http://localhost:8000
```

## API endpoints
| Method | Path | Description |
|--------|------|-------------|
| GET | /api/agents | List all available commentator agents |
| POST | /api/passage | Fetch Sefaria text for a ref + agent |
| POST | /api/chat | Send a message to one agent |

## Adding a new commentator
1. Create `backend/agents/yourname.py` following the pattern in `rashbam.py`
2. Add the agent to `_REGISTRY` in `backend/agents/__init__.py`
3. Add source metadata to `SOURCES` in `backend/agents/__init__.py`
4. Add a static fallback radio button in `frontend/index.html`

The tool schema, agent list API, and frontend switcher all update automatically.

## Deployment (Render)
The repo includes `render.yaml` which configures everything automatically.

1. Push to GitHub
2. Create a new Web Service on Render, point at the repo
3. Add environment variable: `ANTHROPIC_API_KEY=sk-ant-...`
4. Deploy — Render auto-deploys on every push to main

**Optional environment variable:**
- `LLM_MODEL` — override the default model (currently `claude-sonnet-4-6`)
