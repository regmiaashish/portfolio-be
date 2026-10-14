# CLAUDE.md

Backend for Aashish Regmi's portfolio (`api.aashish-regmi.com.np`). The roadmap lives in
`docs/PORTFOLIO_IMPLEMENTATION_PLAN.md`; work happens one phase at a time, on its own branch.

## Safety rules (non-negotiable)

- **Never read, print, copy or commit `.env`** or any real secret. Use dummy values.
- **Never connect to the production `DATABASE_URL`** (Neon). For anything that needs a database,
  use a throwaway local Postgres container, or stop and ask.
- **No real external calls**: no real SMTP, Gemini, Tavily, Turnstile, AWS or geo/weather/FX
  providers from tests or scripts. Mock them (`respx`) or use local sinks.
- **No commits or pushes without explicit approval.** Leave changes for review.
- Give the user the exact commands for anything that must run against production; never run them.
- Clean up every container, image and temp file you start.

`pydantic-settings` loads `.env` relative to the **current working directory**. To run the app or
scripts without touching real config, run them from a directory with no `.env` and pass dummy
values as environment variables (see `tests/__init__.py` for a complete dummy set).

## Stack

- Python 3.12, managed with `uv` (`pyproject.toml` + `uv.lock`)
- FastAPI + Pydantic v2 + pydantic-settings
- SQLAlchemy 2 (async) + asyncpg, Alembic migrations, PostgreSQL (Neon in production)
- Auth: argon2 password hashing, HS256 JWTs (PyJWT) in HttpOnly cookies
- slowapi rate limiting, loguru logging, httpx for outbound HTTP
- Gunicorn + Uvicorn workers in a Docker image (`Dockerfile`, `scripts/run.sh`)
- Pitch Me: LangGraph pipeline, Gemini via langchain-google-genai, Tavily search, SSE via
  sse-starlette; cachetools for in-process caches
- Tooling: ruff (lint + format, config in `pyproject.toml`), pytest, pytest-asyncio, respx

## Configuration

- All settings live in `src/core/config.py` (`Settings`, pydantic-settings) and every key is
  documented in `.env.example`. Add new settings to both; `tests/test_config.py` fails if
  `.env.example` falls out of sync.
- `ENV` is `dev | test | prod` and **defaults to `prod`** (API docs hidden, strict checks such as a
  32+ character `JWT_SECRET_KEY`). Local development sets `ENV=dev`.
- Secrets are `SecretStr`: read them with `.get_secret_value()` at the point of use, never log them.
- Unknown keys are ignored and empty values count as unset.

## Rate limiting and client IP

- One shared `limiter` in `src/core/rate_limit.py`; limits are named constants there
  (`CONTACT_LIMIT`, `LOGIN_LIMIT`). Decorated endpoints need a `request: Request` parameter.
- The key is `client_ip()`: forwarding headers are believed **only** from peers in
  `TRUSTED_PROXY_IPS` (empty by default). Never key limits on a raw header.
- Storage defaults to `memory://` (per worker process); tests reset it in `conftest.py`.

## Commands

```bash
make install            # uv sync --locked
make dev                # uvicorn with reload on :8001
make lint               # ruff check src alembic
make format             # ruff format src alembic
make test               # pytest
make migrate            # alembic upgrade head   (local DB only, never prod)
make migration M="..."  # autogenerate a migration
make docker-up          # build and start the API container

uv run pytest -q
uv run ruff check src alembic tests && uv run ruff format --check src alembic tests
uv lock --check
```

## Layout and layering

```
src/
├── main.py            app creation, middleware, router mounting
├── api/               HTTP layer: routers only
│   ├── api_router.py  mounts every router
│   └── v1/            one router per area (auth, contact, context)
├── core/              config, database, security, dependencies, exceptions
├── services/          business logic and external integrations
├── repositories/      database access (SQLAlchemy queries)
├── models/            SQLAlchemy ORM models
├── schemas/           Pydantic request/response models
└── templates/         email templates
```

Requests flow **router → service → repository**:

- **Routers** parse input (schemas), declare dependencies (auth, DB session, services), call one
  service method, and shape the response. No SQL, no business rules, no external HTTP.
- **Services** hold business rules and talk to external systems. They take repositories and
  clients as constructor arguments, so tests can substitute fakes. They raise domain exceptions
  from `src/core/exceptions.py`, never `HTTPException`.
- **Repositories** are the only place that builds SQLAlchemy queries. They return models (or
  `None`), and don't know about HTTP.
- **Dependencies** (`src/core/dependencies.py`) wire these together with `Depends(...)`. Tests swap
  them with `app.dependency_overrides`.

Conventions:

- Responses use `APIResponse` from `src/schemas/common_schema.py`; errors use `ErrorResponse`.
- Schemas that accept user input set `extra="forbid"` when unexpected fields could be dangerous
  (for example privilege fields on registration).
