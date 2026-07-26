const GITHUB_API_VERSION = "2026-03-10";

function required(env, name) {
  const value = env[name]?.trim();
  if (!value) {
    throw new Error(`Missing required Worker setting: ${name}`);
  }
  return value;
}

export async function triggerWorkflow(env) {
  const owner = required(env, "GITHUB_OWNER");
  const repo = required(env, "GITHUB_REPO");
  const workflow = required(env, "GITHUB_WORKFLOW");
  const ref = required(env, "GITHUB_REF");
  const token = required(env, "GITHUB_TOKEN");
  const endpoint =
    `https://api.github.com/repos/${encodeURIComponent(owner)}/` +
    `${encodeURIComponent(repo)}/actions/workflows/` +
    `${encodeURIComponent(workflow)}/dispatches`;

  const response = await fetch(endpoint, {
    method: "POST",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      "User-Agent": "scratch-ai-agent-cloudflare-scheduler",
      "X-GitHub-Api-Version": GITHUB_API_VERSION,
    },
    body: JSON.stringify({ ref }),
  });

  if (!response.ok) {
    const detail = (await response.text()).slice(0, 500);
    throw new Error(
      `GitHub workflow dispatch failed with ${response.status}: ${detail}`,
    );
  }

  console.log(
    JSON.stringify({
      event: "github_workflow_dispatched",
      owner,
      repo,
      workflow,
      ref,
      scheduledTime: new Date().toISOString(),
    }),
  );
}

export default {
  async scheduled(_controller, env, ctx) {
    ctx.waitUntil(triggerWorkflow(env));
  },

  async fetch(request) {
    const url = new URL(request.url);
    if (url.pathname === "/health") {
      return Response.json({
        status: "ok",
        scheduler: "scratch-ai-agent",
        interval: "1 minute",
      });
    }
    return new Response("Not found", { status: 404 });
  },
};
