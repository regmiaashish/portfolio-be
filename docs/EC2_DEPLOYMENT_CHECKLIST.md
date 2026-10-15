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

The admin uploads images and résumé PDFs **through the API**: the browser sends the file to
`POST /api/v1/admin/uploads/file`, and the server checks its real type and writes it to S3 with
the keys already in `.env`. **No bucket CORS rule is needed.** In the AWS console, for the bucket
in `S3_BUCKET_NAME`:

**a. Request size on the way in.** Uploads are up to `S3_MAX_UPLOAD_MB` (default 5 MB). If nginx
sits in front of the app, set `client_max_body_size 6m;` (nginx's default of 1 MB would reject
larger files with a 413). Cloudflare's limit (100 MB on the free plan) is not an issue.

**b. Keep the bucket PRIVATE: serve files through the API.** No bucket policy, no public
access, no CloudFront. The API serves uploaded files at `GET /api/v1/media/<key>` (only keys the
upload endpoints issue; anything else is 404), so point every public URL there in the VM's `.env`:

```bash
S3_PUBLIC_BASE_URL=https://api.aashish-regmi.com.np/api/v1/media
```

then rebuild/restart. Project image URLs are built from this at request time, so existing images
switch over immediately. A résumé stores its full URL, so **re-upload it** in /admin → Résumé →
Save after the change (that also lets the old-PDF cleanup recognise it).

Cloudflare caches these responses at its edge by file extension (`.png`, `.jpg`, `.webp`, `.pdf`
are in its default cached list) and the API sends `Cache-Control: public, max-age=31536000,
immutable`, so repeat views rarely reach the VM. Check with
`curl -sI <image url> | grep -i cf-cache-status` (`HIT` from the second request on).

(Alternatives, if you ever want them: a public-read bucket policy on `portfolio/*`, or CloudFront
in front of the bucket with `S3_PUBLIC_BASE_URL` set to the distribution. Neither is needed.)

**c. Permissions of the key in `.env`.** That IAM user needs at least `s3:PutObject`,
`s3:DeleteObject` and `s3:GetObject` on `arn:aws:s3:::<S3_BUCKET_NAME>/portfolio/*`
(`GetObject` is what `GET /api/v1/media/...` reads files with, and it signs the résumé's
"Download PDF" link, `GET /api/v1/resume/download`). If it currently has broad
access (e.g. full S3), consider narrowing it to that, since the key lives on an internet-facing
server.

✅ In `/admin`, upload a project image; it previews, and the public project page shows it. Its URL
starts with `https://api.aashish-regmi.com.np/api/v1/media/`, and the bucket itself still returns
403 to the public.

## 9. Cloudflare Turnstile (bot protection)

1. Cloudflare dashboard → Turnstile → Add widget for `aashish-regmi.com.np`.
2. VM `.env`: `TURNSTILE_SECRET=<secret key>` → `docker compose up -d`.
3. Cloudflare frontend build settings: `VITE_TURNSTILE_SITE_KEY=<site key>` → redeploy.
4. Send yourself a message through the contact form.
5. Optionally `CONTACT_CONFIRMATION_ENABLED=true` (visitors get a confirmation email).

## 10. Turn on Pitch Me (costs money per run, so last)

> Superseded by **`docs/PITCH_GO_LIVE.md`** (Phase 7.4), which has the exact commands, the
> Turnstile order and a free pre-flight check. The notes below are kept for reference.

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

## 13. After deploying Phase 6.3 (contact messages in the admin)

Phase 6.3 adds migration `0007_contact_read_at` (a nullable `read_at` column; no data changes).
With `RUN_MIGRATIONS=false`, run it by hand after pulling:

```bash
git pull
sudo docker compose build api
sudo docker compose run --rm --entrypoint alembic api upgrade head
sudo docker compose run --rm --entrypoint alembic api current    # expect 0007_contact_read_at (head)
sudo docker compose up -d
```

Verify: /admin → Messages lists the contact-form messages, all unread at first.

