from __future__ import annotations

import logging
import unittest

from app.config import Settings
from app.models import AgentDecision, CommentRef, ThreadTurn
from app.runner import run_once


class FakeScratch:
    def __init__(self, comments: list[CommentRef]) -> None:
        self.comments = comments
        self.current_ids = {comment.id for comment in comments}
        self.force_stale = False
        self.posted: list[tuple[str, str]] = []

    def recent_conversation_targets(self) -> list[CommentRef]:
        return [comment for comment in self.comments if comment.id in self.current_ids]

    def is_current_target(self, comment: CommentRef) -> bool:
        return not self.force_stale and comment.id in self.current_ids

    def reply(self, comment: CommentRef, text: str) -> None:
        self.posted.append((comment.id, text))
        self.current_ids.remove(comment.id)


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
            audience_mode="allowlist",
            bot_mode="private",
            max_recent_comments=20,
            max_replies_per_run=2,
            max_reply_chars=300,
            max_thread_messages=8,
            persona="Test",
        )

    def test_posts_latest_followup_once(self) -> None:
        comment = CommentRef(
            "3",
            "Tester",
            "What about the second level?",
            "1",
            object(),
            root_id="1",
            thread=(
                ThreadTurn("1", "Tester", "How did you make it?"),
                ThreadTurn("2", "Bot", "I used clones."),
                ThreadTurn("3", "Tester", "What about the second level?"),
            ),
        )
        scratch = FakeScratch([comment])

        first = run_once(self.settings, scratch, FakeAgent(), logging.getLogger("test"))
        second = run_once(self.settings, scratch, FakeAgent(), logging.getLogger("test"))

        self.assertEqual(first.posted, 1)
        self.assertEqual(second.posted, 0)
        self.assertEqual(scratch.posted, [("3", "Safe response.")])

    def test_recheck_blocks_post_when_thread_changes(self) -> None:
        comment = CommentRef("1", "Tester", "Hello", None, object(), root_id="1")
        scratch = FakeScratch([comment])
        scratch.force_stale = True

        stats = run_once(self.settings, scratch, FakeAgent(), logging.getLogger("test"))

        self.assertEqual(stats.posted, 0)
        self.assertEqual(stats.skipped_existing_reply, 1)
        self.assertEqual(scratch.posted, [])


if __name__ == "__main__":
    unittest.main()
