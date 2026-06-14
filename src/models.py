"""Pydantic data models shared across the CineBuff API."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class Movie(BaseModel):
    """A movie record as stored in the database."""

    id: int
    tmdb_id: int
    title: str
    overview: str | None = None
    release_date: date | None = None
    genres: list[str] = Field(default_factory=list)
    poster_path: str | None = None
    vote_average: float | None = None
    popularity: float | None = None


class MovieRecommendation(Movie):
    """A movie returned from a similarity search, with its match score."""

    similarity: float


class ChatMessage(BaseModel):
    """A single turn in the conversation, sent by the client as history."""

    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = Field(default_factory=list)


class ChatResponse(BaseModel):
    reply: str
    recommendations: list[MovieRecommendation] = Field(default_factory=list)


class IngestRequest(BaseModel):
    """Trigger TMDB ingestion: popular, top-rated, a genre, or a search query."""

    source: Literal["popular", "search", "top_rated", "genre"] = "popular"
    query: str | None = None
    genre: str | None = None
    pages: int = Field(default=1, ge=1, le=30)


class IngestResponse(BaseModel):
    movies_ingested: int


class EmbeddingsResponse(BaseModel):
    movies_embedded: int


class EnrichResponse(BaseModel):
    movies_enriched: int
