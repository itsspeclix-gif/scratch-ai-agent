# Scratch AI Agent - Version 2.0

This project runs a Groq- or Mistral-hosted model as a supervised Scratch
account agent. It reads comment notifications, fetches complete conversation
threads, and can reply to each unanswered user message.

## Operating modes

- `observe`: reads and logs eligible comments; does not call Groq or post.
- `simulate`: generates and logs proposed replies; does not post.
- `private`: posts replies. The audience is controlled separately by `AUDIENCE_MODE`.

Audience modes:

- `AUDIENCE_MODE=allowlist`: only usernames in `ALLOWED_USERS` are eligible.
- `AUDIENCE_MODE=everyone`: any Scratch user is eligible.

## Automatic GitHub replies

The GitHub Actions workflow has a five-minute fallback schedule and can also be run manually. The schedule is offset from the start of each hour to reduce GitHub scheduler congestion. Automatic posting requires these repository variables:

```text
BOT_MODE=private
AUDIENCE_MODE=everyone
```

With those values, a scheduled GitHub run checks Scratch and posts eligible replies without your Mac being on. GitHub scheduled workflows are best-effort: runs can be delayed or dropped during periods of high load, so GitHub Actions cannot guarantee an exact five-minute response interval.

## Reliable one-minute scheduling

The recommended free scheduler is the Cloudflare Worker in
`cloudflare-scheduler`. It uses a Cloudflare Cron Trigger to call GitHub's
`workflow_dispatch` API every minute, bypassing GitHub's delayed
scheduled-event queue without moving the Python bot or its Scratch and Groq
secrets out of GitHub.

Follow `cloudflare-scheduler/README.md` to create a repository-scoped GitHub
token, deploy the Worker, test one dispatch, and disable the native GitHub
schedule. Keep `CLOUDFLARE_SCHEDULER_ENABLED` unset until that test succeeds.

## Notification-driven conversations

Every run reads the account's unread Scratch notifications. Comment
notifications identify the profile or project and comment ID, so the agent
fetches only the referenced conversation instead of scanning every project
every minute. After the complete notification batch succeeds, the notifications
are marked as read. A failed batch remains unread for the next run.

The agent supports comments on its own profile and projects. It can also handle
replies to conversations it started on opted-in users' profiles. Notifications
from unrelated conversations on other accounts are ignored.

For each referenced top-level thread, the agent:

1. Reads the root comment and every page of its replies.
2. Finds the newest comment not written by the bot.
3. Skips the thread when the bot has already replied after that comment.
4. Sends the complete thread history to the selected AI provider.
5. Replies directly to the newest user comment.

Scratch displays replies under one top-level comment rather than as deeply nested branches. The agent therefore treats each top-level comment and all its replies as one ordered conversation.

When the newest comment contains a web link, the agent may inspect the first
public HTTP or HTTPS page and give the model only its title and a short text
summary. Link fetching blocks private network addresses, revalidates redirects,
accepts text formats only, and stops after 32 KiB. Page text is treated as
untrusted conversation context and cannot change the bot's rules.

Periodic recovery scanning is disabled by default because it can revisit a flat
Scratch thread even when its newest reply was addressed to somebody else. Set
`FULL_SCAN_ENABLED=true` only when deliberately recovering missed notifications;
`FULL_SCAN_INTERVAL_MINUTES` then controls its interval.

## Opt-in outreach

Add one consenting Scratch username per line to
`config/outreach_users.txt`. Blank lines and lines beginning with `#` are
ignored. Usernames are validated and deduplicated case-insensitively.
Outreach defaults to disabled, so committing a populated list does not contact
anyone. Set the GitHub Actions variable `OUTREACH_ENABLED=true` only when you
are ready to activate it.

At most once per `OUTREACH_INTERVAL_MINUTES` (eight hours by default), the agent:

1. Deterministically selects one username from the list for that time slot.
2. Skips the user if the bot has already started a profile conversation there.
3. Generates and safety-checks one short Scratch conversation starter.
4. Posts the opening profile comment without changing follow status.

Each user receives at most one bot-started conversation; subsequent messages
happen only as replies in that thread.

## Profile invitations

Any eligible commenter can explicitly invite the bot to leave a comment on
their own profile or page. The model recognizes the request by meaning and emits
a structured `comment_on_author_profile` action with a separate profile comment.
The requesting comment's author is always the destination; the model cannot
supply another username. The older phrase matcher remains as a fallback. The bot
does not create another top-level thread if it already started one there.

## Follow requests

