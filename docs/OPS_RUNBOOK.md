# Operations runbook

One-off and routine jobs for the production setup that only the owner can do: in the AWS,
Cloudflare, Google and Neon consoles, and on the EC2 VM. Work through the sections in order;
each one ends with **Verify** and **Roll back**.

Production at a glance:

| Piece | Where |
|---|---|
| API | EC2 VM in `ap-south-1` (Mumbai), `~/portfolio-be`, Docker Compose, container `portfolio-server`, host port 80 |
| In front of the API | Cloudflare (proxied DNS record `api`), SSL mode currently **Flexible** |
| Site | Cloudflare Workers (`portfolio`), deploys itself on merge to `main` |
| Database | Neon project "Portfolio backend V2", AWS `ap-southeast-1` (Singapore), Postgres 18 |
| Files | S3 bucket `aashish-upload-2026`, keys under `portfolio/` (`S3_UPLOAD_PREFIX`) |

## Ground rules

- Secrets never go on the command line, into chat, or into a file in the repo. On the VM, read
  them with a hidden prompt and write them to `.env` with this helper (define it once per SSH
  session, from `~/portfolio-be`):

  ```bash
  cd ~/portfolio-be
  set_env() { [ -z "$(tail -c1 .env)" ] || echo >> .env; sed -i "/^$1=/d" .env; printf '%s=%s\n' "$1" "$2" >> .env; }
  ```

- After changing `.env`, the container must be **recreated** (a plain restart keeps the old
  environment):

  ```bash
  sudo docker compose up -d --force-recreate
  ```

- Before any `.env` change, take a dated backup, and delete old backups once you no longer need
  them (they contain secrets):

  ```bash
  cp .env ".env.backup-$(date +%Y%m%d-%H%M)"
  ```

- Quick health check from the VM, used throughout this document:

  ```bash
  curl -s -o /dev/null -w "health %{http_code}\n" http://localhost/health -H "Host: api.aashish-regmi.com.np"
  ```

---

## 1. Check the second user account

The database copy showed **2** rows in `users`. Only one admin should exist.

1. Neon console → project **Portfolio backend V2** → **SQL Editor** → run:

   ```sql
   select id, email, name, is_superuser, is_active, created_at
   from users
   order by created_at;
   ```

2. If the second row is an account you recognise and want to keep, stop here.
3. If you don't recognise it, delete it **by its exact email**, and never the admin. Nothing
   else in the database references `users`, so a delete doesn't cascade anywhere:

   ```sql
   begin;
   delete from users
   where email = '<the unknown email>'
     and email <> 'regmiaashish660@gmail.com';
   -- must say: DELETE 1
   select email, is_superuser from users;
   commit;   -- or: rollback;  if anything looks wrong
   ```

**Verify:** `select count(*) from users;` returns 1, and you can still sign in at
https://aashish-regmi.com.np/admin.

**Roll back:** before `commit`, run `rollback;`. After a commit there's no undo. If the
deleted account turns out to be needed, recreate it with `scripts/seed.py` (it reads
`ADMIN_NAME`, `ADMIN_EMAIL`, `ADMIN_PASSWORD` from the environment).

---

## 2. Rotate the exposed secrets

These values were pasted into a chat and must be treated as public. The JWT secret is already
rotated, and the database password was replaced by the move to the new Neon project
(finish that in 2d).

### 2a. Gmail app password (`SMTP_PASSWORD`)

1. Go to https://myaccount.google.com/apppasswords (Google Account → Security →
   2-Step Verification → App passwords) and **create** a new one, for example "portfolio api 2026-10".
   Leave the page open.
