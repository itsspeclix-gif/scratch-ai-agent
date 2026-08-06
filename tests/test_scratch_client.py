from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
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

    def test_profile_fetch_is_authenticated_and_cache_busted(self) -> None:
        class FakeSession:
            _headers = {"X-Token": "token"}
            _cookies = {"scratchsessionsid": "session"}

        class ProfileSource:
            username = "Bot"

        class SuccessfulResponse:
            status_code = 200
            content = b"""
                <li class="top-level-reply">
                  <div class="comment" data-comment-id="20">
                    <a id="comment-user" data-comment-user="User"></a>
                    <div class="content">Newest thread</div>
                  </div>
                  <ul>
                    <li class="reply">
                      <div class="comment" data-comment-id="21">
                        <a id="comment-user" data-comment-user="Bot"></a>
                        <div class="content">@User Existing reply</div>
                      </div>
                    </li>
                  </ul>
                </li>
            """

        self.client._session = FakeSession()
        with patch(
            "app.scratch_client.requests.get",
            return_value=SuccessfulResponse(),
        ) as get:
            roots = self.client._fresh_profile_page(ProfileSource(), 1)

        self.assertEqual([root.id for root in roots], ["20"])
        self.assertEqual([reply.id for reply in roots[0].replies()], ["21"])
        self.assertEqual(roots[0].replies()[0].parent_id, "20")
        kwargs = get.call_args.kwargs
        self.assertEqual(kwargs["params"]["page"], 1)
        self.assertIsInstance(kwargs["params"]["_"], int)
        self.assertEqual(kwargs["headers"]["Cache-Control"], "no-cache")
        self.assertEqual(kwargs["headers"]["Pragma"], "no-cache")
        self.assertEqual(kwargs["cookies"], {"scratchsessionsid": "session"})
        self.assertEqual(kwargs["timeout"], 30)

    def test_profile_reply_accepts_success_html_with_wrapped_attributes(self) -> None:
        events: list[str] = []

        class RawProfileComment:
            author_id = None
            id = 10
            parent_id = None

        class ProfileAuthor:
            id = 42

        class FakeSession:
            _headers = {"X-Token": "token"}
            _cookies = {"scratchsessionsid": "session"}

            def connect_user(self, username: str) -> ProfileAuthor:
                events.append(f"resolve:{username}")
                return ProfileAuthor()

        class SuccessfulResponse:
            status_code = 200
            text = """
                <div id="comments-55" class="comment featured"
                     data-comment-id="55">
                  <a
                    href="/users/Bot"
                    data-comment-user="Bot">
                  </a>
                </div>
            """

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
            "app.scratch_client.requests.post",
            return_value=SuccessfulResponse(),
        ) as post:
            self.client.reply(comment, "@User @user, Profile response")

        self.assertEqual(events, ["resolve:User"])
        post.assert_called_once()
        url = post.call_args.args[0]
        kwargs = post.call_args.kwargs
        self.assertEqual(
            url,
            "https://scratch.mit.edu/site-api/comments/user/Bot/add/",
        )
        self.assertEqual(kwargs["headers"]["X-Token"], "token")
        self.assertEqual(kwargs["headers"]["Referer"], "https://scratch.mit.edu/users/Bot/")
        self.assertEqual(kwargs["cookies"], {"scratchsessionsid": "session"})
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(
            json.loads(kwargs["data"]),
            {
                "commentee_id": 42,
                "content": "Profile response",
                "parent_id": "10",
            },
        )

    def test_profile_reply_prefers_raw_comment_id_over_stale_root_id(self) -> None:
        class RawProfileComment:
            author_id = 42
            id = 20
            parent_id = None

        class FakeSession:
            _headers: dict[str, str] = {}
            _cookies: dict[str, str] = {}

        class SuccessfulResponse:
            status_code = 200
            text = '<div class="comment" data-comment-id="21"></div>'

        self.client._session = FakeSession()
        self.client._profile_user_ids = {}
        comment = CommentRef(
            "20",
            "User",
            "Newest separate thread",
            None,
            RawProfileComment(),
            root_id="10",
            source="profile",
            source_id="Bot",
        )

        with patch(
            "app.scratch_client.requests.post",
            return_value=SuccessfulResponse(),
        ) as post:
            self.client.reply(comment, "Reply to the newest thread")

        payload = json.loads(post.call_args.kwargs["data"])
        self.assertEqual(payload["parent_id"], "20")

    def test_profile_reply_rejects_success_status_without_created_comment(self) -> None:
        class RawProfileComment:
            author_id = 42
            id = 10
            parent_id = None

        class FakeSession:
            _headers: dict[str, str] = {}
            _cookies: dict[str, str] = {}

        class RejectedResponse:
            status_code = 200
            text = '<script id="error-data">{"error": "isFlood"}</script>'

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
            "app.scratch_client.requests.post",
            return_value=RejectedResponse(),
        ):
            with self.assertRaisesRegex(RuntimeError, "isFlood"):
                self.client.reply(comment, "Profile response")

    def test_profile_reply_verifies_success_when_response_omits_id(self) -> None:
        class RawProfileComment:
            author_id = 42
            id = 10
            parent_id = None

        class FakeSession:
            _headers: dict[str, str] = {}
            _cookies: dict[str, str] = {}

        class EmptySuccessResponse:
            status_code = 200
            text = ""

        root = FakeComment(
            10,
            "User",
            "Hello",
            replies=[
                FakeComment(
                    11,
                    "Bot",
                    "@User Profile response",
                    parent_id=10,
                    source="profile",
                    source_id="Bot",
                )
            ],
            source="profile",
            source_id="Bot",
        )
        source = FakeUser([], {1: [root], 2: []})
        self.client._session = FakeSession()
        self.client._sources = {("profile", "bot"): source}
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
            "app.scratch_client.requests.post",
            return_value=EmptySuccessResponse(),
        ), patch.object(
            self.client,
            "_fresh_profile_page",
            side_effect=lambda source, page: source.comments(page=page),
        ):
            self.client.reply(comment, "Profile response")

    def test_profile_reply_still_rejects_unverified_success_without_id(self) -> None:
        class RawProfileComment:
            author_id = 42
            id = 10
            parent_id = None

        class FakeSession:
            _headers: dict[str, str] = {}
            _cookies: dict[str, str] = {}

        class EmptySuccessResponse:
            status_code = 200
            text = ""

        root = FakeComment(
            10,
            "User",
            "Hello",
            replies=[],
            source="profile",
            source_id="Bot",
        )
        source = FakeUser([], {1: [root], 2: []})
        self.client._session = FakeSession()
        self.client._sources = {("profile", "bot"): source}
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
            "app.scratch_client.requests.post",
            return_value=EmptySuccessResponse(),
        ), patch.object(
            self.client,
            "_fresh_profile_page",
            side_effect=lambda source, page: source.comments(page=page),
        ):
            with self.assertRaisesRegex(RuntimeError, "no created comment"):
                self.client.reply(comment, "Profile response")

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
            self.client.reply(comment, "@USER: Project response")

        no_error_handling.assert_not_called()
        self.assertEqual(raw.posted, ["Project response"])


