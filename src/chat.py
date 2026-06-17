"""Conversational movie search: Claude for intent parsing + pgvector similarity + Claude for the reply."""

import os

from anthropic import Anthropic
from dotenv import load_dotenv

import database
import embeddings
from models import ChatMessage, ChatResponse, MovieRecommendation

load_dotenv()

MODEL = "claude-sonnet-4-6"

client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

SYSTEM_PROMPT = (
    "You are the movie-obsessed clerk at CineBuff, a video-rental-style discovery "
    "service. You help people find movies to watch based on what they describe, even "
    "when their request is vague or conversational (mood, themes, similar films, "
    "actors, etc.). Keep replies concise, warm, and conversational, like a "
    "knowledgeable clerk chatting with a regular customer. Never introduce yourself "
    "or give yourself a name. The chat UI renders plain text only, so never use "
    "markdown formatting (no **bold**, headers, or bullet/dash lists). Never use "
    "em-dashes (—); use a comma, period, or parentheses instead, it reads as overly "
    "AI-generated otherwise."
)

_SEARCH_TOOL = {
    "name": "search_movies",
    "description": (
        "Search the movie catalog for films matching what the user is looking for. "
        "Call this whenever the user describes a kind of movie they want, even loosely."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "A short natural-language description of the plot, theme, mood, or "
                    "style of movie to search for, written for semantic similarity matching."
                ),
            },
            "genres": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Specific genres the user mentioned, if any (e.g. ['Horror', 'Comedy']).",
            },
        },
        "required": ["query"],
    },
}

_SELECT_RECOMMENDED_TOOL = {
    "name": "select_recommended",
    "description": (
        "After writing your reply, call this to report exactly which catalog movies "
        "you named in it, so the app can show matching cards. List their ids in the "
        "order you mentioned them. Leave out any catalog movie you didn't actually "
        "name in the reply."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "movie_ids": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "The `id` of each catalog movie named in the reply, in mention order.",
            },
        },
        "required": ["movie_ids"],
    },
}


def _extract_search_intent(history: list[ChatMessage], message: str) -> dict | None:
    """Ask Claude to translate the conversation into a structured catalog search, if warranted."""
    messages = [{"role": m.role, "content": m.content} for m in history]
    messages.append({"role": "user", "content": message})

    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=messages,
        tools=[_SEARCH_TOOL],
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "search_movies":
            return block.input
    return None


def _compose_reply(history: list[ChatMessage], message: str, recommendations: list[dict]) -> tuple[str, list[int]]:
    """Ask Claude to write a conversational reply and report which retrieved movies it named.

    Returns (reply_text, movie_ids), where movie_ids are the database ids of the
    catalog movies Claude says it named, in the order it named them, as reported via
    the select_recommended tool. This is what the caller uses to pick which cards to
    show, so the cards are guaranteed to match what the reply actually talks about
    instead of being guessed from the reply text after the fact.
    """
    messages = [{"role": m.role, "content": m.content} for m in history]

    tools = []
    if recommendations:
        catalog_text = "\n".join(
            f"- id={m['id']}: {m['title']} ({m['release_date'].year if m['release_date'] else 'n/a'}): "
            f"{', '.join(m['genres'])}. {m['overview']}"
            for m in recommendations
        )
        context = (
            f"{message}\n\n"
            f"[Catalog search results for you to recommend from, ranked by relevance:\n{catalog_text}\n\n"
            "Only recommend movies from this list by name. Even if these feel like a weak "
            "match, don't name-drop other movies from your own knowledge to fill the gap, "
            "that's not something we can actually show or back up here. If none of these "
            "really fit, say so honestly and ask a follow-up instead of substituting your "
            "own suggestions. Once your reply is written, call select_recommended with the "
            "ids of just the movies you named.]"
        )
        tools = [_SELECT_RECOMMENDED_TOOL]
    else:
        context = (
            f"{message}\n\n"
            "[No catalog search results were found for this request. Don't name specific "
            "movies from your own knowledge since we can't actually show or back those up, "
            "just respond conversationally and, if it's a movie request, ask a follow-up "
            "to help narrow down another search.]"
        )

    messages.append({"role": "user", "content": context})

    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=messages,
        tools=tools,
    )
    reply_text = "".join(block.text for block in response.content if block.type == "text")
    movie_ids = []
    for block in response.content:
        if block.type == "tool_use" and block.name == "select_recommended":
            movie_ids = block.input.get("movie_ids", [])
    return reply_text, movie_ids


def handle_chat(message: str, history: list[ChatMessage]) -> ChatResponse:
    """Handle one turn of the CineBuff conversation: parse intent, retrieve, reply."""
    intent = _extract_search_intent(history, message)

    recommendations: list[dict] = []
    if intent and intent.get("query"):
        query_vector = embeddings.embed_query(intent["query"])
        recommendations = database.similarity_search(
            query_vector, limit=5, genres=intent.get("genres") or None
        )

    reply, movie_ids = _compose_reply(history, message, recommendations)

    by_id = {r["id"]: r for r in recommendations}
    shown = [by_id[i] for i in movie_ids if i in by_id]
    if not shown:
        # Claude didn't report any selection (e.g. it skipped the tool call) -
        # fall back to the top few by relevance rather than dumping every candidate.
        shown = recommendations[:3]

    return ChatResponse(
        reply=reply,
        recommendations=[MovieRecommendation(**r) for r in shown],
    )
