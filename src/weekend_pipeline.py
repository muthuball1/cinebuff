"""Full re-enrichment pipeline: backfill movie details, reset and re-fetch Wikipedia
plot sections (English/high-vote-count first), then clear and re-embed everything.

Designed to run detached on the production server over a weekend without any
manual intervention between steps.
"""

import time

import database
import embeddings
import tmdb
import wikipedia_enrichment


def run() -> None:
    # Step 1: backfill vote_count + original_language for all existing movies.
    # These were not stored before; the ordering of the Wikipedia step depends on them.
    print("Step 1: backfilling vote_count + original_language from TMDB...")
    n = tmdb.backfill_movie_details()
    print(f"Done. Updated {n} movies.")

    # Step 2: reset all existing Wikipedia summaries so they get re-fetched
    # using the new plot-section extraction (not the old intro-only extraction).
    # The empty-string sentinel '' (meaning "no article found") is left alone
    # so those movies aren't retried unnecessarily.
    print("Step 2: resetting existing Wikipedia summaries for re-fetch...")
    with database.get_cursor() as cur:
        cur.execute(
            "UPDATE movies SET wikipedia_summary = NULL "
            "WHERE wikipedia_summary IS NOT NULL AND wikipedia_summary <> '';"
        )
        reset = cur.rowcount
    print(f"Reset {reset} summaries. They will be re-fetched with plot text.")

    # Step 3: Wikipedia enrichment — processes all NULL entries, English +
    # high-vote-count movies first, until the backlog is empty.
    print("Step 3: re-enriching Wikipedia (plot sections, English-first by vote_count)...")
    enriched = 0
    while True:
        n = wikipedia_enrichment.enrich_movies()
        enriched += n
        if n == 0:
            break
        print(f"Wikipedia-enriched {enriched} so far...")
    print(f"Wikipedia enrichment done. Processed {enriched} movies.")

    # Step 4: clear all existing embeddings so everything gets re-embedded
    # with the new text format (plot-section Wikipedia + genres + keywords).
    print("Step 4: clearing all embeddings for full re-embed...")
    cleared = database.clear_all_embeddings()
    print(f"Cleared {cleared} embeddings.")

    # Step 5: re-embed the full catalog.
    print("Step 5: re-embedding all movies...")
    embedded = 0
    while True:
        n = embeddings.embed_movie_plots()
        embedded += n
        if n == 0:
            break
        time.sleep(embeddings.SECONDS_BETWEEN_BATCHES)
        print(f"Embedded {embedded} so far...")
    print(f"Done. Re-embedded {embedded} movies total.")


if __name__ == "__main__":
    run()
