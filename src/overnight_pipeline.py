"""One-time, self-contained run: finish Wikipedia enrichment, then clear and fully
re-embed the catalog so every embedding reflects the latest text (overview + wiki
summary where found). Meant to run detached on the production server so it finishes
without needing the local machine or an active chat session to trigger each step.
"""

import time

import database
import embeddings
import wikipedia_enrichment

WIKIPEDIA_TARGET = 5000


def run() -> None:
    enriched = 0
    while enriched < WIKIPEDIA_TARGET:
        n = wikipedia_enrichment.enrich_movies(
            batch_size=min(wikipedia_enrichment.BATCH_SIZE, WIKIPEDIA_TARGET - enriched)
        )
        enriched += n
        if n == 0:
            break
        print(f"Wikipedia-checked {enriched} so far...")
    print(f"Wikipedia enrichment done. Checked {enriched} movies.")

    cleared = database.clear_all_embeddings()
    print(f"Cleared {cleared} embeddings for a full re-embed.")

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
