# Phase 4.5: Content admin (CMS) contract

The super admin edits everything the public site shows from `/admin` on the frontend.
Content lives in Postgres; images live in the existing S3 bucket. This document is the single
source of truth for the backend and frontend. Change it first, then both sides.

## Decisions

| Topic | Decision |
|---|---|
| Editable | Everything public: profile, projects (incl. images and case studies), career, skills |
| Images | Existing S3 bucket, using the `AWS_*` / `S3_BUCKET_NAME` credentials already in the server's `.env` (same variable names). Browser uploads straight to S3 with a short-lived **presigned POST**; the API never receives the file |
| Admin UI | `/admin` route in the same frontend, lazy-loaded (visitors never download admin code), `noindex` |
| Auth | Existing super-admin login (HttpOnly JWT cookies, `/api/v1/auth/*`). Every admin endpoint requires a superuser |
| CSRF | Admin and auth cookies are `SameSite=Lax` on the API host (`api.aashish-regmi.com.np` is same-site with `aashish-regmi.com.np`). Additionally every **mutating** admin request must send `X-Requested-With: portfolio-admin` (forces a CORS preflight) **and** have an `Origin` in `ALLOWED_ORIGINS`; otherwise 403 |
| Public rendering | The public site fetches `GET /api/v1/content` at runtime and falls back to a snapshot bundled at build time, so it never renders empty when the API is down |

## Data model (migration `0004_site_content`)

- `site_profile` — exactly one row (`id = 1`): `name`, `role`, `location`, `tagline`, `summary`,
  `email`, `phone_display` (nullable), `phone_href` (nullable), `socials` (JSONB list of
  `{kind, label, href}`), `updated_at`.
- `projects` — `id` (UUID), `slug` (unique), `name`, `category`, `summary`, `stack` (JSONB list of
  strings), `repo_url`, `demo_url`, `image_key`, `image_alt`, `image_width`, `image_height` (all
  nullable), `case_study` (JSONB, nullable: `{problem, role, decisions: string[], result}`),
  `published` (bool, default false), `position` (int), `created_at`, `updated_at`.
- `career_entries` — `id`, `role`, `company`, `period`, `summary`, `position`, timestamps.
- `skill_groups` — `id`, `title`, `description`, `skills` (JSONB list of strings), `position`,
  timestamps.

`position` orders items ascending. Seeding: `scripts/seed_content.py` loads
`src/data/seed_content.json` (today's content from the frontend's `src/data/*.ts`) **only into
empty tables** (idempotent; safe to re-run).

## Validation (both sides enforce the same limits)

