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

CI (`.github/workflows/ci.yml`) runs the same on every PR, plus migrations on a throwaway
Postgres 18 (upgrade → `alembic check` → downgrade base → upgrade → the `*_db.py` tests) and a
Docker build that must start hardened and report healthy (`scripts/ci_smoke_image.sh`). On a
push to `main`, once all of those pass, the publish jobs build the image natively for
`linux/amd64` (the VM is x86_64) and `linux/arm64`, smoke-test each pushed digest on its own
runner, and push one multi-arch image to `ghcr.io/regmiaashish/portfolio-be` tagged
`sha-<7-char commit>` (immutable, what the VM deploys) and `main` (only if the commit is still the
tip of `main`). Every push to `main` gets its own CI run; PRs never publish. Actions are pinned to commit SHAs;
Dependabot (`.github/dependabot.yml`) proposes updates weekly. Two pins it doesn't update: the
CI Postgres service image digest, and the uv image in the Dockerfile's `COPY --from` line,
which must match `UV_VERSION` in `ci.yml`.

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
- Pitch history (`pitch_runs`, admin `/admin/pitches`): every run that started streaming is
  recorded when it ends via `PitchRecorder.record()`, a non-blocking call (the DB recorder writes
  in its own background task and session, logs failures, never touches the stream). Store **no**
  IP or other visitor data. Tests override `get_pitch_recorder` with a fake.
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
- Images: S3 behind `StorageService` (`src/services/storage_service.py`). Since Phase 6.1 the
  admin uploads through the API (`POST /admin/uploads/file`): the type comes from the file's
  bytes (`src/services/upload_files.py`), the body is read in bounded chunks, and the server
  calls `put_object` (no bucket CORS needed). The older presigned-POST `POST /admin/uploads`
  remains for compatibility (policy pins key, content type and size; 5 min). Keys are
  generated server-side as `<S3_UPLOAD_PREFIX>projects/<uuid4>.<ext>` (extension from the content
  type, never the filename) and `ImageInput` rejects anything else. Deleting old images is
  best-effort and never fails a request. Tests use `tests/content_fakes.py`; never real S3.
- Seed: `src/data/seed_content.json` via `scripts/seed_content.py` / `make seed-content`, which only
  fills empty tables (never overwrites admin edits).
- Uploads have a `purpose` (`UPLOAD_RULES` in `content_schema.py`): `project_image` (images,
  `projects/` folder) or `resume` (PDF, `resume/` folder). A key from one folder is never valid
  for the other. Replacing the profile's `resume_url` deletes the old PDF only when it lives under
  our own `<public base>/<prefix>resume/`; external links are never touched.
- SEO: `GET /sitemap.xml` (root, `src/api/sitemap_router.py`) lists `PORTFOLIO_URL`, the
  top-level pages (`STATIC_PAGES`), `/resume` only when a résumé is set, and `/work/<slug>` for
  published projects; the frontend's `robots.txt` points to it.
- Media via the API (Phase 6.2): `GET`/`HEAD /api/v1/media/{key}` (`media_router.py`,
  `media_service.py`) serves uploaded files so the bucket stays private; set
  `S3_PUBLIC_BASE_URL=<API>/api/v1/media`. Only keys matching `is_issued_key()` (the exact
  formats in `UPLOAD_RULES`) are read; anything else, including percent-encoded paths, is 404
  before storage is touched. Content-Type comes from the key's extension, never S3 metadata.
  Immutable caching, ETag/304, `nosniff`, `inline`; no `X-Frame-Options` (the résumé is embedded).
- Contact messages (Phase 6.3): `/admin/messages` (list with `q`/`status`/`unread`, `/stats`,
  detail, `PATCH {"read"}`, `POST /{id}/retry`, `DELETE`) via `ContactAdminService`
  (`src/services/contact_admin_service.py`). `read_at` NULL = unread; GET never marks read.
  Retry atomically moves `failed` -> `pending` (`mark_pending_for_retry`) and reuses
  `ContactDelivery.deliver` as a background task, like the form; anything else is 409
  `not_failed`. Audit lines carry the message id, never its body.
- Résumé download: `GET /api/v1/resume/download` 302s to a presigned S3 GET with an attachment
  disposition (`StorageService.presign_get`, `resume_filename()`), or to an external URL as is;
  404 `resume_unavailable` when unset; `Cache-Control: no-store`.

## Production hardening (Phase 7)

