# Deploying this bot on Koyeb

A Telegram polling bot has no website to serve. The cleanest fit is a
**Worker** service (background task, no public port needed) — but if your
plan only offers **Web** services, that works too: the bot now includes a
tiny built-in health-check server, so Koyeb's port check passes.

## 1. Push the project to GitHub

```bash
cd telegram-bot
git init
git add .
git commit -m "Telegram RSS bot"
git branch -M main
git remote add origin https://github.com/<your-username>/<repo>.git
git push -u origin main
```

> Do **not** commit your real `.env` file. Only `.env.example` belongs in git.

## 2. Create the Koyeb service

1. Go to the [Koyeb dashboard](https://app.koyeb.com) → **Create Service**.
2. Choose **GitHub** as the source and select your repository (branch `main`).
3. Builder: **Dockerfile** (Koyeb auto-detects the `Dockerfile` in the repo root).
4. **Service type:**
   - **Worker** (preferred) — no port or health check needed.
   - **Web** (if Worker isn't available on your plan) — keep the port at
     `8000` (or whatever `PORT` Koyeb assigns). The bot answers Koyeb's
     health check itself on `/`, `/health` and `/healthz`, so the
     "TCP health check failed" error goes away. No extra configuration needed.
5. Pick a region and an instance type that fits your plan.
6. Add these **environment variables**:
   - `API_ID`
   - `API_HASH`
   - `BOT_TOKEN`
   - `RSS_FEED_URL`
   - `CHANNEL_ID`
   - `CHECK_INTERVAL_MINUTES`
   - (`PORT` is set automatically by Koyeb for web services; you can omit it.)
7. Deploy, then watch the **Logs** tab. You should see:
   `Health check server listening on 0.0.0.0:8000.`
   followed by `Bot started. Watching 1 feed(s), posting to @mychannel every 15 min.`

## 3. Prepare the Telegram channel

1. Create the channel (or use an existing one).
2. Add your bot as an **administrator** of the channel
   (otherwise Telegram won't let it post).
3. Set `CHANNEL_ID` to the channel's `@username`, or its numeric id
   (looks like `-1001234567890`).

## Notes

- **Ephemeral disk:** Koyeb instances don't keep files between redeploys, so
  `seen.json` (which tracks already-posted items) resets on each deploy.
  The bot handles this gracefully: on first start it marks current feed
  items as seen *without* posting, so your channel won't get flooded —
  but items published during a redeploy window may be skipped.
- **Redeploys:** every push to `main` triggers a new deployment automatically.
  Koyeb sends SIGTERM first, which the bot handles to shut down cleanly.
- **Logs:** check the service logs if nothing arrives in the channel —
  most issues are a missing env var or the bot not being a channel admin.