class FakeProject:
    def __init__(
        self,
        project_id: int,
        roots: list[FakeComment],
        *,
        author_name: str = "Bot",
    ) -> None:
        self.id = project_id
        self._roots = roots
        self.author_name = author_name
        self.comment_offsets: list[int] = []
        self.posted: list[str] = []

    def comments(self, *, limit: int, offset: int) -> list[FakeComment]:
        self.comment_offsets.append(offset)
        return self._roots[offset : offset + limit]

    def comment_by_id(self, comment_id: str) -> FakeComment:
        for root in self._roots:
            if str(root.id) == str(comment_id):
                return root
            for reply in root._replies:
                if str(reply.id) == str(comment_id):
                    return reply
        raise StopIteration

    def post_comment(self, text: str) -> object:
        self.posted.append(text)
        return SimpleNamespace(id=99)


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


class FakeSession:
    _headers = {"X-Token": "token"}
    _cookies = {"scratchsessionsid": "session"}

    def __init__(
        self,
        messages: list[object] | None = None,
        users: dict[str, object] | None = None,
        projects: dict[str, object] | None = None,
    ) -> None:
        self._messages = messages or []
        self._users = {
            username.casefold(): user
            for username, user in (users or {}).items()
        }
        self._projects = {
            str(project_id): project
            for project_id, project in (projects or {}).items()
        }
        self.cleared = 0
        self.message_limits: list[int] = []

    def message_count(self) -> int:
        return len(self._messages)

    def messages(self, *, limit: int) -> list[object]:
        self.message_limits.append(limit)
        return self._messages[:limit]

    def clear_messages(self) -> None:
        self.cleared += 1

    def connect_user(self, username: str) -> object:
        return self._users[username.casefold()]

    def connect_project(self, project_id: str) -> object:
        return self._projects[str(project_id)]


