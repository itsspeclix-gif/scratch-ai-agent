from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


AgentActionType = Literal[
    "follow_author",
    "comment_on_author_profile",
    "comment_on_linked_project",
]


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
    source: str = "project"
    source_id: str | None = None


def pending_author_messages(comment: CommentRef) -> tuple[str, ...]:
    """Return the final author's consecutive messages awaiting one bot reply."""

    if not comment.thread or comment.thread[-1].id != comment.id:
        return (comment.content,)

    author = comment.author.casefold()
    messages: list[str] = []
    for turn in reversed(comment.thread):
        if turn.author.casefold() != author:
            break
        messages.append(turn.content)

    if not messages:
        return (comment.content,)
    return tuple(reversed(messages))


@dataclass(frozen=True)
class AgentAction:
    """A model-requested action whose destination is resolved by trusted code."""

    type: AgentActionType
    content: str = ""
    evidence: str = ""


@dataclass(frozen=True)
class AgentDecision:
    should_reply: bool
    reply: str
    reason: str
    # Retained for compatibility with older integrations. New model responses use
    # the structured actions collection below.
    profile_comment: str = ""
    project_comment: str = ""
    actions: tuple[AgentAction, ...] = field(default_factory=tuple)


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
    profile_invites_simulated: int = 0
    profile_invites_posted: int = 0
    project_invites_simulated: int = 0
    project_invites_posted: int = 0
    follows_simulated: int = 0
    follows_posted: int = 0
    outreach_simulated: int = 0
    outreach_posted: int = 0
    errors: int = 0