An eligible commenter can ask the bot to follow their own account using natural
wording such as `follow me`, `could I get a follow?`, or `mind following back?`.
The model emits a structured `follow_author` action. The requesting comment's
author is always the follow destination, and the action format cannot contain a
different username. Outreach or profile invitations do not imply a follow unless
the same message asks for one.

## Structured conversation actions

For each newest message, the AI response contains a normal `reply` plus an
`actions` list. Supported user-triggered actions are:

- `follow_author`
- `comment_on_author_profile` with standalone comment content
- `comment_on_linked_project` with standalone comment content

Multiple actions may be returned for one request. Python validates and executes
them after response-policy checks. Profile and follow destinations are always the
current comment author. Project actions require a Scratch project link in the
newest message, and `ScratchClient` verifies that the author owns the project.
Every model action must also include a verbatim evidence quote from the newest
message. The runner rejects an action when that evidence exists only in older
thread history, preventing a past profile, project, or follow request from being
repeated on a later conversational turn. Invitations may appear anywhere in the
newest message and the normal reply still posts in the original thread.
The Mistral-generated `reply` is preserved after successful actions so action
acknowledgments use the configured persona instead of fixed response phrases.
The runner substitutes an accurate fixed response only for state the model could
not know, such as an already-open profile or project conversation. Scheduled
outreach is intentionally not exposed as a conversation action.

## Personality

The account personality is stored in `persona.txt`. Edit that file and push the change to GitHub. No API key or GitHub variable is needed for personality changes.

The default persona defines:

- identity and disclosure behavior
- interests
- writing voice
- conversational behavior

Avoid putting secrets or private information in `persona.txt` because it is committed to the repository.

## Configuration

These may be added under `Settings → Secrets and variables → Actions → Variables`:

- `MAX_REPLY_CHARS=500`: maximum generated reply length, kept within Scratch's comment field.
- `AI_PROVIDER=groq`: use `groq` or `mistral`.
- `GROQ_MODEL=qwen/qwen3.6-27b`: Groq model.
- `MISTRAL_AGENT_ID=`: use a configured Mistral Agent through the stateless
  Conversations API. The Agent supplies its own model, reasoning settings,
  personality, and attached document libraries.
- `MISTRAL_MODEL=mistral-medium-3-5`: direct Mistral fallback used only when
  `MISTRAL_AGENT_ID` is empty.
- `OUTREACH_USERS_FILE=config/outreach_users.txt`: opt-in outreach list.
- `OUTREACH_ENABLED=false`: explicit proactive outreach kill switch.
- `OUTREACH_INTERVAL_MINUTES=480`: at most three outreach attempts per day.
- `FULL_SCAN_ENABLED=false`: keep periodic recovery scanning off.
- `FULL_SCAN_INTERVAL_MINUTES=360`: recovery interval when explicitly enabled.

There is no configured cap on projects, top-level threads, reply pages, thread
history, or replies found in a notification batch. Proactive outreach is
intentionally limited to one selected user per configured time slot.

## Required GitHub secrets

- `SCRATCH_USERNAME`
- `SCRATCH_SESSION_STRING`
- `GROQ_API_KEY`
- `MISTRAL_API_KEY` when `AI_PROVIDER=mistral`

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

GitHub Actions starts from a clean machine on each run, so the bot does not rely
on a local database. It uses the unread notification count and Scratch threads
as its source of truth. Immediately before replying, it reloads the thread and
confirms that the same user comment is still the newest unanswered target.
Already-posted replies are therefore not duplicated when an unread batch is
retried.

## Scope

Version 2.0 supports:

- every shared project owned by the bot account
- profile comments
- notification-driven replies
- replies to bot-started threads on opted-in users' profiles
- one-at-a-time profile outreach from `config/outreach_users.txt`
- explicit author-only profile invitations
- explicit owner-only invitations to linked Scratch projects
- notification-driven replies in external project threads the bot has joined
- explicit author-only follow requests
- bounded context from the first public link in a comment
- any audience or an allowlist
- top-level comments and follow-up replies
- complete thread context
- all eligible replies found in each run
- GitHub execution requested every minute through Cloudflare
- optional periodic reconciliation scans, disabled by default
- editable account personality

It does not join studios, create projects, enter an external project without its
owner's explicit linked invitation, contact an unlisted profile without that
author's explicit invitation, or maintain long-term memory across separate
Scratch threads.

`scratchattach` is an unofficial Scratch API wrapper. Scratch may change its site behavior and break the integration. Scratch-specific code remains isolated in `app/scratch_client.py`.
