"""Utilitários de data/hora (sempre UTC, ISO-8601)."""

from datetime import datetime, timezone


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def format_seconds(seconds: float | None) -> str | None:
    """Converte segundos em "HH:MM:SS" para exibição."""
    if seconds is None:
        return None
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"
