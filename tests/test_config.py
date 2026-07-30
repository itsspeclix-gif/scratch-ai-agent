from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from unittest.mock import patch

from app.config import _boolean, _outreach_users


class OutreachUserConfigTests(unittest.TestCase):
    def test_outreach_boolean_defaults_off(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(_boolean("OUTREACH_ENABLED", False))

    def test_outreach_boolean_rejects_unknown_value(self) -> None:
        with patch.dict(
            "os.environ",
            {"OUTREACH_ENABLED": "maybe"},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "must be true or false"):
                _boolean("OUTREACH_ENABLED", False)

    def test_loads_comments_blanks_and_unique_usernames(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "users.txt"
            path.write_text(
                "# opted in\n\nFirst_User\nsecond-user\nfirst_user\n",
                encoding="utf-8",
            )

            self.assertEqual(
                _outreach_users(path),
                ("First_User", "second-user"),
            )

    def test_rejects_invalid_username(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "users.txt"
            path.write_text("not a username\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "Invalid Scratch username"):
                _outreach_users(path)


if __name__ == "__main__":
    unittest.main()
