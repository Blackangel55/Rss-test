"""
Telegram bot built with Kurigram, ready for Docker and Koyeb.

Features:
    - /start, /help and echo handlers
    - RSS watcher: polls RSS_FEED_URL every CHECK_INTERVAL_MINUTES and
      posts new items to CHANNEL_ID.

Configuration (environment variables, see .env.example):
    API_ID, API_HASH       - from https://my.telegram.org
    BOT_TOKEN              - from @BotFather on Telegram
    RSS_FEED_URL           - one or more RSS/Atom feed URLs, comma-separated
    CHANNEL_ID             - @channelusername or numeric id like -1001234567890
                             (the bot must be an admin of the channel)
    CHECK_INTERVAL_MINUTES - how often to check the feeds (default: 15)

Run locally:  docker compose up --build
Deploy:       see DEPLOY_KOYEB.md (deploy as a Worker service)
"""

import html
import json
import logging
import os
import re
import sys
import threading
import time
from pathlib import Path

import feedparser
from dotenv import load_dotenv

# Kurigram is a drop-in Pyrogram fork, so the import name stays `pyrogram`.
from pyrogram import Client, enums, filters, idle

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
)
logger = logging.getLogger(__name__)

STATE_FILE = Path(__file__).with_name("seen.json")
MAX_SEEN_PER_FEED = 200


# ---------------------------------------------------------------- config ---

def parse_channel_id(raw: str):
    """Accept '@channelname' or a numeric id like -1001234567890."""
    raw = raw.strip()
    try:
        return int(raw)
    except ValueError:
        return raw


def get_config() -> dict:
    """Read and validate configuration from the environment."""
    try:
        api_id = int(os.environ["API_ID"])
    except (KeyError, ValueError):
        sys.exit("API_ID is missing or not a number. Copy .env.example to .env and fill it in.")

    api_hash = os.environ.get("API_HASH")
    if not api_hash:
        sys.exit("API_HASH is missing. Copy .env.example to .env and fill it in.")

    bot_token = os.environ.get("BOT_TOKEN")
    if not bot_token:
        sys.exit("BOT_TOKEN is missing. Copy .env.example to .env and fill it in.")

    feed_urls = [u.strip() for u in os.environ.get("RSS_FEED_URL", "").split(",") if u.strip()]
    if not feed_urls:
        sys.exit("RSS_FEED_URL is missing. Copy .env.example to .env and fill it in.")

    channel_id_raw = os.environ.get("CHANNEL_ID")
    if not channel_id_raw:
        sys.exit("CHANNEL_ID is missing. Copy .env.example to .env and fill it in.")

    try:
        interval = int(os.environ.get("CHECK_INTERVAL_MINUTES", "15"))
        if interval < 1:
            raise ValueError
    except ValueError:
        sys.exit("CHECK_INTERVAL_MINUTES must be a positive number of minutes.")

    return {
        "api_id": api_id,
        "api_hash": api_hash,
        "bot_token": bot_token,
        "feed_urls": feed_urls,
        "channel_id": parse_channel_id(channel_id_raw),
        "interval": interval,
    }


config = get_config()

app = Client(
    "telegram-bot",
    api_id=config["api_id"],
    api_hash=config["api_hash"],
    bot_token=config["bot_token"],
)


# ------------------------------------------------------------- chat cmds ---

@app.on_message(filters.command("start"))
async def start_handler(client: Client, message):
    """Greet the user when they send /start."""
    await message.reply_text(
        "Hi! I'm alive and running.\n\n"
        "I watch RSS feeds and post new items to the channel.\n"
        "Try /help to see what I can do."
    )


@app.on_message(filters.command("help"))
async def help_handler(client: Client, message):
    """Show the available commands."""
    await message.reply_text(
        "Here's what I understand:\n"
        "/start - say hello\n"
        "/help  - show this message\n"
        "Anything else - I'll echo it back"
    )


@app.on_message(filters.private & ~filters.command(["start", "help"]))
async def echo_handler(client: Client, message):
    """Echo private text messages back to the sender."""
    if message.text:
        await message.reply_text(f"You said: {message.text}")
    else:
        await message.reply_text("I only echo text messages for now.")