2. On the VM:

   ```bash
   cd ~/portfolio-be && cp .env ".env.backup-$(date +%Y%m%d-%H%M)"
   set_env() { [ -z "$(tail -c1 .env)" ] || echo >> .env; sed -i "/^$1=/d" .env; printf '%s=%s\n' "$1" "$2" >> .env; }
   read -rsp "New Gmail app password: " P; echo
   P=${P// /}                      # Google shows it in groups of 4; spaces aren't part of it
   set_env SMTP_PASSWORD "$P"; unset P
   sudo docker compose up -d --force-recreate
   ```

3. Send yourself a message from https://aashish-regmi.com.np/contact.
4. Once it arrives, go back to the App passwords page and **delete the old** app password
   (anything except the one you just created).

**Verify:** the test email arrives, and `/admin` → Messages shows the test message with status
**Sent**.

**Roll back:** the new password is the only valid one once the old is revoked. If the email
fails, check `sudo docker compose logs --tail=50 api` and repeat step 2 with a freshly created
app password.

### 2b. AWS access key for S3

The API only needs to put, get and delete objects under `portfolio/` in one bucket. You can
either give it a new least-privilege key (option A, quick), or no key at all through an
instance role (option B, recommended long term). In both cases, retire the old key at the end.

First find the ID (not the secret) of the old key, so you can find it in IAM later:

```bash
cd ~/portfolio-be && grep '^AWS_ACCESS_KEY_ID=' .env | cut -d= -f2 | cut -c1-20
```

