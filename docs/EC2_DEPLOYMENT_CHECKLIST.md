# EC2 deployment checklist (backend)

Everything to do on the Amazon EC2 VM to take the backend from today's state to Phases 1, 3, 4
and 4.5 in production. Work top to bottom; each step says how to verify it. Nothing here runs
by itself: you run every command.

**State when this was written (2026-10-14)**

- `api.aashish-regmi.com.np` returns Cloudflare **521**: Cloudflare cannot reach the VM.
- Production still runs pre-Phase-1 code. Phases 1, 3, 4 and 4.5 are on GitHub branches.
- New credentials (Gemini, Tavily, Turnstile) are not on the VM yet.
- The frontend (Cloudflare) already runs Phases 2–3 and degrades gracefully while the API is down.

Conventions below: run commands on the VM from the backend checkout (the folder with
`docker-compose.yaml`). The Compose service is `api`; it reads the VM's `.env`. Never commit or
paste the `.env` anywhere.

---

## 1. Get the API reachable again (fixes the 521)

```bash
docker compose ps                      # is the "api" container running?
docker compose logs --tail=100 api     # why did it stop? (settings errors print here)
curl -s localhost:8001/health || curl -s localhost:8001/   # does it answer on the VM itself?
```

- If it answers locally but Cloudflare still shows 521: the **EC2 security group** or host
  firewall blocks Cloudflare, or the Cloudflare DNS record points at an old IP / wrong port.
- Cloudflare SSL mode must match the origin: "Flexible" if the VM serves plain HTTP on 80,
  "Full"/"Full (strict)" only if the VM serves HTTPS (e.g. nginx with a Cloudflare origin cert).