# ------------------------------------------------------------ rss watcher ---

def load_seen() -> dict:
    """Load the ids of already-posted entries from disk."""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            logger.warning("Could not read %s, starting fresh.", STATE_FILE)
    return {}


def save_seen(seen: dict) -> None:
    """Persist the ids of already-posted entries to disk."""
    try:
        STATE_FILE.write_text(json.dumps(seen, indent=2))
    except OSError:
        logger.warning("Could not write %s.", STATE_FILE)


def entry_uid(entry) -> str:
    """Stable id for a feed entry (falls back to link, then title)."""
    return entry.get("id") or entry.get("guid") or entry.get("link") or entry.get("title", "")


def strip_html(text: str) -> str:
    """Remove HTML tags from a summary/description."""
    return re.sub(r"<[^>]+>", "", text or "").strip()


def format_entry(entry) -> str:
    """Format a feed entry as an HTML message for the channel."""
    title = html.escape(entry.get("title", "Untitled"))
    link = entry.get("link", "")
    published = html.escape(entry.get("published", ""))
    summary = strip_html(entry.get("summary", "") or entry.get("description", ""))
    if len(summary) > 300:
        summary = summary[:297] + "..."

    lines = [f"<b>{title}</b>"]
    if published:
        lines.append(f"<i>{published}</i>")
    if summary:
        lines.append(f"\n{html.escape(summary)}")
    if link:
        lines.append(f'\n<a href="{html.escape(link, quote=True)}">Read more</a>')
    return "\n".join(lines)


def check_feeds(feed_urls: list, seen: dict, first_run: bool) -> list:
    """
    Fetch every feed and return entries that have not been seen yet.

    On the very first run nothing is returned: current items are only
    marked as seen so the channel is not flooded with old posts.
    """
    new_items = []
    for url in feed_urls:
        try:
            parsed = feedparser.parse(url)
        except Exception:
            logger.exception("Error fetching feed %s", url)
            continue
        if parsed.bozo and not parsed.entries:
            logger.warning("Could not parse feed %s: %s", url, parsed.bozo_exception)
            continue

        known = set(seen.get(url, []))
        fresh = []
        for entry in parsed.entries:
            uid = entry_uid(entry)
            if uid and uid not in known:
                fresh.append((uid, entry))

        # Oldest first, so the channel reads in chronological order.
        for uid, entry in reversed(fresh):
            known.add(uid)
            if not first_run:
                new_items.append(entry)
        seen[url] = sorted(known)[-MAX_SEEN_PER_FEED:]
        logger.info("Checked %s: %d new item(s).", url, 0 if first_run else len(fresh))
    return new_items


def rss_poller_loop(feed_urls: list, channel_id, interval_minutes: int):
    """Background thread: poll feeds forever and post new items to the channel."""
    seen = load_seen()
    first_run = not STATE_FILE.exists()
    if first_run:
        logger.info("First run: marking current feed items as seen (nothing will be posted).")

    while True:
        try:
            new_items = check_feeds(feed_urls, seen, first_run)
            save_seen(seen)
            for entry in new_items:
                try:
                    app.send_message(
                        channel_id,
                        format_entry(entry),
                        parse_mode=enums.ParseMode.HTML,
                        disable_web_page_preview=False,
                    )
                    logger.info("Posted to channel: %s", entry.get("title"))
                except Exception:
                    logger.exception("Failed to post entry: %s", entry.get("title"))
                time.sleep(1)  # gentle pacing to respect Telegram rate limits
            first_run = False
        except Exception:
            logger.exception("RSS poller iteration failed")
        time.sleep(interval_minutes * 60)


# ------------------------------------------------------------------ main ---

if __name__ == "__main__":
    app.start()
    threading.Thread(
        target=rss_poller_loop,
        args=(config["feed_urls"], config["channel_id"], config["interval"]),
        daemon=True,
        name="rss-poller",
    ).start()
    logger.info(
        "Bot started. Watching %d feed(s), posting to %s every %d min.",
        len(config["feed_urls"]), config["channel_id"], config["interval"],
    )
    idle()  # block until SIGINT/SIGTERM (e.g. Ctrl+C or a Koyeb redeploy)
    app.stop()
    logger.info("Bot stopped.")
