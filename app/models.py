from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CommentRef:
    """A Scratch comment plus its underlying scratchattach object."""

    id: str
    author: str
    content: str
    parent_id: str | None
    raw: Any


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
