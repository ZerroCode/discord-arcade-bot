"""Read configuration once; environment variables take precedence over .env."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    token: str = field(repr=False)
    guild_id: int | None = None
    mongodb_uri: str = field(default="mongodb://localhost:27017", repr=False)
    mongodb_database: str = "discord_arcade"


def load_config() -> Config:
    load_dotenv(Path(__file__).parent / ".env", override=False)
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        raise ValueError("DISCORD_TOKEN is required. Set it in the environment or .env.")

    raw_guild_id = os.getenv("GUILD_ID", "").strip()
    guild_id = None
    if raw_guild_id:
        try:
            guild_id = int(raw_guild_id)
        except ValueError:
            raise ValueError("GUILD_ID must be a positive integer or left empty.") from None
        if not 0 < guild_id < 2**64:
            raise ValueError("GUILD_ID must be a valid positive Discord ID.")

    uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017").strip()
    database = os.getenv("MONGODB_DATABASE", "discord_arcade").strip()
    if not uri.startswith(("mongodb://", "mongodb+srv://")):
        raise ValueError("MONGODB_URI must be a MongoDB connection string.")
    if not database or any(c in database for c in '/\\. "$*<>:|?\x00'):
        raise ValueError("MONGODB_DATABASE must be a valid nonempty database name.")
    return Config(token=token, guild_id=guild_id, mongodb_uri=uri, mongodb_database=database)
