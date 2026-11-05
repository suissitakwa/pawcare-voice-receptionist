# 0003: Scope and stack

**Status:** Accepted.

## Decisions

- **Modular monolith:** one FastAPI service, one React app, one database. One developer maintains it, and nothing here needs independent scaling.
- **Synchronous SQLAlchemy with psycopg 3.** Traffic is low and the transactions are short and critical. Sync code is simpler to reason about for locking, and FastAPI runs these handlers in a thread pool. Async can come later if measurement calls for it.
- **Structured FAQs, not RAG.** A small approved list gives exact answers and a reliable "I don't know, let me route you to staff." Retrieval would add the risk of plausible wrong answers without a content problem to solve.
- **No multi-agent framework.** One model session and a handful of typed tools are enough.
- **Real PostgreSQL in all tests** (local container and CI service), since the guarantees being tested live in the database.
- **PostgreSQL 18 everywhere:** local development, CI, and Railway run the same major version, because booking guarantees depend on database behaviour.
- **Node 22 and Python 3.12** are pinned, with exact dependency versions and committed lockfiles.
- **Deployment:** Netlify for the frontend, Railway for the backend and PostgreSQL, chosen because keeping the database and API on one platform is the least to operate. The backend image stays host-neutral: it honours `PORT` and accepts a standard `DATABASE_URL`, so moving hosts would not need code changes.

## Consequences

Scaling beyond one business would require tenant scoping through every query. `business_slug` is resolved server-side from the start so that path stays open, but multi-tenancy itself is not built.
