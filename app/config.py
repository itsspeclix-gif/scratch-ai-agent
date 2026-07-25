from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

BotMode = Literal["observe", "simulate", "private"]


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


@dataclass(frozen=True)
class Settings:
    scratch_username: str
    scratch_session_string: str
    scratch_project_id: str
    groq_api_key: str
    groq_model: str
    allowed_users: frozenset[str]
    bot_mode: BotMode
    max_recent_comments: int
    max_replies_per_run: int
    max_reply_chars: int
    persona: str

    @classmethod
    def from_env(cls) -> "Settings":
        mode_raw = os.getenv("BOT_MODE", "simulate").strip().lower()
        if mode_raw not in {"observe", "simulate", "private"}:
            raise ValueError("BOT_MODE must be observe, simulate, or private")
        mode: BotMode = mode_raw  # type: ignore[assignment]

        allowed_users = frozenset(
            username.strip().casefold()
            for username in _required("ALLOWED_USERS").split(",")
            if username.strip()
        )
        if not allowed_users:
            raise ValueError("ALLOWED_USERS must contain at least one Scratch username")

        persona_path = Path(os.getenv("PERSONA_FILE", "persona.txt"))
        try:
            persona = persona_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ValueError(f"Could not read persona file: {persona_path}") from exc
        if not persona:
            raise ValueError("The persona file must not be empty")

        groq_key = os.getenv("GROQ_API_KEY", "").strip()
        if mode != "observe" and not groq_key:
            raise ValueError("GROQ_API_KEY is required in simulate and private modes")

        return cls(
            scratch_username=_required("SCRATCH_USERNAME"),
            scratch_session_string=_required("SCRATCH_SESSION_STRING"),
            scratch_project_id=_required("SCRATCH_PROJECT_ID"),
            groq_api_key=groq_key,
            groq_model=os.getenv("GROQ_MODEL", "llama-3.1-8b-instant").strip(),
            allowed_users=allowed_users,
            bot_mode=mode,
            max_recent_comments=_positive_int("MAX_RECENT_COMMENTS", 20),
            max_replies_per_run=_positive_int("MAX_REPLIES_PER_RUN", 2),
            max_reply_chars=_positive_int("MAX_REPLY_CHARS", 300),
            persona=persona,
        )
