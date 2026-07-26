# Scratch AI Agent — Version 1.2

This project runs a Groq-hosted Llama model as a supervised Scratch account agent. It checks one Scratch project's comment threads, reads recent conversation context, and can reply to the newest unanswered user message.

## Operating modes

- `observe`: reads and logs eligible comments; does not call Groq or post.
- `simulate`: generates and logs proposed replies; does not post.
- `private`: posts replies. The audience is controlled separately by `AUDIENCE_MODE`.

Audience modes:

- `AUDIENCE_MODE=allowlist`: only usernames in `ALLOWED_USERS` are eligible.
- `AUDIENCE_MODE=everyone`: any Scratch user is eligible.

## Automatic GitHub replies

The GitHub Actions workflow runs every five minutes and can also be run manually. Automatic posting requires these repository variables:

```text
BOT_MODE=private
AUDIENCE_MODE=everyone
```

With those values, a scheduled GitHub run checks Scratch and posts eligible replies without your Mac being on. GitHub schedules can begin a few minutes late.

## Threaded conversations

Version 1.2 supports follow-up replies in the same Scratch comment thread.

For each top-level thread, the agent:

1. Reads the root comment and its replies.
2. Finds the newest comment not written by the bot.
3. Skips the thread when the bot has already replied after that comment.
4. Sends recent thread history to Groq.
5. Replies directly to the newest user comment.

Scratch displays replies under one top-level comment rather than as deeply nested branches. The agent therefore treats each top-level comment and all its replies as one ordered conversation.

## Personality

The account personality is stored in `persona.txt`. Edit that file and push the change to GitHub. No API key or GitHub variable is needed for personality changes.

The default persona defines:

- identity and disclosure behavior
- interests
- writing voice
- conversational behavior

Avoid putting secrets or private information in `persona.txt` because it is committed to the repository.

## Configurable limits

These may be added under `Settings → Secrets and variables → Actions → Variables`:

- `MAX_RECENT_COMMENTS=20`: number of recent top-level threads scanned per run.
- `MAX_REPLIES_PER_RUN=2`: maximum generated or posted replies per run.
- `MAX_REPLY_CHARS=300`: maximum generated reply length.
- `MAX_THREAD_MESSAGES=8`: number of recent messages supplied to Groq for one thread.
- `GROQ_MODEL=llama-3.1-8b-instant`: Groq model.

If a variable is absent, the workflow uses the default shown above.

## Required GitHub secrets

- `SCRATCH_USERNAME`
- `SCRATCH_SESSION_STRING`
- `SCRATCH_PROJECT_ID`
- `GROQ_API_KEY`

`ALLOWED_USERS` is required only when `AUDIENCE_MODE=allowlist`.

## Local verification

From the project folder:

```bash
make -n check
make check
make dry-run
```

Then load `.env` and run one real check:

```bash
set -a
source .env
set +a
make run
```

Use `BOT_MODE=simulate` before changing to `private`.

## Duplicate prevention

GitHub Actions starts from a clean machine on each run, so the bot does not rely on a local database. It uses the Scratch thread itself as the source of truth. Immediately before posting, it reloads the thread and confirms that the same user comment is still the newest unanswered target.

## Scope

Version 1.2 supports:

- one Scratch project
- any audience or an allowlist
- top-level comments and follow-up replies
- short conversation context
- GitHub execution every five minutes
- editable account personality

It does not initiate conversations, follow users, post on profiles, join studios, create projects, or maintain long-term memory across separate Scratch threads.

`scratchattach` is an unofficial Scratch API wrapper. Scratch may change its site behavior and break the integration. Scratch-specific code remains isolated in `app/scratch_client.py`.
