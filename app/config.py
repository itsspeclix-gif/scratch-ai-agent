from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

BotMode = Literal["observe", "simulate", "private"]
AudienceMode = Literal["allowlist", "everyone"]
AIProvider = Literal["groq", "mistral"]
SCRATCH_USERNAME_RE = re.compile(r"[A-Za-z0-9_-]{3,20}")


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


def _boolean(name: str, default: bool) -> bool:
    raw = os.getenv(name, str(default).lower()).strip().lower()
    if raw in {"true", "1", "yes", "on"}:
        return True
    if raw in {"false", "0", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def _outreach_users(path: Path) -> tuple[str, ...]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"Could not read outreach user file: {path}") from exc

    users: list[str] = []
    seen: set[str] = set()
    for line_number, raw_line in enumerate(lines, start=1):
        username = raw_line.strip()
        if not username or username.startswith("#"):
            continue
        if not SCRATCH_USERNAME_RE.fullmatch(username):
            raise ValueError(
                f"Invalid Scratch username on line {line_number} of {path}: "
                f"{username!r}"
            )
        key = username.casefold()
        if key not in seen:
            seen.add(key)
            users.append(username)
    return tuple(users)


@dataclass(frozen=True)
class Settings:
    scratch_username: str
    scratch_session_string: str
    groq_api_key: str
    groq_model: str
    allowed_users: frozenset[str]
    audience_mode: AudienceMode
    bot_mode: BotMode
    max_reply_chars: int
    persona: str
    ai_provider: AIProvider = "groq"
    mistral_api_key: str = ""
    mistral_model: str = "mistral-medium-3-5"
    mistral_agent_id: str = ""
    outreach_enabled: bool = False
    outreach_users: tuple[str, ...] = ()
    outreach_interval_minutes: int = 480
    full_scan_enabled: bool = False
    full_scan_interval_minutes: int = 360

    @classmethod
    def from_env(cls) -> "Settings":
        mode_raw = os.getenv("BOT_MODE", "simulate").strip().lower()
        if mode_raw not in {"observe", "simulate", "private"}:
            raise ValueError("BOT_MODE must be observe, simulate, or private")
        mode: BotMode = mode_raw  # type: ignore[assignment]

        audience_raw = os.getenv("AUDIENCE_MODE", "allowlist").strip().lower()
        if audience_raw not in {"allowlist", "everyone"}:
            raise ValueError("AUDIENCE_MODE must be allowlist or everyone")
        audience_mode: AudienceMode = audience_raw  # type: ignore[assignment]

        provider_raw = os.getenv("AI_PROVIDER", "groq").strip().lower()
        if provider_raw not in {"groq", "mistral"}:
            raise ValueError("AI_PROVIDER must be groq or mistral")
        ai_provider: AIProvider = provider_raw  # type: ignore[assignment]

        allowed_users = frozenset(
            username.strip().casefold()
            for username in os.getenv("ALLOWED_USERS", "").split(",")
            if username.strip()
        )
        if audience_mode == "allowlist" and not allowed_users:
            raise ValueError(
                "ALLOWED_USERS must contain at least one Scratch username "
                "when AUDIENCE_MODE=allowlist"
            )

        persona_path = Path(os.getenv("PERSONA_FILE", "persona.txt"))
        try:
            persona = persona_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ValueError(f"Could not read persona file: {persona_path}") from exc
        if not persona:
            raise ValueError("The persona file must not be empty")

        outreach_path = Path(
            os.getenv("OUTREACH_USERS_FILE", "config/outreach_users.txt")
        )
        outreach_users = _outreach_users(outreach_path)

        groq_key = os.getenv("GROQ_API_KEY", "").strip()
        mistral_key = os.getenv("MISTRAL_API_KEY", "").strip()
        if mode != "observe":
            if ai_provider == "groq" and not groq_key:
                raise ValueError(
                    "GROQ_API_KEY is required when AI_PROVIDER=groq"
                )
            if ai_provider == "mistral" and not mistral_key:
                raise ValueError(
                    "MISTRAL_API_KEY is required when AI_PROVIDER=mistral"
                )

        return cls(
            scratch_username=_required("SCRATCH_USERNAME"),
            scratch_session_string=_required("SCRATCH_SESSION_STRING"),
            groq_api_key=groq_key,
            groq_model=os.getenv("GROQ_MODEL", "llama-3.1-8b-instant").strip(),
            allowed_users=allowed_users,
            audience_mode=audience_mode,
            bot_mode=mode,
            max_reply_chars=_positive_int("MAX_REPLY_CHARS", 500),
            persona=persona,
            ai_provider=ai_provider,
            mistral_api_key=mistral_key,
            mistral_model=os.getenv(
                "MISTRAL_MODEL",
                "mistral-medium-3-5",
            ).strip(),
            mistral_agent_id=os.getenv("MISTRAL_AGENT_ID", "").strip(),
            outreach_enabled=_boolean("OUTREACH_ENABLED", False),
            outreach_users=outreach_users,
            outreach_interval_minutes=_positive_int(
                "OUTREACH_INTERVAL_MINUTES",
                480,
            ),
            full_scan_enabled=_boolean("FULL_SCAN_ENABLED", False),
            full_scan_interval_minutes=_positive_int(
                "FULL_SCAN_INTERVAL_MINUTES",
                360,
            ),
        )
