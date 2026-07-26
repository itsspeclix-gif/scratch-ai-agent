from __future__ import annotations

import logging
from typing import Any

from app.config import Settings
from app.models import CommentRef, ThreadTurn

PAGE_SIZE = 40
logger = logging.getLogger(__name__)


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
        self._user = self._session.connect_linked_user()
        self._sources: dict[tuple[str, str], Any] = {
            ("profile", settings.scratch_username.casefold()): self._user,
        }

    @staticmethod
    def _id_sort_key(raw_id: Any) -> tuple[int, str]:
        raw_id = str(raw_id)
        try:
            return (0, f"{int(raw_id):020d}")
        except ValueError:
            return (1, raw_id)

    def _all_replies(self, root: Any) -> list[Any]:
        if str(getattr(root, "source", "")).lower() == "profile":
            return list(root.replies() or [])

        replies: list[Any] = []
        offset = 0
        while True:
            page = list(
                root.replies(
                    use_cache=False,
                    limit=PAGE_SIZE,
                    offset=offset,
                )
                or []
            )
            replies.extend(page)
            if len(page) < PAGE_SIZE:
                break
            offset += len(page)
        return replies

    def _candidate_from_root(
        self,
        root: Any,
        *,
        source_type: str | None = None,
        source_id: str | None = None,
    ) -> CommentRef | None:
        thread_comments = [root, *self._all_replies(root)]
        thread_comments.sort(key=lambda item: self._id_sort_key(item.id))

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
            for item in thread_comments
        )
        parent = getattr(target, "parent_id", None)
        parent_id = None if parent in (None, "", 0, "0") else str(parent)
        resolved_source = (
            source_type or str(getattr(root, "source", "project"))
        ).lower()
        resolved_source_id = source_id or str(
            getattr(root, "source_id", "")
        )

        return CommentRef(
            id=str(target.id),
            author=str(target.author_name),
            content=str(target.content),
            parent_id=parent_id,
            raw=target,
            root_id=str(root.id),
            thread=history,
            source=resolved_source,
            source_id=resolved_source_id or None,
        )

    def _projects(self) -> list[Any]:
        projects: list[Any] = []
        offset = 0
        while True:
            page = list(
                self._user.projects(limit=PAGE_SIZE, offset=offset) or []
            )
            projects.extend(page)
            if len(page) < PAGE_SIZE:
                break
            offset += len(page)
        return projects

    @staticmethod
    def _roots(source_type: str, source: Any) -> list[Any]:
        roots: list[Any] = []
        if source_type == "profile":
            page_number = 1
            while True:
                page = list(source.comments(page=page_number) or [])
                if not page:
                    break
                roots.extend(page)
                page_number += 1
            return roots

        offset = 0
        while True:
            page = list(
                source.comments(limit=PAGE_SIZE, offset=offset) or []
            )
            roots.extend(page)
            if len(page) < PAGE_SIZE:
                break
            offset += len(page)
        return roots

    def conversation_targets(self) -> list[CommentRef]:
        result: list[CommentRef] = []
        sources: list[tuple[str, str, Any]] = [
            ("profile", self._settings.scratch_username.casefold(), self._user)
        ]
        for project in self._projects():
            project_id = str(project.id)
            sources.append(("project", project_id, project))

        for source_type, source_id, source in sources:
            source_key = (source_type, source_id)
            self._sources[source_key] = source
            try:
                roots = self._roots(source_type, source)
            except Exception:
                logger.exception(
                    "failed loading Scratch %s=%s",
                    source_type,
                    source_id,
                )
                continue

            for root in roots:
                try:
                    parent = getattr(root, "parent_id", None)
                    if parent not in (None, "", 0, "0"):
                        continue
                    candidate = self._candidate_from_root(
                        root,
                        source_type=source_type,
                        source_id=source_id,
                    )
                    if candidate is not None:
                        result.append(candidate)
                except Exception:
                    logger.exception(
                        "failed scanning Scratch %s=%s thread=%s",
                        source_type,
                        source_id,
                        getattr(root, "id", "unknown"),
                    )

        result.sort(
            key=lambda comment: self._id_sort_key(comment.id),
            reverse=True,
        )
        return result

    def is_current_target(self, comment: CommentRef) -> bool:
        source_id = comment.source_id or ""
        source_key = (
            comment.source,
            source_id.casefold() if comment.source == "profile" else source_id,
        )
        source = self._sources.get(source_key)
        if source is None:
            return False

        root_id = comment.root_id or comment.id
        root = source.comment_by_id(root_id)
        current = self._candidate_from_root(
            root,
            source_type=comment.source,
            source_id=comment.source_id,
        )
        return current is not None and current.id == comment.id

    def reply(self, comment: CommentRef, text: str) -> None:
        # scratchattach sets the correct root parent and commentee for both root comments
        # and follow-up replies.
        comment.raw.reply(text)
