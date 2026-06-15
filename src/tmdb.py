"""Fetch movies from The Movie Database (TMDB) API and persist them via database.py."""

import os

import requests
from dotenv import load_dotenv

import database

load_dotenv()

TMDB_API_KEY = os.environ["TMDB_API_KEY"]
BASE_URL = "https://api.themoviedb.org/3"

_genre_map: dict[int, str] | None = None


def _get(path: str, **params) -> dict:
    response = requests.get(
        f"{BASE_URL}{path}",
        params={"api_key": TMDB_API_KEY, **params},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def _genre_id_to_name() -> dict[int, str]:
    """Fetch and cache the TMDB genre id -> name mapping."""
    global _genre_map
    if _genre_map is None:
        data = _get("/genre/movie/list")
        _genre_map = {genre["id"]: genre["name"] for genre in data["genres"]}
    return _genre_map


def _normalize(raw: dict) -> dict:
    """Convert a raw TMDB movie payload into the dict shape database.upsert_movie expects."""
    genre_map = _genre_id_to_name()
    return {
        "tmdb_id": raw["id"],
        "title": raw.get("title") or raw.get("original_title") or "Untitled",
        "overview": raw.get("overview") or None,
        "release_date": raw.get("release_date") or None,
        "genres": [genre_map[gid] for gid in raw.get("genre_ids", []) if gid in genre_map],
        "poster_path": raw.get("poster_path"),
        "vote_average": raw.get("vote_average"),
        "vote_count": raw.get("vote_count"),
        "popularity": raw.get("popularity"),
        "original_language": raw.get("original_language"),
    }


def fetch_popular_movies(pages: int = 1) -> list[dict]:
    """Fetch `pages` pages of currently popular movies from TMDB."""
    movies = []
    for page in range(1, pages + 1):
        data = _get("/movie/popular", page=page)
        movies.extend(data["results"])
    return movies


def fetch_top_rated_movies(pages: int = 1) -> list[dict]:
    """Fetch `pages` pages of TMDB's all-time top-rated movies."""
    movies = []
    for page in range(1, pages + 1):
        data = _get("/movie/top_rated", page=page)
        movies.extend(data["results"])
    return movies


def fetch_now_playing_movies(pages: int = 1) -> list[dict]:
    """Fetch `pages` pages of movies currently in theaters. Rotates as new films release."""
    movies = []
    for page in range(1, pages + 1):
        data = _get("/movie/now_playing", page=page)
        movies.extend(data["results"])
    return movies


def fetch_upcoming_movies(pages: int = 1) -> list[dict]:
    """Fetch `pages` pages of movies releasing soon. Catches new titles ahead of demand."""
    movies = []
    for page in range(1, pages + 1):
        data = _get("/movie/upcoming", page=page)
        movies.extend(data["results"])
    return movies


def _genre_name_to_id() -> dict[str, int]:
    return {name: gid for gid, name in _genre_id_to_name().items()}


def fetch_movies_by_genre(genre_name: str, pages: int = 1, min_votes: int = 100) -> list[dict]:
    """Fetch `pages` pages of popular, well-known movies in a given genre (e.g. 'Horror')."""
    genre_ids = _genre_name_to_id()
    if genre_name not in genre_ids:
        raise ValueError(f"Unknown genre: {genre_name!r}. Known genres: {sorted(genre_ids)}")

    movies = []
    for page in range(1, pages + 1):
        data = _get(
            "/discover/movie",
            with_genres=genre_ids[genre_name],
            sort_by="popularity.desc",
            page=page,
            # Filters out obscure/low-quality entries that otherwise flood genre discovery.
            **{"vote_count.gte": min_votes},
        )
        movies.extend(data["results"])
    return movies


def search_movies(query: str, pages: int = 1) -> list[dict]:
    """Search TMDB for movies matching `query`."""
    movies = []
    for page in range(1, pages + 1):
        data = _get("/search/movie", query=query, page=page)
        movies.extend(data["results"])
    return movies


def fetch_movies_by_year_range(start_year: int, end_year: int, pages: int = 1, min_votes: int = 50) -> list[dict]:
    """Fetch `pages` pages of well-reviewed movies released between start_year and end_year.

    Sorted by vote count rather than popularity, so this surfaces era-defining films that
    aren't currently trending (and so wouldn't show up in the popular/top-rated/genre lists).
    """
    movies = []
    for page in range(1, pages + 1):
        data = _get(
            "/discover/movie",
            sort_by="vote_count.desc",
            page=page,
            **{
                "primary_release_date.gte": f"{start_year}-01-01",
                "primary_release_date.lte": f"{end_year}-12-31",
                "vote_count.gte": min_votes,
            },
        )
        movies.extend(data["results"])
    return movies


def fetch_movies_by_language(language: str, pages: int = 1, min_votes: int = 20) -> list[dict]:
    """Fetch `pages` pages of popular movies originally in the given language (ISO 639-1 code).

    Adds non-English cinema that English-biased popularity/genre lists tend to under-represent.
    """
    movies = []
    for page in range(1, pages + 1):
        data = _get(
            "/discover/movie",
            with_original_language=language,
            sort_by="popularity.desc",
            page=page,
            **{"vote_count.gte": min_votes},
        )
        movies.extend(data["results"])
    return movies


def ingest(
    source: str = "popular",
    query: str | None = None,
    pages: int = 1,
    genre: str | None = None,
    start_year: int | None = None,
    end_year: int | None = None,
    language: str | None = None,
    min_votes: int | None = None,
) -> int:
    """Fetch movies from TMDB and upsert them into the database. Returns count ingested."""
    if source == "search":
        if not query:
            raise ValueError("query is required when source='search'")
        raw_movies = search_movies(query, pages=pages)
    elif source == "top_rated":
        raw_movies = fetch_top_rated_movies(pages=pages)
    elif source == "now_playing":
        raw_movies = fetch_now_playing_movies(pages=pages)
    elif source == "upcoming":
        raw_movies = fetch_upcoming_movies(pages=pages)
    elif source == "genre":
        if not genre:
            raise ValueError("genre is required when source='genre'")
        kwargs = {"min_votes": min_votes} if min_votes is not None else {}
        raw_movies = fetch_movies_by_genre(genre, pages=pages, **kwargs)
    elif source == "decade":
        if not start_year or not end_year:
            raise ValueError("start_year and end_year are required when source='decade'")
        kwargs = {"min_votes": min_votes} if min_votes is not None else {}
        raw_movies = fetch_movies_by_year_range(start_year, end_year, pages=pages, **kwargs)
    elif source == "language":
        if not language:
            raise ValueError("language is required when source='language'")
        kwargs = {"min_votes": min_votes} if min_votes is not None else {}
        raw_movies = fetch_movies_by_language(language, pages=pages, **kwargs)
    else:
        raw_movies = fetch_popular_movies(pages=pages)

    count = 0
    for raw in raw_movies:
        database.upsert_movie(_normalize(raw))
        count += 1
    return count


def seed_catalog(
    popular_pages: int = 100,
    top_rated_pages: int = 50,
    genre_pages: int = 15,
    genres: list[str] | None = None,
) -> dict[str, int]:
    """Ingest a broad, diverse seed catalog: top popular, top rated, and per-genre picks.

    Defaults to covering every TMDB genre (except "TV Movie") so the catalog is diverse
    rather than dominated by whatever a few lists happen to overlap on.
    """
    if genres is None:
        genres = [name for name in _genre_id_to_name().values() if name != "TV Movie"]

    counts = {}
    counts["popular"] = ingest(source="popular", pages=popular_pages)
    counts["top_rated"] = ingest(source="top_rated", pages=top_rated_pages)
    for genre_name in genres:
        counts[f"genre:{genre_name}"] = ingest(source="genre", genre=genre_name, pages=genre_pages)
    return counts


DECADES = [
    (1940, 1949), (1950, 1959), (1960, 1969), (1970, 1979), (1980, 1989),
    (1990, 1999), (2000, 2009), (2010, 2019), (2020, 2029),
]

LANGUAGES = [
    "ko", "ja", "fr", "es", "hi", "zh", "de", "it", "ru", "pt", "sv", "da",
    "tr", "pl", "th", "id", "nl", "no", "ta", "te",
]


def expand_catalog(decade_pages: int = 25, language_pages: int = 15) -> dict[str, int]:
    """Ingest additional depth beyond seed_catalog: movies by era and by original language.

    Popularity/genre sorting keeps surfacing the same currently-trending films across
    overlapping lists. This pulls in vote-count-ranked classics per decade and
    non-English-language cinema that those lists under-represent.
    """
    counts = {}
    for start_year, end_year in DECADES:
        counts[f"decade:{start_year}s"] = ingest(
            source="decade", start_year=start_year, end_year=end_year, pages=decade_pages
        )
    for language in LANGUAGES:
        counts[f"language:{language}"] = ingest(source="language", language=language, pages=language_pages)
    return counts


def deepen_catalog(genre_pages: int = 20, decade_pages: int = 20, language_pages: int = 15) -> dict[str, int]:
    """One-time pass at lower vote-count floors than seed/expand_catalog used.

    Same dimensions as before, just pulling further into the "still decent but not
    chart-topping" tier (genre 100->40 votes, decade 50->20, language 20->8 votes)
    rather than going all the way to near-zero-vote noise.
    """
    genres = [name for name in _genre_id_to_name().values() if name != "TV Movie"]
    counts = {}
    for genre_name in genres:
        counts[f"genre:{genre_name}"] = ingest(source="genre", genre=genre_name, pages=genre_pages, min_votes=40)
    for start_year, end_year in DECADES:
        counts[f"decade:{start_year}s"] = ingest(
            source="decade", start_year=start_year, end_year=end_year, pages=decade_pages, min_votes=20
        )
    for language in LANGUAGES:
        counts[f"language:{language}"] = ingest(
            source="language", language=language, pages=language_pages, min_votes=8
        )
    return counts


def backfill_movie_details(workers: int = 20) -> int:
    """Fetch and store vote_count + original_language for movies missing those fields.

    Both fields come from the standard movie detail endpoint. Uses high concurrency
    since TMDB rate limits are generous (~50 req/10s), making this a fast one-time pass.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    total = 0
    while True:
        batch = database.get_movies_missing_details(limit=500)
        if not batch:
            break

        def _fetch_and_store(movie):
            try:
                data = _get(f"/movie/{movie['tmdb_id']}")
                database.set_movie_details(
                    movie["id"],
                    data.get("vote_count") or 0,
                    data.get("original_language") or "en",
                )
                return 1
            except requests.HTTPError:
                return 0

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(_fetch_and_store, m) for m in batch]
            for f in as_completed(futures):
                try:
                    total += f.result()
                except Exception:
                    pass

        print(f"Details backfilled: {total} so far...")

    return total


def fetch_movie_keywords(tmdb_id: int) -> list[str]:
    """Fetch keyword names for a single movie from TMDB."""
    try:
        data = _get(f"/movie/{tmdb_id}/keywords")
        return [kw["name"] for kw in data.get("keywords", [])]
    except requests.HTTPError:
        return []


def backfill_keywords(workers: int = 10, limit: int | None = None) -> int:
    """Fetch and store TMDB keywords for all movies that haven't had them fetched yet.

    Safe to call repeatedly — only processes movies with tmdb_keywords IS NULL.
    Returns the total number of movies updated.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    total = 0
    while True:
        remaining = (limit - total) if limit is not None else 200
        if remaining <= 0:
            break
        batch = database.get_movies_missing_keywords(limit=min(200, remaining))
        if not batch:
            break

        def _fetch_and_store(movie):
            kws = fetch_movie_keywords(movie["tmdb_id"])
            database.set_keywords(movie["id"], kws)
            return 1

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(_fetch_and_store, m) for m in batch]
            for f in as_completed(futures):
                try:
                    total += f.result()
                except Exception:
                    pass

        print(f"Keywords fetched: {total} so far...")

        if limit is not None and total >= limit:
            break

    return total


