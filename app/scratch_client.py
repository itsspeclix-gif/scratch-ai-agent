from __future__ import annotations

import json
import logging
import time
import warnings
from html.parser import HTMLParser
from typing import Any

import requests
from bs4 import BeautifulSoup

from app.config import Settings
from app.models import CommentRef, ThreadTurn

PAGE_SIZE = 40
logger = logging.getLogger(__name__)


class _ProfileComment:
    def __init__(
        self,
        *,
        comment_id: str,
        author: str,
        content: str,
        source_id: str,
        parent_id: str | None = None,
    ) -> None:
        self.id = comment_id
        self.author_name = author
        self.content = content
        self.source = "profile"
        self.source_id = source_id
        self.parent_id = parent_id
        self.cached_replies: list[_ProfileComment] = []

    def replies(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Any]:
        if limit is None:
            return self.cached_replies[offset:]
        return self.cached_replies[offset : offset + limit]


class _ProfilePostParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.created_comment_id: str | None = None
        self._reading_error_data = False
        self.error_data = ""

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if tag == "div" and "comment" in classes:
            comment_id = attributes.get("data-comment-id")
            if comment_id:
                self.created_comment_id = comment_id
        if tag == "script" and attributes.get("id") == "error-data":
            self._reading_error_data = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._reading_error_data = False

    def handle_data(self, data: str) -> None:
        if self._reading_error_data:
            self.error_data += data