- Container: non-root (uid 10001), read-only root filesystem (`/tmp` tmpfs, Gunicorn heartbeat
  in `/dev/shm`, no control socket), `cap_drop: ALL`, `no-new-privileges`, `HEALTHCHECK` via
  `scripts/healthcheck.py` (GET `/health`, stdlib only). Base images and uv are pinned by version
  and digest in the `Dockerfile`; the host port comes from `API_HOST_PORT` (compose only).
  Nothing may write to the image filesystem at runtime.
- Security headers: `src/core/security_headers.py` (pure ASGI, outermost middleware) adds
  `nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY` and
  `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`; media gets only
  `frame-ancestors 'self' <ALLOWED_ORIGINS>` (the résumé is embedded); docs get no CSP only
  while docs are enabled (never in prod); HSTS in prod. `ALLOWED_ORIGINS` is validated at
  startup (`scheme://host[:port]` only), since it goes into CORS and this header. `/health` and
  `/health/ready` also answer `HEAD` (uptime monitors). Routes' own headers win. 500s are sent outside the middleware stack, so
  `unhandled_exception_handler` adds the same headers itself.
- Readiness timeout: `HEALTH_READY_TIMEOUT_SECONDS` (default 10; Neon wakes from idle).
- Error tracking: optional Sentry (`SENTRY_DSN`, `src/core/observability.py`). Never send
  visitor data: no bodies, cookies, auth/IP headers, query strings or local variables, no log
  or HTTP breadcrumbs, and IPs/coordinates redacted anywhere in an event (`scrub_event`,
  `scrub_breadcrumb`). Off in tests (`ENV=test`); a malformed DSN logs a warning instead of
  stopping the API. Loguru tracebacks also omit variable values (`src/core/logging.py`).
- Never log a visitor's IP, coordinates or an httpx error's text (it quotes the URL, which can
  hold them): log `_failure(exc)`-style summaries, and raise user-facing errors `from None`
  when the cause quotes visitor data.
- Deploy (Phase 8): the VM never builds (never `docker compose up --build` there). One deploy at a
  time (`flock`). `scripts/deploy.sh` pulls `ghcr.io/regmiaashish/portfolio-be:sha-<commit>`
  (waits if CI hasn't published it yet), then, **before changing anything**, checks with the new
  image (`env API_IMAGE=<new> docker compose …`): compose config, the TLS mount and that the
  certificate/key load (when TLS is on), `alembic current`, then migrates. Only then it points
  `API_IMAGE` in `.env` at it (compose: `image: ${API_IMAGE:-portfolio-be-api:latest}`),
  recreates, waits for healthy and smoke-tests; on failure it switches back to the exact previous
  image (by ID: a GHCR `sha-` tag, or a `portfolio-be-api:pre-<id>` tag it creates). Downgrade
  hints run with the new image (only it knows the new revision). `scripts/deploy.sh sha-<old>`
  rolls back; `--no-pull` redeploys the current image for configuration-only changes;
  `--build-local` is the fallback. Test changes to it with fake docker/sudo/curl shims, never on
  the VM.
- Origin TLS (Phase 8): with `TLS_CERT_FILE` and `TLS_KEY_FILE` set, `scripts/run.sh` makes
  Gunicorn serve HTTPS on 8001 (it's either HTTP or HTTPS; Gunicorn applies TLS to every bind);
  `docker-compose.tls.yaml` (added via `COMPOSE_FILE`) mounts `/etc/portfolio-tls` read-only at
  `/run/tls`; the key must be readable by gid 10001. The health check and `deploy.sh` switch to
  HTTPS on their own. Cutover steps: `docs/ORIGIN_TLS.md`.
- Monitoring: `docs/MONITORING.md`. Uptime checks hit `/health` (no database); `/health/ready`
  at most hourly, because frequent DB pings keep Neon's free compute awake. Pitch Me go-live:
  `docs/PITCH_GO_LIVE.md` + `scripts/pitch_golive_check.py` (read-only, no paid calls, never
  prints secrets; reuses `Settings.pitch_readiness_problems()`).

## Testing

- `tests/__init__.py` sets a complete dummy environment **before** `src` is imported, so tests never
  read real secrets or reach the real database.
- Prefer `app.dependency_overrides` and in-memory fakes over monkeypatching.
- Every security fix gets a regression test that fails on the old code.
- Anything that needs a real database runs against a throwaway Postgres container (for example
  `docker run --rm -p 127.0.0.1:55432:5432 -e POSTGRES_PASSWORD=... postgres:16-alpine`), never Neon.
- `scripts/smoke_prod.sh` is the only thing meant for production: read-only GETs plus one
  empty-body POST.
