# Portfolio Implementation Plan: Features, Hardening and Frontend Redesign

| | |
|---|---|
| **Date** | 2026-10-13 |
| **Applies to** | `portfolio-website-backend/` (FastAPI) and `Portfolio-Website/` (React + Vite) |
| **Based on** | `FULLSTACK_SYSTEM_OVERVIEW.md` audit and the 2026 Feature Roadmap |
| **Goal** | Ship the 3 roadmap features on a secure backend, behind a fully redesigned frontend |

---

## 1. Where you are vs. where you're going

| Item | Today | Target |
|---|---|---|
| Feature 1: SMTP mailing | Built, but unsafe (HTML injection, silent failures, no persistence) | Escaped, persisted, Turnstile-protected, confirmation email |
| Feature 2: Context engine | Built, but open proxy, no cache, depends on ipify | Cached, rate-limited, IP from proxy headers, timezone overlap and hourly rate |
| Feature 3: Pitch Me (multi-agent) | Not built | LangGraph + Tavily + Gemini streamed over SSE |
| Auth | Public admin self-registration (critical) | Removed or locked down; admin via seed script |
| Frontend | Forked 3D template, heavy, leaky, license risk | Original, fast, accessible design where the live features are the hero |

### Guiding decisions

1. **Security before features.** Feature 3 spends money on every call, so abuse protection comes first.
2. **Redesign by rewriting, not re-skinning.** The current frontend is derived from Moncy Yohannan's template (audit F-HYG-1). A fresh component tree and design system resolves the licensing problem. Re-colouring the old code does not.
3. **The live features are the portfolio.** The hero should show the Context Engine and the Pitch Me pipeline working, instead of a decorative 3D avatar.
4. **One API contract.** A single `api.ts` client, with types generated from `/openapi.json`.

---

## 2. Phase overview

| Phase | Name | Effort | Output |
|---|---|---|---|
| 0 | Security hot-fixes | 1-2 h | Production is no longer exploitable |
| 1 | Backend foundation | 2-3 days | Clean routes, errors, config, migrations, limiter |
| 2 | Frontend foundation and design system | 3-4 days | New app shell, tokens, typography, layout primitives |
| 3 | Feature 1 and 2 (backend + new UI) | 2-3 days | Contact and Context shipped in the new design |
| 4 | Feature 3: Pitch Me | 4-6 days | Streaming multi-agent demo, behind a feature flag |
| 5 | Content, polish, SEO, accessibility | 2-3 days | Real projects, case studies, meta tags, reduced motion |
| 6 | Quality gate and launch | 2-3 days | Tests, CI, headers, monitoring, go-live |

Total: roughly 3 to 4 weeks part-time. Phases 1 and 2 are independent and can run in parallel.

---

## Phase 0: Security hot-fixes (today)

**Goal:** close the exploitable holes before anything else.

- [ ] Run `SELECT email, created_at FROM users WHERE is_superuser;` and delete any account you didn't create
- [ ] Remove `POST /auth/api/v1/register`, or add `Depends(require_superuser)` and drop `is_superuser`/`is_active` from the request schema (B-SEC-1)
- [ ] Move `.dockerignore` from `src/` to the backend root; rebuild the image (B-OPS-1)
- [ ] If any image was ever pushed to a registry, rotate **all** secrets (SMTP, JWT, DB, AWS, Gemini)
- [ ] Escape user input in `contact_template.py` with `html.escape`; strip CR/LF from the email subject (B-SEC-2)
- [ ] Delete the misleading log lines in `main.py` (rate-limit warning and Gemini joke)

**Done when:** `register` cannot create a superuser, `docker run ... env` shows no `.env` secrets baked in, and a contact message containing `<a href=evil>` renders as plain text.

---

## Phase 1: Backend foundation

**Goal:** a clean base that the three features can sit on.

