# Deploying Steward on a Hostinger VPS (single server)

This is the reproducible deployment package for one Ubuntu 24.04 VPS (Hostinger KVM 2 or larger).
**Nothing in this repository deploys automatically.** Every production change is a manual release
(§9) that starts with a backup.

> Status (Phase 5): the full stack — two API containers, two orchestrators, ClamAV — was rehearsed
> again in production mode. Phase 4: the full stack was rehearsed in production mode (`APP_ENV=production`, Caddy TLS, all
> production guards on) on a 4 vCPU / 16 GB test machine with `DOMAIN=localhost`, including backup,
> restore verification and a full destructive restore. **It has not been deployed to a real VPS
> with a real domain, Let's Encrypt certificate or SMTP server.** Those steps are below and must be
> checked by the operator (§12 checklist).

---

## 1. Architecture

```
                         Internet
                            │  80/tcp (redirect only), 443/tcp
                 ┌──────────▼──────────┐   host firewall (ufw): only 22, 80, 443 inbound
                 │ caddy               │   TLS (Let's Encrypt), HSTS, body/time limits,
                 │                     │   /metrics /readyz /livez /healthz /docs → 404
                 └───┬─────────────┬───┘
       pages (edge)  │             │  /api/* (apinet, internal)  round robin + health checks
                 ┌───▼───┐   ┌─────▼─────┐ ┌───────────┐
                 │ web   │   │ api       │ │ api-2     │  identical; restarted one at a time
                 │Next.js│   └─────┬─────┘ └─────┬─────┘  (deploy/rolling-restart.sh)
                 └───────┘         │  scan (internal)  ┌──────────┐      ┌───────────┐
                                   ├──────────────────►│ clamav   │◄─vol─┤ freshclam │──► internet
                                   │                   │ (clamd)  │      │ (updates) │   (egress)
      ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┼ ─ ─ ─ ─ ─ ─ ─ ─   └──────────┘      └───────────┘
                     backend (internal: no internet)
   ┌──────────────┬──────────────┬─┴────────────┬──────────────┐
   │ orchestrator │ tools        │ deletion     │ scheduler    │  workers (own DB roles)
   │ ×2 (+egress) │ (+egress)    │              │              │  egress = model provider /
   └──────┬───────┴──────┬───────┴──────┬───────┴──────┬───────┘  allowlisted sites
          └──────────────┴───────┬──────┴──────────────┘
                       ┌─────────▼─────────┐
                       │ db  PostgreSQL 16 │── db-data volume.  Never published on the host.
                       └───────────────────┘
```

| Service | Image | Runs as | Published | Networks | Persistent data |
|---|---|---|---|---|---|
| caddy | `caddy:2-alpine` | root, **only** `NET_BIND_SERVICE`, read-only FS | 80, 443 | edge, apinet | `caddy-data`, `caddy-config` |
| web | built from `web/` | uid 999 `web` | `127.0.0.1:3000` | edge, backend | — |
| api, api-2 | built from `api/` | uid 999, read-only FS, no capabilities | — | backend, apinet, scan | `storage` |
| migrate | same as api | uid 999 | — | backend | — (one-shot, runs before the APIs) |
| orchestrator (×`ORCHESTRATOR_REPLICAS`), tools | same as api | uid 999, read-only FS | — | backend, egress | — |
| deletion, scheduler | same as api | uid 999, read-only FS | — | backend | `storage` (deletion) |
| clamav (profile `scan`) | `clamav/clamav:1.4` | `clamav` (drops from root; only SETUID/SETGID), read-only FS | — | scan | `clamav-db` |
| freshclam (profile `scan`) | `clamav/clamav:1.4` | `clamav`, read-only FS | — | egress | `clamav-db` |
| db | `postgres:16-alpine` | `postgres` | — | backend | `db-data` |

Who holds which key: `SECRETS_ENCRYPTION_KEY` (MFA seeds) and `INTEGRATION_SECRETS_PUBLIC_KEY` → API
containers only. `INTEGRATION_SECRETS_PRIVATE_KEY` → tools worker only. The migrate job and the other
workers get no encryption keys. The model API key → orchestrator and tools only.

Database roles (created by `deploy/db/init-roles.sh`, privileges by the migrations):
`steward_owner` (migrations only), `steward_app` (API: no DDL, no DELETE), `steward_worker`
(workers: no DELETE, no secrets), `steward_deleter` (deletion service only), `steward_readonly`.

## 2. Prerequisites

