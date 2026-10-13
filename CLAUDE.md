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
- Auth: argon2 password hashing, HS256 JWTs in HttpOnly cookies
- slowapi rate limiting, loguru logging, httpx for outbound HTTP
- Gunicorn + Uvicorn workers in a Docker image (`Dockerfile`, `scripts/run.sh`)
- Tooling: ruff (lint + format, config in `pyproject.toml`), pytest

## Configuration

- All settings live in `src/core/config.py` (`Settings`, pydantic-settings) and every key is
  documented in `.env.example`. Add new settings to both; `tests/test_config.py` fails if
  `.env.example` falls out of sync.
- `ENV` is `dev | test | prod` and **defaults to `prod`** (API docs hidden, strict checks such as a
  32+ character `JWT_SECRET_KEY`). Local development sets `ENV=dev`.
- Secrets are `SecretStr`: read them with `.get_secret_value()` at the point of use, never log them.
- Unknown keys are ignored and empty values count as unset.

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
- New tables need a model in `src/models/` **and** an Alembic migration.

## Testing

- `tests/__init__.py` sets a complete dummy environment **before** `src` is imported, so tests never
  read real secrets or reach the real database.
- Prefer `app.dependency_overrides` and in-memory fakes over monkeypatching.
- Every security fix gets a regression test that fails on the old code.
- Anything that needs a real database runs against a throwaway Postgres container (for example
  `docker run --rm -p 127.0.0.1:55432:5432 -e POSTGRES_PASSWORD=... postgres:16-alpine`), never Neon.
- `scripts/smoke_prod.sh` is the only thing meant for production: read-only GETs plus one
  empty-body POST.
