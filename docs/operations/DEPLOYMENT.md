# Deploying to the shared VPS (step by step)

This guide puts the platform on a VPS that **already runs other projects** and
already uses **Caddy** with a **wildcard domain**. Follow the parts in order.
Text in `UPPER_CASE` is a value you replace.

Files this guide uses:

| File | What it is |
|---|---|
| `docker-compose.yml` | Starts every part of the app (own project name, no clashes) |
| `infrastructure/vps/Caddyfile.snippet` | The block you add to your existing Caddyfile |
| `.env` (you create it on the VPS) | All settings and secrets |

## Ground rules for a shared server

- Only touch the new folder and the new Docker project. Do **not** stop, restart, prune
  or "clean up" anything else. Never run `docker system prune`, `docker compose down`
  outside this project's folder, or `docker rm -f $(docker ps -aq)`.
- Do **not** install nginx or certbot, and do not change the firewall. Caddy already does
  HTTPS.
- Do **not** reuse the existing folders `outreach`, `email_outreach`, `email_outreach_new`,
  `email_outreach_new_testing`. They may be running older versions. Use a new folder.
- Every container here belongs to the fixed Compose project `outly`,
  so it cannot collide with other projects' containers or networks.

## What you will end up with

```text
Browser ──HTTPS──> Caddy (already on the server)
                     ├── /api/v1/*  ──> backend  (FastAPI)
                     └── everything else ─> frontend (Next.js)

Containers (project "outly"):
  backend, frontend, redis, scheduler, rate-controller,
  worker-general, worker-events, worker-send, worker-sync

Outside the server: Supabase (database, login, file storage),
                    Google / Microsoft (mailbox connections)
```

The database is **not** on the server. It stays in Supabase.

---

## Part 1 — Push the deployment files (on your own computer)

```powershell
git status
git add docker-compose.yml infrastructure docs/operations/DEPLOYMENT.md
git commit -m "chore: add VPS production deployment files"
git push
```

Never add `.env`; it holds secrets and git ignores it.

---

## Part 2 — Look around the server first (read-only)

These commands only **read**. Log in and run them:

```bash
ssh root@YOUR_VPS_IP

free -h                                   # memory
nproc                                     # CPU cores
df -h /                                   # disk space
docker --version && docker compose version
docker ps --format 'table {{.Names}}\t{{.Ports}}'      # what is running and on which ports
ss -tlnp | grep LISTEN                    # every port in use on the host
docker ps | grep -i caddy                 # is Caddy a container?
systemctl status caddy --no-pager | head -5   # or a normal host service?
ls /etc/caddy 2>/dev/null                 # where the Caddyfile might be
```

Write down / keep the results. They decide two things:

1. **Free ports.** Pick two unused host ports for this app. The defaults are `8000` (API)
   and `3000` (website); if either is already listed by `ss` or `docker ps`, choose others,
   for example `4130` and `4131`.
2. **How Caddy runs.** On the host (`systemctl` shows `active`) or in a Docker container
   (`docker ps` lists it). Part 8 has one method for each.

**Result on this server (checked 2026-09-24):** 8 CPU cores, 31 GB RAM (about 13 GB available,
but the 2 GB swap is completely full, so do not add memory pressure), 195 GB free disk, Docker 29.7
and Compose v5.5. Caddy runs **on the host** as a systemd service (Part 8A applies; 8B does not).
Ports 3000, 8000 and about 45 others are taken by other projects, so this project uses
`4130` (API) and `4131` (website). Confirm they are still free right before starting:

```bash
ss -tln | grep -E ':(4130|4131)\b' || echo "4130 and 4131 are free"
```

If that prints any line, pick two other unused ports and use them everywhere below.

Memory: the stack uses roughly 1.5–2 GB when running, and building the website needs
about 2 GB extra for a few minutes. If `free -h` shows less than about 3 GB **available**,
tell whoever maintains the server before continuing; the other projects share that memory.

If the server says `System restart required`, do **not** reboot it on your own: that would
briefly stop every project on it. Schedule it with whoever owns those projects.

---

## Part 3 — DNS

Your wildcard record (`*.YOURDOMAIN`) already points to this server, so any new
subdomain works without a DNS change. The platform is called **Outly**, so use
`outly.YOURDOMAIN`. Check it is not already used in the existing Caddy files:

