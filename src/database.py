"""PostgreSQL connection management and schema setup for CineBuff."""

import logging
import os
from contextlib import contextmanager

import numpy as np
import psycopg2
from psycopg2 import pool
from pgvector.psycopg2 import register_vector
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ["DATABASE_URL"]

# Dimensionality of Voyage AI's "voyage-3" embedding model (see embeddings.py).
EMBEDDING_DIMENSIONS = 1024

_pool = psycopg2.pool.SimpleConnectionPool(1, 10, dsn=DATABASE_URL)


@contextmanager
def get_connection():
    """Yield a pooled connection with pgvector types registered, committing on success."""
    conn = _pool.getconn()
    try:
        try:
            register_vector(conn)
        except psycopg2.ProgrammingError:
            pass  # vector extension not created yet; init_db() creates it on first run
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)


@contextmanager
def get_cursor():
    """Yield a cursor for a single transaction (see get_connection)."""
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            yield cur
        finally:
            cur.close()


def init_db():
    """Create the pgvector extension, movies table, and similarity index if missing."""
    with get_cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    with get_cursor() as cur:
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS movies (
                id SERIAL PRIMARY KEY,
                tmdb_id INTEGER UNIQUE NOT NULL,
                title TEXT NOT NULL,
                overview TEXT,
                release_date DATE,
                genres TEXT[] NOT NULL DEFAULT '{{}}',
                poster_path TEXT,
                vote_average REAL,
                popularity REAL,
                embedding VECTOR({EMBEDDING_DIMENSIONS}),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """
        )

    # NULL means "not yet looked up", '' means "looked up, no article found".
    with get_cursor() as cur:
        cur.execute("ALTER TABLE movies ADD COLUMN IF NOT EXISTS wikipedia_summary TEXT;")

    # NULL means "keywords not yet fetched from TMDB", '{}' means "fetched, none found".
    with get_cursor() as cur:
        cur.execute("ALTER TABLE movies ADD COLUMN IF NOT EXISTS tmdb_keywords TEXT[];")

    with get_cursor() as cur:
        cur.execute("ALTER TABLE movies ADD COLUMN IF NOT EXISTS vote_count INTEGER;")

    with get_cursor() as cur:
        cur.execute("ALTER TABLE movies ADD COLUMN IF NOT EXISTS original_language TEXT;")

    # HNSW indexes require pgvector >= 0.5.0; skip gracefully on older installs.
    try:
        with get_cursor() as cur:
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS movies_embedding_idx
                ON movies USING hnsw (embedding vector_cosine_ops);
                """
            )
    except psycopg2.Error as exc:
        logger.warning("Skipping HNSW index creation (pgvector may be outdated): %s", exc)


