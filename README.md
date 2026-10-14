# Telegram Bot (Kurigram + Docker + Koyeb)

A Telegram bot built with [Kurigram](https://github.com/KurimuzonAkuma/kurigram)
(an actively maintained Pyrogram fork). It answers basic commands **and**
watches RSS feeds, posting new items to a Telegram channel.

## What the bot does

- `/start` - greeting
- `/help` - list of commands
- Any private text message - echoed back
- **RSS watcher** - checks `RSS_FEED_URL` every `CHECK_INTERVAL_MINUTES`
  and posts new items (title, date, summary, link) to `CHANNEL_ID`

## 1. Get your credentials

| Credential | Where to get it |
|---|---|
| `API_ID` and `API_HASH` | https://my.telegram.org → API development tools |
| `BOT_TOKEN` | Talk to [@BotFather](https://t.me/BotFather) on Telegram → `/newbot` |
| `CHANNEL_ID` | Your channel's `@username` or numeric id (`-100...`). Add the bot as an **admin** of the channel first |

## 2. Configure

```bash
cp .env.example .env
# edit .env: API_ID, API_HASH, BOT_TOKEN, RSS_FEED_URL, CHANNEL_ID
```

## 3. Run locally with Docker

```bash
docker compose up --build
```

Stop it with `Ctrl+C` or `docker compose down`.

## 4. Deploy on Koyeb

See [DEPLOY_KOYEB.md](DEPLOY_KOYEB.md) — deploy as a **Worker** service
(a polling bot needs no public port).

## Project layout

```
telegram-bot/
├── bot.py              # bot code: handlers + RSS watcher
├── requirements.txt    # kurigram + python-dotenv + feedparser
├── Dockerfile          # python:3.12-slim image
├── docker-compose.yml  # local run config, loads .env
├── .env.example        # copy to .env and fill in credentials
├── DEPLOY_KOYEB.md     # Koyeb deployment guide
└── seen.json           # created at runtime: ids of already-posted items
```

## Notes

- On the very first run the bot marks current feed items as seen *without*
  posting, so the channel isn't flooded with old posts.
- `seen.json` lives on local disk; on ephemeral hosts (like Koyeb) it resets
  on redeploy. Items published during a redeploy window may be skipped.
