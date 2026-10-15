# Progress

What has been built, shipped and changed so far, and what is next. The plan itself lives in
[`PORTFOLIO_IMPLEMENTATION_PLAN.md`](PORTFOLIO_IMPLEMENTATION_PLAN.md); this file is the record.
Update it at the end of every phase.

_Last updated: 2026-10-15 (Phase 8 in progress)._

## At a glance

| | |
|---|---|
| Website | https://aashish-regmi.com.np (React SPA on Cloudflare Workers, deploys on merge to `main`) |
| API | https://api.aashish-regmi.com.np (FastAPI in Docker on an AWS EC2 VM in `ap-south-1`, x86_64, 911 MB RAM + 2 GB swap, behind Cloudflare) |
| Database | Neon Postgres 18, project "Portfolio backend V2", `ap-southeast-1` (Singapore); Alembic head `0007_contact_read_at` |
| Repos | backend [`regmiaashish/portfolio-be`](https://github.com/regmiaashish/portfolio-be), frontend [`regmiaashish/portfolio`](https://github.com/regmiaashish/portfolio) |
| Tests | backend 566 passed (pytest), frontend 448 unit (Vitest) + 16 browser smoke tests (Playwright) |
| CI | GitHub Actions on every PR and push to `main` in both repos; backend `main` requires its 4 checks |

## Completed phases

Dates are merge dates. PR numbers are per repo (backend `portfolio-be`, frontend `portfolio`).

| Phase | What it delivered | Backend PR | Frontend PR | Merged |
|---|---|---|---|---|
| 0. Security hot-fixes | Registration can't create admins, contact email escaping, Docker context excludes secrets, read-only prod smoke test | #1 | | 2026-10-13 |
| 1. Backend foundation | `/api/v1` routing, one error shape `{success, message, errors}`, `/health` + `/health/ready`, typed settings with `SecretStr` and prod guards, shared rate limiter keyed on a trusted client IP, clean Alembic history (0001-0002), PyJWT | #2 | | 2026-10-14 |
| 2. Frontend foundation | Rewrite of the template: design system (ink navy + marigold), tokens, layout primitives, routing, typed API client | | #1 | 2026-10-14 |
| 3. Contact + Context Engine | Contact messages stored in Postgres, emailed in the background, honeypot + optional Turnstile; visitor context (location, local time, weather, FX) with caching and a time-zone-overlap greeting | #3 | #2 | 2026-10-14 |
| 4. Pitch Me | LangGraph researcher -> matchmaker -> writer pipeline, Gemini behind a swappable LLM factory, Tavily research, SSE streaming, cost controls (kill switch, rate limit, cache, Postgres daily cap). **Built but switched off** in production | #4 | #3 | 2026-10-14 |
| 4.5 Admin CMS | `/admin`: profile, projects, career, skills editors with validation, drag ordering, S3 images; content API with ETag caching | #5 | #4 | 2026-10-14 |
| 5. Content, SEO, a11y | Résumé upload and page, `sitemap.xml`, admin Pitch history, meta/OG/JSON-LD, keyboard and screen-reader pass | #6 | #5 | 2026-10-14 |
| 6. Console redesign | New pages (`/`, `/work`, `/work/:slug`, `/pitch`, `/about`, `/contact`, `/resume`), status bar with live API latency, résumé download endpoint | #7 | #6 | 2026-10-14 |
| 6.1 Uploads via the API | Admin uploads go through `POST /api/v1/admin/uploads/file` (type sniffed from bytes, bounded reads), so the bucket needs no CORS | #8 | #7 | 2026-10-14 |
| 6.2 Media via the API | `GET/HEAD /api/v1/media/{key}` serves uploads; the S3 bucket stays private | #9 | #8 | 2026-10-14 |
| 6.3 Contact inbox | Admin **Messages** tab: unread badge, search and filters, read/unread, retry failed emails, delete; migration 0007; visitor data kept out of logs | #10 | #9 | 2026-10-14 |
| 7. Quality gate | Non-root read-only container with health check, pinned images, security headers on the API and a build-generated CSP on the site, CI + Dependabot in both repos, Playwright smoke tests, optional Sentry (no visitor data), monitoring docs, Pitch Me go-live runbook and pre-flight script, `scripts/deploy.sh` | #11 | #10 | 2026-10-15 |
| Dependency fixes | Backend: `sqlalchemy[asyncio]`, Dependabot no longer proposes Python version jumps. Frontend: React 19, Vite 8, Vitest 5, ESLint 10 (a11y lint kept via `eslint-plugin-jsx-a11y-x`), React Compiler lint findings fixed, `npm audit` 12 -> 0 | #14 | #17 | 2026-10-15 |

## Operations log

| Date | Change |
|---|---|
| 2026-10-14 | First deploy of the refactored backend to the EC2 VM (it had been on a months-old commit). Database stamped to `0001_baseline_users`, migrated to head, content seeded, admin account reset |
| 2026-10-14 | Cloudflare 521 fixed (origin port/SSL setting); API live behind Cloudflare with `CLIENT_IP_HEADER=cf-connecting-ip` and the Cloudflare ranges as trusted proxies |
| 2026-10-14 | Database moved from Neon `us-east-1` to a new project in `ap-southeast-1` with `pg_dump`/`pg_restore` (row counts verified). Content API went from 2.7-5.3 s to about 0.6-0.9 s; `/health/ready` stopped returning 503 |
| 2026-10-15 | Cloudflare Configuration Rule added: SSL "Full" for `documind.aashish-regmi.com.np` only (it broke with a redirect loop under the zone's Flexible mode) |
| 2026-10-15 | Phase 7 deployed. The first build on the VM ran out of memory (911 MB RAM, no swap); a 2 GB `/swapfile` was added and the deploy succeeded. Host port now comes from `API_HOST_PORT=80` in `.env` |
| 2026-10-15 | Backend `main` protected by a ruleset (PRs only, 4 required CI checks, no force pushes or deletion) |

## In progress

**Phase 8: deploy pipeline and origin TLS** (branch `phase-8-deploy-pipeline`; built and reviewed,
waiting to be merged and deployed)
- **Images built in CI.** Every green push to `main` builds the image natively for `linux/amd64`
  (what the x86_64 VM runs) and `linux/arm64`, starts each one hardened and checks it is healthy,
  then publishes one multi-arch image to `ghcr.io/regmiaashish/portfolio-be` tagged
  `sha-<commit>` (every commit, immutable) and `main` (only while that commit is still the tip).
  Pull requests never publish.
- **The VM never builds.** `scripts/deploy.sh` pulls the image for the latest commit (waiting for
  CI if needed), checks it before changing anything (compose config, TLS certificate and key,
  migrations), switches, waits for healthy, smoke-tests, and on failure goes back automatically to
  the exact previous image. `scripts/deploy.sh sha-<old>` rolls back by hand; `--no-pull`
  re-deploys after config-only changes; a lock prevents overlapping deploys.
- **HTTPS from Cloudflare to the VM** with a Cloudflare Origin Certificate, so the API can move
  from SSL "Flexible" to "Full (strict)" ([`ORIGIN_TLS.md`](ORIGIN_TLS.md)).
- **Monitoring that lets the free Neon database sleep:** frequent checks hit `/health`,
  `/health/ready` at most hourly; readiness timeout raised from 5 to 10 s for Neon cold starts.
- Two independent reviews (CI publishing; deploy and TLS safety) found no production risk; all
  their findings are fixed. Tests: 566 passed.

## Next

1. **Phase 9: Pitch Me go-live.** Code, cost controls and runbook are ready
   ([`PITCH_GO_LIVE.md`](PITCH_GO_LIVE.md)); needs a Gemini key, a Tavily key and Turnstile.
2. **Content.** Case studies for the flagship projects (problem, role, decisions, results).
3. **Performance.** React 19 grew the main bundle from 85.6 to 109.9 kB gzipped (budget 250 kB);
   look at trimming it and at LCP on a mid-range phone.

## Owner tasks (outside the code)

See [`OPS_RUNBOOK.md`](OPS_RUNBOOK.md) for the steps.

- [ ] Rotate the secrets that were exposed in chat (Gmail app password, AWS keys, Gemini key); the JWT secret and the database password are already new
- [ ] Delete the old Neon `us-east-1` project, then the VM's `.env.backup-*` files
- [ ] Allow port 80 (and 443 after Phase 8) only from Cloudflare's IP ranges
- [ ] Elastic IP for the VM, and a reboot ("System restart required")
- [ ] Uptime monitors ([`MONITORING.md`](MONITORING.md)): `/health` every 5 minutes, never `/health/ready` that often
- [ ] After Phase 8 merges: make the GHCR package `portfolio-be` public, deploy with `scripts/deploy.sh`, then the TLS cutover ([`ORIGIN_TLS.md`](ORIGIN_TLS.md))
- [ ] Turnstile on the contact form
- [ ] Dependabot: rebase and merge backend #13 once green, close #12 (Python 3.14), merge frontend #11
- [ ] Frontend ruleset on `main` (postponed by choice for now)

## Known limitations

- The VM has 911 MB of RAM; it relies on the 2 GB swap file. Phase 8 removes builds from the VM.
- Neon's free plan sleeps the database after a few idle minutes; the first request afterwards can
  take a few seconds.
- Cloudflare -> VM traffic is plain HTTP until the Phase 8 TLS cutover.
