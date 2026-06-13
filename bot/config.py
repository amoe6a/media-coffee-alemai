"""Configuration loaded from environment variables (.env via docker compose)."""
import hashlib
import os
from dataclasses import dataclass


def _parse_ids(raw: str) -> list[int]:
    out = []
    for part in (raw or "").replace(";", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            out.append(int(part))
    return out


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_ids: list[int]
    webhook_url: str | None   # public base URL, e.g. https://xyz.trycloudflare.com
    webhook_secret: str       # Telegram X-Telegram-Bot-Api-Secret-Token header
    url_path: str             # non-guessable path the webhook listens on
    port: int
    db_path: str
    currency: str


def load() -> Config:
    token = os.environ.get("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit(
            "BOT_TOKEN is not set. Get one from @BotFather and put it in .env"
        )

    # Public base URL. An explicit WEBHOOK_URL always wins; otherwise we
    # auto-detect the platform: Render injects RENDER_EXTERNAL_URL, and Fly.io
    # injects FLY_APP_NAME (apps are reachable at https://<name>.fly.dev).
    fly_app = os.environ.get("FLY_APP_NAME", "").strip()
    base = (
        os.environ.get("WEBHOOK_URL", "").strip()
        or os.environ.get("RENDER_EXTERNAL_URL", "").strip()
        or (f"https://{fly_app}.fly.dev" if fly_app else "")
    ).rstrip("/") or None

    # Stable, non-guessable values derived from the token unless overridden.
    secret = os.environ.get("WEBHOOK_SECRET", "").strip() or hashlib.sha256(
        f"secret:{token}".encode()
    ).hexdigest()[:32]
    url_path = hashlib.sha256(f"path:{token}".encode()).hexdigest()[:24]

    return Config(
        bot_token=token,
        admin_ids=_parse_ids(os.environ.get("ADMIN_IDS", "")),
        webhook_url=base,
        webhook_secret=secret,
        url_path=url_path,
        port=int(os.environ.get("PORT", "8080")),
        db_path=os.environ.get("DB_PATH", "data/coffee.db"),
        currency=os.environ.get("CURRENCY", "$"),
    )