### 1.1 Routing and errors
- [ ] Mount everything under one `APIRouter(prefix="/api/v1")`; give `auth_router` `prefix="/auth"` only (B-COR-1)
- [ ] Register `@app.exception_handler(AppException)` returning a normalised `{success:false, message, errors}` body (B-COR-2)
- [ ] Make `UserRepository.get_user_by_id` return `None`; parse `sub` with `UUID()` in a try
- [ ] Fix `APIResponse` misuse; login returns 401; logout is unauthenticated and idempotent (B-COR-3/4, B-SEC-4)
- [ ] Add `/health` and `/health/ready` (DB `SELECT 1`); replace the `/` route
- [ ] Hide `/docs` and `/redoc` when `ENV=prod` (B-SEC-8)

### 1.2 Config
- [ ] Give unused settings defaults or remove them (`GEMINI_API_KEY` is about to be used, so keep it); `allowed_origins: Annotated[list[str], NoDecode]` (B-COR-12)
- [ ] Add: `env`, `tavily_api_key=""`, `pitch_enabled=False`, `pitch_daily_cap=100`, `turnstile_secret=""`, `hourly_rate_gbp`, `context_mock`
- [ ] Commit a `.env.example`

### 1.3 Rate limiting and client IP
- [ ] Single shared `Limiter` in `src/core/rate_limit.py`
- [ ] Key function reads `CF-Connecting-IP` (when the peer is a Cloudflare/trusted proxy), else the peer address
- [ ] Set `FORWARDED_ALLOW_IPS` for Gunicorn (B-OPS-3). **Verify this against your actual deployment path.**

### 1.4 Database
- [ ] Collapse the two `create_table('users')` migrations into one; `alembic stamp head` on the existing DB (B-DB-1)
- [ ] `pool_pre_ping=True`, `pool_recycle=300` for Neon (B-DB-2)
- [ ] Add migration: `contact_messages` (id, name, email, message, status, error, created_at)
- [ ] Run `alembic upgrade head` as a deploy step, not per worker

### 1.5 Dependencies
- [ ] Replace `python-jose` with `PyJWT` (B-SEC-3)
- [ ] `uv add --dev ruff pytest pytest-asyncio respx`
- [ ] `uv add langgraph langchain-google-genai tavily-python sse-starlette cachetools`

**Done when:** `/api/v1/auth/login` works, a deleted-user token returns 401 (not 500), `alembic upgrade head` succeeds on an empty database, and `make lint` runs.

---

## Phase 2: Frontend foundation and design system

**Goal:** a new, original frontend shell. No feature work yet.

### 2.1 Design direction (proposal, change freely)

**Subject and audience:** a backend and AI engineer based in Kathmandu. The audience is recruiters and engineering managers who skim for 30 seconds. The page's job is to prove, quickly, that the work is real and running.

**The one memorable thing:** the hero is **live**. It greets the visitor using the Context Engine (their city, weather, local time, overlap hours), and sits next to a compact "Pitch me" panel where agents visibly work. Everything else stays quiet so that this reads as the centrepiece.

**Palette** (replace the default dark-with-neon and cream-with-terracotta looks)

| Token | Hex | Use |
|---|---|---|
| `--ink` | `#0F1E33` | Primary text, dark surfaces |
| `--snow` | `#F4F6F8` | Page background (light theme) |
| `--glacier` | `#DCE6F0` | Panels, dividers |
| `--marigold` | `#E8A317` | Single accent: focus, live indicators, primary buttons |
| `--signal` | `#2E5BFF` | Links and agent "running" state |
| `--ok` / `--err` | `#1F9D6B` / `#D64545` | Status only |

Marigold is the Kathmandu marigold, used once per view. Provide a dark theme by redefining tokens under `prefers-color-scheme` and `[data-theme="dark"]`. Check every pair for WCAG AA contrast.

**Typography**
- Headings: **Bricolage Grotesque** (variable, distinctive but readable)
- Body: **Public Sans** at 17-18px, line-height 1.6, measure under 70 characters
- Monospace is used **only** for the agent log (it is real data there)
- Scale: 14 / 17 / 21 / 28 / 40 / 64 (clamp for fluid headings)
- Self-host the fonts (WOFF2, `font-display: swap`) to avoid third-party requests