```bash
grep -rn "outly" /etc/caddy 2>/dev/null     # should print nothing
nslookup outly.YOURDOMAIN                   # should show this server's IP
```

Below, `YOUR_DOMAIN` means that full name, for example `outly.yourcompany.com`.

---

## Part 4 — Download the code (new folder)

```bash
cd ~
git clone https://github.com/naitik4129/Email-Outreach-Platform.git outly
cd outly
ls docker-compose.yml
apt install -y postgresql-client        # only if psql is missing; it is just a client program
```

If the repository is private, Git asks for a username and password. Use your GitHub username and a
**fine-grained Personal Access Token** (read-only "Contents" access) as the password.

All later commands run from `~/outly`.

---

## Part 5 — Prepare Supabase, Google and Microsoft (in your browser)

> **Which Supabase?** This server also runs a self-hosted Supabase (`supabase-kong`, `supabase-db`,
> `supabase-pooler`) for other projects. **Do not use it for Outly.** Migration `0001` creates
> database-wide roles (`app_api`, `app_scheduler`, ...) that would land in a database shared with those
> projects. Outly uses **Supabase Cloud**: project `gjfmwuwrlwfpbhlqysqh` (Seoul region,
> `aws-0-ap-northeast-2`), the same one your local `.env` points to. If you use that project for the
> live server, the test users and data created during development will be there too. If you
> want a clean production start instead, create a second Supabase Cloud project and run all
> of Part 5 against it.

### 5.1 Database tables (all files in `supabase/migrations/`)

Check whether your Supabase project already has them. If it is the project used in development,
it most likely does. Get the **Session pooler** string (port **5432**) from
Supabase → Project Settings → Database → Connection string. Do not use "Direct connection"
(IPv6 only) or the "Transaction pooler" (port 6543).

```bash
export DB="postgresql://postgres.PROJECT_REF:DB_PASSWORD@aws-0-REGION.pooler.supabase.com:5432/postgres"
psql "$DB" -c "select count(*) as app_roles from pg_roles where rolname like 'app\_%';"
```

- **Greater than 0** → the initial chain is already applied. Skip the loop below and never
  re-run applied migrations (permanent history, `AGENTS.md` sections 6–8). This check does **not**
  show that the project is up to date: compare the newest file in `supabase/migrations/` with what
  the project has actually applied, and review and apply only the missing newer files, one at a
  time, in order.
- **0** → empty project. Apply them in order. This changes your database, so do it deliberately;
  the loop stops at the first error:

  ```bash
  cd ~/outly
  for f in supabase/migrations/*.sql; do
    echo "=== Applying $f"
    psql "$DB" -v ON_ERROR_STOP=1 -f "$f" || { echo "FAILED at $f"; break; }
  done
  ```

  If a file fails, do not edit it. Keep the error text and ask for help.

Finish with `unset DB`.

### 5.2 Storage bucket

Supabase → **Storage → New bucket** named `imports`, **Public bucket off**. Skip if it exists.

Create a second bucket named `email-attachments` (also **Public bucket off**) for sequence-step attachments and inline images. Set `SUPABASE_ATTACHMENTS_BUCKET` if you name it differently. Migration 0025 does not create it.

### 5.3 Login redirect addresses

Supabase → **Authentication → URL Configuration**:

- **Site URL:** `https://YOUR_DOMAIN`
- **Redirect URLs:** add `https://YOUR_DOMAIN/**`

### 5.4 Collect your keys

Supabase → **Project Settings → API**: Project URL, anon public key, service_role key, and (if
shown) the JWT secret. If your project only has the newer signing keys, leave the JWT secret empty.
The service_role key goes only in the server `.env`.

### 5.5 Google (only if you connect Gmail)

Google Cloud Console → **Credentials** → your OAuth client → **Authorized redirect URIs**:

```text
https://YOUR_DOMAIN/api/v1/mailboxes/connect/gmail/callback
```

Enable the **Gmail API**. In "Testing" mode, add every person who will connect a Gmail account as a
**Test user**.

### 5.6 Microsoft (only if you connect Outlook)

Azure Portal → **App registrations** → your app → **Authentication** → **Web** redirect URI:

```text
https://YOUR_DOMAIN/api/v1/mailboxes/connect/microsoft/callback
```

---

## Part 6 — Create the `.env` file

