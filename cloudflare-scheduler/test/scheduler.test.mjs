import assert from "node:assert/strict";
import test from "node:test";

import scheduler, { triggerWorkflow } from "../src/index.js";

const env = {
  GITHUB_OWNER: "itsspeclix-gif",
  GITHUB_REPO: "scratch-ai-agent",
  GITHUB_WORKFLOW: "scratch-agent.yml",
  GITHUB_REF: "main",
  GITHUB_TOKEN: "github_pat_test_only",
};

test("dispatches the configured GitHub workflow", async (t) => {
  const originalFetch = globalThis.fetch;
  t.after(() => {
    globalThis.fetch = originalFetch;
  });

  const requests = [];
  globalThis.fetch = async (url, options) => {
    const request = { url: String(url), options: options ?? {} };
    requests.push(request);
    if (request.options.method !== "POST") {
      return Response.json({ workflow_runs: [] });
    }
    return new Response(null, { status: 204 });
  };

  const result = await triggerWorkflow(env);

  assert.equal(
    requests[0].url,
    "https://api.github.com/repos/itsspeclix-gif/scratch-ai-agent/" +
      "actions/workflows/scratch-agent.yml/runs?" +
      "branch=main&event=workflow_dispatch&" +
      "exclude_pull_requests=true&per_page=20",
  );
  assert.equal(requests[0].options.method, undefined);
  assert.equal(
    requests[1].url,
    "https://api.github.com/repos/itsspeclix-gif/scratch-ai-agent/" +
      "actions/workflows/scratch-agent.yml/dispatches",
  );
  assert.equal(requests[1].options.method, "POST");
  assert.equal(
    requests[1].options.headers.Authorization,
    "Bearer github_pat_test_only",
  );
  assert.deepEqual(JSON.parse(requests[1].options.body), { ref: "main" });
  assert.equal(result.event, "github_workflow_dispatched");
});

test("does not dispatch while the workflow is queued or running", async (t) => {
  const originalFetch = globalThis.fetch;
  t.after(() => {
    globalThis.fetch = originalFetch;
  });

  let requestCount = 0;
  globalThis.fetch = async () => {
    requestCount += 1;
    return Response.json({
      workflow_runs: [
        { id: 123, run_number: 17061, status: "in_progress" },
      ],
    });
  };

  const result = await triggerWorkflow(env);

  assert.equal(requestCount, 1);
  assert.equal(result.event, "github_workflow_dispatch_skipped");
  assert.equal(result.reason, "workflow_already_active");
  assert.equal(result.runId, 123);
  assert.equal(result.runStatus, "in_progress");
});

test("fails closed when the workflow status cannot be checked", async (t) => {
  const originalFetch = globalThis.fetch;
  t.after(() => {
    globalThis.fetch = originalFetch;
  });

  let requestCount = 0;
  globalThis.fetch = async () => {
    requestCount += 1;
    return new Response(null, { status: 503 });
  };

  await assert.rejects(
    triggerWorkflow(env),
    /status check failed with 503/,
  );
  assert.equal(requestCount, 1);
});

test("scheduled handler registers the dispatch promise", async (t) => {
  const originalFetch = globalThis.fetch;
  t.after(() => {
    globalThis.fetch = originalFetch;
  });
  globalThis.fetch = async (_url, options = {}) => {
    if (options.method !== "POST") {
      return Response.json({ workflow_runs: [] });
    }
    return new Response(null, { status: 204 });
  };

  let scheduledWork;
  scheduler.scheduled({}, env, {
    waitUntil(promise) {
      scheduledWork = promise;
    },
  });

  assert.ok(scheduledWork instanceof Promise);
  await scheduledWork;
});

test("health endpoint exposes no credentials", async () => {
  const response = await scheduler.fetch(
    new Request("https://scheduler.example/health"),
  );

  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), {
    status: "ok",
    scheduler: "scratch-ai-agent",
    interval: "1 minute",
  });
});
