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
from my_agent.retrieval_tool import search_report

# =========================
# Config
# =========================
APP_TOKEN = os.getenv("APP_TOKEN")
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

def _build_context(hits: dict) -> tuple[str, List[Citation]]:
    ctx_parts: List[str] = []
    cites: List[Citation] = []

    for i, r in enumerate(hits.get("results", []), start=1):
        text = (r.get("text") or "").strip()
        source = r.get("source") or ""
        page = int(r.get("page") or 0)
        chunk_id = str(r.get("chunk_id") or "")

        if not text:
            continue

        ctx_parts.append(
            f"[S{i}] source={source} page={page} chunk_id={chunk_id}\n{text}"
        )

        cites.append(
            Citation(
                source=source,
                page=page,
                chunk_id=chunk_id,
                score=0.0,  # belum ada score dari tool kamu
                excerpt=text[:240],
            )
        )

    ctx = "\n\n".join(ctx_parts)
    return ctx, cites

def _content_to_text(content: types.Content | None) -> str:
    if not content or not content.parts:
        return ""
    texts = []
    for part in content.parts:
        if part.text:
            texts.append(part.text)
    return "".join(texts).strip()

async def _ensure_adk_session(session_id: str, user_id: int):
    try:
        await adk_session_service.create_session(
         app_name=ADK_APP_NAME,
            user_id=str(user_id),
            session_id=session_id,   
        )
    except Exception:
        pass
    # session = await adk_session_service.get_session(
    #     app_name=ADK_APP_NAME,
    #     user_id=str(user_id),
    #     session_id=session_id,
    # )
    # if not session:
    #     await adk_session_service.create_session(
    #         app_name=ADK_APP_NAME,
    #         user_id=str(user_id),
    #         session_id=session_id,
    #     )

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

    new_message = types.Content(role="user", parts=[types.Part(text=message)])

    last_text = ""
    async for event in runner.run_async(
        user_id=str(user_id),
        session_id=session_id,
        new_message=new_message,
    ):
        text = _content_to_text(event.content)
        if text:
            last_text = text  # simpan teks terakhir yang valid

        # Jangan nunggu final response yang harus ada text.
        # Banyak kasus final event berisi function_call/tool-result.
        if event.is_final_response():
            break

    return last_text.strip()


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

    q = req.message.lower()
    is_recipe = any(w in q for w in [
        "resep", "alat", "bahan", "takaran", "langkah", "cara", "proses",
        "berapa gram", "berapa gr", "berapa ml", "sdm", "sdt", "kg", "gr", "ml",
        "rendam", "rebus", "masak", "kukus", "goreng", "oven", "kulkas", "hari"
    ])

    # 1) Retrieval
    if is_recipe:
        base_q = req.message
        boost_q = req.message + ' "alat" "bahan" "alat & bahan" "alat dan bahan" langkah "langkah-langkah"'

        hits1 = search_report(base_q, k=8)
        hits2 = search_report(boost_q, k=20)

        merged, seen = [], set()
        for r in (hits1.get("results", []) + hits2.get("results", [])):
            key = r.get("chunk_id") or (r.get("source"), r.get("page"), (r.get("text") or "")[:80])
            if key in seen:
                continue
            seen.add(key)
            merged.append(r)

        def _recipe_boost(item: dict) -> int:
            t = (item.get("text") or "").lower()
            s = 0
            if "alat & bahan" in t or "alat dan bahan" in t:
                s += 5
            if "langkah" in t:
                s += 5
            return s

        merged.sort(key=_recipe_boost, reverse=True)
        hits = {"query": base_q, "results": merged[:15]}
        context, citations = _build_context(hits)
    else:
        hits = search_report(req.message, k=10)
        context, citations = _build_context(hits)

    # 2) Prompt
    if is_recipe:
        prompt = (
            "Gunakan REFERENSI untuk menjawab dan ekstrak resep.\n"
            "Format:\n"
            "## Manisan Pala Basah\n"
            "- Alat & Bahan:\n"
            "- Langkah-langkah:\n"
            "## Manisan Pala Kering\n"
            "- Alat & Bahan:\n"
            "- Langkah-langkah:\n"
            "Jika ada bagian yang tidak ada di potongan referensi, tulis 'tidak ada di potongan referensi'.\n"
            "Jangan bilang 'tidak ditemukan' kalau ada referensi relevan.\n\n"
            "=== REFERENSI ===\n"
            f"{context}\n"
            "=== END REFERENSI ===\n\n"
            f"Pertanyaan user: {req.message}"
        )
    else:
        prompt = (
            "Gunakan REFERENSI untuk menjawab pertanyaan user secara spesifik dan praktis.\n"
            "Jika pertanyaan minta langkah/prosedur, berikan langkah. Jika minta strategi/penjelasan, berikan poin-poin.\n"
            "Jangan bilang 'tidak ditemukan' kalau ada referensi relevan.\n\n"
            "=== REFERENSI ===\n"
            f"{context}\n"
            "=== END REFERENSI ===\n\n"
            f"Pertanyaan user: {req.message}"
        )

    # 3) Call agent
    answer = await call_agent_async(
        message=prompt,
        session_id=req.session_id,
        user_id=req.user_id,
    )

    latency_ms = int((time.time() - t0) * 1000)

    return ChatResponse(
        answer=answer,
        citations=citations,
        meta={"latency_ms": latency_ms, "session_id": req.session_id},
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