- Hostinger VPS, **Ubuntu 24.04**, 2 vCPU / 4 GB RAM minimum (e.g. KVM 2) — **8 GB if you enable malware scanning** (ClamAV needs ~1.5 GB); KVM 4 matches the measured
  test machine (docs/PERFORMANCE.md). 40 GB+ disk. Public IPv4.
- A domain or sub-domain you control (DNS A record).
- An SMTP mailbox that supports **STARTTLS on port 587** (Hostinger Email, Google Workspace,
  Brevo, SES, Mailgun…). Implicit-TLS-only (465) servers are not supported.
- Optional: an AI model endpoint (OpenAI-compatible). Without it tasks are saved and wait.
- A second location for backups (another server, S3-compatible bucket, or your own machine).

## 3. Server preparation

Use the Hostinger browser terminal or SSH **with a key** (never paste private keys or passwords
into chat or tickets).

```bash
ssh root@<VPS_IP>
apt-get update && apt-get -y upgrade
apt-get install -y ca-certificates curl git ufw age unattended-upgrades
dpkg-reconfigure -plow unattended-upgrades      # automatic security updates

# Non-root operator account (membership in the docker group is root-equivalent: keep it to admins)
adduser --disabled-password --gecos "" steward
usermod -aG sudo steward
install -d -m 700 -o steward -g steward /home/steward/.ssh
cp ~/.ssh/authorized_keys /home/steward/.ssh/ && chown steward:steward /home/steward/.ssh/authorized_keys

# SSH: keys only, no root login (test a second session as 'steward' BEFORE closing this one)
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/; s/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
systemctl reload ssh

# Firewall
ufw default deny incoming && ufw default allow outgoing
ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp
ufw --force enable

# Docker Engine + Compose v2 (official convenience script; review it first if you prefer)
curl -fsSL https://get.docker.com -o /tmp/get-docker.sh && sh /tmp/get-docker.sh
usermod -aG docker steward
```

> **Docker and ufw:** ports published by Docker bypass ufw rules. That is why the compose file
> publishes only Caddy's 80/443 and binds the web port to `127.0.0.1`. Do not add other `ports:`
> entries; PostgreSQL, the API and the workers must never be published.

If Hostinger's panel firewall is enabled, allow the same three ports there too.

## 4. DNS

Create an **A record** `agent.example.com → <VPS_IP>` (and `AAAA` if you use IPv6). Wait until
`dig +short agent.example.com` returns the VPS address **before** first start, otherwise Let's
Encrypt validation fails and Caddy retries with back-off.

## 5. Get the code and create secrets

```bash
sudo install -d -o steward -g steward /opt/live-agent /opt/steward-backups
sudo -iu steward
git clone https://github.com/Mostafijemon24/Live-Agent.git /opt/live-agent
cd /opt/live-agent
git checkout <release tag or commit>          # never deploy an unreviewed branch head
cp .env.example .env && chmod 600 .env
```

Generate every secret **on the server** and write it straight into `.env` (nothing is printed):

```bash
gen() { openssl rand -base64 36 | tr -d '/+=' | cut -c1-40; }
for k in POSTGRES_SUPERUSER_PASSWORD STEWARD_OWNER_PASSWORD STEWARD_APP_PASSWORD \
         STEWARD_WORKER_PASSWORD STEWARD_DELETER_PASSWORD STEWARD_READONLY_PASSWORD \
         APPROVAL_SIGNING_KEY METRICS_TOKEN; do
  sed -i "s|^$k=.*|$k=$(gen)|" .env
done
# Encryption keys (MFA seeds; integration credentials as an X25519 key pair). Built image needed:
docker compose build api
docker compose run --rm --no-deps --entrypoint python api -m app.core.crypto keygen > /tmp/keys.env
for k in SECRETS_ENCRYPTION_KEY INTEGRATION_SECRETS_PUBLIC_KEY INTEGRATION_SECRETS_PRIVATE_KEY; do
  v=$(grep "^$k=" /tmp/keys.env | cut -d= -f2-); sed -i "s|^$k=.*|$k=$v|" .env
done
shred -u /tmp/keys.env
grep -c 'replace-me' .env    # must print 0
```

Then edit the non-secret values with `nano .env`:

```ini
APP_ENV=production
APP_ORIGIN=https://agent.example.com
DOMAIN=agent.example.com
TRUST_PROXY_HEADERS=true
MAIL_BACKEND=smtp
MAIL_FROM=Steward <no-reply@example.com>
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USERNAME=no-reply@example.com
SMTP_PASSWORD=<type it in the editor; never in shell history or chat>
MODEL_PROVIDER=none                 # or openai_compatible + MODEL_* (see .env.example)
```

