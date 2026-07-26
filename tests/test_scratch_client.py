from __future__ import annotations

import unittest
from contextlib import contextmanager
from unittest.mock import patch

from app.config import Settings
from app.models import CommentRef
from app.scratch_client import ScratchClient


class FakeComment:
    def __init__(
        self,
        comment_id: int,
        author: str,
        content: str,
        parent_id: int | None = None,
        replies: list["FakeComment"] | None = None,
        source: str = "project",
        source_id: str = "123",
    ) -> None:
        self.id = comment_id
        self.author_name = author
        self.content = content
        self.parent_id = parent_id
        self._replies = replies or []
        self.source = source
        self.source_id = source_id
        self.posted: list[str] = []
        self.reply_offsets: list[int] = []

    def replies(
        self,
        *,
        use_cache: bool = True,
        limit: int = 40,
        offset: int = 0,
    ) -> list["FakeComment"]:
        self.reply_offsets.append(offset)
        return list(self._replies[offset : offset + limit])

    def reply(self, text: str) -> None:
        self.posted.append(text)


class ScratchClientThreadTests(unittest.TestCase):
    def setUp(self) -> None:
        settings = Settings(
            scratch_username="Bot",
            scratch_session_string="fake",
            groq_api_key="fake",
            groq_model="llama-3.1-8b-instant",
            allowed_users=frozenset(),
            audience_mode="everyone",
            bot_mode="private",
            max_reply_chars=300,
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

    def test_full_history_is_preserved(self) -> None:
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
        self.assertEqual(
            [turn.id for turn in target.thread],
            ["1", "2", "3", "4", "5"],
        )

    def test_all_reply_pages_are_loaded(self) -> None:
        root = FakeComment(
            1,
            "User",
            "One",
            replies=[
                FakeComment(2, "Bot", "Two", parent_id=1),
                FakeComment(3, "User", "Three", parent_id=1),
                FakeComment(4, "User", "Four", parent_id=1),
            ],
        )

        with patch("app.scratch_client.PAGE_SIZE", 2):
            target = self.client._candidate_from_root(root)

        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual([turn.id for turn in target.thread], ["1", "2", "3", "4"])
        self.assertEqual(root.reply_offsets, [0, 2])

    def test_profile_reply_uses_profile_html_response_handler(self) -> None:
        events: list[str] = []

        @contextmanager
        def profile_response_handler():
            events.append("enter")
            yield
            events.append("exit")

        class RawProfileComment:
            def reply(self, text: str, *, commentee_id: int) -> None:
                events.append(f"reply:{text}:to:{commentee_id}")

        class ProfileAuthor:
            id = 42

        class FakeSession:
            def connect_user(self, username: str) -> ProfileAuthor:
                events.append(f"resolve:{username}")
                return ProfileAuthor()

        self.client._session = FakeSession()
        self.client._profile_user_ids = {}

        comment = CommentRef(
            "10",
            "User",
            "Hello",
            None,
            RawProfileComment(),
            root_id="10",
            source="profile",
            source_id="Bot",
        )

        with patch(
            "scratchattach.utils.requests.requests.no_error_handling",
            side_effect=profile_response_handler,
        ) as no_error_handling:
            self.client.reply(comment, "Profile response")

        no_error_handling.assert_called_once_with()
        self.assertEqual(
            events,
            [
                "resolve:User",
                "enter",
                "reply:Profile response:to:42",
                "exit",
            ],
        )

    def test_project_reply_keeps_standard_response_handling(self) -> None:
        raw = FakeComment(10, "User", "Hello")
        comment = CommentRef(
            "10",
            "User",
            "Hello",
            None,
            raw,
            root_id="10",
            source="project",
            source_id="123",
        )

        with patch(
            "scratchattach.utils.requests.requests.no_error_handling"
        ) as no_error_handling:
            self.client.reply(comment, "Project response")

        no_error_handling.assert_not_called()
        self.assertEqual(raw.posted, ["Project response"])


class FakeProject:
    def __init__(self, project_id: int, roots: list[FakeComment]) -> None:
        self.id = project_id
        self._roots = roots
        self.comment_offsets: list[int] = []

    def comments(self, *, limit: int, offset: int) -> list[FakeComment]:
        self.comment_offsets.append(offset)
        return self._roots[offset : offset + limit]

    def comment_by_id(self, comment_id: str) -> FakeComment:
        return next(root for root in self._roots if str(root.id) == str(comment_id))


class FakeUser:
    def __init__(
        self,
        projects: list[FakeProject],
        profile_pages: dict[int, list[FakeComment] | None],
    ) -> None:
        self._projects = projects
        self._profile_pages = profile_pages
        self.project_offsets: list[int] = []
        self.profile_pages: list[int] = []

    def projects(self, *, limit: int, offset: int) -> list[FakeProject]:
        self.project_offsets.append(offset)
        return self._projects[offset : offset + limit]

    def comments(self, *, page: int) -> list[FakeComment] | None:
        self.profile_pages.append(page)
        return self._profile_pages.get(page)

    def comment_by_id(self, comment_id: str) -> FakeComment:
        raise AssertionError("profile rechecks must not use scratchattach.comment_by_id")


class ScratchClientDiscoveryTests(unittest.TestCase):
    def test_discovers_profile_and_every_project_page(self) -> None:
        settings = Settings(
            scratch_username="Bot",
            scratch_session_string="fake",
            groq_api_key="fake",
            groq_model="llama-3.1-8b-instant",
            allowed_users=frozenset(),
            audience_mode="everyone",
            bot_mode="private",
            max_reply_chars=500,
            persona="Test",
        )
        profile_root = FakeComment(
            10,
            "User",
            "Profile hello",
            source="profile",
            source_id="Bot",
        )
        projects = [
            FakeProject(
                101,
                [FakeComment(20, "Bot", "My own root", source_id="101")],
            ),
            FakeProject(
                102,
                [FakeComment(30, "User", "Project two", source_id="102")],
            ),
            FakeProject(
                103,
                [FakeComment(40, "User", "Project three", source_id="103")],
            ),
        ]
        user = FakeUser(projects, {1: [profile_root]})
        client = ScratchClient.__new__(ScratchClient)
        client._settings = settings
        client._user = user
        client._sources = {("profile", "bot"): user}

        with patch("app.scratch_client.PAGE_SIZE", 2):
            targets = client.conversation_targets()

        self.assertEqual([target.id for target in targets], ["40", "30", "10"])
        self.assertEqual(user.project_offsets, [0, 2])
        self.assertEqual(user.profile_pages, [1, 2])
        self.assertIn(("project", "101"), client._sources)
        self.assertIn(("project", "102"), client._sources)
        self.assertIn(("project", "103"), client._sources)
        self.assertTrue(client.is_current_target(targets[0]))
        self.assertTrue(client.is_current_target(targets[2]))

    def test_missing_profile_root_is_stale_instead_of_erroring(self) -> None:
        settings = Settings(
            scratch_username="Bot",
            scratch_session_string="fake",
            groq_api_key="fake",
            groq_model="llama-3.1-8b-instant",
            allowed_users=frozenset(),
            audience_mode="everyone",
            bot_mode="private",
            max_reply_chars=500,
            persona="Test",
        )
        user = FakeUser([], {1: None})
        client = ScratchClient.__new__(ScratchClient)
        client._settings = settings
        client._user = user
        client._sources = {("profile", "bot"): user}
        comment = CommentRef(
            "11",
            "User",
            "Deleted before posting",
            None,
            FakeComment(
                11,
                "User",
                "Deleted before posting",
                source="profile",
                source_id="Bot",
            ),
            root_id="11",
            source="profile",
            source_id="Bot",
        )

        self.assertFalse(client.is_current_target(comment))


if __name__ == "__main__":
    unittest.main()