| Field | Rule |
|---|---|
| `slug` | `^[a-z0-9]+(-[a-z0-9]+)*$`, 1–60, unique (409 on conflict) |
| profile `name`, `role`, project `name`, career `role`, `company` | 1–100 |
| `location`, project `category`, skill `title` | 1–60 |
| `tagline` | 1–160 |
| profile `summary` | 1–800 |
| project `summary`, skill `description` | 1–300 |
| career `summary` | 1–600 |
| career `period` | 1–20 |
| `stack` (project) | ≤ 15 items, each 1–30 |
| `skills` (group) | 1–20 items, each 1–30 |
| URLs (`repo_url`, `demo_url`, social `href`) | `https://` or `http://`, ≤ 300; `phone_href` must start with `tel:` |
| `email` | valid email |
| `phone.display` / `phone.href` | 1–40 / 5–60 and starts with `tel:` (whole `phone` may be `null`) |
| `socials` | ≤ 8; `kind` one of `github`, `linkedin`, `instagram`, `x`, `website`; `label` 1–30 |
| `case_study` | `problem`, `role`, `result` 1–2000; `decisions` 1–8 items, each 1–300 |
| `image` | `key` must match the format `/admin/uploads` issues: `<S3_UPLOAD_PREFIX>projects/<uuid4>.<png\|jpg\|webp\|avif>`; `alt` 1–200; `width`, `height` 1–10000 |
| upload `filename` | 1–200 (only used for logging; the key's extension comes from `content_type`) |
| Request bodies | `extra="forbid"` |

## Public endpoint

`GET /api/v1/content` (no auth, `60/minute`) → 200

```json
{
  "profile": {
    "name": "…", "role": "…", "location": "…", "tagline": "…", "summary": "…",
    "email": "…",
    "phone": { "display": "+977 …", "href": "tel:+977…" } | null,
    "socials": [{ "kind": "github", "label": "GitHub", "href": "https://…" }]
  },
  "projects": [{
    "id": "uuid", "slug": "…", "name": "…", "category": "…", "summary": "…",
    "stack": ["…"], "repo_url": "…" | null, "demo_url": "…" | null,
    "image": { "url": "https://…", "alt": "…", "width": 1200, "height": 750 } | null,
    "case_study": { "problem": "…", "role": "…", "decisions": ["…"], "result": "…" } | null
  }],
  "career": [{ "id": "uuid", "role": "…", "company": "…", "period": "…", "summary": "…" }],
  "skills": [{ "id": "uuid", "title": "…", "description": "…", "skills": ["…"] }],
  "updated_at": "ISO 8601"
}
```

- Only `published` projects, all lists in `position` order.
- `image.url` = `S3_PUBLIC_BASE_URL` + `/` + key (default base
  `https://{S3_BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com`).
- Headers: `Cache-Control: public, max-age=60, stale-while-revalidate=600` and a strong `ETag`;
  `If-None-Match` → 304.
- If the profile row does not exist yet: 503 standard error (`errors.code = "content_unavailable"`);
  the frontend then uses its bundled snapshot.

## Admin endpoints

All under `/api/v1/admin`, superuser cookie required (401 / 403 otherwise; frontend sends
`credentials: "include"`). Mutating methods (POST/PUT/DELETE) also need the CSRF header and an
allowed `Origin` (403 `errors.code = "csrf"` otherwise). Errors use the standard
`{success: false, message, errors}` shape; 422 for validation, 404 unknown id, 409 slug conflict.

| Method & path | Body | Success |
|---|---|---|
| `GET /admin/content` | — | 200: same shape as public, but `projects` include unpublished ones and every project has `published`, `position` and `image.key`; career/skills include `position`. Before seeding, `profile` and `updated_at` are `null` (create the profile with `PUT /admin/profile`) |
| `PUT /admin/profile` | full profile (as public `profile`) | 200: profile |
| `POST /admin/projects` | ProjectInput* | 201: project (admin shape) |
| `PUT /admin/projects/{id}` | ProjectInput* | 200: project |
| `DELETE /admin/projects/{id}` | — | 204 (its S3 image is deleted best-effort) |
| `PUT /admin/projects/order` | `{"ids": [uuid, …]}` (must be exactly all project ids) | 204 |
| `POST /admin/career`, `PUT /admin/career/{id}`, `DELETE /admin/career/{id}`, `PUT /admin/career/order` | CareerInput `{role, company, period, summary}` / `{"ids": […]}` | 201 / 200 / 204 / 204 |
| `POST /admin/skills`, `PUT /admin/skills/{id}`, `DELETE /admin/skills/{id}`, `PUT /admin/skills/order` | SkillGroupInput `{title, description, skills}` / `{"ids": […]}` | 201 / 200 / 204 / 204 |
| `POST /admin/uploads` | `{"filename": "…", "content_type": "image/png", "size": 123456}` | 200: `{"url": "…", "fields": {…}, "key": "portfolio/projects/<uuid>.png", "public_url": "…", "max_bytes": 5242880}` |

\*ProjectInput: `{slug, name, category, summary, stack, repo_url, demo_url,
image: {key, alt, width, height} | null, case_study | null, published}`. Replacing or removing
an image deletes the old S3 object best-effort.

Uploads: allowed `content_type` = `image/png`, `image/jpeg`, `image/webp`, `image/avif`;
`size` ≤ `S3_MAX_UPLOAD_MB` (default 5). The presigned POST policy pins the exact key, the
content type and a `content-length-range`, and expires in 5 minutes. The browser sends
`multipart/form-data` to `url` with every `fields` entry followed by `file`. When S3 is not
configured (`S3_BUCKET_NAME` empty) → 503 `errors.code = "uploads_disabled"`.

## Settings (backend)

Restored with the **same names** as the existing `.env`: `AWS_ACCESS_KEY_ID`,
`AWS_SECRET_ACCESS_KEY` (secret), `AWS_REGION`, `S3_BUCKET_NAME` — all optional (empty =
uploads disabled). New: `S3_PUBLIC_BASE_URL` (optional; CloudFront or custom domain),
`S3_UPLOAD_PREFIX` (default `portfolio/`), `S3_MAX_UPLOAD_MB` (default 5).

## Frontend

- `/admin` (lazy chunk, `noindex`): login → dashboard with Profile, Projects, Career, Skills.
  Projects: list with publish state, move up/down, edit form (all fields, stack as chips,
  case study, image upload with preview, alt text, progress), delete with confirmation.
  Logout. Session check via `GET /api/v1/auth/me`; on 401 try `POST /api/v1/auth/refresh` once,
  then show login.
- Public pages read content through one `ContentProvider` (`GET /api/v1/content`), falling back to
  the bundled snapshot (`src/data/*`), so the site always renders.
