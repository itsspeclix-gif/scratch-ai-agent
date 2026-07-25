from __future__ import annotations

import json
import unittest

from app.config import Settings
from app.groq_agent import GROQ_CHAT_URL, GroqAgent
from app.models import CommentRef


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
                                "should_reply": True,
                                "reply": "I used clones.",
                                "reason": "project question",
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
    def test_request_contract_uses_fake_authorization_headers(self) -> None:
        settings = Settings(
            scratch_username="Bot",
            scratch_session_string="fake",
            scratch_project_id="123",
            groq_api_key="fake-key",
            groq_model="llama-3.1-8b-instant",
            allowed_users=frozenset({"tester"}),
            bot_mode="simulate",
            max_recent_comments=20,
            max_replies_per_run=2,
            max_reply_chars=300,
            persona="A test persona.",
        )
        http = FakeHTTPSession()
        decision = GroqAgent(settings, http=http).generate(
            CommentRef("1", "Tester", "How?", None, object())
        )

        self.assertEqual(http.url, GROQ_CHAT_URL)
        self.assertEqual(http.headers["Authorization"], "Bearer fake-key")
        self.assertEqual(http.headers["Content-Type"], "application/json")
        self.assertEqual(http.timeout, 30)
        self.assertEqual(http.payload["response_format"], {"type": "json_object"})
        self.assertTrue(decision.should_reply)
        self.assertEqual(decision.reply, "I used clones.")


if __name__ == "__main__":
    unittest.main()
