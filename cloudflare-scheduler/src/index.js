const GITHUB_API_VERSION = "2026-03-10";
const ACTIVE_RUN_STATUSES = new Set([
  "queued",
  "in_progress",
  "requested",
  "waiting",
  "pending",
]);

function required(env, name) {
  const value = env[name]?.trim();
  if (!value) {
    throw new Error(`Missing required Worker setting: ${name}`);
  }
  return value;
}

function githubHeaders(token, contentType = false) {
  const headers = {
    Accept: "application/vnd.github+json",
    Authorization: `Bearer ${token}`,
    "User-Agent": "scratch-ai-agent-cloudflare-scheduler",
    "X-GitHub-Api-Version": GITHUB_API_VERSION,
  };
  if (contentType) {
    headers["Content-Type"] = "application/json";
  }
  return headers;
}

function workflowEndpoint(owner, repo, workflow) {
  return (
    `https://api.github.com/repos/${encodeURIComponent(owner)}/` +
    `${encodeURIComponent(repo)}/actions/workflows/` +
    encodeURIComponent(workflow)
  );
}

export async function activeWorkflowRun(env) {
  const owner = required(env, "GITHUB_OWNER");
  const repo = required(env, "GITHUB_REPO");
  const workflow = required(env, "GITHUB_WORKFLOW");
  const ref = required(env, "GITHUB_REF");
  const token = required(env, "GITHUB_TOKEN");
  const query = new URLSearchParams({
    branch: ref,
    exclude_pull_requests: "true",
    per_page: "20",
  });
  const endpoint = `${workflowEndpoint(owner, repo, workflow)}/runs?${query}`;

  const response = await fetch(endpoint, {
    headers: githubHeaders(token),
  });
  if (!response.ok) {
    throw new Error(
      `GitHub workflow status check failed with ${response.status}`,
    );
  }

  const body = await response.json();
  if (!body || !Array.isArray(body.workflow_runs)) {
    throw new Error("GitHub workflow status response was malformed");
  }

  return (
    body.workflow_runs.find(
      (run) => run && ACTIVE_RUN_STATUSES.has(run.status),
    ) ?? null
  );
}

export async function triggerWorkflow(env) {
  const owner = required(env, "GITHUB_OWNER");
  const repo = required(env, "GITHUB_REPO");
  const workflow = required(env, "GITHUB_WORKFLOW");
  const ref = required(env, "GITHUB_REF");
  const token = required(env, "GITHUB_TOKEN");
  const activeRun = await activeWorkflowRun(env);

  if (activeRun !== null) {
    const result = {
      event: "github_workflow_dispatch_skipped",
      reason: "workflow_already_active",
      owner,
      repo,
      workflow,
      runId: activeRun.id,
      runNumber: activeRun.run_number,
      runStatus: activeRun.status,
      scheduledTime: new Date().toISOString(),
    };
    console.log(JSON.stringify(result));
    return result;
  }

  const endpoint = `${workflowEndpoint(owner, repo, workflow)}/dispatches`;

  const response = await fetch(endpoint, {
    method: "POST",
    headers: githubHeaders(token, true),
    body: JSON.stringify({ ref }),
  });

  if (!response.ok) {
    throw new Error(
      `GitHub workflow dispatch failed with ${response.status}`,
    );
  }

  const result = {
    event: "github_workflow_dispatched",
    owner,
    repo,
    workflow,
    ref,
    scheduledTime: new Date().toISOString(),
  };
  console.log(JSON.stringify(result));
  return result;
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