## 14. Deploying Phase 7 (hardened container, CI, monitoring)

Phase 7 changes how the container runs: as a non-root user (uid 10001), on a read-only
filesystem, with no Linux capabilities and a Docker `HEALTHCHECK`. It also moves the host port
out of `docker-compose.yaml` into `.env` (`API_HOST_PORT`), so the VM no longer needs a local
edit to that file. No database migration.

Do these **in this order**, one block at a time, and stop at the first check that doesn't match.
The live container keeps serving until step 6.

**1. Check where you are and back up `.env`.**

```bash
cd ~/portfolio-be
git status --short          # expect only:  M docker-compose.yaml   (the local "80:8001" edit)
git log -1 --oneline        # expect c9e5314 (Phase 6.3), or a later main commit
PREV=$(git rev-parse --short HEAD); echo "rollback commit: $PREV"
cp .env ".env.backup-before-phase7"
```

**2. Put the port into `.env` (Cloudflare connects to port 80).** The helper first adds a
missing final newline, so the new line can't be glued onto the last setting.

```bash
set_env() { [ -z "$(tail -c1 .env)" ] || echo >> .env; sed -i "/^$1=/d" .env; printf '%s=%s\n' "$1" "$2" >> .env; }
set_env API_HOST_PORT 80
grep -c '^API_HOST_PORT=80$' .env            # must print 1
```

**3. Drop the local compose edit and pull.**

```bash
git checkout -- docker-compose.yaml && git pull --ff-only
```

**4. Before anything restarts, confirm compose will publish port 80.**

```bash
sudo docker compose config | grep -A4 ports   # must show: published: "80"
```

If it shows `"8001"`, `API_HOST_PORT` didn't land in `.env`: fix step 2 before continuing.

**5. Build, then rehearse with the new image (the live container is untouched).**

```bash
sudo docker compose build api
sudo docker compose run --rm --entrypoint python api -c "import src.main; print('settings OK')"
sudo docker compose run --rm --entrypoint alembic api current    # 0007_contact_read_at (head)
```

`settings OK` proves the new code accepts your `.env` (Phase 7 validates `ALLOWED_ORIGINS`
strictly: `https://host` entries only, no paths or `*`) and runs read-only as uid 10001.

**6. Switch over, then wait for the health check** (it runs every 30 s, so `healthy` takes
about 30-40 s; the loop gives up after 90 s):

```bash
sudo docker compose up -d --force-recreate api
for i in $(seq 30); do s=$(sudo docker inspect -f '{{.State.Health.Status}}' portfolio-server); echo "$s"; [ "$s" = healthy ] && break; sleep 3; done
```

**7. Check.**

```bash
sudo docker compose port api 8001                    # 0.0.0.0:80
curl -s -H "Host: api.aashish-regmi.com.np" http://localhost/health        # {"status":"ok"}
curl -s -H "Host: api.aashish-regmi.com.np" http://localhost/health/ready  # {"status":"ok"}
curl -s -D - -o /dev/null -H "Host: api.aashish-regmi.com.np" http://localhost/health | grep -i -E "content-security|x-frame|strict-transport"
sudo docker compose exec api id                      # uid=10001(app) ..., not root
```

Then open https://aashish-regmi.com.np and the admin once.

**Rollback** (if step 6 or 7 fails). The old `docker-compose.yaml` publishes port 8001, so
re-apply the port edit after checking out the old commit (`$PREV` from step 1; if that shell is
gone, use `c9e5314`):

```bash
cd ~/portfolio-be
git checkout "$PREV"
sed -i 's/"8001:8001"/"80:8001"/' docker-compose.yaml
sudo docker compose up -d --build --force-recreate
```

`API_HOST_PORT` in `.env` is harmless for the old code (unknown keys are ignored). Return to
`main` later with `git checkout -- docker-compose.yaml && git switch main`.

**Later deploys** are one command, `scripts/deploy.sh`. Since Phase 8 it pulls the image CI
published instead of building on the VM: see section 15.