class ScratchClientDiscoveryTests(unittest.TestCase):
    def test_notification_count_endpoint_sets_message_fetch_limit(self) -> None:
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
        messages = [
            SimpleNamespace(type="addcomment", comment_id="1"),
            SimpleNamespace(type="addcomment", comment_id="2"),
            SimpleNamespace(type="addcomment", comment_id="3"),
        ]
        session = FakeSession(messages)
        session._username = "Bot"  # type: ignore[attr-defined]
        client = ScratchClient.__new__(ScratchClient)
        client._settings = settings
        client._session = session

        class CountResponse:
            ok = True

            def json(self) -> dict[str, int]:
                return {"count": 2}

        def target(message: object) -> CommentRef:
            comment_id = str(getattr(message, "comment_id"))
            return CommentRef(comment_id, "Tester", "Hi", None, object())

        with (
            patch("app.scratch_client.requests.get", return_value=CountResponse()),
            patch.object(client, "_full_scan_due", return_value=False),
            patch.object(client, "_notification_target", side_effect=target),
        ):
            targets = client.conversation_targets()

        self.assertEqual(session.message_limits, [2])
        self.assertEqual({target.id for target in targets}, {"1", "2"})

    def test_notification_count_failure_still_fetches_messages(self) -> None:
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
        messages = [
            SimpleNamespace(type="addcomment", comment_id="1"),
            SimpleNamespace(type="addcomment", comment_id="2"),
        ]
        session = FakeSession(messages)
        session._username = "Bot"  # type: ignore[attr-defined]
        client = ScratchClient.__new__(ScratchClient)
        client._settings = settings
        client._session = session

        def target(message: object) -> CommentRef:
            comment_id = str(getattr(message, "comment_id"))
            return CommentRef(comment_id, "Tester", "Hi", None, object())

        with (
            patch(
                "app.scratch_client.requests.get",
                side_effect=RuntimeError("Scratch auth failed"),
            ),
            patch.object(client, "_full_scan_due", return_value=False),
            patch.object(client, "_notification_target", side_effect=target),
        ):
            targets = client.conversation_targets()

        self.assertEqual(session.message_limits, [40])
        self.assertEqual({target.id for target in targets}, {"1", "2"})
        self.assertFalse(client._notification_scan_complete)
        self.assertEqual(client._unread_message_count, 2)

    def test_full_scan_is_disabled_by_default(self) -> None:
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
            full_scan_interval_minutes=1,
        )
        client = ScratchClient.__new__(ScratchClient)
        client._settings = settings

        self.assertFalse(client._full_scan_due())

    def test_explicit_follow_action_follows_requested_author(self) -> None:
        events: list[str] = []

        class FollowableUser(FakeUser):
            username = "Other"

            def follow(self) -> None:
                events.append("follow")

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
        target = FollowableUser([], {})
        client = ScratchClient.__new__(ScratchClient)
        client._settings = settings
        client._session = FakeSession(users={"Other": target})
        client._sources = {}

        client.follow_user("Other")

        self.assertEqual(events, ["follow"])

    def test_invited_owned_project_receives_top_level_comment(self) -> None:
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
        project = FakeProject(123, [], author_name="Other")
        session = FakeSession(projects={"123": project})
        client = ScratchClient.__new__(ScratchClient)
        client._settings = settings
        client._session = session
        client._sources = {}

        created_id = client.start_project_invitation(
            "Other",
            "123",
            "@Other What are you building next?",
        )

        self.assertEqual(created_id, "99")
        self.assertEqual(project.posted, ["What are you building next?"])

    def test_project_invitation_rejects_project_owned_by_someone_else(self) -> None:
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
        project = FakeProject(123, [], author_name="ThirdParty")
        client = ScratchClient.__new__(ScratchClient)
        client._settings = settings
        client._session = FakeSession(projects={"123": project})
        client._sources = {}

        with self.assertRaisesRegex(RuntimeError, "not owned"):
            client.start_project_invitation(
                "Other",
                "123",
                "What are you building next?",
            )

        self.assertEqual(project.posted, [])

    def test_project_invitation_skips_existing_bot_thread(self) -> None:
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
        existing = FakeComment(10, "Bot", "Existing conversation", source_id="123")
        project = FakeProject(123, [existing], author_name="Other")
        client = ScratchClient.__new__(ScratchClient)
        client._settings = settings
        client._session = FakeSession(projects={"123": project})
        client._sources = {}

        created_id = client.start_project_invitation(
            "Other",
            "123",
            "What are you building next?",
        )

        self.assertIsNone(created_id)
        self.assertEqual(project.posted, [])

    def test_notification_finds_reply_on_invited_external_project(self) -> None:
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
            full_scan_interval_minutes=360,
        )
        reply = FakeComment(
            12,
            "Other",
            "@Bot I'm adding another level.",
            parent_id=10,
            source_id="123",
        )
        root = FakeComment(
            10,
            "Bot",
            "What are you building next?",
            replies=[reply],
            source_id="123",
        )
        project = FakeProject(123, [root], author_name="Other")
        message = SimpleNamespace(
            type="addcomment",
            comment_type=0,
            comment_id=12,
            comment_obj_id=123,
        )
        session = FakeSession([message], projects={"123": project})
        client = ScratchClient.__new__(ScratchClient)
        client._settings = settings
        client._session = session
        client._user = FakeUser([], {})
        client._sources = {("profile", "bot"): client._user}
        client._profile_user_ids = {}
        client._prepared_outreach = None

        with patch.object(client, "_full_scan_due", return_value=False):
            targets = client.conversation_targets()

        self.assertEqual([target.id for target in targets], ["12"])
        self.assertEqual(targets[0].source, "project")
        self.assertEqual(targets[0].source_id, "123")

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
            full_scan_enabled=True,
            full_scan_interval_minutes=1,
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
        client._session = FakeSession()
        client._user = user
        client._sources = {("profile", "bot"): user}
        client._profile_user_ids = {}
        client._prepared_outreach = None

        with (
            patch("app.scratch_client.PAGE_SIZE", 2),
            patch.object(
                client,
                "_fresh_profile_page",
                side_effect=lambda source, page: source.comments(page=page),
            ),
        ):
            targets = client.conversation_targets()

        self.assertEqual([target.id for target in targets], ["40", "30", "10"])
        self.assertEqual(user.project_offsets, [0, 2])
        self.assertEqual(user.profile_pages, [1, 2])
        self.assertIn(("project", "101"), client._sources)
        self.assertIn(("project", "102"), client._sources)
        self.assertIn(("project", "103"), client._sources)
        self.assertTrue(client.is_current_target(targets[0]))
        with patch.object(
            client,
            "_fresh_profile_page",
            side_effect=lambda source, page: source.comments(page=page),
        ):
            self.assertTrue(client.is_current_target(targets[2]))

    def test_notification_finds_reply_on_another_users_profile(self) -> None:
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
            full_scan_interval_minutes=360,
        )
        reply = FakeComment(
            12,
            "User",
            "@Bot What are you making?",
            parent_id=10,
            source="profile",
            source_id="Other",
        )
        root = FakeComment(
            10,
            "Bot",
            "What kind of Scratch projects do you enjoy?",
            replies=[reply],
            source="profile",
            source_id="Other",
        )
        external_user = FakeUser([], {1: [root], 2: []})
        external_user.username = "Other"
        message = SimpleNamespace(
            type="addcomment",
            comment_type=1,
            comment_id=12,
            comment_obj_title="Other",
        )
        session = FakeSession([message], {"Other": external_user})
        client = ScratchClient.__new__(ScratchClient)
        client._settings = settings
        client._session = session
        client._user = FakeUser([], {})
        client._sources = {("profile", "bot"): client._user}
        client._profile_user_ids = {}
        client._prepared_outreach = None

        with (
            patch.object(client, "_full_scan_due", return_value=False),
            patch.object(
                client,
                "_fresh_profile_page",
                side_effect=lambda source, page: source.comments(page=page),
            ),
        ):
            targets = client.conversation_targets()

        self.assertEqual([target.id for target in targets], ["12"])
        self.assertEqual(targets[0].source_id, "Other")
        client.finish_notification_batch(success=True)
        self.assertEqual(session.cleared, 1)

    def test_external_notification_without_bot_participation_is_ignored(self) -> None:
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
        root = FakeComment(
            12,
            "User",
            "Unrelated conversation",
            source="profile",
            source_id="Other",
        )
        external_user = FakeUser([], {1: [root], 2: []})
        external_user.username = "Other"
        message = SimpleNamespace(
            type="addcomment",
            comment_type=1,
            comment_id=12,
            comment_obj_title="Other",
        )
        session = FakeSession([message], {"Other": external_user})
        client = ScratchClient.__new__(ScratchClient)
        client._settings = settings
        client._session = session
        client._user = FakeUser([], {})
        client._sources = {("profile", "bot"): client._user}
        client._profile_user_ids = {}
        client._prepared_outreach = None

        with (
            patch.object(client, "_full_scan_due", return_value=False),
            patch.object(
                client,
                "_fresh_profile_page",
                side_effect=lambda source, page: source.comments(page=page),
            ),
        ):
            self.assertEqual(client.conversation_targets(), [])

    def test_notification_clear_waits_when_new_message_arrives(self) -> None:
        session = FakeSession([object()])
        client = ScratchClient.__new__(ScratchClient)
        client._session = session
        client._unread_message_count = 1
        client._notification_scan_complete = True
        session._messages.append(object())

        with patch.object(client, "_message_count", return_value=2):
            client.finish_notification_batch(success=True)

        self.assertEqual(session.cleared, 0)

    def test_notification_clear_failure_is_retryable(self) -> None:
        session = FakeSession([object()])

        def fail_clear() -> None:
            raise RuntimeError("Scratch returned non-JSON")

        session.clear_messages = fail_clear  # type: ignore[method-assign]
        client = ScratchClient.__new__(ScratchClient)
        client._session = session
        client._unread_message_count = 1
        client._notification_scan_complete = True

        with patch.object(client, "_message_count", return_value=1):
            client.finish_notification_batch(success=True)

        self.assertEqual(client._unread_message_count, 1)

    def test_notification_count_failure_is_retryable(self) -> None:
        session = FakeSession([object()])
        client = ScratchClient.__new__(ScratchClient)
        client._session = session
        client._unread_message_count = 1
        client._notification_scan_complete = True

        with patch.object(
            client,
            "_message_count",
            side_effect=RuntimeError("Scratch returned non-JSON"),
        ):
            client.finish_notification_batch(success=True)

        self.assertEqual(client._unread_message_count, 1)

    def test_outreach_posts_without_following(self) -> None:
        events: list[str] = []

        class OutreachUser(FakeUser):
            username = "Other"

            def follow(self) -> None:
                events.append("follow")

        class SuccessfulResponse:
            status_code = 200
            text = '<div class="comment" data-comment-id="99"></div>'

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
            outreach_users=("Other",),
            outreach_interval_minutes=10,
        )
        external_user = OutreachUser([], {1: []})
        session = FakeSession(users={"Other": external_user})
        client = ScratchClient.__new__(ScratchClient)
        client._settings = settings
        client._session = session
        client._user = FakeUser([], {})
        client._sources = {("profile", "bot"): client._user}
        client._profile_user_ids = {}
        client._prepared_outreach = None

        def post(*args: object, **kwargs: object) -> SuccessfulResponse:
            events.append("post")
            return SuccessfulResponse()

        with (
            patch("app.scratch_client.time.time", return_value=600),
            patch.object(
                client,
                "_fresh_profile_page",
                side_effect=lambda source, page: source.comments(page=page),
            ),
            patch("app.scratch_client.requests.post", side_effect=post) as request,
        ):
            self.assertEqual(client.outreach_candidate(), "Other")
            created_id = client.start_outreach(
                "Other",
                "What are you creating in Scratch?",
            )

        self.assertEqual(created_id, "99")
        self.assertEqual(events, ["post"])
        payload = json.loads(request.call_args.kwargs["data"])
        self.assertEqual(payload["parent_id"], "")
        self.assertEqual(payload["commentee_id"], "")

    def test_invited_profile_comment_posts_without_following(self) -> None:
        events: list[str] = []

        class InvitingUser(FakeUser):
            username = "Other"

            def follow(self) -> None:
                events.append("follow")

        class SuccessfulResponse:
            status_code = 200
            text = '<div class="comment" data-comment-id="101"></div>'

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
        external_user = InvitingUser([], {1: []})
        client = ScratchClient.__new__(ScratchClient)
        client._settings = settings
        client._session = FakeSession(users={"Other": external_user})
        client._user = FakeUser([], {})
        client._sources = {("profile", "bot"): client._user}
        client._profile_user_ids = {}
        client._prepared_outreach = None

        def post(*args: object, **kwargs: object) -> SuccessfulResponse:
            events.append("post")
            return SuccessfulResponse()

        with (
            patch.object(
                client,
                "_fresh_profile_page",
                side_effect=lambda source, page: source.comments(page=page),
            ),
            patch("app.scratch_client.requests.post", side_effect=post),
        ):
            created_id = client.start_profile_invitation(
                "Other",
                "Hey! What are you creating next?",
            )

        self.assertEqual(created_id, "101")
        self.assertEqual(events, ["post"])

    def test_invited_profile_comment_skips_existing_bot_thread(self) -> None:
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
        existing_root = FakeComment(
            10,
            "Bot",
            "Existing conversation",
            source="profile",
            source_id="Other",
        )
        external_user = FakeUser([], {1: [existing_root]})
        client = ScratchClient.__new__(ScratchClient)
        client._settings = settings
        client._session = FakeSession(users={"Other": external_user})
        client._user = FakeUser([], {})
        client._sources = {("profile", "bot"): client._user}
        client._profile_user_ids = {}
        client._prepared_outreach = None

        with (
            patch.object(
                client,
                "_fresh_profile_page",
                side_effect=lambda source, page: source.comments(page=page),
            ),
            patch("app.scratch_client.requests.post") as post,
        ):
            created_id = client.start_profile_invitation(
                "Other",
                "Hey! What are you creating next?",
            )

        self.assertIsNone(created_id)
        post.assert_not_called()

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

        with patch.object(
            client,
            "_fresh_profile_page",
            side_effect=lambda source, page: source.comments(page=page),
        ):
            self.assertFalse(client.is_current_target(comment))


if __name__ == "__main__":
    unittest.main()