### 6.1 Get the secrets

**`MAILBOX_ENCRYPTION_KEY`: reuse your existing one.** The Supabase project this app has been using
already holds mailboxes encrypted with the key in your local `.env` (a 64-character value).
Copy that exact value to the server. Do **not** generate a new one, or those mailboxes stop working.
Only if you connect to a brand-new, empty database should you create one with
`openssl rand -hex 32`.

**`PLATFORM_OPERATOR_KEY`: create a new one.**

```bash
openssl rand -hex 24     # → PLATFORM_OPERATOR_KEY
```

Copy values between your computer and the server through a password manager or `scp`, never
through chat or email.

> **`MAILBOX_ENCRYPTION_KEY`** encrypts every connected mailbox password/token. If empty, the app
> silently uses a public built-in key (`backend/app/core/crypto.py`), which is unsafe. If you change
> it later, connected mailboxes become unreadable. If you reuse a database that already has
> mailboxes, use the **same** key as before. Back it up somewhere safe.

### 6.2 Create the file

```bash
cd ~/outly
cp .env.example .env
nano .env
```

```env
APP_ENV=production
APP_NAME="Outly"

# Session pooler string, but starting with postgresql+psycopg://
DATABASE_URL="postgresql+psycopg://postgres.PROJECT_REF:DB_PASSWORD@aws-0-REGION.pooler.supabase.com:5432/postgres"

REDIS_URL="redis://redis:6379/0"
BACKEND_CORS_ORIGINS="https://YOUR_DOMAIN"
LOG_LEVEL=INFO
LOG_FORMAT=json

# Free host ports chosen in Part 2 (defaults 8000 and 3000)
BACKEND_HOST_PORT=4130
FRONTEND_HOST_PORT=4131

SUPABASE_URL="https://PROJECT_REF.supabase.co"
SUPABASE_JWT_SECRET="YOUR_JWT_SECRET_OR_EMPTY"
SUPABASE_JWT_AUDIENCE="authenticated"
SUPABASE_SERVICE_ROLE_KEY="YOUR_SERVICE_ROLE_KEY"
SUPABASE_STORAGE_BUCKET="imports"

# These three are baked into the website when it is built.
NEXT_PUBLIC_API_BASE_URL="https://YOUR_DOMAIN"
NEXT_PUBLIC_SUPABASE_URL="https://PROJECT_REF.supabase.co"
NEXT_PUBLIC_SUPABASE_ANON_KEY="YOUR_ANON_KEY"

FRONTEND_BASE_URL="https://YOUR_DOMAIN"

GOOGLE_CLIENT_ID="..."
GOOGLE_CLIENT_SECRET="..."
GOOGLE_REDIRECT_URI="https://YOUR_DOMAIN/api/v1/mailboxes/connect/gmail/callback"

MICROSOFT_CLIENT_ID="..."
MICROSOFT_CLIENT_SECRET="..."
MICROSOFT_REDIRECT_URI="https://YOUR_DOMAIN/api/v1/mailboxes/connect/microsoft/callback"
MICROSOFT_TENANT="common"

MAILBOX_ENCRYPTION_KEY="THE_64_CHARACTER_VALUE_FROM_6.1"
MAILBOX_ENCRYPTION_KEY_ID="v1"

# The built-in default is operator@example.com,admin@example.com; always override it.
PLATFORM_OPERATOR_EMAILS="you@yourcompany.com"
PLATFORM_OPERATOR_KEY="THE_48_CHARACTER_VALUE_FROM_6.1"

SCHEDULER_ENABLED=true
SCHEDULER_POLL_SECONDS=5.0
SCHEDULER_BATCH_SIZE=50
SCHEDULER_CLAIM_LEASE_SECONDS=300
OUTBOX_PUBLISH_BATCH_SIZE=50
OUTBOX_LEASE_SECONDS=60
OUTBOX_MAX_ATTEMPTS=10

# Follow-up emails (step 2+ after a Wait). Keep false until you have read
# docs/adr/0009: turning it on makes already-running campaigns start sending
# their next step.
SEQUENCE_PROGRESSION_ENABLED=false
SEQUENCE_PROGRESSION_INTERVAL_SECONDS=30
SEQUENCE_PROGRESSION_CAMPAIGNS_PER_RUN=200
SEQUENCE_PROGRESSION_BATCH_SIZE=100
SUPABASE_ATTACHMENTS_BUCKET="email-attachments"

# Keep false for the first start. Part 9 turns it on after checking.
SENDING_WORKER_ENABLED=false
```

