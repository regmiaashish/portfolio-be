# Origin TLS: Cloudflare SSL from Flexible to Full (strict) (Phase 8.2)

Today visitors talk HTTPS to Cloudflare, but Cloudflare talks **plain HTTP** to the VM on
port 80 (SSL mode **Flexible**), so the last hop is unencrypted. This guide makes the API
container serve HTTPS itself with a **Cloudflare Origin Certificate**, then tells Cloudflare to
use **Full (strict)** for the API: encrypted all the way, and Cloudflare checks the
certificate.

How it works in the code:

- `scripts/run.sh`: when `TLS_CERT_FILE` and `TLS_KEY_FILE` are both set, Gunicorn serves
  **HTTPS** on the container's port 8001 instead of HTTP. It refuses to start with a clear
  message if only one is set or the files aren't readable by the container user (uid 10001).
- `docker-compose.tls.yaml`: mounts the host directory `/etc/portfolio-tls` read-only at
  `/run/tls`. It's added on top of `docker-compose.yaml` by `COMPOSE_FILE` in `.env`.
- The Docker health check and `scripts/deploy.sh` switch to HTTPS on their own when
  `TLS_CERT_FILE` is set.
- `scripts/deploy.sh` checks TLS **before it changes anything**: the compose configuration must
  mount `/run/tls`, and the new container must be able to load the certificate and key as uid
  10001 (an unreadable key, or a key that doesn't match the certificate, stops the deploy while
  the running container keeps serving).

Do the steps in order and stop at the first check that doesn't match. Before you start:

- Phase 8.1 is deployed (the VM runs images from GHCR, see section 15 of
  `docs/EC2_DEPLOYMENT_CHECKLIST.md`), and the **latest `main` is already deployed** with a
  normal `scripts/deploy.sh`. The cutover in step 6 uses `scripts/deploy.sh --no-pull`, so it
  changes only the TLS settings, not the code.
- You have the newline-safe helper in your shell:

```bash
cd ~/portfolio-be
set_env() { [ -z "$(tail -c1 .env)" ] || echo >> .env; sed -i "/^$1=/d" .env; printf '%s=%s\n' "$1" "$2" >> .env; }
```

---

## 1. Create the origin certificate (Cloudflare dashboard)

Cloudflare → `aashish-regmi.com.np` → **SSL/TLS** → **Origin Server** → **Create Certificate**:

- **Generate private key and CSR with Cloudflare**, key type **RSA (2048)** (or ECDSA).
- Hostnames: `api.aashish-regmi.com.np` and `*.aashish-regmi.com.np`.
- Certificate validity: **15 years**. → **Create**.

Keep the page open: Cloudflare shows the **private key only once**. Leave the format as PEM.

The certificate is trusted only by Cloudflare, not by browsers. That's fine, because only
Cloudflare connects to the origin.

If you already created one under `/etc/ssl/cloudflare/` (the old §6 of `OPS_RUNBOOK.md`), you
can reuse it: copy it in step 2 instead of pasting.

## 2. Store it on the VM (outside the repository)

The key must never be typed into a command line (it would land in your shell history) or
committed. `sudo tee` reads what you paste from standard input, which isn't recorded in history,
and `stty -echo` keeps the key off the screen while you paste:

```bash
sudo install -d -m 750 -o root -g 10001 /etc/portfolio-tls

# Certificate: run, paste the whole "-----BEGIN CERTIFICATE-----" block, press Enter, then Ctrl+D
sudo tee /etc/portfolio-tls/origin.pem > /dev/null

# Private key: same, but hidden while you paste. Paste the whole "-----BEGIN PRIVATE KEY-----"
# block, press Enter, then Ctrl+D. (If you press Ctrl+C instead, the terminal stays silent:
# type `stty echo` and press Enter to get your typing back, then repeat this line.)
stty -echo; sudo tee /etc/portfolio-tls/origin.key > /dev/null; stty echo; echo

# Readable by the container user (uid/gid 10001), nobody else; the key is never world-readable
sudo chown root:10001 /etc/portfolio-tls/origin.pem /etc/portfolio-tls/origin.key
sudo chmod 644 /etc/portfolio-tls/origin.pem
sudo chmod 640 /etc/portfolio-tls/origin.key
```

**Check:**

