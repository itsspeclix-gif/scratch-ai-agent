from __future__ import annotations

import logging
from typing import Protocol

from app.config import Settings
from app.models import AgentDecision, CommentRef, RunStats
from app.policy import (
    check_incoming,
    check_reply,
    is_explicit_follow_request,
    is_explicit_profile_invitation,
    is_explicit_project_invitation,
    scratch_project_id,
)


class ScratchClientProtocol(Protocol):
    def conversation_targets(self) -> list[CommentRef]: ...

    def is_current_target(self, comment: CommentRef) -> bool: ...

    def reply(self, comment: CommentRef, text: str) -> None: ...

    def start_profile_invitation(self, username: str, text: str) -> str | None: ...

    def start_project_invitation(
        self,
        username: str,
        project_id: str,
        text: str,
    ) -> str | None: ...

    def follow_user(self, username: str) -> None: ...

    def finish_notification_batch(self, success: bool) -> None: ...

    def outreach_candidate(self) -> str | None: ...

    def start_outreach(self, username: str, text: str) -> str: ...


class AgentProtocol(Protocol):
    def generate(self, comment: CommentRef) -> AgentDecision: ...

    def generate_outreach(self, username: str) -> AgentDecision: ...

    def generate_project_invitation(
        self,
        username: str,
        project_id: str,
    ) -> AgentDecision: ...


