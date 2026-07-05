"""FastAPI entrypoint.

Endpoints:
    GET  /                  -> serves the frontend (static/index.html)
    GET  /api/health        -> liveness check
    POST /api/research      -> kicks off a research run, streams progress via SSE

The agent itself lives in agent.py; this file is purely transport/wiring.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from .agent import ResearchAgent
from .config import get_settings
from .documents import DocumentIngestError, DocumentStore
from .schemas import DocumentInfo, ResearchRequest

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()

app = FastAPI(
    title="AI Research Agent",
    description="A small multi-step research agent built with FastAPI + LangGraph.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

document_store = DocumentStore()

_agent: ResearchAgent | None = None


def get_agent() -> ResearchAgent:
    """Lazily build the agent so a missing API key fails on first request, not at import time."""
    global _agent
    if _agent is None:
        if not settings.anthropic_api_key:
            raise HTTPException(
                status_code=500,
                detail="ANTHROPIC_API_KEY is not configured on the server.",
            )
        _agent = ResearchAgent(
            anthropic_api_key=settings.anthropic_api_key,
            tavily_api_key=settings.tavily_api_key,
            model=settings.claude_model,
            document_store=document_store,
        )
    return _agent


def _sse(event_type: str, payload: dict) -> str:
    return f"data: {json.dumps({'type': event_type, **payload})}\n\n"


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/documents", response_model=list[DocumentInfo])
def list_documents():
    return document_store.list_documents()


@app.post("/api/documents/upload", response_model=DocumentInfo)
async def upload_document(file: UploadFile = File(...)):
    raw = await file.read()
    try:
        chunk_count = document_store.add_document(file.filename, raw)
    except DocumentIngestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return DocumentInfo(filename=file.filename, chunks=chunk_count)


@app.delete("/api/documents/{filename}")
def delete_document(filename: str):
    document_store.delete_document(filename)
    return {"status": "deleted", "filename": filename}


@app.delete("/api/documents")
def clear_documents():
    document_store.clear()
    return {"status": "cleared"}


@app.post("/api/research")
async def research(req: ResearchRequest):
    agent = get_agent()
    max_iterations = min(req.max_iterations, settings.max_iterations_hard_cap)

    async def event_stream():
        try:
            yield _sse("status", {"message": f"Starting research on: {req.query}"})
            async for node_name, update in agent.astream_events(
                req.query, max_iterations, use_web=req.use_web, use_documents=req.use_documents
            ):
                for log_line in update.get("log", []):
                    yield _sse("status", {"node": node_name, "message": log_line})

                if node_name == "plan" and update.get("pending_queries"):
                    yield _sse("plan", {"queries": update["pending_queries"]})

                if node_name == "search" and update.get("search_results"):
                    results = [r.model_dump() for r in update["search_results"]]
                    yield _sse("search", {"results": results})

                if node_name == "synthesize" and update.get("report"):
                    yield _sse("report", {"report": update["report"]})

            yield _sse("done", {"message": "Research complete."})
        except Exception as exc:  # noqa: BLE001 - surface any failure to the client
            logger.exception("Research run failed")
            yield _sse("error", {"message": str(exc)})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# --- static frontend -------------------------------------------------------

_frontend_dir = Path(__file__).resolve().parent.parent.parent / "frontend"
if _frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")
