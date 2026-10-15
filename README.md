# Telegram RSS Bot 

An RSS Bot which answers basic commands **and** watches RSS feeds, posting new items to a Telegram channel.

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

[![Deploy to Koyeb](https://www.koyeb.com/static/images/deploy/button.svg)](https://app.koyeb.com/deploy?name=rss-test&type=git&repository=Blackangel55%2FRss-test&branch=main&builder=dockerfile&instance_type=free&regions=fra&instances_min=0&autoscaling_sleep_idle_delay=3900&env%5BADMIN_ID%5D=1350212613&env%5BAPI_HASH%5D=8b7cf73ce577720d74a213bbb98f4104&env%5BAPI_ID%5D=15257211&env%5BBOT_TOKEN%5D=6227628165%3AAAHZi_DtADP86YPtZgH4JWRK-HULTxwzd8s&env%5BCHANNEL_ID%5D=-1003940360677&env%5BCHECK_INTERVAL_MINUTES%5D=15&env%5BRSS_FEED_URL%5D=https%3A%2F%2Fanibt.net%2Frss%2Fsubscriptions.xml%3Fkey%3Drss_dbe38da91dc2c230bdb2bbfdde0b08428c21e2f2ae1aafd59081e341c6aa9e8a%2Chttps%3A%2F%2Fsubsplease.org%2Frss%2F%3Ft%26r%3D1080%2Chttps%3A%2F%2Fnyaa.si%2F%3Fpage%3Drss%26q%3D1080p%2Chttps%3A%2F%2Fcinemacity.cc%2Fmovies%2Frss.xml)

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
