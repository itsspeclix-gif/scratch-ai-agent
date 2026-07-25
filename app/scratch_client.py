from __future__ import annotations

from typing import Any

from app.config import Settings
from app.models import CommentRef, ThreadTurn


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

    @staticmethod
    def _sort_key(comment: Any) -> tuple[int, str]:
        raw_id = str(comment.id)
        try:
            return (0, f"{int(raw_id):020d}")
        except ValueError:
            return (1, raw_id)

    def _candidate_from_root(self, root: Any) -> CommentRef | None:
        thread_comments = [root, *root.replies()]
        thread_comments.sort(key=self._sort_key)

        bot_name = self._settings.scratch_username.casefold()
        latest_external_index: int | None = None
        for index, item in enumerate(thread_comments):
            if str(item.author_name).casefold() != bot_name:
                latest_external_index = index

        if latest_external_index is None:
            return None

        # The thread is already answered when the bot has posted after the newest user comment.
        if any(
            str(item.author_name).casefold() == bot_name
            for item in thread_comments[latest_external_index + 1 :]
        ):
            return None

        target = thread_comments[latest_external_index]
        history = tuple(
            ThreadTurn(
                id=str(item.id),
                author=str(item.author_name),
                content=str(item.content),
            )
            for item in thread_comments[-self._settings.max_thread_messages :]
        )
        parent = getattr(target, "parent_id", None)
        parent_id = None if parent in (None, "", 0, "0") else str(parent)

        return CommentRef(
            id=str(target.id),
            author=str(target.author_name),
            content=str(target.content),
            parent_id=parent_id,
            raw=target,
            root_id=str(root.id),
            thread=history,
        )

    def recent_conversation_targets(self) -> list[CommentRef]:
        roots = self._project.comments(
            limit=self._settings.max_recent_comments,
            offset=0,
        )
        result: list[CommentRef] = []
        for root in roots:
            parent = getattr(root, "parent_id", None)
            if parent not in (None, "", 0, "0"):
                continue
            candidate = self._candidate_from_root(root)
            if candidate is not None:
                result.append(candidate)
        return result

    def is_current_target(self, comment: CommentRef) -> bool:
        root_id = comment.root_id or comment.id
        root = self._project.comment_by_id(root_id)
        current = self._candidate_from_root(root)
        return current is not None and current.id == comment.id

    def reply(self, comment: CommentRef, text: str) -> None:
        # scratchattach sets the correct root parent and commentee for both root comments
        # and follow-up replies.
        comment.raw.reply(text)
