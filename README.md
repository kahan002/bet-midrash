# Torah Study — Commentator Agents

A multi-agent system for studying Torah with AI-powered commentators.
Each commentator is an object implementing a common interface; an
orchestrator manages routing and multi-agent debate turns.

## Project structure

```
torah-study/
├── backend/
│   ├── agents/
│   │   ├── __init__.py      # Registry — add new agents here
│   │   ├── base.py          # CommentatorAgent interface
│   │   └── rashbam.py       # RashbamAgent
│   ├── services/
│   │   ├── llm_client.py    # Thin LLM wrapper (swap providers here)
│   │   ├── sefaria.py       # Sefaria API
│   │   └── conversation.py  # Per-session conversation store
│   ├── orchestrator.py      # ask() and debate_turn()
│   └── main.py              # FastAPI app
├── frontend/
│   └── index.html           # Study UI (connects to backend API)
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
cd backend
uvicorn main:app --reload --port 8000

# 6. Open the frontend
# Open frontend/index.html in your browser
# Or: python -m http.server 3000 --directory frontend
# Then visit http://localhost:3000
```

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/agents | List all available commentator agents |
| POST | /api/passage | Fetch Sefaria text for a ref + agent |
| POST | /api/chat | Send a message to one agent |
| POST | /api/debate | Ask one agent to respond to another |
| DELETE | /api/history/{agent_id} | Clear conversation history |

## Adding a new commentator

1. Create `backend/agents/rashi.py` following the pattern in `rashbam.py`
2. Add `RashiAgent()` to the registry in `backend/agents/__init__.py`
3. That's it — the API and UI pick it up automatically

## Deployment (Render.com — free tier)

1. Push to a GitHub repo
2. Create a new Web Service on Render, point at the repo
3. Set build command: `pip install -r requirements.txt`
4. Set start command: `cd backend && uvicorn main:app --host 0.0.0.0 --port $PORT`
5. Add environment variable: `ANTHROPIC_API_KEY=sk-ant-...`
6. Deploy — share the URL with your educators/students

## Deployment (Railway)

```bash
# Install Railway CLI
npm i -g @railway/cli
railway login
railway init
railway up
# Set ANTHROPIC_API_KEY in the Railway dashboard
```
