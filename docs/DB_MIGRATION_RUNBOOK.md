# Production database: moving onto the new migration history (Phase 1.4)

Phase 1 replaced the two old migrations (`e210ec86766e`, `0aabea5968e1`), which both created
`users`, with:

| Revision | What it does |
|---|---|
| `0001_baseline_users` | The `users` table **exactly** as it already exists in production (verified identical, column by column and constraint by constraint) |
| `0002_contact_messages` | New, empty `contact_messages` table (used from Phase 3) |

Production already has `users`, so it is **stamped** at `0001_baseline_users` (Alembic is told
"this is already applied", nothing is created) and then upgraded, which only creates
`contact_messages`. No existing table or row is changed.

Run every command from the repository root, on the merged `main` branch, with the production
`.env` in place. The app does not need to be stopped: the running code doesn't use
`contact_messages` yet.

> If production's `ENV` is unset (it defaults to `prod`) and `JWT_SECRET_KEY` is shorter than
> 32 characters, these commands stop with a settings error. Rotate the secret first, or prefix
> each command with `ENV=dev` for this one-off session.

## 0. Back up

In the Neon console: **Branches → Create branch** from your production branch, named for example
`pre-phase1-migration`. It is an instant copy you can restore from.

## 1. Inspect (read-only)

```bash
uv run alembic current
```

Any of these is expected and fine:

| Output | Meaning |
|---|---|
| `Can't locate revision identified by 'e210ec86766e'` (or `'0aabea5968e1'`) | The DB was built by an old migration |
| Nothing after the INFO lines | No `alembic_version` table yet (table created another way) |

Check that `users` matches the baseline:

```bash
uv run python - <<'EOF'
import asyncio
from sqlalchemy import inspect
from src.core.database import engine

async def main():
    async with engine.connect() as conn:
        def show(sync_conn):
            insp = inspect(sync_conn)
            print("tables:", sorted(insp.get_table_names()))
            for col in insp.get_columns("users"):
                print(f"  {col['name']:16} {col['type']!s:26} {'NULL' if col['nullable'] else 'NOT NULL'}")
            print("  pk:", insp.get_pk_constraint("users")["name"],
                  " unique:", [u["name"] for u in insp.get_unique_constraints("users")])
        await conn.run_sync(show)
    await engine.dispose()

asyncio.run(main())
EOF
```

Expected (the order of `tables` may include `alembic_version`):

```
tables: ['alembic_version', 'users']
  id               UUID                       NOT NULL
  name             VARCHAR                    NOT NULL
  email            VARCHAR                    NOT NULL
  hashed_password  VARCHAR                    NOT NULL
  is_active        BOOLEAN                    NULL
  is_superuser     BOOLEAN                    NULL
  created_at       TIMESTAMP                  NULL
  updated_at       TIMESTAMP                  NULL
  pk: users_pkey  unique: ['users_email_key']
```

**If anything differs (extra/missing columns, other constraint names, a `contact_messages`
table already present), stop here** and share this output before continuing.

## 2. Preview the SQL (read-only, prints only)

```bash
uv run alembic upgrade 0001_baseline_users:head --sql
```

Expect only `CREATE TABLE contact_messages`, `CREATE INDEX ix_contact_messages_created_at`
and an `UPDATE alembic_version`. Nothing that touches `users`.

## 3. Stamp the baseline (writes only `alembic_version`)

```bash
uv run alembic stamp --purge 0001_baseline_users
```

## 4. Upgrade

```bash
uv run alembic upgrade head
```

## 5. Verify

```bash
uv run alembic current   # expect: the newest revision, e.g. 0005_profile_resume (head)
uv run alembic check     # expect: No new upgrade operations detected.
```

Then run `scripts/smoke_prod.sh` after deploying; `/health/ready` should return 200.

## After Phase 4.5: site content tables

`alembic upgrade head` also applies `0003_pitch_usage` and `0004_site_content` (new, empty
tables only; nothing existing changes). Then load today's site content once:

```bash
uv run alembic upgrade head      # expect: 0005_profile_resume (head)
make seed-content                # or: uv run python scripts/seed_content.py
```

The seed only fills **empty** tables, so it is safe to run again and never overwrites edits
made in the admin. Until it has run, `GET /api/v1/content` returns 503 and the public site
shows its built-in copy of the content.

## Rollback

- Undo step 4 only: `uv run alembic downgrade 0001_baseline_users` (drops the empty
  `contact_messages` table).
- Anything else: restore from the Neon branch made in step 0.

## After Phase 5: résumé link

`alembic upgrade head` also applies `0005_profile_resume`, which only adds a nullable
`site_profile.resume_url` column (no data changes; `downgrade -1` drops it again).
It also applies `0006_pitch_runs`, a new, empty table for the admin's Pitch history (no visitor
IPs are stored; `downgrade -1` drops it, losing only the history).

## Afterwards

Future migrations can run automatically on deploy: set `RUN_MIGRATIONS=true` in the production
environment and `scripts/run.sh` runs `alembic upgrade head` once before Gunicorn starts.
Alternatively, keep it `false` and run `make migrate` by hand as part of each release.

## What was verified (throwaway Postgres 16, never production)

- Fresh database: `upgrade head` → `alembic check` clean → `downgrade base` → `upgrade head`.
- `users` from the new baseline is identical (`pg_dump --schema-only`) to the one the old
  migration produced.
- DB built by the old migration, with rows: stamp + upgrade keep all rows; `alembic check` clean.
- DB with `users` but no `alembic_version`: same result.
- Upgrading **without** stamping fails and changes nothing (transaction rolled back).
