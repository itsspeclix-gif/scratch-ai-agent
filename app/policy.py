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


def check_incoming(comment: CommentRef, allowed_users: frozenset[str]) -> PolicyResult:
    if comment.author.casefold() not in allowed_users:
        return PolicyResult(False, "author is not allowlisted")
    if comment.parent_id is not None:
        return PolicyResult(False, "only top-level comments are handled in version 1")
    if not comment.content.strip():
        return PolicyResult(False, "comment is empty")
    if len(comment.content) > 1000:
        return PolicyResult(False, "comment is unusually long")
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