```bash
sudo ls -ln /etc/portfolio-tls
#   -rw-r----- 1 0 10001 ... origin.key
#   -rw-r--r-- 1 0 10001 ... origin.pem
sudo openssl x509 -in /etc/portfolio-tls/origin.pem -noout -subject -enddate
# The key belongs to the certificate: the two hashes must be identical
sudo openssl x509 -in /etc/portfolio-tls/origin.pem -noout -pubkey | sha256sum
sudo openssl pkey -in /etc/portfolio-tls/origin.key -pubout | sha256sum
```

**Roll back:** `sudo rm -r /etc/portfolio-tls` (nothing uses it yet).

## 3. Allow port 443 from Cloudflare (AWS security group)

Same pattern as port 80 in `OPS_RUNBOOK.md` section 3, with port **443**. Keep port 80 open
for now; it's still what Cloudflare uses until step 6.

Console: EC2 → Instances → your instance → **Security** tab → the security group → **Edit
inbound rules** → **Add** one rule per Cloudflare range (type **HTTPS**, port 443, description
`cloudflare`) → **Save**.

CLI (CloudShell in `ap-south-1`, or your laptop), with `SG` set as in the runbook:

```bash
REGION=ap-south-1
V4=$(curl -fsS https://www.cloudflare.com/ips-v4) && V6=$(curl -fsS https://www.cloudflare.com/ips-v6)
echo "$V4" | grep -c / ; echo "$V6" | grep -c /     # must print 15 and 7; stop if not
for c in $V4; do
  aws ec2 authorize-security-group-ingress --region $REGION --group-id $SG \
    --ip-permissions "IpProtocol=tcp,FromPort=443,ToPort=443,IpRanges=[{CidrIp=$c,Description=cloudflare}]"
done
for c in $V6; do
  aws ec2 authorize-security-group-ingress --region $REGION --group-id $SG \
    --ip-permissions "IpProtocol=tcp,FromPort=443,ToPort=443,Ipv6Ranges=[{CidrIpv6=$c,Description=cloudflare}]"
done
# Must print 22
aws ec2 describe-security-group-rules --region $REGION --filters Name=group-id,Values=$SG \
  --query 'length(SecurityGroupRules[?!IsEgress && FromPort==`443` && Description==`cloudflare`])'
```

**Roll back:** delete the 443 rules in the console (nothing listens on 443 yet).

## 4. Rehearse: can the container read the certificate?

This runs a throwaway container with the mount; the live API is untouched:

```bash
sudo docker compose run --rm -v /etc/portfolio-tls:/run/tls:ro --entrypoint sh api \
  -c 'id; test -r /run/tls/origin.pem && test -r /run/tls/origin.key && echo "TLS files readable"'
```

It must print `uid=10001(app) ...` and `TLS files readable`. If not, redo the `chown`/`chmod`
in step 2.

## 5. Prepare the Cloudflare rule (don't deploy it yet)

Cloudflare → **Rules** → **Overview** → **Create rule** → **Configuration Rule**:

- **Rule name:** `api full strict`
- **If incoming requests match:** Custom filter expression, then **Field** `Hostname`,
  **Operator** `equals`, **Value** `api.aashish-regmi.com.np`, only the hostname.
  The **Expression Preview** must read exactly:
  `(http.host eq "api.aashish-regmi.com.np")`. Don't paste the whole expression into the
  **Value** box: that was the mistake with the documind rule, where the preview showed
  `wildcard r#"(http.host eq ...`.
- **Then the settings are:** **SSL** → **+ Add** → **Strict** (Full (strict)).

Leave this tab open **without clicking Deploy**. Step 6 switches the VM and this rule within
a minute of each other.

## 6. Cut over (pick a quiet moment; the API is unreachable for about a minute)

Between switching the VM and deploying the rule, Cloudflare still knocks on port 80, where
nothing listens any more, so the API returns 521 for that minute. The website itself stays
up (it falls back to its cached content).

On the VM (the backup goes **outside** the repository):

```bash
cd ~/portfolio-be
cp .env ~/env.backup-before-tls
set_env COMPOSE_FILE docker-compose.yaml:docker-compose.tls.yaml
set_env TLS_CERT_FILE /run/tls/origin.pem
set_env TLS_KEY_FILE /run/tls/origin.key
set_env API_HOST_PORT 443
sudo docker compose config | grep -E 'published|/run/tls'   # published: "443", target: /run/tls
scripts/deploy.sh --no-pull
```

