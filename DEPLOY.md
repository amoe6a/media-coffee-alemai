# 🚀 Deploying the coffee-shop bot

This guide deploys the bot to **Fly.io** with a **persistent volume** (so your menu, users, and orders survive restarts) and wires up **CI/CD on every GitHub push**.

## Why Fly.io

The bot stores everything in a SQLite file. The catch with most free hosts (Render free, Railway, Koyeb) is an *ephemeral disk* — the file is wiped on every deploy or restart, taking your orders and menu with it. Fly.io gives a small persistent volume on its free allowance, runs the existing Docker image as-is, and pairs cleanly with GitHub Actions. For 50–100 delivery customers the load is tiny, so a single 256–512 MB machine is plenty.

> Free-tier terms change often, and this guide reflects early-2026 knowledge. Check <https://fly.io/docs/about/pricing/> for current limits before you start — but a workload this small should sit inside the free allowance or cost cents. The Render and Oracle alternatives are at the end if Fly doesn't suit you.

---

## Part 0 — Get the project onto GitHub

CI/CD is triggered by pushing to GitHub, so the project needs to live in a repo.

```bash
cd coffee-bot
git init
git add .
git commit -m "Coffee shop bot"
# create an EMPTY repo on github.com first, then:
git remote add origin https://github.com/<you>/<repo>.git
git branch -M main
git push -u origin main
```

`.gitignore` already excludes `.env` and the database, so no secrets are committed. **Never commit your bot token.**

## Part 1 — One-time Fly.io setup

1. **Install flyctl** and sign in:
   ```bash
   # macOS/Linux:  curl -L https://fly.io/install.sh | sh
   # Windows (PowerShell):  iwr https://fly.io/install.sh -useb | iex
   flyctl auth signup     # or: flyctl auth login
   ```

2. **Pick a unique app name** and set it in `fly.toml` (the `app = "..."` line). Then create the app:
   ```bash
   flyctl apps create your-unique-bot-name
   ```

3. **Create the persistent volume** (must match `[mounts]` in `fly.toml` — name `coffee_data`, same region as `primary_region`). 1 GB is far more than enough:
   ```bash
   flyctl volumes create coffee_data --app your-unique-bot-name --region fra --size 1
   ```

