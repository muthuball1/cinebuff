"""Generate embeddings for movie plots via Voyage AI and store them in pgvector.

Anthropic's Claude API does not expose an embeddings endpoint; Voyage AI is
Anthropic's recommended embeddings provider, so it's used here instead.
"""

import os
import time

import requests
from dotenv import load_dotenv

import database

load_dotenv()

VOYAGE_API_KEY = os.environ["VOYAGE_API_KEY"]
VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
MODEL = "voyage-3"  # 1024 dimensions, matches database.EMBEDDING_DIMENSIONS
# With a payment method on file, Voyage's per-request batch limit (128 inputs) is the
# binding constraint rather than RPM/TPM, so batches are sized against that instead.
BATCH_SIZE = 128
# Small politeness buffer between batches; the paid tier hasn't shown any throttling
# back-to-back, but this avoids hammering the API at full speed for no benefit.
SECONDS_BETWEEN_BATCHES = 0.2


def _embed(texts: list[str], input_type: str, max_retries: int = 4) -> list[list[float]]:
    """Call the Voyage AI embeddings API for a batch of texts, retrying on rate limits."""
    if not texts:
        return []

    for attempt in range(max_retries + 1):
        response = requests.post(
            VOYAGE_URL,
            headers={"Authorization": f"Bearer {VOYAGE_API_KEY}"},
            json={"input": texts, "model": MODEL, "input_type": input_type},
            timeout=30,
        )
        if response.status_code == 429 and attempt < max_retries:
            time.sleep(2 ** (attempt + 1))
            continue
        response.raise_for_status()
        break

    data = response.json()["data"]
    # Voyage echoes input order via "index", but sort defensively just in case.
    return [item["embedding"] for item in sorted(data, key=lambda item: item["index"])]


def embed_query(text: str) -> list[float]:
    """Embed a single search query (asymmetric "query" embedding, for retrieval)."""
    return _embed([text], input_type="query")[0]


WIKIPEDIA_SUMMARY_MAX_CHARS = 3000
KEYWORDS_MAX = 15


def _build_embedding_text(movie: dict) -> str:
    """Build embedding text: Wikipedia (or TMDB overview) + genres + TMDB keywords.

    Wikipedia summary leads when available so it dominates the semantic signal;
    TMDB overview is the fallback. Genres and keywords trail as structured tags.
    """
    wiki = movie.get("wikipedia_summary") or ""
    overview = movie.get("overview") or ""
    genres = movie.get("genres") or []
    keywords = (movie.get("tmdb_keywords") or [])[:KEYWORDS_MAX]

    plot_text = wiki[:WIKIPEDIA_SUMMARY_MAX_CHARS] if wiki else overview

    parts = [plot_text]
    if genres:
        parts.append(f"Genres: {', '.join(genres)}.")
    if keywords:
        parts.append(f"Keywords: {', '.join(keywords)}.")

    return " ".join(parts)


def embed_movie_plots(batch_size: int = BATCH_SIZE) -> int:
    """Embed and store plots for movies that don't have an embedding yet.

    Processes a single batch; call repeatedly to drain a larger backlog.
    Returns the number of movies embedded.
    """
    movies = database.get_movies_missing_embeddings(limit=batch_size)
    if not movies:
        return 0

    texts = [_build_embedding_text(movie) for movie in movies]
    vectors = _embed(texts, input_type="document")

    for movie, vector in zip(movies, vectors):
        database.set_embedding(movie["id"], vector)

    return len(movies)


if __name__ == "__main__":
    total = 0
    while True:
        n = embed_movie_plots()
        total += n
        if n == 0:
            break
        time.sleep(SECONDS_BETWEEN_BATCHES)
    print(f"Embedded {total} movies.")