**Layout concept**

```
┌──────────────────────────────────────────────────────────────┐
│ Aashish Regmi                      Work  Pitch  Contact  ◐   │
├───────────────────────────────┬──────────────────────────────┤
│ Good evening from Kathmandu.  │  Pitch me                    │
│ Clear skies, 18°C.            │  [ Company ] [ Role ] [Run]  │
│ We overlap 4 h a day.         │  ● Researcher   done         │
│                               │  ● Matchmaker   running...   │
│ I build backends that         │  ○ Writer       idle         │
│ stay up and AI that ships.    │  "Hi Spotify, I ..."▌        │
├───────────────────────────────┴──────────────────────────────┤
│ Selected work (3-5 projects, each with a real screenshot)    │
├──────────────────────────────────────────────────────────────┤
│ Stack and how I work · Career · Contact                      │
└──────────────────────────────────────────────────────────────┘
```

- Left-aligned text throughout; no centred paragraphs
- Radius scale with intent: 4px inputs, 8px panels, 999px status pills (not one radius everywhere)
- Borders and dividers carry meaning (section breaks, agent states). No decorative gradients.
- Avoid: number-labelled sections (01/02/03) unless the content is a sequence, uppercase eyebrow labels above every heading, `→` on every link

**Motion rules**
- One orchestrated moment: the hero greeting resolving as context data arrives
- Everything else responds to user action (agent chips changing state, streamed text, form feedback)
- No scroll hijacking, no custom cursor, no splash screen
- Respect `prefers-reduced-motion` everywhere

### 2.2 What to remove and what to keep

| Remove | Why |
|---|---|
| `ScrollSmoother`, `SplitText`, `GsapScroll`, `splitText.ts`, `resizeUtils.ts` | Source of F-RT-1, F-RT-5; hijacks scroll |
| `Cursor`, `Loading`, `LoadingProvider` | Splash screen hides content, custom cursor hurts a11y |
| Encrypted model pipeline (`character.enc`, `encrypt.cjs`, `Scene.tsx`) | Heavy, leaky, obfuscation only |
| `@react-three/*`, `three`, `rapier`, `postprocessing`, `@react-three/cannon`, `@vercel/analytics` | Largest bundle cost |
| `test.js`, 132-byte `character.glb` stub, `react.svg`, unused SVGs | Stray files |

**Optional later:** a small lazy-loaded 3D or canvas element, loaded after first paint, only on desktop, never blocking content. Decide this after Phase 5. The page must look finished without it.

### 2.3 Technical foundation
- [ ] Keep Vite + React + TypeScript. Add **Tailwind v4** with tokens in CSS variables, or CSS Modules if you prefer to write plain CSS
- [ ] Add `react-router` only if you want case-study pages (`/work/:slug`); otherwise stay single-page with anchor sections
- [ ] Add `motion` (small) for state transitions; keep GSAP out unless you truly need it
- [ ] `src/lib/api.ts`: one `API_URL`, one `request()` wrapper, normalised error parsing; fail the build if `VITE_API_URL` is unset
- [ ] `openapi-typescript` script: `npm run gen:api` generates `src/lib/api-types.ts`
- [ ] Rename the package, rewrite the README, add attribution only for anything you really reuse (F-HYG-1)
- [ ] `.env.development` for localhost, delete `.env.local` (§8 of the audit)
- [ ] Fix ESLint to zero errors from the start; add `typecheck`, `lint`, `build` scripts

### 2.4 Folder structure

