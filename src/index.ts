import { Container, getContainer } from "@cloudflare/containers";
import { env as workerEnv } from "cloudflare:workers";

export class PmiAnalysisContainer extends Container {
  defaultPort = 8080;
  sleepAfter = "2m";
  enableInternet = true;
  envVars = {
    R2_BUCKET: workerEnv.R2_BUCKET,
    R2_ACCOUNT_ID: workerEnv.R2_ACCOUNT_ID,
    R2_TEMPLATE_KEY: workerEnv.R2_TEMPLATE_KEY,
    R2_ACCESS_KEY_ID: workerEnv.R2_ACCESS_KEY_ID,
    R2_SECRET_ACCESS_KEY: workerEnv.R2_SECRET_ACCESS_KEY,
    MAX_PDF_BYTES: workerEnv.MAX_PDF_BYTES ?? String(8 * 1024 * 1024)
  };
}

interface Env {
  ACTION_API_KEY: string;
  R2_BUCKET: string;
  R2_ACCOUNT_ID: string;
  R2_TEMPLATE_KEY: string;
  R2_ACCESS_KEY_ID: string;
  R2_SECRET_ACCESS_KEY: string;
  MAX_PDF_BYTES?: string;
  PMI_ANALYSIS_CONTAINER: DurableObjectNamespace<PmiAnalysisContainer>;
  CONTAINER_INSTANCE_NAME?: string;
  ACTION_TIMEOUT_MS?: string;
}

const ACTION_PATH = "/pmi/member-analysis";

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (request.method === "GET" && url.pathname === "/health") {
      return json({ ok: true });
    }

    if (request.method !== "POST" || url.pathname !== ACTION_PATH) {
      return json({ error: "not_found" }, 404);
    }

    const suppliedKey = request.headers.get("x-api-key") ?? "";
    if (!suppliedKey || suppliedKey !== env.ACTION_API_KEY) {
      return json({ error: "unauthorized" }, 401);
    }

    const timeoutMs = parseInt(env.ACTION_TIMEOUT_MS ?? "42000", 10);
    const abort = AbortSignal.timeout(Math.min(timeoutMs, 44000));
    const headers = new Headers(request.headers);
    headers.delete("x-api-key");
    headers.set("x-action-authenticated", "true");

    const proxied = new Request(request, {
      headers,
      signal: abort
    });

    const instanceName = env.CONTAINER_INSTANCE_NAME ?? "pmi-member-analysis";
    const container = getContainer(env.PMI_ANALYSIS_CONTAINER, instanceName);

    try {
      return await container.fetch(proxied);
    } catch (error) {
      return json({ error: "processing_timeout" }, 504);
    }
  }
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store"
    }
  });
}
