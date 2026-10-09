from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


def load_dotenv(path: Path | str = ROOT_DIR / ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    with env_path.open("r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    bot_env: str
    pubg_api_key: str
    pubg_max_match_age_hours: float
    seen_matches_path: Path
    players_path: Path
    discord_bot_token: str | None = None
    discord_channel_id: str | None = None
    discord_thread_id: str | None = None
    discord_guild_id: str | None = None
    local_database_path: Path = ROOT_DIR / "data" / "imposter_finder.db"
    pubg_poll_interval_minutes: float = 15.0
    healthcheck_imposter_finder_url: str | None = None
    nexon_api_key: str | None = None
    nexon_daily_limit: int = 900
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.5-flash-lite"
    # FC 리포트는 BOT_ENV에 맞는 thread 하나로 보낸다
    discord_fc_thread_ids: tuple[str, ...] = ()
    fc_poll_interval_minutes: float = 15.0


def load_settings() -> Settings:
    load_dotenv()
    bot_env = env("BOT_ENV", "dev")
    if bot_env not in {"dev", "prod"}:
        raise RuntimeError("BOT_ENV must be 'dev' or 'prod'")

    pubg_api_key = env("PUBG_API_KEY")
    if not pubg_api_key:
        raise RuntimeError("PUBG_API_KEY is required in .env")
    pubg_max_match_age_hours = float(env("PUBG_MAX_MATCH_AGE_HOURS", "12"))

    channel_id = env("DISCORD_CHANNEL_ID") or env(f"DISCORD_CHANNEL_{bot_env.upper()}")
    thread_id = env("DISCORD_THREAD_ID") or env(f"DISCORD_THREAD_{bot_env.upper()}")

    return Settings(
        bot_env=bot_env,
        pubg_api_key=pubg_api_key,
        pubg_max_match_age_hours=pubg_max_match_age_hours,
        seen_matches_path=ROOT_DIR / "data" / "seen_matches.json",
        players_path=ROOT_DIR / "players.json",
        discord_bot_token=env("DISCORD_BOT_TOKEN"),
        discord_channel_id=channel_id,
        discord_thread_id=thread_id,
        discord_guild_id=env("DISCORD_GUILD_ID"),
        local_database_path=ROOT_DIR / env("LOCAL_DATABASE_PATH", "data/imposter_finder.db"),
        pubg_poll_interval_minutes=float(env("PUBG_POLL_INTERVAL_MINUTES", "15")),
        healthcheck_imposter_finder_url=env("HEALTHCHECK_IMPOSTER_FINDER_URL"),
        nexon_api_key=env("NEXON_API_KEY"),
        nexon_daily_limit=int(env("NEXON_DAILY_LIMIT", "900")),
        gemini_api_key=env("GEMINI_API_KEY"),
        gemini_model=env("GEMINI_MODEL", "gemini-3.5-flash-lite"),
        discord_fc_thread_ids=tuple(thread for thread in (env(f"DISCORD_THREAD_FC_{bot_env.upper()}"),) if thread),
        fc_poll_interval_minutes=float(env("FC_POLL_INTERVAL_MINUTES", "15")),
    )