```
src/
├── app/            App.tsx, providers, error boundary
├── styles/         tokens.css, base.css, utilities
├── lib/            api.ts, api-types.ts, format.ts
├── data/           projects.ts, career.ts, skills.ts, profile.ts
├── hooks/          useVisitorContext.ts, usePitchStream.ts, useTheme.ts
├── components/
│   ├── layout/     Header, Footer, Section, Container
│   ├── ui/         Button, Input, Chip, StatusDot, Toast
│   └── sections/   Hero, Work, Stack, Career, PitchMe, Contact
└── assets/         optimised images (AVIF/WebP), self-hosted fonts
```

### 2.5 Layout primitives to build first
- [ ] `Container`, `Section`, `Stack`, `Grid`
- [ ] `Button` (primary, secondary, ghost), `Input`, `Textarea`, `Chip`, `StatusDot`
- [ ] `Header` with a theme toggle and a mobile menu; visible keyboard focus on everything
- [ ] Responsive checks at 360, 768, 1024, 1440 px

**Done when:** the new shell renders with real header, footer, theme toggle, and placeholder sections; Lighthouse accessibility is 95+; the production bundle is a small fraction of today's (target: under 250 KB gzipped JS for first load).

---

## Phase 3: Feature 1 and 2 (backend + new UI)

### 3.1 Feature 1: Contact (SMTP)

**Backend**
- [ ] Persist to `contact_messages` first (`status="pending"`), then schedule the send, then set `sent` or `failed`
- [ ] `smtplib.SMTP(host, port, timeout=15)`; wrap in try/except with `logger.exception`
- [ ] Schema: `name` max 100, `message` 1-1000, honeypot field `website` (must be empty), optional `turnstile_token`
- [ ] Server-side Turnstile verification via `httpx` to Cloudflare `siteverify`
- [ ] `send_confirmation(name, email)`: short plain template that does not echo the message. Enable **only after** Turnstile and rate limiting are live, otherwise it can be abused to mail third parties
- [ ] Fix `AVATAR_URL`; use timezone-aware timestamps

**Frontend: `Contact` section**
- [ ] Labelled fields, inline validation, `maxLength` limits, honeypot hidden with CSS and `tabIndex={-1}`
- [ ] Turnstile widget (explicit render, theme matches the site)
- [ ] States: idle, sending, success, error. The error text comes from the normalised `message`
- [ ] Button copy says what happens: "Send message" then "Message sent"
- [ ] Keep a visible `mailto:` fallback for when the API is down

### 3.2 Feature 2: Context engine

**Backend**
- [ ] Flow stays: geo first, then `asyncio.gather(weather, fx)`
- [ ] `TTLCache`: geo + weather 30 min per IP, FX 6 h
- [ ] Shared `httpx.AsyncClient` created in the FastAPI lifespan
- [ ] `@limiter.limit("30/minute")`, `response_model=VisitorContext`
- [ ] Ignore `?ip=` unless `settings.env != "prod"`; read the IP from trusted headers
- [ ] New fields: `local_time`, `utc_offset_minutes`, `timezone_overlap_hours`, `hourly_rate_gbp`, `hourly_rate_local`
- [ ] Handle half-hour zones (Nepal is UTC+5:45), and clamp overlap to 0 or more
- [ ] Graceful degradation: if weather or FX fails, return what you have with those fields `null`