Save with `Ctrl+O`, `Enter`, `Ctrl+X`, then:

```bash
chmod 600 .env
```

Leave the Google / Microsoft lines empty (`""`) if you do not use them. While
`SENDING_WORKER_ENABLED=false`, no real email can be sent.

---

## Part 7 — Build and start (this project only)

Building one image at a time is gentler on a shared server:

```bash
cd ~/outly
COMPOSE_PARALLEL_LIMIT=1 docker compose build
docker compose up -d
```

The build takes about 5–15 minutes. If it stops with `Set NEXT_PUBLIC_... in .env`, that
variable is missing.

**Check the containers:**

```bash
docker compose ps
```

You must see 9 services `Up`: `redis`, `backend` (healthy), `frontend`, `scheduler`,
`rate-controller`, `worker-general`, `worker-events`, `worker-send`, `worker-sync`.

**Check the backend** (use your `BACKEND_HOST_PORT`):

```bash
curl -s http://127.0.0.1:4130/api/v1/health
curl -s http://127.0.0.1:4130/api/v1/ready
```

- First → `{"status":"ok"}`
- Second → `"status":"ready"` with `database` and `redis` both `"ok":true`

`database` false → recheck `DATABASE_URL` (password, port 5432, `postgresql+psycopg://`).

**Check the workers and the queue:**

```bash
docker compose logs --tail=30 scheduler
docker compose logs --tail=30 worker-send
docker compose logs --tail=30 rate-controller
docker compose exec worker-general python -m workers.smoke
```

Expect "Scheduler heartbeat" lines, `ready.` from `worker-send`, and a result from the smoke test
within about 10 seconds. The smoke test only tests the queue; it sends no email.

---

## Part 8 — Add the domain to Caddy

Caddy on this server is one shared service that serves **every** project. A mistake in its
config can take all of them down, so go slowly here and always validate before reloading.
Open `infrastructure/vps/Caddyfile.snippet` for the block to add.

### 8A — Caddy runs directly on the server (this server)

**Result on this server (checked 2026-09-24):** there is no wildcard-certificate block. Each site is
its own explicit host block in `/etc/caddy/Caddyfile` or in a per-site file under `/etc/caddy/sites/`
(pulled in by `import /etc/caddy/sites/*.caddy`), for example
`supa.b2botix.ai { reverse_proxy localhost:8000 }`. Caddy obtains a certificate for each new host
automatically on its first request, so Outly needs only its own file, `/etc/caddy/sites/outly.caddy`. Some
files in that directory are written by an automatic deploy service (owned by `deploy-svc`); hand-made
ones are root-owned, as Outly's should be. The global options block also imports
`custom-domains-global.caddy` (on-demand TLS for customer domains); an explicit host block like Outly's
always wins its own hostname over that catch-all.

**Step 1: see how your existing sites are written.** `/etc/caddy/` contains `Caddyfile`, a `sites`
directory and `custom-domains*.caddy` files. These commands hide anything that looks like a secret:

```bash
grep -vEi 'token|secret|key|password' /etc/caddy/Caddyfile | head -60
ls -la /etc/caddy/sites | head -40
```

Then open **one small existing site file** from `sites/` (for example the one for another
project on a subdomain) with the same `grep -vEi ...` filter in front of it, and copy its style.
Note two things: (a) whether `Caddyfile` has a line such as `import sites/*` (meaning each project
has its own file), and (b) whether there is a wildcard block `*.YOURDOMAIN { tls { dns ... } }`.

**Step 2: back up the Caddyfile** (a copy you can restore from):

```bash
sudo cp -a /etc/caddy/Caddyfile /etc/caddy/Caddyfile.backup-before-outly-$(date +%Y%m%d-%H%M%S)
```

**Step 3: add the Outly site.** Follow the same pattern as your existing sites:

- If `Caddyfile` imports `sites/*`, create a **new file** `/etc/caddy/sites/outly.caddy` containing
  the block from `infrastructure/vps/Caddyfile.snippet` with your real domain
  (ports `4130` for the API and `4131` for the website):

  ```bash
  sudo nano /etc/caddy/sites/outly.caddy
  ```

