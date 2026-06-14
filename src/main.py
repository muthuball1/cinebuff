"""FastAPI application tying together TMDB ingestion, embeddings, and conversational search."""

import logging
import time
from contextlib import asynccontextmanager

import requests
from anthropic import APIError
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

import chat
import database
import embeddings
import tmdb
import wikipedia_enrichment
from models import (
    ChatRequest,
    ChatResponse,
    EmbeddingsResponse,
    EnrichResponse,
    IngestRequest,
    IngestResponse,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_db()
    yield


app = FastAPI(title="CineBuff API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/admin/ingest", response_model=IngestResponse)
def ingest_movies(request: IngestRequest):
    """Fetch movies from TMDB (popular, top-rated, genre, or search) and upsert them into the catalog."""
    try:
        count = tmdb.ingest(
            source=request.source, query=request.query, pages=request.pages, genre=request.genre
        )
    except requests.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"TMDB request failed: {exc}") from exc
    return IngestResponse(movies_ingested=count)


@app.post("/admin/enrich", response_model=EnrichResponse)
def enrich_wikipedia():
    """Fetch Wikipedia summaries for any catalog movies not yet checked."""
    total = 0
    try:
        while True:
            n = wikipedia_enrichment.enrich_movies()
            total += n
            if n == 0:
                break
    except requests.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Wikipedia request failed: {exc}") from exc
    return EnrichResponse(movies_enriched=total)


@app.post("/admin/embeddings", response_model=EmbeddingsResponse)
def generate_embeddings():
    """Embed any catalog movies missing embeddings, draining the backlog in batches."""
    total = 0
    try:
        while True:
            n = embeddings.embed_movie_plots()
            total += n
            if n == 0:
                break
            time.sleep(embeddings.SECONDS_BETWEEN_BATCHES)
    except requests.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Voyage AI request failed: {exc}") from exc
    return EmbeddingsResponse(movies_embedded=total)


@app.post("/chat", response_model=ChatResponse)
def chat_endpoint(request: ChatRequest):
    """Handle one conversational turn: intent parsing, retrieval, and reply generation."""
    try:
        return chat.handle_chat(request.message, request.history)
    except APIError as exc:
        raise HTTPException(status_code=502, detail=f"Claude request failed: {exc}") from exc
    except requests.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Voyage AI request failed: {exc}") from exc
