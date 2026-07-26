# Cloudflare scheduler

This Worker triggers the GitHub Actions workflow every five minutes. The
Scratch agent and its existing secrets continue to run inside GitHub Actions.

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
   `*/5 * * * *`

Cron Triggers use UTC, but this expression runs every five minutes in every
time zone.

## 3. Test the dispatch

Cloudflare says a new or changed Cron Trigger can take up to 15 minutes to
propagate. Wait for the first Cron event, then confirm that GitHub Actions shows
a new run with event `workflow_dispatch`.

In Cloudflare, the last 100 scheduled invocations are available under the
Worker's `Settings -> Trigger Events -> View events` screen.

The Worker's `/health` route reports whether the deployment is reachable. It
does not execute the agent or expose secrets.

## 4. Disable GitHub's fallback schedule

Only after the Cloudflare test succeeds, create this GitHub Actions repository
variable:

`CLOUDFLARE_SCHEDULER_ENABLED=true`

The native GitHub schedule will then skip its job. Manual runs and Cloudflare
`workflow_dispatch` runs remain enabled.

## Command-line deployment

With Node.js installed, the same Worker can be deployed from this folder:

```bash
npm install
npx wrangler login
npx wrangler secret put GITHUB_TOKEN
npm test
npm run deploy
```

The non-secret GitHub settings and five-minute Cron Trigger are defined in
`wrangler.jsonc`.

For an immediate local scheduled-event test, run `npm run dev` and open:

`http://localhost:8787/cdn-cgi/handler/scheduled`
