from __future__ import annotations

from app.config import Settings
from app.models import CommentRef


class ScratchClient:
    def __init__(self, settings: Settings) -> None:
        try:
            import scratchattach as sa
        except ImportError as exc:
            raise RuntimeError("scratchattach is not installed; run 'make install'") from exc

        self._settings = settings
        self._session = sa.login_by_session_string(settings.scratch_session_string)
        actual_username = str(self._session.username)
        if actual_username.casefold() != settings.scratch_username.casefold():
            raise RuntimeError(
                "SCRATCH_SESSION_STRING belongs to a different account: "
                f"expected {settings.scratch_username}, received {actual_username}"
            )
        self._project = self._session.connect_project(settings.scratch_project_id)

    def recent_top_level_comments(self) -> list[CommentRef]:
        comments = self._project.comments(
            limit=self._settings.max_recent_comments,
            offset=0,
        )
        result: list[CommentRef] = []
        for comment in comments:
            parent = getattr(comment, "parent_id", None)
            if parent not in (None, "", 0, "0"):
                continue
            result.append(
                CommentRef(
                    id=str(comment.id),
                    author=str(comment.author_name),
                    content=str(comment.content),
                    parent_id=None,
                    raw=comment,
                )
            )
        return result

    def has_bot_reply(self, comment: CommentRef) -> bool:
        replies = comment.raw.replies()
        bot_name = self._settings.scratch_username.casefold()
        return any(str(reply.author_name).casefold() == bot_name for reply in replies)

    def reply(self, comment: CommentRef, text: str) -> None:
        comment.raw.reply(text)
