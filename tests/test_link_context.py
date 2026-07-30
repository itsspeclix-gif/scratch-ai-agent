from __future__ import annotations

import unittest

from app.link_context import LinkInspector


class FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        body: bytes = b"",
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self.body = body
        self.encoding = "utf-8"
        self.closed = False

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise ValueError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size: int) -> list[bytes]:
        return [
            self.body[index : index + chunk_size]
            for index in range(0, len(self.body), chunk_size)
        ]

    def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.urls: list[str] = []

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        self.urls.append(url)
        return self.responses.pop(0)


class LinkInspectorTests(unittest.TestCase):
    def test_extracts_small_html_preview_from_first_link(self) -> None:
        response = FakeResponse(
            headers={"Content-Type": "text/html; charset=utf-8"},
            body=(
                b"<html><head><title>Example Project</title>"
                b'<meta name="description" content="A tiny game about clones.">'
                b"</head><body><script>ignore me</script></body></html>"
            ),
        )
        session = FakeSession([response])
        inspector = LinkInspector(
            http=session,
            resolver=lambda hostname, port: ["8.8.8.8"],
        )

        preview = inspector.inspect_text(
            "Could you look at https://example.com/project?x=1 please?"
        )

        self.assertIsNotNone(preview)
        assert preview is not None
        self.assertEqual(preview.url, "https://example.com/project?x=1")
        self.assertEqual(preview.title, "Example Project")
        self.assertEqual(preview.summary, "A tiny game about clones.")
        self.assertTrue(response.closed)

    def test_private_address_is_rejected_without_request(self) -> None:
        session = FakeSession([])
        inspector = LinkInspector(
            http=session,
            resolver=lambda hostname, port: ["127.0.0.1"],
        )

        preview = inspector.inspect_text("Read http://localhost/admin")

        self.assertIsNone(preview)
        self.assertEqual(session.urls, [])

    def test_redirect_target_is_revalidated(self) -> None:
        redirect = FakeResponse(
            status_code=302,
            headers={"Location": "http://internal.test/private"},
        )
        session = FakeSession([redirect])

        def resolve(hostname: str, port: int) -> list[str]:
            if hostname == "internal.test":
                return ["10.0.0.1"]
            return ["8.8.8.8"]

        inspector = LinkInspector(http=session, resolver=resolve)

        preview = inspector.inspect_text("Read https://example.com/start")

        self.assertIsNone(preview)
        self.assertEqual(session.urls, ["https://example.com/start"])
        self.assertTrue(redirect.closed)


if __name__ == "__main__":
    unittest.main()
