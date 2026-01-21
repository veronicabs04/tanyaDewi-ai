from dotenv import load_dotenv
load_dotenv()

import os
import traceback
import time
from typing import List, Literal, Optional, Dict, Any

from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from google.genai import types
from google.adk.runners import Runner
from google.adk.sessions.in_memory_session_service import InMemorySessionService

from my_agent.agent import root_agent, make_agent
# kalau kamu sudah punya retrieval, nanti kita pakai:
# from my_agent.retrieval_tool import search_report


# =========================
# Config
# =========================
APP_TOKEN = os.getenv("APP_TOKEN")  # samakan dengan AGENT_TOKEN di Laravel
if not APP_TOKEN:
    print("[WARN] APP_TOKEN is not set. Set APP_TOKEN env var!")

ADK_APP_NAME = os.getenv("ADK_APP_NAME", "tanya_dewi")
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL", "gemini-1.5-flash")

# =========================
# FastAPI
# =========================
app = FastAPI(title="Tanya Dewi Agent API (Laravel Integrated)")

# ADK runner setup
adk_session_service = InMemorySessionService()
adk_runner = Runner(
    app_name=ADK_APP_NAME,
    agent=root_agent,
    session_service=adk_session_service,
)

fallback_agent = make_agent(FALLBACK_MODEL)
fallback_runner = Runner(
    app_name=ADK_APP_NAME,
    agent=fallback_agent,
    session_service=adk_session_service,
)

# =========================
# Auth: server-to-server token
# =========================
def verify_app_token(x_app_token: Optional[str] = Header(default=None)):
    if not APP_TOKEN:
        raise HTTPException(status_code=500, detail="APP_TOKEN not configured on agent server")
    if x_app_token != APP_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return True


# =========================
# Schemas (match Laravel payload)
# =========================
class HistoryMsg(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str

class ChatRequest(BaseModel):
    session_id: str
    user_id: int
    message: str
    history: List[HistoryMsg] = []

class Citation(BaseModel):
    source: str
    page: int
    chunk_id: str
    score: float
    excerpt: Optional[str] = None

class ChatResponse(BaseModel):
    answer: str
    citations: List[Citation] = []
    meta: Dict[str, Any] = {}


# =========================
# Helpers
# =========================
def _content_to_text(content: types.Content | None) -> str:
    if not content or not content.parts:
        return ""
    texts = []
    for part in content.parts:
        if part.text:
            texts.append(part.text)
    return "".join(texts).strip()

async def _ensure_adk_session(session_id: str, user_id: int):
    session = await adk_session_service.get_session(
        app_name=ADK_APP_NAME,
        user_id=str(user_id),
        session_id=session_id,
    )
    if not session:
        await adk_session_service.create_session(
            app_name=ADK_APP_NAME,
            user_id=str(user_id),
            session_id=session_id,
        )

def _is_overloaded_error(exc: Exception) -> bool:
    text = str(exc).lower()
    if "overload" in text or "unavailable" in text or "503" in text:
        return True
    status = getattr(exc, "status_code", None)
    if status == 503:
        return True
    code = getattr(exc, "code", None)
    if code == 503:
        return True
    return False

async def _run_with_runner(runner: Runner, message: str, session_id: str, user_id: int) -> str:
    await _ensure_adk_session(session_id, user_id)

    # OPTIONAL: kamu bisa inject context/history ke message kalau agent kamu butuh.
    # Tapi ADK punya session memory sendiri. Karena session_id sama, percakapan tetap nyambung.
    new_message = types.Content(role="user", parts=[types.Part(text=message)])

    final_text = ""
    async for event in runner.run_async(
        user_id=str(user_id),
        session_id=session_id,
        new_message=new_message,
    ):
        text = _content_to_text(event.content)
        if event.is_final_response() and text:
            final_text = text
            break

    return final_text.strip()

async def call_agent_async(message: str, session_id: str, user_id: int) -> str:
    try:
        return await _run_with_runner(adk_runner, message, session_id, user_id)
    except Exception as e:
        if not _is_overloaded_error(e):
            raise
        print(f"[WARN] model overload, switching to fallback model: {FALLBACK_MODEL}")
        return await _run_with_runner(fallback_runner, message, session_id, user_id)


# =========================
# Main endpoint (called by Laravel)
# =========================
@app.post("/chat", response_model=ChatResponse, dependencies=[Depends(verify_app_token)])
async def chat(req: ChatRequest):
    print("[HIT] /chat", {"session_id": req.session_id, "user_id": req.user_id, "msg_len": len(req.message)})
    t0 = time.time()

    # 1) (Optional) retrieval step
    citations: List[Citation] = []
    # Kalau kamu sudah punya tool retrieval, kamu bisa pakai di sini:
    # hits = search_report(req.message)
    # lalu bentuk context untuk agent + isi citations

    # 2) Call ADK agent
    answer = await call_agent_async(
        message=req.message,
        session_id=req.session_id,
        user_id=req.user_id,
    )

    latency_ms = int((time.time() - t0) * 1000)

    return ChatResponse(
        answer=answer,
        citations=citations,
        meta={
            "latency_ms": latency_ms,
            "session_id": req.session_id,
        },
    )


# =========================
# Debug handler
# =========================
@app.exception_handler(Exception)
async def debug_exception_handler(request, exc):
    return PlainTextResponse(
        "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        status_code=500
    )