def run_once(
    settings: Settings,
    scratch: ScratchClientProtocol,
    agent: AgentProtocol | None,
    logger: logging.Logger,
) -> RunStats:
    stats = RunStats()
    comments = scratch.conversation_targets()

    # Scratch normally returns newest root threads first. Process oldest first.
    for comment in reversed(comments):
        stats.scanned += 1

        incoming = check_incoming(
            comment,
            settings.audience_mode,
            settings.allowed_users,
        )
        if not incoming.allowed:
            if (
                settings.audience_mode == "allowlist"
                and comment.author.casefold() not in settings.allowed_users
            ):
                stats.skipped_not_allowed_user += 1
            else:
                stats.skipped_policy += 1
            logger.info("skip comment=%s reason=%s", comment.id, incoming.reason)
            continue

        if settings.bot_mode == "observe":
            logger.info(
                "observe comment=%s author=%s text=%r thread_messages=%d",
                comment.id,
                comment.author,
                comment.content,
                len(comment.thread),
            )
            continue

        if agent is None:
            raise RuntimeError("An AI agent is required outside observe mode")

        try:
            decision = agent.generate(comment)
            if not decision.should_reply:
                stats.skipped_by_agent += 1
                logger.info("skip comment=%s agent_reason=%s", comment.id, decision.reason)
                continue

            actions = {action.type: action for action in decision.actions}
            model_profile_invitation = "comment_on_author_profile" in actions
            model_project_invitation = "comment_on_linked_project" in actions
            model_follow_request = "follow_author" in actions

            profile_invitation = (
                model_profile_invitation
                or is_explicit_profile_invitation(comment.content)
            )
            project_id = scratch_project_id(comment.content)
            project_invitation = bool(
                (model_project_invitation and project_id is not None)
                or is_explicit_project_invitation(comment.content)
            )
            follow_request = (
                model_follow_request
                or is_explicit_follow_request(comment.content)
            )
            if model_project_invitation and project_id is None:
                logger.warning(
                    "ignore project action comment=%s reason=no linked Scratch "
                    "project",
                    comment.id,
                )
            reply_text = (
                "Sure, I'll leave a comment on your project."
                if project_invitation
                else "Sure, I'll leave a comment on your profile."
                if profile_invitation
                else "Sure, I'll follow you."
                if follow_request
                else decision.reply
            )
            output = check_reply(reply_text, settings.max_reply_chars)
            if not output.allowed:
                stats.skipped_policy += 1
                logger.warning("reject comment=%s reason=%s", comment.id, output.reason)
                continue

            profile_comment = ""
            proposed_profile_comment = (
                actions["comment_on_author_profile"].content
                if model_profile_invitation
                else decision.profile_comment
            )
            if profile_invitation and not proposed_profile_comment:
                fallback = agent.generate_outreach(comment.author)
                proposed_profile_comment = fallback.reply
                logger.info(
                    "generated fallback profile invitation comment=%s author=%s",
                    comment.id,
                    comment.author,
                )

            if proposed_profile_comment:
                if not profile_invitation:
                    logger.warning(
                        "ignore profile action comment=%s reason=no explicit "
                        "author invitation",
                        comment.id,
                    )
                else:
                    profile_output = check_reply(
                        proposed_profile_comment,
                        settings.max_reply_chars,
                    )
                    if profile_output.allowed:
                        profile_comment = proposed_profile_comment
                    else:
                        stats.skipped_policy += 1
                        logger.warning(
                            "reject profile action comment=%s reason=%s",
                            comment.id,
                            profile_output.reason,
                        )
            if profile_invitation and not profile_comment:
                raise RuntimeError(
                    "Could not produce a safe invited profile comment"
                )

            project_comment = ""
            proposed_project_comment = (
                actions["comment_on_linked_project"].content
                if model_project_invitation
                else decision.project_comment
            )
            if project_invitation and not proposed_project_comment:
                assert project_id is not None
                fallback = agent.generate_project_invitation(
                    comment.author,
                    project_id,
                )
                proposed_project_comment = fallback.reply
                logger.info(
                    "generated fallback project invitation comment=%s "
                    "author=%s project=%s",
                    comment.id,
                    comment.author,
                    project_id,
                )

            if proposed_project_comment:
                if not project_invitation:
                    logger.warning(
                        "ignore project action comment=%s reason=no explicit "
                        "linked-project invitation",
                        comment.id,
                    )
                else:
                    project_output = check_reply(
                        proposed_project_comment,
                        settings.max_reply_chars,
                    )
                    if project_output.allowed:
                        project_comment = proposed_project_comment
                    else:
                        stats.skipped_policy += 1
                        logger.warning(
                            "reject project action comment=%s reason=%s",
                            comment.id,
                            project_output.reason,
                        )
            if project_invitation and not project_comment:
                raise RuntimeError(
                    "Could not produce a safe invited project comment"
                )

            if settings.bot_mode == "simulate":
                stats.simulated += 1
                if profile_comment:
                    stats.profile_invites_simulated += 1
                    logger.info(
                        "simulate profile invitation source_comment=%s "
                        "author=%s proposed_comment=%r",
                        comment.id,
                        comment.author,
                        profile_comment,
                    )
                if project_comment:
                    stats.project_invites_simulated += 1
                    logger.info(
                        "simulate project invitation source_comment=%s "
                        "author=%s project=%s proposed_comment=%r",
                        comment.id,
                        comment.author,
                        project_id,
                        project_comment,
                    )
                if follow_request:
                    stats.follows_simulated += 1
                    logger.info(
                        "simulate follow request source_comment=%s author=%s",
                        comment.id,
                        comment.author,
                    )
                logger.info(
                    "simulate comment=%s author=%s proposed_reply=%r agent_reason=%s",
                    comment.id,
                    comment.author,
                    reply_text,
                    decision.reason,
                )
                continue

            # Recheck immediately before posting. A new user or bot reply may have appeared
            # while Groq was generating the response.
            if not scratch.is_current_target(comment):
                stats.skipped_existing_reply += 1
                logger.info("skip comment=%s reason=thread changed before post", comment.id)
                continue

            if profile_comment:
                created_id = scratch.start_profile_invitation(
                    comment.author,
                    profile_comment,
                )
                if created_id is not None:
                    reply_text = "Done, I left a comment on your profile."
                    stats.profile_invites_posted += 1
                    logger.info(
                        "posted profile invitation source_comment=%s author=%s "
                        "created_comment=%s",
                        comment.id,
                        comment.author,
                        created_id,
                    )
                elif profile_invitation:
                    reply_text = (
                        "I already have a conversation open on your profile."
                    )

            if project_comment:
                assert project_id is not None
                created_id = scratch.start_project_invitation(
                    comment.author,
                    project_id,
                    project_comment,
                )
                if created_id is not None:
                    reply_text = "Done, I left a comment on your project."
                    stats.project_invites_posted += 1
                    logger.info(
                        "posted project invitation source_comment=%s "
                        "author=%s project=%s created_comment=%s",
                        comment.id,
                        comment.author,
                        project_id,
                        created_id,
                    )
                else:
                    reply_text = (
                        "I already have a conversation open on that project."
                    )

            if follow_request:
                scratch.follow_user(comment.author)
                stats.follows_posted += 1
                if profile_comment or project_comment:
                    reply_text = reply_text.rstrip(".") + " and followed you."
                else:
                    reply_text = "Done, I followed you."
                logger.info(
                    "followed requested user source_comment=%s author=%s",
                    comment.id,
                    comment.author,
                )

            logger.info(
                "prepared reply comment=%s author=%s reply=%r",
                comment.id,
                comment.author,
                reply_text,
            )
            scratch.reply(comment, reply_text)
            stats.posted += 1
            logger.info(
                "posted comment=%s author=%s reply=%r agent_reason=%s",
                comment.id,
                comment.author,
                reply_text,
                decision.reason,
            )
        except Exception:
            stats.errors += 1
            logger.exception("failed processing comment=%s", comment.id)

    if settings.bot_mode == "private":
        try:
            scratch.finish_notification_batch(success=stats.errors == 0)
        except Exception as exc:
            logger.warning(
                "failed marking Scratch notifications as read; continuing: %r",
                exc,
            )

    if settings.outreach_enabled and settings.outreach_users:
        try:
            username = scratch.outreach_candidate()
            if username is not None:
                if settings.bot_mode == "observe":
                    logger.info("observe outreach user=%s", username)
                else:
                    if agent is None:
                        raise RuntimeError(
                            "An AI agent is required outside observe mode"
                        )
                    decision = agent.generate_outreach(username)
                    output = check_reply(
                        decision.reply,
                        settings.max_reply_chars,
                    )
                    if not output.allowed:
                        stats.skipped_policy += 1
                        logger.warning(
                            "reject outreach user=%s reason=%s",
                            username,
                            output.reason,
                        )
                    elif settings.bot_mode == "simulate":
                        stats.outreach_simulated += 1
                        logger.info(
                            "simulate outreach user=%s proposed_comment=%r",
                            username,
                            decision.reply,
                        )
                    else:
                        scratch.start_outreach(username, decision.reply)
                        stats.outreach_posted += 1
        except Exception:
            stats.errors += 1
            logger.exception("failed outreach")

    return stats