Then, when you have time:

- Uptime monitors and (optionally) Sentry: `docs/MONITORING.md`.
- GitHub → each repo → *Settings* → *Rules* → *Rulesets* → new branch ruleset for `main`:
  require a pull request and the CI status checks (backend: *Lint and lockfile*, *Tests*,
  *Migrations on Postgres 18*, *Docker image*), block force pushes.
- GitHub → *Settings* → *Code security*: enable *Dependabot alerts* and *Dependabot security
  updates* (`.github/dependabot.yml` already schedules weekly version updates). Dependabot may
  not bump the uv image in the Dockerfile's `COPY --from=ghcr.io/astral-sh/uv:...` line; when
  you update uv, change it there and `UV_VERSION` in `.github/workflows/ci.yml` together.
- Pitch Me: `docs/PITCH_GO_LIVE.md` (replaces step 10 above).

## 15. Deploying Phase 8 (images from GHCR; the VM stops building)

Since Phase 8, CI builds the API image for `linux/amd64` and `linux/arm64` after every check
passes on `main`, starts each one hardened and smoke-tests it, and publishes it as
`ghcr.io/regmiaashish/portfolio-be:sha-<first 7 characters of the commit>` (plus a moving `main`
tag that only ever moves forward). The VM only pulls it: no more memory-heavy builds on a 1 GB
machine, deploys take seconds, and a rollback is a switch to an older tag. `API_IMAGE` in `.env`
says which image runs. The production VM is **x86_64**, so it runs the `linux/amd64` image.

**Never run `docker compose up --build` (or `docker compose build`) on the VM.** With `API_IMAGE`
set, compose would tag a local build with the CI image's name, and you could no longer tell
them apart. Use `scripts/deploy.sh --build-local` if you ever really need to build there.

### First time

**1. Wait for the first publish.** After the Phase 8 PR is merged, GitHub → the repo →
*Actions* → the run for the merge commit: *Publish multi-arch image* must be green (about
5–10 minutes after the merge). Before that, pulls fail with "denied", because the package
doesn't exist yet.

**2. Let the VM pull it.** The image contains no secrets (`.dockerignore` excludes every `.env`
file, certificates and keys) and the repository is public, so the simplest option is a public
package:

- **Recommended: make the package public.** GitHub → your profile → *Packages* →
  `portfolio-be` → *Package settings* → *Danger Zone* → *Change visibility* → **Public**.
  The VM then pulls without any credentials.
- **Or keep it private** and log the VM in once. Create a **classic** personal access token
  with only the `read:packages` scope and an expiry date (GitHub → *Settings* → *Developer
  settings* → *Personal access tokens* → *Tokens (classic)*). Then on the VM:

  ```bash
  read -rsp "GHCR token (read:packages): " T; echo
  printf '%s' "$T" | sudo docker login ghcr.io -u regmiaashish --password-stdin; unset T
  ```

  Docker stores it in `/root/.docker/config.json`. Renew it before it expires, or deploys stop
  with `the registry refused …`.

**3. Check the VM's architecture is in the image** (both are published, this just confirms):

```bash
uname -m                      # x86_64 on this VM
sudo docker manifest inspect ghcr.io/regmiaashish/portfolio-be:main | grep -E '"architecture"'
```

**4. Prepare.** Back up `.env` **outside** the repository, and check the readiness timeout:

```bash
cd ~/portfolio-be && cp .env ~/env.backup-before-phase8
grep '^HEALTH_READY_TIMEOUT_SECONDS' .env    # nothing, or 10
```

