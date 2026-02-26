from dotenv import load_dotenv
load_dotenv()

import os
import traceback
import time
import asyncio
import uuid
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

# Model settings:
# - Primary runner uses root_agent (model diatur di my_agent/agent.py)
# - Fallback runner dibuat dari FALLBACK_MODEL (env), default flash
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL", "gemini-1.5-flash")

# Heuristic: kalau prompt terlalu panjang, langsung pakai fallback_runner (biasanya Pro)
# Kamu bisa set env ini kalau mau fine-tune.
PROMPT_LEN_USE_FALLBACK = int(os.getenv("PROMPT_LEN_USE_FALLBACK", "9000"))

# Timeout untuk panggilan model (Python-side). Pastikan layer Laravel/proxy juga diset cukup.
MODEL_TIMEOUT_SEC = int(os.getenv("MODEL_TIMEOUT_SEC", "170"))

# Retrieval controls
RECIPE_K_BASE = int(os.getenv("RECIPE_K_BASE", "5"))
RECIPE_K_BOOST = int(os.getenv("RECIPE_K_BOOST", "10"))
RECIPE_TOP_N = int(os.getenv("RECIPE_TOP_N", "8"))

GENERAL_K = int(os.getenv("GENERAL_K", "6"))  # dulu 10

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

def _normalize_session_id(session_id: Optional[str]) -> str:
    if not session_id or not str(session_id).strip():
        return str(uuid.uuid4())
    return str(session_id).strip()

# =========================
# Schemas (match Laravel payload)
# =========================
class HistoryMsg(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str

class ChatRequest(BaseModel):
    session_id: Optional[str] = None
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

        ctx_parts.append(f"[S{i}] source={source} page={page} chunk_id={chunk_id}\n{text}")

        cites.append(
            Citation(
                source=source,
                page=page,
                chunk_id=chunk_id,
                score=float(r.get("score") or 0.0),  # kalau tool kamu belum ada score, tetap aman
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
    except Exception as e:
        # create_session mungkin fail kalau session sudah ada; itu aman
        print(f"[WARN] create_session failed: {e}")

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
            last_text = text

        if event.is_final_response():
            break

    return last_text.strip()

async def call_agent_async(message: str, session_id: str, user_id: int) -> str:
    """
    Strategy:
    1) Kalau prompt panjang banget -> pakai fallback_runner langsung (biasanya lebih kuat/stabil).
    2) Normal -> pakai adk_runner.
    3) Kalau overload 503 -> switch ke fallback_runner.
    4) Kalau session missing -> recreate session and retry.
    """
    # Heuristic: prompt kepanjangan => langsung fallback
    if len(message) >= PROMPT_LEN_USE_FALLBACK:
        return await _run_with_runner(fallback_runner, message, session_id, user_id)

    try:
        return await _run_with_runner(adk_runner, message, session_id, user_id)

    except ValueError as e:
        if "Session not found" in str(e):
            print(f"[WARN] {e}. Re-creating session and retrying: {session_id}")
            await _ensure_adk_session(session_id, user_id)
            return await _run_with_runner(adk_runner, message, session_id, user_id)
        raise

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
    sid = _normalize_session_id(req.session_id)
    t0 = time.time()

    msg = (req.message or "").strip()
    q = msg.lower()

    is_recipe = any(w in q for w in [
        "resep", "alat", "bahan", "takaran", "langkah", "cara", "proses",
        "berapa gram", "berapa gr", "berapa ml", "sdm", "sdt", "kg", "gr", "ml",
        "rendam", "rebus", "masak", "kukus", "goreng", "oven", "kulkas", "hari"
    ])

    # =========================
    # 1) Retrieval
    # =========================
    if is_recipe:
        base_q = msg
        boost_q = f'({base_q}) AND (alat OR bahan OR takaran OR langkah OR "langkah-langkah" OR cara OR proses OR "alat" NEAR "bahan")'

        hits1 = search_report(base_q, k=RECIPE_K_BASE, source_like="%Resep%")
        hits2 = search_report(boost_q, k=RECIPE_K_BOOST, source_like="%Resep%")

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

        # TOP-N yang dikirim ke model harus kecil (biar cepat & fokus)
        hits = {"query": base_q, "results": merged[:RECIPE_TOP_N]}
        context, citations = _build_context(hits)

    else:
        hits = search_report(msg, k=GENERAL_K)
        context, citations = _build_context(hits)

    # Logging RAG
    print("[HIT] /chat", {
        "session_id": sid,
        "user_id": req.user_id,
        "msg_len": len(msg),
        "is_recipe": is_recipe,
        "chunks": len(hits.get("results", [])),
        "ctx_len": len(context),
    })

    # =========================
    # 2) Prompt
    # =========================
    if is_recipe:
        prompt = (
            "Gunakan REFERENSI untuk menjawab dan ekstrak resep.\n"
            "WAJIB patuh referensi. Jangan menambah info di luar referensi.\n"
            "Tuliskan semua langkah yang ada di referensi tanpa mengurangi atau menambahkan.\n"
            "Gunakan format berikut:\n"
            "Nama Resep:\n"
            "Alat dan Bahan:\n"
            "Langkah-langkah:\n\n"
            "=== REFERENSI ===\n"
            f"{context}\n"
            "=== END REFERENSI ===\n\n"
            f"Pertanyaan user: {msg}" 
        )
    else:
        prompt = (
            "Gunakan REFERENSI untuk menjawab pertanyaan user secara spesifik dan praktis.\n"
            "WAJIB patuh referensi. Jangan menambah info di luar referensi.\n"
            "Gunakan teks biasa tanpa simbol markdown seperti #, *, atau -.\n"
            "Jawab dengan ringkas namun tetap lengkap sesuai referensi.\n"
            "Jika referensi tidak cukup, tulis 'tidak ada di potongan referensi' lalu berhenti.\n"
            "Jika pertanyaan meminta langkah atau prosedur, susun secara berurutan menggunakan angka.\n"
            "Jika pertanyaan meminta strategi atau penjelasan, susun dalam paragraf yang jelas.\n\n"
            "=== REFERENSI ===\n"
            f"{context}\n"
            "=== END REFERENSI ===\n\n"
            f"Pertanyaan user: {msg}"
        )

    # =========================
    # 3) Call agent with safety timeout (FIXED INDENT)
    # =========================
    try:
        answer = await asyncio.wait_for(
            call_agent_async(
                message=prompt,
                session_id=sid,
                user_id=req.user_id,
            ),
            timeout=MODEL_TIMEOUT_SEC
        )
    except asyncio.TimeoutError:
        answer = "Pertanyaan membutuhkan analisis lebih dalam. Mohon tunggu atau sederhanakan pertanyaan."

    latency_ms = int((time.time() - t0) * 1000)

    return ChatResponse(
        answer=answer,
        citations=citations,
        meta={
            "latency_ms": latency_ms,
            "session_id": sid,
            "is_recipe": is_recipe,
            "chunks": len(hits.get("results", [])),
            "ctx_len": len(context),
            "timeout_sec": MODEL_TIMEOUT_SEC,
            "fallback_model": FALLBACK_MODEL,
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