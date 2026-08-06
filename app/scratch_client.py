from __future__ import annotations

import hashlib
import json
import logging
import re
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


def _session_headers(session: Any) -> dict[str, str]:
    headers = getattr(session, "_headers", None)
    if isinstance(headers, dict):
        return headers
    return {}


def _session_cookies(session: Any) -> dict[str, str]:
    cookies = getattr(session, "_cookies", None)
    if isinstance(cookies, dict):
        return cookies
    return {}


def _strip_leading_author_mentions(text: str, author: str) -> str:
    mention = re.compile(
        rf"^(?:\s*@{re.escape(author)}(?![A-Za-z0-9_-])[\s,:;.!?-]*)+",
        re.IGNORECASE,
    )
    return mention.sub("", text).strip()


def _normalized_comment_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _profile_post_response_markers(text: str) -> str:
    markers = {
        "comment_id": "data-comment-id" in text or "comments-" in text,
        "error_data": "error-data" in text,
        "is_flood": "isFlood" in text,
        "mute": "mute_status" in text,
        "login": "modal-login" in text or "login" in text.lower(),
    }
    return ",".join(name for name, present in markers.items() if present) or "none"


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
        self._unread_message_count = 0
        self._notification_scan_complete = True
        self._prepared_outreach: str | None = None

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

    def _profile_root_containing(
        self,
        source: Any,
        comment_id: str,
    ) -> Any | None:
        page_number = 1
        while True:
            roots = self._fresh_profile_page(source, page_number)
            if not roots:
                return None
            for root in roots:
                if str(root.id) == str(comment_id):
                    return root
                if any(
                    str(reply.id) == str(comment_id)
                    for reply in self._all_replies(root)
                ):
                    return root
            page_number += 1

    def _connect_source(self, source_type: str, source_id: str) -> Any:
        source_key = (
            source_type,
            source_id.casefold() if source_type == "profile" else source_id,
        )
        source = self._sources.get(source_key)
        if source is not None:
            return source
        if source_type == "profile":
            source = self._session.connect_user(source_id)
        elif source_type == "project":
            source = self._session.connect_project(source_id)
        else:
            raise ValueError(f"Unsupported Scratch source type: {source_type}")
        self._sources[source_key] = source
        return source

    def _notification_target(self, message: Any) -> CommentRef | None:
        if str(getattr(message, "type", "")).lower() != "addcomment":
            return None

        try:
            comment_type = int(getattr(message, "comment_type", -1))
        except (TypeError, ValueError):
            return None
        comment_id = str(getattr(message, "comment_id", "") or "")
        if not comment_id:
            return None

        if comment_type == 0:
            source_type = "project"
            source_id = str(getattr(message, "comment_obj_id", "") or "")
        elif comment_type == 1:
            source_type = "profile"
            source_id = str(getattr(message, "comment_obj_title", "") or "")
        else:
            return None
        if not source_id:
            return None

        source = self._connect_source(source_type, source_id)
        if source_type == "profile":
            root = self._profile_root_containing(source, comment_id)
        else:
            notified_comment = source.comment_by_id(comment_id)
            parent_id = getattr(notified_comment, "parent_id", None)
            root_id = (
                comment_id
                if parent_id in (None, "", 0, "0")
                else str(parent_id)
            )
            root = source.comment_by_id(root_id)
        if root is None:
            logger.info(
                "notification comment no longer exists source=%s id=%s",
                source_id,
                comment_id,
            )
            return None

        candidate = self._candidate_from_root(
            root,
            source_type=source_type,
            source_id=source_id,
        )
        if candidate is None:
            return None

        bot_name = self._settings.scratch_username.casefold()
        if source_type == "profile":
            source_is_owned = source_id.casefold() == bot_name
        else:
            source_is_owned = (
                str(getattr(source, "author_name", "")).casefold() == bot_name
            )
        bot_participated = any(
            turn.author.casefold() == bot_name
            for turn in candidate.thread
        )
        if not source_is_owned and not bot_participated:
            logger.info(
                "skip unrelated external notification source=%s comment=%s",
                source_id,
                comment_id,
            )
            return None
        return candidate

    def _notification_targets(self) -> list[CommentRef]:
        self._notification_scan_complete = True
        count_known = True
        try:
            self._unread_message_count = self._message_count()
        except Exception as exc:
            count_known = False
            self._notification_scan_complete = False
            self._unread_message_count = 0
            logger.warning(
                "failed checking Scratch notification count; fetching mail "
                "anyway: %r",
                exc,
            )
        if count_known and self._unread_message_count <= 0:
            return []

        try:
            if count_known:
                fetch_limit = min(self._unread_message_count, PAGE_SIZE)
            else:
                fetch_limit = PAGE_SIZE
            messages = list(self._session.messages(limit=fetch_limit) or [])
        except Exception:
            self._notification_scan_complete = False
            logger.exception("failed loading Scratch notifications")
            return []

        if self._unread_message_count > len(messages):
            self._notification_scan_complete = False
            logger.info(
                "loaded %d of %d Scratch notifications; keeping mail unread",
                len(messages),
                self._unread_message_count,
            )

        if self._unread_message_count <= 0:
            self._unread_message_count = len(messages)

        result: list[CommentRef] = []
        for message in messages:
            try:
                candidate = self._notification_target(message)
                if candidate is not None:
                    result.append(candidate)
            except Exception:
                self._notification_scan_complete = False
                logger.exception(
                    "failed resolving Scratch notification comment=%s",
                    getattr(message, "comment_id", "unknown"),
                )
        return result

    def _message_count(self) -> int:
        username = self._settings.scratch_username
        headers = _session_headers(self._session)
        cookies = _session_cookies(self._session)
        if not getattr(self._session, "_username", None) or (
            not headers and not cookies
        ):
            return int(self._session.message_count())
        response = requests.get(
            f"https://api.scratch.mit.edu/users/{username}/messages/count",
            headers=headers,
            cookies=cookies,
            timeout=10,
        )
        if response.ok:
            data = response.json()
            if "count" in data:
                return int(data["count"])
        return int(self._session.message_count())

    def _full_scan_due(self) -> bool:
        if not self._settings.full_scan_enabled:
            return False
        minute = int(time.time() // 60)
        return minute % self._settings.full_scan_interval_minutes == 0

    def _full_scan_targets(self) -> list[CommentRef]:
        result: list[CommentRef] = []
        sources: list[tuple[str, str, Any]] = [
            ("profile", self._settings.scratch_username.casefold(), self._user)
        ]
        for project in self._projects():
            project_id = str(project.id)
            sources.append(("project", project_id, project))

        bot_name = self._settings.scratch_username.casefold()
        for username in self._settings.outreach_users:
            if username.casefold() == bot_name:
                continue
            source = self._connect_source("profile", username)
            sources.append(("profile", username.casefold(), source))

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
                    if (
                        source_type == "profile"
                        and source_id != bot_name
                        and str(root.author_name).casefold() != bot_name
                    ):
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
        return result

    def conversation_targets(self) -> list[CommentRef]:
        result = self._notification_targets()
        if self._full_scan_due():
            logger.info("running periodic full conversation scan")
            result.extend(self._full_scan_targets())

        deduplicated: dict[tuple[str, str, str, str], CommentRef] = {}
        for comment in result:
            source_id = comment.source_id or ""
            if comment.source == "profile":
                source_id = source_id.casefold()
            key = (
                comment.source,
                source_id,
                comment.root_id or comment.id,
                comment.id,
            )
            deduplicated[key] = comment
        result = list(deduplicated.values())
        result.sort(
            key=lambda comment: self._id_sort_key(comment.id),
            reverse=True,
        )
        return result

    def finish_notification_batch(self, success: bool) -> None:
        if self._unread_message_count <= 0:
            return
        if not success or not self._notification_scan_complete:
            logger.warning(
                "leaving %d Scratch notifications unread for retry",
                self._unread_message_count,
            )
            return
        try:
            current_count = self._message_count()
        except Exception as exc:
            logger.warning(
                "failed checking Scratch notification count; leaving %d "
                "Scratch notifications unread for retry: %r",
                self._unread_message_count,
                exc,
            )
            return
        if current_count != self._unread_message_count:
            logger.info(
                "notification count changed from %d to %d; deferring clear",
                self._unread_message_count,
                current_count,
            )
            return
        try:
            self._session.clear_messages()
        except Exception as exc:
            logger.warning(
                "failed clearing %d Scratch notifications; leaving them "
                "unread for retry: %r",
                self._unread_message_count,
                exc,
            )
            return
        logger.info(
            "marked %d Scratch notifications as read",
            self._unread_message_count,
        )
        self._unread_message_count = 0

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

    def _post_profile_comment(
        self,
        profile_username: str,
        text: str,
        *,
        parent_id: str = "",
        commentee_id: Any = "",
        recipient_username: str | None = None,
    ) -> str:
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
            raise RuntimeError("Scratch rate-limited the profile comment")
        if not 200 <= response.status_code < 300:
            raise RuntimeError(
                "Scratch rejected the profile comment "
                f"(HTTP {response.status_code})"
            )

        parser = _ProfilePostParser()
        parser.feed(response.text)
        if parser.created_comment_id is not None:
            return parser.created_comment_id

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
            verified_comment_id = None
            for attempt in range(3):
                if attempt:
                    time.sleep(1)
                verified_comment_id = self._verified_profile_post_id(
                    profile_username,
                    text,
                    parent_id=parent_id,
                    recipient_username=recipient_username,
                )
                if verified_comment_id is not None:
                    break
            if verified_comment_id is not None:
                logger.info(
                    "profile comment accepted but response omitted id; "
                    "verified created_comment=%s",
                    verified_comment_id,
                )
                return verified_comment_id
        if not error_message:
            error_message = (
                "Scratch returned no created comment "
                f"(HTTP {response.status_code}, "
                f"content-type={response.headers.get('content-type', 'unknown')}, "
                f"body_length={len(response.text)}, "
                f"markers={_profile_post_response_markers(response.text)})"
            )
        raise RuntimeError(f"Profile comment was not accepted: {error_message[:200]}")

    def _profile_content_matches(
        self,
        content: str,
        text: str,
        recipient_username: str | None,
    ) -> bool:
        actual = _normalized_comment_text(content)
        expected = _normalized_comment_text(text)
        if recipient_username:
            actual = _normalized_comment_text(
                _strip_leading_author_mentions(actual, recipient_username)
            )
        return actual == expected

    def _verified_profile_post_id(
        self,
        profile_username: str,
        text: str,
        *,
        parent_id: str = "",
        recipient_username: str | None = None,
    ) -> str | None:
        source = self._connect_source("profile", profile_username)
        bot_name = self._settings.scratch_username.casefold()
        candidates: list[Any]
        if parent_id:
            root = self._profile_root_by_id(source, parent_id)
            candidates = self._all_replies(root) if root is not None else []
        else:
            candidates = self._fresh_profile_page(source, 1)

        for candidate in candidates:
            if str(getattr(candidate, "author_name", "")).casefold() != bot_name:
                continue
            content = str(getattr(candidate, "content", "") or "")
            if self._profile_content_matches(content, text, recipient_username):
                return str(getattr(candidate, "id", "") or "") or None
        return None

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
        created_comment_id = self._post_profile_comment(
            profile_username,
            text,
            parent_id=parent_id,
            commentee_id=commentee_id,
            recipient_username=comment.author,
        )
        logger.info(
            "profile reply accepted source_comment=%s created_comment=%s parent=%s",
            comment.id,
            created_comment_id,
            parent_id,
        )

    def start_profile_invitation(self, username: str, text: str) -> str | None:
        if username.casefold() == self._settings.scratch_username.casefold():
            return None

        source = self._connect_source("profile", username)
        bot_name = self._settings.scratch_username.casefold()
        if any(
            str(root.author_name).casefold() == bot_name
            for root in self._roots("profile", source)
        ):
            logger.info(
                "skip profile invitation user=%s reason=existing bot conversation",
                username,
            )
            return None

        text = _strip_leading_author_mentions(text, username)
        if not text:
            raise RuntimeError(
                "Invited profile comment contains only a recipient mention"
            )
        created_comment_id = self._post_profile_comment(username, text)
        logger.info(
            "profile invitation posted user=%s created_comment=%s",
            username,
            created_comment_id,
        )
        return created_comment_id

    def start_project_invitation(
        self,
        username: str,
        project_id: str,
        text: str,
    ) -> str | None:
        source = self._connect_source("project", project_id)
        owner = str(getattr(source, "author_name", ""))
        if owner.casefold() != username.casefold():
            raise RuntimeError(
                "Invited project is not owned by the requesting Scratch user"
            )

        bot_name = self._settings.scratch_username.casefold()
        if any(
            str(root.author_name).casefold() == bot_name
            for root in self._roots("project", source)
        ):
            logger.info(
                "skip project invitation project=%s reason=existing bot conversation",
                project_id,
            )
            return None

        text = _strip_leading_author_mentions(text, username)
        if not text:
            raise RuntimeError(
                "Invited project comment contains only a recipient mention"
            )
        created = source.post_comment(text)
        created_comment_id = str(getattr(created, "id", "") or "")
        if not created_comment_id:
            raise RuntimeError("Scratch returned no created project comment id")
        logger.info(
            "project invitation posted user=%s project=%s created_comment=%s",
            username,
            project_id,
            created_comment_id,
        )
        return created_comment_id

    def follow_user(self, username: str) -> None:
        if username.casefold() == self._settings.scratch_username.casefold():
            return
        source = self._connect_source("profile", username)
        source.follow()
        logger.info("follow request completed user=%s", username)

    def outreach_candidate(self) -> str | None:
        users = [
            username
            for username in self._settings.outreach_users
            if username.casefold()
            != self._settings.scratch_username.casefold()
        ]
        if not users:
            return None

        minute = int(time.time() // 60)
        if minute % self._settings.outreach_interval_minutes != 0:
            return None
        slot = minute // self._settings.outreach_interval_minutes
        seed = f"{self._settings.scratch_username.casefold()}:{slot}".encode()
        index = int.from_bytes(hashlib.sha256(seed).digest()[:8], "big") % len(users)
        username = users[index]
        source = self._connect_source("profile", username)
        bot_name = self._settings.scratch_username.casefold()
        if any(
            str(root.author_name).casefold() == bot_name
            for root in self._roots("profile", source)
        ):
            logger.info(
                "skip outreach user=%s reason=existing bot conversation",
                username,
            )
            return None
        self._prepared_outreach = username
        return username

    def start_outreach(self, username: str, text: str) -> str:
        if (
            self._prepared_outreach is None
            or username.casefold() != self._prepared_outreach.casefold()
        ):
            raise RuntimeError("Outreach user was not prepared for this run")
        text = _strip_leading_author_mentions(text, username)
        if not text:
            raise RuntimeError("Outreach comment contains only a recipient mention")
        created_comment_id = self._post_profile_comment(username, text)
        self._prepared_outreach = None
        logger.info(
            "outreach posted user=%s created_comment=%s",
            username,
            created_comment_id,
        )
        return created_comment_id

    def reply(self, comment: CommentRef, text: str) -> None:
        text = _strip_leading_author_mentions(text, comment.author)
        if not text:
            raise RuntimeError("Reply contains only a recipient mention")

        if comment.source == "profile":
            commentee_id = self._profile_commentee_id(comment)
            self._post_profile_reply(comment, text, commentee_id)
            return

        # scratchattach sets the correct root parent and commentee for project roots
        # and follow-up replies.
        comment.raw.reply(text)