**Frontend: `Hero`**
- [ ] `useVisitorContext()` calls `/api/v1/context` with no query parameters; remove the ipify call (F-SEC-1)
- [ ] Skeleton state, then the greeting resolves (the page's one orchestrated moment)
- [ ] If the call fails, show a generic greeting. The hero must never look broken
- [ ] Greeting copy example: "Good evening from Kathmandu. It's clear and 18°C here. You're in London, so we overlap about 4 hours a day."

**Done when:** a visitor sees a personalised greeting with no third-party calls from their browser, and repeated page loads hit the cache (check logs).

---

## Phase 4: Feature 3: Pitch Me (multi-agent)

Ship it dark: `pitch_enabled=False` until everything below is verified.

### 4.1 Backend

```
src/
├── api/v1/pitch_router.py
├── schemas/pitch_schema.py
├── services/pitch/{graph.py, tools.py, stream.py}
└── data/profile.md
```

- [ ] `profile.md`: your real skills, 5 projects, and career in plain markdown. No vector DB is needed at this size
- [ ] LangGraph with 3 nodes: `researcher` (Tavily) → `matchmaker` (LLM + profile) → `writer` (LLM, streamed)
- [ ] LLM is Gemini (`GEMINI_API_KEY` already exists); keep it behind a single factory so it is swappable
- [ ] `GET /api/v1/pitch/stream?company=&role=` using `EventSourceResponse`, `ping=15`, `X-Accel-Buffering: no`
- [ ] Event types: `agent` (running or done), `token`, `done`, `error`
- [ ] Inputs validated by regex and length; research text wrapped in `<research>` and labelled as untrusted
- [ ] 45 s overall timeout; stop work on client disconnect

### 4.2 Cost and abuse controls (required before enabling)
- [ ] `@limiter.limit("3/minute")` per IP
- [ ] Turnstile token required (query param)
- [ ] Global daily cap (`pitch_daily_cap`) with a friendly "demo is resting, try tomorrow" response
- [ ] 24 h cache on `(company.lower(), role.lower())`; replay cached results as a fast stream
- [ ] Kill switch via `pitch_enabled`
- [ ] Provider-side spending limits set on the Gemini and Tavily dashboards
- [ ] Log per-request cost estimate and duration

### 4.3 Frontend: `PitchMe` section
- [ ] `usePitchStream()` with `EventSource`; **close on `done` and on `error`** so it can't auto-reconnect and re-run the paid pipeline
- [ ] UI: Company and Role inputs, "Pitch me" button, three agent chips (idle, running with a pulse, done), a log panel (monospace), and the streamed pitch with a caret
- [ ] Live region (`aria-live="polite"`) for status; pitch text is selectable and copyable
- [ ] "Copy pitch" button; error text explains what to do ("Rate limit reached. Try again in a minute.")
- [ ] Reduced motion: no pulse or caret animation, show text as it arrives
- [ ] Embed a compact version in the hero, with the full panel in its own section

### 4.4 Verify
- [ ] `curl -N "localhost:8001/api/v1/pitch/stream?company=Spotify&role=Backend%20Engineer"` streams incrementally
- [ ] Test through the **real proxy**, not just localhost: tokens must arrive one by one, not in one burst
- [ ] Hold a stream open for over 100 s with pings to confirm Cloudflare doesn't drop it
- [ ] Prompt-injection test: a company name like `Ignore previous instructions` is rejected or ignored
- [ ] With 2 Gunicorn workers the effective limit is about 6/min; acceptable for now (Redis later)

**Done when:** a stranger can run a pitch end to end on production, and the cost and daily cap hold under a scripted burst test.

---

## Phase 5: Content, polish, SEO and accessibility

### 5.1 Content (this is what actually impresses)
- [ ] `data/projects.ts`: 3 to 5 projects, each with the problem, your role, stack, one measurable result, and links (repo, demo)
- [ ] Real screenshots, optimised (AVIF/WebP, correct dimensions, `loading="lazy"` below the fold). Fix the 5 broken `.webp` references (F-RT-3)
- [ ] One short case study per flagship project (what was hard, what you decided, what you'd change)
- [ ] Career: 3 entries, outcome-focused, no filler
- [ ] Resume PDF in `public/`; the button links to it (F-HYG-6)
- [ ] Write the copy in plain, specific language; sentence case; buttons that say what they do

### 5.2 Performance
- [ ] Lazy-load below-the-fold sections; no layout shift (set image sizes)
- [ ] Preload the one heading font weight; subset fonts
- [ ] Targets: LCP under 2.0 s on a mid-range phone, CLS under 0.05, total JS under 250 KB gzipped

### 5.3 SEO and sharing
- [ ] Title, meta description, canonical URL, favicon set (F-SEO-1)
- [ ] Open Graph and Twitter card with a real 1200×630 image
- [ ] `robots.txt`, `sitemap.xml`, JSON-LD `Person` markup

### 5.4 Accessibility and privacy
- [ ] Full keyboard navigation, visible focus, skip link, semantic landmarks
- [ ] `prefers-reduced-motion` and `prefers-color-scheme` honoured
- [ ] Icon-only links have `aria-label` (F-A11Y-2)
- [ ] Remove AdSense, or add a consent banner (F-SEC-2). For a portfolio, removing it is cleaner and looks more professional

---

## Phase 6: Quality gate and launch

### 6.1 Tests
- [ ] Backend (pytest + httpx `AsyncClient` + `respx`): contact validation and escaping, context with mocked providers, auth cycle, **regression test that `register` cannot escalate**, pitch graph with a fake LLM and mocked Tavily (CI never spends money)
- [ ] Frontend (Vitest + Testing Library): `Contact` states, `useVisitorContext` fallbacks, `usePitchStream` closes on `done`/`error`
- [ ] Playwright smoke test: page loads, hero resolves, form submits against a mock API

### 6.2 CI/CD
- [ ] Backend: `ruff` → `pytest` → build image → migrate → deploy
- [ ] Frontend: `npm ci` → `lint` → `typecheck` → `test` → `build` → `wrangler deploy`
- [ ] Dependabot or Renovate for both repos

### 6.3 Production hardening
- [ ] Security headers via `_headers` (CSP allowing only your API, Turnstile and fonts; `X-Content-Type-Options`; `Referrer-Policy`; `Permissions-Policy`) (F-SEC-4)
- [ ] CORS restricted to your exact origins
- [ ] Non-root Docker user, pinned `uv`, `HEALTHCHECK` (B-OPS-4/5)
- [ ] Uptime monitor on `/health/ready`; error tracking (e.g. Sentry free tier)
- [ ] Redis-backed rate limiting if you ever run more than one host (B-OPS-6)

### 6.4 Launch checklist
- [ ] All Phase 0 items verified in production
- [ ] `pitch_enabled=True` only after the cost controls are confirmed
- [ ] Spending alerts set on Gemini, Tavily and any SMTP provider
- [ ] Test on a real phone over mobile data
- [ ] Share the link and watch logs for the first 48 hours

---

## Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Abuse of Pitch Me burns API budget | High | High | Turnstile, rate limit, daily cap, cache, provider limits, kill switch |
| Streaming buffered by proxy or Cloudflare | Medium | High | `X-Accel-Buffering`, pings, test through the real proxy early (Phase 1) |
| Redesign stalls on perfectionism | Medium | Medium | Ship Phase 2 shell with placeholder content, then fill |
| LLM output is inaccurate or off-brand | Medium | Medium | Profile-only grounding, short 3-sentence output, review samples before launch |
| Free-tier API limits (ipapi.co, Tavily) | Medium | Medium | Caching, fallbacks (`ipwho.is`), graceful degradation |
| Template licensing complaint | Low | High | Full rewrite in Phase 2; no upstream code, assets or copy retained |

---

## Suggested schedule

| Week | Focus |
|---|---|
| 1 | Phase 0 (day 1), Phase 1, start Phase 2 design tokens and shell |
| 2 | Finish Phase 2, Phase 3 (contact and context in the new UI) |
| 3 | Phase 4 (Pitch Me backend, then frontend), dark launch |
| 4 | Phase 5 content and polish, Phase 6 tests, CI and launch |

---

## Final decisions

1. **Visual direction:** ink-navy + marigold, as specified in Phase 2.1.
2. **3D:** dropped entirely. Remove Three.js, R3F, rapier, postprocessing and the encrypted model pipeline.
3. **Project pages:** separate case-study pages at `/work/:slug` using `react-router`.
4. **Theme:** follow the system preference, with a manual toggle.
5. **AdSense:** remove.
6. **LLM for Pitch Me:** Gemini, behind a swappable factory.
