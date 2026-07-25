from __future__ import annotations

import logging
import unittest

from app.config import Settings
from app.models import AgentDecision, CommentRef
from app.runner import run_once


class FakeRaw:
    def __init__(self, bot_replied: bool = False) -> None:
        self.bot_replied = bot_replied


class FakeScratch:
    def __init__(self, comments: list[CommentRef]) -> None:
        self.comments = comments
        self.posted: list[str] = []

    def recent_top_level_comments(self) -> list[CommentRef]:
        return self.comments

    def has_bot_reply(self, comment: CommentRef) -> bool:
        return comment.raw.bot_replied

    def reply(self, comment: CommentRef, text: str) -> None:
        self.posted.append(text)
        comment.raw.bot_replied = True


class FakeAgent:
    def generate(self, comment: CommentRef) -> AgentDecision:
        return AgentDecision(True, "Safe response.", "test")


class RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(
            scratch_username="Bot",
            scratch_session_string="fake",
            scratch_project_id="123",
            groq_api_key="fake",
            groq_model="llama-3.1-8b-instant",
            allowed_users=frozenset({"tester"}),
            bot_mode="private",
            max_recent_comments=20,
            max_replies_per_run=2,
            max_reply_chars=300,
            persona="Test",
        )

    def test_posts_once_and_then_detects_existing_reply(self) -> None:
        comment = CommentRef("1", "Tester", "Hello", None, FakeRaw())
        scratch = FakeScratch([comment])

        first = run_once(self.settings, scratch, FakeAgent(), logging.getLogger("test"))
        second = run_once(self.settings, scratch, FakeAgent(), logging.getLogger("test"))

        self.assertEqual(first.posted, 1)
        self.assertEqual(second.posted, 0)
        self.assertEqual(second.skipped_existing_reply, 1)
        self.assertEqual(scratch.posted, ["Safe response."])


if __name__ == "__main__":
    unittest.main()
