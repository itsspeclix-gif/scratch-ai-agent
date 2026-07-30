from __future__ import annotations

import logging
from typing import Protocol

from app.config import Settings
from app.models import AgentDecision, CommentRef, RunStats
from app.policy import check_incoming, check_reply, is_explicit_profile_invitation


class ScratchClientProtocol(Protocol):
    def conversation_targets(self) -> list[CommentRef]: ...

    def is_current_target(self, comment: CommentRef) -> bool: ...

    def reply(self, comment: CommentRef, text: str) -> None: ...

    def start_profile_invitation(self, username: str, text: str) -> str | None: ...

    def finish_notification_batch(self, success: bool) -> None: ...

    def outreach_candidate(self) -> str | None: ...

    def start_outreach(self, username: str, text: str) -> str: ...


class AgentProtocol(Protocol):
    def generate(self, comment: CommentRef) -> AgentDecision: ...

    def generate_outreach(self, username: str) -> AgentDecision: ...


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

            output = check_reply(decision.reply, settings.max_reply_chars)
            if not output.allowed:
                stats.skipped_policy += 1
                logger.warning("reject comment=%s reason=%s", comment.id, output.reason)
                continue

            explicit_profile_invitation = is_explicit_profile_invitation(
                comment.content
            )
            profile_comment = ""
            proposed_profile_comment = decision.profile_comment
            if explicit_profile_invitation and not proposed_profile_comment:
                fallback = agent.generate_outreach(comment.author)
                proposed_profile_comment = fallback.reply
                logger.info(
                    "generated fallback profile invitation comment=%s author=%s",
                    comment.id,
                    comment.author,
                )

            if proposed_profile_comment:
                if not explicit_profile_invitation:
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
                logger.info(
                    "simulate comment=%s author=%s proposed_reply=%r agent_reason=%s",
                    comment.id,
                    comment.author,
                    decision.reply,
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
                    stats.profile_invites_posted += 1
                    logger.info(
                        "posted profile invitation source_comment=%s author=%s "
                        "created_comment=%s",
                        comment.id,
                        comment.author,
                        created_id,
                    )

            scratch.reply(comment, decision.reply)
            stats.posted += 1
            logger.info(
                "posted comment=%s author=%s reply=%r agent_reason=%s",
                comment.id,
                comment.author,
                decision.reply,
                decision.reason,
            )
        except Exception:
            stats.errors += 1
            logger.exception("failed processing comment=%s", comment.id)

    if settings.bot_mode == "private":
        try:
            scratch.finish_notification_batch(success=stats.errors == 0)
        except Exception:
            stats.errors += 1
            logger.exception("failed marking Scratch notifications as read")

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
