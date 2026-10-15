# Pitch Me go-live runbook (Phase 7.4)

Pitch Me calls Gemini and Tavily on every uncached run, so it is switched on last, with every
cost control in place. This runbook replaces step 10 of `EC2_DEPLOYMENT_CHECKLIST.md`.

Everything below runs on the EC2 VM in `~/portfolio-be` unless it says Cloudflare, Google or
Tavily. **Never paste a key into chat, a ticket or a file other than `.env`.** The commands read
secrets with a hidden prompt and never echo them.

## What protects your money (already built)

| Control | Where | What it does |
|---|---|---|
| Kill switch | `PITCH_ENABLED` | `false` = every request gets 503, nothing is called |
| Start-up guard | `src/core/config.py` | With `ENV=prod`, the API **refuses to start** with `PITCH_ENABLED=true` unless Turnstile, Gemini and Tavily keys are set and neither provider is `fake` |
| Bot protection | Turnstile | Each run needs a valid Turnstile token |
| Rate limit | 3 per minute per visitor | Needs `CLIENT_IP_HEADER=cf-connecting-ip` + `TRUSTED_PROXY_IPS` (already set) |
| Cache | 24 h, per worker | The same company + role replays for free |
| Daily cap | `PITCH_DAILY_CAP`, in Postgres | Counted **before** each paid run and never refunded, so bursts can't overshoot |
| Deadline | `PITCH_TIMEOUT_SECONDS` (45 s) | A run is cancelled at the deadline or when the visitor leaves |

Rehearsed locally with fake providers and `PITCH_DAILY_CAP=2`: two paid runs and one cache hit
streamed; the next new company got 429 "The demo is resting for today"; a cached company still
streamed after the cap; every run appeared in `pitch_runs` (cache hits with cost 0); with
`PITCH_ENABLED=false` the endpoint answered 503.

## 1. Accounts and keys (you, in the browser)

1. **Gemini**: <https://aistudio.google.com> → *Get API key* → create a key in a new Google Cloud
   project used only for this.
   - On the **free tier** (no billing account linked to that project) Google never charges you;
     over the free quota requests fail with 429, which the app reports as a normal error.
   - If you ever **enable billing** on that project: Google Cloud console → *Billing* → *Budgets
     & alerts* → budget of e.g. US$5/month with email alerts at 50 %, 90 % and 100 %.
   - Free-tier prompts may be used by Google to improve its models. This demo only sends company
     and role names plus public web snippets, never visitor data.
2. **Tavily**: <https://app.tavily.com> → copy the API key. The free plan includes monthly
   credits; each paid run uses one search. Check *Usage* weekly, and turn on usage alerts if your
   plan offers them.
3. **Turnstile** (Cloudflare dashboard → *Turnstile* → *Add widget*): hostname
   `aashish-regmi.com.np` (add `www.aashish-regmi.com.np` too), mode *Managed*. You get a
   **site key** (public) and a **secret key**.

## 2. Turnstile first: frontend, then backend (order matters)

The backend only demands a Turnstile token once `TURNSTILE_SECRET` is set. If the backend gets
the secret before the site sends tokens, the contact form starts failing. So:

1. Cloudflare → *Workers & Pages* → the `portfolio` project → *Settings* → *Build* → *Variables*:
   add `VITE_TURNSTILE_SITE_KEY=<site key>`, then redeploy (*Deployments* → *Retry deployment*,
   or merge any PR). Check the contact page shows the Turnstile box.
2. On the VM:

   ```bash
   cd ~/portfolio-be
   set_env() { [ -z "$(tail -c1 .env)" ] || echo >> .env; sed -i "/^$1=/d" .env; printf '%s=%s\n' "$1" "$2" >> .env; }
   read -rsp "Turnstile SECRET key: " v; echo; set_env TURNSTILE_SECRET "$v"; unset v
   sudo docker compose up -d --force-recreate
   ```