def refresh_new_releases(now_playing_pages: int = 5, upcoming_pages: int = 5, popular_pages: int = 5) -> dict[str, int]:
    """Pick up movies that wouldn't have existed in any earlier ingestion pass.

    Meant to run on a recurring schedule (daily) so the catalog keeps growing with
    real-world releases indefinitely, at a small, predictable, near-zero-cost volume
    rather than one-off bulk passes. Existing movies are upserted (no duplicates).
    """
    return {
        "now_playing": ingest(source="now_playing", pages=now_playing_pages),
        "upcoming": ingest(source="upcoming", pages=upcoming_pages),
        "popular": ingest(source="popular", pages=popular_pages),
    }


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "seed":
        counts = seed_catalog()
        for key, value in counts.items():
            print(f"{key}: {value}")
        print(f"total fetched (pre-dedup by tmdb_id): {sum(counts.values())}")
    elif len(sys.argv) > 1 and sys.argv[1] == "expand":
        counts = expand_catalog()
        for key, value in counts.items():
            print(f"{key}: {value}")
        print(f"total fetched (pre-dedup by tmdb_id): {sum(counts.values())}")
    elif len(sys.argv) > 1 and sys.argv[1] == "deepen":
        counts = deepen_catalog()
        for key, value in counts.items():
            print(f"{key}: {value}")
        print(f"total fetched (pre-dedup by tmdb_id): {sum(counts.values())}")
    elif len(sys.argv) > 1 and sys.argv[1] == "refresh":
        counts = refresh_new_releases()
        for key, value in counts.items():
            print(f"{key}: {value}")
        print(f"total fetched (pre-dedup by tmdb_id): {sum(counts.values())}")
    elif len(sys.argv) > 1 and sys.argv[1] == "keywords":
        n = backfill_keywords()
        print(f"Done. Fetched keywords for {n} movies.")
    elif len(sys.argv) > 1 and sys.argv[1] == "details":
        n = backfill_movie_details()
        print(f"Done. Backfilled details for {n} movies.")
    else:
        n = ingest(source="popular", pages=1)
        print(f"Ingested {n} movies.")