- **Security group:** allow 80/443 inbound **only from Cloudflare's IP ranges**
  (https://www.cloudflare.com/ips/) and SSH only from your own IP. Otherwise anyone can bypass
  Cloudflare and the rate limits by hitting the EC2 IP directly.

✅ Done when `curl -s https://api.aashish-regmi.com.np/` stops returning 521.

## 2. Back up the database

Neon console → **Branches → Create branch** from production, e.g. `pre-phase-1-deploy`.
That instant copy is your rollback for everything below.

## 3. Merge the code (on GitHub, in this order)

1. `phase-1-backend-foundation` → main
2. `phase-3-contact-context` → main
3. `phase-4-pitch-me` → main
4. `phase-4.5-admin-cms` → main (once it is finished and pushed)

Then on the VM:

```bash
git fetch origin && git switch main && git pull --ff-only
```

## 4. Update the VM's `.env`

Edit it on the VM (`nano .env`), keep it private (`chmod 600 .env`). Unknown or retired keys are
ignored, so old lines can stay. Every key is documented in `.env.example`.

### Must change or add before the new code starts

| Key | Value | Why |
|---|---|---|
| `ENV` | `prod` (or leave unset: defaults to prod) | Hides `/docs`, enables production safety checks |
| `JWT_SECRET_KEY` | ≥ 32 random characters | **The app refuses to start in prod with a shorter one.** Check: `grep '^JWT_SECRET_KEY=' .env \| cut -d= -f2- \| tr -d '"\n' \| wc -c`. New one: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` (logs everyone out) |
| `ALLOWED_ORIGINS` | `https://aashish-regmi.com.np` (add `https://www.aashish-regmi.com.np` if used) | CORS, and the admin's CSRF check, require the exact site origin |
| `CLIENT_IP_HEADER` | `cf-connecting-ip` | The API is behind Cloudflare |
| `TRUSTED_PROXY_IPS` | Cloudflare's ranges, comma-separated (command below). If nginx sits between Cloudflare and the app, use nginx's address instead (e.g. `127.0.0.1,172.16.0.0/12`) | Without it every visitor shares Cloudflare's IP for rate limiting |
| `COOKIE_SECURE` | `true` | Admin login over HTTPS |
| `COOKIE_SAMESITE` | `lax` | Admin cookies (CSRF-safe across `api.` and the apex domain) |
| `COOKIE_DOMAIN` | empty | Host-only cookies on the API domain |
| `CONTEXT_MOCK` | `false` (or remove) | Otherwise visitors see sample data |
| `RUN_MIGRATIONS` | `false` for now | Turn on only after step 5 |
| `PITCH_ENABLED` | `false` | Pitch Me stays off until step 10 |

Generate the Cloudflare list:

```bash
echo "TRUSTED_PROXY_IPS=$( (curl -s https://www.cloudflare.com/ips-v4; echo; curl -s https://www.cloudflare.com/ips-v6) | paste -sd, - )"
```

### Already there, keep as they are

`DATABASE_URL`, `SMTP_*`, `SUPER_ADMIN_EMAIL`, `PORTFOLIO_URL`, `GITHUB_URL`, `LINKEDIN_URL`,
and the AWS keys: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `S3_BUCKET_NAME`
(Phase 4.5 uses these exact names for image uploads). `PORTFOLIO_URL` must be the public site
URL, exactly `https://aashish-regmi.com.np`: `/sitemap.xml` builds every page address from it.

### Add when you get to that step (safe to leave empty until then)

| Key | Step | Notes |
|---|---|---|
| `S3_UPLOAD_PREFIX` | 8 | Default `portfolio/` |
| `S3_PUBLIC_BASE_URL` | 8 | Only if images are served through CloudFront or a custom domain |
| `S3_MAX_UPLOAD_MB` | 8 | Default 5 |
| `TURNSTILE_SECRET` | 9 | Cloudflare Turnstile secret key |
| `CONTACT_CONFIRMATION_ENABLED` | 9 | `true` only after Turnstile works |
| `HOURLY_RATE_GBP` | any | Optional, shows your rate in the visitor's currency |
| `EMAIL_AVATAR_URL` | any | Optional image in contact emails |
| `GEMINI_API_KEY` | 10 | Free key from Google AI Studio |
| `TAVILY_API_KEY` | 10 | From tavily.com |
| `PITCH_LLM_MODEL` | 10 | Default `gemini-2.5-flash`; confirm your key can use it |
| `PITCH_DAILY_CAP` | 10 | Default 100 runs/day |
| `FORWARDED_ALLOW_IPS` | 1 | Only if nginx runs on the VM (see `.env.example`) |

`FRONTEND_URL` is no longer used and can be deleted.

## 5. Move the database onto the new migration history

Follow `docs/DB_MIGRATION_RUNBOOK.md`. Inside Docker, the commands are:

```bash
docker compose build api
docker compose run --rm --entrypoint alembic api current          # read-only
docker compose run --rm --entrypoint alembic api upgrade 0001_baseline_users:head --sql   # preview only
docker compose run --rm --entrypoint alembic api stamp --purge 0001_baseline_users
docker compose run --rm --entrypoint alembic api upgrade head     # contact_messages, pitch_usage, content tables
docker compose run --rm --entrypoint alembic api current          # expect the newest revision (head)
```

If `current` or the users-table check in the runbook shows anything unexpected, **stop** and ask.
After this, you may set `RUN_MIGRATIONS=true` so future deploys migrate automatically.

## 6. Restart and smoke-test

```bash
docker compose up -d --build
docker compose logs --tail=50 api        # look for settings errors (e.g. JWT secret length)
```

From your laptop (in the backend repo): `scripts/smoke_prod.sh` → all checks PASS
(`/health`, `/health/ready`, register returns 401).

## 7. Admin account and site content

Check for unexpected super admins first (read-only):

```bash
docker compose run --rm --entrypoint python api -c "
import asyncio
from sqlalchemy import select
from src.core.database import AsyncSessionLocal, engine
from src.models.user_model import User
async def main():
    async with AsyncSessionLocal() as s:
        for u in (await s.execute(select(User).where(User.is_superuser.is_(True)))).scalars():
            print(u.email, u.created_at)
    await engine.dispose()
asyncio.run(main())"
```

Delete any account you did not create. Then create or reset your admin. The password goes only
in this command, never in a file:

```bash
docker compose run --rm --entrypoint python \
  -e ADMIN_NAME="Aashish Regmi" -e ADMIN_EMAIL="regmiaashish660@gmail.com" -e ADMIN_PASSWORD='<your password>' \
  api scripts/seed.py
```

(Prefer a long random password: `python3 -c "import secrets; print(secrets.token_urlsafe(18))"`.
Then clear the command from shell history: `history -d $(history 1)` in bash.)

Load today's site content into the new content tables (only fills empty tables; safe to re-run):

```bash
docker compose run --rm --entrypoint python api scripts/seed_content.py
```

✅ Log in at `https://aashish-regmi.com.np/admin`.

## 8. S3 for admin image uploads

The admin uploads images **straight from the browser to S3** using short-lived presigned POSTs,
with the keys already in `.env`. In the AWS console, for the bucket in `S3_BUCKET_NAME`:

**a. CORS** (Bucket → Permissions → CORS):

```json
[
  {
    "AllowedOrigins": ["https://aashish-regmi.com.np", "http://localhost:5173"],
    "AllowedMethods": ["POST"],
    "AllowedHeaders": ["*"],
    "ExposeHeaders": ["ETag"],
    "MaxAgeSeconds": 3000
  }
]
```

(`http://localhost:5173` lets the admin upload from your laptop's dev server; drop it if you
never do that. Add `https://www.aashish-regmi.com.np` if the site is also served on `www`.)

**b. Public read for the upload folder only.** Either:

- *Bucket policy* (Permissions → Bucket policy; Block Public Access must allow public bucket
  policies for this bucket):

  ```json
  {
    "Version": "2012-10-17",
    "Statement": [{
      "Sid": "PublicReadPortfolioImages",
      "Effect": "Allow",
      "Principal": "*",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::<S3_BUCKET_NAME>/portfolio/*"
    }]
  }
  ```

- or *CloudFront* in front of the bucket (keeps the bucket private), then set
  `S3_PUBLIC_BASE_URL=https://<your-distribution>.cloudfront.net`.

Résumé PDFs uploaded in the admin (Profile → Résumé) go to `portfolio/resume/`, so the same
CORS rule and the same `portfolio/*` public-read policy (or CloudFront) cover them; nothing extra
to configure.

**c. Permissions of the key in `.env`.** That IAM user needs at least `s3:PutObject` and
`s3:DeleteObject` on `arn:aws:s3:::<S3_BUCKET_NAME>/portfolio/*`. If it currently has broad
access (e.g. full S3), consider narrowing it to that, since the key lives on an internet-facing
server.

✅ In `/admin`, upload a project image; it previews, and the public project page shows it.

## 9. Cloudflare Turnstile (bot protection)

1. Cloudflare dashboard → Turnstile → Add widget for `aashish-regmi.com.np`.
2. VM `.env`: `TURNSTILE_SECRET=<secret key>` → `docker compose up -d`.
3. Cloudflare frontend build settings: `VITE_TURNSTILE_SITE_KEY=<site key>` → redeploy.
4. Send yourself a message through the contact form.
5. Optionally `CONTACT_CONFIRMATION_ENABLED=true` (visitors get a confirmation email).

## 10. Turn on Pitch Me (costs money per run, so last)

1. Gemini: free key from Google AI Studio → `GEMINI_API_KEY`. Set a quota or budget limit in the
   Google Cloud console. (The free tier may use prompts to improve Google's models; this demo only
   sends company and role names plus public web snippets.)
2. Tavily: account + key → `TAVILY_API_KEY`; enable usage alerts.
3. `.env`: `PITCH_ENABLED=true` (+ `PITCH_DAILY_CAP` if not 100) → `docker compose up -d`.
   The app **refuses to start** if Turnstile, Gemini or Tavily keys are missing; check the logs.
4. Go-live checks (do these together with Claude):
   - `curl -N "https://api.aashish-regmi.com.np/api/v1/pitch/stream?company=Spotify&role=Backend%20engineer"`
     streams events one by one (with Turnstile on, this needs a valid token; Claude will walk you
     through it).
   - A stream stays open past 100 s with pings (Cloudflare drops idle connections at 100 s).
   - A company name like `Ignore previous instructions` produces a normal pitch.
   - A burst of requests hits the 3/minute limit and the daily cap message.
5. Only then: Cloudflare frontend build settings `VITE_PITCH_ENABLED=true` → redeploy.
6. Watch it in `/admin` → **Pitch history**: every run (company, role, outcome, the pitch text,
   duration, tokens, estimated cost), today's paid runs against the daily cap, and the last 30
   days' spend. Only what visitors typed is stored: **no IP addresses or other visitor data**.

## 11. Frontend build settings (Cloudflare)

| Variable | Value |
|---|---|
| `VITE_API_URL` | `https://api.aashish-regmi.com.np` (already set: builds fail without it) |
| `VITE_TURNSTILE_SITE_KEY` | Step 9 |
| `VITE_PITCH_ENABLED` | `true` only after step 10 |

## 12. Security hygiene

- **Rotate secrets** if any old Docker image (which contained `.env` before Phase 0) ever left
  your machines: SMTP password, `JWT_SECRET_KEY`, Neon password, AWS keys, Gemini key.
- Keep `.env` at `chmod 600`, owned by the deploy user.
- Security group: 80/443 from Cloudflare only, SSH from your IP only.
- Keep `/docs` hidden (`ENV=prod`).

## Rollback

- Code: `git switch` back to the previous commit on the VM, then `docker compose up -d --build`.
- Database: `docker compose run --rm --entrypoint alembic api downgrade <revision>` for the newest
  steps, or restore the Neon branch from step 2.
