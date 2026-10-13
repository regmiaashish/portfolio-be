## Portfolio backend

Install dependencies and run the API locally:

```bash
make install
make dev
```

The API is available at `http://localhost:8001`.

### Reset the admin account

The seed is idempotent: it creates the admin when the email is missing, or
updates the existing account when the email already exists. In an interactive
terminal, `make seed-admin` securely prompts for missing credentials. For CI or
deployment, set the credentials only in your shell or secret manager; do not
commit them to `.env`:

```bash
export ADMIN_NAME="Portfolio Administrator"
export ADMIN_EMAIL="you@example.com"
export ADMIN_PASSWORD="use-a-new-password-at-least-12-chars"
make seed-admin
```

### Visitor context API

`GET /api/v1/context?ip=<visitor-ip>` returns geolocation, current weather, and
the GBP conversion rate for the visitor's local currency. Geolocation runs
first; weather and currency requests then run concurrently.

For a live test, pass a public IP address rather than `127.0.0.1`:

```bash
curl "http://localhost:8001/api/v1/context?ip=8.8.8.8"
```

If an upstream provider is rate-limited, test the complete application flow
with deterministic local data:

```bash
CONTEXT_MOCK=true make dev
curl "http://localhost:8001/api/v1/context"
```

Keep `CONTEXT_MOCK` unset or set to `false` in production.
