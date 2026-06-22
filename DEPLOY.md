# Deploying CineBuff to AWS

Single EC2 instance running three Docker Compose services: Postgres+pgvector, the
FastAPI backend, and an nginx-served frontend that reverse-proxies `/api/*` to the
backend (same-origin, no CORS configuration needed). Sized for sharing with a
handful of people, not production scale.

## 1. Launch the instance

- EC2, Ubuntu 22.04/24.04, t3.small or larger (Postgres + embeddings calls need more
  than t3.micro's 1GB RAM).
- Security group: allow inbound 22 (SSH, your IP only), 80 and 443 (HTTP/HTTPS, anywhere).
- Attach enough EBS storage (20GB+) — the movie catalog and pgvector index live on disk.

## 2. Install Docker

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# log out/in (or `newgrp docker`) for the group change to take effect
```

Docker Engine bundles the `docker compose` plugin already — no separate install needed.

## 3. Copy the project to the instance

```bash
scp -r . ubuntu@<instance-ip>:~/cinematch
```

(Or `git clone` if you push this to a repo — either way, `.env` itself should NOT be
committed/copied with real values; create it fresh on the server in the next step.)

## 4. Set real secrets

```bash
cd ~/cinematch
cp .env.example .env
nano .env   # fill in ANTHROPIC_API_KEY, TMDB_API_KEY, VOYAGE_API_KEY, POSTGRES_PASSWORD
```

## 5. Build and start

```bash
docker compose up -d --build
```

This starts Postgres (with the `vector` extension already available via the
`pgvector/pgvector:pg16` image), runs the backend (which calls `database.init_db()`
on startup to create the schema), and serves the frontend on port 80.

## 6. Load the catalog

**Don't re-run ingestion from scratch** — the local dev database already has the full
catalog with Wikipedia-enriched embeddings, which cost real wall-clock time to build
under Voyage's 3 RPM limit. Migrate that data instead of recomputing it:

```bash
# On your local machine: dump the existing catalog as plain SQL (not --format=custom —
# pg_dump's custom archive format isn't readable by an older pg_restore than the one
# that created it, and the `pgvector/pgvector:pg16` image only has a PG16 pg_restore;
# plain SQL via psql avoids that version coupling entirely)
pg_dump --no-owner --format=plain postgresql://postgres:password@localhost:5432/cinematch -f cinematch.sql

# If your local Postgres is newer than 16, strip the `SET transaction_timeout = 0;`
# header line (a PG17+-only setting that PG16's psql rejects):
grep -v '^SET transaction_timeout' cinematch.sql > cinematch_fixed.sql

gzip cinematch_fixed.sql
scp cinematch_fixed.sql.gz ubuntu@<instance-ip>:~/cinematch.sql.gz

# On the instance, once `docker compose up -d --build` has the db container running.
# The backend's init_db() already created an empty `movies` table on first boot, so
# drop it first or the dump's CREATE TABLE will fail:
docker compose exec -T db psql -U postgres -d cinematch -c 'DROP TABLE IF EXISTS movies CASCADE;'
gunzip -c ~/cinematch.sql.gz | docker compose exec -T db psql -U postgres -d cinematch -v ON_ERROR_STOP=1
```

Only fall back to the admin endpoints below if you genuinely want to (re)build the
catalog from nothing — `/admin/enrich` and `/admin/embeddings` drain their full
backlog internally, and at Voyage's 3 RPM free-tier limit that's tens of minutes to
hours depending on catalog size. They're also blocked from the public internet (see
nginx.conf), and `backend`/`frontend` don't publish ports to the host at all (only
`caddy` does — see step 7), so run them from inside the backend container itself:

```bash
docker compose exec backend curl -X POST http://localhost:8000/admin/ingest -H "Content-Type: application/json" -d '{"source":"popular","pages":25}'
docker compose exec backend curl -X POST http://localhost:8000/admin/enrich
# Long-running — launch detached so it survives the SSH session and isn't killed by
# a later `docker compose up`/`restart` of the backend container:
docker compose exec -d backend python embeddings.py
```

## 7. HTTPS via Caddy + nip.io

No need to own a domain: `nip.io` gives any IP a free, real hostname
(`<ip-with-dots>.nip.io`, e.g. `203.0.113.42.nip.io`) that resolves to that IP, and
Caddy auto-issues a real Let's Encrypt certificate for it on first request.

A `caddy` service in `docker-compose.yml` owns ports 80/443 publicly; `frontend` and
`backend` no longer publish ports to the host at all (only reachable over the
internal Docker network, by service name), so Caddy (and nginx behind it) are the
only way in. The `Caddyfile` at the repo root just has:

```
<instance-ip-with-dots>.nip.io {
	reverse_proxy frontend:80
}
```

Update it if the instance's IP ever changes, then `scp` it over and
`docker compose restart caddy`. Caddy auto-redirects HTTP to HTTPS and auto-renews
the cert.

**Note:** we initially tried `sslip.io` (the sibling service) and it failed with
ACME error `authorizations for these identifiers not valid` — that domain regularly
hits Let's Encrypt's shared rate limit due to its own popularity
([cunnie/sslip.io#108](https://github.com/cunnie/sslip.io/issues/108)). `nip.io` is
a separate domain with its own rate-limit bucket and worked immediately. If `nip.io`
ever has the same problem, any other "IP-in-hostname" wildcard DNS service is a
drop-in replacement, or switch to a real owned domain.

Security group needs port 443 open (in addition to 22 and 80) for this to work.

## 8. Visit it

`https://<instance-ip-with-dots>.nip.io/` — the chat UI talks to `/api/chat`, which
Caddy and nginx proxy through to the backend container on the same host.

## Notes / things to revisit before wider sharing

- **`/admin/*` is blocked at the nginx layer** (returns 403 publicly), and the
  backend/frontend containers aren't reachable from outside Docker at all anymore
  (see step 7) — so visitors can't trigger re-ingestion/re-embedding themselves.
  Run those from the instance via `docker compose exec backend ...` if you need them.
- **Voyage rate limit applies to live chat too** — a payment method on the Voyage
  account (dashboard.voyageai.com) raises it well past 3 RPM, worth doing before
  multiple people use the chat concurrently.
- **Single instance, no backups** — the Postgres volume (`pgdata`) is the only copy
  of the catalog. Fine for this scale; snapshot the EBS volume if you don't want to
  redo the ~hour of ingestion/embedding work after an instance loss.
