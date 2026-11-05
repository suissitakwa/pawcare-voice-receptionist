# Deployment

| Part | Host | Config |
|---|---|---|
| Frontend | Netlify | [netlify.toml](../netlify.toml) |
| Backend | Railway, built from the Dockerfile | [backend/railway.json](../backend/railway.json) |
| Database | Railway PostgreSQL | Service variables |

## First deployment

### 1. Database

In a new Railway project on a paid plan or active trial, add a **PostgreSQL** service.

### 2. Backend

Add a service from the GitHub repository, then set these under its Settings:

| Setting | Value |
|---|---|
| Root Directory | `backend` |
| Railway Config File | `/backend/railway.json` (this path does not follow Root Directory, so it must be the full path) |

Set these under Variables:

| Variable | Value |
|---|---|
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` |
| `ENVIRONMENT` | `production` |
| `BUSINESS_SLUG` | `pawcare-demo` |
| `SEED_DEMO_DATA` | `true` |
| `CORS_ALLOWED_ORIGINS` | Leave as a placeholder until step 3 |

Under Networking, generate a public domain, and then check the deployment:

```bash
curl -s https://YOUR-BACKEND.up.railway.app/health/ready
```

Expect `"status":"ready"`. If migration 0001 fails on `btree_gist`, stop here: the booking design in [decision 0002](decisions/0002-booking-consistency.md) depends on that extension.

### 3. Frontend

In Netlify, import the same repository. `netlify.toml` supplies the build settings. Set `VITE_API_BASE_URL` to the Railway backend's public URL, with no trailing slash. This value is compiled into the bundle, so changing it requires a rebuild.

### 4. Connect them

Set `CORS_ALLOWED_ORIGINS` on the backend to the exact Netlify origin, for example `https://your-site.netlify.app`. No wildcards and no trailing slash. Click **Deploy** to apply it, and wait for **Active**: the old deployment keeps serving the old value until the rollout finishes.

### 5. Verify

```bash
curl -s https://YOUR-BACKEND.up.railway.app/api/v1/services
```

Then open the Netlify URL and confirm that services, hours, and FAQs appear, with no CORS error in the browser console.

## How a deploy behaves

- The container runs `alembic upgrade head`, then the seed if `SEED_DEMO_DATA=true`, then the server.
- Railway waits for `/health/ready`, which passes only when the database is reachable and the schema matches this build. A failed check keeps the previous deployment serving.
- The seed overwrites business details, services, hours, and FAQs with the contents of `demo_business.json`. Set `SEED_DEMO_DATA=false` before staff need to edit these records in production.
- Only changes under `backend/` trigger a backend deploy.

## Cost control

Set a usage limit in Railway's workspace settings before sharing the URL. Voice costs are separate and are controlled by the quota and disable switch added on Day 6.
