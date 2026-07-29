from __future__ import annotations

import json
import unittest

from app.config import Settings
from app.groq_agent import GROQ_CHAT_URL, GroqAgent
from app.models import CommentRef, ThreadTurn


class FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "reply": "The second level uses the same clone system.",
                                "reason": "follow-up project question",
                            }
                        )
                    }
                }
            ]
        }


class FakeHTTPSession:
    def __init__(self) -> None:
        self.url = ""
        self.headers: dict[str, str] = {}
        self.payload: dict = {}
        self.timeout = 0

    def post(self, url: str, *, headers: dict, json: dict, timeout: int) -> FakeResponse:
        self.url = url
        self.headers = headers
        self.payload = json
        self.timeout = timeout
        return FakeResponse()


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
        self.assertNotIn("should_reply", system_prompt)
        transcript = http.payload["messages"][1]["content"]
        self.assertIn("How did you make it?", transcript)
        self.assertIn("I used clones.", transcript)
        self.assertIn("What about level two?", transcript)
        self.assertTrue(decision.should_reply)


if __name__ == "__main__":
    unittest.main()
