from __future__ import annotations

import json
import unittest

from app.config import Settings
from app.groq_agent import (
    GROQ_CHAT_URL,
    MISTRAL_CHAT_URL,
    MISTRAL_CONVERSATIONS_URL,
    ChatAgent,
    GroqAgent,
)
from app.link_context import LinkPreview
from app.models import CommentRef, ThreadTurn


class FakeResponse:
    def __init__(
        self,
        content: dict[str, str] | None = None,
        body: dict | None = None,
    ) -> None:
        self.content = content or {
            "reply": "The second level uses the same clone system.",
            "reason": "follow-up project question",
        }
        self.body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        if self.body is not None:
            return self.body
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(self.content)
                    }
                }
            ]
        }


class FakeHTTPSession:
    def __init__(
        self,
        content: dict[str, str] | None = None,
        body: dict | None = None,
    ) -> None:
        self.url = ""
        self.headers: dict[str, str] = {}
        self.payload: dict = {}
        self.timeout = 0
        self.content = content
        self.body = body

    def post(self, url: str, *, headers: dict, json: dict, timeout: int) -> FakeResponse:
        self.url = url
        self.headers = headers
        self.payload = json
        self.timeout = timeout
        return FakeResponse(self.content, self.body)


class FakeLinkInspector:
    def __init__(self, preview: LinkPreview | None = None) -> None:
        self.preview = preview
        self.inputs: list[str] = []

    def inspect_text(self, text: str) -> LinkPreview | None:
        self.inputs.append(text)
        return self.preview


