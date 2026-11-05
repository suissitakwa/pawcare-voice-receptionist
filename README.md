# PawCare Voice Receptionist

A demonstration website for a fictional dog grooming business, with a browser-based AI voice receptionist that books real appointments in a real database. A conventional booking form covers visitors who can't or won't use voice.

> **Status: Day 2 of 15.** The website shows services, prices, hours, and FAQs from the database. **Voice and booking are not built yet.** See [docs/progress.md](docs/progress.md) for verified progress and [docs/mvp.md](docs/mvp.md) for the plan.

PawCare Grooming Studio is fictional. It offers no real services.

## Stack

React, TypeScript, and Vite on the frontend. FastAPI, SQLAlchemy, Alembic, and PostgreSQL on the backend. The OpenAI Realtime API over WebRTC will provide voice. See [docs/architecture.md](docs/architecture.md).

## Run it locally

You need Docker Desktop running.

```bash
docker compose up --build
```

The first start takes a few minutes while images and dependencies install.

| What | URL |
|---|---|
| Website | http://localhost:5173 |
| API documentation | http://localhost:8000/docs |
| Readiness check | http://localhost:8000/health/ready |

Host ports are bound to localhost only: PostgreSQL 5440, API 8000, website 5173.

### Check that it works

```bash
curl -s http://localhost:8000/health/ready
```

Expected: `{"status":"ready","database":"ok","schema_revision":"0002"}`

```bash
curl -s http://localhost:8000/api/v1/business
```

Expected: JSON describing PawCare Grooming Studio, read from PostgreSQL.

Then open the website. The business name in the header comes from the database. To see that for yourself, change it and reload the page:

```bash
docker compose exec db psql -U pawcare -d pawcare -c "UPDATE businesses SET name = 'Changed In Postgres';"
```

To restore it, restart the backend, which reseeds the demo data:

```bash
docker compose restart backend
```

To see the failure path, stop the database and reload the page. The site should show a retry message, and readiness should return 503:

```bash
docker compose stop db
```

## Run the checks

Backend tests need PostgreSQL, because the guarantees they verify live in the database. They create and use a separate `pawcare_test` database.

```bash
docker compose up -d db
```

```bash
cd backend && uv sync && uv run ruff check . && uv run ruff format --check . && uv run pytest
```

Frontend checks need Node 22 (see `frontend/.nvmrc`):

```bash
cd frontend && npm ci && npm run typecheck && npm test && npm run build
```

GitHub Actions runs the same checks, plus a backend image build, on every pull request.

## Configuration

Examples, without secrets: [backend/.env.example](backend/.env.example) and [frontend/.env.example](frontend/.env.example). Anything prefixed `VITE_` ships in the browser bundle, so it must never hold a secret.

## Deployment

The frontend deploys to Netlify, and the backend and PostgreSQL deploy to Railway. Follow [docs/deployment.md](docs/deployment.md).

## Documentation

- [MVP scope and 15-day plan](docs/mvp.md)
- [Progress log with evidence](docs/progress.md)
- [Architecture](docs/architecture.md)
- [API](docs/api.md)
- [Deployment](docs/deployment.md)
- Decisions: [voice](docs/decisions/0001-voice-architecture.md), [booking consistency](docs/decisions/0002-booking-consistency.md), [scope](docs/decisions/0003-scope.md)
- [Evaluation approach](evals/README.md)

## Current limitations

- The "Talk to our assistant" and "Book an appointment" buttons lead to sections explaining that those features are not available yet.
- Prices are indicative demo values for a fictional business.
- Nothing is deployed yet.