**The policy** (used by both options). IAM → Policies → Create policy → JSON → paste, name it
`portfolio-api-s3`:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "PortfolioObjects",
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"],
      "Resource": "arn:aws:s3:::aashish-upload-2026/portfolio/*"
    }
  ]
}
```

`s3:GetObject` also covers the `HEAD` requests the media route makes and the résumé download
links. The API never lists the bucket, so no `s3:ListBucket`.

#### Option A: a new access key

1. IAM → Users → **Create user** `portfolio-api` (no console access) → attach the policy
   `portfolio-api-s3` directly.
2. Open the user → **Security credentials** → **Create access key** → use case "Application
   running outside AWS" → keep the page open (the secret is shown once).
3. On the VM:

   ```bash
   cd ~/portfolio-be && cp .env ".env.backup-$(date +%Y%m%d-%H%M)"
   set_env() { [ -z "$(tail -c1 .env)" ] || echo >> .env; sed -i "/^$1=/d" .env; printf '%s=%s\n' "$1" "$2" >> .env; }
   read -rsp "New AWS access key ID: " K; echo
   read -rsp "New AWS secret access key: " S; echo
   set_env AWS_ACCESS_KEY_ID "$K"; set_env AWS_SECRET_ACCESS_KEY "$S"; unset K S
   sudo docker compose up -d --force-recreate
   ```

   Leave `AWS_REGION`, `S3_BUCKET_NAME`, `S3_UPLOAD_PREFIX` and `S3_PUBLIC_BASE_URL` as they are.

#### Option B: an instance role (no keys on the server)

The API leaves the key settings empty when they aren't set, so boto3 falls back to the EC2
instance role.

1. IAM → Roles → **Create role** → trusted entity "AWS service" → **EC2** → attach
   `portfolio-api-s3` → name it `portfolio-api-ec2`.
2. EC2 → Instances → your instance → Actions → Security → **Modify IAM role** →
   `portfolio-api-ec2`.
3. Let the container reach the instance metadata service (Docker adds one network hop). From
   CloudShell or your laptop:

   ```bash
   aws ec2 modify-instance-metadata-options --region ap-south-1 \
     --instance-id <instance-id> --http-tokens required --http-put-response-hop-limit 2 --http-endpoint enabled
   ```

   `--http-tokens required` turns on IMDSv2 only, for every process on the VM. The API's boto3
   uses IMDSv2 already; nothing else on this VM should need the metadata service. If something
   breaks right after this, run the same command with `--http-tokens optional` to undo it.

4. On the VM, remove the keys and recreate:

   ```bash
   cd ~/portfolio-be && cp .env ".env.backup-$(date +%Y%m%d-%H%M)"
   sed -i '/^AWS_ACCESS_KEY_ID=/d; /^AWS_SECRET_ACCESS_KEY=/d' .env
   sudo docker compose up -d --force-recreate
   ```

#### Then, for either option

1. Test: `/admin` → Projects → upload an image → **Save** → open the project on the site and
   check the image loads. `/resume` should still show the résumé. The policy only covers keys
   under `portfolio/`: before changing anything, check that the résumé link in `/admin` →
   Résumé (and the project image links) contain `/media/portfolio/`. If one doesn't (an old
   upload under another prefix), widen the policy's `Resource` to cover it, or re-upload that
   file first.
2. Retire the old key: IAM → Users → find the user that owns the key ID you noted →
   Security credentials → **Deactivate** the old key.
3. After a day with no problems, **Delete** it. If the old key belonged to a user with broader
   permissions (or to the root account), review that user too.

**Verify:** the upload works and the image is visible. IAM shows the old key as inactive or
deleted, and the new key's (or role's) "Last used" updates after the upload.

**Roll back:** while the old key is only *deactivated*, reactivate it in IAM, restore the
`.env` backup (`cp .env.backup-<stamp> .env`) and recreate the container.

### 2c. Gemini API key

Pitch Me is off (`PITCH_ENABLED=false`), so nothing uses the key right now.

1. Google AI Studio → **API keys** → delete the old key.
2. On the VM, remove it so a dead key isn't lying around:

   ```bash
   cd ~/portfolio-be && sed -i '/^GEMINI_API_KEY=/d' .env && sudo docker compose up -d --force-recreate
   ```

3. Create a new key only when you switch Pitch Me on, following `docs/PITCH_GO_LIVE.md`.

**Verify:** `/health` is 200 and the site works (the key is only needed when Pitch Me is on).

**Roll back:** not needed. A deleted key can't be restored; create a new one if required.

### 2d. Old Neon project and old backups (a few days after the move)

1. Confirm the site has run on the Singapore database for a few days without problems.
2. Neon console → the **old us-east-1 project** → Settings → **Delete project**. This makes the
   old database password useless.
3. On the VM, delete every backup that still holds old secrets:

   ```bash
   cd ~/portfolio-be && ls -1 .env.backup-* && rm -f .env.backup-*
   ```

**Verify:** `ls .env*` shows only `.env`, and `/admin` still loads your content.

**Roll back:** none. That's why this step waits a few days.

---

## 3. Allow port 80 only from Cloudflare

At the moment anyone on the internet can reach the API directly on the VM's IP, bypassing
Cloudflare. Restricting the security group to Cloudflare's ranges closes that.

Cloudflare's published ranges (from https://www.cloudflare.com/ips-v4 and
https://www.cloudflare.com/ips-v6, fetched 2026-10-14; check them again before applying):

```text
173.245.48.0/20
103.21.244.0/22
103.22.200.0/22
103.31.4.0/22
141.101.64.0/18
108.162.192.0/18
190.93.240.0/20
188.114.96.0/20
197.234.240.0/22
198.41.128.0/17
162.158.0.0/15
104.16.0.0/13
104.24.0.0/14
172.64.0.0/13
131.0.72.0/22
2400:cb00::/32
2606:4700::/32
2803:f800::/32
2405:b500::/32
2405:8100::/32
2a06:98c0::/29
2c0f:f248::/32
```

These are the same ranges as `TRUSTED_PROXY_IPS` in the VM's `.env`. If Cloudflare ever adds
a range, update both.

### Don't lock yourself out of SSH

Only port **80** changes here; leave port **22** alone in this step. Check what SSH currently
allows: EC2 → Instances → your instance → **Security** tab → inbound rules. Ideally port 22 is
open only to your own IP (`<your IP>/32`; see https://checkip.amazonaws.com). If you want to
tighten 22 too, *add* the new rule first and test a new SSH session before removing the old
rule. If you're ever locked out:
- **EC2 Instance Connect** needs port 22 open to AWS's Instance Connect range for
  `ap-south-1`:

  ```bash
  curl -s https://ip-ranges.amazonaws.com/ip-ranges.json | jq -r '.prefixes[] | select(.service=="EC2_INSTANCE_CONNECT" and .region=="ap-south-1") | .ip_prefix'
  ```

- **Session Manager** needs no open ports at all, but requires the SSM agent and an instance
  role with `AmazonSSMManagedInstanceCore`.

### Option A: AWS console

1. EC2 → Instances → your instance → **Security** tab → click the security group.
2. **Edit inbound rules**:
   1. **Add** one rule per Cloudflare range above: type HTTP, port 80, source = the range,
      description `cloudflare`.
   2. **Save** first, then edit again and **delete** the HTTP rules whose source is `0.0.0.0/0`
      or `::/0`. Adding before removing means no downtime.

### Option B: AWS CLI (CloudShell in ap-south-1, or your laptop)

```bash
REGION=ap-south-1
# Find the security group of the instance (by its public IP; change if you moved to an Elastic IP)
aws ec2 describe-instances --region $REGION \
  --filters Name=ip-address,Values=<public IP> \
  --query 'Reservations[].Instances[].{id:InstanceId,sgs:SecurityGroups}' --output json
