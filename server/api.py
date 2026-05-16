"""
FinDecide FastAPI Server
------------------------
Serves the web UI and exposes a streaming SSE endpoint for the reasoning loop.

Run with:  uvicorn server.api:app --reload --port 8000
"""

from __future__ import annotations

import json
import os
import asyncio
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from server.orchestrator import run_decision, DEFAULT_PROVIDER, GEMINI_MODEL, OLLAMA_MODEL, OLLAMA_BASE_URL, EXTERNAL_BASE_URL, EXTERNAL_PROVIDER, EXTERNAL_MODEL

app = FastAPI(title="FinDecide API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static web files
WEB_DIR = Path(__file__).parent.parent / "web"
if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


class DecisionRequest(BaseModel):
    question: str
    provider: Optional[Literal["gemini", "ollama", "external"]] = None  # None → use LLM_PROVIDER env var


@app.get("/", response_class=HTMLResponse)
async def root():
    index = WEB_DIR / "index.html"
    if index.exists():
        return HTMLResponse(content=index.read_text())
    return HTMLResponse("<h1>FinDecide API running. Open /static/index.html</h1>")


@app.post("/decide/stream")
async def decide_stream(req: DecisionRequest):
    """
    SSE endpoint — streams each reasoning step as a JSON event.
    The frontend listens with EventSource / fetch + ReadableStream.
    """
    queue: asyncio.Queue = asyncio.Queue()

    async def on_step(step: dict):
        await queue.put(step)

    async def runner():
        await run_decision(req.question, on_step=on_step, provider=req.provider)
        await queue.put(None)  # sentinel

    async def event_generator():
        asyncio.create_task(runner())
        while True:
            step = await queue.get()
            if step is None:
                yield "data: [DONE]\n\n"
                break
            yield f"data: {json.dumps(step)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/decide")
async def decide(req: DecisionRequest):
    """Non-streaming endpoint — returns full result at once."""
    result = await run_decision(req.question, provider=req.provider)
    return result


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "default_provider": DEFAULT_PROVIDER,
        "gemini_key_set": bool(os.environ.get("GEMINI_API_KEY")),
        "gemini_model": GEMINI_MODEL,
        "ollama_model": OLLAMA_MODEL,
        "ollama_base_url": OLLAMA_BASE_URL,
        "external_base_url": EXTERNAL_BASE_URL,
        "external_provider": EXTERNAL_PROVIDER,
        "external_model": EXTERNAL_MODEL,
    }