class ScratchClient:
    def __init__(self, settings: Settings) -> None:
        try:
            import scratchattach as sa
        except ImportError as exc:
            raise RuntimeError("scratchattach is not installed; run 'make install'") from exc

        self._settings = settings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=sa.LoginDataWarning)
            self._session = sa.login_by_session_string(
                settings.scratch_session_string
            )
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
        self._profile_user_ids: dict[str, Any] = {}

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

    def _fresh_profile_page(self, source: Any, page_number: int) -> list[Any]:
        profile_username = str(
            getattr(source, "username", None)
            or self._settings.scratch_username
        )
        headers = dict(getattr(self._session, "_headers", {}))
        headers["Cache-Control"] = "no-cache"
        headers["Pragma"] = "no-cache"
        response = requests.get(
            f"https://scratch.mit.edu/site-api/comments/user/{profile_username}/",
            params={"page": page_number, "_": time.time_ns()},
            headers=headers,
            cookies=getattr(self._session, "_cookies", {}),
            timeout=30,
        )
        if not 200 <= response.status_code < 300:
            raise RuntimeError(
                "Scratch rejected the profile comment fetch "
                f"(HTTP {response.status_code})"
            )

        soup = BeautifulSoup(response.content, "html.parser")
        roots: list[_ProfileComment] = []
        for entity in soup.select("li.top-level-reply"):
            root_node = entity.find("div", class_="comment")
            author_node = entity.find("a", id="comment-user")
            content_node = entity.find("div", class_="content")
            if root_node is None or author_node is None or content_node is None:
                raise RuntimeError("Scratch returned malformed profile comment HTML")

            root_id = root_node.get("data-comment-id")
            author = author_node.get("data-comment-user")
            if not root_id or not author:
                raise RuntimeError("Scratch profile comment is missing an id or author")

            root = _ProfileComment(
                comment_id=str(root_id),
                author=str(author),
                content=content_node.get_text(" ", strip=True),
                source_id=profile_username,
            )
            for reply_entity in entity.select("li.reply"):
                reply_node = reply_entity.find("div", class_="comment")
                reply_author_node = reply_entity.find("a", id="comment-user")
                reply_content_node = reply_entity.find("div", class_="content")
                if (
                    reply_node is None
                    or reply_author_node is None
                    or reply_content_node is None
                ):
                    raise RuntimeError(
                        "Scratch returned malformed profile reply HTML"
                    )

                reply_id = reply_node.get("data-comment-id")
                reply_author = reply_author_node.get("data-comment-user")
                if not reply_id or not reply_author:
                    raise RuntimeError(
                        "Scratch profile reply is missing an id or author"
                    )
                root.cached_replies.append(
                    _ProfileComment(
                        comment_id=str(reply_id),
                        author=str(reply_author),
                        content=reply_content_node.get_text(" ", strip=True),
                        source_id=profile_username,
                        parent_id=str(root_id),
                    )
                )
            roots.append(root)
        return roots

    def _roots(self, source_type: str, source: Any) -> list[Any]:
        roots: list[Any] = []
        if source_type == "profile":
            page_number = 1
            while True:
                page = self._fresh_profile_page(source, page_number)
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

    def _profile_root_by_id(self, source: Any, root_id: str) -> Any | None:
        page_number = 1
        while True:
            roots = self._fresh_profile_page(source, page_number)
            if not roots:
                return None
            for root in roots:
                if str(root.id) == str(root_id):
                    return root
            page_number += 1

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
        if comment.source == "profile":
            root = self._profile_root_by_id(source, root_id)
            if root is None:
                return False
        else:
            root = source.comment_by_id(root_id)
        current = self._candidate_from_root(
            root,
            source_type=comment.source,
            source_id=comment.source_id,
        )
        return current is not None and current.id == comment.id

    def _profile_commentee_id(self, comment: CommentRef) -> Any:
        raw_author_id = getattr(comment.raw, "author_id", None)
        if raw_author_id not in (None, "", 0, "0"):
            return raw_author_id

        username_key = comment.author.casefold()
        if username_key not in self._profile_user_ids:
            author = self._session.connect_user(comment.author)
            author_id = getattr(author, "id", None)
            if author_id in (None, "", 0, "0"):
                raise RuntimeError(
                    f"Could not resolve Scratch user id for {comment.author}"
                )
            self._profile_user_ids[username_key] = author_id
        return self._profile_user_ids[username_key]

    def _post_profile_reply(
        self,
        comment: CommentRef,
        text: str,
        commentee_id: Any,
    ) -> None:
        profile_username = comment.source_id or self._settings.scratch_username
        raw_parent_id = getattr(comment.raw, "parent_id", None)
        raw_comment_id = getattr(comment.raw, "id", None)
        parent_id = (
            raw_parent_id
            if raw_parent_id not in (None, "", 0, "0")
            else raw_comment_id
        )
        if parent_id in (None, "", 0, "0"):
            parent_id = comment.parent_id or comment.id
        parent_id = str(parent_id)

        if comment.root_id and str(comment.root_id) != parent_id:
            logger.warning(
                "correcting profile reply parent comment=%s stored_root=%s "
                "authoritative_parent=%s",
                comment.id,
                comment.root_id,
                parent_id,
            )
        url = (
            "https://scratch.mit.edu/site-api/comments/user/"
            f"{profile_username}/add/"
        )
        headers = dict(getattr(self._session, "_headers", {}))
        headers["Referer"] = f"https://scratch.mit.edu/users/{profile_username}/"
        response = requests.post(
            url,
            headers=headers,
            cookies=getattr(self._session, "_cookies", {}),
            data=json.dumps(
                {
                    "commentee_id": commentee_id,
                    "content": text,
                    "parent_id": parent_id,
                }
            ),
            timeout=30,
        )

        if response.status_code == 429:
            raise RuntimeError("Scratch rate-limited the profile reply")
        if not 200 <= response.status_code < 300:
            raise RuntimeError(
                "Scratch rejected the profile reply "
                f"(HTTP {response.status_code})"
            )

        parser = _ProfilePostParser()
        parser.feed(response.text)
        if parser.created_comment_id is not None:
            logger.info(
                "profile reply accepted source_comment=%s created_comment=%s "
                "parent=%s",
                comment.id,
                parser.created_comment_id,
                parent_id,
            )
            return

        error_message = ""
        if parser.error_data.strip():
            try:
                error_body = json.loads(parser.error_data)
            except json.JSONDecodeError:
                error_message = parser.error_data.strip()
            else:
                if isinstance(error_body, dict):
                    error_message = str(
                        error_body.get("message")
                        or error_body.get("error")
                        or error_body
                    )
        if not error_message:
            error_message = "Scratch returned no created comment"
        raise RuntimeError(f"Profile reply was not accepted: {error_message[:200]}")

    def reply(self, comment: CommentRef, text: str) -> None:
        if comment.source == "profile":
            commentee_id = self._profile_commentee_id(comment)
            self._post_profile_reply(comment, text, commentee_id)
            return

        # scratchattach sets the correct root parent and commentee for project roots
        # and follow-up replies.
        comment.raw.reply(text)