If it prints `HEALTH_READY_TIMEOUT_SECONDS=5` (Phase 7's example value), raise it, because Neon
can take longer than 5 s to wake up (the value isn't a secret):

```bash
set_env() { [ -z "$(tail -c1 .env)" ] || echo >> .env; sed -i "/^$1=/d" .env; printf '%s=%s\n' "$1" "$2" >> .env; }
set_env HEALTH_READY_TIMEOUT_SECONDS 10
```

**5. Deploy.** Pull first, so the new `deploy.sh` runs (the old one would build on the VM):

```bash
cd ~/portfolio-be && git pull --ff-only && scripts/deploy.sh
```

It prints `==> Pulling ghcr.io/regmiaashish/portfolio-be:sha-…`, checks the new image, migrates,
switches `API_IMAGE`, recreates, waits for `healthy`, smoke-tests and ends with
`==> Deployed …`. The Phase 7 build that was running is kept as
`portfolio-be-api:pre-<image id>` (printed at the end), so going back to it is exact.
`.env` now contains `API_IMAGE=ghcr.io/regmiaashish/portfolio-be:sha-…`.

**6. Free the disk** (about a week later, after at least one more successful deploy): the
locally built images from Phase 7 aren't needed any more.

```bash
sudo docker image rm portfolio-be-api:rollback portfolio-be-api:latest 2>/dev/null; sudo docker image prune -f
```

### Every deploy after that

Merge on GitHub, wait until *Publish multi-arch image* is green, then on the VM:

```bash
cd ~/portfolio-be && scripts/deploy.sh
```

If CI hasn't finished publishing the merge yet, it says `not published yet` and retries every
30 s for up to 10 minutes. It refuses to run when another deploy is running, when
`API_HOST_PORT` isn't a port number in `.env`, or when a tracked file was edited on the VM. Before
changing anything it checks, **with the new image**: the compose configuration (image, port,
and the TLS mount when TLS is on), that the TLS certificate and key load (when TLS is on), and
that `alembic current` works. Then it migrates and switches. It keeps the three newest images of
each kind and never removes the running one or the rollback image.

Other modes:

- `scripts/deploy.sh sha-1a2b3c4`: deploy a specific published image. It still runs `git pull`
  first, so the compose file and scripts come from the latest `main`.
- `scripts/deploy.sh --no-pull`: redeploy the image already in `.env` with the current checkout
  and `.env`, without pulling anything. For configuration-only changes (for example the TLS
  cutover in `docs/ORIGIN_TLS.md`); still checked, health-checked and smoke-tested.
- `scripts/deploy.sh --build-local`: build on the VM (fallback; needs the swap file, takes
  minutes).

### Rollback

- **Automatic:** if the new container doesn't become healthy, or `/health` or `/health/ready`
  fail the smoke test, `deploy.sh` switches `API_IMAGE` back to the exact image that was running
  and recreates the container. If the deploy also migrated the database, it prints the downgrade
  command, which runs with the **new** image (only it knows the new revision):

  ```bash
  sudo env API_IMAGE=ghcr.io/regmiaashish/portfolio-be:sha-<new> docker compose run --rm --entrypoint alembic api downgrade <previous revision>
  ```

  Run it only if the old image can't work with the new schema.
- **By hand, to any published version:** `scripts/deploy.sh sha-1a2b3c4` (tags: GitHub →
  *Packages* → `portfolio-be`). The end of every successful deploy prints the tag to go back to.
  If a migration ran since that version, downgrade first with the command above.
- **Back to a kept local image** (`portfolio-be-api:pre-…`, printed by the deploy): set
  `API_IMAGE` to it with `set_env`, then `scripts/deploy.sh --no-pull`.
- **Last resort,** if `deploy.sh` itself can't run: `cp ~/env.backup-before-phase8 .env`, then
  `sudo docker compose up -d --no-build --force-recreate api`.

### Origin TLS (optional, next)

To encrypt the Cloudflare → VM hop and move to SSL **Full (strict)**, follow
`docs/ORIGIN_TLS.md`.

## Rollback

- Code (Phase 8 and later): `scripts/deploy.sh sha-<older commit>`; see section 15. Don't build
  on the VM with `docker compose up --build`.
- Database: run the downgrade with the image that knows the newest revision, for example
  `sudo env API_IMAGE=<that image> docker compose run --rm --entrypoint alembic api downgrade <revision>`,
  or restore the Neon branch from step 2.
