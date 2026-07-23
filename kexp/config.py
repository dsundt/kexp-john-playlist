"""Typed configuration loaded from an environment mapping."""
from dataclasses import dataclass


def _flag(env: dict, key: str, default: bool) -> bool:
    if key in env:
        return env.get(key) == "1"
    return default


@dataclass(frozen=True)
class Config:
    client_id: str
    client_secret: str
    refresh_token: str
    playlist_id: str

    do_spotify_adds: bool
    do_reorder: bool
    do_dedupe: bool
    do_email: bool

    feed_max_items: int

    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    mail_from: str
    mail_to: str
    mail_subject_prefix: str

    @staticmethod
    def from_env(env: dict) -> "Config":
        return Config(
            client_id=env.get("SPOTIFY_CLIENT_ID", "") or "",
            client_secret=env.get("SPOTIFY_CLIENT_SECRET", "") or "",
            refresh_token=env.get("SPOTIFY_REFRESH_TOKEN", "") or "",
            playlist_id=env.get("SPOTIFY_PLAYLIST_ID", "") or "",
            do_spotify_adds=_flag(env, "DO_SPOTIFY_ADDS", True),
            do_reorder=_flag(env, "DO_REORDER", True),
            do_dedupe=_flag(env, "DO_DEDUPE", True),
            do_email=_flag(env, "DO_EMAIL", True),
            feed_max_items=int(env.get("FEED_MAX_ITEMS", "200") or "200"),
            smtp_host=env.get("SMTP_HOST", "") or "",
            smtp_port=int(env.get("SMTP_PORT", "587") or "587"),
            smtp_username=env.get("SMTP_USERNAME", "") or "",
            smtp_password=env.get("SMTP_PASSWORD", "") or "",
            mail_from=env.get("MAIL_FROM", "") or "",
            mail_to=env.get("MAIL_TO", "") or "",
            mail_subject_prefix=env.get("MAIL_SUBJECT_PREFIX", "KEXP — John") or "KEXP — John",
        )

    def missing(self) -> list:
        required = {
            "SPOTIFY_CLIENT_ID": self.client_id,
            "SPOTIFY_CLIENT_SECRET": self.client_secret,
            "SPOTIFY_REFRESH_TOKEN": self.refresh_token,
            "SPOTIFY_PLAYLIST_ID": self.playlist_id,
        }
        return [name for name, value in required.items() if not value]