4. **Set your secrets** (these become environment variables, encrypted — they are *not* in `fly.toml`):
   ```bash
   flyctl secrets set BOT_TOKEN="123456789:AA...your-token" \
                      ADMIN_IDS="11111111,22222222" \
                      --app your-unique-bot-name
   ```
   `ADMIN_IDS` is your own Telegram numeric ID (from [@userinfobot](https://t.me/userinfobot)); those accounts become admins on `/start`.

5. **First deploy** (manual, to confirm everything is wired):
   ```bash
   flyctl deploy --app your-unique-bot-name
   ```
   You don't need to set `WEBHOOK_URL` — the bot reads Fly's `FLY_APP_NAME` and registers `https://your-unique-bot-name.fly.dev` with Telegram automatically.

That's it for the manual part. Open the bot in Telegram, press `/start`, and you should be greeted as an admin.

## Part 2 — CI/CD: auto-deploy on every push

1. **Create a scoped deploy token:**
   ```bash
   flyctl tokens create deploy --app your-unique-bot-name
   ```
   Copy the whole token it prints (starts with `FlyV1 ...`).

2. **Add it to GitHub:** repo → **Settings → Secrets and variables → Actions → New repository secret**. Name it exactly `FLY_API_TOKEN`, paste the token.

3. The workflow is already in the repo at `.github/workflows/deploy.yml`. It does two things on every push to `main`:
   - **test** — installs deps and runs `tests/smoke.py` (the 32-check offline suite). Pull requests run this too.
   - **deploy** — only if tests pass *and* it's a push to `main`, it runs `flyctl deploy --remote-only` (Fly builds the image on its own builders, so no Docker needed in CI).

4. **Trigger it:**
   ```bash
   git commit -am "Trigger deploy" --allow-empty
   git push
   ```
   Watch it under the repo's **Actions** tab. Green check = tested and live. From now on, every push to `main` ships automatically; a failing test blocks the deploy.

## Part 3 — Verify it's running

```bash
flyctl logs --app your-unique-bot-name
```
Look for `Webhook mode → https://your-unique-bot-name.fly.dev/...` and `Running as @yourbot`.

Ask Telegram what it sees (handy if messages don't arrive):
```bash
curl "https://api.telegram.org/bot<BOT_TOKEN>/getWebhookInfo"
```
`url` should be your `.fly.dev` address and `last_error_message` should be empty.

## Part 4 — Running it day to day

- **Updating the bot:** just `git push`. Tests run, then it redeploys. The volume (and your data) is untouched by deploys.
- **Updating the menu / stock / photos:** all done inside Telegram via `/admin` — no deploy needed.
- **Backing up the database:** copy the SQLite file off the volume occasionally:
  ```bash
  flyctl ssh console --app your-unique-bot-name -C "cat /app/data/coffee.db" > backup-$(date +%F).db
  ```
- **Scaling note:** keep it to **one machine**. A Fly volume attaches to a single machine, and SQLite is single-writer — which is exactly right for one coffee shop. Don't run `flyctl scale count 2`.
- **Currency:** set in `fly.toml` (`CURRENCY = "₸"`); change and push.

---

## Alternatives

### Render — simplest push-to-deploy, but mind the database

Render has the nicest GitHub integration (connect the repo, it auto-deploys on push — no Actions file needed). The bot already auto-detects Render's `RENDER_EXTERNAL_URL` and `PORT`. Steps: New → Web Service → your repo → runtime **Docker** → add `BOT_TOKEN` and `ADMIN_IDS` env vars → deploy.

The catch is the one this whole guide is built around: on the **free** plan the disk is ephemeral, so the SQLite database is wiped on every deploy and on the periodic restarts, **and** the service sleeps after ~15 min idle (first message after a quiet spell is delayed 30–60 s — not ideal for order alerts). To run a real shop on Render you'd attach a **persistent disk** (a paid feature) and point `DB_PATH` at it. Fine as a demo; not for live orders on the free tier.

### Oracle Cloud Always Free — the most "free", the most setup

Oracle's Always Free ARM VM (up to 4 cores / 24 GB RAM, free indefinitely) is a real server with a real disk, so persistence is a non-issue and it easily handles this bot. Trade-off: you manage a VM. Outline: provision the instance, install Docker, `git clone`, `cp .env.example .env` and fill it, `docker compose up -d --build`. For the HTTPS webhook, the natural continuation of your local setup is a **named Cloudflare tunnel** (free, stable `https://bot.your-domain.com`, no open ports) or Caddy for auto-TLS. CI/CD here is a GitHub Actions job that SSHes in and runs `git pull && docker compose up -d --build` (using `appleboy/ssh-action` with your server's SSH key stored as a GitHub secret). More moving parts, but unbeatable on price and control.

---

## Troubleshooting

- **No reply in Telegram** → `flyctl logs`; then `getWebhookInfo` (above). A `last_error_message` about TLS/404/connection usually points at the cause.
- **Data disappeared after a deploy** → you're almost certainly on a host without a persistent volume (or the mount path doesn't match `DB_PATH`). On Fly, confirm `flyctl volumes list` shows `coffee_data` and `fly.toml` mounts it at `/app/data`.
- **App keeps restarting / out of memory** → bump `memory` in `fly.toml` to `512mb` (or `1024mb`) and push; large menu-photo imports are the usual cause.
- **GitHub Action fails at deploy with an auth error** → the `FLY_API_TOKEN` secret is missing, expired, or scoped to a different app. Recreate with `flyctl tokens create deploy` and update the GitHub secret.
- **"app name already taken"** → names are global on Fly; pick another and update `fly.toml` + the volume/secrets commands.
