from __future__ import annotations

import logging

from app.config import Settings
from app.models import AgentDecision, CommentRef
from app.runner import run_once


class FakeComment:
    def __init__(self, replies: list[str] | None = None) -> None:
        self._replies = replies or []

    def replies(self) -> list[object]:
        return [type("Reply", (), {"author_name": name})() for name in self._replies]


class FakeScratch:
    def __init__(self) -> None:
        self.posted: list[tuple[str, str]] = []
        self._comments = [
            CommentRef("1", "Tester", "How did you make the movement smooth?", None, FakeComment()),
            CommentRef("2", "SomeoneElse", "Hello", None, FakeComment()),
            CommentRef("3", "Tester", "Already answered", None, FakeComment(["BotAccount"])),
        ]

    def recent_top_level_comments(self) -> list[CommentRef]:
        return self._comments

    def has_bot_reply(self, comment: CommentRef) -> bool:
        return any(reply.author_name.casefold() == "botaccount" for reply in comment.raw.replies())

    def reply(self, comment: CommentRef, text: str) -> None:
        self.posted.append((comment.id, text))
        comment.raw._replies.append("BotAccount")


class FakeAgent:
    def generate(self, comment: CommentRef) -> AgentDecision:
        return AgentDecision(True, "I used small position changes in a fast loop.", "project question")


def main() -> None:
    settings = Settings(
        scratch_username="BotAccount",
        scratch_session_string="fake-session",
        scratch_project_id="123",
        groq_api_key="fake-groq-key",
        groq_model="llama-3.1-8b-instant",
        allowed_users=frozenset({"tester"}),
        bot_mode="private",
        max_recent_comments=20,
        max_replies_per_run=2,
        max_reply_chars=300,
        persona="A transparent experimental AI Scratch creator.",
    )
    scratch = FakeScratch()
    stats = run_once(settings, scratch, FakeAgent(), logging.getLogger("dry-run"))

    assert scratch.posted == [("1", "I used small position changes in a fast loop.")]
    assert stats.posted == 1
    assert stats.skipped_not_allowed_user == 1
    assert stats.skipped_existing_reply == 1
    print("Dry run passed: allowlist, duplicate prevention, policy path, and posting path work with fake services.")


if __name__ == "__main__":
    main()