- New tables need a model in `src/models/` (imported in `src/models/__init__.py`) **and** an
  Alembic migration. Generate it with `make migration M="..."` against a throwaway database,
  review it, then confirm `make migrate-check` reports no differences.
- Constraint names follow the naming convention in `src/core/database.py` (PostgreSQL's own
  defaults). In migrations, wrap explicit names in `op.f(...)` so the convention isn't applied twice.
- Production migrations: see `docs/DB_MIGRATION_RUNBOOK.md`. `scripts/run.sh` only migrates on
  start when `RUN_MIGRATIONS=true`.

## External calls and background work

- All outbound HTTP uses the one `httpx.AsyncClient` created in the app lifespan
  (`src/main.py`), injected with `Depends(get_http_client)`. Never create a client per request.
- Optional upstreams (weather, FX) degrade to `null` fields; only a missing essential
  upstream (geolocation) fails the request with `ServiceUnavailableError` (503).
- Work that outlives the response (sending email) runs as a background task with its **own**
  database session (`own_session_repository`), since the request's session is closed by then.
  Blocking SMTP runs via `asyncio.to_thread`.
- Contact spam controls: honeypot (`website`), Turnstile when `TURNSTILE_SECRET` is set, and
  confirmation emails only when Turnstile is on.

## Pitch Me (paid pipeline)

- `src/services/pitch/`: `graph.py` (researcher -> matchmaker -> writer, progress via
  LangGraph's custom stream writer), `llm.py` and `research.py` (providers, chosen **only** in
  `create_llm()` / `create_research()`), `prompts.py`, `stream.py` (SSE run, cache, cost log),
  `service.py` (pre-stream checks). Route: `src/api/v1/pitch_router.py`.
- Every run costs money. Keep these in front of the pipeline: kill switch (`PITCH_ENABLED`),
  3/minute limit, Turnstile, cache (hits are free), then the Postgres daily cap
  (`pitch_usage`, atomic upsert, counted before the run and never refunded).
- Visitor input and scraped text go into prompts only inside `<company>`, `<role>`,
  `<research>` tags, with `UNTRUSTED_RULE` in the system prompt; scraped `<`/`>` are escaped.
- The pipeline runs in its own task behind a queue: the deadline (`PITCH_TIMEOUT_SECONDS`) and
  client disconnects cancel it so no further paid calls are made.
- Tests and local dev use `PITCH_LLM_PROVIDER=fake` / `PITCH_RESEARCH_PROVIDER=fake`; the prod
  settings validator refuses fakes and missing keys when `PITCH_ENABLED=true`. Never call real
  providers from tests or scripts.

## Site content and admin (Phase 4.5)

- Spec: `docs/ADMIN_CMS_CONTRACT.md`. Change it first, then backend and frontend.
- Public: `GET /api/v1/content` (published projects only, `position` order, ETag + 304,
  `Cache-Control: public, max-age=60, stale-while-revalidate=600`).
- Admin: everything under `/api/v1/admin` has a router-level `Depends(require_superuser)`.
  Every **mutating** admin route also has `Depends(require_csrf)`: header
  `X-Requested-With: portfolio-admin` plus an `Origin` in `ALLOWED_ORIGINS`, else 403 `csrf`.
  New mutating admin routes must use the `_MUTATION` dependencies, and log via `_audit()`.
- Content lives in `src/models/content_model.py`; ordered tables share `OrderedRepository`
  (`list_all`, `create` appends at the end, `reorder` takes exactly every id).
- Images: S3 behind `StorageService` (`src/services/storage_service.py`). The browser uploads
  straight to S3 with a presigned POST (policy pins key, content type and size; 5 min). Keys are
  generated server-side as `<S3_UPLOAD_PREFIX>projects/<uuid4>.<ext>` (extension from the content
  type, never the filename) and `ImageInput` rejects anything else. Deleting old images is
  best-effort and never fails a request. Tests use `tests/content_fakes.py`; never real S3.
- Seed: `src/data/seed_content.json` via `scripts/seed_content.py` / `make seed-content`, which only
  fills empty tables (never overwrites admin edits).

## Testing

- `tests/__init__.py` sets a complete dummy environment **before** `src` is imported, so tests never
  read real secrets or reach the real database.
- Prefer `app.dependency_overrides` and in-memory fakes over monkeypatching.
- Every security fix gets a regression test that fails on the old code.
- Anything that needs a real database runs against a throwaway Postgres container (for example
  `docker run --rm -p 127.0.0.1:55432:5432 -e POSTGRES_PASSWORD=... postgres:16-alpine`), never Neon.
- `scripts/smoke_prod.sh` is the only thing meant for production: read-only GETs plus one
  empty-body POST.
