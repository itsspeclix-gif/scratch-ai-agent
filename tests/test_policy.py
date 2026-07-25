from __future__ import annotations

import unittest

from app.models import CommentRef
from app.policy import check_incoming, check_reply


class PolicyTests(unittest.TestCase):
    def test_allowlisted_top_level_comment_is_allowed(self) -> None:
        comment = CommentRef("1", "Tester", "How does this work?", None, object())
        self.assertTrue(check_incoming(comment, frozenset({"tester"})).allowed)

    def test_non_allowlisted_comment_is_rejected(self) -> None:
        comment = CommentRef("1", "Other", "Hello", None, object())
        self.assertFalse(check_incoming(comment, frozenset({"tester"})).allowed)

    def test_external_link_is_rejected(self) -> None:
        self.assertFalse(check_reply("See https://example.com", 300).allowed)

    def test_personal_question_is_rejected(self) -> None:
        self.assertFalse(check_reply("What school do you go to?", 300).allowed)

    def test_safe_reply_is_allowed(self) -> None:
        self.assertTrue(check_reply("I used clones and a short loop.", 300).allowed)


if __name__ == "__main__":
    unittest.main()
