# Scratch AI Agent — Version 1

This starter project checks one Scratch project's top-level comments, sends eligible comments to a Groq-hosted Llama model, and replies only to allowlisted test accounts.

It uses three modes:

- `observe`: reads and logs comments; does not call Groq or post.
- `simulate`: generates a reply and logs it; does not post.
- `private`: posts only to usernames listed in `ALLOWED_USERS`.

There is no public mode in Version 1.

## Why it does not need a database

GitHub Actions starts from a clean machine on every run. To prevent duplicate replies, the agent checks the Scratch comment thread itself. If the bot account has already replied to a top-level comment, that comment is skipped. The workflow also uses a GitHub Actions concurrency group so two runs cannot overlap.

## 1. Install locally

Install Python 3.12, open Terminal in this folder, and run:

```bash
make -n check
make check
make dry-run
```

The first real command automatically creates `.venv` and installs the required packages. The `.venv` directory is created locally and is intentionally not included in the downloaded ZIP. Later commands reuse that environment. `make -n check` previews the bootstrap and test commands without changing anything. `make dry-run` uses fake Scratch objects, a fake AI response, and no network access.

## 2. Create the Scratch session secret

Run:

```bash
make session
```

Enter the bot account username and password. The password is hidden while you type. Copy the generated session string. Treat it exactly like a password.

Do not put the Scratch password, session string, or Groq key into any project file.

## 3. Test locally in simulation mode

Copy the example configuration:

```bash
cp .env.example .env
```

Edit `.env`, then load it and run one check:

```bash
set -a
source .env
set +a
make run
```

Use `BOT_MODE=simulate` first. Add a top-level comment to the selected Scratch project from the allowlisted testing account. The proposed reply will appear in Terminal, but nothing will be posted.

## 4. Put it on GitHub Actions

Create a private GitHub repository and upload this project. In the repository, open:

`Settings → Secrets and variables → Actions`

Create these repository secrets:

- `SCRATCH_USERNAME`
- `SCRATCH_SESSION_STRING`
- `SCRATCH_PROJECT_ID`
- `GROQ_API_KEY`
- `ALLOWED_USERS`

`ALLOWED_USERS` is a comma-separated list, for example:

```text
MyTestAccount,MySecondTestAccount
```

Create these repository variables:

- `BOT_MODE` = `simulate`
- `GROQ_MODEL` = `llama-3.1-8b-instant`

Then open `Actions → Scratch AI agent → Run workflow`.

## 5. Enable one-to-one posting

After the simulation output is correct, change the GitHub repository variable:

```text
BOT_MODE=private
```

The workflow runs once per hour and can reply only to allowlisted accounts. All other users are ignored. You can also run it manually at any time from the Actions page.

## Scope of Version 1

Version 1 intentionally supports only:

- one Scratch project
- top-level comments
- one short reply per top-level comment
- allowlisted testers
- a maximum of two generated or posted replies per run

It does not follow users, post on profiles, join studios, create projects, handle nested conversations, or initiate conversations.

## Important limitation

`scratchattach` is an unofficial Scratch API wrapper. Scratch may change its site behavior and break the integration. The Scratch-specific code is isolated in `app/scratch_client.py` so it can be repaired without changing the AI and policy logic.

## Troubleshooting: `ModuleNotFoundError`

All project commands use the Python interpreter inside `.venv`. If `.venv` does not exist, the current Makefile creates it and installs the dependencies automatically. Run:

```bash
make check
make dry-run
```

A successful check ends with `Ran 7 tests` and `OK`.