`deploy.sh --no-pull` redeploys the image already running with the new settings. First it checks
the TLS mount and loads the certificate and key in a throwaway container (`TLS certificate and
key load fine`); if that fails it stops **before** touching the running container, which keeps
serving on port 80: fix the files (step 2) or roll back below. Then it recreates the container,
waits for `healthy`, and smoke tests `https://127.0.0.1:443` (`Smoke test (https on host port
443)`). If the new container fails, it recreates the previous image, but the TLS settings are
still in `.env`: use the rollback below.

As soon as it prints `==> Deployed`, click **Deploy** on the Cloudflare rule from step 5.

**Check** from your laptop:

```bash
curl -s https://api.aashish-regmi.com.np/health          # {"status":"ok"}
curl -s https://api.aashish-regmi.com.np/health/ready    # {"status":"ok"}
```

Then open https://aashish-regmi.com.np and `/admin`.

- **525** (SSL handshake failed): the container isn't serving TLS on 443. Check
  `sudo docker compose ps` and `sudo docker compose logs --tail=30 api` (a `TLS:` line names
  the problem).
- **526** (invalid certificate): the certificate isn't a Cloudflare Origin Certificate for
  `api.aashish-regmi.com.np`, or the key and certificate don't match (step 2 check).
- **521/522**: the security group doesn't allow 443 from Cloudflare (step 3).

**Roll back** (back to HTTP on port 80, about a minute): restore the backup and redeploy it,
checked and health-checked like any deploy:

```bash
cd ~/portfolio-be && cp ~/env.backup-before-tls .env && scripts/deploy.sh --no-pull
```

…and in Cloudflare, disable or delete the `api full strict` rule (if you deployed it).

## 7. A few days later: Full (strict) for the whole zone, close port 80

Once the API has run on Full (strict) for a few days:

1. **Check every other proxied hostname supports TLS.** Cloudflare → **DNS** → records with
   the orange cloud. The website (`aashish-regmi.com.np`, `www`) runs on Cloudflare Workers and
   isn't affected by this setting. `documind.aashish-regmi.com.np` already has its own rule
   (SSL **Full**). To test it under strict, edit that rule's SSL to **Strict**: if documind
   still loads, its host has a valid certificate; if it shows **526**, set the rule back to
   **Full** and keep it.
2. **Zone default:** SSL/TLS → **Overview** → **Full (strict)**.
3. **Remove rules that now duplicate the default:** the `api full strict` rule (and
   documind's, if it passed the strict test in step 1).
4. **Make Cloudflare redirect plain HTTP first.** Under Full (strict), Cloudflare connects to
   the VM with the visitor's scheme, so `http://api…` requests would still go to port 80.
   SSL/TLS → **Edge Certificates** → **Always Use HTTPS**: On. Check from your laptop:

   ```bash
   curl -sI http://api.aashish-regmi.com.np/health | head -n 3   # must be a 301 to https://
   ```

5. **Close port 80 on the VM:** in the security group, delete the port 80 rules (console), or
   revoke them with the CLI loop from `OPS_RUNBOOK.md` section 3 using
   `revoke-security-group-ingress` and port 80. The API now listens only on 443, so nothing
   needs to change on the VM.

**Check:** the site, the API and documind all load; `curl -m 10 http://<public IP>/health` from
your laptop times out.

**Roll back:** zone SSL back to **Flexible** only if you've also rolled the VM back to port 80
(step 6 rollback); otherwise re-add a per-host rule.

## Optional, later: Authenticated Origin Pulls

With the security group limited to Cloudflare's ranges, only Cloudflare can reach the VM. For a
second layer, Cloudflare's **Authenticated Origin Pulls** presents a client certificate on every
request, and the origin can refuse anything without it (Gunicorn's `--ca-certs` and
`--cert-reqs 2`). Not needed for a portfolio; noted here so it isn't forgotten.

## Renewal

The origin certificate is valid for 15 years (until around 2041). Calendar reminder:
`sudo openssl x509 -in /etc/portfolio-tls/origin.pem -noout -enddate`. To replace it, repeat
steps 1–2 and run `scripts/deploy.sh --no-pull` (it checks the new pair before switching).
