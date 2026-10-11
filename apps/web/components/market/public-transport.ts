// Quotes and other public data use the existing API host. The identity-only
// search index has a separate bounded edge read; it contains no current prices.
export const EDGE_ORIGIN = "https://korea-stocks-edge.lch68-workers.workers.dev";
export const PUBLIC_ORIGIN = "https://stocks.62-171-141-206.sslip.io";
const PREFIX = "/api/market/v1/";
const readPosts = new Set([PREFIX + "screener", PREFIX + "screener/export", PREFIX + "analysis/scenario"]);

function currentOrigin() {
  return typeof location === "undefined" ? PUBLIC_ORIGIN : location.origin;
}

export function marketUrl(path: string, origin = currentOrigin()) {
  const url = new URL(path, origin);
  if (!path.startsWith(PREFIX) || path.split("?")[0].includes("\\") || url.origin !== origin ||
      !url.pathname.startsWith(PREFIX) ||
      url.pathname.slice(PREFIX.length).split("/").some(part => ["internal", "ops", "account", "accounts", "auth"].includes(part))) {
    throw new Error("지원하지 않는 조회 주소입니다.");
  }
  return origin === EDGE_ORIGIN ? PUBLIC_ORIGIN + url.pathname + url.search : path;
}

export function streamUrl(origin = currentOrigin()) {
  const url = new URL(PREFIX + "stream", origin === EDGE_ORIGIN ? PUBLIC_ORIGIN : origin);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

export function marketFetch(path: string, options: RequestInit = {}) {
  const target = marketUrl(path);
  const method = (options.method || "GET").toUpperCase();
  if (!["GET", "HEAD"].includes(method) && !(method === "POST" && readPosts.has(path.split("?")[0]))) {
    throw new Error("지원하지 않는 조회 방식입니다.");
  }
  const headers = new Headers(options.headers);
  if (["authorization", "cookie", "x-api-key", "proxy-authorization"].some(key => headers.has(key)) || options.credentials === "include") {
    throw new Error("공개 조회에는 인증정보를 보내지 않습니다.");
  }
  return fetch(target, {...options, headers, credentials: "omit"});
}

/** Do not add another five minutes to an already cached identity response. */
export function searchIndexExpires(headers: Headers, started: number, now = Date.now()) {
  const date = Date.parse(headers.get("Date") || "");
  const rawAge = headers.get("Age"), age = rawAge === null ? 0 : /^\d+$/.test(rawAge) ? Number(rawAge) : NaN;
  if (!Number.isFinite(date) || date > now || !Number.isSafeInteger(age) || age < 0 ||
      /(?:^|,)\s*(?:private|no-store)(?:\s|=|,|$)/i.test(headers.get("Cache-Control") || "")) return 0;
  // CORS can hide Date/Age on the VPS fallback: use that valid body, but do not
  // invent a fresh in-memory lifetime when its upstream age is unknown.
  return Math.max(0, Math.min(date + 300000, started + Math.max(0, 300 - age) * 1000));
}

/** Fixed GET path, no custom credentials/validators, at most one VPS fallback. */
export async function readSearchIndex<T>(validate: (body: unknown) => T, signal?: AbortSignal) {
  const path = PREFIX + "search-index", edge = currentOrigin() === EDGE_ORIGIN;
  const total = AbortSignal.any([AbortSignal.timeout(8000), ...(signal ? [signal] : [])]);
  const read = async (target: string, timeout: number) => {
    total.throwIfAborted();
    const attempt = AbortSignal.any([total, AbortSignal.timeout(timeout)]), started = Date.now();
    const response = await fetch(target, {method: "GET", credentials: "omit", signal: attempt});
    try {
      // There is no validator/body pair owned by this reader. A raw 304, error,
      // HTML fallback or malformed 200 must never become a successful index.
      if (response.status !== 200 || !/^application\/json(?:\s*;|$)/i.test(response.headers.get("Content-Type") || "")) {
        throw new Error("검색 목록을 불러오지 못했습니다.");
      }
      const data = validate(await response.json());
      attempt.throwIfAborted();
      return {data, expiresAt: searchIndexExpires(response.headers, started)};
    } finally {
      if (!response.bodyUsed) await response.body?.cancel().catch(() => {});
    }
  };
  const direct = marketUrl(path);
  if (!edge) return read(direct, 8000);
  try { return await read(path, 3000); }
  catch {
    // A caller cancellation or total deadline is terminal, not a retry reason.
    total.throwIfAborted();
    return read(direct, 5000);
  }
}