- If your other subdomains are written as `handle @name { ... }` blocks inside a wildcard
  `*.YOURDOMAIN` block instead, add Outly the same way: a host matcher for `YOUR_DOMAIN` whose `handle`
  routes are the three `handle` lines from the snippet. If you are not sure how, stop and send me the
  filtered output from Step 1; I will write the exact block.

Do not edit or delete any other project's block.

**Step 4: validate (nothing changes yet).** It must end with `Valid configuration`:

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
```

If it prints an error, fix your new block (or restore the backup from Step 2) and validate again.
Never reload until it is valid.

**Step 5: reload Caddy.** A reload does not drop other sites' connections:

```bash
sudo systemctl reload caddy
sudo systemctl status caddy --no-pager | head -5
```

If other sites misbehave after the reload, restore the backup and reload again:

```bash
sudo cp -a /etc/caddy/Caddyfile.backup-before-outly-YYYYMMDD-HHMMSS /etc/caddy/Caddyfile   # use the real backup name
sudo rm -f /etc/caddy/sites/outly.caddy
sudo systemctl reload caddy
```

### 8B — Caddy runs in a Docker container

`127.0.0.1` inside that container is the container itself, so it cannot reach the ports above.
Connect Caddy's container to this project's network instead and address the services by name:

```bash
docker network connect outly_app CADDY_CONTAINER_NAME
```

Then use this block in the Caddyfile:

```text
YOUR_DOMAIN {
	encode gzip
	handle /api/v1/webhooks/* {
		respond "Forbidden" 403
	}
	handle /api/v1/* {
		reverse_proxy backend:8000
	}
	handle {
		reverse_proxy frontend:3000
	}
}
```

Validate and reload from inside the container (your Caddyfile path may differ):

```bash
docker exec CADDY_CONTAINER_NAME caddy validate --config /etc/caddy/Caddyfile
docker exec CADDY_CONTAINER_NAME caddy reload --config /etc/caddy/Caddyfile
```

`docker network connect` lasts until Caddy's container is recreated. To make it permanent, add
`outly_app` as an `external: true` network to Caddy's own Compose file. Do
that change together with whoever maintains it.

### 8C — Test from the internet

In a browser:

- `https://YOUR_DOMAIN/api/v1/health` → `{"status":"ok"}`
- `https://YOUR_DOMAIN` → the website
- `https://YOUR_DOMAIN/api/v1/webhooks/events` → `403`. That is intentional (see "Known limitations").

---

## Part 9 — Test the whole app, then enable sending

In the browser, in order:

1. **Sign up** at `https://YOUR_DOMAIN`, confirm the email, reach onboarding.
2. Create a **workspace**; the dashboard loads with no red errors.
3. **Mailboxes → Connect** your own Gmail / Outlook / SMTP mailbox.
4. **Leads → Imports:** upload a small CSV of 3–5 rows containing **your own** addresses. It must
   reach "completed" (proves the storage bucket and `worker-general`).
5. Create a small **campaign** using that list and mailbox. The review page must show no blocking
   problems. Do not activate it yet.

### Enable real sending

Only after steps 1–5 work. From here emails are **really sent** from connected mailboxes.

```bash
cd ~/outly
nano .env            # change SENDING_WORKER_ENABLED=false to true
docker compose up -d worker-send
```

Activate the small test campaign and watch it:

```bash
docker compose logs -f worker-send     # Ctrl+C stops watching only
```

Check the test emails arrive. Reply to one; within a few minutes the reply should appear in
**Inbox** (`worker-sync` plus the scheduler). Only then use real leads, with low daily limits on
new mailboxes.

---

## Part 10 — Everyday operations

Always run these from `~/outly` (the project name `outly` is fixed in
`docker-compose.yml`), so they affect only this project.

```bash
docker compose ps
docker compose logs --tail=100 backend
docker compose logs -f scheduler
docker compose restart worker-send
docker compose up -d                       # after editing .env
docker compose up -d --build frontend      # after changing any NEXT_PUBLIC_ value
```

**Update to new code**

```bash
cd ~/outly
git pull
COMPOSE_PARALLEL_LIMIT=1 docker compose build
docker compose up -d
```

If the update adds a file in `supabase/migrations/`, review and apply it as its own deliberate step
**before** this (Part 5.1). Never edit or re-run older migrations.

**Stop only this project** (data is safe; it lives in Supabase)

```bash
docker compose down
```

**Backups:** data is in Supabase (use its backups). Keep a copy of `.env`, especially
`MAILBOX_ENCRYPTION_KEY`, somewhere safe outside the server.

**After a reboot:** containers use `restart: unless-stopped`, so they return on their own once Docker
starts.

---

## Part 11 — If something goes wrong

| Symptom | What to check |
|---|---|
| `up` says a variable must be set | It is missing in `.env`. |
| `port is already allocated` | Another project uses that host port. Choose different `BACKEND_HOST_PORT` / `FRONTEND_HOST_PORT`, then `up -d` again. |
| `/api/v1/ready` shows `database: false` | Wrong `DATABASE_URL`. Use the Session pooler on port 5432, starting with `postgresql+psycopg://`. |
| `/api/v1/ready` shows `redis: false` | `REDIS_URL` must be `redis://redis:6379/0`. |
| Website loads but every request fails or logs you out | `NEXT_PUBLIC_API_BASE_URL` is wrong. Fix it and rebuild `frontend`. |
| Browser CORS error | `BACKEND_CORS_ORIGINS` must be exactly `https://YOUR_DOMAIN` (no trailing slash). |
| Login works, then 401 errors | `SUPABASE_URL` / JWT secret belong to a different Supabase project than `NEXT_PUBLIC_SUPABASE_*`. |
| Sign-up emails link to `localhost` | Fix the Supabase Site URL (5.3). |
| Gmail `redirect_uri_mismatch` | The Google Console address must exactly match `GOOGLE_REDIRECT_URI`. |
| CSV import stays "pending" | Check `worker-general` logs, the `imports` bucket, and `SUPABASE_SERVICE_ROLE_KEY`. |
| Campaign running but nothing sends | Is `SENDING_WORKER_ENABLED=true`? Check `worker-send` and `rate-controller` logs and mailbox health. |
| Build is killed / server slows down | Out of memory. Stop this project's build, and check `free -h` before retrying. |
| Caddy `502` | The container is down (`ps`), or the Caddy block has the wrong port or (8B) Caddy is not on the project's network. |
| Caddy cannot get a certificate for the subdomain | The wildcard setup is not covering it. Ask whoever manages Caddy/DNS; do not change other sites' blocks. |

---

## Known limitations

Found while preparing this guide. They are code or design matters, not deployment steps, and were not changed here.

1. **The webhook endpoints have no authentication (security issue).**
   `backend/app/api/v1/webhooks.py` reads `gmail_webhook_secret`, `event_webhook_secret` and
   `webhook_secret` from settings, but none of them exist in `backend/app/core/config.py`, so
   they can never be set from `.env`. The three adapters skip verification when no secret is set,
   so `POST /api/v1/webhooks/gmail`, `/microsoft` and `/events` accept anyone's requests, and
   `/events` places bounce/complaint/unsubscribe safety holds immediately. **The Caddy block
   therefore returns 403 for `/api/v1/webhooks/*`.** Do not register webhook URLs with Google,
   Microsoft or an email provider, and do not remove that block, until the secrets are added to
   `Settings` and the adapters reject requests when no secret is configured. Replies are still
   collected by mailbox polling (`worker-sync`), so the MVP works without webhooks.
2. **Webhook events would not be queued from the containerized backend.**
   `webhooks.py` imports `workers.celery_app`, but the backend image (`backend/Dockerfile`)
   does not contain the `workers` package, so the follow-up `event.process` task is not published, and
   nothing in the scheduler re-queues the stored receipts (`recover_stale_event_processing` in
   `workers/events.py` is defined but never called). The intended fix is small: use the existing
   `get_task_producer()` in `backend/app/services/task_dispatch.py`. Fix it together with item 1
   before enabling webhooks.
3. **Shared server, shared failure.** Other projects share this server's CPU, memory and Docker
   daemon. A memory-hungry neighbour can slow sending; watch `free -h` and `docker stats`.
   Custom SMTP mailboxes may also be throttled by the hosting provider (see
   `docs/product/PROJECT_CONTEXT.md`, sections 103–104); Gmail and Microsoft mailboxes use HTTPS
   and are not affected.
