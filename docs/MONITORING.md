# Monitoring (Phase 7.3, revised in Phase 8)

Two free layers: an **uptime monitor** that tells you when the site or API is down, and optional
**error tracking** (Sentry) that tells you when code fails. Both are set up by you in the
browser; nothing here sends visitor data anywhere.

## 1. Uptime monitors (UptimeRobot, free plan)

Sign up at <https://uptimerobot.com> → *Add New Monitor*, three times:

| Name | Type | URL | Settings |
|---|---|---|---|
| API alive | Keyword | `https://api.aashish-regmi.com.np/health` | Keyword `"status":"ok"` (with the quotes, so a Cloudflare error page can't match), alert when the keyword **does not exist**; interval **5 min**; timeout 30 s |
| Website | HTTP(s) | `https://aashish-regmi.com.np/` | interval 5 min; timeout 30 s |
| API database (optional) | Keyword | `https://api.aashish-regmi.com.np/health/ready` | Keyword `"status":"ok"`, alert when it **does not exist**; interval **60 min** (never shorter); timeout 30 s; *Notify after* 2 failures |

**Why the database check is hourly, not every 5 minutes.** `/health` never touches the
database, so it can be checked as often as you like. `/health/ready` runs `SELECT 1` on Neon.
On Neon's free plan the database compute **suspends after about 5 minutes without queries** and
wakes on the next one; that is what keeps it free. As far as we know (plans change: check
<https://neon.com/pricing>), the free plan includes about **100 CU-hours of compute per project
per month**, and once that is used up the compute stays suspended until the next month. The
smallest compute is 0.25 CU, so running 24/7 costs about 0.25 × 730 ≈ **180+ CU-hours**: far more
than the allowance. A check every 5 minutes would never let the compute sleep, so it would burn
through the month and take the site's content and admin down. An hourly check wakes it for
about 5 minutes each time (roughly 0.25 × 5/60 × 730 ≈ 15 CU-hours a month), which is cheap;
no database check at all is cheaper still.

The first `/health/ready` after a long idle wakes the database, which has taken more than 5 s
in production; the API waits up to `HEALTH_READY_TIMEOUT_SECONDS` (default 10) before answering
503, and *Notify after* 2 failures means one slow wake-up never alerts you.

Keyword monitors send `GET`; plain HTTP(s) monitors may send `HEAD`. Both `/health` and
`/health/ready` answer `HEAD` as well as `GET`, so either method works.

*Alert contacts*: your email (and the UptimeRobot phone app for push notifications if you like).

Better Stack (<https://betterstack.com/uptime>) works the same way if you prefer it.

### What each alert means and what to do first

**API database down, API alive up.** The API runs but can't reach the database.

1. <https://neonstatus.com> and the Neon console: is the project suspended or over a free-plan
   limit?
2. On the VM: `cd ~/portfolio-be && sudo docker compose logs --tail=100 api | grep -i -E "error|readiness"`
3. A wake-up from Neon's scale-to-zero can take several seconds; the readiness timeout is
   `HEALTH_READY_TIMEOUT_SECONDS` (default 10). Repeated failures are a real problem.

**API alive down.** The API process or the VM is down (Cloudflare shows 521/522/523).

1. `cd ~/portfolio-be && sudo docker compose ps`: the STATUS should be `Up … (healthy)`.
2. `sudo docker compose logs --tail=100 api`: a settings error stops the API at start-up and
   names the problem.
3. `sudo docker compose up -d` to start it; if the VM itself doesn't answer, reboot it from the
   EC2 console (the Docker service restarts the container, `restart: always`).
4. <https://www.cloudflarestatus.com> if the VM looks fine.

**Website down.** The frontend (Cloudflare Workers) is affected: check
<https://www.cloudflarestatus.com> and the latest deployment in the Cloudflare dashboard.
Roll back by redeploying the previous deployment.

## 2. Error tracking (Sentry, optional)

Off unless `SENTRY_DSN` is set. When on, every unhandled error and every `logger.error` /
`logger.exception` becomes a Sentry issue with its stack trace. By design **nothing about
visitors is sent**: no request bodies (contact messages, pitch inputs), no cookies, no auth or
forwarding headers, no query strings, no local variable values, and no breadcrumbs from log
lines or outbound HTTP calls (the geolocation and weather URLs contain the visitor's IP and
coordinates). As a last safety net, any IP address or latitude/longitude left in an event is
replaced with `[ip]` / `[redacted]` (`src/core/observability.py`). Sentry never starts in tests.

1. <https://sentry.io> → create an account (the free *Developer* plan is enough) → *Create
   project* → platform **FastAPI** → name `portfolio-api`. Copy the **DSN**.
2. Sentry → *Settings* → *Projects* → `portfolio-api` → *Security & Privacy*: turn on *Data
   Scrubber* and *Use Default Scrubbers* (a second safety net), and *Prevent Storing of IP
   Addresses*.
3. On the VM:

   ```bash
   cd ~/portfolio-be
   set_env() { [ -z "$(tail -c1 .env)" ] || echo >> .env; sed -i "/^$1=/d" .env; printf '%s=%s\n' "$1" "$2" >> .env; }
   read -rsp "Sentry DSN: " v; echo; set_env SENTRY_DSN "$v"; unset v
   sudo docker compose up -d --force-recreate
   sudo docker compose logs --tail=50 api | grep -i sentry   # no output = Sentry started
   ```

   A mistyped DSN doesn't take the API down: it logs `Sentry is off: SENTRY_DSN was rejected`
   (without the value) and keeps serving. Paste the DSN again if you see that line.

4. Sentry → *Alerts*: the default "new issue" email alert is enough to start.

To switch it off again: `set_env SENTRY_DSN ""` and recreate the container.

## 3. Logs on the VM

- `sudo docker compose logs -f api` follows the live log; `--since 1h` limits it.
- Docker keeps at most 3 × 10 MB of logs per container (set in `docker-compose.yaml`), so the
  disk can't fill up.
- Admin actions are logged as `Admin <email> …` lines: `sudo docker compose logs api | grep "Admin "`.
- Visitor messages are never logged; a failed contact email logs only the message id.