Secret handling rules:

- `.env` stays on the server, mode 600, owned by `steward`. It is in `.gitignore`; never commit it.
- Keep an offline copy of `.env` in your password manager — **backups are useless without the
  database passwords, `APPROVAL_SIGNING_KEY` and the three encryption keys** (without them, MFA
  seeds and WordPress credentials in a restored database can't be read).
- Rotating `SECRETS_ENCRYPTION_KEY`: move the current value to `SECRETS_ENCRYPTION_PREVIOUS_KEY`,
  set a new one, release. Rotating the integration key pair means reconnecting WordPress.
- The model API key is passed only to the orchestrator and tools workers (the API container gets
  an empty value). It never reaches the browser, the model prompt, logs or task output.
- Rotate a database password: change it in `.env`, then
  `docker compose exec db psql -U postgres -c "ALTER ROLE steward_app PASSWORD '<new>'"` and
  `docker compose up -d`. Rotating `APPROVAL_SIGNING_KEY` invalidates pending approvals (they must be re-requested).

The API and workers **refuse to start** in production with placeholders, short signing keys,
the mock model, insecure egress, non-SMTP mail, a non-https origin, rate limiting disabled or
`TRUST_PROXY_HEADERS=false` (the last four apply to the API process only).

## 6. First start

```bash
cd /opt/live-agent
docker compose --profile tls build --pull
docker compose --profile tls up -d                    # add --profile scan for malware scanning (§12)
docker compose ps        # all services "healthy" (caddy has no healthcheck: "Up")
```

Order is enforced by compose: `db` healthy → `migrate` completes → `api` healthy → workers / web.

Smoke test from your own machine:

```bash
curl -sI https://agent.example.com/login | grep -iE 'HTTP/|strict-transport|cache-control'
curl -s -o /dev/null -w '%{http_code}\n' https://agent.example.com/metrics      # 404 (internal only)
curl -s -o /dev/null -w '%{http_code}\n' http://agent.example.com/login        # 308 → https
```

Then register in the browser, receive the verification e-mail (this proves SMTP), sign in, and open
**Settings → Agent** to check that all four workers report as alive.

## 7. Health checks, restarts and logs

- Every service has `restart: unless-stopped`; containers come back after a crash or reboot
  (Docker starts on boot).
- Health checks: `api` (`/healthz` from inside the container, Dockerfile `HEALTHCHECK`), `web` (`/login`), `db`
  (`pg_isready`), workers (`python -m app.workers.health`: heartbeat row younger than 60 s).
- Readiness for monitoring (internal network only):
  `docker compose exec api python -c "import urllib.request as u;print(u.urlopen('http://localhost:8000/readyz').read().decode())"`
- Logs: `docker compose logs -f --tail 100 api orchestrator`. Every container uses json-file
  rotation (5 × 10 MB). Caddy access logs are JSON on stdout (same rotation).
- Graceful shutdown: uvicorn waits up to 25 s for in-flight requests (`stop_grace_period: 30s`);
  workers finish or release their job lease. Restart the APIs with `deploy/rolling-restart.sh`
  (zero errors measured); restarting both at once makes Caddy hold requests for ~10 s.

## 8. Backups and restore

`deploy/backup.sh` writes `db.dump` (pg_dump custom format), `storage.tar.gz` (all private files)
and `manifest.json` (sha256 checksums, row counts, file count), optionally encrypted with
[age](https://age-encryption.org).

```bash
# One-time: create a key pair OFF the server (your laptop); put only the PUBLIC key on the server.
age-keygen -o steward-backup.key          # keep this file offline + in your password manager
# → "Public key: age1..."

# Cron (as steward): daily 03:15, encrypted, keep 14 days locally
crontab -e
15 3 * * * cd /opt/live-agent && BACKUP_AGE_RECIPIENT=age1... BACKUP_KEEP_DAYS=14 ./deploy/backup.sh /opt/steward-backups >> /opt/steward-backups/backup.log 2>&1
```

Copy backups off the server (example with rsync to another host; or rclone to S3-compatible storage):

```bash
45 3 * * * rsync -a --delete-after /opt/steward-backups/ backup@offsite.example.com:steward/
```

**A backup is not verified until it has been restored.** Verify at least weekly and after every
upgrade (non-destructive: restores into a scratch database and directory, then deletes them):

```bash
AGE_IDENTITY=/path/to/steward-backup.key ./deploy/restore.sh verify /opt/steward-backups/steward-<stamp>
# → "result": "VERIFIED"  (row counts, file count, table ownership and grants all match)
```

(The private key is needed for this; bring it temporarily, or run `verify` on a separate restore
host with the same compose stack.)

Full restore (disaster recovery):

```bash
docker compose stop web api orchestrator tools deletion scheduler
AGE_IDENTITY=... ./deploy/restore.sh restore /opt/steward-backups/steward-<stamp>   # type: replace live data
docker compose --profile tls up -d
```

The previous database is kept as `steward_pre_restore_<UTC>` and the previous files as
`/data/storage.pre-restore`; the script prints the exact roll-back commands. Remove them yourself
once the restored system is confirmed good.

| Target | Value | Basis |
|---|---|---|
| RPO | ≤ 24 h with the daily cron (shorten with a more frequent cron) | schedule |
| RTO | ≈ 1 min for the rehearsal data set (restore + restart measured 47 s); grows with data size — measure with your own data during the weekly `verify` | rehearsal |
| Retention | 14 days local (opt-in pruning), off-site per your storage policy | config |
| Not covered | Caddy certificates (re-issued automatically), `.env` (keep in password manager) | — |

## 9. Releases, migrations and rollback

Releases are explicit and manual. CI (`.github/workflows/ci.yml`) builds and tests every push and
produces a release artifact for `v*` tags, but **never deploys**.

```bash
cd /opt/live-agent
./deploy/backup.sh /opt/steward-backups                      # 1. backup (mandatory)
./deploy/restore.sh verify /opt/steward-backups/steward-<stamp>   # 2. prove it restores
git fetch --tags && git checkout v<X.Y.Z>                    # 3. reviewed release only
docker compose --profile tls build --pull                    # 4. build new images
docker compose run --rm migrate                              # 5. migrations (backward compatible!)
COMPOSE="docker compose" deploy/rolling-restart.sh           # 6. api, then api-2: no downtime
docker compose --profile tls up -d                           # 7. web, workers, everything else
docker compose ps && docker compose logs --tail 50 migrate api api-2
```

Migrations run in the one-shot `migrate` service as `steward_owner`. Because the two API containers
are restarted one after the other, old and new code briefly run side by side: a migration must work
with the previous release (add columns/tables first; remove or rename them in a later release).
Measured: a rolling restart under ~125 req/s produced 0 errors (docs/PERFORMANCE.md §3b).
The web container restarts in a few seconds; during that time pages (not API calls) briefly fail. CI checks every migration upgrades, downgrades and upgrades again on an isolated database
and that models and schema match (`alembic check`).

Rollback:

- **Code only (no migration in the release):** `git checkout <previous tag> && docker compose --profile tls up -d --build` (rebuilds the previous version; a few minutes of build time, the running version keeps serving until the new containers start).
- **Release included a migration:** prefer restoring the pre-release backup (`restore.sh restore`),
  because downgrades may drop columns added by the release. `alembic downgrade` exists for every
  revision but is a last resort: `docker compose run --rm migrate alembic downgrade <revision>`.

Upgrading a server from an older phase: the role init script only runs on a fresh database volume.
Create roles added later once with
`docker compose exec db bash /docker-entrypoint-initdb.d/10-init-roles.sh` (existing roles are
left unchanged), then release as above.

## 10. Monitoring hooks

See `docs/OPERATIONS.md` for metrics, alert rules and the telemetry policy. In short:
`/metrics` (bearer `METRICS_TOKEN`) on the internal network gives request rates and latency,
DB pool use, queue depth and age, worker liveness, task outcomes, model call failures and
security-relevant audit counts. **Nothing is sent off the server by default.**

## 11. Troubleshooting

| Symptom | Check |
|---|---|
| `api` or a worker restarting | `docker compose logs api` — "Unsafe production configuration: …" lists exactly what to fix in `.env` |
| `migrate` failed | `docker compose logs migrate`; database reachable? roles created? After a manual restore, tables must be owned by `steward_owner` (use `restore.sh`, not raw `pg_restore`) |
| Certificate not issued | DNS A record correct? Ports 80/443 open in ufw **and** the Hostinger panel? `docker compose logs caddy` |
| Verification e-mail never arrives | SMTP host/port/credentials; mailbox allows SMTP; check spam; `docker compose logs api \| grep -i smtp` |
| 503 `service_busy` under load | DB pool saturated: see docs/PERFORMANCE.md (raise `DB_POOL_SIZE` within Postgres limits, or scale) |
| 503 `queue_full` | Workers down or overloaded: `docker compose ps`, worker logs, `/metrics` queue depth |
| Agent says "No AI model is configured" | `MODEL_PROVIDER` / `MODEL_*` in `.env`, then `docker compose up -d` |
| Disk full | `docker system df`; prune old images (`docker image prune`); move old backups off-site. **Do not delete volumes.** |
| Everyone shares one rate limit | `TRUST_PROXY_HEADERS=true` and Caddy in front (the production guard enforces this) |
| Uploads fail with "malware scanner is not available" | `docker compose ps clamav freshclam`; first start needs freshclam to finish the download; `docker compose logs clamav` |
| `clamav` keeps restarting right after install | Normal until freshclam has written the first signatures (`docker compose logs freshclam`) |
| A user can't pass two-step sign-in | Check device clock (codes are time-based, ±30 s); recovery codes; last resort §13 |
| WordPress shows "refused the username or Application Password" | Application Password revoked or mistyped; user lacks `edit_posts`; reconnect with a new one |
| API refuses to start: "INTEGRATION_SECRETS_PUBLIC_KEY must be set" / "SECRETS_ENCRYPTION_KEY …" | Generate keys (§5) and put them in `.env` |

## 12. Malware scanning (optional, recommended)

```bash
# .env: MALWARE_SCANNER=clamav      (and at least 8 GB RAM on the VPS)
docker compose --profile tls --profile scan up -d
docker compose logs -f freshclam     # first signature download: a few minutes
docker compose ps clamav             # "healthy" once signatures are loaded
```

- Every upload is streamed to clamd before it is stored. Infected files are quarantined and can't
  be opened. If clamd is down or answers with an error, uploads are **refused** (503), never
  accepted unscanned. Content clamd could not fully inspect (archive limits, encrypted Office
  files) is reported as found and quarantined (`deploy/clamav/clamd.conf`).
- clamd is reachable only from the API containers and has no internet access; freshclam is the
  only ClamAV process on the egress network.
- Files uploaded before scanning was enabled stay "Not scanned"; they are not rescanned.
- Test it with the standard EICAR test string (harmless): upload a text file containing it; it
  must show as quarantined.

## 13. Two-step sign-in (MFA) — operator notes

- Users turn it on in **Settings → Security**. It is optional per user (no workspace-wide
  requirement yet).
- A user who lost both their phone and their recovery codes can't sign in. After verifying their
  identity out of band, an operator can switch it off (this is audited only by you — note it):
  `docker compose exec db psql -U postgres -d steward -c "UPDATE user_mfa SET enabled_at = NULL, secret_enc = NULL, last_used_step = NULL WHERE user_id = (SELECT id FROM users WHERE email = 'user@example.com')"`
  and tell the user to turn it on again.
- Repeated failed second steps e-mail the account owner (the password is probably known).

## 14. WordPress draft posts (optional)

- In WordPress: **Users → Profile → Application Passwords**, create one for "Steward" with a user
  that may create posts (Author or Editor role is enough; avoid Administrator).
- In Steward: Integrations → WordPress → site address + "Also allow draft posts" (username +
  Application Password), and switch on **Settings → Tool permissions → WordPress write**.
- The agent can only create **drafts**; each one needs an approval of the exact title and text,
  and it is refused if the destination changed since approval. You publish it yourself in WordPress.
- Revoke access any time in WordPress (delete the Application Password) or with Disconnect in
  Steward (wipes the stored credential).

## 15. Go-live checklist (operator)

- [ ] DNS resolves to the VPS; HTTPS certificate issued by Let's Encrypt (browser shows a valid lock)
- [ ] `curl -sI https://<domain>/login` shows HSTS and `cache-control: no-store`
- [ ] `/metrics`, `/readyz`, `/api/v1/docs` return 404 from the internet
- [ ] `ss -tlnp` on the VPS: only 22, 80, 443 on public addresses (3000 on 127.0.0.1 only)
- [ ] Registration e-mail received; password reset e-mail received
- [ ] All workers alive in Settings → Agent
- [ ] Model provider configured and a test task planned (or `MODEL_PROVIDER=none` deliberately)
- [ ] First backup taken, copied off-site, and `restore.sh verify` → VERIFIED
- [ ] `.env` and the backup private key stored in a password manager, not on the server
- [ ] Alerts from docs/OPERATIONS.md wired to someone who will act on them
- [ ] If malware scanning is on: `docker compose ps clamav` healthy and the EICAR test file is quarantined
- [ ] Encryption keys stored in the password manager together with `.env`
- [ ] A rolling restart (`deploy/rolling-restart.sh`) completed without errors
