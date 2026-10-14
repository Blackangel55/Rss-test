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
    PORT                   - port for the health-check server (default: 8000).
                             Koyeb sets this automatically for web services.

Run locally:  docker compose up --build
Deploy:       see DEPLOY_KOYEB.md (Worker service, or Web service with health check)
"""

import html
import json
import logging
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import feedparser
from dotenv import load_dotenv

# Kurigram is a drop-in Pyrogram fork, so the import name stays `pyrogram`.
from pyrogram import Client, enums, filters, idle
from pyrogram.types import LinkPreviewOptions

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

    try:
        port = int(os.environ.get("PORT", "8000"))
    except ValueError:
        logger.warning("PORT is not a number, falling back to 8000.")
        port = 8000

    return {
        "api_id": api_id,
        "api_hash": api_hash,
        "bot_token": bot_token,
        "feed_urls": feed_urls,
        "channel_id": parse_channel_id(channel_id_raw),
        "interval": interval,
        "port": port,
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


# Some sites (e.g. Nyaa behind Cloudflare) block feedparser's default
# User-Agent or datacenter IPs with an HTML challenge page instead of XML,
# so we fetch with a browser UA and log what we actually received.
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


def download(url: str):
    """
    GET a URL with retries on transient failures (blocking).
    Returns (status, data) on success, (None, None) on failure.
    Retries 5xx responses and connection errors; 4xx fails immediately.
    """
    for attempt in range(1, 4):
        req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            if 500 <= e.code < 600 and attempt < 3:
                wait = 5 * attempt
                logger.warning("Feed %s: HTTP %s (attempt %d/3), retrying in %ds...",
                               url, e.code, attempt, wait)
                time.sleep(wait)
                continue
            logger.warning("Feed %s returned HTTP %s.", url, e.code)
            return None, None
        except Exception:
            if attempt < 3:
                wait = 5 * attempt
                logger.warning("Feed %s: download failed (attempt %d/3), retrying in %ds...",
                               url, attempt, wait)
                time.sleep(wait)
                continue
            logger.exception("Error downloading feed %s.", url)
            return None, None
    return None, None  # unreachable, safety net


def fetch_feed(url: str) -> list:
    """
    Download and parse one feed (blocking) - runs in the RSS thread.
    Never raises: returns [] on any failure.
    """
    status, data = download(url)
    if data is None:
        return []

    parsed = feedparser.parse(data)
    if parsed.bozo and not parsed.entries:
        snippet = data[:200].decode("utf-8", "replace").replace("\n", " ")
        logger.warning(
            "Could not parse feed %s (HTTP %s): %s | body starts with: %.200s",
            url, status, parsed.bozo_exception, snippet,
        )
        return []
    return parsed.entries


def check_feeds(feed_urls: list, seen: dict) -> list:
    """
    Fetch every feed and return entries that have not been seen yet.

    The first time a feed is *successfully* fetched, its current items are
    only marked as seen so the channel is not flooded with old posts.
    A failed fetch leaves that feed's state untouched, so recovering from
    an outage (or a fresh deploy whose first checks fail, e.g. on Koyeb's
    ephemeral filesystem) still seeds quietly instead of posting the
    whole backlog at once.
    """
    new_items = []
    for url in feed_urls:
        entries = fetch_feed(url)
        if not entries:
            # Fetch failed (or the feed is genuinely empty): don't touch
            # `seen` for this feed, so the next successful fetch seeds quietly.
            logger.info("Checked %s: no entries fetched; will retry next cycle.", url)
            continue

        known = set(seen.get(url, []) or [])
        first_success = not seen.get(url)
        fresh = []
        for entry in entries:
            uid = entry_uid(entry)
            if uid and uid not in known:
                fresh.append((uid, entry))

        # Oldest first, so the channel reads in chronological order.
        for uid, entry in reversed(fresh):
            known.add(uid)
            if not first_success:
                new_items.append(entry)
        seen[url] = sorted(known)[-MAX_SEEN_PER_FEED:]
        logger.info("Checked %s: %d new item(s).", url, 0 if first_success else len(fresh))
    return new_items


def rss_poller_loop(feed_urls: list, channel_id, interval_minutes: int):
    """Background thread: poll feeds forever and post new items to the channel."""
    seen = load_seen()

    while True:
        try:
            new_items = check_feeds(feed_urls, seen)
            save_seen(seen)
            for entry in new_items:
                try:
                    app.send_message(
                        channel_id,
                        format_entry(entry),
                        parse_mode=enums.ParseMode.HTML,
                        link_preview_options=LinkPreviewOptions(is_disabled=False),
                    )
                    logger.info("Posted to channel: %s", entry.get("title"))
                except Exception:
                    logger.exception("Failed to post entry: %s", entry.get("title"))
                time.sleep(1)  # gentle pacing to respect Telegram rate limits
        except Exception:
            logger.exception("RSS poller iteration failed")
        time.sleep(interval_minutes * 60)


# ------------------------------------------------------- health check ---

def start_health_server(port: int):
    """
    Minimal HTTP server so PaaS health checks (e.g. Koyeb web services)
    see a healthy port. Runs in a daemon thread; the bot itself still
    uses polling and needs no incoming connections.
    """
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        server_version = "HealthBot/1.0"

        def _respond(self, include_body: bool):
            body = b'{"status":"ok","service":"telegram-bot"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if include_body:
                self.wfile.write(body)

        def _handle(self, include_body: bool):
            if self.path in ("/", "/health", "/healthz"):
                self._respond(include_body)
            else:
                self.send_error(404)

        def do_GET(self):
            self._handle(include_body=True)

        def do_HEAD(self):
            self._handle(include_body=False)

        def log_message(self, *args):
            logger.debug("health: %s", args[0] % args[1:])

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="health-server")
    thread.start()
    logger.info("Health check server listening on 0.0.0.0:%d.", port)
    return server


# ------------------------------------------------------------------ main ---

if __name__ == "__main__":
    # Bind the health-check port first so the platform sees us as alive
    # even while Telegram is still connecting.
    start_health_server(config["port"])
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
