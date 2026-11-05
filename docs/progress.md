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

**Outcome:** services, prices, durations, hours, and FAQs now come from PostgreSQL and appear on the site. Railway deployment configuration and a runbook are written. **The first deployment has not happened yet.**

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
| Database value changed, then backend restarted | API returned the new value, then the seed restored it |
| Database stopped under Compose | Readiness and catalog return 503. Recovers on restart without a backend restart |

### Not verified

| Item | Why | How to close it |
|---|---|---|
| Railway and Netlify deployment | Needs a GitHub repository and accounts, which only the owner can set up | Follow [deployment.md](deployment.md) |
| `btree_gist` on Railway | Same | First deploy |
| GitHub Actions | Nothing has been pushed | First push |
| Color contrast | Not measured | Day 13 |

### Known issues

- **Default-timeout test failure:** `npm test` with the default 5-second timeout failed once, on the first test only, at a load average above 400. All 14 passed with a 60-second timeout. CI will show whether this happens on a normal machine.
- **Slow on this machine:** the first page load under Compose takes more than 5 seconds while Vite bundles dependencies. The first image build took about 18 minutes under overload, and later starts took about 3.
- **The Day 1 503 is still unexplained.** It has not recurred. Keep watching on the deployed host.

### What remains for Day 2

Commit, create the GitHub repository, push, and deploy using [deployment.md](deployment.md).
