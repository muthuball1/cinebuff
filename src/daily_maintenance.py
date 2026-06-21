"""Recurring catalog upkeep: pick up new TMDB releases, embed them, enrich with Wikipedia.

Meant to run on a daily cron schedule (see DEPLOY.md) so the catalog keeps growing
with real-world movie releases indefinitely without ever needing a manual bulk pass
again. Each run touches a small, bounded number of movies (whatever's newly out or
newly trending), so cost stays negligible no matter how long this runs for.
"""

import time

import embeddings
import tmdb
import wikipedia_enrichment

WIKIPEDIA_DAILY_CAP = 300


def run() -> None:
    counts = tmdb.refresh_new_releases()
    print(f"Ingested: {counts}")

    # Keywords → Wikipedia → embed, in that order, so each step's output feeds the next.
    # Once the initial keyword backfill is done, this only touches newly-added movies.
    kw_count = tmdb.backfill_keywords(limit=500)
    print(f"Fetched keywords for {kw_count} movies.")

    enriched = 0
    while enriched < WIKIPEDIA_DAILY_CAP:
        n = wikipedia_enrichment.enrich_movies(
            batch_size=min(wikipedia_enrichment.BATCH_SIZE, WIKIPEDIA_DAILY_CAP - enriched)
        )
        enriched += n
        if n == 0:
            break
    print(f"Wikipedia-checked {enriched} movies.")

    embedded = 0
    while True:
        n = embeddings.embed_movie_plots()
        embedded += n
        if n == 0:
            break
        time.sleep(embeddings.SECONDS_BETWEEN_BATCHES)
    print(f"Embedded {embedded} movies.")


if __name__ == "__main__":
    run()
