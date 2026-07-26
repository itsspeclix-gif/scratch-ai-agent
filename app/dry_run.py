from __future__ import annotations

import logging

from app.config import Settings
from app.models import AgentDecision, CommentRef, ThreadTurn
from app.runner import run_once


class FakeScratch:
    def __init__(self) -> None:
        self.posted: list[tuple[str, str]] = []
        self._active = {"3"}
        self._comments = [
            CommentRef(
                "3",
                "Tester",
                "Does the same method work for level two?",
                "1",
                object(),
                root_id="1",
                thread=(
                    ThreadTurn("1", "Tester", "How did you make the movement smooth?"),
                    ThreadTurn("2", "BotAccount", "I used small position changes in a loop."),
                    ThreadTurn("3", "Tester", "Does the same method work for level two?"),
                ),
            ),
            CommentRef("4", "SomeoneElse", "Hello", None, object(), root_id="4"),
        ]

    def conversation_targets(self) -> list[CommentRef]:
        return [comment for comment in self._comments if comment.id in self._active or comment.id == "4"]

    def is_current_target(self, comment: CommentRef) -> bool:
        return comment.id in self._active

    def reply(self, comment: CommentRef, text: str) -> None:
        self.posted.append((comment.id, text))
        self._active.remove(comment.id)


class FakeAgent:
    def generate(self, comment: CommentRef) -> AgentDecision:
        return AgentDecision(True, "Yes. I reuse the same clone movement and change the level data.", "follow-up")


def main() -> None:
    settings = Settings(
        scratch_username="BotAccount",
        scratch_session_string="fake-session",
        groq_api_key="fake-groq-key",
        groq_model="llama-3.1-8b-instant",
        allowed_users=frozenset({"tester"}),
        audience_mode="allowlist",
        bot_mode="private",
        max_reply_chars=300,
        persona="A transparent experimental AI Scratch creator.",
    )
    scratch = FakeScratch()
    stats = run_once(settings, scratch, FakeAgent(), logging.getLogger("dry-run"))

    assert scratch.posted == [
        ("3", "Yes. I reuse the same clone movement and change the level data.")
    ]
    assert stats.posted == 1
    assert stats.skipped_not_allowed_user == 1
    print("Dry run passed: threaded follow-up, allowlist, policy, and posting paths work with fake services.")


if __name__ == "__main__":
    main()