SG=<sg-id from the output>

# See the current rules first
aws ec2 describe-security-group-rules --region $REGION \
  --filters Name=group-id,Values=$SG --query 'SecurityGroupRules[?!IsEgress].[FromPort,ToPort,CidrIpv4,CidrIpv6,Description]' --output table

# 1. Fetch Cloudflare's ranges. -f makes curl fail on an error page instead of returning HTML;
#    the counts must be 15 and 7 (the lists in this section). Stop here if they aren't.
V4=$(curl -fsS https://www.cloudflare.com/ips-v4) && V6=$(curl -fsS https://www.cloudflare.com/ips-v6)
echo "$V4" | grep -c / ; echo "$V6" | grep -c /

# 2. Allow Cloudflare (add before removing, so there is no downtime)
for c in $V4; do
  aws ec2 authorize-security-group-ingress --region $REGION --group-id $SG \
    --ip-permissions "IpProtocol=tcp,FromPort=80,ToPort=80,IpRanges=[{CidrIp=$c,Description=cloudflare}]"
done
for c in $V6; do
  aws ec2 authorize-security-group-ingress --region $REGION --group-id $SG \
    --ip-permissions "IpProtocol=tcp,FromPort=80,ToPort=80,Ipv6Ranges=[{CidrIpv6=$c,Description=cloudflare}]"
done

# 3. Confirm all 22 Cloudflare rules exist BEFORE removing the open rule. Must print 22.
aws ec2 describe-security-group-rules --region $REGION --filters Name=group-id,Values=$SG \
  --query 'length(SecurityGroupRules[?!IsEgress && FromPort==`80` && Description==`cloudflare`])'

# 4. Only if step 3 printed 22: remove "open to the world" on port 80
#    (an error just means that rule didn't exist)
aws ec2 revoke-security-group-ingress --region $REGION --group-id $SG \
  --ip-permissions 'IpProtocol=tcp,FromPort=80,ToPort=80,IpRanges=[{CidrIp=0.0.0.0/0}]'
aws ec2 revoke-security-group-ingress --region $REGION --group-id $SG \
  --ip-permissions 'IpProtocol=tcp,FromPort=80,ToPort=80,Ipv6Ranges=[{CidrIpv6=::/0}]'
```

If step 1 printed other counts, Cloudflare changed its list: use the console steps above with
the lists from <https://www.cloudflare.com/ips/>, and check step 3's expected total
(IPv4 count + IPv6 count) before step 4.

**Verify:**
- https://aashish-regmi.com.np loads and the status bar shows the API online, and
  `curl -s https://api.aashish-regmi.com.np/health` from your laptop returns `{"status":"ok"}`.
- A direct request now times out:
  `curl -m 10 http://<public IP>/health` from your laptop prints a timeout, not `ok`.
- After a day, `sudo docker compose logs api | grep -c " 404 "` should show far fewer scanner
  hits.

**Roll back:** add back an HTTP rule for `0.0.0.0/0` (console), or:

```bash
aws ec2 authorize-security-group-ingress --region ap-south-1 --group-id $SG \
  --ip-permissions 'IpProtocol=tcp,FromPort=80,ToPort=80,IpRanges=[{CidrIp=0.0.0.0/0}]'
```

---

## 4. Give the VM an Elastic IP

The VM's public IP changes if the instance is stopped and started. An Elastic IP stays the same,
so Cloudflare's DNS record never goes stale.

About cost: since February 2024 AWS charges for **every** public IPv4 address
(about USD 0.005/hour, roughly USD 3.60/month), auto-assigned or Elastic, attached or not. The
AWS free tier covers 750 hours a month of public IPv4 for the first 12 months. Swapping the
auto-assigned address for an Elastic IP doesn't add cost (the old one is released), but an
Elastic IP left **unattached** keeps costing money, so release any you stop using.

1. EC2 → **Elastic IPs** (region ap-south-1) → **Allocate Elastic IP address** → Allocate.
2. Select it → Actions → **Associate Elastic IP address** → your instance → Associate.
   The instance's public IP changes **immediately**, so do step 3 right away. Expect a minute or
   two of Cloudflare 521 errors for the API.
3. Cloudflare → `aashish-regmi.com.np` → **DNS** → edit the `A` record **`api`** → IPv4 address
   = the new Elastic IP → keep **Proxied** (orange cloud) → Save.
4. Update your SSH command or config to the new IP.

**Verify:**
- `curl -s https://api.aashish-regmi.com.np/health` returns `{"status":"ok"}`.
- The site shows the API online.
- EC2 → Instances shows the Elastic IP as the public IPv4 address.

**Roll back:** disassociate and **release** the Elastic IP. The instance gets a new
auto-assigned IP, and you must update the `api` record again. Releasing means you can't get
the same address back.

---

## 5. Reboot the VM ("System restart required")

The container has `restart: always`, so Docker starts it again after the reboot.

```bash
cd ~/portfolio-be
systemctl is-enabled docker        # must print: enabled
sudo docker compose ps             # note the state before
sudo reboot
```

Wait a minute, SSH in again, then:

```bash
cd ~/portfolio-be
sudo docker compose ps             # portfolio-server should be "Up" (and "healthy" after Phase 7)
curl -s -o /dev/null -w "health %{http_code}\n" http://localhost/health -H "Host: api.aashish-regmi.com.np"
ls /var/run/reboot-required 2>/dev/null || echo "no restart pending"
```

**Verify:** `health 200`, https://aashish-regmi.com.np shows the API online, and no restart is
pending.

**Roll back:** if the container didn't come back, run `sudo docker compose up -d`. If Docker
isn't running, run `sudo systemctl enable --now docker` and then `sudo docker compose up -d`.

---

## 6. Cloudflare SSL: from Flexible to Full (strict)

With **Flexible**, visitors talk HTTPS to Cloudflare, but Cloudflare talks plain HTTP to the VM
on port 80, so the last hop is unencrypted. **Full (strict)** encrypts that hop and checks the
origin's certificate. That needs the VM to serve HTTPS itself, on port 443, with a certificate
Cloudflare trusts.

> **Don't switch the SSL mode in the dashboard yet.** Switching before the VM serves HTTPS breaks
> the API immediately: Cloudflare returns **525** (SSL handshake failed) or **526** (invalid
> certificate). The server-side part (TLS on the origin, port 443, and security group rules for
> Cloudflare's ranges on 443) will come as a separate code change with its own steps.

What you can prepare now:

1. **Origin certificate:** Cloudflare → SSL/TLS → **Origin Server** → **Create Certificate**
   → "Generate private key and CSR with Cloudflare", RSA 2048, hostnames
   `api.aashish-regmi.com.np`, validity **15 years** → Create.
2. Store the certificate and key on the VM, readable only by root. The key is shown once:

   ```bash
   sudo install -d -m 700 /etc/ssl/cloudflare
   sudo nano /etc/ssl/cloudflare/api-origin.pem      # paste the certificate, save
   sudo nano /etc/ssl/cloudflare/api-origin.key      # paste the private key, save
   sudo chmod 600 /etc/ssl/cloudflare/api-origin.*
   sudo openssl x509 -in /etc/ssl/cloudflare/api-origin.pem -noout -subject -enddate
   ```

   The certificate is only trusted by Cloudflare, not by browsers. That's fine, because only
   Cloudflare connects to the origin.

Things that are safe to turn on now (Cloudflare → SSL/TLS → **Edge Certificates**):

- **Always Use HTTPS:** on. HTTP visitors are redirected to HTTPS.
- **Minimum TLS Version:** TLS 1.2.
- **HSTS:** optional. Phase 7 makes both the site and the API send
  `Strict-Transport-Security: max-age=31536000` themselves (one year, no `includeSubDomains`,
  no `preload`). That is consistent with this advice and harmless: both hosts are always HTTPS at
  Cloudflare, and the header covers only that exact host. If you also enable it here, responses
  may carry two HSTS headers; browsers apply only the first, which is harmless. Leave
  **includeSubDomains** and **Preload** **off**. Those two
  commit every subdomain to HTTPS for a long time and are hard to undo, so only turn them on
  when you're sure every subdomain will always have HTTPS.

**Verify:** `curl -sI http://aashish-regmi.com.np | head -3` shows a 301 to `https://`, and the
site and API still work.

**Roll back:** turn off Always Use HTTPS or HSTS in the same screen. Browsers that already saw
HSTS keep enforcing it until its `max-age` expires, which is why to start short.

---

## 7. Turn on Turnstile (bot protection on the contact form)

The API only checks Turnstile tokens when `TURNSTILE_SECRET` is set. Set up the **site first**,
so visitors are already sending tokens when the API starts requiring them. Pitch Me uses the
same widget.

1. Cloudflare → **Turnstile** → **Add widget**: name `portfolio`, hostnames
   `aashish-regmi.com.np` and `www.aashish-regmi.com.np` (add `localhost` only if you want to
   test locally), mode **Managed** → Create. You get a **site key** (public) and a **secret
   key**.
2. **Site:** Workers & Pages → `portfolio` → Settings → **Build** → Variables and secrets
   (build variables) → add `VITE_TURNSTILE_SITE_KEY` = the site key → then **redeploy**
   (Deployments → latest → Retry/Redeploy, or merge any change to `main`).
3. Check the widget appears under the form at https://aashish-regmi.com.np/contact.
4. **API:** on the VM:

   ```bash
   cd ~/portfolio-be && cp .env ".env.backup-$(date +%Y%m%d-%H%M)"
   set_env() { [ -z "$(tail -c1 .env)" ] || echo >> .env; sed -i "/^$1=/d" .env; printf '%s=%s\n' "$1" "$2" >> .env; }
   read -rsp "Turnstile secret key: " T; echo
   set_env TURNSTILE_SECRET "$T"; unset T
   sudo docker compose up -d --force-recreate
   ```

5. Optional: with Turnstile on, the API can email visitors a short "message received" note.
   Turn it on with `set_env CONTACT_CONFIRMATION_ENABLED true` and recreate. It's off by default.

**Verify:**
- Send a message from the contact page. It succeeds, appears in `/admin` → **Messages**, and
  the notification email arrives.
- A request without a token is refused:
  `curl -s -X POST https://api.aashish-regmi.com.np/api/v1/contact -H 'Content-Type: application/json' -d '{"name":"Test","email":"test@example.com","message":"no token test"}'`
  should return an error, not 202.

**Roll back:** remove the secret and recreate:
`sed -i '/^TURNSTILE_SECRET=/d' .env && sudo docker compose up -d --force-recreate`. The
widget on the site is harmless while the API doesn't check it. To hide it too, delete the build
variable and redeploy.

---

## 8. Cloudflare dashboard hygiene

All in the `aashish-regmi.com.np` zone.

- **Security → Bots → Bot Fight Mode: on.** It challenges known bad bots across the whole zone,
  and on the free plan it can't be bypassed per path. After turning it on, check that your
  uptime monitor (`docs/MONITORING.md`) still passes and the site's API calls work. If the
  monitor starts failing, turn Bot Fight Mode back off. The API's own rate limits still apply.
- **Security → Settings → Security level: Medium** (the default). Use "I'm Under Attack" only
  during an actual attack; it puts a challenge in front of every visitor, including API calls.
- **Security → WAF → Rate limiting rules:** the free plan allows **one** rule. Use it on the
  two endpoints that accept unauthenticated POSTs and do real work, login and contact:
  - Name: `login and contact`
  - Expression (Edit expression):

    ```text
    (http.request.method eq "POST" and http.request.uri.path in {"/api/v1/auth/login" "/api/v1/contact"})
    ```

  - Characteristics: IP. Rate: **10 requests per 10 seconds**. Action: **Block** for 10
    seconds. On the free plan the period and duration are fixed at 10 seconds; if the editor
    refuses a field, match on the path alone.
  - Why these numbers: the API already limits each IP to 5 per minute on both endpoints. This
    rule stops floods at Cloudflare before they reach the VM, without ever affecting a real
    visitor.
- **Caching:** leave the API un-cached at Cloudflare (it sends its own `Cache-Control`;
  `/api/v1/content` uses ETag plus a short max-age).

**Verify:** Security → Events shows the rule and Bot Fight Mode working, and the site, admin
sign-in and contact form still work.

**Roll back:** each setting can be switched off on the same page.

---

## 9. Monthly checks (about 15 minutes)

- [ ] VM: `sudo apt update && apt list --upgradable`. Apply updates, and reboot (section 5) if
      `/var/run/reboot-required` exists.
- [ ] VM disk: `df -h /` (keep under 80%), and `sudo docker system df`. To clean old images:
      `sudo docker image prune -f`.
- [ ] `sudo docker compose ps` shows `portfolio-server` up (and healthy).
      `sudo docker compose logs --since 720h api | grep -c ERROR` is low; look at anything new.
- [ ] `/admin` → **Messages**: no unread messages left behind, and no **Email failed** ones
      (retry them, or check `SMTP_PASSWORD`).
- [ ] Neon: usage on the free plan (storage, compute hours), and that the project is still
      in Singapore.
- [ ] AWS: Billing → Bills (public IPv4, EC2, S3), and IAM → access key "last used" (an unused
      or unexpected key gets removed).
- [ ] Cloudflare → Security → Events: anything unusual against `/api/v1/auth/login`.
- [ ] Cloudflare IP ranges unchanged (compare https://www.cloudflare.com/ips-v4 and `-v6`
      with the security group and `TRUSTED_PROXY_IPS`).
- [ ] GitHub: merge or close Dependabot PRs once CI is green, in both repos.
- [ ] If Pitch Me is on: `/admin` → Pitch history (costs, failures), plus the Gemini and
      Tavily dashboards (see `docs/PITCH_GO_LIVE.md`).
- [ ] Delete leftover `.env.backup-*` files you no longer need.
