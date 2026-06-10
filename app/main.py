"""
FastAPI backend for the RCA agent.
Endpoints:
  POST /chat        — send a message, get a response
  POST /session     — create a new session
  GET  /health      — health check
  GET  /            — serves the frontend HTML
"""

import uuid
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.agent import chat, create_session
from app.database import get_connection 

app = FastAPI(title="RCA Agent API", version="1.0.0")


@app.on_event("startup")
async def startup():
    # Pre-warm DuckDB so first query isn't slow
    get_connection()
    print("[API] Database pre-warmed.")


@app.post("/session")
def new_session():
    """Create a new conversation session."""
    session_id = str(uuid.uuid4())
    create_session(session_id)
    return {"session_id": session_id}


@app.post("/chat")
async def chat_endpoint(request: Request):
    """Send a message and get the agent's response."""
    body = await request.json()
    session_id = body.get("session_id")
    message = body.get("message")

    if not session_id or not message:
        return JSONResponse(
            status_code=400,
            content={"error": "Both session_id and message are required."}
        )

    try:
        response = chat(session_id, message)
        return {"session_id": session_id, "response": response}
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": str(e), "session_id": session_id}
        )


@app.get("/", response_class=HTMLResponse)
def frontend():
    """Serve the minimal chat frontend."""
    with open("frontend/index.html", "r") as f:
        return f.read()
