from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ThreadTurn:
    id: str
    author: str
    content: str


@dataclass(frozen=True)
class CommentRef:
    """The latest unanswered Scratch comment in one conversation thread."""

    id: str
    author: str
    content: str
    parent_id: str | None
    raw: Any
    root_id: str | None = None
    thread: tuple[ThreadTurn, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class AgentDecision:
    should_reply: bool
    reply: str
    reason: str


@dataclass(frozen=True)
class PolicyResult:
    allowed: bool
    reason: str


@dataclass
class RunStats:
    scanned: int = 0
    skipped_not_allowed_user: int = 0
    skipped_existing_reply: int = 0
    skipped_policy: int = 0
    skipped_by_agent: int = 0
    simulated: int = 0
    posted: int = 0
    errors: int = 0
