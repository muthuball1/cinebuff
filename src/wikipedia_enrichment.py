"""Enrich movies with Wikipedia plot summaries, combined with TMDB overviews for embedding.

Named wikipedia_enrichment (not wikipedia) to avoid shadowing the third-party
`wikipedia` PyPI package should it ever be added as a dependency.
"""

import re
import time
from concurrent.futures import ThreadPoolExecutor

import requests

import database

API_URL = "https://en.wikipedia.org/w/api.php"
# Wikimedia's API requires a descriptive User-Agent; generic/default ones can be throttled.
HEADERS = {"User-Agent": "CineBuff-MovieDiscovery/1.0 (educational project)"}
BATCH_SIZE = 200
SECONDS_BETWEEN_REQUESTS = 0.5
# Empirically, Wikipedia's rate limit is a hard ceiling around ~0.2 successful
# req/sec regardless of concurrency (measured: 8 workers got the same real
# throughput as 1, just with more requests wasted on exhausted retries). A small
# worker count avoids that waste without expecting it to outrun the ceiling.
WORKERS = 3

# "List of X films of Y" and filmography pages are common false-positive top search
# results for obscure titles; they're not about the movie itself, so skip them.
_NON_ARTICLE_PAGE = re.compile(r"^list of |filmography", re.IGNORECASE)

# Matches a Plot/Synopsis/Storyline section header and captures everything up to
# the next same-level section or end of text. exsectionformat=wiki gives headers
# as == Title == so we match on that pattern.
_PLOT_SECTION = re.compile(
    r"==\s*(?:Plot|Synopsis|Storyline|Story)\s*==\s*\n(.*?)(?=\n==\s|\Z)",
    re.DOTALL | re.IGNORECASE,
)


def _extract_plot(text: str) -> str | None:
    """Return the Plot section from a Wikipedia plain-text article, or None if absent."""
    m = _PLOT_SECTION.search(text)
    return m.group(1).strip() if m else None


def _get_with_retry(url: str, params: dict | None, max_retries: int = 4) -> requests.Response:
    """GET with exponential backoff on 429/5xx (raises after exhausting retries).

    Wikipedia's REST summary endpoint throttles well below what the polite
    inter-request delay alone accounts for, especially in sustained bulk runs.
    """
    response = None
    for attempt in range(max_retries + 1):
        response = requests.get(url, params=params, headers=HEADERS, timeout=10)
        if response.status_code == 429 or response.status_code >= 500:
            if attempt == max_retries:
                response.raise_for_status()
            time.sleep(2 ** (attempt + 1))
            continue
        return response
    return response


def fetch_movie_summary(title: str, year: int | None) -> str | None:
    """Look up a movie's Wikipedia summary by title and release year.

    Combines the search and extract lookup into a single API call (generator=search
    + prop=extracts) instead of a separate search-then-summary round trip — halving
    the request volume against Wikipedia's rate limit, which is the actual bottleneck
    (not CPU or our own request pacing). Quotes the title for an exact phrase match,
    and skips list/filmography/disambiguation pages that otherwise tend to outrank a
    real article for obscure titles.

    Returns None if no article is found. Raises requests.RequestException if
    Wikipedia couldn't be reached after retries — callers should leave the movie
    unmarked (NULL) in that case so it's retried later, rather than recording a
    false "no article" result that would never be retried again.
    """
    query = f'"{title}" {year} film' if year else f'"{title}" film'
    response = _get_with_retry(
        API_URL,
        {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": query,
            "gsrlimit": 3,
            "prop": "extracts|pageprops",
            "explaintext": 1,
            "exsectionformat": "wiki",  # section headers as == Title ==
            "exchars": 8000,            # enough to reach the Plot section
            "ppprop": "disambiguation",
        },
    )
    response.raise_for_status()
    pages = response.json().get("query", {}).get("pages", {})
    for page in pages.values():
        if _NON_ARTICLE_PAGE.search(page.get("title", "")):
            continue
        if "disambiguation" in page.get("pageprops", {}):
            continue
        extract = page.get("extract", "")
        if not extract:
            continue
        # Prefer the Plot section; fall back to the intro paragraph.
        plot = _extract_plot(extract)
        if plot:
            return plot
        intro = re.split(r"\n==", extract)[0].strip()
        if intro:
            return intro
    return None


def _process_one(movie: dict) -> None:
    """Look up and store one movie's Wikipedia summary; called concurrently by workers."""
    year = movie["release_date"].year if movie["release_date"] else None
    try:
        summary = fetch_movie_summary(movie["title"], year)
    except requests.RequestException:
        return
    database.set_wikipedia_summary(movie["id"], summary or "")
    time.sleep(SECONDS_BETWEEN_REQUESTS)


def enrich_movies(batch_size: int = BATCH_SIZE, workers: int = WORKERS) -> int:
    """Fetch and store Wikipedia summaries for movies not yet checked.

    Processes the batch across a thread pool, since each lookup is dominated by
    network wait time rather than CPU. Stores '' (not NULL) only on a confirmed
    "no article" result, so those aren't retried every run. Movies that fail due
    to a network/rate-limit error are left untouched (still NULL) so a later run
    retries them. Returns the number of movies attempted (matching
    get_movies_missing_wikipedia's batch, regardless of how many of those
    succeeded), so the caller's drain loop still terminates once the backlog is
    genuinely empty.
    """
    movies = database.get_movies_missing_wikipedia(limit=batch_size)
    if not movies:
        return 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        list(executor.map(_process_one, movies))
    return len(movies)


if __name__ == "__main__":
    import sys

    # Movies are processed popularity-first (see get_movies_missing_wikipedia), so
    # capping the run at N stops once the N most relevant movies are covered,
    # leaving the long-tail backlog to embed from TMDB overview text alone.
    max_total = int(sys.argv[1]) if len(sys.argv) > 1 else None

    total = 0
    while max_total is None or total < max_total:
        n = enrich_movies(batch_size=min(BATCH_SIZE, max_total - total) if max_total else BATCH_SIZE)
        total += n
        if n == 0:
            break
        print(f"Enriched {total} movies so far...")
    print(f"Done. Processed {total} movies.")
