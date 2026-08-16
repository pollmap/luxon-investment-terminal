import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { resolve } from "node:path";

const API_PORT = process.env.PLAYWRIGHT_API_PORT ?? "18100";
const API_HEALTH_URL = `http://127.0.0.1:${API_PORT}/api/health`;
const ROOT = resolve(__dirname, "../../..");

export default async function globalSetup() {
  if (await isHealthy(API_HEALTH_URL, 750)) {
    return;
  }

  const api = spawn("python", ["-m", "uvicorn", "services.api.main:app", "--port", API_PORT], {
    cwd: ROOT,
    windowsHide: true,
    stdio: "ignore"
  });

  await waitForHealthy(API_HEALTH_URL, 30_000);

  return async () => {
    stopProcessTree(api);
  };
}

async function waitForHealthy(url: string, timeoutMs: number) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (await isHealthy(url, 1_000)) {
      return;
    }
    await new Promise((resolveWait) => setTimeout(resolveWait, 500));
  }
  throw new Error(`API did not become healthy: ${url}`);
}

async function isHealthy(url: string, timeoutMs: number) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, { signal: controller.signal });
    return response.ok;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

function stopProcessTree(child: ChildProcess) {
  if (!child.pid) {
    return;
  }
  if (process.platform === "win32") {
    spawnSync("taskkill", ["/pid", String(child.pid), "/T", "/F"], { stdio: "ignore" });
    return;
  }
  child.kill("SIGTERM");
}
