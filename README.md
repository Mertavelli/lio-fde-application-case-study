# Lio Procurement App

Upload vendor-offer PDFs, extract the details with AI, and track procurement
requests across your organization.

See [HANDOFF.md](HANDOFF.md) for the implemented requirements, engineering decisions, verification results and remaining customer questions.

- **`frontend/`** — Next.js 15, React 19, Tailwind CSS 4
- **`backend/`** — FastAPI, SQLAlchemy, Alembic, JWT auth
- **Database** — PostgreSQL 16

## Prerequisites

- **Docker Desktop** (or Docker Engine) with **Docker Compose v2** — running before you start.
- An **OpenAI API key** is optional: the app boots without it; PDF extraction and semantic classification need one.

## Quick start (Docker)

```bash
cp .env.example .env      # optional: add your OpenAI API key for PDF extraction
docker compose up --build
```

| | URL |
| --- | --- |
| App | http://localhost:3200 |
| API + docs | http://localhost:7200 · http://localhost:7200/docs |

Sign in with a demo account — no registration needed (password `demo1234`).
Development happens in the **Acme Corp** organization, which has one of each role:

| Account | Role | Can do |
| --- | --- | --- |
| `admin@acme.com` | admin | everything incl. members & settings |
| `buyer@acme.com` | buyer | approve requests |
| `requester@acme.com` | requester | create & track requests |

Acme requests strictly below $2,000 are approved automatically when their totals and article classification are verified. Other requests need buyer or admin approval. Uncertain article types require buyer/admin review first.

Other tenants exist to show the multi-tenant setup — **Walmart, Schaeffler,
Siemens, BMW** — each with an admin at `admin@<company>.com` (same password).
Their data is fully isolated from Acme's.

On startup the backend applies schema migrations and seeds the database (demo
accounts, organizations, commodity groups, suppliers) — both steps are
idempotent, so restarts never duplicate or overwrite anything (your data lives
in the `postgres_data` volume; `docker compose down -v` resets it). The OpenAI
key enables PDF extraction and semantic classification.

## Local development

Run each service in a separate terminal, starting from the repository root. If the full Docker stack is running, stop it first with `docker compose down` (without `-v`, to preserve data).

```bash
# Terminal 1: Postgres only
docker compose up db
```

```bash
# Terminal 2: backend → http://localhost:7200
cd backend
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head
python -m app.seed
CORS_ORIGINS=http://localhost:3200 uvicorn app.main:app --reload --port 7200 --env-file ../.env
```

Create `.env` in the repository root from `.env.example` before starting the backend, and add an OpenAI key to exercise extraction and semantic features.

```bash
# Terminal 3: frontend → http://localhost:3200
cd frontend
npm ci
npm run dev
```

Local PDF parsing and OCR tests need Poppler, Tesseract and the English language data (`eng`). Check with `tesseract --list-langs`; having the Tesseract binary alone is insufficient. On Debian/Ubuntu: `sudo apt-get install poppler-utils tesseract-ocr tesseract-ocr-eng`. The Docker image already installs these prerequisites.

## Tests

```bash
# From the repository root, with the backend virtual environment activated:
(cd backend && python -m pytest)      # API tests (in-memory SQLite)
(cd frontend && npx tsc --noEmit && npm run build)
```

## Layout

```
backend/    FastAPI app — routers, services, models, schemas, core; Alembic migrations
frontend/   Next.js app — app-router pages, typed API client (src/lib/api), components
docker-compose.yml
```
