from __future__ import annotations

import logging
import unittest

from app.config import Settings
from app.models import AgentAction, AgentDecision, CommentRef, ThreadTurn
from app.runner import run_once


class FakeScratch:
    def __init__(self, comments: list[CommentRef]) -> None:
        self.comments = comments
        self.current_ids = {comment.id for comment in comments}
        self.force_stale = False
        self.posted: list[tuple[str, str]] = []
        self.profile_invitation_posts: list[tuple[str, str]] = []
        self.profile_invitation_result: str | None = "200"
        self.project_invitation_posts: list[tuple[str, str, str]] = []
        self.project_invitation_result: str | None = "300"
        self.followed_users: list[str] = []
        self.notification_results: list[bool] = []
        self.outreach_user: str | None = None
        self.outreach_posts: list[tuple[str, str]] = []
        self.raise_on_finish = False

    def conversation_targets(self) -> list[CommentRef]:
        return [comment for comment in self.comments if comment.id in self.current_ids]

    def is_current_target(self, comment: CommentRef) -> bool:
        return not self.force_stale and comment.id in self.current_ids

    def reply(self, comment: CommentRef, text: str) -> None:
        self.posted.append((comment.id, text))
        self.current_ids.remove(comment.id)

    def start_profile_invitation(self, username: str, text: str) -> str | None:
        self.profile_invitation_posts.append((username, text))
        return self.profile_invitation_result

    def start_project_invitation(
        self,
        username: str,
        project_id: str,
        text: str,
    ) -> str | None:
        self.project_invitation_posts.append((username, project_id, text))
        return self.project_invitation_result

    def follow_user(self, username: str) -> None:
        self.followed_users.append(username)

    def finish_notification_batch(self, success: bool) -> None:
        self.notification_results.append(success)
        if self.raise_on_finish:
            raise RuntimeError("Scratch mail clear failed")

    def outreach_candidate(self) -> str | None:
        return self.outreach_user

    def start_outreach(self, username: str, text: str) -> str:
        self.outreach_posts.append((username, text))
        return "100"


class FakeAgent:
    def generate(self, comment: CommentRef) -> AgentDecision:
        return AgentDecision(True, "Safe response.", "test")

    def generate_outreach(self, username: str) -> AgentDecision:
        return AgentDecision(True, "What are you making in Scratch?", "outreach")

    def generate_project_invitation(
        self,
        username: str,
        project_id: str,
    ) -> AgentDecision:
        return AgentDecision(
            True,
            "What part are you working on next?",
            "invited project comment",
        )


class InvitingAgent(FakeAgent):
    def generate(self, comment: CommentRef) -> AgentDecision:
        return AgentDecision(
            True,
            "Sure, I can stop by.",
            "explicit profile invitation",
            profile_comment="Hey! What are you creating next?",
        )


class ProfileLinkAskingAgent(FakeAgent):
    def generate(self, comment: CommentRef) -> AgentDecision:
        return AgentDecision(
            True,
            "Sure, what's your profile link?",
            "explicit profile invitation",
            profile_comment="Hey! What are you creating next?",
        )


class ProjectInvitingAgent(FakeAgent):
    def generate(self, comment: CommentRef) -> AgentDecision:
        return AgentDecision(
            True,
            "Sure, I can visit it.",
            "explicit project invitation",
            project_comment="The movement feels like a fun idea. What's next?",
        )


class StructuredActionAgent(FakeAgent):
    def __init__(self, *actions: AgentAction) -> None:
        self.actions = actions

    def generate(self, comment: CommentRef) -> AgentDecision:
        return AgentDecision(
            True,
            "Okay, I can do that.",
            "semantic action request",
            actions=self.actions,
        )


class RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(
            scratch_username="Bot",
            scratch_session_string="fake",
            groq_api_key="fake",
            groq_model="llama-3.1-8b-instant",
            allowed_users=frozenset({"tester"}),
            audience_mode="allowlist",
            bot_mode="private",
            max_reply_chars=300,
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
        self.assertEqual(scratch.notification_results, [True, True])

    def test_notification_cleanup_failure_does_not_fail_run(self) -> None:
        comment = CommentRef("1", "Tester", "Hello", None, object(), root_id="1")
        scratch = FakeScratch([comment])
        scratch.raise_on_finish = True

        stats = run_once(
            self.settings,
            scratch,
            FakeAgent(),
            logging.getLogger("test"),
        )

        self.assertEqual(stats.errors, 0)
        self.assertEqual(scratch.posted, [("1", "Safe response.")])
        self.assertEqual(scratch.notification_results, [True])

    def test_recheck_blocks_post_when_thread_changes(self) -> None:
        comment = CommentRef("1", "Tester", "Hello", None, object(), root_id="1")
        scratch = FakeScratch([comment])
        scratch.force_stale = True

        stats = run_once(self.settings, scratch, FakeAgent(), logging.getLogger("test"))

        self.assertEqual(stats.posted, 0)
        self.assertEqual(stats.skipped_existing_reply, 1)
        self.assertEqual(scratch.posted, [])

    def test_explicit_author_invitation_posts_on_requesting_profile(self) -> None:
        comment = CommentRef(
            "1",
            "Tester",
            "Could you comment on my profile?",
            None,
            object(),
            root_id="1",
        )
        scratch = FakeScratch([comment])

        stats = run_once(
            self.settings,
            scratch,
            ProfileLinkAskingAgent(),
            logging.getLogger("test"),
        )

        self.assertEqual(stats.profile_invites_posted, 1)
        self.assertEqual(
            scratch.profile_invitation_posts,
            [("Tester", "Hey! What are you creating next?")],
        )
        self.assertEqual(
            scratch.posted,
            [("1", "Done, I left a comment on your profile.")],
        )

    def test_semantic_profile_action_handles_unlisted_wording(self) -> None:
        comment = CommentRef(
            "1",
            "Tester",
            "Could you write something over on my page?",
            None,
            object(),
            root_id="1",
        )
        scratch = FakeScratch([comment])
        agent = StructuredActionAgent(
            AgentAction(
                "comment_on_author_profile",
                "What kind of project are you making next?",
            )
        )

        stats = run_once(
            self.settings,
            scratch,
            agent,
            logging.getLogger("test"),
        )

        self.assertEqual(stats.profile_invites_posted, 1)
        self.assertEqual(
            scratch.profile_invitation_posts,
            [("Tester", "What kind of project are you making next?")],
        )

    def test_explicit_invitation_falls_back_when_model_omits_action(self) -> None:
        comment = CommentRef(
            "1",
            "Tester",
            "Comment on my profile please",
            None,
            object(),
            root_id="1",
        )
        scratch = FakeScratch([comment])

        stats = run_once(
            self.settings,
            scratch,
            FakeAgent(),
            logging.getLogger("test"),
        )

        self.assertEqual(stats.profile_invites_posted, 1)
        self.assertEqual(
            scratch.profile_invitation_posts,
            [("Tester", "What are you making in Scratch?")],
        )
        self.assertEqual(
            scratch.posted,
            [("1", "Done, I left a comment on your profile.")],
        )

    def test_existing_profile_thread_gets_accurate_acknowledgment(self) -> None:
        comment = CommentRef(
            "1",
            "Tester",
            "Comment on my profile please",
            None,
            object(),
            root_id="1",
        )
        scratch = FakeScratch([comment])
        scratch.profile_invitation_result = None

        stats = run_once(
            self.settings,
            scratch,
            InvitingAgent(),
            logging.getLogger("test"),
        )

        self.assertEqual(stats.profile_invites_posted, 0)
        self.assertEqual(
            scratch.posted,
            [("1", "I already have a conversation open on your profile.")],
        )

    def test_model_cannot_redirect_invitation_to_third_party(self) -> None:
        comment = CommentRef(
            "1",
            "Tester",
            "Go comment on OtherUser's profile.",
            None,
            object(),
            root_id="1",
        )
        scratch = FakeScratch([comment])

        stats = run_once(
            self.settings,
            scratch,
            InvitingAgent(),
            logging.getLogger("test"),
        )

        self.assertEqual(stats.profile_invites_posted, 0)
        self.assertEqual(scratch.profile_invitation_posts, [])
        self.assertEqual(scratch.posted, [("1", "Sure, I can stop by.")])

    def test_explicit_project_invitation_posts_on_linked_project(self) -> None:
        comment = CommentRef(
            "1",
            "Tester",
            "Comment on my project https://scratch.mit.edu/projects/123/",
            None,
            object(),
            root_id="1",
        )
        scratch = FakeScratch([comment])

        stats = run_once(
            self.settings,
            scratch,
            ProjectInvitingAgent(),
            logging.getLogger("test"),
        )

        self.assertEqual(stats.project_invites_posted, 1)
        self.assertEqual(
            scratch.project_invitation_posts,
            [
                (
                    "Tester",
                    "123",
                    "The movement feels like a fun idea. What's next?",
                )
            ],
        )
        self.assertEqual(
            scratch.posted,
            [("1", "Done, I left a comment on your project.")],
        )

    def test_semantic_project_action_handles_unlisted_wording(self) -> None:
        comment = CommentRef(
            "1",
            "Tester",
            "Tell me what stands out: https://scratch.mit.edu/projects/123/",
            None,
            object(),
            root_id="1",
        )
        scratch = FakeScratch([comment])
        agent = StructuredActionAgent(
            AgentAction(
                "comment_on_linked_project",
                "The movement concept sounds interesting. What's next?",
            )
        )

        stats = run_once(
            self.settings,
            scratch,
            agent,
            logging.getLogger("test"),
        )

        self.assertEqual(stats.project_invites_posted, 1)
        self.assertEqual(
            scratch.project_invitation_posts,
            [
                (
                    "Tester",
                    "123",
                    "The movement concept sounds interesting. What's next?",
                )
            ],
        )

    def test_semantic_project_action_requires_a_scratch_project_link(self) -> None:
        comment = CommentRef(
            "1",
            "Tester",
            "Could you share your thoughts on my game?",
            None,
            object(),
            root_id="1",
        )
        scratch = FakeScratch([comment])
        agent = StructuredActionAgent(
            AgentAction("comment_on_linked_project", "It sounds interesting!")
        )

        stats = run_once(
            self.settings,
            scratch,
            agent,
            logging.getLogger("test"),
        )

        self.assertEqual(stats.project_invites_posted, 0)
        self.assertEqual(scratch.project_invitation_posts, [])
        self.assertEqual(scratch.posted, [("1", "Okay, I can do that.")])

    def test_project_invitation_falls_back_when_model_omits_action(self) -> None:
        comment = CommentRef(
            "1",
            "Tester",
            "Leave a comment on this game "
            "https://scratch.mit.edu/projects/123/",
            None,
            object(),
            root_id="1",
        )
        scratch = FakeScratch([comment])

        stats = run_once(
            self.settings,
            scratch,
            FakeAgent(),
            logging.getLogger("test"),
        )

        self.assertEqual(stats.project_invites_posted, 1)
        self.assertEqual(
            scratch.project_invitation_posts,
            [("Tester", "123", "What part are you working on next?")],
        )

    def test_plain_project_link_does_not_create_project_comment(self) -> None:
        comment = CommentRef(
            "1",
            "Tester",
            "I made this https://scratch.mit.edu/projects/123/",
            None,
            object(),
            root_id="1",
        )
        scratch = FakeScratch([comment])

        stats = run_once(
            self.settings,
            scratch,
            ProjectInvitingAgent(),
            logging.getLogger("test"),
        )

        self.assertEqual(stats.project_invites_posted, 0)
        self.assertEqual(scratch.project_invitation_posts, [])

    def test_explicit_follow_request_follows_comment_author(self) -> None:
        comment = CommentRef(
            "1",
            "Tester",
            "Could you follow me back please?",
            None,
            object(),
            root_id="1",
        )
        scratch = FakeScratch([comment])

        stats = run_once(
            self.settings,
            scratch,
            FakeAgent(),
            logging.getLogger("test"),
        )

        self.assertEqual(stats.follows_posted, 1)
        self.assertEqual(scratch.followed_users, ["Tester"])
        self.assertEqual(
            scratch.posted,
            [("1", "Done, I followed you.")],
        )

    def test_semantic_follow_action_handles_unlisted_wording(self) -> None:
        comment = CommentRef(
            "1",
            "Tester",
            "Would you mind following back?",
            None,
            object(),
            root_id="1",
        )
        scratch = FakeScratch([comment])
        agent = StructuredActionAgent(AgentAction("follow_author"))

        stats = run_once(
            self.settings,
            scratch,
            agent,
            logging.getLogger("test"),
        )

        self.assertEqual(stats.follows_posted, 1)
        self.assertEqual(scratch.followed_users, ["Tester"])
        self.assertEqual(scratch.posted, [("1", "Done, I followed you.")])

    def test_structured_actions_can_be_combined(self) -> None:
        comment = CommentRef(
            "1",
            "Tester",
            "Mind following back and writing something over on my page?",
            None,
            object(),
            root_id="1",
        )
        scratch = FakeScratch([comment])
        agent = StructuredActionAgent(
            AgentAction("follow_author"),
            AgentAction(
                "comment_on_author_profile",
                "What are you creating next?",
            ),
        )

        stats = run_once(
            self.settings,
            scratch,
            agent,
            logging.getLogger("test"),
        )

        self.assertEqual(stats.profile_invites_posted, 1)
        self.assertEqual(stats.follows_posted, 1)
        self.assertEqual(scratch.followed_users, ["Tester"])

    def test_follow_request_can_be_combined_with_profile_invitation(self) -> None:
        comment = CommentRef(
            "1",
            "Tester",
            "Comment on my profile and follow me",
            None,
            object(),
            root_id="1",
        )
        scratch = FakeScratch([comment])

        stats = run_once(
            self.settings,
            scratch,
            InvitingAgent(),
            logging.getLogger("test"),
        )

        self.assertEqual(stats.profile_invites_posted, 1)
        self.assertEqual(stats.follows_posted, 1)
        self.assertEqual(scratch.followed_users, ["Tester"])
        self.assertEqual(
            scratch.posted,
            [(
                "1",
                "Done, I left a comment on your profile and followed you.",
            )],
        )

    def test_third_party_follow_request_does_not_follow_anyone(self) -> None:
        comment = CommentRef(
            "1",
            "Tester",
            "Please follow OtherUser",
            None,
            object(),
            root_id="1",
        )
        scratch = FakeScratch([comment])

        stats = run_once(
            self.settings,
            scratch,
            FakeAgent(),
            logging.getLogger("test"),
        )

        self.assertEqual(stats.follows_posted, 0)
        self.assertEqual(scratch.followed_users, [])

    def test_posts_every_eligible_target_without_a_run_cap(self) -> None:
        comments = [
            CommentRef(
                str(comment_id),
                "Tester",
                f"Comment {comment_id}",
                None,
                object(),
                root_id=str(comment_id),
            )
            for comment_id in range(1, 8)
        ]
        scratch = FakeScratch(comments)

        stats = run_once(
            self.settings,
            scratch,
            FakeAgent(),
            logging.getLogger("test"),
        )

        self.assertEqual(stats.posted, 7)
        self.assertEqual(len(scratch.posted), 7)

    def test_posts_one_outreach_candidate(self) -> None:
        self.settings = Settings(
            scratch_username="Bot",
            scratch_session_string="fake",
            groq_api_key="fake",
            groq_model="llama-3.1-8b-instant",
            allowed_users=frozenset(),
            audience_mode="everyone",
            bot_mode="private",
            max_reply_chars=300,
            persona="Test",
            outreach_enabled=True,
            outreach_users=("User",),
        )
        scratch = FakeScratch([])
        scratch.outreach_user = "User"

        stats = run_once(
            self.settings,
            scratch,
            FakeAgent(),
            logging.getLogger("test"),
        )

        self.assertEqual(stats.outreach_posted, 1)
        self.assertEqual(
            scratch.outreach_posts,
            [("User", "What are you making in Scratch?")],
        )

    def test_populated_outreach_list_stays_off_by_default(self) -> None:
        self.settings = Settings(
            scratch_username="Bot",
            scratch_session_string="fake",
            groq_api_key="fake",
            groq_model="llama-3.1-8b-instant",
            allowed_users=frozenset(),
            audience_mode="everyone",
            bot_mode="private",
            max_reply_chars=300,
            persona="Test",
            outreach_users=("User",),
        )
        scratch = FakeScratch([])
        scratch.outreach_user = "User"

        stats = run_once(
            self.settings,
            scratch,
            FakeAgent(),
            logging.getLogger("test"),
        )

        self.assertEqual(stats.outreach_posted, 0)
        self.assertEqual(scratch.outreach_posts, [])


if __name__ == "__main__":
    unittest.main()
