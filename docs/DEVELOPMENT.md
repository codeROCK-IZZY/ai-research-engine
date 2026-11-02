# Development guide

## Prerequisites
Python 3.12+, Node 22, PostgreSQL 16 with the **pgvector** extension, and **Tesseract OCR** (with `fra` and `spa` language data). On Debian/Ubuntu:

```bash
sudo apt-get install -y postgresql-16-pgvector tesseract-ocr tesseract-ocr-fra tesseract-ocr-spa
```

## Database
pgvector must be enabled by a superuser once per database. The migration also runs `CREATE EXTENSION IF NOT EXISTS`, which works for the RDS master user.

```bash
sudo -u postgres psql -c "CREATE ROLE rke LOGIN PASSWORD 'rke' CREATEDB;" -c "CREATE DATABASE rke OWNER rke;" -c "CREATE DATABASE rke_test OWNER rke;"
```

```bash
for d in rke rke_test; do sudo -u postgres psql -d $d -c "CREATE EXTENSION IF NOT EXISTS vector;"; done
```

## Backend

```bash
cd backend && python -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt
```

```bash
export RKE_DATABASE_URL=postgresql+psycopg://rke:rke@localhost:5432/rke RKE_JWT_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(48))") RKE_COOKIE_SECURE=false RKE_DEMO_PASSWORD='choose-a-long-password'
```

```bash
python -m app.cli demo
```

```bash
uvicorn app.main:app --reload --port 8000
```

Then, in another terminal, start the worker:

```bash
python -m app.cli worker
```

The first run downloads the embedding model (about 70 MB) to the fastembed cache.

## Frontend

```bash
cd frontend && npm install && npm run dev
```

The app is at <http://localhost:5173>, and `/api` is proxied to port 8000.

## Tests

| Command | What it runs |
|---|---|
| `cd backend && pytest` | All backend tests on `rke_test` (schema rebuilt from the migration). AI runs offline: no API key, no cost |
| `pytest -m "not slow"` | Skips the end-to-end evaluation regression guard |
| `cd frontend && npm test` | Vitest |
| `make evaluate` | Full evaluation on the loaded demo corpus → `eval/reports/` |

The first run downloads the embedding and re-ranker models (~150 MB) into `backend/var/models`.

## CLI (`python -m app.cli`)
`migrate` · `create-user --email E --role admin|member|viewer` · `generate-corpus` · `load-demo` · `drain` · `worker` · `evaluate --out DIR` · `demo`

## Code map
| Path | Responsibility |
|---|---|
| `app/modules/workspaces/` | Workspaces, members and roles, knowledge bases, saved answers |
| `app/connectors/` | Web page fetching with SSRF protection, ZIP archives, refresh |
| `app/pipeline/` | Type detection and limits, text/HTML/OCR extraction, chunking, entities and concepts, MinHash |
| `app/retrieval/search.py` | Hybrid search with access control in SQL, cross-encoder re-ranking, entity filter |
| `app/retrieval/qa.py` | Grounded Q&A: entity filter → retrieval → relevance gate → provider → citation enforcement |
| `app/knowledge/` | Concept graph, topics, freshness/reliability indicators, cross-source conflict check |
| `app/ai/` | Provider interface, Claude, offline extractive answerer, embeddings and re-ranker |
| `app/synthetic/corpus.py` | The benchmark corpus and labelled questions |
| `app/evaluation/run.py` | All reported metrics (retrieval and answers separately) |
| `migrations/versions/0001_initial_schema.py` | Schema, including the `readable_documents` access view |
