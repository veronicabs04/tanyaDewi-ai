from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
import requests
import uuid
from my_agent.agent import root_agent
from google.adk.runners import Runner
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.genai import types
from my_agent.app.storage import DatabaseSessionService

LARAVEL_BASE_URL = "http://127.0.0.1:8000"  # kalau docker service name 'laravel'
app = FastAPI(title="Tanya Dewi API")
db_session = DatabaseSessionService(db_path="data/chat.db")
ADK_APP_NAME = "tanya_dewi"
adk_session_service = InMemorySessionService()
adk_runner = Runner(
    app_name=ADK_APP_NAME,
    agent=root_agent,
    session_service=adk_session_service,
)

security = HTTPBearer()
DEV_MODE = True
DEV_TOKEN ="secrettoken"

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    
    if DEV_MODE and token == DEV_TOKEN:
        return {"id": 1, "email": "dev@local", "token": token}
    try:
        r = requests.get(
            f"{LARAVEL_BASE_URL}/api/me",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
    except requests.RequestException:
        raise HTTPException(status_code=503, detail="Laravel auth service unreachable")

    if r.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    data = r.json()
    user = data.get("user")
    if not user or "id" not in user:
        raise HTTPException(status_code=401, detail="Cannot resolve user")

    return {"id": user["id"], "email": user.get("email"), "token": token}

@app.get("/debug/auth")
def debug_auth(credentials: HTTPAuthorizationCredentials = Depends(security)):
    return {
        "scheme": credentials.scheme,
        "token": credentials.credentials,
        "dev_mode": DEV_MODE,
        "dev_token": DEV_TOKEN,
        "dev_match": credentials.credentials == DEV_TOKEN,
    }


class ChatSendRequest(BaseModel):
    conversation_id: str
    message: str

@app.post("/chat/start")
def start_chat(current_user=Depends(get_current_user)):
    cid = str(uuid.uuid4())
    db_session.create_session(cid, user_id=current_user["id"])
    adk_session_service.create_session_sync(
        app_name=ADK_APP_NAME,
        user_id=str(current_user["id"]),
        session_id=cid,
    )
    return {"conversation_id": cid}

def _content_to_text(content: types.Content | None) -> str:
    if not content or not content.parts:
        return ""
    texts = []
    for part in content.parts:
        if part.text:
            texts.append(part.text)
    return "".join(texts).strip()

async def _ensure_adk_session(conversation_id: str, user_id: int):
    session = await adk_session_service.get_session(
        app_name=ADK_APP_NAME,
        user_id=str(user_id),
        session_id=conversation_id,
    )
    if not session:
        await adk_session_service.create_session(
            app_name=ADK_APP_NAME,
            user_id=str(user_id),
            session_id=conversation_id,
        )

async def call_agent_async(message: str, conversation_id: str, user_id: int):
    await _ensure_adk_session(conversation_id, user_id)
    new_message = types.Content(
        role="user",
        parts=[types.Part(text=message)],
    )
    final_text = ""
    async for event in adk_runner.run_async(
        user_id=str(user_id),
        session_id=conversation_id,
        new_message=new_message,
    ):
        text = _content_to_text(event.content)
        if text:
            final_text = text
        if event.is_final_response() and text:
            final_text = text
    return final_text

@app.post("/chat/send")
async def chat_send(payload: ChatSendRequest, current_user=Depends(get_current_user)):
    if not db_session.session_belongs_to_user(payload.conversation_id, current_user["id"]):
        raise HTTPException(status_code=403, detail="Forbidden")

    db_session.add_message(payload.conversation_id, role="user", content=payload.message)

    reply = await call_agent_async(
        payload.message,
        payload.conversation_id,
        current_user["id"],
    )

    db_session.add_message(payload.conversation_id, role="assistant", content=reply)

    return {"conversation_id": payload.conversation_id, "reply": reply}

@app.get("/debug/agent")
def debug_agent():
    keys = [k for k in dir(root_agent) if k in ("chat","generate","call","respond","complete","__call__","run","invoke")]
    return {"type": str(type(root_agent)), "candidates": keys}


@app.get("/chat/{conversation_id}/history")
def history(conversation_id: str, current_user=Depends(get_current_user)):
    if not db_session.session_belongs_to_user(conversation_id, current_user["id"]):
        raise HTTPException(status_code=403, detail="Forbidden")
    msgs = db_session.get_messages(conversation_id)
    return {"conversation_id": conversation_id, "messages": msgs}

import traceback
from fastapi.responses import PlainTextResponse

@app.exception_handler(Exception)
async def debug_exception_handler(request, exc):
    return PlainTextResponse(
        "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        status_code=500
    )