def upsert_movie(movie: dict) -> int:
    """Insert or update a movie by tmdb_id, returning its database id."""
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO movies (tmdb_id, title, overview, release_date, genres, poster_path,
                                vote_average, vote_count, popularity, original_language)
            VALUES (%(tmdb_id)s, %(title)s, %(overview)s, %(release_date)s, %(genres)s,
                    %(poster_path)s, %(vote_average)s, %(vote_count)s, %(popularity)s,
                    %(original_language)s)
            ON CONFLICT (tmdb_id) DO UPDATE SET
                title = EXCLUDED.title,
                overview = EXCLUDED.overview,
                release_date = EXCLUDED.release_date,
                genres = EXCLUDED.genres,
                poster_path = EXCLUDED.poster_path,
                vote_average = EXCLUDED.vote_average,
                vote_count = EXCLUDED.vote_count,
                popularity = EXCLUDED.popularity,
                original_language = EXCLUDED.original_language
            RETURNING id;
            """,
            movie,
        )
        return cur.fetchone()[0]


def get_movies_missing_details(limit: int = 500) -> list[dict]:
    """Return movies that are missing vote_count or original_language (pre-schema-update rows)."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT id, tmdb_id
            FROM movies
            WHERE vote_count IS NULL OR original_language IS NULL
            ORDER BY popularity DESC
            LIMIT %s;
            """,
            (limit,),
        )
        columns = [desc[0] for desc in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def set_movie_details(movie_id: int, vote_count: int, original_language: str) -> None:
    """Backfill vote_count and original_language for a movie."""
    with get_cursor() as cur:
        cur.execute(
            "UPDATE movies SET vote_count = %s, original_language = %s WHERE id = %s;",
            (vote_count, original_language, movie_id),
        )


def get_movies_missing_embeddings(limit: int = 100) -> list[dict]:
    """Return movies that have embeddable content but no embedding yet."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT id, title, overview, wikipedia_summary, genres,
                   COALESCE(tmdb_keywords, '{}') AS tmdb_keywords
            FROM movies
            WHERE embedding IS NULL
              AND (
                  (wikipedia_summary IS NOT NULL AND wikipedia_summary <> '')
                  OR (overview IS NOT NULL AND overview <> '')
              )
            ORDER BY popularity DESC
            LIMIT %s;
            """,
            (limit,),
        )
        columns = [desc[0] for desc in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def get_movies_missing_keywords(limit: int = 200) -> list[dict]:
    """Return movies that haven't had TMDB keyword lookup attempted yet."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT id, tmdb_id
            FROM movies
            WHERE tmdb_keywords IS NULL
            ORDER BY popularity DESC
            LIMIT %s;
            """,
            (limit,),
        )
        columns = [desc[0] for desc in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def set_keywords(movie_id: int, keywords: list[str]) -> None:
    """Store TMDB keywords for a movie (pass [] when the movie has no keywords)."""
    with get_cursor() as cur:
        cur.execute(
            "UPDATE movies SET tmdb_keywords = %s WHERE id = %s;",
            (keywords, movie_id),
        )


def get_movies_missing_wikipedia(limit: int = 100) -> list[dict]:
    """Return movies that haven't had a Wikipedia summary lookup attempted yet.

    Ordered by: English-language first, then vote_count DESC so the most
    culturally significant mainstream movies get enriched before niche/foreign ones.
    """
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT id, title, release_date
            FROM movies
            WHERE wikipedia_summary IS NULL
            ORDER BY
                CASE WHEN original_language = 'en' THEN 0 ELSE 1 END,
                COALESCE(vote_count, 0) DESC,
                popularity DESC
            LIMIT %s;
            """,
            (limit,),
        )
        columns = [desc[0] for desc in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def set_wikipedia_summary(movie_id: int, summary: str) -> None:
    """Store a movie's Wikipedia summary (pass '' when no article was found)."""
    with get_cursor() as cur:
        cur.execute(
            "UPDATE movies SET wikipedia_summary = %s WHERE id = %s;",
            (summary, movie_id),
        )


def clear_all_embeddings() -> int:
    """Null out every movie's embedding so embed_movie_plots() recomputes all of them.

    Used after a change to the embedding text basis (e.g. adding Wikipedia summaries)
    that requires re-embedding the whole catalog, not just newly-added movies.
    """
    with get_cursor() as cur:
        cur.execute("UPDATE movies SET embedding = NULL WHERE embedding IS NOT NULL;")
        return cur.rowcount


def set_embedding(movie_id: int, embedding: list[float]) -> None:
    """Store a computed embedding vector for a movie."""
    with get_cursor() as cur:
        cur.execute(
            "UPDATE movies SET embedding = %s WHERE id = %s;",
            (np.array(embedding, dtype=np.float32), movie_id),
        )


# Weight given to (normalized) popularity when re-ranking candidates, vs. semantic
# similarity. Pure similarity ranking surfaces obscure movies whose embedding text
# happens to line up tightly with the query; blending in popularity keeps results
# tilted toward movies people have actually heard of without ignoring relevance.
_POPULARITY_WEIGHT = 0.25

# How many nearest-neighbor candidates to pull before re-ranking by popularity.
# Wide enough that a well-known movie ranked slightly outside `limit` on pure
# similarity still has a chance to surface once popularity is factored in.
_CANDIDATE_MULTIPLIER = 6
_MIN_CANDIDATES = 30


def similarity_search(embedding: list[float], limit: int = 5, genres: list[str] | None = None) -> list[dict]:
    """Find movies closest to the query embedding, re-ranked to favor popular/mainstream matches.

    Pulls a wider pool of nearest neighbors by cosine similarity, then re-ranks that
    pool with a popularity blend so a slightly-more-obscure-but-closer embedding
    doesn't automatically beat a well-known, clearly-relevant movie.
    """
    vector = np.array(embedding, dtype=np.float32)
    genre_filter = "AND genres && %s" if genres else ""
    candidate_pool = max(limit * _CANDIDATE_MULTIPLIER, _MIN_CANDIDATES)
    params: tuple = (vector,)
    if genres:
        params += (genres,)
    params += (vector, candidate_pool)

    with get_cursor() as cur:
        cur.execute(
            f"""
            SELECT id, tmdb_id, title, overview, release_date, genres, poster_path, vote_average, popularity,
                   1 - (embedding <=> %s) AS similarity
            FROM movies
            WHERE embedding IS NOT NULL
            {genre_filter}
            ORDER BY embedding <=> %s
            LIMIT %s;
            """,
            params,
        )
        columns = [desc[0] for desc in cur.description]
        candidates = [dict(zip(columns, row)) for row in cur.fetchall()]

    if not candidates:
        return []

    max_popularity = max((c["popularity"] or 0) for c in candidates) or 1.0

    def _score(c: dict) -> float:
        popularity_score = (c["popularity"] or 0) / max_popularity
        return (1 - _POPULARITY_WEIGHT) * c["similarity"] + _POPULARITY_WEIGHT * popularity_score

    candidates.sort(key=_score, reverse=True)
    return candidates[:limit]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
    logger.info("Database initialized.")
