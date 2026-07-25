from __future__ import annotations

import unittest

from app.config import Settings
from app.scratch_client import ScratchClient


class FakeComment:
    def __init__(
        self,
        comment_id: int,
        author: str,
        content: str,
        parent_id: int | None = None,
        replies: list["FakeComment"] | None = None,
    ) -> None:
        self.id = comment_id
        self.author_name = author
        self.content = content
        self.parent_id = parent_id
        self._replies = replies or []
        self.posted: list[str] = []

    def replies(self) -> list["FakeComment"]:
        return list(self._replies)

    def reply(self, text: str) -> None:
        self.posted.append(text)


class ScratchClientThreadTests(unittest.TestCase):
    def setUp(self) -> None:
        settings = Settings(
            scratch_username="Bot",
            scratch_session_string="fake",
            scratch_project_id="123",
            groq_api_key="fake",
            groq_model="llama-3.1-8b-instant",
            allowed_users=frozenset(),
            audience_mode="everyone",
            bot_mode="private",
            max_recent_comments=20,
            max_replies_per_run=2,
            max_reply_chars=300,
            max_thread_messages=3,
            persona="Test",
        )
        self.client = ScratchClient.__new__(ScratchClient)
        self.client._settings = settings

    def test_followup_after_bot_becomes_new_target(self) -> None:
        followup = FakeComment(3, "User", "What about level two?", parent_id=1)
        root = FakeComment(
            1,
            "User",
            "How did you make it?",
            replies=[FakeComment(2, "Bot", "I used clones.", parent_id=1), followup],
        )

        target = self.client._candidate_from_root(root)

        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual(target.id, "3")
        self.assertEqual(target.parent_id, "1")
        self.assertEqual([turn.id for turn in target.thread], ["1", "2", "3"])

    def test_bot_reply_after_latest_user_means_no_target(self) -> None:
        root = FakeComment(
            1,
            "User",
            "How did you make it?",
            replies=[FakeComment(2, "Bot", "I used clones.", parent_id=1)],
        )

        self.assertIsNone(self.client._candidate_from_root(root))

    def test_history_is_trimmed_to_configured_length(self) -> None:
        root = FakeComment(
            1,
            "User",
            "One",
            replies=[
                FakeComment(2, "Bot", "Two", parent_id=1),
                FakeComment(3, "User", "Three", parent_id=1),
                FakeComment(4, "Bot", "Four", parent_id=1),
                FakeComment(5, "User", "Five", parent_id=1),
            ],
        )

        target = self.client._candidate_from_root(root)

        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual([turn.id for turn in target.thread], ["3", "4", "5"])


if __name__ == "__main__":
    unittest.main()
