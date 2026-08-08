# Cloudflare scheduler

This Worker checks the GitHub Actions workflow every minute. It dispatches a new
run only when the workflow has no queued, requested, waiting, pending, or
in-progress run. The Scratch agent and its existing secrets continue to run
inside GitHub Actions.

## 1. Create a restricted GitHub token

In GitHub, open:

`Settings -> Developer settings -> Personal access tokens -> Fine-grained tokens`

Create a token with:

- Resource owner: `itsspeclix-gif`
- Repository access: only `scratch-ai-agent`
- Repository permission: `Actions` set to `Read and write`
- A reasonable expiration date with a reminder to rotate it

Copy the token when GitHub displays it. Never put it in this repository.

## 2. Deploy with Cloudflare's dashboard

1. Create a free Cloudflare account.
2. Open `Workers & Pages`, create a Worker, and name it
   `scratch-ai-agent-scheduler`.
3. Replace the example code with `src/index.js` from this folder and deploy it.
4. Under `Settings -> Variables and Secrets`, add:
   - Secret: `GITHUB_TOKEN` with the fine-grained token
   - Variable: `GITHUB_OWNER=itsspeclix-gif`
   - Variable: `GITHUB_REPO=scratch-ai-agent`
   - Variable: `GITHUB_WORKFLOW=scratch-agent.yml`
   - Variable: `GITHUB_REF=main`
5. Under `Settings -> Triggers -> Cron Triggers`, add:
   `* * * * *`

Cron Triggers use UTC, but this expression runs every minute in every
time zone. The one-minute check is the default. When a run is already active,
that minute is logged as `github_workflow_dispatch_skipped`; the next minute
checks again instead of creating another queued run.

## 3. Test the dispatch

Cloudflare says a new or changed Cron Trigger can take up to 15 minutes to
propagate. Wait for the first Cron event, then confirm that GitHub Actions shows
a new run with event `workflow_dispatch`.

In Cloudflare, the last 100 scheduled invocations are available under the
Worker's `Settings -> Trigger Events -> View events` screen.

The Worker's `/health` route reports whether the deployment is reachable. It
does not execute the agent or expose secrets.

## 4. Keep the GitHub workflow dispatch-only

The repository's `.github/workflows/scratch-agent.yml` intentionally contains
only `workflow_dispatch`. Do not add a native GitHub `schedule` trigger: it would
create separate scheduled runs that compete with the one-minute Cloudflare
dispatcher. Cloudflare and GitHub's **Run workflow** button both use the same
dispatch path.

## Command-line deployment

With Node.js installed, the same Worker can be deployed from this folder:

```bash
npm install
npx wrangler login
npx wrangler secret put GITHUB_TOKEN
npm test
npm run deploy
```

The non-secret GitHub settings and one-minute Cron Trigger are defined in
`wrangler.jsonc`.

For an immediate local scheduled-event test, run `npm run dev` and open:

`http://localhost:8787/cdn-cgi/handler/scheduled`
