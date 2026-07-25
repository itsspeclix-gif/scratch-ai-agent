from __future__ import annotations

import logging
from typing import Protocol

from app.config import Settings
from app.models import AgentDecision, CommentRef, RunStats
from app.policy import check_incoming, check_reply


class ScratchClientProtocol(Protocol):
    def recent_top_level_comments(self) -> list[CommentRef]: ...

    def has_bot_reply(self, comment: CommentRef) -> bool: ...

    def reply(self, comment: CommentRef, text: str) -> None: ...


class AgentProtocol(Protocol):
    def generate(self, comment: CommentRef) -> AgentDecision: ...


def run_once(
    settings: Settings,
    scratch: ScratchClientProtocol,
    agent: AgentProtocol | None,
    logger: logging.Logger,
) -> RunStats:
    stats = RunStats()
    comments = scratch.recent_top_level_comments()

    # Scratch normally returns newest first. Process oldest first for stable conversations.
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

        if scratch.has_bot_reply(comment):
            stats.skipped_existing_reply += 1
            logger.info("skip comment=%s reason=bot already replied", comment.id)
            continue

        if settings.bot_mode == "observe":
            logger.info(
                "observe comment=%s author=%s text=%r",
                comment.id,
                comment.author,
                comment.content,
            )
            continue

        if agent is None:
            raise RuntimeError("An AI agent is required outside observe mode")

        if stats.posted + stats.simulated >= settings.max_replies_per_run:
            logger.info("reply limit reached for this run")
            break

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

            if settings.bot_mode == "simulate":
                stats.simulated += 1
                logger.info(
                    "simulate comment=%s author=%s proposed_reply=%r agent_reason=%s",
                    comment.id,
                    comment.author,
                    decision.reply,
                    decision.reason,
                )
                continue

            # Recheck immediately before posting. This closes the duplicate-reply race if
            # another process answered after the first check.
            if scratch.has_bot_reply(comment):
                stats.skipped_existing_reply += 1
                logger.info("skip comment=%s reason=reply appeared before post", comment.id)
                continue

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

    return stats
