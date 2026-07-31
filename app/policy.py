from __future__ import annotations

import re

from app.models import CommentRef, PolicyResult

URL_RE = re.compile(r"(?:https?://|www\.)", re.IGNORECASE)
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
PHONE_RE = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")

PERSONAL_QUESTION_RE = re.compile(
    r"\b(?:how old are you|what(?:'s| is) your age|where do you live|"
    r"what school|which school|what(?:'s| is) your real name|full name|"
    r"phone number|email address|discord|snapchat|instagram|meet me)\b",
    re.IGNORECASE,
)

SEVERE_CONTENT_RE = re.compile(
    r"\b(?:kill yourself|suicide instructions|sexual content|send nudes|nazi propaganda)\b",
    re.IGNORECASE,
)

PROFILE_INVITE_REQUEST_RE = re.compile(
    r"""
    ^\s*
    (?:(?:hey|hi|yo)[,!]?\s+)?
    (?:(?:please|pls)\s+)?
    (?:(?:can|could|would|will)\s+(?:you|u)\s+)?
    (?:go\s+)?
    (?:
        comment(?:\s+(?:on|at))?
        | post(?:\s+(?:on|to))?
        | (?:come\s+)?leave\s+(?:(?:me\s+)?a\s+)?comment(?:\s+(?:on|at))?
        | visit
        | come\s+to
        | stop\s+by
        | drop\s+by
        | check\s+out
        | say\s+(?:hi|hello)\s+(?:on|at)
    )
    \s+
    (?:my\s+)?
    (?:scratch\s+)?
    (?:profile|page|pf)
    \b
    """,
    re.IGNORECASE | re.VERBOSE,
)
NEGATED_PROFILE_INVITE_RE = re.compile(
    r"\b(?:do not|don't|dont|never|stop|not)\b.{0,35}"
    r"\b(?:comment|post|leave|visit|come|stop by|drop by|"
    r"say (?:hi|hello)|check out)\b",
    re.IGNORECASE,
)
SCRATCH_PROJECT_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?scratch\.mit\.edu/projects/(\d+)(?:[/?#]|$)",
    re.IGNORECASE,
)
PROJECT_INVITE_REQUEST_RE = re.compile(
    r"""
    \b
    (?:
        comment(?:\s+(?:on|at))?
        | post(?:\s+(?:on|to))?
        | leave\s+(?:(?:me\s+)?a\s+)?comment(?:\s+(?:on|at))?
        | visit
        | check\s+out
        | look\s+at
        | review
    )
    \b
    .{0,100}
    (?:
        \b(?:my|this)\s+(?:scratch\s+)?(?:project|game)\b
        | scratch\.mit\.edu/projects/\d+
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)
NEGATED_PROJECT_INVITE_RE = re.compile(
    r"\b(?:do not|don't|dont|never|stop|not)\b.{0,40}"
    r"\b(?:comment|post|leave|visit|check out|look at|review)\b",
    re.IGNORECASE,
)
FOLLOW_REQUEST_RE = re.compile(
    r"""
    (?:^|[.!?]\s*|\band\s+)
    (?:(?:hey|hi|yo)[,!]?\s+)?
    (?:(?:please|pls)\s+)?
    (?:(?:can|could|would|will)\s+(?:you|u)\s+)?
    (?:(?:please|pls)\s+)?
    (?:
        follow\s+(?:me(?:\s+back)?|my\s+(?:account|profile)|back)
        | give\s+me\s+(?:a\s+)?follow
    )
    \b
    """,
    re.IGNORECASE | re.VERBOSE,
)
NEGATED_FOLLOW_REQUEST_RE = re.compile(
    r"\b(?:do not|don't|dont|never|stop|not)\b.{0,40}\bfollow\b",
    re.IGNORECASE,
)


def is_explicit_profile_invitation(text: str) -> bool:
    return bool(
        PROFILE_INVITE_REQUEST_RE.search(text)
        and not NEGATED_PROFILE_INVITE_RE.search(text)
    )


def scratch_project_id(text: str) -> str | None:
    match = SCRATCH_PROJECT_URL_RE.search(text)
    return match.group(1) if match else None


def is_explicit_project_invitation(text: str) -> bool:
    return bool(
        scratch_project_id(text)
        and PROJECT_INVITE_REQUEST_RE.search(text)
        and not NEGATED_PROJECT_INVITE_RE.search(text)
    )


def is_explicit_follow_request(text: str) -> bool:
    return bool(
        FOLLOW_REQUEST_RE.search(text)
        and not NEGATED_FOLLOW_REQUEST_RE.search(text)
    )


def check_incoming(
    comment: CommentRef,
    audience_mode: str,
    allowed_users: frozenset[str],
) -> PolicyResult:
    if audience_mode == "allowlist" and comment.author.casefold() not in allowed_users:
        return PolicyResult(False, "author is not allowlisted")
    if not comment.content.strip():
        return PolicyResult(False, "comment is empty")
    if EMAIL_RE.search(comment.content) or PHONE_RE.search(comment.content):
        return PolicyResult(False, "comment contains contact information")
    return PolicyResult(True, "incoming comment accepted")


def check_reply(text: str, max_chars: int) -> PolicyResult:
    stripped = text.strip()
    if not stripped:
        return PolicyResult(False, "reply is empty")
    if len(stripped) > max_chars:
        return PolicyResult(False, f"reply exceeds {max_chars} characters")
    if URL_RE.search(stripped):
        return PolicyResult(False, "reply contains an external link")
    if EMAIL_RE.search(stripped) or PHONE_RE.search(stripped):
        return PolicyResult(False, "reply contains contact information")
    if PERSONAL_QUESTION_RE.search(stripped):
        return PolicyResult(False, "reply requests personal or off-platform information")
    if SEVERE_CONTENT_RE.search(stripped):
        return PolicyResult(False, "reply contains severe prohibited content")
    if "<" in stripped or ">" in stripped:
        return PolicyResult(False, "reply contains markup-like characters")
    return PolicyResult(True, "reply accepted")
