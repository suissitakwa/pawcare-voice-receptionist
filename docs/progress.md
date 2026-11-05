# Progress

Evidence log. Each entry records what was actually run. Anything not run is listed as not verified.

## Day 1: Foundation (2026-11-04)

**Outcome:** a website shell that renders business details read from PostgreSQL through the API, with health endpoints, migrations, repeatable seed data, tests, and CI configuration.

### Verified

Run on macOS with Python 3.12, Node 22.23.4, and PostgreSQL **14.13** running natively. Compose and CI specify PostgreSQL 17, so 14.13 is not the target version.

| Check | Result |
|---|---|
| `ruff check` and `ruff format --check` | Pass |
| `pytest` on real PostgreSQL | 14 passed, including migration up, down, and up again |
| `pytest -W error::DeprecationWarning` | 14 passed, no deprecation warnings |
| `npm run typecheck` | Pass |
| `npm test` | 5 passed |
| `npm run build` | Pass: 224.93 kB JS (70.30 kB gzip) |
| `docker compose config` | Valid. This parses the file and does not start anything |
| `GET /health/ready` | 200, `{"status":"ready","database":"ok","schema_revision":"0001"}` |
| `GET /api/v1/business` | 200, record read from PostgreSQL |
| Browser at 800 px and 375 px wide | Renders database content, no horizontal scroll |
| Keyboard | First Tab reveals and focuses the skip link |

**Real database outage,** by stopping PostgreSQL while the app ran:

| Check | Result |
|---|---|
| `GET /health` | 200: liveness does not depend on the database |
| `GET /health/ready` | 503, `database: unreachable`, no connection details |
| `GET /api/v1/business` | 503, `database_unavailable` |
| Browser | Error message and retry button. The demo notice stays visible |
| After restarting PostgreSQL | Readiness returned 200 and the retry button restored the page, without restarting the backend |

### Verified later on Day 2

The Docker items that could not run on Day 1 passed once the machine had recovered. See the Day 2 entry below.

### Open issue: one unexplained 503

The first page load in the browser got a 503 from `/api/v1/business`, and a reload succeeded. It has not recurred, but the attempt to reproduce it was inconclusive because the machine was too overloaded for the requests to complete.

- **Observed:** the server logged `OperationalError`. PostgreSQL's log showed no error. The machine's load average was over 500, with 4 GB of swap in use.
- **Most likely cause, not proven:** the 3-second database connection timeout expired while the machine was overloaded.
- **Done:** the original log recorded only the exception type, which is why the cause was lost. It now records the driver message for connection failures and still logs other database errors by type only, so row data stays out of logs. Tests cover both.
- **Next:** if it recurs, the log will identify the cause. Check again on Day 2 on a healthy host, before changing the timeout.

### What remains

Everything from Day 2 onward in [mvp.md](mvp.md). The services, FAQ, voice, and booking sections currently state that they are not available.

## Day 2: Catalog and deployment readiness (2026-11-04)

**Outcome:** services, prices, durations, hours, and FAQs now come from PostgreSQL and appear on the site. The site is deployed: frontend on Netlify, backend and PostgreSQL on Railway.

### Verified

Run with `docker compose` on PostgreSQL 17.11, on an 8 GB machine under heavy load.

| Check | Result |
|---|---|
| Backend image build and `docker compose up` | All three services start. Backend and database report healthy |
| Backend runs as a non-root user | uid 1000 |
| `GET /health/ready` | 200, `schema_revision` `0002` |
| `btree_gist` on PostgreSQL 17 | Enabled by migration 0001 |
| Seed data | 3 services, 6 FAQs, 5 open days |
| `ruff check` and `ruff format --check` | Pass |
| `pytest -W error::DeprecationWarning` on PostgreSQL 17 | 29 passed |
| `npm run typecheck` and `npm run build` | Pass (run natively on Node 22) |
| `npm test` with a 60-second timeout | 14 passed |
| Browser | Services, 6 FAQs, and 7 days of hours render, with no console errors or horizontal scroll |
| GitHub Actions, first run | Frontend and backend image jobs passed |
| Database value changed, then backend restarted | API returned the new value, then the seed restored it |
| Database stopped under Compose | Readiness and catalog return 503. Recovers on restart without a backend restart |

### Verified live

Frontend: https://pawcare-voice-receptionist.netlify.app. Backend: https://pawcare-voice-receptionist-production.up.railway.app.

| Check | Result |
|---|---|
| `GET /health/ready` | 200, `database: ok`, `schema_revision` `0002` |
| Migrations and `btree_gist` on Railway PostgreSQL 18 | Migration 0001 applied, so the extension is available |
| Live site in a browser at 1280 px and 375 px wide | Business name, 3 services, 6 FAQs, and 7 days of hours render, with no error message or horizontal scroll |
| CORS on the live API | Netlify origin allowed. `https://evil.example` and `http://localhost:5173` not allowed |
| Netlify response headers | `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, and `Permissions-Policy` present |

### Not verified

| Item | Why | How to close it |
|---|---|---|
| GitHub Actions backend job | The first run failed before any code ran: the workflow referenced `setup-uv@v10`, a tag that does not exist. Pinned to `v10.3.3` | Next CI run |
| Color contrast | Not measured | Day 13 |

### Known issues

- **Default-timeout test failure:** `npm test` with the default 5-second timeout failed once locally, on the first test only, at a load average above 400. The GitHub Actions frontend job passed with the default timeout, so this was machine load.
- **Slow on this machine:** the first page load under Compose takes more than 5 seconds while Vite bundles dependencies. The first image build took about 18 minutes under overload, and later starts took about 3.
- **The Day 1 503 is still unexplained.** It has not recurred, including on the deployed host so far. Keep watching.
- **PostgreSQL versions differ:** Railway runs 18, while Compose and CI use 17. Align them before the Day 4 booking constraint.
- **Netlify badge:** a "Powered by Netlify" badge appears in the corner of the live site. At narrow widths it can cover part of the navigation.

### What remains for Day 2

Get the backend CI job passing on `main`.
