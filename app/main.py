from __future__ import annotations

import logging
import sys

from app.config import Settings
from app.groq_agent import ChatAgent
from app.runner import run_once
from app.scratch_client import ScratchClient


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger = logging.getLogger("scratch-agent")

    try:
        settings = Settings.from_env()
        scratch = ScratchClient(settings)
        agent = None if settings.bot_mode == "observe" else ChatAgent(settings)
        stats = run_once(settings, scratch, agent, logger)
    except Exception:
        logger.exception("agent run failed before completion")
        return 1

    logger.info(
        "summary mode=%s scanned=%d simulated=%d posted=%d "
        "profile_invites_simulated=%d profile_invites_posted=%d "
        "outreach_simulated=%d outreach_posted=%d existing=%d "
        "not_allowlisted=%d policy=%d agent_skips=%d errors=%d",
        settings.bot_mode,
        stats.scanned,
        stats.simulated,
        stats.posted,
        stats.profile_invites_simulated,
        stats.profile_invites_posted,
        stats.outreach_simulated,
        stats.outreach_posted,
        stats.skipped_existing_reply,
        stats.skipped_not_allowed_user,
        stats.skipped_policy,
        stats.skipped_by_agent,
        stats.errors,
    )
    return 1 if stats.errors else 0


if __name__ == "__main__":
    sys.exit(main())