class GroqAgentTests(unittest.TestCase):
    def test_request_includes_thread_history(self) -> None:
        settings = Settings(
            scratch_username="Bot",
            scratch_session_string="fake",
            groq_api_key="fake-key",
            groq_model="qwen/qwen3.6-27b",
            allowed_users=frozenset({"tester"}),
            audience_mode="allowlist",
            bot_mode="simulate",
            max_reply_chars=300,
            persona="A test persona.",
        )
        http = FakeHTTPSession()
        decision = GroqAgent(settings, http=http).generate(
            CommentRef(
                "3",
                "Tester",
                "What about level two?",
                "1",
                object(),
                root_id="1",
                thread=(
                    ThreadTurn("1", "Tester", "How did you make it?"),
                    ThreadTurn("2", "Bot", "I used clones."),
                    ThreadTurn("3", "Tester", "What about level two?"),
                ),
            )
        )

        self.assertEqual(http.url, GROQ_CHAT_URL)
        self.assertEqual(http.headers["Authorization"], "Bearer fake-key")
        self.assertEqual(http.headers["Content-Type"], "application/json")
        self.assertEqual(http.timeout, 30)
        self.assertEqual(http.payload["response_format"], {"type": "json_object"})
        self.assertEqual(http.payload["reasoning_effort"], "none")
        system_prompt = http.payload["messages"][0]["content"]
        self.assertIn("Always respond to every supplied message", system_prompt)
        self.assertIn("off-topic messages", system_prompt)
        self.assertIn("Do not begin the reply with @username", system_prompt)
        self.assertIn("profile_comment", system_prompt)
        self.assertIn("project_comment", system_prompt)
        self.assertNotIn("should_reply", system_prompt)
        transcript = http.payload["messages"][1]["content"]
        self.assertIn("How did you make it?", transcript)
        self.assertIn("I used clones.", transcript)
        self.assertIn("What about level two?", transcript)
        self.assertTrue(decision.should_reply)

    def test_user_link_preview_is_supplied_as_untrusted_context(self) -> None:
        settings = Settings(
            scratch_username="Bot",
            scratch_session_string="fake",
            groq_api_key="fake-key",
            groq_model="qwen/qwen3.6-27b",
            allowed_users=frozenset(),
            audience_mode="everyone",
            bot_mode="simulate",
            max_reply_chars=300,
            persona="A test persona.",
        )
        http = FakeHTTPSession()
        inspector = FakeLinkInspector(
            LinkPreview(
                "https://example.com/game",
                "Clone Game",
                "A platformer made with clones.",
            )
        )

        ChatAgent(settings, http=http, link_inspector=inspector).generate(
            CommentRef(
                "1",
                "Tester",
                "What do you think? https://example.com/game",
                None,
                object(),
            )
        )

        supplied = http.payload["messages"][1]["content"]
        self.assertIn("optional linked-page text", supplied)
        self.assertIn("Clone Game", supplied)
        self.assertIn("A platformer made with clones.", supplied)
        self.assertEqual(
            inspector.inputs,
            ["What do you think? https://example.com/game"],
        )

    def test_profile_invitation_action_is_parsed(self) -> None:
        settings = Settings(
            scratch_username="Bot",
            scratch_session_string="fake",
            groq_api_key="fake-key",
            groq_model="qwen/qwen3.6-27b",
            allowed_users=frozenset(),
            audience_mode="everyone",
            bot_mode="simulate",
            max_reply_chars=300,
            persona="A test persona.",
        )
        http = FakeHTTPSession(
            {
                "reply": "Sure, I can stop by.",
                "reason": "explicit profile invitation",
                "profile_comment": "Hey! What are you creating next?",
            }
        )

        decision = ChatAgent(
            settings,
            http=http,
            link_inspector=FakeLinkInspector(),
        ).generate(
            CommentRef(
                "1",
                "Tester",
                "Can you comment on my profile?",
                None,
                object(),
            )
        )

        self.assertEqual(
            decision.profile_comment,
            "Hey! What are you creating next?",
        )

    def test_outreach_request_uses_separate_opening_prompt(self) -> None:
        settings = Settings(
            scratch_username="Bot",
            scratch_session_string="fake",
            groq_api_key="fake-key",
            groq_model="qwen/qwen3.6-27b",
            allowed_users=frozenset(),
            audience_mode="everyone",
            bot_mode="simulate",
            max_reply_chars=300,
            persona="A test persona.",
        )
        http = FakeHTTPSession()

        decision = GroqAgent(settings, http=http).generate_outreach("Tester")

        system_prompt = http.payload["messages"][0]["content"]
        self.assertIn("opening profile comment", system_prompt)
        self.assertIn("Do not claim to have inspected", system_prompt)
        self.assertIn("Tester", http.payload["messages"][1]["content"])
        self.assertTrue(decision.should_reply)

    def test_project_invitation_request_uses_separate_prompt(self) -> None:
        settings = Settings(
            scratch_username="Bot",
            scratch_session_string="fake",
            groq_api_key="fake-key",
            groq_model="qwen/qwen3.6-27b",
            allowed_users=frozenset(),
            audience_mode="everyone",
            bot_mode="simulate",
            max_reply_chars=300,
            persona="A test persona.",
        )
        http = FakeHTTPSession()

        decision = GroqAgent(
            settings,
            http=http,
        ).generate_project_invitation("Tester", "123")

        system_prompt = http.payload["messages"][0]["content"]
        self.assertIn("standalone comment for a Scratch project", system_prompt)
        self.assertIn("Do not claim to have played", system_prompt)
        self.assertIn('"project_id": "123"', http.payload["messages"][1]["content"])
        self.assertTrue(decision.should_reply)

    def test_mistral_provider_uses_pinned_model_and_direct_endpoint(self) -> None:
        settings = Settings(
            scratch_username="Bot",
            scratch_session_string="fake",
            groq_api_key="groq-key",
            groq_model="qwen/qwen3.6-27b",
            allowed_users=frozenset(),
            audience_mode="everyone",
            bot_mode="simulate",
            max_reply_chars=300,
            persona="A test persona.",
            ai_provider="mistral",
            mistral_api_key="mistral-key",
            mistral_model="mistral-medium-3-5",
        )
        http = FakeHTTPSession()

        ChatAgent(settings, http=http).generate(
            CommentRef("1", "Tester", "Hello", None, object())
        )

        self.assertEqual(http.url, MISTRAL_CHAT_URL)
        self.assertEqual(
            http.headers["Authorization"],
            "Bearer mistral-key",
        )
        self.assertEqual(
            http.payload["model"],
            "mistral-medium-3-5",
        )
        self.assertEqual(http.payload["reasoning_effort"], "none")

    def test_mistral_agent_uses_stateless_conversations_endpoint(self) -> None:
        settings = Settings(
            scratch_username="Bot",
            scratch_session_string="fake",
            groq_api_key="groq-key",
            groq_model="qwen/qwen3.6-27b",
            allowed_users=frozenset(),
            audience_mode="everyone",
            bot_mode="simulate",
            max_reply_chars=300,
            persona="A test persona.",
            ai_provider="mistral",
            mistral_api_key="mistral-key",
            mistral_agent_id="ag_test",
        )
        reply = {
            "reply": "BB7 was hosted by ManageLimit.",
            "reason": "library-grounded answer",
            "profile_comment": "",
            "project_comment": "",
        }
        http = FakeHTTPSession(
            body={
                "outputs": [
                    {"type": "tool.execution", "name": "document_library"},
                    {
                        "type": "message.output",
                        "content": [
                            {
                                "type": "tool_reference",
                                "tool": "document_library",
                            },
                            {
                                "type": "thinking",
                                "thinking": [{"type": "text", "text": "Reasoning"}],
                            },
                            {
                                "type": "text",
                                "text": "```json\n" + json.dumps(reply) + "\n```",
                            },
                        ],
                    },
                ]
            }
        )

        decision = ChatAgent(settings, http=http).generate(
            CommentRef("1", "Tester", "Who hosted BB7?", None, object())
        )

        self.assertEqual(http.url, MISTRAL_CONVERSATIONS_URL)
        self.assertEqual(http.payload["agent_id"], "ag_test")
        self.assertFalse(http.payload["store"])
        self.assertFalse(http.payload["stream"])
        self.assertNotIn("model", http.payload)
        self.assertNotIn("tools", http.payload)
        self.assertNotIn("instructions", http.payload)
        self.assertNotIn("completion_args", http.payload)
        self.assertIn("Who hosted BB7?", http.payload["inputs"])
        self.assertIn("Non-negotiable rules", http.payload["inputs"])
        self.assertEqual(decision.reply, "BB7 was hosted by ManageLimit.")


if __name__ == "__main__":
    unittest.main()
