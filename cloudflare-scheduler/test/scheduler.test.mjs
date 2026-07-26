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

  let request;
  globalThis.fetch = async (url, options) => {
    request = { url, options };
    return new Response(null, { status: 204 });
  };

  await triggerWorkflow(env);

  assert.equal(
    request.url,
    "https://api.github.com/repos/itsspeclix-gif/scratch-ai-agent/" +
      "actions/workflows/scratch-agent.yml/dispatches",
  );
  assert.equal(request.options.method, "POST");
  assert.equal(
    request.options.headers.Authorization,
    "Bearer github_pat_test_only",
  );
  assert.deepEqual(JSON.parse(request.options.body), { ref: "main" });
});

test("scheduled handler registers the dispatch promise", async (t) => {
  const originalFetch = globalThis.fetch;
  t.after(() => {
    globalThis.fetch = originalFetch;
  });
  globalThis.fetch = async () => new Response(null, { status: 204 });

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
    interval: "5 minutes",
  });
});
