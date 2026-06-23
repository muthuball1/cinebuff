# CineBuff

A conversational movie discovery app. You describe what you're in the mood for,
in plain language, and it talks back like a knowledgeable video-rental clerk,
pulling real recommendations from a catalog of 20,000+ movies via semantic
search rather than keyword matching.

## How it works

1. **Intent parsing** — an LLM reads the conversation and decides whether (and
   what) to search for, extracting a natural-language query plus any genres
   mentioned.
2. **Retrieval** — that query is embedded (Voyage AI) and matched against the
   catalog's embeddings in Postgres via `pgvector` cosine similarity.
3. **Reply** — the LLM writes a conversational response grounded in the
   retrieved movies, instructed to only recommend titles actually in the
   results so the displayed cards always match what's said.

The catalog itself is built from TMDB (popular, top-rated, genre, decade, and
multi-language discovery passes) and enriched with Wikipedia plot summaries
where available, combined with the TMDB overview before embedding.

## Stack

- **Backend**: FastAPI, PostgreSQL + pgvector, Anthropic's API for chat,
  Voyage AI for embeddings
- **Frontend**: React + Vite, plain CSS (retro VHS/video-store theme)
- **Data**: TMDB API, Wikipedia's MediaWiki API
- **Deployment**: Docker Compose (Postgres, backend, frontend, Caddy for
  automatic HTTPS) — see [DEPLOY.md](DEPLOY.md)

## Running locally

Requirements: Python 3.12+, Node 20+, a PostgreSQL instance with the `vector`
extension available (e.g. the `pgvector/pgvector:pg16` Docker image).

```bash
cp .env.example .env   # fill in ANTHROPIC_API_KEY, TMDB_API_KEY, VOYAGE_API_KEY,
                        # POSTGRES_PASSWORD, and DATABASE_URL for your local Postgres

python -m venv venv
source venv/Scripts/activate   # or venv/bin/activate on macOS/Linux
pip install -r requirements.txt

cd src
uvicorn main:app --reload --port 8000
```

In another terminal:

```bash
cd frontend
npm install
npm run dev
```

The frontend talks to `http://127.0.0.1:8000` by default; visit the Vite dev
server URL it prints (usually `http://localhost:5173`).

## Building the catalog

```bash
cd src
python tmdb.py seed       # popular/top-rated/genre sweep
python tmdb.py expand     # decade- and language-based depth
python wikipedia_enrichment.py   # plot summary enrichment (rate-limited by Wikipedia)
python embeddings.py      # generate embeddings for anything missing one
```

`daily_maintenance.py` bundles a lighter version of this (new releases +
enrich + embed) meant to run on a schedule so the catalog keeps growing
indefinitely without a manual re-run.

## API

| Endpoint | Method | Purpose |
|---|---|---|
| `/chat` | POST | One conversational turn: `{message, history}` → `{reply, recommendations}` |
| `/health` | GET | Liveness check |
| `/admin/ingest` | POST | Trigger a TMDB ingestion pass |
| `/admin/enrich` | POST | Drain the Wikipedia enrichment backlog |
| `/admin/embeddings` | POST | Drain the embedding backlog |

The `/admin/*` endpoints are meant for operator use only — the included
deployment config blocks them from the public internet at the proxy layer.

## Deployment

See [DEPLOY.md](DEPLOY.md) for the full AWS EC2 + Docker Compose + Caddy setup,
including HTTPS via a free IP-based hostname (no domain purchase required).