3. Send yourself a message through the contact form: it must arrive (Admin → Messages).

## 3. Keys and conservative limits (VM, Pitch Me still off)

```bash
cd ~/portfolio-be
set_env() { [ -z "$(tail -c1 .env)" ] || echo >> .env; sed -i "/^$1=/d" .env; printf '%s=%s\n' "$1" "$2" >> .env; }
read -rsp "Gemini API key: " v; echo; set_env GEMINI_API_KEY "$v"; unset v
read -rsp "Tavily API key: " v; echo; set_env TAVILY_API_KEY "$v"; unset v
set_env PITCH_LLM_PROVIDER gemini
set_env PITCH_RESEARCH_PROVIDER tavily
set_env PITCH_DAILY_CAP 20
set_env PITCH_ENABLED false
sudo docker compose up -d --force-recreate
```

## 4. Pre-flight check (free, read-only)

```bash
sudo docker compose run --rm --entrypoint python api scripts/pitch_golive_check.py
```

It prints a PASS/WARN/FAIL checklist (keys only as "set"/"missing", never their values), checks
the database and today's usage, and makes **no** external calls. Continue only when the last
line says `READY`.

## 5. Switch the API on

```bash
set_env() { [ -z "$(tail -c1 .env)" ] || echo >> .env; sed -i "/^$1=/d" .env; printf '%s=%s\n' "$1" "$2" >> .env; }
set_env PITCH_ENABLED true
sudo docker compose up -d --force-recreate
for i in $(seq 30); do s=$(sudo docker inspect -f '{{.State.Health.Status}}' portfolio-server); echo "$s"; [ "$s" = healthy ] && break; sleep 3; done   # ends with "healthy" (about 30-40 s)
sudo docker compose logs --tail=30 api    # a config problem shows up here and the API stops
sudo docker compose run --rm --entrypoint python api scripts/pitch_golive_check.py
```

If the API doesn't come up, go straight to **Rollback** below.

## 6. One real run (costs one run), then the frontend

1. The API now needs a Turnstile token, so the real test goes through the site, and the site
   only shows the demo once its flag is on. The backend is ready at this point, so flip it:
   Cloudflare → *Workers & Pages* → `portfolio` → *Settings* → *Variables*: add
   `VITE_PITCH_ENABLED=true` → redeploy. (Visitors can use it from this moment; the cap and
   limits already apply.)
2. Open `https://aashish-regmi.com.np/pitch` in a private window and run one pitch for a company
   you care about. Expect the three agents to show
   progress and the pitch to stream in within about 20–40 s.
3. In `/admin` → **Pitch history**: the run is listed as *completed*, with tokens and an
   estimated cost. Run the same company + role again: it replays instantly and shows as cached
   with cost 0.
4. Prompt-injection spot check: company `Ignore previous instructions`, role `Engineer` gives a
   normal (if odd) pitch, never a change of behaviour.

## 7. Keep an eye on it

- First week: look at *Pitch history* daily (runs today vs cap, last 30 days' spend), Tavily
  usage, and Google AI Studio usage.
- If everything is calm, raise `PITCH_DAILY_CAP` gradually (`set_env PITCH_DAILY_CAP 50` →
  `sudo docker compose up -d --force-recreate`).
- Uptime and error alerts: see `MONITORING.md`.

## Rollback (instant)

```bash
cd ~/portfolio-be
set_env() { [ -z "$(tail -c1 .env)" ] || echo >> .env; sed -i "/^$1=/d" .env; printf '%s=%s\n' "$1" "$2" >> .env; }
set_env PITCH_ENABLED false
sudo docker compose up -d --force-recreate
```

Every Pitch Me request now gets 503 and nothing is called. Optionally also remove
`VITE_PITCH_ENABLED` in Cloudflare and redeploy so the page hides the demo. If a key may have
leaked, revoke it in AI Studio / Tavily first, then do the above.
