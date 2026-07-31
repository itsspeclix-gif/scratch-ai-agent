from __future__ import annotations

import unittest

from app.models import CommentRef
from app.policy import (
    check_incoming,
    check_reply,
    is_explicit_follow_request,
    is_explicit_profile_invitation,
    is_explicit_project_invitation,
    scratch_project_id,
)


class PolicyTests(unittest.TestCase):
    def test_allowlisted_reply_comment_is_allowed(self) -> None:
        comment = CommentRef("2", "Tester", "What about clones?", "1", object())
        self.assertTrue(check_incoming(comment, "allowlist", frozenset({"tester"})).allowed)

    def test_non_allowlisted_comment_is_rejected(self) -> None:
        comment = CommentRef("1", "Other", "Hello", None, object())
        self.assertFalse(check_incoming(comment, "allowlist", frozenset({"tester"})).allowed)

    def test_external_link_is_rejected(self) -> None:
        self.assertFalse(check_reply("See https://example.com", 300).allowed)

    def test_personal_question_is_rejected(self) -> None:
        self.assertFalse(check_reply("What school do you go to?", 300).allowed)

    def test_safe_reply_is_allowed(self) -> None:
        self.assertTrue(check_reply("I used clones and a short loop.", 300).allowed)

    def test_everyone_mode_accepts_non_allowlisted_user(self) -> None:
        comment = CommentRef("1", "Anyone", "Hello", None, object())
        result = check_incoming(comment, "everyone", frozenset())
        self.assertTrue(result.allowed)

    def test_explicit_self_profile_invitation_is_detected(self) -> None:
        variations = (
            "Could you come leave a comment on my Scratch profile?",
            "comment on my profile please",
            "comment profile",
            "can u comment profile pls",
            "please visit my pf",
            "stop by my page",
            "say hi on my profile",
        )
        for text in variations:
            with self.subTest(text=text):
                self.assertTrue(
                    is_explicit_profile_invitation(text)
                )

    def test_profile_description_is_not_mistaken_for_invitation(self) -> None:
        descriptions = (
            "I changed my profile comment yesterday",
            "How do I comment on a profile?",
            "Their profile has comments disabled",
        )
        for text in descriptions:
            with self.subTest(text=text):
                self.assertFalse(
                    is_explicit_profile_invitation(text)
                )

    def test_third_party_profile_request_is_not_an_invitation(self) -> None:
        self.assertFalse(
            is_explicit_profile_invitation("Go comment on OtherUser's profile")
        )

    def test_negated_profile_invitation_is_not_detected(self) -> None:
        self.assertFalse(
            is_explicit_profile_invitation("Please don't comment on my profile")
        )

    def test_explicit_linked_project_invitation_is_detected(self) -> None:
        variations = (
            "Comment on my project https://scratch.mit.edu/projects/123/",
            "please leave a comment on this Scratch game: "
            "https://scratch.mit.edu/projects/456",
            "check out scratch.mit.edu/projects/789/ and comment",
        )
        for text in variations:
            with self.subTest(text=text):
                self.assertTrue(is_explicit_project_invitation(text))

    def test_project_link_without_invitation_is_not_detected(self) -> None:
        self.assertFalse(
            is_explicit_project_invitation(
                "I made this https://scratch.mit.edu/projects/123/"
            )
        )

    def test_negated_project_invitation_is_not_detected(self) -> None:
        self.assertFalse(
            is_explicit_project_invitation(
                "Don't comment on my project "
                "https://scratch.mit.edu/projects/123/"
            )
        )

    def test_project_id_is_extracted_from_scratch_url(self) -> None:
        self.assertEqual(
            scratch_project_id(
                "See https://scratch.mit.edu/projects/123456/editor/"
            ),
            "123456",
        )

    def test_explicit_self_follow_request_is_detected(self) -> None:
        variations = (
            "follow me",
            "Could you follow me back please?",
            "pls follow my account",
            "give me a follow",
            "Comment on my profile and follow me",
        )
        for text in variations:
            with self.subTest(text=text):
                self.assertTrue(is_explicit_follow_request(text))

    def test_third_party_follow_request_is_not_detected(self) -> None:
        self.assertFalse(is_explicit_follow_request("Please follow OtherUser"))

    def test_follow_discussion_is_not_detected_as_request(self) -> None:
        variations = (
            "I follow you already",
            "How do I follow somebody?",
            "My followers like this project",
            "Why did you follow me?",
        )
        for text in variations:
            with self.subTest(text=text):
                self.assertFalse(is_explicit_follow_request(text))

    def test_negated_follow_request_is_not_detected(self) -> None:
        self.assertFalse(is_explicit_follow_request("Please don't follow me"))


if __name__ == "__main__":
    unittest.main()
